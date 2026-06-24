# -*- coding: utf-8 -*-
"""
交易流水导入 API — 按券商提供不同解析器

支持券商：
  - 民生证券：Sheet1，第2行为表头，第3行起为数据
  - 平安证券：第1行为表头，第2行起为数据
"""
import io
import logging
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.params import Form

import openpyxl

from web.backend.database import get_db
from web.backend.time_utils import sh_timestamp_str
from web.backend.upload_security import MAX_GENERIC_UPLOAD_BYTES, read_limited_upload

logger = logging.getLogger(__name__)
router = APIRouter()


# ──────────────────────────────────────────
# 通用工具
# ──────────────────────────────────────────

def _to_float(val) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def _infer_ts_code(code: str) -> str:
    """将6位代码推断为带交易所后缀的 ts_code"""
    if not code:
        return ""
    code = str(code).strip().split(".")[0]  # 去掉可能存在的后缀
    if not code.isdigit() or len(code) != 6:
        return ""
    if code.startswith(("60", "68", "11", "50",
                         "70", "71", "72", "73", "78")):  # 沪市正股/债/可转债认购
        return f"{code}.SH"
    if code.startswith(("00", "30", "12", "15",
                         "37", "08")):  # 深市正股/债/可转债认购
        return f"{code}.SZ"
    return f"{code}.SH"


def _parse_date(raw) -> str:
    """解析日期为 YYYY-MM-DD 格式。
    兼容三种来源：
      1. openpyxl 读出的 datetime/date 对象
      2. YYYYMMDD 纯数字字符串（如 "20240115"）
      3. YYYY-MM-DD / YYYY/MM/DD 带分隔符字符串
    """
    from datetime import date as date_type
    if isinstance(raw, datetime):
        return raw.strftime("%Y-%m-%d")
    if isinstance(raw, date_type):
        return raw.strftime("%Y-%m-%d")
    s = str(raw).strip().replace("-", "").replace("/", "")
    return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")


# ──────────────────────────────────────────
# 可转债认购辅助函数
# ──────────────────────────────────────────

def _is_bond_subscription_name(name: str) -> bool:
    """判断证券名称是否为可转债认购期（发债/配债，尚未上市的认购代码）"""
    return "发债" in name or "配债" in name


def _bond_name_root(name: str) -> str:
    """提取债券名称根部，用于认购代码（发债/配债）和正式代码（转债/转N）双向匹配。

    支持：
      发债/配债 类：兴业发债→兴业，柳工配债→柳工，福能配债→福能
      转债 类：兴业转债→兴业，华安转债01→华安
      转N 类：柳工转2→柳工，东财转3→东财
    """
    import re
    # 发债 / 配债
    for sfx in ("发债", "配债"):
        if name.endswith(sfx):
            return name[:-len(sfx)]
    # 转债 + 可选后续数字字母（如 转债01, 转债）
    m = re.match(r'^(.+?)转债[\w]*$', name)
    if m:
        return m.group(1)
    # 转 + 纯数字（如 转2, 转3）
    m = re.match(r'^(.+?)转\d+$', name)
    if m:
        return m.group(1)
    return name


_FORMAL_BOND_PREFIXES_SH = ("110", "113")
_FORMAL_BOND_PREFIXES_SZ = ("123", "127", "128")


def _is_formal_bond_code(ts_code: str) -> bool:
    """判断是否为正式可转债代码（非认购临时代码）"""
    if not ts_code or "." not in ts_code:
        return False
    code6 = ts_code[:6]
    market = ts_code.split(".")[-1]
    if market == "SH":
        return any(code6.startswith(p) for p in _FORMAL_BOND_PREFIXES_SH)
    if market == "SZ":
        return any(code6.startswith(p) for p in _FORMAL_BOND_PREFIXES_SZ)
    return False


def _normalize_bond_subscription_codes(
    transactions: List[Dict],
    warnings_out: List[str],
    skipped_out: Optional[List[Dict]] = None,
) -> None:
    """将可转债认购记录（remarks='配债认购'）中的认购临时代码替换为正式交易代码。

    策略：
      1. 扫描 transactions 中所有正式转债代码，建立 名称根部 → 正式代码映射
      2. 扫描 remarks='配债认购' 的记录，按名称根部匹配并替换 ts_code/name
      3. 归并成功 → 买入记录挂到正式代码下
      4. 归并失败 → 从 transactions 中移除（避免幽灵持仓），记入 skipped_out 供 CSV 导出

    注意：不使用市场后缀做 key。原因：认购期临时代码（072xxx/073xxx/078xxx）
    经 _infer_ts_code 推断为 .SH，但对应正式转债可能是 .SZ（如 128xxx 前缀）。
    两者 market 不一致时，用 (root, market) 做 key 会导致匹配失败。

    Args:
        transactions:  当前解析的交易记录列表（in-place 修改）
        warnings_out:  追加人类可读 warning 字符串
        skipped_out:   若非 None，追加结构化跳过记录 dict（用于 CSV 导出）
    """
    # 建立正式转债代码映射：name_root -> (ts_code, name)
    formal_map: Dict[str, Tuple[str, str]] = {}
    for tx in transactions:
        if not _is_formal_bond_code(tx["ts_code"]):
            continue
        root = _bond_name_root(tx["name"])
        if root not in formal_map:
            formal_map[root] = (tx["ts_code"], tx["name"])

    # 替换认购记录的代码；归并失败的加入移除列表
    to_remove = []
    for tx in transactions:
        if tx["remarks"] != "配债认购":
            continue
        root = _bond_name_root(tx["name"])
        if root in formal_map:
            tx["ts_code"], tx["name"] = formal_map[root]
        else:
            to_remove.append(tx)
            warnings_out.append(
                f"⚠️ 可转债认购记录已跳过（无法归并）：{tx['name']}（{tx['ts_code']}）"
                f" 认购日期 {tx.get('date','')}，数量 {tx.get('amount','')}，"
                f"请在正式转债代码下手动补录买入记录。"
            )
            if skipped_out is not None:
                # 构建完整导出行：先放解析字段，再展开原始行（_raw），最后加说明
                entry: Dict[str, Any] = {
                    "日期":     tx.get("date", ""),
                    "认购代码": tx.get("ts_code", ""),
                    "认购名称": tx.get("name", ""),
                    "数量":     tx.get("amount", ""),
                    "认购金额": tx.get("total_amt", ""),
                    "业务标志": tx.get("_biz_type", ""),
                }
                # 展开原始行（华林 CSV parser 存的完整行）
                raw = tx.get("_raw", {})
                for k, v in (raw.items() if isinstance(raw, dict) else []):
                    col = str(k).strip()
                    if col and col not in entry:  # 不覆盖已有字段
                        entry[col] = v
                entry["说明"] = "无法归并到正式转债代码，请手动在正式代码下补录买入"
                skipped_out.append(entry)


    # 移除无法归并的认购记录，避免创建临时代码幽灵持仓
    for tx in to_remove:
        transactions.remove(tx)

    # ── 步骤 5：移除归并后的重复"到账"记录 ──────────────────────────────────
    # 可转债认购链路：
    #   认购缴款(BUY) → [已归并] → 配售股份/股份上市/配股入账(RIGHTS/BUY) ← 重复！
    # 这些"到账"记录在不同券商 remarks 里叫不同名字，但有共同特征：
    #   - 业务类型属于"通知/到账"，不代表实际资金流动的买入
    #   - 出现在同一正式转债代码下，且该代码已经有归并过来的 BUY
    # 判断依据：remarks 是否是已知的"到账通知"业务标志
    _ARRIVAL_REMARKS = {
        "配股入账", "配股入帐",   # 华林/民生 配售股份到账
        "股份上市",               # 平安 转债上市到账
        "新股入账", "新股入帐",   # 部分券商新债上市入账
        "国债新股上市和配股到账", # 民生 可转债上市
        "配售股份",               # 到账通知（无资金流动）
    }
    # 收集已成功归并的正式代码（归并后 remarks 仍为 "配债认购"）
    merged_formal_codes = {
        tx["ts_code"]
        for tx in transactions
        if tx["remarks"] == "配债认购" and _is_formal_bond_code(tx["ts_code"])
    }
    if merged_formal_codes:
        arrival_to_remove = [
            tx for tx in transactions
            if tx.get("ts_code") in merged_formal_codes
            and tx.get("trans_type") in ("RIGHTS", "BUY")
            and tx["remarks"] in _ARRIVAL_REMARKS
        ]
        for tx in arrival_to_remove:
            transactions.remove(tx)
            warnings_out.append(
                f"ℹ️ 已移除可转债重复到账记录：{tx['name']}（{tx['ts_code']}）"
                f" {tx.get('date','')} {tx.get('remarks','')} qty={tx.get('amount','')}"
            )




# ──────────────────────────────────────────
# 民生证券解析器
# ──────────────────────────────────────────

# (系统交易类型, 是否为特殊来源需在remarks标注)
MINSHENG_TYPE_MAP = {
    # 买入
    "证券买入":         ("BUY",      False),
    "新股入帐":         ("BUY",      True),
    "新股入账":         ("BUY",      True),
    "中签股份下账":     ("BUY",      True),
    "市值申购中签":     ("BUY",      True),
    "申购中签":         ("BUY",      True),
    "权证上账":         ("BUY",      True),
    "转托管入":         ("XFER_IN",  True),
    "指定入帐":         ("XFER_IN",  True),   # 从其他账户转入持仓，同转托管入
    # 配股 → RIGHTS（与持仓分析配股操作一致）
    "配股入帐":         ("RIGHTS",   True),
    "配股入账":         ("RIGHTS",   True),
    "配股缴款":         ("RIGHTS",   True),
    "配售股份":         ("RIGHTS",   True),
    "配售缴款":         ("RIGHTS",   True),
    # 卖出
    "证券卖出":         ("SELL",     False),
    "托管转出":         ("SELL",     True),
    "股份转出":         ("MERGE_OUT", True),   # 吸收合并/摘牌转出（无现金）
    # 分红（现金）
    "红利入账":         ("DIVIDEND",     False),
    "股息入账":         ("DIVIDEND",     False),
    "债券兑息":         ("DIVIDEND",     False),
    "债券兑息兑付扣税": ("DIVIDEND",     False),
    # 送股（股票股利）→ 增加股数，不消耗现金，等同拆股
    "红股入账":         ("SPLIT",        True),
    # 股息税补扣（记录在对应股票名下）
    "股息红利差异扣税": ("DIVIDEND_TAX", False),
    # 银证转账
    "银行转证券":       ("DEPOSIT",  False),
    "银行转取":         ("WITHDRAW", False),  # 资金从账户转出
    "证券转银行":       ("WITHDRAW", False),
    "OTC资金划出":      ("WITHDRAW", False),
    # 利息 → 资金流入
    "利息归本":         ("DEPOSIT",  False),
}

MINSHENG_IGNORE = {
    "指定交易", "证券清理", "新股申购", "新股申购确认缴款",
    "申购还款", "申购配号", "市值申购中签扣款", "市值申购中签扣款回冲",
    "配股权证", "交收资金冻结", "交收资金冻结取消",
    "转账冻结取消", "转账取预冻结",
    "融券回购", "融券购回", "质押回购拆出", "拆出质押购回",
}


def parse_minsheng(content: bytes) -> Tuple[List[Dict], List[Dict], List[str], List[Dict]]:
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if len(rows) < 3:
        raise ValueError("文件行数不足，请确认是民生证券流水文件")

    header = rows[1]
    expected = ["资金账号", "客户名称", "股东账号", "日期", "业务摘要"]
    if not all(str(h or "").strip().startswith(e) for h, e in zip(header, expected)):
        raise ValueError(f"表头不匹配，当前表头: {header[:5]}")

    transactions, capital_flows, warnings, skipped = [], [], [], []

    for row_idx, row in enumerate(rows[2:], start=3):
        if not row or not row[3]:
            continue
        try:
            date_str = _parse_date(row[3])
        except Exception:
            warnings.append(f"第{row_idx}行日期格式异常: {row[3]}")
            continue

        biz_type = str(row[4] or "").strip()
        if biz_type in MINSHENG_IGNORE:
            continue
        if biz_type not in MINSHENG_TYPE_MAP:
            warnings.append(f"第{row_idx}行未知业务类型: [{biz_type}]，已跳过")
            continue

        sys_type, is_special = MINSHENG_TYPE_MAP[biz_type]
        ts_code = _infer_ts_code(str(row[6] or ""))
        name = str(row[7] or "").strip()
        quantity  = _to_float(row[8])
        price     = _to_float(row[9])
        total_amt = _to_float(row[10])
        fund_chg  = _to_float(row[11])
        if sys_type == "SELL" and fund_chg == 0 and abs(total_amt) > 0:
            sys_type = "MERGE_OUT"  # 资金发生金额=0：股权置换转出，非真实卖出
        commission= _to_float(row[14])
        stamp_tax = _to_float(row[15])
        xfer_fee  = _to_float(row[16])
        other_fee = _to_float(row[17]) if len(row) > 17 else 0.0
        fee = commission + xfer_fee + other_fee
        remarks = biz_type if is_special else None

        if sys_type in ("BUY", "SELL", "RIGHTS", "SPLIT", "MERGE_OUT"):
            if not ts_code or quantity == 0:
                continue
            # 可转债认购归并：任何业务下只要名称含"发债"/"配债"，统一走归并逻辑
            if _is_bond_subscription_name(name):
                if sys_type == "RIGHTS":
                    if fund_chg < 0:
                        calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
                        transactions.append({
                            "ts_code": ts_code, "name": name,
                            "trans_type": "BUY",
                            "price": calc_price,
                            "amount": int(abs(quantity)),
                            "total_amt": abs(total_amt),
                            "fee": fee, "stamp_tax": stamp_tax,
                            "date": date_str, "remarks": "配债认购",
                            "_biz_type": biz_type,
                        })
                else:  # BUY 分支（如 配股缴款 被映射为 BUY 特殊）
                    calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
                    transactions.append({
                        "ts_code": ts_code, "name": name,
                        "trans_type": "BUY",
                        "price": calc_price,
                        "amount": int(abs(quantity)),
                        "total_amt": abs(total_amt),
                        "fee": fee, "stamp_tax": stamp_tax,
                        "date": date_str, "remarks": "配债认购",
                        "_biz_type": biz_type,
                    })
                continue
            calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": sys_type,
                "price": calc_price,
                "amount": int(abs(quantity)),
                "total_amt": abs(total_amt),
                "fee": fee, "stamp_tax": stamp_tax,
                "date": date_str, "remarks": remarks,
            })

        elif sys_type == "XFER_IN":
            # 转托管入：股票从其他券商迁入。
            # 从单券商独立视角：记录 BUY（成本）+ DEPOSIT（资本投入）
            if not ts_code or quantity == 0:
                continue
            calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": "BUY",
                "price": calc_price,
                "amount": int(abs(quantity)),
                "total_amt": abs(total_amt),
                "fee": fee, "stamp_tax": stamp_tax,
                "date": date_str, "remarks": biz_type,
            })
            deposit_amt = abs(total_amt) if abs(total_amt) > 0 else abs(fund_chg)
            if deposit_amt > 0:
                capital_flows.append({
                    "flow_type": "DEPOSIT", "amount": deposit_amt,
                    "date": date_str, "remarks": f"转托管入({name})",
                })

        elif sys_type == "DIVIDEND":
            if not ts_code:
                continue
            div_amount = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": "DIVIDEND",
                "price": price, "amount": int(abs(quantity)),
                "total_amt": div_amount,
                "fee": 0.0, "stamp_tax": 0.0,
                "date": date_str, "remarks": biz_type,
            })

        elif sys_type == "DIVIDEND_TAX":
            if not ts_code:
                continue
            tax_amount = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
            if tax_amount == 0:
                continue
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": "DIVIDEND_TAX",
                "price": 0.0, "amount": 0,
                "total_amt": tax_amount,
                "fee": 0.0, "stamp_tax": 0.0,
                "date": date_str, "remarks": biz_type,
            })

        elif sys_type in ("DEPOSIT", "WITHDRAW"):
            amt = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
            if amt == 0:
                continue
            capital_flows.append({
                "flow_type": sys_type, "amount": amt,
                "date": date_str, "remarks": biz_type,
            })

    _normalize_bond_subscription_codes(transactions, warnings, skipped)
    return transactions, capital_flows, warnings, skipped


# ──────────────────────────────────────────
# 民生证券 CSV 解析器
# ──────────────────────────────────────────
# CSV 格式与 Excel 格式相比：
#   - 无"客户名称"列，列数少1
#   - 首行即表头，第2行起为数据
#   - 用 DictReader 按列名访问，更稳定
#
# 列名（16列）：
#   资金账号, 股东账号, 日期, 业务摘要, 币种,
#   证券代码, 证券名称, 成交数量, 成交价格, 成交金额,
#   资金发生金额, 交易后账户\n证券余额, 佣金, 印花税, 过户费, 其他税费

def parse_minsheng_csv(content: bytes) -> Tuple[List[Dict], List[Dict], List[str], List[Dict]]:
    """解析民生证券 CSV 格式流水（utf-8-sig 编码，首行表头）"""
    import csv as csv_module

    for encoding in ("utf-8-sig", "gbk", "gb2312"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("无法识别文件编码，请确认是民生证券导出的 CSV 文件")

    reader = csv_module.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV 文件为空或无表头")

    # 归一化列名（去掉换行符/空格）
    def _col(row: dict, name: str) -> str:
        for k, v in row.items():
            if k and k.strip().replace("\n", "").replace("\r", "") == name:
                return str(v or "").strip()
        return ""

    transactions, capital_flows, warnings, skipped = [], [], [], []

    for row_idx, row in enumerate(reader, start=2):
        date_raw = _col(row, "日期")
        if not date_raw:
            continue
        try:
            date_str = _parse_date(date_raw)
        except Exception:
            warnings.append(f"第{row_idx}行日期格式异常: {date_raw}")
            continue

        biz_type = _col(row, "业务摘要")
        if biz_type in MINSHENG_IGNORE:
            continue
        if biz_type not in MINSHENG_TYPE_MAP:
            warnings.append(f"第{row_idx}行未知业务类型: [{biz_type}]，已跳过")
            continue

        sys_type, is_special = MINSHENG_TYPE_MAP[biz_type]
        ts_code   = _infer_ts_code(_col(row, "证券代码"))
        name      = _col(row, "证券名称")
        quantity  = _to_float(_col(row, "成交数量"))
        price     = _to_float(_col(row, "成交价格"))
        total_amt = _to_float(_col(row, "成交金额"))
        fund_chg  = _to_float(_col(row, "资金发生金额"))
        if sys_type == "SELL" and fund_chg == 0 and abs(total_amt) > 0:
            sys_type = "MERGE_OUT"  # 资金发生金额=0：股权置换转出，非真实卖出
        commission= _to_float(_col(row, "佣金"))
        stamp_tax = _to_float(_col(row, "印花税"))
        xfer_fee  = _to_float(_col(row, "过户费"))
        other_fee = _to_float(_col(row, "其他税费"))
        fee       = commission + xfer_fee + other_fee
        remarks   = biz_type if is_special else None

        if sys_type in ("BUY", "SELL", "RIGHTS", "SPLIT", "MERGE_OUT"):
            if not ts_code or quantity == 0:
                continue
            if _is_bond_subscription_name(name):
                if sys_type == "RIGHTS":
                    if fund_chg < 0:
                        calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
                        transactions.append({
                            "ts_code": ts_code, "name": name,
                            "trans_type": "BUY",
                            "price": calc_price,
                            "amount": int(abs(quantity)),
                            "total_amt": abs(total_amt),
                            "fee": fee, "stamp_tax": stamp_tax,
                            "date": date_str, "remarks": "配债认购",
                            "_biz_type": biz_type,
                        })
                else:
                    calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
                    transactions.append({
                        "ts_code": ts_code, "name": name,
                        "trans_type": "BUY",
                        "price": calc_price,
                        "amount": int(abs(quantity)),
                        "total_amt": abs(total_amt),
                        "fee": fee, "stamp_tax": stamp_tax,
                        "date": date_str, "remarks": "配债认购",
                        "_biz_type": biz_type,
                    })
                continue
            calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": sys_type,
                "price": calc_price,
                "amount": int(abs(quantity)),
                "total_amt": abs(total_amt),
                "fee": fee, "stamp_tax": stamp_tax,
                "date": date_str, "remarks": remarks,
            })

        elif sys_type == "XFER_IN":
            # 转托管入：股票从其他券商迁入。
            # 从单券商独立视角：记录 BUY（成本）+ DEPOSIT（资本投入）
            if not ts_code or quantity == 0:
                continue
            calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": "BUY",
                "price": calc_price,
                "amount": int(abs(quantity)),
                "total_amt": abs(total_amt),
                "fee": fee, "stamp_tax": stamp_tax,
                "date": date_str, "remarks": biz_type,
            })
            deposit_amt = abs(total_amt) if abs(total_amt) > 0 else abs(fund_chg)
            if deposit_amt > 0:
                capital_flows.append({
                    "flow_type": "DEPOSIT", "amount": deposit_amt,
                    "date": date_str, "remarks": f"转托管入({name})",
                })

        elif sys_type == "DIVIDEND":
            if not ts_code:
                continue
            div_amount = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": "DIVIDEND",
                "price": price, "amount": int(abs(quantity)),
                "total_amt": div_amount,
                "fee": 0.0, "stamp_tax": 0.0,
                "date": date_str, "remarks": biz_type,
            })

        elif sys_type == "DIVIDEND_TAX":
            if not ts_code:
                continue
            tax_amount = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
            if tax_amount == 0:
                continue
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": "DIVIDEND_TAX",
                "price": 0.0, "amount": 0,
                "total_amt": tax_amount,
                "fee": 0.0, "stamp_tax": 0.0,
                "date": date_str, "remarks": biz_type,
            })

        elif sys_type in ("DEPOSIT", "WITHDRAW"):
            amt = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
            if amt == 0:
                continue
            capital_flows.append({
                "flow_type": sys_type, "amount": amt,
                "date": date_str, "remarks": biz_type,
            })

    _normalize_bond_subscription_codes(transactions, warnings, skipped)
    return transactions, capital_flows, warnings, skipped


# ──────────────────────────────────────────
# 平安证券解析器
# ──────────────────────────────────────────

PINGAN_TYPE_MAP = {
    # 买入
    "买入":                       ("BUY",      False),
    "证券买入清算":               ("BUY",      False),
    # 新股/新债（按买入记录）
    "新股上市":                   ("BUY",      True),
    "新股登记":                   ("BUY",      True),
    "国债新股上市和配股到账":     ("BUY",      True),
    "股份上市":                   ("BUY",      True),
    "股份登记（权益、新股）":     ("BUY",      True),
    "新债上市":                   ("BUY",      True),
    "普通转托管入":               ("XFER_IN",  True),
    "转托管转入":                 ("XFER_IN",  True),
    # 配股 → RIGHTS
    "配股缴款":                   ("RIGHTS",   True),
    "配股（债）到帐":             ("RIGHTS",   True),
    # 卖出
    "卖出":                       ("SELL",     False),
    "证券卖出清算":               ("SELL",     False),
    "普通转托管出":               ("SELL",     True),
    "清算过户转出":               ("SELL",     True),
    # 分红（现金）
    "红利发放":                   ("DIVIDEND",     False),
    "股息派发":                   ("DIVIDEND",     False),
    "债券兑息":                   ("DIVIDEND",     False),
    # 送股（股票股利）→ 增加股数，不消耗现金
    "红股派发":                   ("SPLIT",        True),
    "红股派息":                   ("SPLIT",        True),
    # 股息税补扣（记录在对应股票名下）
    "上海市场股息红利个人所得税扣款": ("DIVIDEND_TAX", False),
    "深圳市场股息红利个人所得税扣款": ("DIVIDEND_TAX", False),
    "红利税差异化扣除":           ("DIVIDEND_TAX", False),
    # 银证转账
    "银证转入":                   ("DEPOSIT",  False),
    "银证转出":                   ("WITHDRAW", False),
    # 吸收合并/退市（股票置换为另一只，无现金）
    "摘牌/注销/退出结算系统":         ("MERGE_OUT", True),
    # 利息 → 资金流入
    "利息归本":                   ("DEPOSIT",  False),
}

PINGAN_IGNORE = {
    "其它上账",               # 新股入账重复通知，跳过避免重复记录
    "批量客户利息归本",
    "回购融券清算", "融券购回清算",
    "债券质押式融券回购", "债券质押式融券购回",
    "LOF申购", "基金申购", "基金赎回",
    "市值认购资金扣收",
    "退出结算系统",
    "现金产品快速取现客户方",
    "配股（债）权证到帐",  # 配股权证，非实际配股
}


def parse_pingan(content: bytes) -> Tuple[List[Dict], List[Dict], List[str], List[Dict]]:
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if len(rows) < 2:
        raise ValueError("文件行数不足，请确认是平安证券流水文件")

    # 第1行表头校验
    header = rows[0]
    expected_cols = ["记账日期", "业务摘要", "资产账户"]
    if not all(str(h or "").strip().startswith(e) for h, e in zip(header, expected_cols)):
        raise ValueError(f"表头不匹配，当前表头: {header[:3]}")

    # 列索引：记账日期,业务摘要,资产账户,股东帐号,证券代码,证券名称,
    #         成交股数,成交价格,成交金额,利息金额,发生金额,资金余额,手续费,印花税,过户费等
    COL_DATE, COL_TYPE, COL_CODE, COL_NAME = 0, 1, 4, 5
    COL_QTY, COL_PRICE, COL_AMT, COL_FUND = 6, 7, 8, 10
    COL_FEE, COL_STAMP, COL_XFER = 12, 13, 14

    transactions, capital_flows, warnings, skipped = [], [], [], []

    for row_idx, row in enumerate(rows[1:], start=2):
        if not row or not row[COL_DATE]:
            continue
        try:
            date_str = _parse_date(row[COL_DATE])
        except Exception:
            warnings.append(f"第{row_idx}行日期格式异常: {row[COL_DATE]}")
            continue

        biz_type = str(row[COL_TYPE] or "").strip()
        if biz_type in PINGAN_IGNORE:
            continue
        if biz_type not in PINGAN_TYPE_MAP:
            warnings.append(f"第{row_idx}行未知业务类型: [{biz_type}]，已跳过")
            continue

        sys_type, is_special = PINGAN_TYPE_MAP[biz_type]
        ts_code = _infer_ts_code(str(row[COL_CODE] or ""))
        name = str(row[COL_NAME] or "").strip()
        quantity  = _to_float(row[COL_QTY])
        price     = _to_float(row[COL_PRICE])
        total_amt = _to_float(row[COL_AMT])   # 买入为负，卖出为正
        fund_chg  = _to_float(row[COL_FUND])  # 发生金额
        fee       = abs(_to_float(row[COL_FEE]))
        stamp_tax = abs(_to_float(row[COL_STAMP]))
        xfer_fee  = abs(_to_float(row[COL_XFER]))
        fee += xfer_fee
        remarks = biz_type if is_special else None

        if sys_type in ("BUY", "SELL", "RIGHTS", "SPLIT", "MERGE_OUT"):
            if not ts_code or quantity == 0:
                # 红股派息 quantity=0 的行是现金派息部分，应按 DIVIDEND 记录（XLS 解析器）
                if sys_type == "SPLIT" and biz_type == "红股派息" and ts_code and quantity == 0:
                    div_amount = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
                    if div_amount > 0:
                        transactions.append({
                            "ts_code": ts_code, "name": name,
                            "trans_type": "DIVIDEND",
                            "price": 0.0, "amount": 0,
                            "total_amt": div_amount,
                            "fee": 0.0, "stamp_tax": 0.0,
                            "date": date_str, "remarks": biz_type,
                        })
                continue
            # 平安：负qty行为注销临时认购代码记录，跳过
            if quantity < 0 and sys_type == "BUY":
                continue
            if _is_bond_subscription_name(name):
                if sys_type == "RIGHTS":
                    if fund_chg < 0:
                        calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
                        transactions.append({
                            "ts_code": ts_code, "name": name,
                            "trans_type": "BUY",
                            "price": calc_price,
                            "amount": int(abs(quantity)),
                            "total_amt": abs(total_amt),
                            "fee": fee, "stamp_tax": stamp_tax,
                            "date": date_str, "remarks": "配债认购",
                            "_biz_type": biz_type,
                        })
                elif sys_type == "BUY":  # 仅按买入处理，SELL不误入
                    calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
                    transactions.append({
                        "ts_code": ts_code, "name": name,
                        "trans_type": "BUY",
                        "price": calc_price,
                        "amount": int(abs(quantity)),
                        "total_amt": abs(total_amt),
                        "fee": fee, "stamp_tax": stamp_tax,
                        "date": date_str, "remarks": "配债认购",
                        "_biz_type": biz_type,
                    })
                continue
            calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": sys_type,
                "price": calc_price,
                "amount": int(abs(quantity)),
                "total_amt": abs(total_amt),
                "fee": fee, "stamp_tax": stamp_tax,
                "date": date_str, "remarks": remarks,
            })
            # 红股派息 quantity>0 时已记送股(SPLIT)，若同行含现金派息(fund_chg>0)，额外追加 DIVIDEND（XLS）
            if sys_type == "SPLIT" and biz_type == "红股派息":
                div_amount = abs(fund_chg) if fund_chg != 0 else 0
                if div_amount > 0:
                    transactions.append({
                        "ts_code": ts_code, "name": name,
                        "trans_type": "DIVIDEND",
                        "price": 0.0, "amount": 0,
                        "total_amt": div_amount,
                        "fee": 0.0, "stamp_tax": 0.0,
                        "date": date_str, "remarks": biz_type,
                    })

        elif sys_type == "XFER_IN":
            # 转托管入：股票从其他券商迁入。
            # 从单券商独立视角：记录 BUY（成本）+ DEPOSIT（资本投入）
            if not ts_code or quantity == 0:
                continue
            calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": "BUY",
                "price": calc_price,
                "amount": int(abs(quantity)),
                "total_amt": abs(total_amt),
                "fee": fee, "stamp_tax": stamp_tax,
                "date": date_str, "remarks": biz_type,
            })
            deposit_amt = abs(total_amt) if abs(total_amt) > 0 else abs(fund_chg)
            if deposit_amt > 0:
                capital_flows.append({
                    "flow_type": "DEPOSIT", "amount": deposit_amt,
                    "date": date_str, "remarks": f"转托管入({name})",
                })

        elif sys_type == "DIVIDEND":
            if not ts_code:
                continue
            div_amount = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": "DIVIDEND",
                "price": price, "amount": int(abs(quantity)),
                "total_amt": div_amount,
                "fee": 0.0, "stamp_tax": 0.0,
                "date": date_str, "remarks": biz_type,
            })

        elif sys_type == "DIVIDEND_TAX":
            if not ts_code:
                continue
            tax_amount = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
            if tax_amount == 0:
                continue
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": "DIVIDEND_TAX",
                "price": 0.0, "amount": 0,
                "total_amt": tax_amount,
                "fee": 0.0, "stamp_tax": 0.0,
                "date": date_str, "remarks": biz_type,
            })

        elif sys_type in ("DEPOSIT", "WITHDRAW"):
            amt = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
            if amt == 0:
                continue
            capital_flows.append({
                "flow_type": sys_type, "amount": amt,
                "date": date_str, "remarks": biz_type,
            })

    _normalize_bond_subscription_codes(transactions, warnings, skipped)
    return transactions, capital_flows, warnings, skipped


# ──────────────────────────────────────────
# 平安证券 CSV 解析器
# ──────────────────────────────────────────
# CSV 格式与 Excel 格式相比：
#   - 表头为双语（中文\nEnglish），首行即表头，第2行起为数据
#   - 列名取 \n 前的中文部分与 Excel 完全一致，逻辑复用
#
# 列名（18列）：
#   记账日期, 业务摘要, 资产账户, 股东帐号, 证券代码, 证券名称,
#   成交股数, 成交价格, 成交金额, 利息金额, 发生金额, 资金余额,
#   手续费, 印花税, 过户费等, 财汇局交易征费, 币种, 港股汇率

def parse_pingan_csv(content: bytes) -> Tuple[List[Dict], List[Dict], List[str], List[Dict]]:
    """解析平安证券 CSV 格式流水（utf-8-sig 编码，双语表头 中文\\nEnglish）"""
    import csv as csv_module

    for encoding in ("utf-8-sig", "gbk", "gb2312"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("无法识别文件编码，请确认是平安证券导出的 CSV 文件")

    reader = csv_module.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV 文件为空或无表头")

    # 校验表头（首列应含"记账日期"）
    if "记账日期" not in (reader.fieldnames[0] or ""):
        raise ValueError(f"表头不匹配，首列为: {reader.fieldnames[0]}")

    transactions, capital_flows, warnings, skipped = [], [], [], []

    for row_idx, row in enumerate(reader, start=2):
        # 归一化列名：取 \n 前的中文部分
        r = {(k or "").split("\n")[0].strip(): str(v or "").strip()
             for k, v in row.items() if k}

        date_raw = r.get("记账日期", "")
        if not date_raw:
            continue
        try:
            date_str = _parse_date(date_raw)
        except Exception:
            warnings.append(f"第{row_idx}行日期格式异常: {date_raw}")
            continue

        biz_type = r.get("业务摘要", "")
        if biz_type in PINGAN_IGNORE:
            continue
        if biz_type not in PINGAN_TYPE_MAP:
            warnings.append(f"第{row_idx}行未知业务类型: [{biz_type}]，已跳过")
            continue

        sys_type, is_special = PINGAN_TYPE_MAP[biz_type]
        ts_code   = _infer_ts_code(r.get("证券代码", ""))
        name      = r.get("证券名称", "")
        quantity  = _to_float(r.get("成交股数", 0))
        price     = _to_float(r.get("成交价格", 0))
        total_amt = _to_float(r.get("成交金额", 0))
        fund_chg  = _to_float(r.get("发生金额", 0))
        fee       = abs(_to_float(r.get("手续费", 0)))
        stamp_tax = abs(_to_float(r.get("印花税", 0)))
        xfer_fee  = abs(_to_float(r.get("过户费等", 0)))
        fee      += xfer_fee
        remarks   = biz_type if is_special else None

        if sys_type in ("BUY", "SELL", "RIGHTS", "SPLIT", "MERGE_OUT"):
            if not ts_code or quantity == 0:
                # 红股派息 quantity=0 的行是现金派息部分，应按 DIVIDEND 记录（CSV 解析器）
                if sys_type == "SPLIT" and biz_type == "红股派息" and ts_code and quantity == 0:
                    div_amount = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
                    if div_amount > 0:
                        transactions.append({
                            "ts_code": ts_code, "name": name,
                            "trans_type": "DIVIDEND",
                            "price": 0.0, "amount": 0,
                            "total_amt": div_amount,
                            "fee": 0.0, "stamp_tax": 0.0,
                            "date": date_str, "remarks": biz_type,
                        })
                continue
            # 平安：负qty行为注销临时认购代码记录，跳过
            if quantity < 0 and sys_type == "BUY":
                continue
            if _is_bond_subscription_name(name):
                if sys_type == "RIGHTS":
                    if fund_chg < 0:
                        calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
                        transactions.append({
                            "ts_code": ts_code, "name": name,
                            "trans_type": "BUY",
                            "price": calc_price,
                            "amount": int(abs(quantity)),
                            "total_amt": abs(total_amt),
                            "fee": fee, "stamp_tax": stamp_tax,
                            "date": date_str, "remarks": "配债认购",
                            "_biz_type": biz_type,
                        })
                elif sys_type == "BUY":  # 仅按买入处理，SELL不误入
                    calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
                    transactions.append({
                        "ts_code": ts_code, "name": name,
                        "trans_type": "BUY",
                        "price": calc_price,
                        "amount": int(abs(quantity)),
                        "total_amt": abs(total_amt),
                        "fee": fee, "stamp_tax": stamp_tax,
                        "date": date_str, "remarks": "配债认购",
                        "_biz_type": biz_type,
                    })
                continue
            calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": sys_type,
                "price": calc_price,
                "amount": int(abs(quantity)),
                "total_amt": abs(total_amt),
                "fee": fee, "stamp_tax": stamp_tax,
                "date": date_str, "remarks": remarks,
            })
            # 红股派息 quantity>0 时已记送股(SPLIT)，若同行含现金派息(fund_chg>0)，额外追加 DIVIDEND（CSV）
            if sys_type == "SPLIT" and biz_type == "红股派息":
                div_amount = abs(fund_chg) if fund_chg != 0 else 0
                if div_amount > 0:
                    transactions.append({
                        "ts_code": ts_code, "name": name,
                        "trans_type": "DIVIDEND",
                        "price": 0.0, "amount": 0,
                        "total_amt": div_amount,
                        "fee": 0.0, "stamp_tax": 0.0,
                        "date": date_str, "remarks": biz_type,
                    })

        elif sys_type == "XFER_IN":
            # 转托管入：股票从其他券商迁入。
            # 从单券商独立视角：记录 BUY（成本）+ DEPOSIT（资本投入）
            if not ts_code or quantity == 0:
                continue
            calc_price = price if price > 0 else (abs(total_amt) / abs(quantity) if quantity else 0)
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": "BUY",
                "price": calc_price,
                "amount": int(abs(quantity)),
                "total_amt": abs(total_amt),
                "fee": fee, "stamp_tax": stamp_tax,
                "date": date_str, "remarks": biz_type,
            })
            deposit_amt = abs(total_amt) if abs(total_amt) > 0 else abs(fund_chg)
            if deposit_amt > 0:
                capital_flows.append({
                    "flow_type": "DEPOSIT", "amount": deposit_amt,
                    "date": date_str, "remarks": f"转托管入({name})",
                })

        elif sys_type == "DIVIDEND":
            if not ts_code:
                continue
            div_amount = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": "DIVIDEND",
                "price": price, "amount": int(abs(quantity)),
                "total_amt": div_amount,
                "fee": 0.0, "stamp_tax": 0.0,
                "date": date_str, "remarks": biz_type,
            })

        elif sys_type == "DIVIDEND_TAX":
            if not ts_code:
                continue
            tax_amount = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
            if tax_amount == 0:
                continue
            transactions.append({
                "ts_code": ts_code, "name": name,
                "trans_type": "DIVIDEND_TAX",
                "price": 0.0, "amount": 0,
                "total_amt": tax_amount,
                "fee": 0.0, "stamp_tax": 0.0,
                "date": date_str, "remarks": biz_type,
            })

        elif sys_type in ("DEPOSIT", "WITHDRAW"):
            amt = abs(fund_chg) if fund_chg != 0 else abs(total_amt)
            if amt == 0:
                continue
            capital_flows.append({
                "flow_type": sys_type, "amount": amt,
                "date": date_str, "remarks": biz_type,
            })

    _normalize_bond_subscription_codes(transactions, warnings, skipped)
    return transactions, capital_flows, warnings, skipped


# ──────────────────────────────────────────
# 写入数据库
# ──────────────────────────────────────────

def _write_to_db(account_id: str, transactions: List[Dict], capital_flows: List[Dict]) -> Dict:
    """
    写入交易和资金流水，去重策略：
    - 交易：按 (date, ts_code, trans_type, amount, price, total_amt) 分组计数，
            DB 已有 N 条相同记录时，本批次只插入超出 N 的部分。
            同一天同一股票多笔不同价格/金额的真实成交会被正确区分。
    - 资金流水：按 (date, flow_type, amount) 分组计数，同上。
            同一天两笔相同金额的银证转账会被正确区分。
    """
    from collections import Counter

    inserted_tx = skipped_tx = inserted_cf = skipped_cf = 0

    def _tx_key(tx: Dict):
        return (tx["date"], tx["ts_code"], tx["trans_type"],
                tx["amount"], round(tx["price"], 4), round(tx["total_amt"], 2))

    def _cf_key(cf: Dict):
        return (cf["date"], cf["flow_type"], round(cf["amount"], 2))

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM accounts WHERE id = ?", (account_id,))
        if not c.fetchone():
            raise ValueError(f"账户 {account_id} 不存在")

        # ── 交易记录 ─────────────────────────────────────────────
        for key, needed in Counter(_tx_key(tx) for tx in transactions).items():
            date, ts_code, trans_type, amount, price, total_amt = key
            c.execute("""
                SELECT COUNT(*) FROM transactions
                WHERE account_id=? AND date=? AND ts_code=? AND trans_type=?
                  AND amount=? AND ABS(price-?)<0.0001 AND ABS(total_amt-?)<0.01
            """, (account_id, date, ts_code, trans_type, amount, price, total_amt))
            existing = c.fetchone()[0]
            to_insert = max(0, needed - existing)
            skipped_tx += needed - to_insert

            matching = [tx for tx in transactions if _tx_key(tx) == key]
            for tx in matching[:to_insert]:
                created_at = sh_timestamp_str()
                c.execute("""
                    INSERT INTO transactions
                    (account_id, ts_code, name, trans_type, price, amount, total_amt,
                     fee, stamp_tax, date, remarks, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    account_id, tx["ts_code"], tx["name"], tx["trans_type"],
                    tx["price"], tx["amount"], tx["total_amt"],
                    round(tx["fee"] or 0, 2), round(tx["stamp_tax"] or 0, 2), tx["date"], tx["remarks"], created_at,
                ))
                inserted_tx += 1

        # ── 资金流水 ─────────────────────────────────────────────
        for key, needed in Counter(_cf_key(cf) for cf in capital_flows).items():
            date, flow_type, amount = key
            c.execute("""
                SELECT COUNT(*) FROM capital_flows
                WHERE account_id=? AND date=? AND flow_type=? AND ABS(amount-?)<0.01
            """, (account_id, date, flow_type, amount))
            existing = c.fetchone()[0]
            to_insert = max(0, needed - existing)
            skipped_cf += needed - to_insert

            matching = [cf for cf in capital_flows if _cf_key(cf) == key]
            for cf in matching[:to_insert]:
                created_at = sh_timestamp_str()
                c.execute("""
                    INSERT INTO capital_flows (account_id, flow_type, amount, date, remarks, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (account_id, cf["flow_type"], cf["amount"], cf["date"], cf.get("remarks"), created_at))
                inserted_cf += 1

        # 重建持仓和现金余额
        skipped_sells = _rebuild_holdings(account_id, conn)
        conn.commit()

    return {
        "transactions": {"inserted": inserted_tx, "skipped": skipped_tx},
        "capital_flows": {"inserted": inserted_cf, "skipped": skipped_cf},
        "skipped_sells": skipped_sells,
    }


# ──────────────────────────────────────────
# 重建持仓（从交易记录重放）
# ──────────────────────────────────────────

def _rebuild_holdings(account_id: str, conn) -> list:
    """
    按时间顺序重放该账户所有交易记录，重建 holdings 和 cash_balance。
    在同一个 DB 连接事务内执行。
    返回 skipped_sells 列表：无法匹配买入记录的卖出，已跳过，需用户手动补录。
    """
    c = conn.cursor()

    # 保存旧持仓的 buy_pb（重建后需要还原，因为 transactions 表不存 buy_pb）
    c.execute("SELECT ts_code, buy_pb FROM holdings WHERE account_id = ?", (account_id,))
    _old_buy_pb = {row["ts_code"]: row["buy_pb"] for row in c.fetchall() if row["buy_pb"]}

    # 清空旧持仓
    c.execute("DELETE FROM holdings WHERE account_id = ?", (account_id,))
    # 重置现金余额
    c.execute("UPDATE accounts SET cash_balance = 0 WHERE id = ?", (account_id,))

    # 按日期正序重放所有交易
    c.execute("""
        SELECT ts_code, name, trans_type, price, amount, total_amt,
               fee, stamp_tax, date, remarks
        FROM transactions
        WHERE account_id = ?
        ORDER BY date ASC, id ASC
    """, (account_id,))
    txs = c.fetchall()

    holdings: dict = {}  # ts_code -> {qty, total_cost, name}
    skipped_sells: list = []  # 无对应买入记录的卖出（如股份认领 qty=0 未导入）
    cash = 0.0

    # ── 吸收合并预扫描 ──
    # 只把"有后续到账配对"的 MERGE_OUT 入合并池；
    # 纯退市（注销后无新股）的 MERGE_OUT 不入池，避免污染成本。
    MERGER_ARRIVAL_BIZ = {"新股入帐", "新股入账", "新股上市"}
    from collections import deque
    from datetime import date as _dt_date

    # 只有普通 A 股（6/3/0 开头）才能成为合法的吸收合并 MERGE_OUT 来源
    # 公司债/可转债（4/1/2 开头）等排除，避免退市债券误触合并池
    def _is_stock_code(ts: str) -> bool:
        c6 = ts.split(".")[0]
        return bool(c6) and c6[0] in ("6", "3", "0")

    # 收集所有 MERGE_OUT 事件（按时间排序，已经 ORDER BY date ASC）
    _merge_outs = [(tx["ts_code"], tx["date"])
                   for tx in txs
                   if tx["trans_type"] == "MERGE_OUT" and _is_stock_code(tx["ts_code"])]
    # 收集所有零成本白名单 BUY（候选合并到账）
    _merge_in_candidates = [(tx["ts_code"], tx["date"])
                            for tx in txs
                            if tx["trans_type"] == "BUY"
                            and tx["total_amt"] == 0
                            and (tx["fee"] or 0) == 0
                            and (tx["remarks"] or "") in MERGER_ARRIVAL_BIZ]
    # 构建：哪些 MERGE_OUT 有配对的到账（≤60天、不同代码）
    _confirmed_merge_out: set = set()
    for out_code, out_date in _merge_outs:
        try:
            out_d = _dt_date.fromisoformat(out_date)
        except Exception:
            continue
        for in_code, in_date in _merge_in_candidates:
            try:
                in_d = _dt_date.fromisoformat(in_date)
            except Exception:
                continue
            if in_code != out_code and 0 <= (in_d - out_d).days <= 60:
                _confirmed_merge_out.add((out_code, out_date))
                break  # 找到一个配对即确认

    # 运行时成本池：仅确认合并的 MERGE_OUT 会入队
    merge_pool: deque = deque()

    # 资金流水（计算净投入现金）
    c.execute("""
        SELECT flow_type, amount FROM capital_flows WHERE account_id = ? ORDER BY date ASC, id ASC
    """, (account_id,))
    for cf in c.fetchall():
        if cf["flow_type"] == "DEPOSIT":
            cash += cf["amount"]
        elif cf["flow_type"] == "WITHDRAW":
            cash -= cf["amount"]

    for tx in txs:
        ts_code  = tx["ts_code"]
        qty      = tx["amount"]
        total    = tx["total_amt"]
        fee      = tx["fee"] or 0
        stamp    = tx["stamp_tax"] or 0
        ttype    = tx["trans_type"]
        name     = tx["name"] or ""

        if ts_code not in holdings:
            holdings[ts_code] = {"name": name, "qty": 0, "total_cost": 0.0}

        h = holdings[ts_code]
        # 始终用最新 name 更新持仓名称（处理股票更名/吸收合并后更名）
        if name:
            h["name"] = name

        if ttype == "BUY":
            cost = total + fee
            # ── 吸收合并到账（双重精确门控）──
            # 条件1：业务摘要白名单（仅三个已知合并到账业务类型）
            # 条件2：成本池有未消费的 MERGE_OUT
            # 条件3：时间接近度：本 BUY 与队头 MERGE_OUT 间隔 ≤ 60 天
            # 三条同时满足才继承成本，否则按正常 BUY 处理（成本=0 或付出金额）
            tx_remarks = tx["remarks"] or ""
            is_merger_arrival = False
            if (total == 0 and fee == 0
                    and tx_remarks in MERGER_ARRIVAL_BIZ
                    and merge_pool):
                from datetime import date as _date
                try:
                    in_d  = _date.fromisoformat(tx["date"])
                    out_d = _date.fromisoformat(merge_pool[0]["date"])
                    if 0 <= (in_d - out_d).days <= 60:
                        is_merger_arrival = True
                except Exception:
                    pass  # 日期解析失败则不视为合并到账

            if is_merger_arrival:
                pool_entry = merge_pool.popleft()
                h["qty"] += qty
                h["total_cost"] += pool_entry["cost"]
                # 不影响 cash（非真实买入）
            else:
                h["qty"] += qty
                h["total_cost"] += cost
                cash -= (total + fee)

        elif ttype == "SELL":
            sell_qty = qty
            if sell_qty > h["qty"]:
                # 无对应买入记录（如股份认领 qty=0 导致持仓为0），跳过此卖出，记录警告
                skipped_sells.append(
                    f"{ts_code}（{h['name'] or name}）{tx['date']} 卖出{sell_qty}股"
                    f"（无对应买入记录，已跳过，请手动补录）"
                )
                continue
            # 净投入法：用卖出净回款冲减成本基数，允许成本为负（表示已回本）。
            # 与 transactions.py create_transaction SELL 路径保持一致。
            net_proceeds = total - fee - stamp
            h["total_cost"] = h["total_cost"] - net_proceeds
            h["qty"] -= sell_qty
            cash += net_proceeds
            # 清仓截断：卖出后持仓归零，立即重置成本，开启新周期
            if h["qty"] == 0:
                h["total_cost"] = 0.0

        elif ttype == "RIGHTS":
            if h["qty"] <= 0:
                # 正式可转债代码（127/123/128/110/113 开头）的"配售"是首次认购入账，
                # 不存在对应正股持仓，按首次 BUY 处理；正股配股确实要求有持仓。
                if _is_formal_bond_code(ts_code):
                    cost = total + fee + stamp
                    h["qty"] += qty
                    h["total_cost"] += cost
                    cash -= cost
                    continue
                # 正股无持仓（如来自股份认领 qty=0 未导入），跳过并记录警告
                skipped_sells.append(
                    f"{ts_code}（{h['name'] or name}）{tx['date']} 配股{qty}股"
                    f"（无对应持仓记录，已跳过，请手动补录）"
                )
                continue
            rights_cost = total + fee + stamp
            h["qty"] += qty
            h["total_cost"] += rights_cost
            cash -= rights_cost

        elif ttype == "DIVIDEND":
            # 现金分红降低成本；qty=0（已清仓）时不影响新周期成本，允许为负不截断
            if h["qty"] > 0:
                h["total_cost"] -= total
            cash += total

        elif ttype == "DIVIDEND_TAX":
            # 股息红利差异扣税：属于投资支出（追缴税款），增加成本基数并扣减现金。
            # 与红利入账对称：红利收入降低成本；差异税支出增加成本。
            h["total_cost"] += total
            cash -= total

        elif ttype == "SPLIT":
            h["qty"] += qty
            # 成本不变，单价摊薄

        elif ttype == "MERGE_OUT":
            # 吸收合并转出：仅当预扫描确认有后续到账配对时才入合并池
            # 纯退市（摘牌无换股）的 MERGE_OUT 不入池，避免污染后续 IPO 成本
            if (ts_code, tx["date"]) in _confirmed_merge_out:
                merge_pool.append({"out_code": ts_code,
                                   "date": tx["date"],
                                   "cost": h["total_cost"]})
            h["total_cost"] = 0.0
            h["qty"] = 0
            # 不影响 cash（非真实卖出）

        elif ttype == "MERGE_IN":
            # 显式 MERGE_IN（未来扩展留用）：目前为 BUY is_special pool门控路径分流
            if merge_pool:
                pool_entry = merge_pool.popleft()
                h["qty"] += qty
                h["total_cost"] += pool_entry["cost"]
            else:
                # 无匹配的 MERGE_OUT，按正常 BUY（成本=0）处理
                h["qty"] += qty
            # 不影响 cash

    # 写回 holdings；qty=0 的自动归档到 archived_holdings
    for ts_code, h in holdings.items():
        if h["qty"] > 0:
            cost_price = h["total_cost"] / h["qty"] if h["qty"] > 0 else 0
            preserved_buy_pb = _old_buy_pb.get(ts_code, 0.0)
            now_ts = sh_timestamp_str()
            c.execute("""
                INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost, buy_pb, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (account_id, ts_code, h["name"], h["qty"],
                  round(cost_price, 4), round(h["total_cost"], 3), preserved_buy_pb, now_ts, now_ts))
        else:
            # 清仓：从交易记录汇总盈亏，upsert 到 archived_holdings
            c.execute("""
                SELECT
                    SUM(CASE WHEN trans_type='BUY'      THEN total_amt + COALESCE(fee,0) + COALESCE(stamp_tax,0) ELSE 0 END) AS total_buy,
                    SUM(CASE WHEN trans_type='SELL'     THEN total_amt - COALESCE(fee,0) - COALESCE(stamp_tax,0) ELSE 0 END) AS total_sell,
                    SUM(CASE WHEN trans_type='DIVIDEND' THEN total_amt ELSE 0 END)                                            AS total_dividend,
                    SUM(CASE WHEN trans_type IN ('BUY','SELL','RIGHTS') THEN COALESCE(fee,0)+COALESCE(stamp_tax,0) ELSE 0 END) AS total_fees,
                    MIN(CASE WHEN trans_type='BUY' THEN date END)  AS first_buy_date,
                    MAX(CASE WHEN trans_type='SELL' THEN date END) AS last_sell_date
                FROM transactions
                WHERE account_id=? AND ts_code=?
            """, (account_id, ts_code))
            row = c.fetchone()
            if not row or not row["first_buy_date"]:
                continue   # 无买入记录（如纯送股），跳过

            total_buy      = row["total_buy"]      or 0.0
            total_sell     = row["total_sell"]     or 0.0
            total_dividend = row["total_dividend"] or 0.0
            total_fees     = row["total_fees"]     or 0.0
            profit = total_sell + total_dividend - total_buy
            profit_pct = (profit / total_buy * 100) if total_buy > 0 else 0.0

            c.execute("SELECT id FROM archived_holdings WHERE account_id=? AND ts_code=?",
                      (account_id, ts_code))
            existing = c.fetchone()
            if existing:
                c.execute("""
                    UPDATE archived_holdings
                    SET stock_name=?, total_buy_amount=?, total_sell_amount=?,
                        total_dividend=?, total_profit=?, profit_pct=?, total_fees=?,
                        first_buy_date=?, last_sell_date=?
                    WHERE account_id=? AND ts_code=?
                """, (
                    h["name"],
                    round(total_buy, 2), round(total_sell, 2),
                    round(total_dividend, 2), round(profit, 2),
                    round(profit_pct, 2), round(total_fees, 2),
                    row["first_buy_date"], row["last_sell_date"],
                    account_id, ts_code,
                ))
            else:
                c.execute("""
                    INSERT INTO archived_holdings
                    (account_id, ts_code, stock_name, total_buy_amount, total_sell_amount,
                     total_dividend, total_profit, profit_pct, total_fees,
                     first_buy_date, last_sell_date, archived_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    account_id, ts_code, h["name"],
                    round(total_buy, 2), round(total_sell, 2),
                    round(total_dividend, 2), round(profit, 2),
                    round(profit_pct, 2), round(total_fees, 2),
                    row["first_buy_date"], row["last_sell_date"], sh_timestamp_str(),
                ))

    # 更新现金余额
    c.execute("UPDATE accounts SET cash_balance = ? WHERE id = ?",
              (round(cash, 2), account_id))

    return skipped_sells



# ──────────────────────────────────────────
# 华林证券解析器（仅 CSV）
# ──────────────────────────────────────────

# 华林证券 CSV 字段说明：
#   日    期  业务标志  银    行  证券代码  证券名称  股东账号
#   价    格  发 生 数  发生金额  资金余额
#   手 续 费  印 花 税  过 户 费  净手续费  财汇局征费
#   操作方式  备    注
#
# 列名含多余空格，需 strip 后比较。
# 发生金额：买入为负，卖出为正；发 生 数 始终为正。

HUALIN_TYPE_MAP = {
    # 买入
    "证券买入":     ("BUY",          False),
    "港股通买入":   ("BUY",          True),
    "新股入账":     ("BUY",          True),
    "中签股份下账": ("BUY",          True),
    "申购中签":     ("BUY",          True),
    "指定入账":     ("XFER_IN",      True),   # 从其他账户转入持仓，同转托管入
    "股份认领":     ("BUY",          True),   # 开户时带入的持仓
    # 卖出
    "证券卖出":     ("SELL",         False),
    "股份转出":         ("MERGE_OUT", True),  # 吸收合并/摘牌转出（无现金）
    # 配股
    "配股入账":     ("RIGHTS",       True),
    "配股缴款":     ("RIGHTS",       True),
    "配售股份":     ("RIGHTS",       True),
    "配售缴款":     ("RIGHTS",       True),
    # 送股（股票股利）
    "红股入账":     ("SPLIT",        True),
    # 现金分红（qty=0）或送转股（qty>0）在解析时分别处理
    "红利入账":     ("DIVIDEND",     False),
    # 债券利息
    "债券兑息":             ("DIVIDEND",     False),
    "债券兑息兑付扣税":     ("DIVIDEND",     False),
    # 股息税
    "股息红利差异扣税": ("DIVIDEND_TAX", False),
    # 银证转账
    "银行转证券":   ("DEPOSIT",      False),
    "证券转银行":   ("WITHDRAW",     False),
    "利息归本":     ("DEPOSIT",      False),  # 账户利息入账，与民生/平安处理一致
}

HUALIN_IGNORE = {
    "融券回购", "融券购回",          # 融资融券，不影响现货持仓

    "港股通组合费",                   # 手续费，不影响持仓
    "删除过期证券",                   # 无效证券清理
    "开放基金申购",                   # 基金，不处理
    "基金下折", "基金上折",
    "新股申购", "申购还款",           # 打新流程，成交前无须处理
    "指定交易",                       # 指定交易关系，无实际交割
    "配股权证",                       # 权证，不处理
}


def parse_hualin_csv(content: bytes) -> Tuple[List[Dict], List[Dict], List[str], List[Dict]]:
    """解析华林证券 CSV 格式流水（utf-8 编码，首行表头，列名含多余空格）"""
    import csv as csv_module

    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb2312"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("无法识别文件编码，请确认是华林证券导出的 CSV 文件")

    reader = csv_module.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV 文件为空或无表头")

    # 列名含多余空格，归一化后比较
    def _col(row: dict, name: str) -> str:
        for k, v in row.items():
            if k and k.strip() == name:
                return str(v or "").strip()
        return ""

    # 校验表头
    first_row = None
    for row in reader:
        first_row = row
        break
    if first_row is None or not _col(first_row, "业务标志"):
        raise ValueError("表头不匹配，请确认是华林证券 CSV 流水文件")

    # 重新建立 reader（已消耗第一行，重新解析）
    reader2 = csv_module.DictReader(io.StringIO(text))

    transactions: List[Dict] = []
    capital_flows: List[Dict] = []
    warnings: List[str] = []
    skipped: List[Dict] = []

    for row_idx, row in enumerate(reader2, start=2):
        date_raw = _col(row, "日    期")
        if not date_raw:
            continue
        try:
            date_str = _parse_date(date_raw)
        except Exception:
            warnings.append(f"第{row_idx}行日期格式异常: {date_raw}")
            continue

        biz_type = _col(row, "业务标志")
        if biz_type in HUALIN_IGNORE:
            continue
        if biz_type not in HUALIN_TYPE_MAP:
            if biz_type:
                warnings.append(f"第{row_idx}行未知业务类型: [{biz_type}]，已跳过")
            continue

        sys_type, is_special = HUALIN_TYPE_MAP[biz_type]

        code_raw  = _col(row, "证券代码")
        name      = _col(row, "证券名称")
        ts_code   = _infer_ts_code(code_raw)
        price     = _to_float(_col(row, "价    格"))
        quantity  = _to_float(_col(row, "发 生 数"))
        fund_chg  = _to_float(_col(row, "发生金额"))  # 正=现金入账，负=现金出账
        # 发生金额=0 的 SELL → 吸收合并置换，不是真实卖出
        if sys_type == "SELL" and fund_chg == 0 and abs(quantity) > 0:
            sys_type = "MERGE_OUT"
        commission= _to_float(_col(row, "手 续 费"))
        stamp_tax = _to_float(_col(row, "印 花 税"))
        xfer_fee  = _to_float(_col(row, "过 户 费"))
        hk_fee    = _to_float(_col(row, "财汇局征费"))
        fee = commission + xfer_fee + hk_fee
        remarks = biz_type if is_special else None

        # ── 银证转账 ──
        if sys_type in ("DEPOSIT", "WITHDRAW"):
            amount = abs(fund_chg)
            if amount == 0:
                continue
            capital_flows.append({
                "flow_type": sys_type,
                "amount":    round(amount, 2),
                "date":      date_str,
                "remarks":   biz_type,
            })
            continue

        # ── 买卖 / 配股 / 送股 ──
        if sys_type in ("BUY", "SELL", "RIGHTS", "SPLIT", "MERGE_OUT"):
            if not ts_code or quantity == 0:
                continue
            # 华林的"发生金额"是账户资金净变动（含手续费），不是纯成交金额。
            # 用 price × quantity 计算纯成交额，与民生/平安语义一致（fee 单独存储），
            # 避免在 _rebuild_holdings 的 cost = total_amt + fee 中双计手续费。
            # price=0 的特殊情况（股份认领、零成本到账等）才回退用 fund_chg。
            if price > 0 and quantity > 0:
                total_amt = round(price * quantity, 2)
            else:
                total_amt = abs(fund_chg)
            calc_price = price if price > 0 else (total_amt / quantity if quantity else 0)
            # 可转债认购归并：名称含"发债"/"配债"，无论业务类型统一走归并逻辑
            if _is_bond_subscription_name(name):
                if sys_type == "RIGHTS":
                    if fund_chg < 0:
                        transactions.append({
                            "ts_code":    ts_code,
                            "name":       name,
                            "trans_type": "BUY",
                            "price":      round(calc_price, 4),
                            "amount":     int(abs(quantity)),
                            "total_amt":  round(total_amt, 2),
                            "fee":        round(fee, 2),
                            "stamp_tax":  round(stamp_tax, 2),
                            "date":       date_str,
                            "remarks":    "配债认购",
                            "_raw":       {(k or "").strip(): (v or "") for k, v in row.items()},
                        })
                elif sys_type == "BUY":  # 仅按买入处理，SELL不误入
                    transactions.append({
                        "ts_code":    ts_code,
                        "name":       name,
                        "trans_type": "BUY",
                        "price":      round(calc_price, 4),
                        "amount":     int(abs(quantity)),
                        "total_amt":  round(total_amt, 2),
                        "fee":        round(fee, 2),
                        "stamp_tax":  round(stamp_tax, 2),
                        "date":       date_str,
                        "remarks":    "配债认购",
                        "_raw":       {(k or "").strip(): (v or "") for k, v in row.items()},
                    })
                continue
            transactions.append({
                "ts_code":    ts_code,
                "name":       name,
                "trans_type": sys_type,
                "price":      round(calc_price, 4),
                "amount":     int(abs(quantity)),
                "total_amt":  round(total_amt, 2),
                "fee":        round(fee, 2),
                "stamp_tax":  round(stamp_tax, 2),
                "date":       date_str,
                "remarks":    remarks,
            })
            continue

        # ── 指定入账 / 转托管入：股票从其他账户迁入，转成 BUY + DEPOSIT ──
        if sys_type == "XFER_IN":
            if not ts_code or quantity == 0:
                continue
            calc_price = price if price > 0 else (abs(fund_chg) / abs(quantity) if quantity else 0)
            xfer_amt = abs(fund_chg) if abs(fund_chg) > 0 else round(calc_price * abs(quantity), 2)
            transactions.append({
                "ts_code":    ts_code,
                "name":       name,
                "trans_type": "BUY",
                "price":      round(calc_price, 4),
                "amount":     int(abs(quantity)),
                "total_amt":  xfer_amt,
                "fee":        0.0,
                "stamp_tax":  0.0,
                "date":       date_str,
                "remarks":    biz_type,
            })
            if xfer_amt > 0:
                capital_flows.append({
                    "flow_type": "DEPOSIT",
                    "amount":    xfer_amt,
                    "date":      date_str,
                    "remarks":   f"转入持仓({name})",
                })
            continue

        # ── 红利入账：华林 CSV 里 quantity 是持股基数（分红对象股数），不是送股数 ──
        # 真正的送股/转增已经由 红股入账 → SPLIT 独立处理，这里只写现金分红
        if sys_type == "DIVIDEND":
            if not ts_code:
                continue
            cash_div = abs(fund_chg)
            if cash_div > 0:
                transactions.append({
                    "ts_code":    ts_code,
                    "name":       name,
                    "trans_type": "DIVIDEND",
                    "price":      0.0,
                    "amount":     0,
                    "total_amt":  round(cash_div, 2),
                    "fee":        0.0,
                    "stamp_tax":  0.0,
                    "date":       date_str,
                    "remarks":    biz_type,
                })
            continue

        # ── 股息税 ──
        if sys_type == "DIVIDEND_TAX":
            if not ts_code:
                continue
            tax_amount = abs(fund_chg)
            if tax_amount == 0:
                continue
            transactions.append({
                "ts_code":    ts_code,
                "name":       name,
                "trans_type": "DIVIDEND_TAX",
                "price":      0.0,
                "amount":     0,
                "total_amt":  round(tax_amount, 2),
                "fee":        0.0,
                "stamp_tax":  0.0,
                "date":       date_str,
                "remarks":    biz_type,
            })

    _normalize_bond_subscription_codes(transactions, warnings, skipped)
    return transactions, capital_flows, warnings, skipped


# ──────────────────────────────────────────
# API 端点
# ──────────────────────────────────────────

PARSERS = {
    "minsheng": parse_minsheng,
    "pingan":   parse_pingan,
}

CSV_PARSERS = {
    "minsheng": parse_minsheng_csv,
    "pingan":   parse_pingan_csv,
    "hualin":   parse_hualin_csv,
}


@router.post("/import/flow")
async def import_flow(
    account_id: str = Form(...),
    broker_type: str = Form(...),
    file: UploadFile = File(...),
):
    """导入券商交易流水（Excel 或 CSV）。broker_type: minsheng | pingan | hualin"""
    fname = (file.filename or "").lower()
    is_csv   = fname.endswith(".csv")
    is_excel = fname.endswith(".xlsx")

    if not (is_csv or is_excel):
        raise HTTPException(status_code=400, detail="请上传 Excel（.xlsx）或 CSV（.csv）文件")

    if is_csv and broker_type not in CSV_PARSERS:
        raise HTTPException(status_code=400, detail=f"券商 [{broker_type}] 暂不支持 CSV 格式，请使用 Excel")

    if is_excel and broker_type not in PARSERS:
        raise HTTPException(status_code=400, detail=f"不支持的券商类型: {broker_type}")

    content = await read_limited_upload(file, MAX_GENERIC_UPLOAD_BYTES)

    # 按内容实际格式路由，覆盖扩展名（平安证券有时导出 .csv 但实际是 xlsx binary）
    is_actually_xlsx = content[:4] == b'PK\x03\x04'
    if is_actually_xlsx:
        if broker_type not in PARSERS:
            raise HTTPException(status_code=400, detail=f"不支持的券商类型: {broker_type}")
        is_csv, is_excel = False, True

    try:
        if is_csv:
            transactions, capital_flows, warnings, skipped = CSV_PARSERS[broker_type](content)
        else:
            transactions, capital_flows, warnings, skipped = PARSERS[broker_type](content)
        result = _write_to_db(account_id, transactions, capital_flows)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"导入失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"解析失败: {e}")

    # 生成跳过记录的 CSV 字符串（供前端提供下载）
    # 动态收集所有 entry 的列名（保持顺序，合并各行可能不同的列）
    skipped_bonds_csv = ""
    if skipped:
        import csv as _csv
        seen_fields: list = []
        seen_set: set = set()
        for entry in skipped:
            for k in entry:
                if k not in seen_set and not str(k).startswith("_"):
                    seen_fields.append(k)
                    seen_set.add(k)
        buf = io.StringIO()
        writer = _csv.DictWriter(buf, fieldnames=seen_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(skipped)
        skipped_bonds_csv = "﻿" + buf.getvalue()  # UTF-8 BOM，Excel 直接打开不乱码

    msg_base = (
        f"导入完成：交易记录 +{result['transactions']['inserted']} 条"
        f"（跳过重复 {result['transactions']['skipped']} 条），"
        f"资金流水 +{result['capital_flows']['inserted']} 条"
        f"（跳过重复 {result['capital_flows']['skipped']} 条）"
    )
    if skipped:
        msg_base += f"；⚠️ {len(skipped)} 条可转债认购无法自动归并，请下载 CSV 手动补录买入。"
    skipped_sells = result.get("skipped_sells", [])
    if skipped_sells:
        msg_base += f"；⚠️ {len(skipped_sells)} 笔卖出无对应买入记录（如开户转入的股份），已跳过，请手动补录：" + "；".join(skipped_sells[:5])
        if len(skipped_sells) > 5:
            msg_base += f"…等共 {len(skipped_sells)} 笔"

    return {
        "status": "success",
        "broker": broker_type,
        "file": file.filename,
        "result": result,
        "warnings": warnings[:20],
        "skipped_bonds_count": len(skipped),
        "skipped_bonds_csv": skipped_bonds_csv,
        "skipped_sells": skipped_sells,  # 供前端生成"核对文件"下载
        "message": msg_base,
    }


@router.post("/import/rebuild-all-holdings")
async def rebuild_all_holdings():
    """对所有账户重跑 _rebuild_holdings，修复持仓成本价。
    用于逻辑变更后批量修正存量数据。"""
    results = {}
    errors = {}
    with get_db() as conn:
        account_ids = [row["id"] for row in conn.execute("SELECT id FROM accounts").fetchall()]
        for aid in account_ids:
            sp = f"sp_rebuild_{aid.replace('-', '_')}"
            conn.execute(f"SAVEPOINT {sp}")
            released = False
            try:
                skipped = _rebuild_holdings(aid, conn)
            except Exception as e:
                errors[aid] = str(e)
                try:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                finally:
                    conn.execute(f"RELEASE SAVEPOINT {sp}")
                    released = True
            else:
                results[aid] = {"skipped_sells": len(skipped)}
            finally:
                if not released:
                    conn.execute(f"RELEASE SAVEPOINT {sp}")
        conn.commit()
    return {
        "status": "ok" if not errors else "partial",
        "rebuilt": results,
        "errors": errors,
        "message": f"已重建 {len(results)} 个账户的持仓数据" + (f"，{len(errors)} 个失败" if errors else ""),
    }
