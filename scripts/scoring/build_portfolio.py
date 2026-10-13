# -*- coding: utf-8 -*-
"""
build_portfolio.py — 大数投资组合构建器
基于《大数投资》的“绝对胜算”策略，自动生成建议持仓清单。

核心逻辑：
1. 分层抽样：在每个 TDX L1 行业中，选出估值（PB/PB_Anchor）最低的 1 只股票。
2. 胜算计算：基于 PB 相对于 Anchor 的位置，线性插值计算胜算率 P。
   - PB >= Anchor      -> P <= 50%
   - PB = 0.4 * Anchor -> P = 95%
3. 资金配置：
   - 配置比例 Ratio = 2 * P - 1
   - 建议金额 = 总资金 * 单行业上限(3%) * Ratio

用法：
  python build_portfolio.py --total-capital 1000000
"""

import os
import argparse
import pandas as pd
import yaml
import numpy as np

# -------- 1. 配置与辅助函数 --------

def load_yaml(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def clean_data(df: pd.DataFrame, exclude_st=True, min_list_days=365) -> pd.DataFrame:
    """简单清洗：剔除ST、上市不足一年、停牌等（如有状态字段）"""
    # 假设 enriched 中已有 basic cols
    # 这里主要依赖 score_candidates 的逻辑，或者简单过滤
    if "name" in df.columns and exclude_st:
        df = df[~df["name"].str.contains("ST|退市", na=False)].copy()
    
    # 确保 PB 有效且大于0 (PB<0通常是负资产，大数投资不碰)
    df = df[pd.to_numeric(df["pb"], errors="coerce") > 0].copy()
    
    return df

def get_anchor(industry: str, cfg_pos: dict) -> float:
    """从配置获取行业锚点，默认值为 2.5 (书中均值)"""
    pb_cfg = cfg_pos.get("pb", {})
    anchors = pb_cfg.get("anchors_by_industry", {})
    default = float(pb_cfg.get("anchor_default", 2.5))
    return float(anchors.get(industry, default))

# -------- 2. 核心算法 --------

def calc_win_prob(pb: float, anchor: float) -> float:
    """
    计算胜算概率 P
    逻辑：
      - Ratio = PB / Anchor
      - Ratio >= 1.0 (PB>=Anchor) -> P = 0.50 (均值无超额胜算)
      - Ratio <= 0.4 (极度低估)   -> P = 0.95
      - 中间线性插值
    """
    if anchor <= 0: return 0.5 # 异常保护
    ratio = pb / anchor
    
    if ratio >= 1.0:
        return 0.50
    elif ratio <= 0.4:
        return 0.95
    else:
        # 线性插值: (ratio - 0.4) / (1.0 - 0.4) = (0.95 - P) / (0.95 - 0.50)
        # norm_pos (0~1) 越接近 1 代表越接近 anchor(P低)
        norm_pos = (ratio - 0.4) / 0.6
        p = 0.95 - norm_pos * 0.45 
        return round(p, 4)

def calc_allocation_ratio(prob: float) -> float:
    """
    计算资金配置比例 X (Kelly-like)
    X = 2 * P - 1
    """
    x = 2 * prob - 1
    return max(0.0, x)

# -------- 3. 核心业务逻辑 (Refactored) --------

def generate_portfolio(
    capital: float, 
    ind_cap: float, 
    config_path: str = "config/scoring.yaml",
    enriched_path: str = "market_enriched.csv",
    base_path: str = "stock_industry_base.csv"
) -> pd.DataFrame:
    """
    核心计算函数，供 API 和 CLI 共用。
    返回: result_df (包含 ts_code, name, win_prob, alloc_ratio, suggested_amt 等)
    """
    # 1. 加载数据
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config not found: {config_path}")
    cfg = load_yaml(config_path)
    
    # 路径处理：如果是相对路径，且调用方可能是 API，需确保路径正确
    # 这里简单处理：如果传入的 enriched_path 存在就用，不存在则尝试从 config 读
    real_enriched = enriched_path
    if not os.path.exists(real_enriched):
        # 尝试从 config 读取
        universe_cfg = cfg.get("universe", {})
        real_enriched = universe_cfg.get("enriched_file", "market_enriched.csv")
        
    if not os.path.exists(real_enriched):
        raise FileNotFoundError(f"Enriched data not found: {real_enriched}")

    df = pd.read_csv(real_enriched, dtype={"ts_code": str})
    
    # 合并基础信息（若缺少）
    if "tdx_l1" not in df.columns:
        real_base = base_path
        if not os.path.exists(real_base):
             # 尝试从 config 读取
            universe_cfg = cfg.get("universe", {})
            real_base = universe_cfg.get("base_file", "stock_industry_base.csv")
            
        if os.path.exists(real_base):
            base = pd.read_csv(real_base, dtype={"ts_code": str})
            cols_to_use = ["ts_code", "name", "tdx_l1"]
            # 避免重复列
            merge_cols = [c for c in cols_to_use if c in base.columns]
            df = df.merge(base[merge_cols], on="ts_code", how="left", suffixes=("", "_base"))
            if "name" not in df.columns and "name_base" in df.columns: df["name"] = df["name_base"]
            if "tdx_l1" not in df.columns and "tdx_l1_base" in df.columns: df["tdx_l1"] = df["tdx_l1_base"]

    # 2. 清洗
    df_clean = clean_data(df)
    
    # 3. 分层抽样 & 计算
    pos_cfg = cfg.get("positives", {})
    results = []
    
    grouped = df_clean.groupby("tdx_l1")
    for industry, group in grouped:
        if pd.isna(industry) or industry == "": continue
        
        anchor = get_anchor(industry, pos_cfg)
        
        group = group.copy()
        group["pb_val"] = pd.to_numeric(group["pb"], errors="coerce")
        group = group.dropna(subset=["pb_val"])
        
        if group.empty: continue
        
        # 排序选 Top 1
        top_stock = group.sort_values("pb_val", ascending=True).iloc[0]
        
        pb = top_stock["pb_val"]
        win_prob = calc_win_prob(pb, anchor)
        alloc_ratio = calc_allocation_ratio(win_prob)
        
        suggested_amt = capital * ind_cap * alloc_ratio
        
        results.append({
            "ts_code": top_stock["ts_code"],
            "name": top_stock["name"],
            "industry": industry,
            "pb": pb,
            "anchor_pb": anchor,
            "pb_rel": round(pb / anchor, 2),
            "win_prob": win_prob,
            "alloc_ratio": round(alloc_ratio, 4),
            "suggested_amt": int(suggested_amt)
        })

    if not results:
        return pd.DataFrame()

    res_df = pd.DataFrame(results)
    return res_df.sort_values("alloc_ratio", ascending=False)

# -------- 4. CLI 入口 --------

def main():
    parser = argparse.ArgumentParser(description="Build Portfolio based on Law of Large Numbers")
    parser.add_argument("--capital", type=float, default=1_000_000, help="Total Capital")
    parser.add_argument("--ind-cap", type=float, default=0.03, help="Max capital ratio per industry (default 3%)")
    parser.add_argument("--config", default="config/scoring.yaml", help="Path to parsing config")
    args = parser.parse_args()

    # 调用通用逻辑
    res_df = generate_portfolio(
        capital=args.capital,
        ind_cap=args.ind_cap,
        config_path=args.config
    )

    if res_df.empty:
        print("No valid stocks found for portfolio.")
        return
    
    # 统计
    total_invest = res_df["suggested_amt"].sum()
    total_ratio = total_invest / args.capital
    
    print("\n" + "="*40)
    print(f"PORTFOLIO BUILDER REPORT (Capital: {args.capital:,.0f})")
    print("="*40)
    print(f"Industries Covered : {len(res_df)}")
    print(f"Total Invested     : {total_invest:,.0f} ({total_ratio:.1%})")
    print(f"Cash Reserved      : {args.capital - total_invest:,.0f} ({1-total_ratio:.1%})")
    print("-" * 40)
    
    if total_ratio > 0.67:
        print(f"⚠️ WARNING: Total assignment {total_ratio:.1%} exceeds 2/3 limit.")
    else:
        print("✅ Portfolio adheres to the < 2/3 capital limit.")

    out_file = "data/portfolio_suggestion.csv"
    os.makedirs("data", exist_ok=True)
    res_df.to_csv(out_file, index=False, encoding="utf-8-sig")
    print(f"\nDetailed suggestion saved to: {out_file}")
    
    print("\nTop 10 High Conviction Picks:")
    print(res_df[["industry", "name", "pb", "anchor_pb", "win_prob", "alloc_ratio"]].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
