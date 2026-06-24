# -*- coding: utf-8 -*-
"""
score_candidates.py — 行业锚点驱动的“台阶打分”
- PB/PE：按行业锚点 + tiers 自动生成分数桶
- 距一年低点：沿用固定桶
- 三负 / 一重大 / 硬性规则 / 三张输出表
"""
import os
import math
import argparse
import pandas as pd

try:
    import yaml
except ModuleNotFoundError:
    raise SystemExit("请先安装依赖： pip install pyyaml pandas")

def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def read_csv(path: str) -> pd.DataFrame:
    if not path or not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"ts_code": str})

# -------- scoring helpers --------
def build_buckets_from_anchor(anchor: float, tiers: list[dict], mode: str, direction: str) -> list[dict]:
    """
    根据锚点 + tiers 生成桶：
      - additive: lower_better → max = anchor + delta
      - multiplicative: lower_better → max = anchor * delta
    仅实现 lower_better（PB/PE）足够用了，如后续有更高更好再扩展
    """
    buckets = []
    for t in tiers:
        if "else" in t:
            buckets.append({"else": t["else"], "score": float(t.get("score", t["else"]))})
        else:
            delta = float(t.get("delta", 0))
            if mode == "multiplicative":
                thr = anchor * delta
            else:  # additive
                thr = anchor + delta
            buckets.append({"max": thr, "score": float(t["score"])})
    # 确保按阈值升序
    buckets_sorted = [b for b in buckets if "max" in b]
    buckets_sorted.sort(key=lambda x: x["max"])
    tails = [b for b in buckets if "else" in b]
    return buckets_sorted + tails

def pick_bucket_score(value, buckets: list[dict], direction: str) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    if direction == "lower_better":
        for b in buckets:
            if "max" in b and value <= b["max"]:
                return float(b["score"])
        # else 分支
        last = buckets[-1]
        return float(last.get("score", 0.0)) if "else" in last else 0.0
    else:
        # 如未来有 higher_better，可在此扩展
        return 0.0

def get_anchor(cfg_metric: dict, industry: str) -> float:
    anchors = cfg_metric.get("anchors_by_industry") or {}
    return float(anchors.get(industry, cfg_metric.get("anchor_default", 0)))

def to_bool(x):
    if isinstance(x, (int, float)): return x != 0
    if isinstance(x, str): return x.strip().lower() in ("1","true","yes","y","t")
    return False

# -------- negatives / major --------
def apply_three_negatives(row, cfg_neg: dict, industry_medians: dict) -> tuple[bool, list[str]]:
    reasons = []; ok = True
    def fail(msg): nonlocal ok; ok = False; reasons.append(msg)
    pass_missing = cfg_neg.get("treat_missing_as_pass", False)

    # 资产负债率 < 行业中位数
    dr = cfg_neg["debt_ratio"]; dr_field = dr["field_pct"]; by_ind = dr["by_industry_field"]
    val = pd.to_numeric(row.get(dr_field), errors="coerce")
    ind = row.get(by_ind)
    med = industry_medians.get(ind) if ind in industry_medians else None
    if pd.isna(val) or med is None:
        if not pass_missing: fail("资产负债率/行业中位数缺失")
    else:
        if not (val < med): fail(f"资产负债率未低于行业中位数({val:.1f}% ≥ {med:.1f}%)")

    # 商誉+无形占比
    gi = cfg_neg["goodwill_intangibles"]; v = pd.to_numeric(row.get(gi["field_pct"]), errors="coerce")
    if pd.isna(v):
        if not pass_missing: fail("商誉及无形资产占比缺失")
    else:
        if v > float(gi["max_pct"]): fail(f"商誉及无形资产占比 {v:.1f}% 超阈值 {gi['max_pct']:.1f}%")

    # 质押/担保率
    pl = cfg_neg["controller_pledge"]; v = pd.to_numeric(row.get(pl["field_pct"]), errors="coerce")
    if pd.isna(v):
        if not pass_missing: fail("控股股东质押/担保率缺失")
    else:
        if v > float(pl["max_pct"]): fail(f"控股股东质押/担保率 {v:.1f}% 超阈值 {pl['max_pct']:.1f}%")

    return ok, reasons

def apply_major_events(row, cfg_major: dict) -> tuple[bool, list[str]]:
    reasons = []; ok = True
    def fail(msg): nonlocal ok; ok = False; reasons.append(msg)
    if to_bool(row.get(cfg_major.get("st_flag_field","st_flag"))): fail("ST/风险警示")
    if to_bool(row.get(cfg_major.get("major_flag_field","major_event_flag"))): fail("重大事项标记")
    note = str(row.get("notes","") or "")
    kws = [str(x) for x in (cfg_major.get("keywords") or [])]
    if any(kw in note for kw in kws): fail("重大事项关键字命中")
    return ok, reasons

# -------- main --------
def main():
    ap = argparse.ArgumentParser(description="三正/三负/一重大（行业锚点自动分桶）")
    ap.add_argument("--cfg", default="scoring.yaml")
    args = ap.parse_args()

    cfg = load_yaml(args.cfg)
    base = read_csv(cfg["universe"]["base_file"])
    enriched = read_csv(cfg["universe"]["enriched_file"])
    if base.empty or enriched.empty:
        raise SystemExit("缺少基础/估值数据，请先生成 market_enriched.csv")

    df = base.merge(enriched, on="ts_code", how="inner")

    # 可选风险表
    risk_path = ((cfg.get("negatives") or {}).get("debt_ratio") or {}).get("source_file", "")
    if not risk_path:
        risk_path = (cfg.get("major_events") or {}).get("source_file", "")
    risk = read_csv(risk_path) if risk_path else pd.DataFrame()
    if not risk.empty:
        df = df.merge(risk, on="ts_code", how="left", suffixes=("",""))

    # 硬性剔除
    excl = cfg.get("exclusions", {}) or {}
    keep = [True]*len(df); rejects = []
    if excl.get("exclude_if_st_in_name", True):
        idx = df["name"].astype(str).str.contains("ST", case=False, na=False)
        for i in df[idx].index: keep[i]=False; rejects.append((df.at[i,"ts_code"], "名称含ST"))
    min_price = float(excl.get("min_price", 0) or 0)
    if min_price>0 and "latest" in df.columns:
        idx = pd.to_numeric(df["latest"], errors="coerce") < min_price
        for i in df[idx].index: keep[i]=False; rejects.append((df.at[i,"ts_code"], f"价格<{min_price}"))

    # —— 三正打分：PB/PE 用“锚点→自动分桶” ——
    pos = cfg["positives"]; w = pos["weights"]
    totals=[]; pb_s=[]; pe_s=[]; low_s=[]

    # 预先准备“距一年低点”的固定桶
    low_cfg = pos["distance_from_1y_low"]
    low_buckets = []
    for b in low_cfg["buckets"]:
        if "else" in b: low_buckets.append({"else": b["else"], "score": float(b["else"])})
        else: low_buckets.append({"max": float(b["max"]), "score": float(b["score"])})

    for _, row in df.iterrows():
        ind = row.get("tdx_l1","")

        # PB
        pb_cfg = pos["pb"]
        pb_anchor = get_anchor(pb_cfg, ind)
        pb_buckets = build_buckets_from_anchor(
            anchor=pb_anchor,
            tiers=pb_cfg["tiers"],
            mode=pb_cfg.get("tiers_mode","additive"),
            direction=pb_cfg["direction"]
        )
        pb_val = pd.to_numeric(row.get(pb_cfg["field"]), errors="coerce")
        score_pb = pick_bucket_score(pb_val, pb_buckets, pb_cfg["direction"])

        # PE(TTM)
        pe_cfg = pos["pe_ttm"]
        pe_anchor = get_anchor(pe_cfg, ind)
        pe_buckets = build_buckets_from_anchor(
            anchor=pe_anchor,
            tiers=pe_cfg["tiers"],
            mode=pe_cfg.get("tiers_mode","additive"),
            direction=pe_cfg["direction"]
        )
        pe_val = pd.to_numeric(row.get(pe_cfg["field"]), errors="coerce")
        if pd.isna(pe_val) or (pe_cfg.get("ignore_if_le_zero", True) and pe_val<=0):
            score_pe = 0.0
        else:
            score_pe = pick_bucket_score(pe_val, pe_buckets, pe_cfg["direction"])

        # 距一年低点
        low_val = pd.to_numeric(row.get(low_cfg["field"]), errors="coerce")
        score_low = pick_bucket_score(low_val, low_buckets, low_cfg["direction"])

        total = w["pb"]*score_pb + w["pe_ttm"]*score_pe + w["distance_from_1y_low"]*score_low
        totals.append(total); pb_s.append(score_pb); pe_s.append(score_pe); low_s.append(score_low)

    df["score_total"]=totals; df["score_pb"]=pb_s; df["score_pe_ttm"]=pe_s; df["score_distance_from_1y_low"]=low_s

    # —— 三负 / 一重大 ——
    neg = cfg["negatives"]; maj = cfg.get("major_events", {}) or {}
    med = {}
    dr_field = neg["debt_ratio"]["field_pct"]; by_ind = neg["debt_ratio"]["by_industry_field"]
    if dr_field in df.columns and by_ind in df.columns:
        sub = df[[by_ind, dr_field]].copy()
        sub[dr_field] = pd.to_numeric(sub[dr_field], errors="coerce")
        med = sub.groupby(by_ind)[dr_field].median().to_dict()

    pass_neg=[]; neg_rea=[]; pass_maj=[]; maj_rea=[]
    for _, row in df.iterrows():
        ok1, r1 = apply_three_negatives(row, neg, med)
        ok2, r2 = apply_major_events(row, maj)
        pass_neg.append(ok1); neg_rea.append(";".join(r1))
        pass_maj.append(ok2); maj_rea.append(";".join(r2))
    df["pass_negatives"]=pass_neg; df["neg_reasons"]=neg_rea
    df["pass_major"]=pass_maj; df["major_reasons"]=maj_rea

    # —— 汇总筛选 & 导出 ——
    out = cfg.get("outputs", {}) or {}
    min_total = float(out.get("min_total_score", 70))
    final_ok=[]
    for i, k in enumerate(keep):
        ok = k and df.at[df.index[i],"pass_negatives"] and df.at[df.index[i],"pass_major"]
        ok = ok and (df.at[df.index[i],"score_total"] >= min_total)
        final_ok.append(ok)
        if not ok:
            reason=[]
            if not k: reason.append("硬性剔除")
            if not df.at[df.index[i],"pass_negatives"]: reason.append(df.at[df.index[i],"neg_reasons"])
            if not df.at[df.index[i],"pass_major"]: reason.append(df.at[df.index[i],"major_reasons"])
            if df.at[df.index[i],"score_total"] < min_total: reason.append(f"总分<{min_total}")
            rejects.append((df.at[df.index[i],"ts_code"], ";".join([r for r in reason if r])))

    file_scored = out.get("file_scored","scored_candidates.csv")
    file_three  = out.get("file_three_pos","three_positives_table.csv")
    file_rej    = out.get("file_rejected","rejected_with_reasons.csv")

    cols_ok = ["ts_code","name","tdx_l1","latest","pb","pe_ttm","rise_from_1y_low_pct",
               "score_total","score_pb","score_pe_ttm","score_distance_from_1y_low",
               "pass_negatives","pass_major"]
    df_ok = df[final_ok].copy()
    for c in cols_ok:
        if c not in df_ok.columns: df_ok[c]=None
    df_ok[cols_ok].sort_values(["score_total","tdx_l1"], ascending=[False, True]).to_csv(file_scored, index=False, encoding="utf-8-sig")

    df_three = df[["ts_code","name","tdx_l1","pb","pe_ttm","rise_from_1y_low_pct",
                   "score_pb","score_pe_ttm","score_distance_from_1y_low","score_total"]].copy()
    df_three.sort_values(["tdx_l1","score_total"], ascending=[True, False]).to_csv(file_three, index=False, encoding="utf-8-sig")

    if rejects:
        pd.DataFrame(rejects, columns=["ts_code","reason"]).drop_duplicates().to_csv(file_rej, index=False, encoding="utf-8-sig")

    print(f"✅ 完成：{file_scored}；✅ 三正明细：{file_three}")
    if rejects: print(f"⚠️ 剔除清单：{file_rej}（{len(rejects)} 条）")

if __name__ == "__main__":
    main()
