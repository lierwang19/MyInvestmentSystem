# -*- coding: utf-8 -*-
"""
rescue_backfill_failed.py — 针对 backfill_from_ipo.py 回灌失败标的，用 AkShare 源兜底首段历史
兜底顺序：腾讯(stock_zh_a_hist_tx) → 新浪(stock_zh_a_daily) → 东财(stock_zh_a_hist)
仅补：数据库中该票“当前最早 trade_date 之前”的缺口，避免重复抓取
使用：
  python rescue_backfill_failed.py --db market.db --failed logs/backfill_start_failed.csv --sleep 0.8 --order tx,sina,em
可选：
  --list-date-csv  覆核 IPO 日（如从 tushare 获取到的 list_date CSV），仅用于日志提示，不强依赖
"""

from __future__ import annotations
import argparse, sqlite3, time, random
from datetime import date
from pathlib import Path
from typing import Optional, List
import pandas as pd

try:
    import akshare as ak
except ModuleNotFoundError:
    raise SystemExit("需要先安装 AkShare： pip install -U akshare pandas")

ADJUST_MODE = "qfq"  # 与库内口径保持一致
JITTER = (0.05, 0.20)

def ts_to_ak_symbol(ts_code: str) -> str:
    code6, exch = ts_code.split(".")
    e = exch.upper()
    if e == "SZ": return f"sz{code6}"
    if e == "SH": return f"sh{code6}"
    if e == "BJ": return f"bj{code6}"
    raise ValueError(f"未知交易所: {ts_code}")

def yyyymmdd(s: str) -> str:
    return pd.to_datetime(s).strftime("%Y%m%d")

def last_trade_day_str(today: Optional[date] = None) -> str:
    base_day = today or date.today()
    cal = ak.tool_trade_date_hist_sina()
    if "trade_date" in cal.columns:
        cal["dt"] = pd.to_datetime(cal["trade_date"])
    elif "日期" in cal.columns:
        cal["dt"] = pd.to_datetime(cal["日期"])
    else:
        cal["dt"] = pd.to_datetime(cal.iloc[:, 0])
    cal = cal[cal["dt"] <= pd.to_datetime(base_day)]
    return cal["dt"].max().strftime("%Y-%m-%d")

def connect_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-50000;")
    return conn

def upsert_daily_price(conn: sqlite3.Connection, ts_code: str, df_hist: pd.DataFrame) -> int:
    if df_hist is None or df_hist.empty:
        return 0
    rename_map = {
        "date": "日期", "trade_date": "日期",
        "open": "开盘", "open_price": "开盘", "开盘价": "开盘",
        "close": "收盘", "close_price": "收盘", "收盘价": "收盘",
        "high": "最高", "high_price": "最高", "最高价": "最高",
        "low": "最低", "low_price": "最低", "最低价": "最低",
        "vol": "成交量", "volume": "成交量",
        "amount": "成交额", "成交金额": "成交额",
    }
    for k, v in rename_map.items():
        if k in df_hist.columns and v not in df_hist.columns:
            df_hist.rename(columns={k: v}, inplace=True)

    must = ["日期","开盘","最高","最低","收盘"]
    if any(c not in df_hist.columns for c in must):
        return 0

    vol_col = "成交量" if "成交量" in df_hist.columns else None
    amt_col = "成交额" if "成交额" in df_hist.columns else None

    def _f(x):
        try: return float(x)
        except: return None

    recs = []
    for _, r in df_hist.iterrows():
        trade_date = pd.to_datetime(r["日期"]).strftime("%Y-%m-%d")
        recs.append((
            ts_code, trade_date,
            _f(r["开盘"]), _f(r["最高"]), _f(r["最低"]), _f(r["收盘"]),
            _f(r[vol_col]) if vol_col and (r.get(vol_col) is not None) else None,
            _f(r[amt_col]) if amt_col and (r.get(amt_col) is not None) else None,
        ))
    conn.executemany("""
        INSERT INTO daily_price (ts_code, trade_date, open, high, low, close, volume, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ts_code, trade_date) DO UPDATE SET
          open=excluded.open, high=excluded.high, low=excluded.low,
          close=excluded.close, volume=excluded.volume, amount=excluded.amount;
    """, recs)
    conn.commit()
    return len(recs)

def get_current_min_date(conn: sqlite3.Connection, ts_code: str) -> Optional[str]:
    row = conn.execute("SELECT MIN(trade_date) FROM daily_price WHERE ts_code=?", (ts_code,)).fetchone()
    return row[0] if row and row[0] else None

def fetch_hist_tx(symbol: str, start: str, end: str) -> pd.DataFrame:
    return ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end, adjust=ADJUST_MODE)

def fetch_hist_sina(symbol: str, start: str, end: str) -> pd.DataFrame:
    return ak.stock_zh_a_daily(symbol=symbol, start_date=start, end_date=end, adjust=ADJUST_MODE)

def fetch_hist_em(symbol: str, start: str, end: str) -> pd.DataFrame:
    return ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start, end_date=end, adjust=ADJUST_MODE)

def fetch_with_order(order: List[str], symbol: str, start: str, end: str) -> pd.DataFrame:
    last_err = None
    for src in order:
        try:
            if src == "tx":
                df = fetch_hist_tx(symbol, start, end)
            elif src == "sina":
                df = fetch_hist_sina(symbol, start, end)
            elif src == "em":
                df = fetch_hist_em(symbol, start, end)
            else:
                continue
            if df is not None and not df.empty:
                return df
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return pd.DataFrame()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--failed", default="logs/backfill_start_failed.csv")
    ap.add_argument("--order", default="tx,sina,em", help="兜底顺序，逗号分隔：tx,sina,em")
    ap.add_argument("--sleep", type=float, default=0.8)
    ap.add_argument("--list-date-csv", default=None, help="可选：含 ts_code,list_date 的 CSV，仅用于日志提示")
    args = ap.parse_args()

    failed_csv = Path(args.failed)
    if not failed_csv.exists():
        raise SystemExit(f"未找到失败清单：{failed_csv}")

    order = [s.strip() for s in args.order.split(",") if s.strip()]
    df_failed = pd.read_csv(failed_csv)
    # 兼容不同列名
    if "ts_code" not in df_failed.columns and "code" in df_failed.columns:
        df_failed.rename(columns={"code":"ts_code"}, inplace=True)
    df_failed = df_failed.dropna(subset=["ts_code"]).drop_duplicates("ts_code").reset_index(drop=True)

    list_map = {}
    if args.list_date_csv and Path(args.list_date_csv).exists():
        ld = pd.read_csv(args.list_date_csv, dtype={"ts_code":str, "list_date":str})
        ld["list_date"] = ld["list_date"].fillna("").astype(str)
        list_map = dict(zip(ld["ts_code"], ld["list_date"]))

    conn = connect_db(args.db)
    end = yyyymmdd(last_trade_day_str())

    total_patched, total_empty = 0, 0
    for _, row in df_failed.iterrows():
        ts = str(row["ts_code"]).strip()
        if ts.endswith(".BJ"):
            # 忽略北交所
            continue

        symbol = ts_to_ak_symbol(ts)
        min_dt = get_current_min_date(conn, ts)   # 库内已存在的最早日期
        if not min_dt:
            # 库里一条都没有 — 直接从一个“很早的日子”拉到最近交易日
            start = "19901219"
            # 如果你有 list_date，可用于日志提示
            tip = f"list_date={list_map.get(ts,'?')}"
        else:
            # 仅补“最早日期之前”
            # 起点：list_date 或 19901219（没有 list_date 情况下兜底）
            ld = list_map.get(ts)
            if ld and len(ld) == 8 and ld.isdigit():
                start = ld
            else:
                start = "19901219"
            tip = f"min_db_date={min_dt}, list_date={ld or '?'}"
            # 终点改为 min_dt 的前一日
            end_dt = (pd.to_datetime(min_dt) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            if pd.to_datetime(end_dt) < pd.to_datetime(start[:4]+"-"+start[4:6]+"-"+start[6:8]):
                print(f"[{ts}] 无需补：{tip}")
                continue
            end = yyyymmdd(end_dt)

        print(f"[{ts}] rescue fetch {start} ~ {end}  ({tip})  via {order}")
        try:
            df = fetch_with_order(order, symbol, start, end)
            if df is None or df.empty:
                print(f"[{ts}] EMPTY from all ak sources")
                total_empty += 1
            else:
                n = upsert_daily_price(conn, ts, df)
                total_patched += n
                print(f"[{ts}] inserted/updated {n} rows")
        except Exception as e:
            print(f"[{ts}] ERROR: {e}")

        time.sleep(args.sleep + random.uniform(*JITTER))

    conn.close()
    print("\n=== Rescue Summary ===")
    print(f"Patched rows: {total_patched}")
    print(f"Still empty : {total_empty}")
    print("完成。建议再运行： python check_coverage.py")
if __name__ == "__main__":
    main()
