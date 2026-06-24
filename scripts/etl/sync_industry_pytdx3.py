# -*- coding: utf-8 -*-
"""
sync_industry_pytdx3.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
通过 pytdx3 从通达信行情服务器获取 A 股列表 + 行业分类，
生成/更新 stock_industry_base.csv。

功能：
1. 连接通达信行情服务器，获取沪深 A 股完整列表
2. 下载 tdxhy.cfg 行业配置，解析每只股票的行业代码
3. 解析行业代码为一/二/三级行业名称：
   - 使用本地 incon.dat 文件的 #TDXRSHY 段（X前缀体系，与通达信终端一致）
     X10=一级行业, X1001=二级行业, X100101=细分行业
   - 若无 incon.dat，所有行业名称为空字符串（需先上传 incon.dat）
4. 可选加载 scoring.yaml 的 L2 别名映射
5. 增量合并已有 list_date 等字段
6. 输出格式与现有 stock_industry_base.csv 完全兼容

用法：
  python sync_industry_pytdx3.py --incon /path/to/incon.dat
  python sync_industry_pytdx3.py --incon data/incon.dat --out /tmp/test.csv
  python sync_industry_pytdx3.py --yaml config/scoring.yaml --skip-bj 1
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

import pandas as pd

# ── pytdx3 导入 ─────────────────────────────────────────────
# 优先从已安装的 pytdx3 导入；如未安装则尝试从同级 pytdx3 目录导入
try:
    from pytdx3.hq import TdxHq_API
    from pytdx3.params import TDXParams
except ImportError:
    # Fallback: 从同级目录或上级目录查找 pytdx3
    _candidates = [
        Path(__file__).resolve().parent.parent / "pytdx3",
        Path(__file__).resolve().parent / "pytdx3",
    ]
    for _p in _candidates:
        if (_p / "pytdx3" / "hq.py").exists():
            sys.path.insert(0, str(_p))
            break
    from pytdx3.hq import TdxHq_API
    from pytdx3.params import TDXParams

try:
    import yaml  # type: ignore
except ModuleNotFoundError:
    yaml = None


# ── #TDXRSHY 自定义解析器 ────────────────────────────────────
# pytdx3 只内置了 #TDXNHY（T前缀）解析，#TDXRSHY（X前缀）需手动实现。
# incon.dat 中 #TDXRSHY 段格式与 #TDXNHY 完全一致，区别是前缀为 X：
#   X10       = 一级行业（X + 2位数字）
#   X1001     = 二级行业（X + 4位数字）
#   X100101   = 细分行业（X + 6位数字）

def parse_incon_tdxrshy(data: bytes):
    """
    从 incon.dat 二进制数据中解析 #TDXRSHY 段的通达信荣誉行业分类（X前缀体系）。

    :param data: incon.dat 的完整文件内容 (bytes)
    :return: (l1_dict, l2_dict, l3_dict) 三个字典分别为一/二/三级行业映射
             如解析失败返回 (None, None, None)
    """
    try:
        text = data.decode('gbk', errors='ignore')
    except Exception:
        return None, None, None

    lines = text.replace('\r\n', '\n').split('\n')

    # 定位 #TDXRSHY 段
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == '#TDXRSHY':
            start_idx = i + 1
        elif start_idx is not None and stripped.startswith('######'):
            end_idx = i
            break

    if start_idx is None:
        return None, None, None
    if end_idx is None:
        end_idx = len(lines)

    l1, l2, l3 = {}, {}, {}

    for i in range(start_idx, end_idx):
        line = lines[i].strip()
        if not line or not line.startswith('X'):
            continue
        parts = line.split('|', 1)
        if len(parts) != 2:
            continue

        code = parts[0].strip()
        name = parts[1].strip()

        # X + 2位 = 一级, X + 4位 = 二级, X + 6位 = 三级
        code_digits = code[1:]  # 去掉 X
        if len(code_digits) == 2:
            l1[code] = name
        elif len(code_digits) == 4:
            l2[code] = name
        elif len(code_digits) == 6:
            l3[code] = name

    if not l1:
        return None, None, None

    return l1, l2, l3


def get_rshy_l1_name(tdx_code: str, mapping: dict) -> str:
    """从 X 前缀行业代码提取一级名称（取前3位 = X + 2位）"""
    if not tdx_code or len(tdx_code) < 3:
        return ""
    return mapping.get(tdx_code[:3], "")


def get_rshy_l2_name(tdx_code: str, mapping: dict) -> str:
    """从 X 前缀行业代码提取二级名称（取前5位 = X + 4位）"""
    if not tdx_code or len(tdx_code) < 5:
        return ""
    return mapping.get(tdx_code[:5], "")


def get_rshy_l3_name(tdx_code: str, mapping: dict) -> str:
    """从 X 前缀行业代码提取三级名称（取前7位 = X + 6位）"""
    if not tdx_code or len(tdx_code) < 7:
        return ""
    return mapping.get(tdx_code[:7], "")


# ── 通达信行情服务器列表 ────────────────────────────────────
TDX_SERVERS = [
    ("119.147.212.81", 7709),
    ("110.41.2.72", 7709),
    ("218.75.126.9", 7709),
    ("115.238.56.198", 7709),
    ("124.160.88.183", 7709),
    ("60.12.136.250", 7709),
    ("218.108.98.244", 7709),
    ("101.227.73.20", 7709),
    ("14.215.128.18", 7709),
]


# ── 工具函数 ─────────────────────────────────────────────────

def code_to_ts_code(code: str, market: int) -> str:
    """通达信 code + market → ts_code (000001 + 0 → 000001.SZ)"""
    code = str(code).strip().zfill(6)
    if market == 1:
        return f"{code}.SH"
    if code.startswith(("43", "83", "87", "88", "89", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def is_a_share(code: str, market: int) -> bool:
    """判断是否为 A 股（排除基金、债券、权证等）"""
    if market == 0:  # 深圳
        return code.startswith(("00", "30"))
    else:  # 上海
        return code.startswith(("60", "68"))


def load_incon_dat(incon_path: str) -> tuple:
    """
    从本地 incon.dat 文件解析行业映射（仅支持 #TDXRSHY X前缀体系）。
    返回: (l1_dict, l2_dict, l3_dict, prefix)
      prefix: 'X' 表示解析成功, None 表示解析失败
    """
    p = Path(incon_path)
    if not p.exists():
        print(f"[WARN] incon.dat 不存在: {incon_path}")
        print("[WARN] ⚠ 请先上传 incon.dat，否则行业名称将全部为空")
        return None, None, None, None

    data = p.read_bytes()
    l1, l2, l3 = parse_incon_tdxrshy(data)
    if l1:
        print(f"[INFO] 已加载 incon.dat #TDXRSHY: L1={len(l1)}  L2={len(l2)}  L3={len(l3)}")
        return l1, l2, l3, 'X'

    print(f"[WARN] incon.dat 未找到 #TDXRSHY 段，行业名称将全部为空")
    return None, None, None, None


def load_l2_alias_map(yaml_path: str) -> Dict[str, str]:
    """从 scoring.yaml 中读取 L2 别名映射"""
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
        return {}

    alias_map: Dict[str, str] = {}
    for key in ("l2_aliases", "l2_alias_map"):
        section = data.get(key)
        if isinstance(section, dict):
            for k, v in section.items():
                alias_map[str(k).strip()] = str(v).strip()

    if alias_map:
        print(f"[YAML] 解析到别名映射：{len(alias_map)} 条")
    return alias_map


def merge_existing_data(df_new: pd.DataFrame, existing_path: str) -> pd.DataFrame:
    """增量合并：保留已有的 list_date 等字段"""
    p = Path(existing_path)
    if not p.exists():
        return df_new

    try:
        df_old = pd.read_csv(p, dtype=str)
    except Exception:
        return df_new

    # 仅从旧数据保留 list_date
    if "list_date" in df_old.columns and "ts_code" in df_old.columns:
        old_dates = df_old.set_index("ts_code")["list_date"].to_dict()
        df_new["list_date"] = df_new.apply(
            lambda row: old_dates.get(row["ts_code"], row.get("list_date", "")),
            axis=1,
        )
    return df_new


# ── 主函数 ───────────────────────────────────────────────────

def sync_industry(
    incon_path: Optional[str] = None,
    yaml_path: Optional[str] = None,
    output_path: str = "stock_industry_base.csv",
    skip_bj: bool = True,
) -> dict:
    """
    从通达信行情服务器同步行业分类数据。
    
    返回: {"status": "success/error", "message": str, "industries": int, "stocks": int}
    """
    api = TdxHq_API()

    # 尝试连接服务器
    connected = False
    for ip, port in TDX_SERVERS:
        try:
            if api.connect(ip, port):
                print(f"[CONN] 已连接: {ip}:{port}")
                connected = True
                break
        except Exception as e:
            print(f"[CONN] {ip}:{port} 连接失败: {e}")
    
    if not connected:
        return {"status": "error", "message": "无法连接任何通达信服务器"}

    try:
        # ── 1. 获取全部股票列表 ──
        print("[STEP 1] 获取股票列表...")
        all_stocks = []
        for market in [TDXParams.MARKET_SZ, TDXParams.MARKET_SH]:
            count = api.get_security_count(market)
            mkt_name = "SZ" if market == 0 else "SH"
            print(f"  {mkt_name}: {count} 只")
            for start in range(0, count, 1000):
                stocks = api.get_security_list(market, start)
                if stocks:
                    for s in stocks:
                        s["market"] = market
                    all_stocks.extend(stocks)

        # 筛选 A 股
        df_stocks = pd.DataFrame(all_stocks)
        df_a = df_stocks[
            df_stocks.apply(lambda r: is_a_share(r["code"], r["market"]), axis=1)
        ].copy()
        print(f"  A 股筛选后: {len(df_a)} 只")

        # ── 2. 下载行业分类配置 (tdxhy.cfg) ──
        print("[STEP 2] 下载行业分类配置 tdxhy.cfg...")
        content = api.get_report_file_by_size("tdxhy.cfg")
        text = content.decode("gbk", errors="ignore")
        lines = text.strip().split("\r\n")

        industry_records = []
        for line in lines:
            parts = line.split("|")
            if len(parts) >= 6:
                industry_records.append({
                    "market": int(parts[0]),
                    "code": parts[1],
                    # tdxhy.cfg 格式: market|code|T前缀|...|...|X前缀(RSHY)
                    # parts[2]=T前缀(如T1001)  parts[5]=X前缀(如X500102)
                    # 使用 parts[5] 匹配 incon.dat #TDXRSHY 体系
                    "tdx_code": parts[5] if parts[5] else "",
                })
        df_industry = pd.DataFrame(industry_records)
        print(f"  行业配置: {len(df_industry)} 条记录")

        # ── 3. 合并股票列表与行业分类 ──
        df_result = df_a.merge(df_industry, on=["market", "code"], how="left")

        # ── 4. 解析行业名称 ──
        print("[STEP 3] 解析行业名称...")
        l1_map, l2_map, l3_map = None, None, None

        # 从本地 incon.dat 加载（仅支持 X 前缀 RSHY 体系）
        if incon_path:
            l1_map, l2_map, l3_map, _ = load_incon_dat(incon_path)

        # 无 incon.dat 时中止同步：不写出空行业数据，避免覆盖已有映射
        if l1_map is None:
            msg = "未找到 incon.dat 或解析失败，行业同步已中止。请在页面上传 incon.dat 后重新同步。"
            print(f"  ⚠ {msg}")
            return {"status": "error", "message": msg}

        resolve_l1 = lambda x: get_rshy_l1_name(x, l1_map)
        resolve_l2 = lambda x: get_rshy_l2_name(x, l2_map)
        resolve_l3 = lambda x: get_rshy_l3_name(x, l3_map)

        df_result["industry_l1"] = df_result["tdx_code"].apply(
            lambda x: resolve_l1(x) if pd.notna(x) else ""
        )
        df_result["industry_l2"] = df_result["tdx_code"].apply(
            lambda x: resolve_l2(x) if pd.notna(x) else ""
        )
        df_result["industry_l3"] = df_result["tdx_code"].apply(
            lambda x: resolve_l3(x) if pd.notna(x) else ""
        )

        # ── 5. 构建输出 DataFrame ──
        df_result["ts_code"] = df_result.apply(
            lambda r: code_to_ts_code(r["code"], r["market"]), axis=1
        )

        # 行业映射：incon.dat #TDXRSHY X前缀体系
        #   X10       = 一级行业（大类）
        #   X1001     = 二级行业（中类）
        #   X100101   = 细分行业
        df_result["tdx_l1"] = df_result["industry_l1"]   # 一级（大类）
        df_result["tdx_l2"] = df_result["industry_l2"]   # 二级（中类）
        df_result["tdx_l3"] = df_result["industry_l3"]   # 细分

        # 跳过北交所
        if skip_bj:
            n_before = len(df_result)
            df_result = df_result[~df_result["ts_code"].str.endswith(".BJ")]
            print(f"[FILTER] 跳过北交所: {n_before - len(df_result)} 条")

        # L2 别名映射 → anchor_industry
        alias_map: Dict[str, str] = {}
        if yaml_path:
            alias_map = load_l2_alias_map(yaml_path)

        def map_anchor(l2: Any) -> Optional[str]:
            if pd.isna(l2) or not str(l2).strip():
                return None
            k = str(l2).strip()
            return alias_map.get(k, k)

        # anchor_industry 仍从 tdx_l2 (中类) 映射，经 alias_map 标准化
        df_result["anchor_industry"] = df_result["tdx_l2"].map(map_anchor)
        df_result["list_date"] = pd.NA

        # 选取输出列（新增 tdx_l3 细分行业列）
        df_out = df_result[
            ["ts_code", "name", "tdx_l1", "tdx_l2", "tdx_l3", "anchor_industry", "list_date"]
        ].copy()
        df_out = df_out.dropna(subset=["ts_code"])
        df_out = df_out.drop_duplicates("ts_code").sort_values("ts_code").reset_index(drop=True)

        # 增量合并已有数据（保留 list_date）
        df_out = merge_existing_data(df_out, output_path)

        # 写入 CSV
        df_out.to_csv(output_path, index=False, encoding="utf-8-sig")

        # 统计
        n_stocks = len(df_out)
        n_l1 = df_out["tdx_l1"].nunique()
        n_l2 = df_out["tdx_l2"].nunique()
        n_l3 = df_out["tdx_l3"].nunique()

        msg = f"同步完成: {n_stocks} 只股票, {n_l1} 个一级行业, {n_l2} 个二级行业, {n_l3} 个细分行业"
        print(f"\n✅ {msg}")
        print(f"   输出: {output_path}")

        return {
            "status": "success",
            "message": msg,
            "stocks": n_stocks,
            "industries_l1": n_l1,
            "industries_l2": n_l2,
            "industries_l3": n_l3,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}
    finally:
        api.disconnect()


# ── CLI 入口 ──

def main():
    ap = argparse.ArgumentParser(
        description="通过 pytdx3 从通达信服务器同步行业分类数据"
    )
    ap.add_argument(
        "--incon", default="",
        help="本地 incon.dat 文件路径（可选，用于更新行业名称映射）"
    )
    ap.add_argument(
        "--yaml", default="",
        help="scoring.yaml 路径，用于读取 L2 别名映射"
    )
    ap.add_argument(
        "--out", default="stock_industry_base.csv",
        help="输出 CSV 文件（默认 stock_industry_base.csv）"
    )
    ap.add_argument(
        "--skip-bj", type=int, default=1,
        help="是否跳过北交所（默认 1=跳过）"
    )
    args = ap.parse_args()
    result = sync_industry(
        incon_path=args.incon or None,
        yaml_path=args.yaml or None,
        output_path=args.out,
        skip_bj=bool(args.skip_bj),
    )
    if result["status"] != "success":
        print(f"❌ {result['message']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
