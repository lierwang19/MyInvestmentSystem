# -*- coding: utf-8 -*-
"""
suggest_l2_aliases.py — 从未命中的 tdx_l2 里，生成别名映射的 YAML 草案
输入：--base stock_industry_base.csv --yaml scoring.yaml
输出：l2_not_hit_freq.csv, l2_aliases_suggest.yaml
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable, Optional, Set

import pandas as pd
import yaml

def _norm(s: str) -> str:
    return s.strip().replace("–","-").replace("—","-").replace("－","-")

def _extract_nested_key(d: dict, key: str):
    if not isinstance(d, dict):
        return None
    if key in d:
        return d[key]
    for v in d.values():
        if isinstance(v, dict):
            hit = _extract_nested_key(v, key)
            if hit is not None:
                return hit
        elif isinstance(v, list):
            for i in v:
                if isinstance(i, dict):
                    hit = _extract_nested_key(i, key)
                    if hit is not None:
                        return hit
    return None

def load_anchors(yaml_path: str) -> Set[str]:
    anchors: Set[str] = set()
    if not yaml_path or not Path(yaml_path).exists():
        return anchors
    cfg = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    raw = _extract_nested_key(cfg, "l2_anchors")
    if isinstance(raw, list):
        anchors = {_norm(x) for x in raw if str(x).strip()}
    return anchors

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="stock_industry_base.csv")
    ap.add_argument("--yaml", default="config/scoring.yaml")
    ap.add_argument("--top", type=int, default=200)
    args = ap.parse_args()

    df = pd.read_csv(args.base, dtype=str)
    df["tdx_l2"] = df["tdx_l2"].fillna("").map(_norm)
    anchors = load_anchors(args.yaml)

    # 未命中
    miss = df[(df["tdx_l2"]!="") & (~df["tdx_l2"].isin(anchors))]["tdx_l2"]

    freq = miss.value_counts().reset_index()
    freq.columns = ["tdx_l2", "count"]
    Path("data").mkdir(parents=True, exist_ok=True)
    freq.to_csv("data/l2_not_hit_freq.csv", index=False, encoding="utf-8-sig")

    # 生成别名草案（右侧先自映射，便于你手动替换为目标锚点）
    alias_map = {k: k for k in freq["tdx_l2"].head(args.top)}
    out_yaml = {"l2_aliases": alias_map}
    Path("data/l2_aliases_suggest.yaml").write_text(
        yaml.safe_dump(out_yaml, allow_unicode=True, sort_keys=False),
        encoding="utf-8"
    )

    print("已导出：")
    print("- data/l2_not_hit_freq.csv")
    print("- data/l2_aliases_suggest.yaml   （请把右侧的自映射改为你想归一到的锚点名）")

if __name__ == "__main__":
    main()
