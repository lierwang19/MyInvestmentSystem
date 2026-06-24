# -*- coding: utf-8 -*-
"""
process_tdx_industry_simple.py

功能：
1. 从通达信导出的 tdx_export.xlsx 中读取：
   - 代码列（如 “代码”）
   - 名称列（如 “名称”）
   - 一二级行业列（如 “一二级行业”，形如 “银行-全国性银行”）
   - 可选：上市日期列（如 “上市日期”）
2. 将“一二级行业”拆成：
   - tdx_l1（一级行业，如 “银行”）
   - tdx_l2（二级行业，如 “全国性银行”）
3. 生成基础表 stock_industry_base.csv，列为：
   ts_code, name, tdx_l1, tdx_l2, anchor_industry, list_date
4. 可选从 scoring.yaml 中读取 L2 别名映射，将 tdx_l2 归一到 anchor_industry。
   如不提供或没有别名，则 anchor_industry == tdx_l2。
5. 支持跳过北交所（包括 43/83/87/88/89/92 开头代码）。

示例（PowerShell）：
  $ArgsList = @(
    "--tdx-xlsx","tdx_export.xlsx",
    "--sheet","0",                # “0” 会当第 0 个工作表
    "--col-code","代码",
    "--col-name","名称",
    "--col-l1l2","一二级行业",
    "--col-listdate","上市日期",
    "--yaml","config/scoring.yaml",   # 如不需要别名，可去掉这一行
    "--out","stock_industry_base.csv",
    "--skip-bj","1"
  )
  python .\\process_tdx_industry_simple.py @ArgsList
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Tuple, Dict, Any

import pandas as pd

try:
    import yaml  # type: ignore
except ModuleNotFoundError:
    yaml = None  # 只有用到 --yaml 时才需要


# ---------- 工具函数 ----------

def make_ts_code_from_tdx(raw_code: Any) -> str:
    """
    将通达信 6 位代码转成 ts_code（SZ/SH/BJ）
    规则：
      6xxxx -> .SH
      43/83/87/88/89/92 开头 -> .BJ
      其余 -> .SZ
    """
    if pd.isna(raw_code):
        return ""

    s = str(raw_code).strip()
    # 提取数字
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""

    if len(digits) < 6:
        digits = digits.zfill(6)
    else:
        digits = digits[:6]

    prefix2 = digits[:2]
    if digits.startswith("6"):
        suffix = "SH"
    elif prefix2 in ("43", "83", "87", "88", "89", "92"):
        suffix = "BJ"
    else:
        suffix = "SZ"
    return f"{digits}.{suffix}"


def split_l1_l2(val: Any) -> Tuple[str, str]:
    """
    从“一二级行业”字段中拆出一级、二级行业。
    例如：“银行-全国性银行” -> ("银行", "全国性银行")
    """
    if pd.isna(val):
        return "", ""
    text = str(val).strip()
    if not text or text in ("-", "--", "—", "－"):
        return "", ""

    for sep in ["-", "－", "—", "——"]:
        if sep in text:
            p1, p2 = text.split(sep, 1)
            return p1.strip(), p2.strip()
    # 没有分隔符，就当只有一级
    return text, ""


def normalize_listdate(val: Any) -> str | None:
    """
    将“上市日期”统一成 YYYYMMDD 字符串：
      19910403 / 19910403.0 / "1991-04-03" 等。
    解析失败返回 None。
    """
    if pd.isna(val):
        return None

    s = str(val).strip()
    if not s:
        return None

    # 先尝试直接提取 8 位数字
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 8:
        return digits

    # 再退化成通用日期解析
    try:
        dt = pd.to_datetime(s, errors="coerce")
        if pd.isna(dt):
            return None
        return dt.strftime("%Y%m%d")
    except Exception:
        return None


def load_l2_alias_map(yaml_path: str) -> Dict[str, str]:
    """
    从 scoring.yaml 中读取 L2 别名映射：
      - l2_aliases: {原名: 归一名}
      - l2_alias_map: {原名: 归一名}
    任一存在即可；都不存在则返回空 dict。
    """
    if yaml is None:
        print("[YAML] 未安装 pyyaml，忽略 --yaml")
        return {}

    p = Path(yaml_path)
    if not p.exists():
        print(f"[YAML] 文件不存在：{yaml_path}，忽略别名映射")
        return {}

    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        print("[YAML] 文件内容不是字典结构，忽略别名映射")
        return {}

    alias_map: Dict[str, str] = {}

    if isinstance(data.get("l2_aliases"), dict):
        for k, v in data["l2_aliases"].items():
            alias_map[str(k).strip()] = str(v).strip()

    if isinstance(data.get("l2_alias_map"), dict):
        for k, v in data["l2_alias_map"].items():
            alias_map[str(k).strip()] = str(v).strip()

    if alias_map:
        print(f"[YAML] 解析到别名映射：{len(alias_map)} 条；将用于 L2→锚点归一")
    else:
        print("[YAML] 未解析到别名映射；anchor_industry 将与 tdx_l2 相同")

    return alias_map


# ---------- 主函数 ----------

def main():
    ap = argparse.ArgumentParser(description="从通达信导出表生成 stock_industry_base.csv（简单版）")

    ap.add_argument("--tdx-xlsx", required=True, help="通达信导出 Excel 文件，如 tdx_export.xlsx")
    ap.add_argument("--sheet", default=0, help="工作表名或索引（默认 0）")

    ap.add_argument("--col-code", required=True, help="代码列列名，如 '代码'")
    ap.add_argument("--col-name", required=True, help="名称列列名，如 '名称'")
    ap.add_argument("--col-l1l2", required=True, help="一二级行业列列名，如 '一二级行业'")
    ap.add_argument("--col-l2", default="", help="可选：单独二级行业列，如 '细分行业'（当前可不使用）")
    ap.add_argument("--col-listdate", default="", help="可选：上市日期列列名，如 '上市日期'")

    ap.add_argument("--yaml", default="", help="可选：scoring.yaml 路径，用于读取 l2 别名")
    ap.add_argument("--out", default="stock_industry_base.csv", help="输出 CSV 文件名")
    ap.add_argument("--skip-bj", type=int, default=1, help="是否跳过北交所（默认 1=跳过）")

    args = ap.parse_args()

    # 处理 sheet 参数（既支持名字，也支持 "0" 这样的字符串索引）
    sheet_arg = args.sheet
    if isinstance(sheet_arg, str) and sheet_arg.isdigit():
        sheet_to_use = int(sheet_arg)
    else:
        sheet_to_use = sheet_arg

    # 读取 Excel
    df = pd.read_excel(args.tdx_xlsx, sheet_name=sheet_to_use, dtype=str)
    cols = [str(c) for c in df.columns]
    # 检查必要列
    for col in [args.col_code, args.col_name, args.col_l1l2]:
        if col not in df.columns:
            raise ValueError(f"找不到列：{col}，当前列有：{cols}")

    # 构造 ts_code
    df["ts_code"] = df[args.col_code].map(make_ts_code_from_tdx)
    df["name"] = df[args.col_name].astype(str).str.strip()

    # 拆分一二级行业
    l1_l2 = df[args.col_l1l2].apply(split_l1_l2).apply(pd.Series)
    l1_l2.columns = ["tdx_l1", "tdx_l2"]
    df["tdx_l1"] = l1_l2["tdx_l1"]
    df["tdx_l2"] = l1_l2["tdx_l2"]

    # 如果提供了单独的二级行业列（比如“细分行业”），目前可以选择不用；
    # 若以后你想用细分行业覆盖拆出来的 tdx_l2，把下面几行注释解除即可。
    if args.col_l2 and args.col_l2 in df.columns:
        # 这里只提示一下，目前不覆盖 tdx_l2
        print(f"[提示] 已检测到单独二级行业列 '{args.col_l2}'，当前版本未用其覆盖 tdx_l2。")
        # 如需覆盖，可改为：
        # df["tdx_l2"] = df[args.col_l2].fillna(df["tdx_l2"])

    # 上市日期处理：统一 YYYYMMDD
    if args.col_listdate and args.col_listdate in df.columns:
        ser_ld = df[args.col_listdate]
        df["list_date"] = ser_ld.map(normalize_listdate)
    else:
        df["list_date"] = pd.NA

    # 读取 L2 别名映射（可选）
    alias_map: Dict[str, str] = {}
    if args.yaml:
        alias_map = load_l2_alias_map(args.yaml)

    def map_anchor(l2: Any) -> str | None:
        if pd.isna(l2):
            return None
        k = str(l2).strip()
        if not k:
            return None
        return alias_map.get(k, k)

    df["anchor_industry"] = df["tdx_l2"].map(map_anchor)

    # 跳过北交所
    n_before = len(df)
    if args.skip_bj:
        df = df[~df["ts_code"].str.endswith(".BJ")]
        n_after = len(df)
        print(f"[过滤] 跳过北交所：{n_before - n_after} 条（剩余 {n_after} 条）")
    else:
        print(f"[过滤] 未跳过北交所，标的总数：{n_before} 条")

    # 选取并去重
    df_out = df[["ts_code", "name", "tdx_l1", "tdx_l2", "anchor_industry", "list_date"]].copy()
    df_out = df_out.dropna(subset=["ts_code"])
    df_out = df_out.drop_duplicates("ts_code").sort_values("ts_code").reset_index(drop=True)

    df_out.to_csv(args.out, index=False, encoding="utf-8-sig")

    print("\n✅ 生成完成：", args.out)
    print(f"总数：{len(df_out)}  | 含二级行业：{df_out['tdx_l2'].notna().sum()}  | 含上市日：{df_out['list_date'].notna().sum()}")
    print("列：", ", ".join(df_out.columns))


if __name__ == "__main__":
    main()
