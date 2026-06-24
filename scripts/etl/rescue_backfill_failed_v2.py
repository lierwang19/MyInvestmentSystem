# -*- coding: utf-8 -*-
"""
rescue_backfill_failed_v2.py
- 读取失败清单或 --codes
- 仅补库内最早交易日之前的缺口
- AK 多复权口径(qfq,hfq,none) × 多源顺序(tx,sina,em)
- 可选再用 TuShare(pro.bar/daily) 小窗口兜底
"""

from __future__ import annotations
import argparse, sqlite3, time, random, os
import pandas as pd
from datetime import date

try:
    import akshare as ak
except ModuleNotFoundError:
    raise SystemExit("pip install -U akshare pandas")

JITTER = (0.05, 0.20)

def ts_to_ak_symbol(ts_code: str) -> str:
    code6, exch = ts_code.split(".")
    e = exch.upper()
    if e == "SZ": return f"sz{code6}"
    if e == "SH": return f"sh{code6}"
    if e == "BJ": return f"bj{code6}"
    raise ValueError(f"未知交易所: {ts_code}")

def yyyymmdd(s: str) -> str: return pd.to_datetime(s).strftime("%Y%m%d")

def last_trade_day_str(today: date|None=None) -> str:
    base_day = today or date.today()
    cal = ak.tool_trade_date_hist_sina()
    if "trade_date" in cal.columns: cal["dt"] = pd.to_datetime(cal["trade_date"])
    elif "日期" in cal.columns: cal["dt"] = pd.to_datetime(cal["日期"])
    else: cal["dt"] = pd.to_datetime(cal.iloc[:,0])
    cal = cal[cal["dt"] <= pd.to_datetime(base_day)]
    return cal["dt"].max().strftime("%Y-%m-%d")

def connect_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-50000;")
    return conn

def get_min_db_date(conn, ts_code: str):
    r = conn.execute("SELECT MIN(trade_date) FROM daily_price WHERE ts_code=?", (ts_code,)).fetchone()
    return r[0] if r and r[0] else None

def upsert_daily_price(conn, ts_code: str, df_hist: pd.DataFrame) -> int:
    if df_hist is None or df_hist.empty: return 0
    rename_map = {
        "date":"日期","trade_date":"日期","open":"开盘","open_price":"开盘","开盘价":"开盘",
        "close":"收盘","close_price":"收盘","收盘价":"收盘","high":"最高","high_price":"最高","最高价":"最高",
        "low":"最低","low_price":"最低","最低价":"最低","vol":"成交量","volume":"成交量","amount":"成交额","成交金额":"成交额"
    }
    for k,v in rename_map.items():
        if k in df_hist.columns and v not in df_hist.columns:
            df_hist.rename(columns={k:v}, inplace=True)
    must = ["日期","开盘","最高","最低","收盘"]
    if any(c not in df_hist.columns for c in must): return 0
    vol_col = "成交量" if "成交量" in df_hist.columns else None
    amt_col = "成交额" if "成交额" in df_hist.columns else None

    def _f(x):
        try: return float(x)
        except: return None
    recs=[]
    for _,r in df_hist.iterrows():
        d = pd.to_datetime(r["日期"]).strftime("%Y-%m-%d")
        recs.append((ts_code, d, _f(r["开盘"]), _f(r["最高"]), _f(r["最低"]), _f(r["收盘"]),
                     _f(r[vol_col]) if vol_col and r.get(vol_col) is not None else None,
                     _f(r[amt_col]) if amt_col and r.get(amt_col) is not None else None))
    conn.executemany("""
    INSERT INTO daily_price (ts_code, trade_date, open, high, low, close, volume, amount)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(ts_code, trade_date) DO UPDATE SET
      open=excluded.open, high=excluded.high, low=excluded.low,
      close=excluded.close, volume=excluded.volume, amount=excluded.amount;
    """, recs)
    conn.commit()
    return len(recs)

# ---- AK 多源 ----
def fetch_tx(symbol, start, end, adjust): return ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end, adjust=adjust)
def fetch_sina(symbol, start, end, adjust): return ak.stock_zh_a_daily(symbol=symbol, start_date=start, end_date=end, adjust=adjust)
def fetch_em(symbol, start, end, adjust): return ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start, end_date=end, adjust=adjust)

def try_ak(symbol, start, end, order, adjusts):
    last_err=None
    for adj in adjusts:
        for src in order:
            try:
                if src=="tx": df=fetch_tx(symbol,start,end,adj)
                elif src=="sina": df=fetch_sina(symbol,start,end,adj)
                elif src=="em": df=fetch_em(symbol,start,end,adj)
                else: continue
                if df is not None and not df.empty:
                    return df, f"{src}:{adj}"
            except Exception as e:
                last_err=e
    if last_err: raise last_err
    return pd.DataFrame(), None

# ---- TuShare 兜底（可选）----
def try_tushare(ts_code: str, start: str, end: str, token: str):
    try:
        import tushare as ts
        pro = ts.pro_api(token)
        # 先试 pro.bar(qfq) 再试 daily
        for mode in ("qfq","hfq",None):
            try:
                if mode:
                    df = ts.pro_bar(ts_code=ts_code, start_date=start, end_date=end, adj=mode, factors=None)
                else:
                    df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
                if df is not None and not df.empty:
                    # 对齐列名
                    df = df.rename(columns={"trade_date":"日期","open":"开盘","high":"最高","low":"最低","close":"收盘","vol":"成交量","amount":"成交额"})
                    # TuShare vol 单位手、amount 千元 → 换算到“股/元”可选；这里先不换，入库不强制
                    return df, f"ts:{'pro_bar_'+(mode or 'none')}"
            except Exception:
                pass
    except Exception:
        pass
    return pd.DataFrame(), None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="market.db")
    ap.add_argument("--failed", default="logs/backfill_start_failed.csv")
    ap.add_argument("--codes", default="")
    ap.add_argument("--ak-order", default="tx,sina,em")
    ap.add_argument("--ak-adjusts", default="qfq,hfq,none")
    ap.add_argument("--use-ts", type=int, default=1)
    ap.add_argument("--ts-token", default=os.getenv("TUSHARE_TOKEN",""))
    ap.add_argument("--sleep", type=float, default=0.8)
    args = ap.parse_args()

    if args.codes.strip():
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        df_failed = pd.DataFrame({"ts_code": codes})
    else:
        df_failed = pd.read_csv(args.failed) if os.path.exists(args.failed) else pd.DataFrame(columns=["ts_code"])
    if "ts_code" not in df_failed.columns and "code" in df_failed.columns:
        df_failed.rename(columns={"code":"ts_code"}, inplace=True)
    df_failed = df_failed.dropna(subset=["ts_code"]).drop_duplicates("ts_code").reset_index(drop=True)

    order = [x.strip() for x in args.ak_order.split(",") if x.strip()]
    adjusts = [x.strip() if x.strip()!="none" else None for x in args.ak_adjusts.split(",")]

    conn = connect_db(args.db)
    end0 = yyyymmdd(last_trade_day_str())

    patched_rows, ak_hits, ts_hits, empties = 0, 0, 0, 0

    for _, r in df_failed.iterrows():
        ts = str(r["ts_code"]).strip()
        if not ts or ts.endswith(".BJ"): continue

        min_dt = get_min_db_date(conn, ts)
        if not min_dt:
            start, end = "19901219", end0
        else:
            end = yyyymmdd((pd.to_datetime(min_dt) - pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
            start = "19901219"
            if int(end) < int(start):
                print(f"[{ts}] 无需补（库内已含最早段）")
                continue

        symbol = ts_to_ak_symbol(ts)
        print(f"[{ts}] rescue {start}~{end}  AK({order} × {adjusts}) ...")
        try:
            df, tag = try_ak(symbol, start, end, order, adjusts)
            if df is not None and not df.empty:
                n = upsert_daily_price(conn, ts, df)
                patched_rows += n; ak_hits += 1
                print(f"[{ts}] AK[{tag}] upsert {n}")
            elif args.use_ts:
                print(f"[{ts}] AK 空，再试 TuShare ...")
                df2, tag2 = try_tushare(ts, start, end, args.ts_token)
                if df2 is not None and not df2.empty:
                    n = upsert_daily_price(conn, ts, df2)
                    patched_rows += n; ts_hits += 1
                    print(f"[{ts}] TS[{tag2}] upsert {n}")
                else:
                    print(f"[{ts}] 仍为空")
                    empties += 1
            else:
                print(f"[{ts}] AK 空（未启用 TuShare 兜底）")
                empties += 1
        except Exception as e:
            print(f"[{ts}] ERROR: {e}")

        time.sleep(args.sleep + random.uniform(*JITTER))

    conn.close()
    print("\n=== Rescue v2 Summary ===")
    print(f"patched_rows={patched_rows}, ak_hits={ak_hits}, ts_hits={ts_hits}, empties={empties}")
    print("建议再执行：python check_coverage.py")

if __name__ == "__main__":
    main()
