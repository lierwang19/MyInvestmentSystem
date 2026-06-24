# -*- coding: utf-8 -*-
"""
backfill_from_ipo.py — 以 TuShare 为主从 IPO 日回灌起始缺口（支持 end-target）
--------------------------------------------------------------------
目标：
- 对照 IPO 上市日，把 DB 中每只股票的“起始缺口”补齐
- 只处理沪深 A 股，自动跳过北交所（.BJ）
- 以 TuShare 为主（pro_bar 前复权优先；失败时 daily+adj_factor 兜底前复权）
- 支持大跨度“分片抓取”，避免一次取太多；带重试与节奏控制
- 新增 --end-target 用于指定尾部对齐日期口径（prev|today|date:YYYY-MM-DD）

依赖：
  pip install -U pandas akshare tushare

环境变量：
  TUSHARE_TOKEN（必需）

示例（PowerShell）：
  $env:TUSHARE_TOKEN="你的token"
  $args = @(
    "--db","market.db",
    "--base","stock_industry_base.csv",
    "--allow-start-gap","5",
    "--sleep","0.12",
    "--max-retry","8",
    "--no-proxy","1",
    "--chunk-days","5500",
    "--end-target","prev"   # prev|today|date:2024-12-31
  )
  python backfill_from_ipo.py @args
"""

from __future__ import annotations

import os
import sys
import time
import math
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ====== 依赖检查 ======
try:
    import akshare as ak
except ModuleNotFoundError:
    raise SystemExit("未检测到 akshare，请先安装：pip install -U akshare")

try:
    import tushare as ts
except ModuleNotFoundError:
    raise SystemExit("未检测到 tushare，请先安装：pip install -U tushare")


LOG_DIR = Path("./logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)


# --------------------- 工具函数 ---------------------
def unset_proxies_if_needed(no_proxy_flag: int) -> None:
    """
    no_proxy_flag=1 时，显式屏蔽代理，避免局域网/本地代理干扰。
    """
    if int(no_proxy_flag) == 1:
        envs = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
        for k in envs:
            os.environ[k] = ""
        # 避免 requests 使用代理
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        print(f"[no-proxy=1] Proxy env = {{'HTTP_PROXY': None, 'HTTPS_PROXY': None, 'ALL_PROXY': None, "
              f"'http_proxy': None, 'https_proxy': None, 'all_proxy': None, 'NO_PROXY': '*', 'no_proxy': '*'}}")
    # else: 保持系统环境代理原样


def connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    # WAL 友好参数
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-50000;")
    return conn


def ensure_daily_price_table(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_price (
        ts_code     TEXT NOT NULL,
        trade_date  TEXT NOT NULL,     -- YYYY-MM-DD
        open        REAL,
        high        REAL,
        low         REAL,
        close       REAL,
        volume      REAL,
        amount      REAL,
        PRIMARY KEY (ts_code, trade_date)
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dp_ts_date ON daily_price(ts_code, trade_date DESC);")
    conn.commit()


def read_base_codes(base_file: str) -> pd.DataFrame:
    df = pd.read_csv(base_file, dtype={"ts_code": str})
    if "ts_code" not in df.columns:
        raise ValueError(f"{base_file} 缺少 ts_code 列")
    # 跳过北交所
    df = df[~df["ts_code"].str.endswith(".BJ")].drop_duplicates("ts_code").reset_index(drop=True)
    return df


def get_min_db_dates(conn: sqlite3.Connection, codes: List[str]) -> Dict[str, Optional[str]]:
    """
    查询每只票在 DB 中的最早 trade_date（YYYY-MM-DD），若无记录则返回 None。
    """
    out: Dict[str, Optional[str]] = {}
    q = "SELECT MIN(trade_date) FROM daily_price WHERE ts_code=?"
    cur = conn.cursor()
    for code in codes:
        row = cur.execute(q, (code,)).fetchone()
        out[code] = row[0] if row and row[0] else None
    return out


def get_tushare_pro() -> "ts.pro_api":  # type: ignore
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise SystemExit("未发现环境变量 TUSHARE_TOKEN，请先设置。")
    ts.set_token(token)
    pro = ts.pro_api()
    return pro


def get_list_dates_from_tushare(codes: List[str]) -> Dict[str, Optional[str]]:
    """
    读取 TuShare 上市日；同时合并 L(上市)、P(暂停)、D(退市) 三类，尽量覆盖全历史。
    返回 dict: ts_code -> YYYYMMDD 或 None
    """
    pro = get_tushare_pro()
    frames = []
    for st in ["L", "P", "D"]:
        try:
            tmp = pro.stock_basic(exchange="", list_status=st, fields="ts_code,list_date")
            frames.append(tmp)
        except Exception:
            pass
    if not frames:
        return {c: None for c in codes}

    all_basic = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code")
    mp = dict(zip(all_basic["ts_code"], all_basic["list_date"]))
    # 仅保留需要的 codes
    out = {c: mp.get(c) for c in codes}
    return out


def yyyymmdd(dt: pd.Timestamp | str) -> str:
    return pd.to_datetime(dt).strftime("%Y%m%d")


def resolve_end_target(flag: str) -> str:
    """
    end-target 解析：
      - prev : 取 “严格小于今天”的最近交易日
      - today: 取 “小于等于今天”的最近交易日
      - date:YYYY-MM-DD : 将目标日期映射到≤该日的最近交易日
    返回 YYYYMMDD
    """
    cal = ak.tool_trade_date_hist_sina()
    if "trade_date" in cal.columns:
        cal["dt"] = pd.to_datetime(cal["trade_date"])
    elif "日期" in cal.columns:
        cal["dt"] = pd.to_datetime(cal["日期"])
    else:
        cal["dt"] = pd.to_datetime(cal.iloc[:, 0])

    cal = cal.sort_values("dt").reset_index(drop=True)
    today = pd.to_datetime(date.today())
    if flag == "prev":
        c = cal[cal["dt"] < today]
        if c.empty:
            raise ValueError("交易日历异常：找不到 today 之前的交易日")
        return yyyymmdd(c["dt"].iloc[-1])
    elif flag == "today":
        c = cal[cal["dt"] <= today]
        if c.empty:
            raise ValueError("交易日历异常：找不到 ≤today 的交易日")
        return yyyymmdd(c["dt"].iloc[-1])
    elif flag.startswith("date:"):
        target = pd.to_datetime(flag.split("date:", 1)[1].strip())
        c = cal[cal["dt"] <= target]
        if c.empty:
            raise ValueError(f"交易日历异常：找不到 ≤{target.date()} 的交易日")
        return yyyymmdd(c["dt"].iloc[-1])
    else:
        raise ValueError("end-target 仅支持 prev|today|date:YYYY-MM-DD")


def chunk_ranges(start_yyyymmdd: str, end_yyyymmdd: str, chunk_days: int) -> List[Tuple[str, str]]:
    """
    将 [start, end]（均含）按自然日窗口切分为多段，避免一次拉取过大。
    """
    s = pd.to_datetime(start_yyyymmdd)
    e = pd.to_datetime(end_yyyymmdd)
    if s > e:
        return []
    out = []
    cur = s
    one_day = timedelta(days=1)
    while cur <= e:
        seg_end = min(e, cur + timedelta(days=chunk_days - 1))
        out.append((yyyymmdd(cur), yyyymmdd(seg_end)))
        cur = seg_end + one_day
    return out


def unify_columns_qfq(df: pd.DataFrame) -> pd.DataFrame:
    """
    统一列名为：日期/开盘/最高/最低/收盘/成交量/成交额（均为前复权价）
    Tushare:
      pro_bar 列：ts_code,trade_date,open,high,low,close,vol,amount（amount 元）
      daily   列：ts_code,trade_date,open,high,low,close,vol(手),amount(千元) —— 但我们在兜底时会转换为 QFQ
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额"])
    cols = {c.lower(): c for c in df.columns}
    # 兼容大小写
    def has(k): return (k in df.columns) or (k in cols)

    def col(k):
        if k in df.columns:
            return k
        lk = k.lower()
        return cols.get(lk, k)

    # 期望列转换
    out = pd.DataFrame()
    out["日期"] = pd.to_datetime(df[col("trade_date")]).dt.strftime("%Y-%m-%d")
    out["开盘"] = pd.to_numeric(df[col("open")], errors="coerce")
    out["最高"] = pd.to_numeric(df[col("high")], errors="coerce")
    out["最低"] = pd.to_numeric(df[col("low")], errors="coerce")
    out["收盘"] = pd.to_numeric(df[col("close")], errors="coerce")

    # 成交量/额：pro_bar 为 vol/amount（股/元），daily 为手/千元；此处尽量转为“股/元”口径
    if has("vol"):
        out["成交量"] = pd.to_numeric(df[col("vol")], errors="coerce")
    elif has("volume"):
        out["成交量"] = pd.to_numeric(df[col("volume")], errors="coerce")
    else:
        out["成交量"] = None

    if has("amount"):
        amt = pd.to_numeric(df[col("amount")], errors="coerce")
        # 若来自 daily 的“千元”，我们会事先转为“元”，否则保持“元”
        out["成交额"] = amt
    elif has("turnover"):
        out["成交额"] = pd.to_numeric(df[col("turnover")], errors="coerce")
    else:
        out["成交额"] = None

    return out


def fetch_tushare_pro_bar_chunk(
    pro: "ts.pro_api", ts_code: str, start: str, end: str, max_retry: int
) -> pd.DataFrame:
    """
    优先使用 pro_bar（adj='qfq'），失败重试 max_retry 次。
    """
    last_err = None
    for _ in range(max_retry):
        try:
            df = ts.pro_bar(ts_code=ts_code, start_date=start, end_date=end, adj="qfq", freq="D")
            if df is not None and not df.empty:
                return df.sort_values("trade_date")
        except Exception as e:
            last_err = e
        time.sleep(0.2)
    if last_err:
        raise RuntimeError(f"pro_bar failed: {last_err}")
    return pd.DataFrame()


def fetch_tushare_daily_qfq_chunk(
    pro: "ts.pro_api", ts_code: str, start: str, end: str, max_retry: int
) -> pd.DataFrame:
    """
    兜底：使用 daily + adj_factor 推导“前复权价格”。
    daily 的 amount 是“千元”，vol 是“手”，此处转为 元/股。
    QFQ: price_qfq = price * (adj_factor / adj_factor_at_end)
    """
    last_err = None
    df_daily, df_adj = None, None

    for _ in range(max_retry):
        try:
            df_daily = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
            if df_daily is not None:
                break
        except Exception as e:
            last_err = e
        time.sleep(0.2)

    for _ in range(max_retry):
        try:
            df_adj = pro.adj_factor(ts_code=ts_code, start_date=start, end_date=end)
            if df_adj is not None:
                break
        except Exception as e:
            last_err = e
        time.sleep(0.2)

    if df_daily is None or df_daily.empty:
        if last_err:
            raise RuntimeError(f"daily empty: {last_err}")
        return pd.DataFrame()
    if df_adj is None or df_adj.empty:
        # 没有复权因子，无法推导 QFQ，只能返回空
        raise RuntimeError("adj_factor empty")

    d = df_daily.copy()
    a = df_adj[["trade_date", "adj_factor"]].drop_duplicates("trade_date").copy()

    # 将 daily 的“手/千元”转换为“股/元”
    d["vol"] = pd.to_numeric(d["vol"], errors="coerce") * 100.0
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce") * 1000.0

    # 合并并按“区间末日”的因子做 QFQ
    d = d.merge(a, on="trade_date", how="left")
    d["adj_factor"] = d["adj_factor"].astype(float)

    # 对齐区间末日因子
    end_fact = d.sort_values("trade_date")["adj_factor"].iloc[-1]
    for col in ["open", "high", "low", "close"]:
        d[col] = pd.to_numeric(d[col], errors="coerce") * (d["adj_factor"] / end_fact)

    # 统一列名
    d = d[["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"]].sort_values("trade_date")
    d.rename(columns={"vol": "volume"}, inplace=True)
    return d


def fetch_tushare_chunked_qfq(
    pro: "ts.pro_api",
    ts_code: str,
    start_yyyymmdd: str,
    end_yyyymmdd: str,
    chunk_days: int,
    max_retry: int,
    sleep_sec: float
) -> pd.DataFrame:
    """
    分片抓取（优先 pro_bar，失败再 daily+adj_factor 兜底 QFQ）
    """
    segs = chunk_ranges(start_yyyymmdd, end_yyyymmdd, chunk_days)
    frames = []

    for s, e in segs:
        # 先试 pro_bar
        try:
            df = fetch_tushare_pro_bar_chunk(pro, ts_code, s, e, max_retry)
        except Exception:
            # 再试 daily+adj_factor 兜底
            df = fetch_tushare_daily_qfq_chunk(pro, ts_code, s, e, max_retry)

        frames.append(df)
        time.sleep(max(0.0, float(sleep_sec)))

    if not frames:
        return pd.DataFrame()

    full = pd.concat(frames, ignore_index=True)
    full = full.drop_duplicates(["trade_date"]).sort_values("trade_date")
    return full


def upsert_daily_price(conn: sqlite3.Connection, ts_code: str, df_qfq: pd.DataFrame) -> int:
    """
    将“前复权日线”写入 DB：日期/开盘/最高/最低/收盘/成交量/成交额
    返回插入/更新条数
    """
    if df_qfq is None or df_qfq.empty:
        return 0
    df_u = unify_columns_qfq(df_qfq)

    recs = []
    for _, row in df_u.iterrows():
        trade_date = pd.to_datetime(row["日期"]).strftime("%Y-%m-%d")
        def _f(x):
            try:
                return float(x)
            except Exception:
                return None
        recs.append((
            ts_code, trade_date,
            _f(row["开盘"]), _f(row["最高"]), _f(row["最低"]), _f(row["收盘"]),
            _f(row["成交量"]), _f(row["成交额"]),
        ))
    cur = conn.cursor()
    cur.executemany("""
        INSERT INTO daily_price (ts_code, trade_date, open, high, low, close, volume, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ts_code, trade_date) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
            volume=excluded.volume, amount=excluded.amount;
    """, recs)
    conn.commit()
    return len(recs)


# --------------------- 主流程 ---------------------
def main():
    parser = argparse.ArgumentParser(description="从 IPO 日回灌起始缺口（TuShare 为主，支持 end-target）")
    parser.add_argument("--db", type=str, default="market.db", help="SQLite 数据库路径")
    parser.add_argument("--base", type=str, default="stock_industry_base.csv", help="基础表 CSV（含 ts_code）")
    parser.add_argument("--allow-start-gap", type=int, default=5, help="允许起始缺口的最大交易日数（≤该值视为无需回灌）")
    parser.add_argument("--sleep", type=float, default=0.15, help="每只票抓取后的休眠秒数")
    parser.add_argument("--max-retry", type=int, default=6, help="单段抓取的最大重试次数")
    parser.add_argument("--no-proxy", type=int, default=1, help="1=屏蔽代理环境变量；0=不改动")
    parser.add_argument("--chunk-days", type=int, default=5500, help="单段分片的自然日跨度（建议接近 6000）")
    parser.add_argument("--end-target", type=str, default="prev",
                        help="回灌尾部对齐口径：prev|today|date:YYYY-MM-DD（默认 prev）")
    args = parser.parse_args()

    unset_proxies_if_needed(args.no_proxy)

    # 解析 end-target
    try:
        end_target_yyyymmdd = resolve_end_target(args.end_target)
    except Exception as e:
        raise SystemExit(f"解析 --end-target 失败：{e}")
    # 用于进度打印
    end_target_dt = pd.to_datetime(end_target_yyyymmdd).strftime("%Y-%m-%d")

    # 读基础表 & 建库
    base = read_base_codes(args.base)
    codes = base["ts_code"].tolist()
    conn = connect_db(args.db)
    ensure_daily_price_table(conn)

    # 准备 list_date
    list_date_map = get_list_dates_from_tushare(codes)

    # DB 最早日
    min_db_map = get_min_db_dates(conn, codes)

    # 计算需要回灌的标的与区间
    need_rows = []
    for ts_code in codes:
        list_date = list_date_map.get(ts_code)
        if not list_date or pd.isna(list_date) or str(list_date).strip() in ("", "None", "NaT"):
            # 没拿到上市日，跳过（可以后续单独救火）
            continue

        min_db = min_db_map.get(ts_code)  # YYYY-MM-DD or None
        list_dt = pd.to_datetime(list_date)
        if min_db is None:
            # DB 没有任何数据 → 直接从上市日回灌到 end-target
            need_rows.append({
                "ts_code": ts_code,
                "start_cal": yyyymmdd(list_dt),
                "end_cal": end_target_yyyymmdd
            })
        else:
            min_db_dt = pd.to_datetime(min_db)  # YYYY-MM-DD
            # 以交易日口径衡量缺口：这里简化为自然日差判定；后续 check_coverage 再做交易日精校
            gap_days = (min_db_dt.date() - list_dt.date()).days
            if gap_days > args.allow_start_gap:
                backfill_end = (min_db_dt - timedelta(days=1))
                if backfill_end >= list_dt:
                    need_rows.append({
                        "ts_code": ts_code,
                        "start_cal": yyyymmdd(list_dt),
                        "end_cal": yyyymmdd(min(backfill_end, pd.to_datetime(end_target_yyyymmdd)))
                    })

    need_df = pd.DataFrame(need_rows)
    if need_df.empty:
        print("无需回灌：所有标的起始缺口均在阈值内。")
        conn.close()
        return

    need_csv = LOG_DIR / "backfill_start_needed.csv"
    need_df.to_csv(need_csv, index=False, encoding="utf-8-sig")
    print(f"需回灌标的数：{len(need_df)}，清单见 {need_csv}")

    # 开始回灌
    pro = get_tushare_pro()
    failed = []
    patched_total = 0

    for i, row in need_df.iterrows():
        ts_code = row["ts_code"]
        start_cal = str(row["start_cal"])
        end_cal = str(row["end_cal"])

        try:
            df = fetch_tushare_chunked_qfq(
                pro=pro,
                ts_code=ts_code,
                start_yyyymmdd=start_cal,
                end_yyyymmdd=end_cal,
                chunk_days=int(args.chunk_days),
                max_retry=int(args.max_retry),
                sleep_sec=float(args.sleep)
            )

            if df is None or df.empty:
                raise RuntimeError("empty after chunked pro_bar/daily retries")

            n = upsert_daily_price(conn, ts_code, df)
            patched_total += n
            # 统计打印
            print(f"[{ts_code}] backfilled {n} rows (chunked): "
                  f"{pd.to_datetime(start_cal).strftime('%Y%m%d')}~{pd.to_datetime(end_cal).strftime('%Y%m%d')}  "
                  f"(end-target={end_target_dt})")

        except Exception as e:
            failed.append({"ts_code": ts_code, "start_cal": start_cal, "end_cal": end_cal, "error": str(e)})
            print(f"[{ts_code}] FAILED: {e}")

        # 节奏（对“每只票”层面的等待，chunk 内已各自 sleep）
        time.sleep(max(0.0, float(args.sleep)))

    if failed:
        fail_csv = LOG_DIR / "backfill_start_failed.csv"
        pd.DataFrame(failed).to_csv(fail_csv, index=False, encoding="utf-8-sig")
        print(f"⚠️ 有 {len(failed)} 只回灌失败，见 {fail_csv}")

    print(f"✅ 起始段回灌完成。total_patched_rows={patched_total}，end-target={end_target_dt}")
    conn.close()


if __name__ == "__main__":
    main()
