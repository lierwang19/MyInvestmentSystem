# -*- coding: utf-8 -*-
"""
sync_ipo_calendar.py — 股票打新日历同步

从 AkShare / Eastmoney 拉取 today ~ today+N 天的新股申购日历，
覆盖沪市主板、深市主板、创业板、科创板、北交所，
写入 user_data.db 的 ipo_calendar 表。

用法：
    python scripts/etl/sync_ipo_calendar.py
    python scripts/etl/sync_ipo_calendar.py --days 14
    python scripts/etl/sync_ipo_calendar.py --start 2026-04-01 --end 2026-04-14
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

try:
    from web.backend.time_utils import sh_timestamp_str, sh_today
except ImportError:
    from zoneinfo import ZoneInfo
    def sh_timestamp_str() -> str:
        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    def sh_today() -> date:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date()
try:
    from web.backend.config import USER_DB_PATH as USER_DB  # data/ 统一路径，含旧位置兼容
except ImportError:
    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _new = os.path.join(_ROOT, "data", "user_data.db")
    _old = os.path.join(_ROOT, "user_data.db")
    USER_DB = _new if os.path.exists(_new) or not os.path.exists(_old) else _old

for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
os.environ.setdefault("NO_PROXY", "*")

MARKET_DISPLAY = {
    "沪市主板": "沪市主板",
    "深市主板": "深市主板",
    "创业板": "创业板",
    "科创板": "科创板",
    "北交所": "北交所",
}


def _normalize_date(value) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception:
        return None


def _safe_num(value) -> Optional[float]:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _parse_date(value: str) -> date:
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"无效日期格式: {value}")


def _infer_market_board(code: str, fallback_market: str, fallback_board: str) -> tuple[str, str, str]:
    raw = str(code or "").strip()
    if not raw or raw.lower() == "nan":
        return "", "", ""

    if "." in raw:
        raw = raw.split(".")[0]

    if raw.startswith(("688", "689")):
        return "科创板", "科创板", f"{raw}.SH"
    if raw.startswith(("300", "301")):
        return "创业板", "创业板", f"{raw}.SZ"
    if raw.startswith(("600", "601", "603", "605")):
        return "沪市主板", "沪市主板", f"{raw}.SH"
    if raw.startswith(("000", "001", "002", "003")):
        return "深市主板", "深市主板", f"{raw}.SZ"
    if raw.startswith(("4", "8", "92")):
        return "北交所", "北交所", f"{raw}.BJ"

    market = fallback_market if fallback_market in MARKET_DISPLAY else "未知"
    board = fallback_board or market
    suffix = ".BJ" if market == "北交所" else ".SH" if market == "沪市主板" else ".SZ"
    return market, board, f"{raw}{suffix}"


def _within_range(df: pd.DataFrame, start_date: date, end_date: date) -> pd.DataFrame:
    date_col = "申购日期" if "申购日期" in df.columns else ("申购日" if "申购日" in df.columns else None)
    if not date_col:
        return df.iloc[0:0].copy()

    series = pd.to_datetime(df[date_col], errors="coerce")
    mask = (series >= pd.Timestamp(start_date)) & (series <= pd.Timestamp(end_date))
    out = df.loc[mask].copy()
    out[date_col] = series.loc[mask].dt.strftime("%Y-%m-%d")
    return out


def fetch_ipo_calendar(start_date: date, end_date: date) -> pd.DataFrame:
    import akshare as ak

    market_map = {
        "沪市主板": "沪市主板",
        "科创板": "科创板",
        "深市主板": "深市主板",
        "创业板": "创业板",
        "北交所": "北交所",
    }

    frames: list[pd.DataFrame] = []
    for display_name, symbol in market_map.items():
        try:
            df = ak.stock_xgsglb_em(symbol=symbol)
        except Exception as e:
            print(f"⚠ 拉取失败: {display_name}: {e}")
            continue
        if df is None or df.empty:
            continue

        filtered = _within_range(df, start_date, end_date)
        if filtered.empty:
            continue

        normalized = pd.DataFrame(
            {
                "market": display_name,
                "board": filtered.get("板块", display_name),
                "ts_code": filtered.get("股票代码", filtered.get("代码")),
                "sub_code": filtered.get("申购代码"),
                "name": filtered.get("股票简称", filtered.get("简称")),
                "subscribe_date": filtered.get("申购日期", filtered.get("申购日")),
                "issue_price": filtered.get("发行价格"),
                "apply_limit": filtered.get("申购上限", filtered.get("网上-申购上限")),
                "lottery_date": filtered.get("中签号公布日", filtered.get("发行结果公告日期")),
                "listing_date": filtered.get("上市日期", filtered.get("上市首日-上市日")),
                "source": "akshare_em",
            }
        )
        frames.append(normalized)

    if not frames:
        return pd.DataFrame(
            columns=[
                "market",
                "board",
                "ts_code",
                "sub_code",
                "name",
                "subscribe_date",
                "issue_price",
                "apply_limit",
                "lottery_date",
                "listing_date",
                "source",
            ]
        )

    out = pd.concat(frames, ignore_index=True)
    for col in ("subscribe_date", "lottery_date", "listing_date"):
        out[col] = out[col].map(_normalize_date)
    for col in ("issue_price", "apply_limit"):
        out[col] = out[col].map(_safe_num)

    out["name"] = out["name"].astype(str).str.strip()
    out["sub_code"] = out["sub_code"].fillna("").astype(str).str.strip()
    out = out[(out["subscribe_date"].notna()) & (out["ts_code"].notna())].copy()

    market_board = out.apply(
        lambda row: _infer_market_board(row["ts_code"], str(row.get("market") or ""), str(row.get("board") or "")),
        axis=1,
        result_type="expand",
    )
    market_board.columns = ["market", "board", "ts_code"]
    out[["market", "board", "ts_code"]] = market_board

    out = out[out["ts_code"] != ""].copy()
    out = out.drop_duplicates(subset=["ts_code", "subscribe_date"], keep="first")
    out = out.sort_values(["subscribe_date", "market", "ts_code"]).reset_index(drop=True)
    return out


def ensure_ipo_calendar_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ipo_calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            board TEXT DEFAULT '',
            ts_code TEXT NOT NULL,
            sub_code TEXT DEFAULT '',
            name TEXT NOT NULL,
            subscribe_date TEXT NOT NULL,
            issue_price REAL,
            apply_limit REAL,
            lottery_date TEXT,
            listing_date TEXT,
            source TEXT DEFAULT 'akshare_em',
            updated_at TEXT,
            updated_at TEXT,
            UNIQUE(ts_code, subscribe_date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ipo_calendar_subscribe_date ON ipo_calendar (subscribe_date)"
    )
    conn.commit()


def sync_ipo_calendar(start_date: date, end_date: date) -> dict:
    if not os.path.isfile(USER_DB):
        raise RuntimeError(f"user_data.db 不存在: {USER_DB}")

    df = fetch_ipo_calendar(start_date, end_date)

    conn = sqlite3.connect(USER_DB)
    try:
        ensure_ipo_calendar_table(conn)
        conn.execute(
            "DELETE FROM ipo_calendar WHERE subscribe_date >= ? AND subscribe_date <= ?",
            (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
        )

        now_ts = sh_timestamp_str()
        if not df.empty:
            records = [
                (
                    str(row["market"] or ""),
                    str(row["board"] or ""),
                    str(row["ts_code"] or ""),
                    str(row.get("sub_code") or ""),
                    str(row["name"] or ""),
                    str(row["subscribe_date"] or ""),
                    row.get("issue_price"),
                    row.get("apply_limit"),
                    row.get("lottery_date"),
                    row.get("listing_date"),
                    str(row.get("source") or "akshare_em"),
                    now_ts,
                )
                for _, row in df.iterrows()
            ]
            conn.executemany(
                """
                INSERT INTO ipo_calendar (
                    market, board, ts_code, sub_code, name, subscribe_date,
                    issue_price, apply_limit, lottery_date, listing_date, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    issue_price, apply_limit, lottery_date, listing_date, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ts_code, subscribe_date) DO UPDATE SET
                    market=excluded.market,
                    board=excluded.board,
                    sub_code=excluded.sub_code,
                    name=excluded.name,
                    issue_price=excluded.issue_price,
                    apply_limit=excluded.apply_limit,
                    lottery_date=excluded.lottery_date,
                    listing_date=excluded.listing_date,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                    updated_at=excluded.updated_at
                """,
                records,
            )
        conn.commit()
    finally:
        conn.close()

    market_counts = (
        df.groupby("market").size().to_dict() if not df.empty else {}
    )
    return {
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "rows_written": int(len(df)),
        "market_counts": {str(k): int(v) for k, v in market_counts.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="同步股票打新日历")
    parser.add_argument("--days", type=int, default=7, help="向后同步多少天，默认 7")
    parser.add_argument("--start", default="", help="起始日期，支持 YYYY-MM-DD / YYYYMMDD")
    parser.add_argument("--end", default="", help="结束日期，支持 YYYY-MM-DD / YYYYMMDD")
    args = parser.parse_args()

    start_date = _parse_date(args.start) if args.start else sh_today()
    start_date = _parse_date(args.start) if args.start else sh_today()
    end_date = _parse_date(args.end) if args.end else (start_date + timedelta(days=args.days))

    result = sync_ipo_calendar(start_date, end_date)
    print(
        f"✅ IPO 日历同步完成：{result['start_date']} ~ {result['end_date']}，"
        f"共写入 {result['rows_written']} 条"
    )
    if result["market_counts"]:
        print("按市场统计:", result["market_counts"])


if __name__ == "__main__":
    main()
