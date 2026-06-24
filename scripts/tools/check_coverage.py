# -*- coding: utf-8 -*-
"""
check_coverage.py — A股历史行情覆盖核查（支持 end-target 口径）
----------------------------------------------------------------
功能
1) 读取 SQLite(daily_price) 的最早/最晚交易日，评估“起始覆盖/尾部覆盖”
2) 起始覆盖：用 TuShare 上市日（L/P/D 合并）对齐至最近交易日，计算“起始缺口(按交易日)”
3) 尾部覆盖：对齐到 --end-target（prev|today|date:YYYY-MM-DD）的最近交易日，计算“尾部缺口(按交易日)”
4) 支持跳过北交所；支持提供 base CSV 以限定核查股票清单
5) 导出 offenders_start_gap_top30.csv / offenders_end_gap_top30.csv / offenders_zero_rows.csv / coverage_report.csv

依赖
  pip install -U pandas akshare tushare

环境变量
  TUSHARE_TOKEN（use-tushare=1 时必需）

示例（PowerShell）
  $env:TUSHARE_TOKEN="你的token"
  python check_coverage.py --db market.db --base stock_industry_base.csv --skip-bj 1 ^
    --allow-start-gap 5 --allow-end-gap 1 --use-tushare 1 --end-target prev
"""

from __future__ import annotations

import os
import sys
import argparse
import sqlite3
from pathlib import Path
from datetime import date
from typing import Dict, List, Optional, Tuple

import pandas as pd

# 依赖检查
try:
    import akshare as ak
except ModuleNotFoundError:
    raise SystemExit("未检测到 akshare，请先安装：pip install -U akshare")

# TuShare 可选
def _get_ts_pro():
    import tushare as ts  # 局部导入，避免未装时报错
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise SystemExit("use-tushare=1 但未发现 TUSHARE_TOKEN 环境变量")
    ts.set_token(token)
    return ts.pro_api()


LOG_DIR = Path("./")
# ---------------------------------------------------------------------

def connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-50000;")
    return conn


def read_base_symbols(base_file: Optional[str]) -> Optional[pd.DataFrame]:
    if not base_file:
        return None
    df = pd.read_csv(base_file, dtype={"ts_code": str})
    if "ts_code" not in df.columns:
        raise ValueError(f"{base_file} 缺少 ts_code 列")
    return df.drop_duplicates("ts_code")[["ts_code"]].reset_index(drop=True)


def get_calendar_df() -> pd.DataFrame:
    cal = ak.tool_trade_date_hist_sina()
    if "trade_date" in cal.columns:
        cal["dt"] = pd.to_datetime(cal["trade_date"])
    elif "日期" in cal.columns:
        cal["dt"] = pd.to_datetime(cal["日期"])
    else:
        cal["dt"] = pd.to_datetime(cal.iloc[:, 0])
    cal = cal.sort_values("dt").reset_index(drop=True)
    return cal[["dt"]]


def yyyymmdd(x) -> str:
    return pd.to_datetime(x).strftime("%Y%m%d")


def resolve_end_target(flag: str, cal_df: pd.DataFrame) -> Tuple[str, str]:
    """
    返回 (YYYYMMDD, YYYY-MM-DD) 口径对齐后的最近交易日
    prev  : 严格小于今天的最近交易日
    today : 小于等于今天的最近交易日
    date:YYYY-MM-DD : 小于等于给定日期的最近交易日
    """
    td = pd.to_datetime(date.today())
    if flag == "prev":
        c = cal_df[cal_df["dt"] < td]
    elif flag == "today":
        c = cal_df[cal_df["dt"] <= td]
    elif flag.startswith("date:"):
        tgt = pd.to_datetime(flag.split("date:", 1)[1].strip())
        c = cal_df[cal_df["dt"] <= tgt]
    else:
        raise ValueError("end-target 仅支持 prev|today|date:YYYY-MM-DD")
    if c.empty:
        raise ValueError("交易日历异常，无法解析 end-target")
    d = c["dt"].iloc[-1]
    return (yyyymmdd(d), d.strftime("%Y-%m-%d"))


def build_td_index(cal_df: pd.DataFrame) -> Dict[str, int]:
    """交易日 -> 索引（从 0 递增）"""
    cal_df = cal_df.copy()
    cal_df["key"] = cal_df["dt"].dt.strftime("%Y-%m-%d")
    return dict(zip(cal_df["key"], range(len(cal_df))))


def trading_gap_days(idx_map: Dict[str, int], start_ymd: str, end_ymd: str) -> Optional[int]:
    """
    计算两个“交易日口径”之间的间隔天数（索引差，<0 则返回 0）
    输入 YYYY-MM-DD；若任一不在交易日历中返回 None
    """
    s = idx_map.get(start_ymd)
    e = idx_map.get(end_ymd)
    if s is None or e is None:
        return None
    gap = e - s
    return gap if gap >= 0 else 0


def first_trading_on_or_after(idx_map: Dict[str, int], cal_df: pd.DataFrame, ymd: str) -> Optional[str]:
    """
    给定自然日 YYYYMMDD，返回 >=该日 的第一个交易日（YYYY-MM-DD）
    """
    d = pd.to_datetime(ymd)
    c = cal_df[cal_df["dt"] >= d]
    if c.empty:
        return None
    return c["dt"].iloc[0].strftime("%Y-%m-%d")


def get_list_dates_from_tushare(codes: List[str]) -> Dict[str, Optional[str]]:
    pro = _get_ts_pro()
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
    return {c: mp.get(c) for c in codes}


def query_db_minmax(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    一次性取出各票的最早/最晚交易日与行数，避免 N 次查询
    """
    q = """
    SELECT ts_code,
           MIN(trade_date) AS min_db_date,
           MAX(trade_date) AS max_db_date,
           COUNT(*)         AS rows_cnt
    FROM daily_price
    GROUP BY ts_code
    """
    df = pd.read_sql_query(q, conn)
    return df


def main():
    ap = argparse.ArgumentParser(description="A股历史行情覆盖核查（支持 end-target 口径）")
    ap.add_argument("--db", type=str, default="market.db", help="SQLite 数据库路径")
    ap.add_argument("--base", type=str, default="", help="基础表 CSV（含 ts_code）；留空则以 DB 中股票为准")
    ap.add_argument("--skip-bj", type=int, default=1, help="1=跳过北交所(.BJ)")
    ap.add_argument("--allow-start-gap", type=int, default=5, help="允许起始缺口的最大交易日数")
    ap.add_argument("--allow-end-gap", type=int, default=1, help="允许尾部缺口的最大交易日数")
    ap.add_argument("--use-tushare", type=int, default=1, help="1=启用 TuShare 上市日；0=不校验起始覆盖")
    ap.add_argument("--end-target", type=str, default="prev", help="prev|today|date:YYYY-MM-DD")
    args = ap.parse_args()

    cal_df = get_calendar_df()
    idx_map = build_td_index(cal_df)
    end_target_ymd, end_target_iso = resolve_end_target(args.end_target, cal_df)

    conn = connect_db(args.db)
    db_mm = query_db_minmax(conn)

    # 股票清单
    if args.base:
        base_df = read_base_symbols(args.base)
        all_codes = base_df["ts_code"].tolist()
    else:
        if db_mm.empty:
            raise SystemExit("数据库暂无数据；请先完成数据抓取/回灌")
        all_codes = db_mm["ts_code"].drop_duplicates().tolist()

    # 跳过北交所
    if int(args.skip_bj) == 1:
        all_codes = [c for c in all_codes if not c.endswith(".BJ")]

    # 合并 DB 覆盖统计
    mm = db_mm.copy()
    mm = mm[mm["ts_code"].isin(all_codes)]
    # 可能存在 base 有而 DB 暂无的情况：补空行
    if len(mm) < len(all_codes):
        missing = sorted(set(all_codes) - set(mm["ts_code"]))
        if missing:
            mm = pd.concat([mm, pd.DataFrame({"ts_code": missing})], ignore_index=True)

    # 计算起始/尾部缺口
    # 上市日
    list_map: Dict[str, Optional[str]] = {}
    if int(args.use_tushare) == 1:
        try:
            list_map = get_list_dates_from_tushare(all_codes)
        except Exception:
            print("[WARN] TuShare 上市日获取失败，跳过起始覆盖校验")
            args.use_tushare = 0

    recs = []
    for _, r in mm.iterrows():
        ts_code = str(r["ts_code"])
        min_db = r.get("min_db_date")
        max_db = r.get("max_db_date")
        rows_cnt = int(r.get("rows_cnt")) if pd.notna(r.get("rows_cnt")) else 0

        min_db_iso = str(min_db) if pd.notna(min_db) else None
        max_db_iso = str(max_db) if pd.notna(max_db) else None

        # 起始缺口（交易日）
        start_gap_days = None
        list_date = None
        if int(args.use_tushare) == 1:
            list_date = list_map.get(ts_code)
            if list_date:
                first_td = first_trading_on_or_after(idx_map, cal_df, list_date)
                if first_td and min_db_iso:
                    start_gap_days = trading_gap_days(idx_map, first_td, min_db_iso)
                elif first_td and not min_db_iso:
                    # DB 无数据，按“first_td 到 end-target”的跨度表示严重欠缺
                    # 这里给出 None，交由 offenders_zero_rows 统计
                    start_gap_days = None

        # 尾部缺口（交易日）
        end_gap_days = None
        if max_db_iso:
            # 若 max_db 已经 >= end_target_ymd，对齐索引后 gap<=0 视为 0
            # 需要保证 end_target_ymd 映射到交易日（resolve_end_target 已保证）
            end_gap_days = trading_gap_days(idx_map, max_db_iso, pd.to_datetime(end_target_ymd).strftime("%Y-%m-%d"))
            if end_gap_days is not None and end_gap_days < 0:
                end_gap_days = 0

        recs.append({
            "ts_code": ts_code,
            "list_date": list_date,
            "min_db_date": min_db_iso,
            "max_db_date": max_db_iso,
            "rows_cnt": rows_cnt,
            "start_gap_days": start_gap_days,
            "end_gap_days": end_gap_days
        })

    rep = pd.DataFrame(recs)

    # 统计
    zero_rows = rep[rep["rows_cnt"].fillna(0) == 0]
    offenders_zero_path = LOG_DIR / "offenders_zero_rows.csv"
    zero_rows.to_csv(offenders_zero_path, index=False, encoding="utf-8-sig")

    # 起始统计（仅在 use-tushare=1 时有效）
    if int(args.use_tushare) == 1 and "start_gap_days" in rep.columns:
        start_ok = rep["start_gap_days"].apply(lambda x: (pd.notna(x) and x <= int(args.allow_start_gap)))
        start_ok_cnt = int(start_ok.sum())
        start_total = int(rep["start_gap_days"].notna().sum())  # 仅统计拿到上市日的股票
        offenders_start = rep[(rep["start_gap_days"].notna()) & (rep["start_gap_days"] > int(args.allow_start_gap))]
        offenders_start = offenders_start.sort_values("start_gap_days", ascending=False).head(30)
        offenders_start_path = LOG_DIR / "offenders_start_gap_top30.csv"
        offenders_start.to_csv(offenders_start_path, index=False, encoding="utf-8-sig")
    else:
        start_ok_cnt = 0
        start_total = 0
        offenders_start_path = LOG_DIR / "offenders_start_gap_top30.csv"
        pd.DataFrame(columns=rep.columns).to_csv(offenders_start_path, index=False, encoding="utf-8-sig")

    # 尾部统计
    end_ok = rep["end_gap_days"].apply(lambda x: (pd.notna(x) and x <= int(args.allow_end_gap)))
    end_ok_cnt = int(end_ok.sum())
    end_total = len(rep)
    offenders_end = rep[rep["end_gap_days"].notna() & (rep["end_gap_days"] > int(args.allow_end_gap))]
    offenders_end = offenders_end.sort_values("end_gap_days", ascending=False).head(30)
    offenders_end_path = LOG_DIR / "offenders_end_gap_top30.csv"
    offenders_end.to_csv(offenders_end_path, index=False, encoding="utf-8-sig")

    # 全量报告
    rep["pass_start"] = rep["start_gap_days"].apply(
        lambda x: (pd.notna(x) and x <= int(args.allow_start_gap)) if int(args.use_tushare) == 1 else None
    )
    rep["pass_end"] = rep["end_gap_days"].apply(lambda x: (pd.notna(x) and x <= int(args.allow_end_gap)))
    rep["end_target_date"] = end_target_iso
    coverage_path = LOG_DIR / "coverage_report.csv"
    rep.to_csv(coverage_path, index=False, encoding="utf-8-sig")

    # 摘要
    if int(args.use_tushare) == 1:
        print(f"=== Coverage Summary (HS A only{' with base' if args.base else ''}) ===")
        print(f"Symbols total: {end_total}")
        print(f"Start-gap pass (≤{args.allow_start_gap}d): {start_ok_cnt} / {start_total} | rate: "
              f"{(start_ok_cnt / start_total * 100.0 if start_total else 0.0):.1f}%")
    else:
        print(f"=== Coverage Summary (尾部核查；未启用上市日起始核查) ===")
        print(f"Symbols total: {end_total}")

    print(f"End-gap   pass (≤{args.allow_end_gap}d): {end_ok_cnt} / {end_total} | rate: "
          f"{(end_ok_cnt / max(1, end_total) * 100.0):.1f}%")
    print(f"Zero rows: {len(zero_rows)}")

    print("\nExported:")
    print(f"- {offenders_start_path}  # 起始缺口 Top30")
    print(f"- {offenders_end_path}  # 尾部缺口 Top30")
    print(f"- {offenders_zero_path}  # 行数为 0 的标的")
    print(f"- {coverage_path}")
    print(f"\nend-target used: {end_target_iso}")

    conn.close()


if __name__ == "__main__":
    main()
