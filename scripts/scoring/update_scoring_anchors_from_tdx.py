# -*- coding: utf-8 -*-
r"""
update_scoring_anchors_from_tdx.py
从 TDX 导出表抽取二级行业集合，写回 scoring.yaml 的 anchors.l2
- 支持二级行业列（推荐：细分行业）
- 也支持只有“一级-二级”的合并列（如：一二级行业，形如 '交通运输-公路铁路'）

用法（PowerShell 示例）：
  python .\update_scoring_anchors_from_tdx.py `
    --tdx-xlsx tdx_export.xlsx `
    --sheet 0 `
    --yaml config\scoring.yaml `
    --out  config\scoring_merged.yaml `
    --col-l2 细分行业 `
    --col-l1l2 一二级行业 `
    --mode merge    # merge=合并(默认)，replace=完全替换

注意：
- 只更新 anchors.l2，不改 l2_alias_map、特殊行业阈值等其它条目
- 若两列都提供，优先使用 --col-l2；同时也会从 --col-l1l2 解析补充
"""

import argparse
from pathlib import Path
import re
import sys
import pandas as pd
import yaml

SEP_PAT = re.compile(r"\s*[-—－–]\s*")  # 兼容各种连字符

def normalize(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    if not s or s.lower() == "nan":
        return ""
    s = (s.replace("（", "(").replace("）", ")")
           .replace("　", " ").strip())
    return s

def parse_l2_from_l1l2(val: str) -> str:
    """从形如 '一级-二级' 的字段里提取二级行业（取最后一段更稳健）"""
    v = normalize(val)
    if not v:
        return ""
    parts = SEP_PAT.split(v)
    if len(parts) == 1:
        return ""  # 无法分出二级
    return normalize(parts[-1])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tdx-xlsx", required=True, help="TDX 导出xlsx路径")
    ap.add_argument("--sheet", default="0", help="工作表索引或名称，默认0")
    ap.add_argument("--yaml", required=True, help="输入 scoring.yaml")
    ap.add_argument("--out",  required=True, help="输出 scoring_merged.yaml（不覆盖原文件）")

    # 行业列
    ap.add_argument("--col-l2", default="", help="二级行业列名（推荐：细分行业）")
    ap.add_argument("--col-l1l2", default="", help="一级-二级合并列（如：一二级行业，内容形如 '交通运输-公路铁路'）")

    # 合并模式
    ap.add_argument("--mode", choices=["merge","replace"], default="merge",
                    help="merge=把抽取到的二级行业与 anchors.l2 取并集；replace=完全替换 anchors.l2")

    args = ap.parse_args()

    xlsx_p = Path(args.tdx_xlsx)
    if not xlsx_p.exists():
        print(f"[ERR] 找不到文件：{xlsx_p}")
        sys.exit(1)

    # 读 Excel
    try:
        sheet = int(args.sheet) if str(args.sheet).isdigit() else args.sheet
    except Exception:
        sheet = 0
    df = pd.read_excel(xlsx_p, sheet_name=sheet, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    l2_candidates = set()

    # 优先二级行业列
    if args.col_l2 and args.col_l2 in df.columns:
        for v in df[args.col_l2].astype(str).values:
            nv = normalize(v)
            if nv:
                l2_candidates.add(nv)
    else:
        print(f"[WARN] 未找到 '--col-l2 {args.col_l2}' 列，尝试仅从 '--col-l1l2' 解析…")

    # 合并列补充
    if args.col_l1l2 and args.col_l1l2 in df.columns:
        for v in df[args.col_l1l2].astype(str).values:
            nv = parse_l2_from_l1l2(v)
            if nv:
                l2_candidates.add(nv)
    elif not args.col_l2:
        print(f"[ERR] 既没有有效的 '--col-l2' 也没有有效的 '--col-l1l2'，无法抽取二级行业。")
        sys.exit(2)

    # 读 scoring.yaml
    scoring_p = Path(args.yaml)
    if not scoring_p.exists():
        print(f"[ERR] 找不到 scoring.yaml：{scoring_p}")
        sys.exit(3)
    scoring = yaml.safe_load(scoring_p.read_text(encoding="utf-8")) or {}
    scoring.setdefault("anchors", {})
    anchors = scoring["anchors"]
    old_l2 = set([normalize(x) for x in anchors.get("l2", []) if normalize(x)])

    # 合并/替换
    if args.mode == "replace":
        new_l2 = sorted([x for x in l2_candidates if x])
        merged_mode = "REPLACE"
    else:
        new_l2 = sorted([x for x in (old_l2 | l2_candidates) if x])
        merged_mode = "MERGE"

    anchors["l2"] = new_l2

    # 输出
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(yaml.safe_dump(scoring, allow_unicode=True, sort_keys=False), encoding="utf-8")

    print(f"✔ 抽取到 L2 数量：{len(l2_candidates)}")
    print(f"✔ 原 anchors.l2 数量：{len(old_l2)}")
    print(f"✔ {merged_mode} 后 anchors.l2 数量：{len(new_l2)}")
    print(f"✅ 已写出：{out_p}")

if __name__ == "__main__":
    main()
