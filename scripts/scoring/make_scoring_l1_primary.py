# -*- coding: utf-8 -*-
r"""
make_scoring_l1_primary.py

清理并标准化 scoring YAML（L1 为主轴，L2 仅做覆盖）

功能要点：
1) 将 positives.pb / positives.pe_ttm 的 industry_field_for_anchor 统一设为 tdx_l1；
2) 合并 + 清理锚点和别名：
   - 顶层 l2_anchors 并入 anchors.l2
   - 顶层 l2_aliases 并入 l2_alias_map
   - 去重、排序、保证别名“目标值”均在 anchors.l2
3) 标准化行业轴声明：industry_axes.primary/override/override_enabled，保证 overrides.l2 存在
4) 可选：从 stock_industry_base.csv 或 tdx_export.xlsx 自动补充 anchors.l2 的二级行业全集

用法示例：
# 最常用：对一份已有的 scoring YAML 做清理输出
python make_scoring_l1_primary.py --in config/scoring_l1_primary.yaml --out config/scoring_l1_primary.cleaned.yaml

# 附加：从 base csv 补充 L2 锚点全集（列名 tdx_l2）
python make_scoring_l1_primary.py --in config/scoring_l1_primary.yaml --out config/scoring_l1_primary.cleaned.yaml --base stock_industry_base.csv

# 附加：直接从 TDX 导出 Excel 补充 L2（指定二级行业列名、工作表）
python make_scoring_l1_primary.py --in config/scoring_l1_primary.yaml --out config/scoring_l1_primary.cleaned.yaml \
  --tdx-xlsx tdx_export.xlsx --sheet 0 --col-l2 细分行业

Windows PowerShell 行续写法（示例）：
$Args = @(
  "--in","config/scoring_l1_primary.yaml",
  "--out","config/scoring_l1_primary.cleaned.yaml",
  "--base","stock_industry_base.csv"
)
python .\make_scoring_l1_primary.py @Args
"""

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    print("缺少依赖：pyyaml，请先执行： pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# 可选依赖：用于从 csv/xlsx 读二级行业
try:
    import pandas as pd  # noqa
except ModuleNotFoundError:
    pd = None


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def dump_yaml(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            allow_unicode=True,
            sort_keys=False,
            width=1000,
        )


def ensure_dict(d: dict, key: str) -> dict:
    if key not in d or not isinstance(d[key], dict):
        d[key] = {}
    return d[key]


def ensure_list(d: dict, key: str) -> list:
    if key not in d or not isinstance(d[key], list):
        d[key] = []
    return d[key]


def to_set_list(iterable) -> list:
    """去重 + 排序后的列表"""
    s = {str(x).strip() for x in iterable if isinstance(x, (str, int, float)) and str(x).strip()}
    return sorted(s)


def read_l2_from_base_csv(base_csv: Path) -> set:
    if pd is None:
        print("[WARN] 未安装 pandas，无法从 base csv 读取 L2。", file=sys.stderr)
        return set()
    try:
        df = pd.read_csv(base_csv)
        if "tdx_l2" not in df.columns:
            print(f"[WARN] {base_csv} 不含列 tdx_l2，跳过补充。", file=sys.stderr)
            return set()
        vals = set(str(x).strip() for x in df["tdx_l2"].dropna().unique())
        return {v for v in vals if v}
    except Exception as e:
        print(f"[WARN] 读取 {base_csv} 失败：{e}", file=sys.stderr)
        return set()


def read_l2_from_tdx_xlsx(xlsx: Path, sheet, col_l2: str) -> set:
    if pd is None:
        print("[WARN] 未安装 pandas/openpyxl，无法从 Excel 读取 L2。", file=sys.stderr)
        return set()
    try:
        df = pd.read_excel(xlsx, sheet_name=sheet)
        if col_l2 not in df.columns:
            print(f"[WARN] {xlsx} 工作表 {sheet} 不含列 {col_l2}，跳过补充。", file=sys.stderr)
            return set()
        vals = set(str(x).strip() for x in df[col_l2].dropna().unique())
        return {v for v in vals if v}
    except Exception as e:
        print(f"[WARN] 读取 {xlsx} 失败：{e}", file=sys.stderr)
        return set()


def unify_anchors_and_aliases(cfg: dict,
                              l2_from_sources: set,
                              primary_field: str = "tdx_l1",
                              override_field: str = "tdx_l2") -> dict:
    """核心清理逻辑"""
    # 1) 设置行业轴
    cfg["industry_axes"] = {
        "primary": primary_field,
        "override": override_field,
        "override_enabled": True,
    }
    overrides = ensure_dict(cfg, "overrides")
    ensure_dict(overrides, "l2")

    # 2) 统一 anchors.l2
    anchors = ensure_dict(cfg, "anchors")
    l2_list = ensure_list(anchors, "l2")

    # 兼容顶层 l2_anchors（列表）
    top_l2_anchors = cfg.pop("l2_anchors", None)
    if top_l2_anchors and isinstance(top_l2_anchors, list):
        l2_list.extend(top_l2_anchors)

    # 额外来源（base/xlsx）
    if l2_from_sources:
        l2_list.extend(list(l2_from_sources))

    anchors["l2"] = to_set_list(l2_list)

    # 3) 合并别名到 l2_alias_map
    l2_alias_map = ensure_dict(cfg, "l2_alias_map")

    # 顶层 l2_aliases 兼容（字典）
    top_l2_aliases = cfg.pop("l2_aliases", None)
    if top_l2_aliases and isinstance(top_l2_aliases, dict):
        # 合并（上层优先保留当前 map 的键；也可选择上层覆盖）
        for k, v in top_l2_aliases.items():
            k1 = str(k).strip()
            v1 = str(v).strip()
            if not k1 or not v1:
                continue
            # 默认采用“后者覆盖前者”的策略，避免自映射残留
            l2_alias_map[k1] = v1

    # 去掉无效别名（自映射 + 空串）
    to_del = []
    for k, v in l2_alias_map.items():
        ks = str(k).strip()
        vs = str(v).strip()
        if not ks or not vs or ks == vs:
            to_del.append(k)
    for k in to_del:
        l2_alias_map.pop(k, None)

    # 4) 确保别名目标在 anchors.l2（若不在则补上）
    l2_targets = set(str(v).strip() for v in l2_alias_map.values() if str(v).strip())
    merged = set(anchors["l2"]) | l2_targets
    anchors["l2"] = to_set_list(merged)

    # 5) 标准化 positives.pb / pe_ttm 的 anchor 取值字段（= L1）
    #    若找不到该路径则跳过，不报错（兼容不同模板）
    try:
        pb = cfg["positives"]["pb"]
        pb["industry_field_for_anchor"] = primary_field
    except Exception:
        pass
    try:
        pe = cfg["positives"]["pe_ttm"]
        pe["industry_field_for_anchor"] = primary_field
    except Exception:
        pass

    return cfg


def main():
    ap = argparse.ArgumentParser(description="清理并标准化 scoring YAML（L1为主轴，L2仅覆盖）")
    ap.add_argument("--in", dest="in_yaml", required=True, help="输入 YAML（待清理）")
    ap.add_argument("--out", dest="out_yaml", required=True, help="输出 YAML（清理后）")

    # 行业字段可自定义
    ap.add_argument("--primary", default="tdx_l1", help="主轴行业字段名（默认 tdx_l1）")
    ap.add_argument("--override", default="tdx_l2", help="覆盖行业字段名（默认 tdx_l2）")

    # 从现有数据补充 L2 锚点全集（可选其一或全部）
    ap.add_argument("--base", help="从 stock_industry_base.csv 读取 tdx_l2 补充 anchors.l2")
    ap.add_argument("--tdx-xlsx", help="从 tdx_export.xlsx 读取二级行业补充 anchors.l2")
    ap.add_argument("--sheet", default=0, help="tdx_export.xlsx 的 sheet（名称或索引），默认 0")
    ap.add_argument("--col-l2", default="细分行业", help="tdx_export.xlsx 的二级行业列名，默认 细分行业")

    args = ap.parse_args()
    in_path = Path(args.in_yaml)
    out_path = Path(args.out_yaml)

    if not in_path.exists():
        print(f"[ERR] 输入文件不存在：{in_path}", file=sys.stderr)
        sys.exit(2)

    cfg = load_yaml(in_path)

    # 收集 L2 追加来源
    l2_sources = set()
    if args.base:
        l2_sources |= read_l2_from_base_csv(Path(args.base))
    if args.tdx_xlsx:
        l2_sources |= read_l2_from_tdx_xlsx(Path(args.tdx_xlsx), args.sheet, args.col_l2)

    cfg2 = unify_anchors_and_aliases(
        cfg,
        l2_from_sources=l2_sources,
        primary_field=args.primary,
        override_field=args.override,
    )

    dump_yaml(cfg2, out_path)

    # 汇总提示
    anchors = cfg2.get("anchors", {})
    l2_list = anchors.get("l2", [])
    alias_n = len(cfg2.get("l2_alias_map", {}))
    print("✔ 清理完成：")
    print(f"  - anchors.l2 数量：{len(l2_list)}")
    print(f"  - l2_alias_map 条数：{alias_n}")
    try:
        f1 = cfg2["positives"]["pb"].get("industry_field_for_anchor")
        f2 = cfg2["positives"]["pe_ttm"].get("industry_field_for_anchor")
        print(f"  - PB/PE 锚点字段：pb={f1}，pe_ttm={f2}")
    except Exception:
        pass
    print(f"✅ 已写出：{out_path}")
    if args.base or args.tdx_xlsx:
        print("  （已启用二级行业补充源，anchors.l2 已合并去重）")


if __name__ == "__main__":
    main()
