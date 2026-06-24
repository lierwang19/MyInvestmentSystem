"""
Isolated regression test for _bond_name_root and convertible bond normalization logic.
Does NOT import the full web backend module, tests the pure functions only.
"""
import re
import sys


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
        return any(code6.startswith(p) for p in ("110", "113"))
    if market == "SZ":
        return any(code6.startswith(p) for p in ("123", "127", "128"))
    return False


def _normalize(transactions, warnings_out):
    formal_map = {}
    for tx in transactions:
        if not _is_formal_bond_code(tx["ts_code"]):
            continue
        root = _bond_name_root(tx["name"])
        if root not in formal_map:
            formal_map[root] = (tx["ts_code"], tx["name"])
    for tx in transactions:
        if tx.get("remarks") != "配债认购":
            continue
        root = _bond_name_root(tx["name"])
        if root in formal_map:
            tx["ts_code"], tx["name"] = formal_map[root]
        else:
            warnings_out.append(f"unmapped: {tx['name']}({tx['ts_code']})")


def test_bond_name_root():
    cases = [
        ("柳工配债", "柳工"),
        ("柳工转2", "柳工"),
        ("东财发债", "东财"),
        ("东财转3", "东财"),
        ("华安转债01", "华安"),
        ("兴业转债", "兴业"),
        ("兴业发债", "兴业"),
        ("福能配债", "福能"),
        ("福能转债", "福能"),
        ("珀莱发债", "珀莱"),
        ("珀莱转债", "珀莱"),
    ]
    for name, expected in cases:
        got = _bond_name_root(name)
        assert got == expected, f"_bond_name_root({name!r}) = {got!r}, expected {expected!r}"
    print("✓ _bond_name_root: all name patterns correct")


def test_normalize_minsheng_chain():
    """民生：080528.SZ 柳工配债 → 127084.SZ 柳工转2"""
    txs = [
        {"ts_code": "080528.SZ", "name": "柳工配债", "trans_type": "BUY", "remarks": "配债认购"},
        {"ts_code": "127084.SZ", "name": "柳工转2", "trans_type": "SELL", "remarks": None},
    ]
    warns = []
    _normalize(txs, warns)
    assert txs[0]["ts_code"] == "127084.SZ", f"Expected 127084.SZ, got {txs[0]['ts_code']}"
    assert warns == [], f"Unexpected warnings: {warns}"
    print("✓ minsheng: 080528.SZ 柳工配债 → 127084.SZ 柳工转2")


def test_normalize_pingan_chain():
    """平安：370059.SZ 东财发债 → 123111.SZ 东财转3"""
    txs = [
        {"ts_code": "370059.SZ", "name": "东财发债", "trans_type": "BUY", "remarks": "配债认购"},
        {"ts_code": "123111.SZ", "name": "东财转3", "trans_type": "SELL", "remarks": None},
    ]
    warns = []
    _normalize(txs, warns)
    assert txs[0]["ts_code"] == "123111.SZ", f"Expected 123111.SZ, got {txs[0]['ts_code']}"
    assert warns == [], f"Unexpected warnings: {warns}"
    print("✓ pingan: 370059.SZ 东财发债 → 123111.SZ 东财转3")


def test_normalize_pingan_zhuanzi_style():
    """平安：783609.SZ 金铜发债 → (no formal code in same file, should warn not crash)"""
    txs = [
        {"ts_code": "783609.SZ", "name": "金铜发债", "trans_type": "BUY", "remarks": "配债认购"},
    ]
    warns = []
    _normalize(txs, warns)
    # No formal code present → warning generated, ts_code unchanged
    assert txs[0]["ts_code"] == "783609.SZ"
    assert len(warns) == 1
    print("✓ pingan: 783609.SZ 金铜发债 → unmapped warning (no formal code in file)")


def test_normalize_cross_market_sh_sub_sz_formal():
    """华林：072284.SH 亚太发债 → 128023.SZ 亚太转债（SH认购代码 vs SZ正式代码）"""
    txs = [
        {"ts_code": "072284.SH", "name": "亚太发债", "trans_type": "BUY", "remarks": "配债认购"},
        {"ts_code": "128023.SZ", "name": "亚太转债", "trans_type": "SELL", "remarks": None},
    ]
    warns = []
    _normalize(txs, warns)
    assert txs[0]["ts_code"] == "128023.SZ", f"Expected 128023.SZ, got {txs[0]['ts_code']}"
    assert warns == [], f"Unexpected warnings: {warns}"
    print("✓ hualin cross-market: 072284.SH 亚太发债 → 128023.SZ 亚太转债")


def test_normalize_cross_market_转n():
    """华林：070932.SH 华菱发债 → 127023.SZ 华菱转2（跨市场 + 转N后缀）"""
    txs = [
        {"ts_code": "070932.SH", "name": "华菱发债", "trans_type": "BUY", "remarks": "配债认购"},
        {"ts_code": "127023.SZ", "name": "华菱转2", "trans_type": "SELL", "remarks": None},
    ]
    warns = []
    _normalize(txs, warns)
    assert txs[0]["ts_code"] == "127023.SZ", f"Expected 127023.SZ, got {txs[0]['ts_code']}"
    assert warns == [], f"Unexpected warnings: {warns}"
    print("✓ hualin cross-market 转N: 070932.SH 华菱发债 → 127023.SZ 华菱转2")


if __name__ == "__main__":
    test_bond_name_root()
    test_normalize_minsheng_chain()
    test_normalize_pingan_chain()
    test_normalize_pingan_zhuanzi_style()
    test_normalize_cross_market_sh_sub_sz_formal()
    test_normalize_cross_market_转n()
    print("\n✅ All convertible bond normalization tests passed!")
