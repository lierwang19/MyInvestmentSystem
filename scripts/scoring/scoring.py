# -*- coding: utf-8 -*-
"""
scoring.py — 统一打分入口
--------------------------
封装 score_universe.py 的核心逻辑，支持：
  1. 命令行独立运行
  2. 被 Web API (scan_stock_pool) 调用
  3. 结果写入 user_data.db 的 stock_pool 表

用法：
  python scoring.py
  python scoring.py --config config/scoring.yaml --min-score 70
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import pandas as pd
import yaml

try:
    from web.backend.time_utils import sh_timestamp_str
except ImportError:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    def sh_timestamp_str() -> str:
        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


# ===================== 核心打分函数（从 score_universe.py 提取） =====================

def load_yaml(path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def to_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        if pd.isna(x):
            return None
        return float(x)
    except Exception:
        return None


def score_anchor_tiers(
    value: Optional[float],
    anchor: Optional[float],
    tiers: List[Dict[str, Any]],
    direction: str = "lower_better",
    tiers_mode: str = "ratio",
    ignore_if_le_zero: bool = False,
) -> Optional[float]:
    """PB/PE 行业锚点 + 分档打分。

    tiers_mode:
      - 'ratio'    : delta = (v - a) / a，相对比例偏离（旧模式，PB 常用）
      - 'additive' : delta = v - a，绝对值偏离（YAML tiers_mode: additive，PE_TTM 使用）

    ignore_if_le_zero:
      True 时，若 value <= 0 则返回 None（PE/PB 为负无意义，YAML ignore_if_le_zero: true）
    """
    v = to_float(value)
    a = to_float(anchor)
    if v is None or a is None or a <= 0:
        return None
    if ignore_if_le_zero and v <= 0:
        return None

    else_score = None
    norm_tiers: List[Tuple[float, float]] = []
    for t in tiers:
        if "else" in t:
            else_score = float(t["else"])
        elif "delta" in t and "score" in t:
            norm_tiers.append((float(t["delta"]), float(t["score"])))
    norm_tiers.sort(key=lambda x: x[0])

    if tiers_mode == "additive":
        diff = v - a  # 绝对偏离量
    else:
        diff = (v - a) / a  # 相对偏离比例

    if direction == "lower_better":
        if v <= a:
            return norm_tiers[0][1] if norm_tiers else else_score
        for delta, score in norm_tiers:
            if diff <= delta:
                return score
        return else_score
    else:
        norm_tiers_desc = sorted(norm_tiers, key=lambda x: x[0], reverse=True)
        for delta, score in norm_tiers_desc:
            if diff >= delta:
                return score
        return else_score


def score_buckets(
    value: Optional[float],
    buckets: List[Dict[str, Any]],
    direction: str = "lower_better",
) -> Optional[float]:
    """距一年低点等简单分桶打分"""
    v = to_float(value)
    if v is None:
        return None

    else_score = None
    norm_buckets: List[Tuple[float, float]] = []
    for b in buckets:
        if "else" in b:
            else_score = float(b["else"])
        elif "max" in b and "score" in b:
            norm_buckets.append((float(b["max"]), float(b["score"])))
    norm_buckets.sort(key=lambda x: x[0])

    if direction == "lower_better":
        for m, s in norm_buckets:
            if v <= m:
                return s
        return else_score
    else:
        norm_buckets_desc = sorted(norm_buckets, key=lambda x: x[0], reverse=True)
        for m, s in norm_buckets_desc:
            if v >= m:
                return s
        return else_score


def is_st_name(name: str) -> bool:
    if not isinstance(name, str):
        return False
    s = name.upper()
    return ("ST" in s) or ("退市" in name)


# ===================== 主打分流程 =====================

def run_scoring(
    config_path: str = None,
    base_path: str = None,
    enriched_path: str = None,
    user_db_path: str = None,
    min_score: float = 70,
    write_csv: bool = True,
) -> dict:
    """
    执行全市场打分，结果写入 stock_pool 表。

    返回：
        {
            "total": int,          # 总标的数
            "candidates": int,     # 候选数(通过全部过滤)
            "pool_count": int,     # 写入优选池的数量(score >= min_score)
            "data": list[dict],    # 优选池详情
        }
    """
    # 路径默认值
    # __file__ 是 scripts/scoring/scoring.py，上两级到项目根
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if config_path is None:
        config_path = os.path.join(project_root, "config", "scoring.yaml")
    # 数据文件默认放在项目根目录（与 score_universe.py 从 cfg_dir 解析保持一致）
    cfg_dir_for_defaults = Path(config_path).resolve().parent.parent  # config/ -> project_root
    if base_path is None:
        base_path = str(cfg_dir_for_defaults / "stock_industry_base.csv")
    if enriched_path is None:
        enriched_path = str(cfg_dir_for_defaults / "market_enriched.csv")
    if user_db_path is None:
        user_db_path = os.path.join(project_root, "user_data.db")

    cfg = load_yaml(config_path)
    cfg_dir = Path(config_path).resolve().parent

    # ========== 1. 读入基础数据 ==========
    df_base = pd.read_csv(base_path, dtype=str)
    df_en = pd.read_csv(enriched_path, dtype=str)

    df_base["ts_code"] = df_base["ts_code"].astype(str)
    df_en["ts_code"] = df_en["ts_code"].astype(str)

    df = pd.merge(df_base, df_en, on="ts_code", how="inner", suffixes=("", "_en"))
    print(f"合并后标的数: {len(df)}")

    # ========== 2. 正向三项打分 ==========
    positives = cfg.get("positives", {})
    weights = positives.get("weights", {})

    pb_cfg = positives.get("pb", {})
    pe_cfg = positives.get("pe_ttm", {})
    dist_cfg = positives.get("distance_from_1y_low", {})

    pb_field = pb_cfg.get("field", "pb")
    pb_dir = pb_cfg.get("direction", "lower_better")
    pb_anchor_default = float(pb_cfg.get("anchor_default", 1.0))
    pb_ind_field = pb_cfg.get("industry_field_for_anchor", "tdx_l1")
    pb_anchors = pb_cfg.get("anchors_by_industry", {})

    pe_field = pe_cfg.get("field", "pe_ttm")
    pe_dir = pe_cfg.get("direction", "lower_better")
    pe_anchor_default = float(pe_cfg.get("anchor_default", 8.0))
    pe_ind_field = pe_cfg.get("industry_field_for_anchor", "tdx_l1")
    pe_anchors = pe_cfg.get("anchors_by_industry", {})

    dist_field = dist_cfg.get("field", "rise_from_1y_low_pct")
    dist_dir = dist_cfg.get("direction", "lower_better")
    dist_buckets = dist_cfg.get("buckets", [])

    pb_tiers_mode = pb_cfg.get("tiers_mode", "ratio")
    pb_ignore_le_zero = bool(pb_cfg.get("ignore_if_le_zero", False))
    pe_tiers_mode = pe_cfg.get("tiers_mode", "ratio")
    pe_ignore_le_zero = bool(pe_cfg.get("ignore_if_le_zero", False))

    pb_scores = []
    pe_scores = []
    dist_scores = []
    anchor_pb_list = []

    for _, row in df.iterrows():
        ind_pb = row.get(pb_ind_field)
        anc_pb = pb_anchors.get(ind_pb, pb_anchor_default)
        anchor_pb_list.append(to_float(anc_pb))
        pb_scores.append(
            score_anchor_tiers(
                row.get(pb_field), anc_pb, pb_cfg.get("tiers", []),
                direction=pb_dir,
                tiers_mode=pb_tiers_mode,
                ignore_if_le_zero=pb_ignore_le_zero,
            )
        )

        ind_pe = row.get(pe_ind_field)
        anc_pe = pe_anchors.get(ind_pe, pe_anchor_default)
        pe_scores.append(
            score_anchor_tiers(
                row.get(pe_field), anc_pe, pe_cfg.get("tiers", []),
                direction=pe_dir,
                tiers_mode=pe_tiers_mode,
                ignore_if_le_zero=pe_ignore_le_zero,
            )
        )

        dist_scores.append(
            score_buckets(row.get(dist_field), dist_buckets, direction=dist_dir)
        )

    df["pb_score"] = pb_scores
    df["pe_score"] = pe_scores
    df["dist_score"] = dist_scores
    df["anchor_pb"] = anchor_pb_list

    # 加权总分
    w_pb = float(weights.get("pb", 0))
    w_pe = float(weights.get("pe_ttm", 0))
    w_dist = float(weights.get("distance_from_1y_low", 0))

    total_scores = []
    for _, row in df.iterrows():
        parts = []
        if row["pb_score"] is not None:
            parts.append((w_pb, row["pb_score"]))
        if row["pe_score"] is not None:
            parts.append((w_pe, row["pe_score"]))
        if row["dist_score"] is not None:
            parts.append((w_dist, row["dist_score"]))

        if not parts:
            total_scores.append(None)
            continue

        w_sum = sum(p[0] for p in parts)
        if w_sum <= 0:
            total_scores.append(None)
            continue

        score = sum(p[0] * p[1] for p in parts) / w_sum
        total_scores.append(round(score, 2))

    df["total_score"] = total_scores

    # ========== 3. 负面条件 & 重大事件（与 score_universe.py 对齐） ==========
    negatives_cfg = cfg.get("negatives", {})
    treat_missing_as_pass = bool(negatives_cfg.get("treat_missing_as_pass", True))

    # 收集 risk 文件路径
    risk_file_set: set = set()
    for key, rule in negatives_cfg.items():
        if key == "treat_missing_as_pass":
            continue
        if isinstance(rule, dict) and "source_file" in rule:
            risk_file_set.add(rule["source_file"])
    major_cfg = cfg.get("major_events", {})
    if isinstance(major_cfg, dict) and "source_file" in major_cfg:
        risk_file_set.add(major_cfg["source_file"])

    risk_df = None
    cfg_dir = Path(config_path).resolve().parent
    if risk_file_set:
        risk_source_file = list(risk_file_set)[0]
        # 路径相对于 config/ 目录
        risk_path = cfg_dir / risk_source_file
        # 若不在 config/ 下则直接用项目根目录
        if not risk_path.exists():
            risk_path = Path(project_root) / risk_source_file
        if risk_path.exists():
            print(f"加载风险数据：{risk_path}")
            risk_df = pd.read_csv(str(risk_path), dtype=str)
            risk_df["ts_code"] = risk_df["ts_code"].astype(str)
        else:
            print(f"警告：未找到风险数据文件 {risk_source_file}，"
                  f"treat_missing_as_pass={treat_missing_as_pass}")

    if risk_df is not None:
        df = df.merge(risk_df, on="ts_code", how="left", suffixes=("", "_risk"))

    df["pass_negatives"] = True
    df["negative_reasons"] = ""

    # 1) 负债率 < 行业中位数
    debt_rule = negatives_cfg.get("debt_ratio")
    if isinstance(debt_rule, dict) and "field_pct" in debt_rule and "by_industry_field" in debt_rule:
        if risk_df is not None:
            field_pct = debt_rule["field_pct"]
            ind_field = debt_rule["by_industry_field"]
            cond = debt_rule.get("condition", "below_industry_median")
            if field_pct in df.columns and ind_field in df.columns and cond == "below_industry_median":
                tmp = df[[ind_field, field_pct]].copy()
                tmp[field_pct] = pd.to_numeric(tmp[field_pct], errors="coerce")
                med = tmp.groupby(ind_field)[field_pct].median().rename("debt_median")
                df = df.merge(med, left_on=ind_field, right_index=True, how="left")
                mask_fail = (
                    (pd.to_numeric(df[field_pct], errors="coerce") > df["debt_median"])
                    & df[field_pct].notna()
                    & df["debt_median"].notna()
                )
                df.loc[mask_fail, "pass_negatives"] = False
                df.loc[mask_fail, "negative_reasons"] = (
                    df.loc[mask_fail, "negative_reasons"].astype(str) + "; debt_ratio>industry_median"
                )
        else:
            if not treat_missing_as_pass:
                df["pass_negatives"] = False
                df["negative_reasons"] = df["negative_reasons"].astype(str) + "; missing_risk_debt_ratio"

    # 2) 商誉+无形资产占比
    goodwill_rule = negatives_cfg.get("goodwill_intangibles")
    if isinstance(goodwill_rule, dict) and "field_pct" in goodwill_rule and "max_pct" in goodwill_rule:
        field_pct = goodwill_rule["field_pct"]
        max_pct = float(goodwill_rule["max_pct"])
        if risk_df is not None and field_pct in df.columns:
            val = pd.to_numeric(df[field_pct], errors="coerce")
            mask_fail = val > max_pct
            df.loc[mask_fail, "pass_negatives"] = False
            df.loc[mask_fail, "negative_reasons"] = (
                df.loc[mask_fail, "negative_reasons"].astype(str) + "; goodwill_intangibles_too_high"
            )
        else:
            if not treat_missing_as_pass:
                df["pass_negatives"] = False
                df["negative_reasons"] = df["negative_reasons"].astype(str) + "; missing_goodwill_intangibles"

    # 3) 控股股东质押比例
    pledge_rule = negatives_cfg.get("controller_pledge")
    if isinstance(pledge_rule, dict) and "field_pct" in pledge_rule and "max_pct" in pledge_rule:
        field_pct = pledge_rule["field_pct"]
        max_pct = float(pledge_rule["max_pct"])
        if risk_df is not None and field_pct in df.columns:
            val = pd.to_numeric(df[field_pct], errors="coerce")
            mask_fail = val > max_pct
            df.loc[mask_fail, "pass_negatives"] = False
            df.loc[mask_fail, "negative_reasons"] = (
                df.loc[mask_fail, "negative_reasons"].astype(str) + "; controller_pledge_too_high"
            )
        else:
            if not treat_missing_as_pass:
                df["pass_negatives"] = False
                df["negative_reasons"] = df["negative_reasons"].astype(str) + "; missing_controller_pledge"

    # ========== 4. 重大事件 / ST / 退市 ==========
    df["has_major_event"] = False
    df["major_event_reasons"] = ""

    excl_cfg = cfg.get("exclusions", {})
    if excl_cfg.get("exclude_if_st_in_name", True) and "name" in df.columns:
        mask_st = df["name"].apply(is_st_name)
        df.loc[mask_st, "has_major_event"] = True
        df.loc[mask_st, "major_event_reasons"] = (
            df.loc[mask_st, "major_event_reasons"].astype(str) + "; name_contains_ST_or_退市"
        )

    if risk_df is not None and isinstance(major_cfg, dict):
        st_field = major_cfg.get("st_flag_field")
        major_field = major_cfg.get("major_flag_field")
        if st_field and st_field in df.columns:
            mask = df[st_field].astype(str).isin(["1", "True", "true", "是", "Y", "y"])
            df.loc[mask, "has_major_event"] = True
            df.loc[mask, "major_event_reasons"] = (
                df.loc[mask, "major_event_reasons"].astype(str) + "; st_flag_from_risk"
            )
        if major_field and major_field in df.columns:
            mask = df[major_field].astype(str).isin(["1", "True", "true", "是", "Y", "y"])
            df.loc[mask, "has_major_event"] = True
            df.loc[mask, "major_event_reasons"] = (
                df.loc[mask, "major_event_reasons"].astype(str) + "; major_event_flag"
            )

    # pass_all_filters = 通过 negatives & 无重大事件 & 硬性剔除
    # （先设好，下面 exclusions 段会叠加 pass_exclusions）
    df["pass_exclusions"] = True
    df["exclusion_reasons"] = ""

    # ========== 4b. 硬性剔除（价格、市值）—— 对齐 score_universe.py ==========
    min_price = float(excl_cfg.get("min_price", 0) or 0)
    min_mkt_cap = float(excl_cfg.get("min_market_cap", 0) or 0)

    if min_price > 0 and "latest" in df.columns:
        price = pd.to_numeric(df["latest"], errors="coerce")
        mask = price < min_price
        df.loc[mask, "pass_exclusions"] = False
        df.loc[mask, "exclusion_reasons"] = (
            df.loc[mask, "exclusion_reasons"].astype(str) + f"; price<{min_price}"
        )

    if min_mkt_cap > 0:
        mkt_cols = [c for c in ["market_cap", "total_mv", "cir_mv", "流通市值"] if c in df.columns]
        if mkt_cols:
            col = mkt_cols[0]
            val = pd.to_numeric(df[col], errors="coerce")
            mask = val < min_mkt_cap
            df.loc[mask, "pass_exclusions"] = False
            df.loc[mask, "exclusion_reasons"] = (
                df.loc[mask, "exclusion_reasons"].astype(str) + f"; {col}<{min_mkt_cap}"
            )

    df["pass_all_filters"] = df["pass_negatives"] & (~df["has_major_event"]) & df["pass_exclusions"]

    # ========== 5. 筛选候选池 ==========
    # 要求 pb_score 和 pe_score 都不为空，阻断"只有 dist_score 的混合口径"进入优选池
    mask_candidate = (
        df["pass_all_filters"]
        & df["total_score"].notna()
        & (df["total_score"] >= min_score)
        & df["pb_score"].notna()
        & df["pe_score"].notna()
    )
    df_pool = df[mask_candidate].copy()
    df_pool = df_pool.sort_values("total_score", ascending=False)

    no_val = int((df["pb_score"].isna() | df["pe_score"].isna()).sum())
    no_neg = int((~df["pass_negatives"]).sum())
    no_major = int(df["has_major_event"].sum())
    print(f"总标的数: {len(df)}")
    print(f"因 PB/PE 缺失被排除: {no_val}")
    print(f"因 negatives 被排除: {no_neg}")
    print(f"因 major_events/ST 被排除: {no_major}")
    print(f"候选数(score≥{min_score} & 通过全部过滤 & PB/PE可用): {len(df_pool)}")

    # ========== 5. 写入 stock_pool 表 ==========
    pool_records = []
    for _, row in df_pool.iterrows():
        pb_val = to_float(row.get(pb_field))
        pe_val = to_float(row.get(pe_field))
        anc = to_float(row.get("anchor_pb"))
        industry = row.get(pb_ind_field) or row.get("tdx_l1", "")

        pool_records.append({
            "ts_code": row["ts_code"],
            "name": row.get("name", ""),
            "industry": industry,
            "pb": pb_val,
            "pe": pe_val,
            "anchor_pb": anc,
            "score": row["total_score"],
            "pb_score": row.get("pb_score"),
            "pe_score": row.get("pe_score"),
            "dist_score": row.get("dist_score"),
        })

    if os.path.exists(user_db_path):
        conn = sqlite3.connect(user_db_path)
        c = conn.cursor()

        # 确保 stock_pool 表有新列
        try:
            c.execute("ALTER TABLE stock_pool ADD COLUMN pb_score REAL")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE stock_pool ADD COLUMN pe_score REAL")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE stock_pool ADD COLUMN dist_score REAL")
        except sqlite3.OperationalError:
            pass

        # 清空旧数据，写入新数据
        c.execute("DELETE FROM stock_pool")
        now_ts = sh_timestamp_str()
        for rec in pool_records:
            rec["updated_at"] = now_ts
        c.executemany('''
            INSERT INTO stock_pool (ts_code, name, industry, pb, pe, anchor_pb, score,
                                    pb_score, pe_score, dist_score, updated_at)
            VALUES (:ts_code, :name, :industry, :pb, :pe, :anchor_pb, :score,
                    :pb_score, :pe_score, :dist_score, :updated_at)
        ''', pool_records)

        conn.commit()
        conn.close()
        print(f"✅ 已写入 stock_pool 表: {len(pool_records)} 条")

    # ========== 6. 可选：输出 CSV ==========
    if write_csv:
        outputs = cfg.get("outputs", {})
        scored_file = outputs.get("file_scored", "config/scored_candidates.csv")
        scored_path = os.path.join(os.path.dirname(config_path), os.path.basename(scored_file))
        df_pool.to_csv(scored_path, index=False, encoding="utf-8-sig")
        print(f"CSV 输出: {scored_path}")

    return {
        "total": len(df),
        "candidates": len(df_pool),
        "pool_count": len(pool_records),
        "data": pool_records,
    }


# ===================== CLI 入口 =====================

def main():
    parser = argparse.ArgumentParser(description="统一打分入口 — 写入 stock_pool 表")
    parser.add_argument("--config", default=None, help="YAML 配置路径 (默认: config/scoring.yaml)")
    parser.add_argument("--base", default=None, help="base CSV 路径")
    parser.add_argument("--enriched", default=None, help="enriched CSV 路径")
    parser.add_argument("--db", default=None, help="user_data.db 路径")
    parser.add_argument("--min-score", type=float, default=70, help="最低入池分数 (默认: 70)")
    parser.add_argument("--no-csv", action="store_true", help="不输出 CSV")
    args = parser.parse_args()

    result = run_scoring(
        config_path=args.config,
        base_path=args.base,
        enriched_path=args.enriched,
        user_db_path=args.db,
        min_score=args.min_score,
        write_csv=not args.no_csv,
    )

    print(f"\n=== 打分完成 ===")
    print(f"总标的: {result['total']}  |  入池: {result['pool_count']}")


if __name__ == "__main__":
    main()
