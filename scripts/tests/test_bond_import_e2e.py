"""
端到端测试：模拟三个券商 CSV 文件的可转债导入流程。
提取所有认购记录，运行归并逻辑，打印：
  - 归并成功的记录（会被导入）
  - 归并失败的记录（被跳过，需手动补录）
不依赖完整后端，只用纯 Python。
"""
import re, csv, io, os, sys

# ── 复制自 import_flow.py 的核心纯函数 ─────────────────────

def _bond_name_root(name: str) -> str:
    for sfx in ("发债", "配债"):
        if name.endswith(sfx):
            return name[:-len(sfx)]
    m = re.match(r'^(.+?)转债[\w]*$', name)
    if m:
        return m.group(1)
    m = re.match(r'^(.+?)转\d+$', name)
    if m:
        return m.group(1)
    return name

def _is_formal_bond_code(ts_code: str) -> bool:
    if not ts_code or "." not in ts_code:
        return False
    code6 = ts_code[:6]
    market = ts_code.split(".")[-1]
    if market == "SH":
        return any(code6.startswith(p) for p in ("110", "111", "113", "118"))
    if market == "SZ":
        return any(code6.startswith(p) for p in ("123", "127", "128"))
    return False

def _is_bond_subscription_name(name: str) -> bool:
    return "发债" in name or "配债" in name

def _infer_ts_code(code: str) -> str:
    code = str(code).strip().split(".")[0]
    if not code.isdigit() or len(code) != 6:
        return ""
    if code.startswith(("60", "68", "11", "50", "70", "71", "72", "73", "78", "75", "76")):
        return code + ".SH"
    if code.startswith(("00", "30", "12", "15", "37", "08", "38")):
        return code + ".SZ"
    return code + ".SH"

def normalize(transactions, warnings_out):
    """归并逻辑：成功→替换代码；失败→移除+warning"""
    formal_map = {}
    for tx in transactions:
        if not _is_formal_bond_code(tx["ts_code"]):
            continue
        root = _bond_name_root(tx["name"])
        if root not in formal_map:
            formal_map[root] = (tx["ts_code"], tx["name"])

    to_remove = []
    for tx in transactions:
        if tx.get("remarks") != "配债认购":
            continue
        root = _bond_name_root(tx["name"])
        if root in formal_map:
            old = tx["ts_code"]
            tx["ts_code"], tx["name"] = formal_map[root]
            tx["_merged_from"] = old
        else:
            to_remove.append(tx)
            warnings_out.append(
                f"⚠️  跳过（无法归并）: {tx['name']}({tx['ts_code']}) "
                f"日期={tx.get('date','')} 数量={tx.get('amount','')} 金额={tx.get('total_amt','')}"
            )
    for tx in to_remove:
        transactions.remove(tx)


# ── 每个券商的简化解析器 ──────────────────────────────────

def read_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb2312"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return ""

def parse_hualin(path):
    """华林：读发生金额/发生数/业务标志"""
    text = read_file(path)
    reader = csv.DictReader(io.StringIO(text))
    def col(row, k):
        for rk, rv in row.items():
            if (rk or "").strip() == k:
                return str(rv or "").strip()
        return ""
    txs, warns = [], []
    FUND_BIZ = {"配售缴款", "配股缴款"}
    for row in reader:
        biz  = col(row, "业务标志")
        code = _infer_ts_code(col(row, "证券代码"))
        name = col(row, "证券名称")
        date = col(row, "日    期")
        fund_str = col(row, "发生金额")
        qty_str  = col(row, "发 生 数")
        try:
            fund = float(fund_str) if fund_str else 0
            qty  = float(qty_str)  if qty_str  else 0
        except Exception:
            fund = qty = 0
        if biz in FUND_BIZ and _is_bond_subscription_name(name) and fund < 0 and code:
            txs.append({
                "ts_code": code, "name": name, "trans_type": "BUY",
                "amount": int(abs(qty)), "total_amt": round(abs(fund), 2),
                "date": date, "remarks": "配债认购",
            })
        elif code and not _is_bond_subscription_name(name) and biz not in {"删除过期证券"}:
            txs.append({
                "ts_code": code, "name": name, "trans_type": biz,
                "amount": int(abs(qty)), "total_amt": round(abs(fund), 2),
                "date": date, "remarks": None,
            })
    normalize(txs, warns)
    return txs, warns

def parse_minsheng(path):
    """民生：简化解析"""
    text = read_file(path)
    reader = csv.DictReader(io.StringIO(text))
    BOND_BIZ = {"新股登记", "国债新股上市和配股到账"}
    txs, warns = [], []
    for row in reader:
        biz  = str(row.get("业务标志", "") or "").strip()
        code = _infer_ts_code(str(row.get("证券代码", "") or "").strip())
        name = str(row.get("证券名称", "") or "").strip()
        date = str(row.get("发生日期", "") or "").strip()
        try:
            qty  = float(str(row.get("成交数量", 0) or 0))
            amt  = float(str(row.get("发生金额", 0) or 0))
        except Exception:
            qty = amt = 0
        if not code or not name:
            continue
        if _is_bond_subscription_name(name) or biz in BOND_BIZ:
            if qty > 0 and abs(amt) > 0:
                txs.append({
                    "ts_code": code, "name": name, "trans_type": "BUY",
                    "amount": int(abs(qty)), "total_amt": round(abs(amt), 2),
                    "date": date, "remarks": "配债认购",
                })
        else:
            txs.append({
                "ts_code": code, "name": name, "trans_type": biz,
                "amount": int(abs(qty)), "total_amt": round(abs(amt), 2),
                "date": date, "remarks": None,
            })
    normalize(txs, warns)
    return txs, warns

def parse_pingan(path):
    """平安：简化解析"""
    text = read_file(path)
    reader = csv.DictReader(io.StringIO(text))
    txs, warns = [], []
    for row in reader:
        biz  = str(row.get("业务标志", "") or row.get("摘要", "") or "").strip()
        code = _infer_ts_code(str(row.get("证券代码", "") or "").strip())
        name = str(row.get("证券名称", "") or "").strip()
        date = str(row.get("日期", "") or row.get("交易日期", "") or "").strip()
        try:
            qty = float(str(row.get("成交股数", 0) or row.get("发生数量", 0) or 0))
            amt = float(str(row.get("发生金额", 0) or row.get("成交金额", 0) or 0))
        except Exception:
            qty = amt = 0
        if not code or not name:
            continue
        if qty < 0:
            continue  # 平安注销临时代码的负qty行
        if _is_bond_subscription_name(name) and qty > 0 and abs(amt) > 0:
            txs.append({
                "ts_code": code, "name": name, "trans_type": "BUY",
                "amount": int(abs(qty)), "total_amt": round(abs(amt), 2),
                "date": date, "remarks": "配债认购",
            })
        elif code:
            txs.append({
                "ts_code": code, "name": name, "trans_type": biz,
                "amount": int(abs(qty)), "total_amt": round(abs(amt), 2),
                "date": date, "remarks": None,
            })
    normalize(txs, warns)
    return txs, warns


# ── 主测试逻辑 ───────────────────────────────────────────

DATA = "/Volumes/外挂硬盘/ClaudeCode-work/myinvestmentsystem/data"
FILES = [
    ("华林证券",  parse_hualin,  os.path.join(DATA, "华林证券.csv")),
    ("民生证券",  parse_minsheng, os.path.join(DATA, "账户资金股份变动流水_民生证券.csv")),
    ("平安证券",  parse_pingan,  os.path.join(DATA, "平安证券交易流水.csv")),
]

total_merged = 0
total_skipped = 0

for broker, parser, path in FILES:
    if not os.path.exists(path):
        print(f"\n[{broker}] 文件不存在: {path}")
        continue
    print(f"\n{'='*60}")
    print(f"  {broker}")
    print(f"{'='*60}")
    txs, warns = parser(path)

    # 归并记录
    merged = [t for t in txs if t.get("remarks") == "配债认购"]
    if merged:
        print(f"\n✓ 归并成功（{len(merged)} 条）：")
        for t in merged:
            print(f"   {t['date']}  {t['ts_code']}  {t['name']}  数量={t['amount']}  金额={t['total_amt']}"
                  f"  ← {t.get('_merged_from','?')}")
        total_merged += len(merged)

    # Warning（跳过记录）
    if warns:
        print(f"\n⚠️  归并失败/跳过（{len(warns)} 条）：")
        for w in warns:
            print(f"   {w}")
        total_skipped += len(warns)

    if not merged and not warns:
        print("  （无认购记录）")

print(f"\n{'='*60}")
print(f"汇总：归并成功 {total_merged} 条，跳过（需手动补录）{total_skipped} 条")
print(f"{'='*60}")
