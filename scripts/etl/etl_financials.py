# -*- coding: utf-8 -*-
"""
etl_financials.py — 风险标记 ETL
---------------------------------
从 AkShare 抓取财务数据，计算风险标记（三负 + 一重大），写入 user_data.db 的 risk_flags 表。

三负：
  1. 资产负债率 > 行业中位数 → ok_debt=0
  2. (商誉+无形资产)/总资产 > 30% → ok_goodwill=0
  3. 控股股东质押率 > 30% → ok_pledge=0

一重大：
  - 名称含 ST / 退市 → is_st=1

用法：
  python etl_financials.py
  python etl_financials.py --db user_data.db --base stock_industry_base.csv
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

import pandas as pd

def _resolve_user_db(root):
    """data/user_data.db 优先；兼容给定 root 下的旧位置。"""
    new = os.path.join(root, "data", "user_data.db")
    if os.path.exists(new):
        return new
    old = os.path.join(root, "user_data.db")
    return old if os.path.exists(old) else new

try:
    from web.backend.time_utils import sh_timestamp_str
except ImportError:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    def sh_timestamp_str() -> str:
        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

# 防代理干扰
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
os.environ.setdefault("NO_PROXY", "*")

try:
    import akshare as ak
except ModuleNotFoundError:
    ak = None
    print("警告: akshare 未安装，将跳过在线数据抓取。请运行: pip install akshare")


# ===================== 工具函数 =====================

def ts_code_to_plain(ts_code: str) -> str:
    """600000.SH → 600000"""
    return ts_code.split(".")[0]


def is_st_name(name: str) -> bool:
    if not isinstance(name, str):
        return False
    s = name.upper()
    return ("ST" in s) or ("退市" in name)


def safe_float(val) -> Optional[float]:
    try:
        if val is None or pd.isna(val):
            return None
        return float(val)
    except (ValueError, TypeError):
        return None


# ===================== 数据获取 =====================

def fetch_balance_sheet_indicators(code6: str, sleep: float = 0.3) -> Optional[dict]:
    """
    从 AkShare 获取个股资产负债表关键指标。
    返回 {debt_ratio_pct, goodwill_intangibles_pct} 或 None
    """
    if ak is None:
        return None
    try:
        time.sleep(sleep)
        # 新浪财务指标：stock_financial_analysis_indicator
        df = ak.stock_financial_analysis_indicator(symbol=code6, start_year="2023")
        if df is None or df.empty:
            return None

        # 取最新一期
        # 资产负债率列名可能是 "资产负债率(%)" 
        debt_col = None
        for c in df.columns:
            if "资产负债率" in c:
                debt_col = c
                break

        debt_ratio = None
        if debt_col:
            debt_ratio = safe_float(df.iloc[0][debt_col])

        return {"debt_ratio_pct": debt_ratio}
    except Exception as e:
        print(f"  ⚠ {code6} 财务指标获取失败: {e}")
        return None


def fetch_balance_sheet_detail(code6: str, market: str, sleep: float = 0.3) -> Optional[dict]:
    """
    从 AkShare 获取资产负债表详情，计算商誉+无形资产占比。
    market: 'sh' 或 'sz'
    """
    if ak is None:
        return None
    try:
        time.sleep(sleep)
        stock_symbol = f"{market}{code6}"
        df = ak.stock_financial_report_sina(stock=stock_symbol, symbol="资产负债表")
        if df is None or df.empty:
            return None

        # 取最新一期（第一行）
        row = df.iloc[0]

        # 列名搜索
        total_assets = None
        goodwill = None
        intangibles = None

        for c in df.columns:
            val = safe_float(row[c])
            if "总资产" in c or "资产总计" in c:
                total_assets = val
            elif "商誉" in c and "减值" not in c:
                goodwill = val
            elif "无形资产" in c and "摊销" not in c and "减值" not in c:
                intangibles = val

        if total_assets and total_assets > 0:
            gw = goodwill or 0
            ia = intangibles or 0
            pct = (gw + ia) / total_assets * 100
            return {"goodwill_intangibles_pct": round(pct, 2)}

        return None
    except Exception as e:
        print(f"  ⚠ {code6} 资产负债表获取失败: {e}")
        return None


# ===================== Tushare 数据获取 =====================

def fetch_financials_tushare(ts_code: str, pro, sleep: float = 0.15) -> dict:
    """
    通过 Tushare 接口获取三项财务风险指标：
    - debt_ratio_pct:           fina_indicator.debt_to_assets（资产负债率 %）
    - goodwill_intangibles_pct: balancesheet 商誉+无形资产/总资产 %
    - controller_pledge_pct:    pledge_stat.pledge_ratio（整体质押率 %）

    缺失字段保持 None，调用失败不抛异常（记日志继续）。
    sleep 默认 0.15s（三次调用总计 0.45s/股）。
    """
    result = {
        "debt_ratio_pct": None,
        "goodwill_intangibles_pct": None,
        "controller_pledge_pct": None,
        "_errors": [],   # 内部字段：记录失败原因，run_etl 会汇总
    }

    # 1. 资产负债率 —— fina_indicator.debt_to_assets
    try:
        time.sleep(sleep)
        df = pro.fina_indicator(
            ts_code=ts_code,
            fields="ts_code,end_date,report_type,debt_to_assets",
            limit=10,
        )
        if df is not None and not df.empty:
            # 只取合并报表（report_type='1'），取最新非空期
            if "report_type" in df.columns:
                df = df[df["report_type"].astype(str) == "1"]
            series = df["debt_to_assets"].dropna() if "debt_to_assets" in df.columns else pd.Series(dtype=float)
            if not series.empty:
                result["debt_ratio_pct"] = safe_float(series.iloc[0])
            else:
                result["_errors"].append("debt_ratio_pct:字段为空")
        else:
            result["_errors"].append("debt_ratio_pct:无数据")
    except Exception as e:
        result["_errors"].append(f"debt_ratio_pct:{e}")
        print(f"  ⚠ {ts_code} fina_indicator 失败: {e}")

    # 2. 商誉+无形资产占比 —— balancesheet
    try:
        time.sleep(sleep)
        df = pro.balancesheet(
            ts_code=ts_code,
            fields="ts_code,end_date,report_type,goodwill,intan_assets,total_assets",
            limit=10,
        )
        if df is not None and not df.empty:
            if "report_type" in df.columns:
                df = df[df["report_type"].astype(str) == "1"]
            if not df.empty:
                row = df.iloc[0]
                ta = safe_float(row.get("total_assets"))
                gw = safe_float(row.get("goodwill")) or 0
                ia = safe_float(row.get("intan_assets")) or 0
                if ta and ta > 0:
                    result["goodwill_intangibles_pct"] = round((gw + ia) / ta * 100, 2)
                else:
                    result["_errors"].append("goodwill_intangibles_pct:total_assets 为零/空")
            else:
                result["_errors"].append("goodwill_intangibles_pct:合并报表无数据")
        else:
            result["_errors"].append("goodwill_intangibles_pct:无数据")
    except Exception as e:
        result["_errors"].append(f"goodwill_intangibles_pct:{e}")
        print(f"  ⚠ {ts_code} balancesheet 失败: {e}")

    # 3. 质押率 —— pledge_stat.pledge_ratio（最新一期）
    try:
        time.sleep(sleep)
        df = pro.pledge_stat(ts_code=ts_code, limit=1)
        if df is not None and not df.empty and "pledge_ratio" in df.columns:
            result["controller_pledge_pct"] = safe_float(df["pledge_ratio"].iloc[0])
        else:
            result["_errors"].append("controller_pledge_pct:无数据")
    except Exception as e:
        result["_errors"].append(f"controller_pledge_pct:{e}")
        print(f"  ⚠ {ts_code} pledge_stat 失败: {e}")

    return result


# ===================== 主流程 =====================

def run_etl(
    base_path: str = None,
    user_db_path: str = None,
    sleep: float = 0.3,
    max_stocks: int = 0,
    skip_online: bool = False,
    source: str = None,          # "offline" | "akshare" | "tushare"；None 时由 skip_online 推断
    tushare_token: str = "",
    pool_only: bool = False,     # True 时仅扫描 stock_pool 里的标的
    on_progress=None,            # callable(current, total)，每 5 只回调一次（供后台线程更新进度）
) -> dict:
    """
    执行风险标记 ETL。

    source 参数控制财务数据来源：
      "offline"  — 仅做 ST 检测，不抓财务数据（快速，默认）
      "akshare"  — 通过 AkShare 抓取负债率/商誉（慢，需网络）
      "tushare"  — 通过 Tushare 抓取三项指标（需 Token）

    pool_only=True 时只处理 stock_pool 表里的标的（适合前端按钮触发）。
    skip_online 参数已保留作向后兼容（True 等价于 source="offline"）。
    """
    # 推断 source
    if source is None:
        source = "offline" if skip_online else "akshare"

    project_root = os.path.dirname(os.path.abspath(__file__))
    if base_path is None:
        base_path = os.path.join(project_root, "stock_industry_base.csv")
    if user_db_path is None:
        user_db_path = _resolve_user_db(project_root)

    # 读取基础表
    df = pd.read_csv(base_path, dtype=str)
    df["ts_code"] = df["ts_code"].astype(str)
    print(f"基表标的数: {len(df)}")

    # 跳过北交所
    df = df[~df["ts_code"].str.endswith(".BJ")].copy()
    print(f"排除北交所后: {len(df)}")

    # pool_only：只扫优选池里的股票
    if pool_only:
        try:
            conn_pool = sqlite3.connect(user_db_path)
            pool_codes = {row[0] for row in conn_pool.execute("SELECT ts_code FROM stock_pool").fetchall()}
            conn_pool.close()
            df = df[df["ts_code"].isin(pool_codes)].copy()
            print(f"仅扫描优选池: {len(df)} 只")
        except Exception as e:
            print(f"警告: 读取 stock_pool 失败（{e}），扫描全量标的")

    if max_stocks > 0:
        df = df.head(max_stocks)
        print(f"限制处理数: {max_stocks}")

    # 初始化 Tushare Pro（仅 source=tushare 时）
    ts_pro = None
    if source == "tushare":
        if not tushare_token:
            tushare_token = os.environ.get("TUSHARE_TOKEN", "")
        if not tushare_token:
            raise ValueError("source=tushare 需提供 TUSHARE_TOKEN 环境变量或 tushare_token 参数")
        try:
            import tushare as ts_lib
            ts_pro = ts_lib.pro_api(tushare_token, timeout=60)
            print(f"Tushare Pro 已初始化，准备扫描 {len(df)} 只")
        except Exception as e:
            raise RuntimeError(f"Tushare 初始化失败: {e}")

    # 准备结果
    records = []
    total = len(df)
    # 抓取成功计数（仅 Tushare 模式有意义）
    fetch_ok = {"debt_ratio_pct": 0, "goodwill_intangibles_pct": 0, "controller_pledge_pct": 0}
    # 各列第一条错误采样（方便诊断权限/字段问题）
    fetch_err_sample: dict[str, str] = {}
    print(f"数据来源: {source}")

    for idx, (_, row) in enumerate(df.iterrows()):
        ts_code = row["ts_code"]
        name = row.get("name", "")
        industry = row.get("tdx_l1", row.get("anchor_industry", ""))
        code6 = ts_code_to_plain(ts_code)

        # 市场判断
        if ts_code.endswith(".SH"):
            market = "sh"
        else:
            market = "sz"

        # ST 检测
        st = is_st_name(name)

        # 基础记录
        rec = {
            "ts_code": ts_code,
            "name": name,
            "industry": industry,
            "debt_ratio_pct": None,
            "goodwill_intangibles_pct": None,
            "controller_pledge_pct": None,
            "is_st": 1 if st else 0,
            "has_major_event": 1 if st else 0,
        }

        if (idx + 1) % 50 == 0 or idx == 0:
            print(f"  处理中: {idx + 1}/{total} ({ts_code} {name})")
        if on_progress and (idx + 1) % 5 == 0:
            on_progress(idx + 1, total)

        # ── AkShare 模式 ──
        if source == "akshare" and ak is not None:
            indicators = fetch_balance_sheet_indicators(code6, sleep=sleep)
            if indicators:
                rec["debt_ratio_pct"] = indicators.get("debt_ratio_pct")
            detail = fetch_balance_sheet_detail(code6, market, sleep=sleep)
            if detail:
                rec["goodwill_intangibles_pct"] = detail.get("goodwill_intangibles_pct")

        # ── Tushare 模式 ──
        elif source == "tushare" and ts_pro is not None:
            fin = fetch_financials_tushare(ts_code, ts_pro, sleep=sleep if sleep else 0.15)
            rec["debt_ratio_pct"]           = fin["debt_ratio_pct"]
            rec["goodwill_intangibles_pct"] = fin["goodwill_intangibles_pct"]
            rec["controller_pledge_pct"]    = fin["controller_pledge_pct"]
            # 统计各列填充情况
            for col in fetch_ok:
                if fin.get(col) is not None:
                    fetch_ok[col] += 1
            # 采样首条错误（便于诊断 Token 权限不足等）
            for err in fin.get("_errors", []):
                col_key = err.split(":")[0]
                if col_key not in fetch_err_sample:
                    fetch_err_sample[col_key] = err

        records.append(rec)

    df_result = pd.DataFrame(records)

    # ========== 计算行业负债率中位数 ==========
    df_result["debt_ratio_pct_num"] = pd.to_numeric(df_result["debt_ratio_pct"], errors="coerce")
    industry_medians = df_result.groupby("industry")["debt_ratio_pct_num"].median()

    df_result["industry_debt_median"] = df_result["industry"].map(industry_medians)

    # ========== 判定三负 ==========
    # 1. 负债率 > 行业中位数
    df_result["ok_debt"] = 1
    mask_debt = (
        df_result["debt_ratio_pct_num"].notna()
        & df_result["industry_debt_median"].notna()
        & (df_result["debt_ratio_pct_num"] > df_result["industry_debt_median"])
    )
    df_result.loc[mask_debt, "ok_debt"] = 0

    # 2. 商誉+无形资产 > 30%
    df_result["ok_goodwill"] = 1
    gw_pct = pd.to_numeric(df_result["goodwill_intangibles_pct"], errors="coerce")
    df_result.loc[gw_pct > 30, "ok_goodwill"] = 0

    # 3. 质押率 > 30% (暂无数据，默认通过)
    df_result["ok_pledge"] = 1
    pledge_pct = pd.to_numeric(df_result["controller_pledge_pct"], errors="coerce")
    df_result.loc[pledge_pct > 30, "ok_pledge"] = 0

    # ========== 汇总风险等级 ==========
    df_result["negative_count"] = (
        (1 - df_result["ok_debt"])
        + (1 - df_result["ok_goodwill"])
        + (1 - df_result["ok_pledge"])
        + df_result["is_st"]
        + df_result["has_major_event"]
    ).astype(int)

    def calc_risk_level(row):
        if row["is_st"] or row["has_major_event"]:
            return "high"
        if row["negative_count"] >= 2:
            return "high"
        if row["negative_count"] == 1:
            return "medium"
        return "low"

    df_result["risk_level"] = df_result.apply(calc_risk_level, axis=1)

    # ========== 写入数据库 ==========
    conn = sqlite3.connect(user_db_path)
    c = conn.cursor()

    # 确保表存在
    c.execute('''
    CREATE TABLE IF NOT EXISTS risk_flags (
        ts_code TEXT PRIMARY KEY,
        name TEXT,
        industry TEXT,
        debt_ratio_pct REAL,
        industry_debt_median REAL,
        ok_debt INTEGER DEFAULT 1,
        goodwill_intangibles_pct REAL,
        ok_goodwill INTEGER DEFAULT 1,
        controller_pledge_pct REAL,
        ok_pledge INTEGER DEFAULT 1,
        is_st INTEGER DEFAULT 0,
        has_major_event INTEGER DEFAULT 0,
        risk_level TEXT DEFAULT 'low',
        negative_count INTEGER DEFAULT 0,
        updated_at TEXT
    )
    ''')

    # 清空旧数据
    c.execute("DELETE FROM risk_flags")

    # 写入
    insert_cols = [
        "ts_code", "name", "industry", "debt_ratio_pct", "industry_debt_median",
        "ok_debt", "goodwill_intangibles_pct", "ok_goodwill", "controller_pledge_pct",
        "ok_pledge", "is_st", "has_major_event", "risk_level", "negative_count"
    ]
    for _, row in df_result.iterrows():
        vals = []
        for col in insert_cols:
            v = row.get(col)
            if pd.isna(v) if isinstance(v, float) else False:
                vals.append(None)
            else:
                vals.append(v)

        placeholders = ", ".join(["?"] * (len(insert_cols) + 1))
        c.execute(f'''
            INSERT OR REPLACE INTO risk_flags ({", ".join(insert_cols)}, updated_at)
            VALUES ({placeholders})
        ''', vals + [sh_timestamp_str()])

    conn.commit()
    conn.close()

    # 统计
    n_high = int((df_result["risk_level"] == "high").sum())
    n_medium = int((df_result["risk_level"] == "medium").sum())
    n_low = int((df_result["risk_level"] == "low").sum())

    print(f"\n✅ 风险标记完成: {len(df_result)} 只")
    print(f"   高风险: {n_high}  |  中风险: {n_medium}  |  低风险: {n_low}")

    # Tushare 模式：打印数据质量摘要，帮助诊断权限问题
    if source == "tushare" and total > 0:
        print(f"\n📊 Tushare 抓取成功率（共 {total} 只）:")
        for col, label in [
            ("debt_ratio_pct",           "负债率"),
            ("goodwill_intangibles_pct", "商誉占比"),
            ("controller_pledge_pct",    "质押率"),
        ]:
            n = fetch_ok[col]
            pct = round(n / total * 100)
            sample_err = fetch_err_sample.get(col, "")
            warn = f"  ⚠ 首条错误: {sample_err}" if n == 0 and sample_err else ""
            print(f"   {label}: {n}/{total} ({pct}%){warn}")

    return {
        "total": len(df_result),
        "high": n_high,
        "medium": n_medium,
        "low": n_low,
        # 抓取成功数（仅 Tushare 模式有值，其他模式为 0）
        "fetch_stats": fetch_ok,
        # 首条错误采样（Token 权限不足等诊断用）
        "fetch_errors": fetch_err_sample,
    }


# ===================== CLI =====================

def main():
    parser = argparse.ArgumentParser(description="风险标记 ETL — 三负 + 一重大")
    parser.add_argument("--base", default=None, help="stock_industry_base.csv 路径")
    parser.add_argument("--db", default=None, help="user_data.db 路径")
    parser.add_argument("--sleep", type=float, default=0.3, help="API 请求间隔 (秒)")
    parser.add_argument("--max", type=int, default=0, help="最大处理数量 (0=全部)")
    parser.add_argument("--skip-online", action="store_true", help="跳过在线抓取，仅做 ST 检测（同 --source offline）")
    parser.add_argument("--source", default=None, choices=["offline", "akshare", "tushare"],
                        help="数据来源: offline(默认)|akshare|tushare")
    parser.add_argument("--token", default="", help="Tushare Token（也可通过 TUSHARE_TOKEN 环境变量传入）")
    parser.add_argument("--pool-only", action="store_true", help="只扫描优选池（stock_pool）里的标的")
    args = parser.parse_args()

    run_etl(
        base_path=args.base,
        user_db_path=args.db,
        sleep=args.sleep,
        max_stocks=args.max,
        skip_online=args.skip_online,
        source=args.source,
        tushare_token=args.token,
        pool_only=args.pool_only,
    )


if __name__ == "__main__":
    main()
