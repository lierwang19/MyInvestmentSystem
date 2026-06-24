# -*- coding: utf-8 -*-
"""
sync_hk_daily_prices.py — 港股历史日线补全（最小可行版本）

从 user_data.db holdings 表中读取所有港股（.HK 后缀），
使用 akshare stock_hk_hist() 拉取历史日线数据，
写入 market.db daily_price 表。

数据写入后，持仓分析页的"涨幅1/涨幅3/涨幅最低"列会自动显示港股数据，
无需改动任何后端分析逻辑。

用法：
    # Docker 内运行
    docker exec invest_backend python scripts/etl/sync_hk_daily_prices.py

    # 本地运行（在项目根目录）
    python scripts/etl/sync_hk_daily_prices.py

    # 指定起始日期（默认拉取全历史）
    python scripts/etl/sync_hk_daily_prices.py --start 20200101

    # 只同步指定股票
    python scripts/etl/sync_hk_daily_prices.py --codes 09988.HK 01810.HK
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime
from typing import Optional

import pandas as pd

# ── 路径常量 ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
USER_DB      = os.path.join(PROJECT_ROOT, "user_data.db")
MARKET_DB    = os.path.join(PROJECT_ROOT, "market.db")

# 防止继承本机代理导致 AkShare 请求被错误转发到失效本地端口
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
os.environ.setdefault("NO_PROXY", "*")

DEFAULT_HK_START = "20000101"
DEFAULT_HK_ADJUST = "qfq"


def connect_market_db() -> sqlite3.Connection:
    conn = sqlite3.connect(MARKET_DB)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn


def get_hk_holdings() -> list[str]:
    """从 holdings 表读取所有港股 ts_code（去重，含所有账户）"""
    if not os.path.isfile(USER_DB):
        print(f"⚠ user_data.db 不存在: {USER_DB}")
        return []
    conn = sqlite3.connect(USER_DB)
    rows = conn.execute(
        "SELECT DISTINCT ts_code FROM holdings WHERE ts_code LIKE '%.HK'"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def ensure_daily_price_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_price (
            ts_code    TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open       REAL,
            high       REAL,
            low        REAL,
            close      REAL,
            volume     REAL,
            amount     REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dp_ts_date ON daily_price (ts_code, trade_date DESC)"
    )
    conn.commit()


def upsert_daily_price(conn: sqlite3.Connection, ts_code: str, df: pd.DataFrame) -> int:
    """把 akshare 返回的港股日线 DataFrame 写入 daily_price 表，返回写入行数"""
    if df is None or df.empty:
        return 0

    recs = []
    for _, row in df.iterrows():
        def _f(x) -> Optional[float]:
            try:
                return float(x)
            except Exception:
                return None

        trade_date = str(row["日期"])[:10]  # YYYY-MM-DD
        recs.append((
            ts_code, trade_date,
            _f(row.get("开盘")), _f(row.get("最高")),
            _f(row.get("最低")), _f(row.get("收盘")),
            _f(row.get("成交量")), _f(row.get("成交额")),
        ))

    conn.executemany("""
        INSERT INTO daily_price (ts_code, trade_date, open, high, low, close, volume, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ts_code, trade_date) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume, amount=excluded.amount
    """, recs)
    conn.commit()
    return len(recs)


def _normalize_hk_hist_df(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    """
    统一东方财富 / 新浪港股历史数据列结构，输出 upsert_daily_price 可消费的中文列。
    """
    if df is None or df.empty:
        return pd.DataFrame()

    normalized = df.copy()
    if "date" in normalized.columns and "日期" not in normalized.columns:
        normalized = normalized.rename(columns={
            "date": "日期",
            "open": "开盘",
            "high": "最高",
            "low": "最低",
            "close": "收盘",
            "volume": "成交量",
        })

    required_cols = {"日期", "开盘", "最高", "最低", "收盘"}
    if not required_cols.issubset(set(normalized.columns)):
        return pd.DataFrame()

    normalized["日期"] = pd.to_datetime(normalized["日期"], errors="coerce")
    normalized = normalized.dropna(subset=["日期"]).copy()
    normalized["日期"] = normalized["日期"].dt.strftime("%Y-%m-%d")
    normalized = normalized[
        (normalized["日期"] >= f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}")
        & (normalized["日期"] <= f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}")
    ].copy()
    if normalized.empty:
        return pd.DataFrame()

    if "成交额" not in normalized.columns:
        normalized["成交额"] = None

    return normalized[["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]]


def _fetch_hk_hist_eastmoney(symbol: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_hk_hist(
        symbol=symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
    )
    return _normalize_hk_hist_df(df, start_date, end_date)


def _fetch_hk_hist_sina(symbol: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
    import akshare as ak

    sina_adjust = adjust
    if adjust == "none":
        sina_adjust = ""
    df = ak.stock_hk_daily(symbol=symbol, adjust=sina_adjust)
    return _normalize_hk_hist_df(df, start_date, end_date)


def _candidate_hk_symbols(ts_code: str) -> list[str]:
    """
    返回用于抓取港股历史数据的候选代码。

    持仓里可能出现港股通 WR 代码，如：
      81810.HK -> 底层 01810
      83690.HK -> 底层 03690
      89988.HK -> 底层 09988

    抓取时优先尝试原代码，失败后回退到 0 前缀底层代码；
    入库时仍写回原始 ts_code，保证 holdings/analysis 链路能命中。
    """
    code = ts_code.split(".")[0]
    candidates = [code]
    if len(code) == 5 and code[0] in ("8", "9"):
        base_code = "0" + code[1:]
        if base_code not in candidates:
            candidates.append(base_code)
    return candidates


def sync_one(
    conn: sqlite3.Connection,
    ts_code: str,
    start_date: str,
    end_date: str,
    adjust: str,
) -> int:
    """
    同步单只港股历史日线。
    ts_code 格式: 09988.HK
    akshare symbol 格式: 09988（去掉 .HK）
    """
    try:
        import akshare as ak  # noqa: F401
    except ImportError:
        raise RuntimeError("akshare 未安装，请 pip install akshare")

    last_error = None
    fetchers = [
        ("eastmoney", _fetch_hk_hist_eastmoney),
        ("sina", _fetch_hk_hist_sina),
    ]
    for symbol in _candidate_hk_symbols(ts_code):
        for source_name, fetcher in fetchers:
            try:
                df = fetcher(symbol, start_date, end_date, adjust)
            except Exception as e:
                last_error = f"{source_name}/{symbol}: {e}"
                continue

            if df is None or df.empty:
                last_error = f"{source_name}/{symbol}: 无数据返回"
                continue

            n = upsert_daily_price(conn, ts_code, df)
            notes = []
            if symbol != ts_code.split(".")[0]:
                notes.append(f"使用底层代码 {symbol}")
            notes.append(f"源 {source_name}")
            print(f"  ↺ {'，'.join(notes)}", end=" ", flush=True)
            return n

    print(f"  ✗ {ts_code} 拉取失败: {last_error or '未知错误'}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="港股历史日线补全")
    parser.add_argument(
        "--start", default=None,
        help="起始日期 YYYYMMDD（默认全历史，从 20000101 开始）"
    )
    parser.add_argument(
        "--end", default=None,
        help="结束日期 YYYYMMDD（默认今天）"
    )
    parser.add_argument(
        "--codes", nargs="*", default=None,
        help="指定港股代码（格式 09988.HK），不填则从 holdings 自动读取"
    )
    parser.add_argument(
        "--delay", type=float, default=1.0,
        help="每只股票间隔秒数，避免被限频（默认 1.0s）"
    )
    parser.add_argument(
        "--adjust",
        default=DEFAULT_HK_ADJUST,
        choices=["qfq", "hfq", "none"],
        help="复权口径：qfq=前复权，hfq=后复权，none=不复权（默认 qfq）",
    )
    args = parser.parse_args()

    end_date   = args.end   or datetime.today().strftime("%Y%m%d")
    start_date = args.start or DEFAULT_HK_START
    adjust = "" if args.adjust == "none" else args.adjust

    # 确定要同步的港股列表
    if args.codes:
        ts_codes = [c if c.endswith(".HK") else f"{c}.HK" for c in args.codes]
    else:
        ts_codes = get_hk_holdings()

    if not ts_codes:
        print("没有找到港股持仓，退出。")
        return

    print(f"港股日线同步 {start_date} → {end_date}（复权口径: {args.adjust}）")
    print(f"目标股票（{len(ts_codes)} 只）: {ts_codes}")

    if not os.path.isfile(MARKET_DB):
        print(f"⚠ market.db 不存在: {MARKET_DB}")
        return

    conn = connect_market_db()
    ensure_daily_price_table(conn)

    total = 0
    failed = 0
    for i, ts_code in enumerate(ts_codes, 1):
        print(f"[{i}/{len(ts_codes)}] {ts_code} ...", end=" ", flush=True)
        n = sync_one(conn, ts_code, start_date, end_date, adjust)
        print(f"写入 {n} 行")
        total += n
        if n == 0:
            failed += 1
        if i < len(ts_codes):
            time.sleep(args.delay)

    conn.close()
    print(f"\n✅ 完成。共写入 {total} 行到 daily_price 表。")
    print("请重新运行因子计算（make_market_enriched_from_db.py）更新涨幅数据。")
    if failed and total == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
