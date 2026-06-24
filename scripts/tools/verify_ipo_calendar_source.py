#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_ipo_calendar_source.py — 验证“股票打新通知”数据源是否可用

目标：
1. 验证 today ~ today+N 天范围内，能否拿到沪市/深市/北交所的新股申购日历
2. 输出字段覆盖情况、按日期/市场分组计数，以及示例行
3. 当前优先验证 AkShare 封装的数据源：
   - Eastmoney: stock_xgsglb_em（默认，覆盖沪深A股 + 北交所）
   - 同花顺:   stock_ipo_ths（可选，作为对照）

示例：
  python scripts/tools/verify_ipo_calendar_source.py
  python scripts/tools/verify_ipo_calendar_source.py --days 7 --provider em
  python scripts/tools/verify_ipo_calendar_source.py --days 14 --provider ths --save logs/ipo_verify.csv
"""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

# 防止本机代理把请求劫持到不可用端口，和现有 ETL 脚本保持一致
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
os.environ.setdefault("NO_PROXY", "*")


def _normalize_date(x) -> str | None:
    if x is None or x == "":
        return None
    try:
        return pd.to_datetime(x).strftime("%Y-%m-%d")
    except Exception:
        return None


def _within_days(df: pd.DataFrame, col: str, days: int) -> pd.DataFrame:
    if col not in df.columns:
        return df.iloc[0:0].copy()

    today = pd.Timestamp(date.today())
    end = today + pd.Timedelta(days=days)
    s = pd.to_datetime(df[col], errors="coerce")
    mask = (s >= today) & (s <= end)
    out = df.loc[mask].copy()
    out[col] = s.loc[mask].dt.strftime("%Y-%m-%d")
    return out


def _safe_num(v):
    try:
        if pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def fetch_akshare_em(days: int) -> pd.DataFrame:
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
            print(f"⚠ Eastmoney 拉取失败: {display_name}: {e}")
            continue
        if df is None or df.empty:
            continue

        filtered = _within_days(df, "申购日期" if "申购日期" in df.columns else "申购日", days)
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
    return _post_normalize(out)


def fetch_akshare_ths(days: int) -> pd.DataFrame:
    import akshare as ak

    market_map = {
        "沪市主板": "沪市主板",
        "深市主板": "深市主板",
        "创业板": "创业板",
        "科创板": "科创板",
        "北交所": "京市主板",
    }

    frames: list[pd.DataFrame] = []
    for display_name, symbol in market_map.items():
        try:
            df = ak.stock_ipo_ths(symbol=symbol)
        except Exception as e:
            print(f"⚠ 同花顺拉取失败: {display_name}: {e}")
            continue
        if df is None or df.empty:
            continue

        # 同花顺页面字段有时是“申购日期”有时是“申购日”
        date_col = "申购日期" if "申购日期" in df.columns else ("申购日" if "申购日" in df.columns else None)
        if not date_col:
            continue
        filtered = _within_days(df, date_col, days)
        if filtered.empty:
            continue

        normalized = pd.DataFrame(
            {
                "market": display_name,
                "board": display_name,
                "ts_code": filtered.get("股票代码", filtered.get("代码")),
                "sub_code": filtered.get("申购代码"),
                "name": filtered.get("股票简称", filtered.get("简称")),
                "subscribe_date": filtered.get("申购日期", filtered.get("申购日")),
                "issue_price": filtered.get("发行价", filtered.get("发行价格")),
                "apply_limit": filtered.get("申购上限"),
                "lottery_date": filtered.get("中签号公布日", filtered.get("中签公布日")),
                "listing_date": filtered.get("上市日期", filtered.get("上市日")),
                "source": "akshare_ths",
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
    return _post_normalize(out)


def _post_normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    for col in ("subscribe_date", "lottery_date", "listing_date"):
        if col in df.columns:
            df[col] = df[col].map(_normalize_date)

    for col in ("issue_price", "apply_limit"):
        if col in df.columns:
            df[col] = df[col].map(_safe_num)

    df["ts_code"] = df["ts_code"].astype(str).str.strip()
    df["sub_code"] = df["sub_code"].astype(str).str.strip()
    df["name"] = df["name"].astype(str).str.strip()

    df = df[(df["subscribe_date"].notna()) & (df["ts_code"] != "nan")].copy()
    df = df.sort_values(["subscribe_date", "market", "ts_code"]).reset_index(drop=True)
    return df


def print_summary(df: pd.DataFrame, days: int):
    today = date.today().strftime("%Y-%m-%d")
    end = (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")

    print(f"\n验证区间: {today} ~ {end}")
    print(f"总记录数: {len(df)}")
    if df.empty:
        print("⚠ 未拿到区间内打新数据。可能原因：")
        print("  1. 当前区间确实无新股")
        print("  2. 数据源接口结构变化")
        print("  3. 当前环境无法访问外网")
        return

    print("\n按市场统计:")
    print(df.groupby("market").size().rename("count").to_string())

    print("\n按申购日统计:")
    print(df.groupby("subscribe_date").size().rename("count").to_string())

    print("\n字段缺失统计:")
    for col in ["market", "ts_code", "name", "subscribe_date", "issue_price", "apply_limit", "lottery_date", "listing_date"]:
        miss = int(df[col].isna().sum()) if col in df.columns else len(df)
        print(f"  {col}: 缺失 {miss}/{len(df)}")

    print("\n样例数据:")
    cols = [
        "market",
        "ts_code",
        "name",
        "subscribe_date",
        "issue_price",
        "apply_limit",
        "lottery_date",
        "listing_date",
        "source",
    ]
    print(df[cols].head(20).to_string(index=False))


def main():
    ap = argparse.ArgumentParser(description="验证股票打新通知数据源（今天 + 未来 N 天）")
    ap.add_argument("--days", type=int, default=7, help="向后看多少天，默认 7")
    ap.add_argument("--provider", choices=["em", "ths"], default="em", help="数据提供方：em=Eastmoney, ths=同花顺")
    ap.add_argument("--save", default="", help="可选，保存标准化结果到 CSV")
    args = ap.parse_args()

    if args.provider == "em":
        df = fetch_akshare_em(args.days)
    else:
        df = fetch_akshare_ths(args.days)

    print_summary(df, args.days)

    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n✅ 已保存到: {out}")


if __name__ == "__main__":
    main()
