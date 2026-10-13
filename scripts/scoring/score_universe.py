# -*- coding: utf-8 -*-
"""
score_universe.py
-----------------
根据 YAML 规则，对 A 股全市场做“估值+位置”打分。

用法示例：
  python score_universe.py --config config/scoring_l1_primary.cleaned.yaml

依赖：
  pip install pandas pyyaml
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import pandas as pd
import yaml


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_path(base_dir: Path, p: str) -> Path:
    p = p.strip()
    path = Path(p)
    if not path.is_absolute():
        path = (base_dir / p).resolve()
    return path


def _valid_score(x) -> bool:
    """分项分数是否可参与加权：None / NaN / Inf 均视为缺失。"""
    v = to_float(x)
    return v is not None and math.isfinite(v)


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
    """
    针对 PB / PE 这种“有行业锚点 + 分档”的打分。

    约定（direction="lower_better"）：
      - value <= anchor            → 命中 delta=0 档
      - value <= anchor*(1+delta) → 命中对应档
      - 其余                        → else 分数

    tiers 形如：
      - {delta: 0.0, score: 100}
      - {delta: 0.1, score: 90}
      - ...
      - {else: 25}
    """
    v = to_float(value)
    a = to_float(anchor)
    if v is None or a is None or a <= 0:
        return None
    if ignore_if_le_zero and v <= 0:
        return None

    # 取 else 分数 + 正常档位
    else_score = None
    norm_tiers: List[Tuple[float, float]] = []
    for t in tiers:
        if "else" in t:
            else_score = float(t["else"])
        elif "delta" in t and "score" in t:
            norm_tiers.append((float(t["delta"]), float(t["score"])))
    norm_tiers.sort(key=lambda x: x[0])

    if tiers_mode == "additive":
        diff = v - a
    else:
        diff = (v - a) / a

    if direction == "lower_better":
        # 一旦低于锚点，直接按 delta=0 档
        if v <= a:
            if norm_tiers:
                return norm_tiers[0][1]
            return else_score
        for delta, score in norm_tiers:
            if diff <= delta:
                return score
        return else_score
    else:
        # higher_better 的情况：距离锚点越高越好（这里一般用不到，先保留）
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
    """
    针对“离一年低点涨幅”这种简单分桶：
      buckets 形如：
        - {max: 3, score: 100}
        - {max: 6, score: 90}
        - ...
        - {else: 25}
    """
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


def main():
    parser = argparse.ArgumentParser(description="根据 YAML 规则对全市场做估值打分")
    parser.add_argument(
        "--config",
        required=True,
        help="配置文件 YAML 路径，例如 config/scoring_l1_primary.cleaned.yaml",
    )
    args = parser.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg_dir = cfg_path.parent
    cfg = load_yaml(cfg_path)

    # ========== 1. 读入基础数据 ==========
    universe = cfg.get("universe", {})
    base_file = resolve_path(cfg_dir, universe.get("base_file", "stock_industry_base.csv"))
    enriched_file = resolve_path(cfg_dir, universe.get("enriched_file", "market_enriched.csv"))
    db_path = resolve_path(cfg_dir, universe.get("db_path", "market.db"))  # 目前未直接使用

    print(f"使用基表: {base_file}")
    print(f"使用 enriched: {enriched_file}")
    print(f"DB 路径（本脚本未直接使用）: {db_path}")

    df_base = pd.read_csv(base_file, dtype=str)
    df_en = pd.read_csv(enriched_file, dtype=str)

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

    # PB
    pb_field = pb_cfg.get("field", "pb")
    pb_dir = pb_cfg.get("direction", "lower_better")
    pb_anchor_default = float(pb_cfg.get("anchor_default", 1.0))
    pb_ind_field = pb_cfg.get("industry_field_for_anchor", "tdx_l1")
    pb_anchors = pb_cfg.get("anchors_by_industry", {})
    pb_tiers_mode = pb_cfg.get("tiers_mode", "ratio")
    pb_ignore_le_zero = bool(pb_cfg.get("ignore_if_le_zero", False))

    # PE
    pe_field = pe_cfg.get("field", "pe_ttm")
    pe_dir = pe_cfg.get("direction", "lower_better")
    pe_anchor_default = float(pe_cfg.get("anchor_default", 8.0))
    pe_ind_field = pe_cfg.get("industry_field_for_anchor", "tdx_l1")
    pe_anchors = pe_cfg.get("anchors_by_industry", {})
    pe_tiers_mode = pe_cfg.get("tiers_mode", "ratio")
    pe_ignore_le_zero = bool(pe_cfg.get("ignore_if_le_zero", False))

    # 距一年低点
    dist_field = dist_cfg.get("field", "rise_from_1y_low_pct")
    dist_dir = dist_cfg.get("direction", "lower_better")
    dist_buckets = dist_cfg.get("buckets", [])

    pb_scores: List[Optional[float]] = []
    pe_scores: List[Optional[float]] = []
    dist_scores: List[Optional[float]] = []

    for _, row in df.iterrows():
        # PB
        ind_pb = row.get(pb_ind_field)
        anc_pb = pb_anchors.get(ind_pb, pb_anchor_default)
        pb_scores.append(
            score_anchor_tiers(
                row.get(pb_field),
                anc_pb,
                pb_cfg.get("tiers", []),
                direction=pb_dir,
                tiers_mode=pb_tiers_mode,
                ignore_if_le_zero=pb_ignore_le_zero,
            )
        )

        # PE
        ind_pe = row.get(pe_ind_field)
        anc_pe = pe_anchors.get(ind_pe, pe_anchor_default)
        pe_scores.append(
            score_anchor_tiers(
                row.get(pe_field),
                anc_pe,
                pe_cfg.get("tiers", []),
                direction=pe_dir,
                tiers_mode=pe_tiers_mode,
                ignore_if_le_zero=pe_ignore_le_zero,
            )
        )

        # 距一年低点
        dist_scores.append(
            score_buckets(
                row.get(dist_field),
                dist_buckets,
                direction=dist_dir,
            )
        )

    df["pb_score"] = pb_scores
    df["pe_score"] = pe_scores
    df["dist_score"] = dist_scores

    # 归一化加权总分：缺一项就重算权重
    w_pb = float(weights.get("pb", 0))
    w_pe = float(weights.get("pe_ttm", 0))
    w_dist = float(weights.get("distance_from_1y_low", 0))

    total_scores: List[Optional[float]] = []
    for _, row in df.iterrows():
        parts = []
        if _valid_score(row["pb_score"]):
            parts.append(("pb", w_pb, row["pb_score"]))
        if _valid_score(row["pe_score"]):
            parts.append(("pe_ttm", w_pe, row["pe_score"]))
        if _valid_score(row["dist_score"]):
            parts.append(("distance_from_1y_low", w_dist, row["dist_score"]))

        if not parts:
            total_scores.append(None)
            continue

        w_sum = sum(p[1] for p in parts)
        if w_sum <= 0:
            total_scores.append(None)
            continue

        score = sum(p[1] * p[2] for p in parts) / w_sum
        total_scores.append(score)

    df["total_score"] = total_scores

    # ========== 3. 负面条件 & 重大事件（宽松处理） ==========
    negatives_cfg = cfg.get("negatives", {})
    treat_missing_as_pass = bool(negatives_cfg.get("treat_missing_as_pass", True))

    # 找出需要的风险文件
    risk_file_set = set()
    for key, rule in negatives_cfg.items():
        if key == "treat_missing_as_pass":
            continue
        if isinstance(rule, dict) and "source_file" in rule:
            risk_file_set.add(rule["source_file"])
    major_cfg = cfg.get("major_events", {})
    if isinstance(major_cfg, dict) and "source_file" in major_cfg:
        risk_file_set.add(major_cfg["source_file"])

    risk_df = None
    risk_source_file = None
    if risk_file_set:
        risk_source_file = list(risk_file_set)[0]
        risk_path = resolve_path(cfg_dir, risk_source_file)
        if risk_path.exists():
            print(f"加载风险数据：{risk_path}")
            risk_df = pd.read_csv(risk_path, dtype=str)
            risk_df["ts_code"] = risk_df["ts_code"].astype(str)
        else:
            print(f"警告：未找到风险数据文件：{risk_path} ，负面/重大事件规则将按 treat_missing_as_pass={treat_missing_as_pass} 处理")

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
                    (df[field_pct].astype(float) > df["debt_median"])
                    & df[field_pct].notna()
                    & df["debt_median"].notna()
                )
                df.loc[mask_fail, "pass_negatives"] = False
                df.loc[mask_fail, "negative_reasons"] = (
                    df.loc[mask_fail, "negative_reasons"].astype(str)
                    + "; debt_ratio>industry_median"
                )
        else:
            if not treat_missing_as_pass:
                df["pass_negatives"] = False
                df["negative_reasons"] = (
                    df["negative_reasons"].astype(str) + "; missing_risk_debt_ratio"
                )

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
                df.loc[mask_fail, "negative_reasons"].astype(str)
                + "; goodwill_intangibles_too_high"
            )
        else:
            if not treat_missing_as_pass:
                df["pass_negatives"] = False
                df["negative_reasons"] = (
                    df["negative_reasons"].astype(str) + "; missing_goodwill_intangibles"
                )

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
                df.loc[mask_fail, "negative_reasons"].astype(str)
                + "; controller_pledge_too_high"
            )
        else:
            if not treat_missing_as_pass:
                df["pass_negatives"] = False
                df["negative_reasons"] = (
                    df["negative_reasons"].astype(str) + "; missing_controller_pledge"
                )

    # ========== 4. 重大事件 / ST / 退市 ==========
    df["has_major_event"] = False
    df["major_event_reasons"] = ""

    # 名称中含 ST / 退市
    excl_cfg = cfg.get("exclusions", {})
    if excl_cfg.get("exclude_if_st_in_name", True):
        mask_st = df["name"].apply(is_st_name)
        df.loc[mask_st, "has_major_event"] = True
        df.loc[mask_st, "major_event_reasons"] = (
            df.loc[mask_st, "major_event_reasons"].astype(str)
            + "; name_contains_ST_or_退市"
        )

    # 风险文件中的 ST / 重大事件标志
    if risk_df is not None and isinstance(major_cfg, dict):
        st_field = major_cfg.get("st_flag_field")
        major_field = major_cfg.get("major_flag_field")

        if st_field and st_field in df.columns:
            mask = df[st_field].astype(str).isin(["1", "True", "true", "是", "Y", "y"])
            df.loc[mask, "has_major_event"] = True
            df.loc[mask, "major_event_reasons"] = (
                df.loc[mask, "major_event_reasons"].astype(str)
                + "; st_flag_from_risk"
            )

        if major_field and major_field in df.columns:
            mask = df[major_field].astype(str).isin(["1", "True", "true", "是", "Y", "y"])
            df.loc[mask, "has_major_event"] = True
            df.loc[mask, "major_event_reasons"] = (
                df.loc[mask, "major_event_reasons"].astype(str)
                + "; major_event_flag"
            )

    # ========== 5. 硬性剔除（价格、市值等） ==========
    df["pass_exclusions"] = True
    df["exclusion_reasons"] = ""

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
                df.loc[mask, "exclusion_reasons"].astype(str)
                + f"; {col}<{min_mkt_cap}"
            )

    # 通过全部硬条件 = 没严重风险 & 没重大事件 & 没触发排除
    df["pass_all_filters"] = df["pass_negatives"] & (~df["has_major_event"]) & df["pass_exclusions"]

    # ========== 6. 输出三张表 ==========
    outputs = cfg.get("outputs", {})
    min_total_score = float(outputs.get("min_total_score", 0) or 0)
    file_scored = resolve_path(cfg_dir, outputs.get("file_scored", "scored_candidates.csv"))
    file_three = resolve_path(cfg_dir, outputs.get("file_three_pos", "three_positives_table.csv"))
    file_rejected = resolve_path(cfg_dir, outputs.get("file_rejected", "rejected_with_reasons.csv"))

    # 候选池：通过全部过滤 & 总分 ≥ 阈值
    mask_candidate = df["pass_all_filters"] & df["total_score"].notna() & (df["total_score"] >= min_total_score)
    df_candidates = df[mask_candidate].copy()

    # “三正交叉表”：这里只保留候选池（想看全市场可以改成 df）
    df_three = df_candidates[[
        "ts_code", "name", "tdx_l1", "tdx_l2",
        pb_field, "pb_score",
        pe_field, "pe_score",
        dist_field, "dist_score",
        "total_score",
    ]].copy()

    # 被拒绝：没通过过滤 或 总分不足 或 无总分
    mask_rej = (~df["pass_all_filters"]) | df["total_score"].isna() | (df["total_score"] < min_total_score)
    df_rejected = df[mask_rej].copy()

    df_candidates.to_csv(file_scored, index=False, encoding="utf-8-sig")
    df_three.to_csv(file_three, index=False, encoding="utf-8-sig")
    df_rejected.to_csv(file_rejected, index=False, encoding="utf-8-sig")

    print("=== 打分完成 ===")
    print(f"总标的数: {len(df)}")
    print(f"候选数(通过全部过滤 & total_score≥{min_total_score}): {len(df_candidates)}")
    print(f"Scored candidates: {file_scored}")
    print(f"Three positives table: {file_three}")
    print(f"Rejected with reasons: {file_rejected}")


if __name__ == "__main__":
    main()
