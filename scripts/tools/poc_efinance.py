# -*- coding: utf-8 -*-
"""
efinance 数据源可行性验证脚本 (PoC)

验证目标：
  1. A 股代码映射 — 000001.SZ / 600519.SH 能否稳定映射并取到行情
  2. 港股支持     — 00700.HK 等是否可以直接获取行情
  3. 字段对齐     — 返回字段能否对齐 quotes.py 的标准结构
  4. 连续轮询     — 30 秒间隔连续拉取 N 次，观察是否被限流或封禁

运行方式：
    cd /Volumes/外挂硬盘/ClaudeCode-work/myinvestmentsystem
    pip install efinance  # 如果尚未安装
    python scripts/tools/poc_efinance.py

    # 可选参数
    python scripts/tools/poc_efinance.py --rounds 5 --interval 30
"""

import sys
import time
import argparse
import traceback
from datetime import datetime

# ── 标准输出 schema（与 quotes.py 保持一致）
# {
#   'name': str,
#   'price': float,
#   'prev_close': float,
#   'open': float,
#   'high': float,
#   'low': float,
#   'volume': float,    # 手（100股）
#   'amount': float,    # 万元
#   'change_pct': float, # %
#   'change_amt': float,
#   'time': str,        # HH:MM:SS
# }

# ── 测试用股票代码（ts_code 格式）
TEST_A_CODES = [
    "000001.SZ",   # 平安银行
    "600519.SH",   # 贵州茅台
    "300750.SZ",   # 宁德时代
    "000651.SZ",   # 格力电器
    "601318.SH",   # 中国平安
]

TEST_HK_CODES = [
    "00700.HK",    # 腾讯控股
    "09988.HK",    # 阿里巴巴
    "01810.HK",    # 小米集团
]

REQUIRED_FIELDS = ['name', 'price', 'prev_close', 'open', 'high', 'low',
                   'change_pct', 'change_amt', 'time']


# ────────────────────────────────────────
# 辅助：代码格式转换
# ────────────────────────────────────────
def ts_to_efinance_a(ts_code: str) -> str:
    """600519.SH -> '600519'  |  000001.SZ -> '000001'"""
    return ts_code.split('.')[0]


def ts_to_efinance_hk(ts_code: str) -> str:
    """
    00700.HK -> 可能需要的格式，待 PoC 验证。
    efinance 港股通常用 'HK00700' 或直接 '00700' 配合 market 参数。
    本函数尝试两种格式，观察哪种能命中。
    """
    code = ts_code.split('.')[0]   # '00700'
    return code  # 先尝试裸号，若不行再试 'HK' + code


# ────────────────────────────────────────
# 字段归一化
# ────────────────────────────────────────
def normalize_row(row: dict, ts_code: str) -> dict | None:
    """
    将 efinance DataFrame 的一行转换为标准 schema。
    如果关键字段缺失或解析失败，返回 None。
    """
    try:
        price = float(row.get('最新价') or 0)
        if price <= 0:
            return None

        prev_close = float(row.get('昨收') or 0)
        open_price = float(row.get('今开') or 0)
        high = float(row.get('最高') or 0)
        low = float(row.get('最低') or 0)
        volume_raw = float(row.get('成交量') or 0)   # efinance 返回的单位可能是"手"或"股"，需验证
        amount_raw = float(row.get('成交额') or 0)   # 可能是元，需换算成万元
        change_pct = float(row.get('涨跌幅') or 0)
        change_amt = float(row.get('涨跌额') or 0)
        name = str(row.get('股票名称') or row.get('名称') or '')

        # volume: 如果单位是股，转为手（÷100）；如果已经是手，直接用
        # efinance 官方文档说成交量单位是"手"，但实测可能是"股"，PoC 需要对比
        volume = volume_raw  # 先保留原始值，PoC 输出时注明

        # amount: efinance 成交额单位是元，需换算为万元
        amount = amount_raw / 10000 if amount_raw > 10000 else amount_raw

        return {
            'name': name,
            'price': price,
            'prev_close': prev_close,
            'open': open_price,
            'high': high,
            'low': low,
            'volume': volume,
            'amount': amount,
            'change_pct': round(change_pct, 2),
            'change_amt': round(change_amt, 3),
            'time': datetime.now().strftime('%H:%M:%S'),
            '_raw_volume': volume_raw,  # 调试用：保留原始量
            '_raw_amount': amount_raw,  # 调试用：保留原始额
        }
    except (ValueError, TypeError, KeyError) as e:
        print(f"    [WARN] normalize failed for {ts_code}: {e}")
        return None


# ────────────────────────────────────────
# 检查 1: A 股代码映射 + 字段
# ────────────────────────────────────────
def check_a_shares(ef) -> dict:
    """验证 A 股行情拉取和字段归一化"""
    print("\n=== 检查 1: A 股行情 ===")
    codes = [ts_to_efinance_a(c) for c in TEST_A_CODES]
    print(f"  请求代码: {codes}")

    try:
        t0 = time.time()
        df = ef.stock.get_realtime_quotes(codes)
        elapsed = time.time() - t0
        print(f"  返回行数: {len(df)}, 耗时: {elapsed:.2f}s")
        print(f"  返回列名: {list(df.columns)}")

        results = {}
        for _, row in df.iterrows():
            code_raw = str(row.get('股票代码', ''))
            # 重建 ts_code
            exchange = 'SH' if code_raw.startswith('6') else 'SZ'
            ts_code = f"{code_raw}.{exchange}"
            normalized = normalize_row(row.to_dict(), ts_code)
            if normalized:
                results[ts_code] = normalized
                print(f"  ✅ {ts_code} {normalized['name']}: "
                      f"price={normalized['price']}, "
                      f"chg={normalized['change_pct']}%, "
                      f"vol_raw={normalized['_raw_volume']}")
            else:
                print(f"  ❌ {ts_code}: normalize 失败")

        # 检查字段完整性
        if results:
            sample = next(iter(results.values()))
            missing = [f for f in REQUIRED_FIELDS if f not in sample]
            if missing:
                print(f"  ⚠️  缺少字段: {missing}")
            else:
                print(f"  ✅ 所有必需字段均存在")

        return {'ok': True, 'count': len(results), 'elapsed': elapsed}

    except Exception as e:
        print(f"  ❌ A 股拉取失败: {e}")
        traceback.print_exc()
        return {'ok': False, 'error': str(e)}


# ────────────────────────────────────────
# 检查 2: 港股支持
# ────────────────────────────────────────
def check_hk_shares(ef) -> dict:
    """验证港股代码是否能通过 efinance 获取行情"""
    print("\n=== 检查 2: 港股行情 ===")

    # 尝试方式 1：裸号
    codes_bare = [ts_to_efinance_hk(c) for c in TEST_HK_CODES]
    # 尝试方式 2：加 HK 前缀
    codes_hk = ['HK' + ts_to_efinance_hk(c) for c in TEST_HK_CODES]

    for attempt_name, codes in [('裸号', codes_bare), ('HK 前缀', codes_hk)]:
        print(f"\n  尝试格式 [{attempt_name}]: {codes}")
        try:
            t0 = time.time()
            df = ef.stock.get_realtime_quotes(codes)
            elapsed = time.time() - t0
            print(f"  返回行数: {len(df)}, 耗时: {elapsed:.2f}s")

            if len(df) > 0:
                print(f"  返回列名: {list(df.columns)}")
                for _, row in df.iterrows():
                    code_raw = str(row.get('股票代码', ''))
                    price = row.get('最新价', 'N/A')
                    name = row.get('股票名称', '未知')
                    print(f"  ✅ 港股 {code_raw} {name}: price={price}")
                return {'ok': True, 'format': attempt_name, 'count': len(df), 'elapsed': elapsed}
            else:
                print(f"  ⚠️  返回空数据")
        except Exception as e:
            print(f"  ❌ 港股 [{attempt_name}] 失败: {e}")

    # 也尝试 ef.stock.get_realtime_quotes 的其他参数形式
    print("\n  尝试单独调用每个港股代码...")
    for ts_code in TEST_HK_CODES:
        for code_fmt in [ts_code.split('.')[0], 'HK' + ts_code.split('.')[0]]:
            try:
                df = ef.stock.get_realtime_quotes([code_fmt])
                if len(df) > 0 and float(df.iloc[0].get('最新价', 0) or 0) > 0:
                    print(f"  ✅ {ts_code} 使用 '{code_fmt}' 成功: {df.iloc[0].get('最新价')}")
                    break
            except Exception:
                pass

    return {'ok': False, 'error': '所有格式均失败或返回空'}


# ────────────────────────────────────────
# 检查 3: 连续轮询限流测试
# ────────────────────────────────────────
def check_polling(ef, rounds: int = 5, interval: int = 30) -> dict:
    """连续轮询 N 轮，检查是否被限流"""
    print(f"\n=== 检查 3: 连续轮询 ({rounds} 轮, 间隔 {interval}s) ===")

    codes = [ts_to_efinance_a(c) for c in TEST_A_CODES[:3]]  # 只用 3 只股票测试
    success_count = 0
    fail_count = 0
    latencies = []

    for i in range(1, rounds + 1):
        try:
            t0 = time.time()
            df = ef.stock.get_realtime_quotes(codes)
            elapsed = time.time() - t0
            latencies.append(elapsed)

            if len(df) > 0:
                price_sample = df.iloc[0].get('最新价', 'N/A')
                print(f"  轮次 {i}/{rounds}: ✅ 返回 {len(df)} 行, "
                      f"price_sample={price_sample}, 耗时={elapsed:.2f}s")
                success_count += 1
            else:
                print(f"  轮次 {i}/{rounds}: ⚠️  返回空数据, 耗时={elapsed:.2f}s")
                fail_count += 1

        except Exception as e:
            print(f"  轮次 {i}/{rounds}: ❌ 失败: {e}")
            fail_count += 1

        if i < rounds:
            print(f"  等待 {interval}s...")
            time.sleep(interval)

    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    print(f"\n  轮询结果: 成功 {success_count}/{rounds}, 失败 {fail_count}/{rounds}")
    print(f"  平均耗时: {avg_latency:.2f}s")

    return {
        'ok': fail_count == 0,
        'success': success_count,
        'fail': fail_count,
        'rounds': rounds,
        'avg_latency': avg_latency,
    }


# ────────────────────────────────────────
# 主程序
# ────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='efinance PoC 验证脚本')
    parser.add_argument('--rounds', type=int, default=3, help='轮询轮数（默认 3）')
    parser.add_argument('--interval', type=int, default=30, help='轮询间隔秒数（默认 30）')
    parser.add_argument('--skip-polling', action='store_true', help='跳过轮询测试')
    args = parser.parse_args()

    print("=" * 60)
    print("efinance PoC 验证脚本")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 检查 efinance 是否已安装
    try:
        import efinance as ef
        print(f"\n✅ efinance 已安装")
        # 尝试获取版本号
        try:
            import importlib.metadata
            ver = importlib.metadata.version('efinance')
            print(f"   版本: {ver}")
        except Exception:
            pass
    except ImportError:
        print("\n❌ efinance 未安装。请先运行：")
        print("   pip install efinance")
        sys.exit(1)

    results = {}

    # 检查 1: A 股
    results['a_shares'] = check_a_shares(ef)

    # 检查 2: 港股
    results['hk_shares'] = check_hk_shares(ef)

    # 检查 3: 连续轮询（可跳过）
    if not args.skip_polling:
        results['polling'] = check_polling(ef, rounds=args.rounds, interval=args.interval)
    else:
        print("\n=== 检查 3: 跳过轮询测试 ===")

    # ── 汇总结论
    print("\n" + "=" * 60)
    print("验证结论")
    print("=" * 60)

    a_ok = results['a_shares'].get('ok', False)
    hk_ok = results['hk_shares'].get('ok', False)
    poll_ok = results.get('polling', {}).get('ok', True) if not args.skip_polling else None

    print(f"\n检查 1 A 股行情: {'✅ 通过' if a_ok else '❌ 失败'}")
    if a_ok:
        print(f"  命中 {results['a_shares']['count']} 只，耗时 {results['a_shares']['elapsed']:.2f}s")
    else:
        print(f"  错误: {results['a_shares'].get('error', '未知')}")

    print(f"\n检查 2 港股行情: {'✅ 通过' if hk_ok else '⚠️  不支持或失败'}")
    if hk_ok:
        print(f"  命中格式: {results['hk_shares']['format']}")
    else:
        print(f"  {results['hk_shares'].get('error', '未知')}")
        print("  → 港股仍需依赖腾讯 HK 接口")

    if poll_ok is not None:
        print(f"\n检查 3 连续轮询: {'✅ 无限流' if poll_ok else '⚠️  出现失败'}")
        p = results.get('polling', {})
        print(f"  成功率: {p.get('success', 0)}/{p.get('rounds', 0)}, "
              f"平均耗时: {p.get('avg_latency', 0):.2f}s")

    print("\n建议后续操作:")
    if a_ok:
        print("  ✅ efinance 可以作为 A 股行情第三数据源（P0 优先级）")
        print("  → 可接入 quote_manager 作为优先数据源")
    else:
        print("  ❌ efinance A 股不可用，暂不接入")

    if hk_ok:
        print("  ✅ efinance 支持港股，可替换腾讯 HK 接口")
    else:
        print("  → 港股继续使用腾讯 HK 接口")

    print("\n注意字段单位问题:")
    print("  - 成交量：efinance 返回值是否已经是 '手'，需对比腾讯/新浪数据确认")
    print("  - 成交额：efinance 返回元，已在 normalize 中换算为万元，需人工验证")

    print("\n如需在 Docker 环境测试限流，请运行：")
    print("  docker exec -it <backend_container> python scripts/tools/poc_efinance.py --rounds 10 --interval 30")


if __name__ == '__main__':
    main()
