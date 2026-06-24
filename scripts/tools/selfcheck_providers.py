# -*- coding: utf-8 -*-
"""
selfcheck_providers.py
检测 AkShare 各历史/快照数据源连通性：
- 新浪  stock_zh_a_daily
- 东财  stock_zh_a_hist / stock_zh_a_spot_em
- 腾讯  stock_zh_a_hist_tx
- (可选) baostock 历史日线兜底
输出：终端打印 + logs/selfcheck_*.txt
"""

from __future__ import annotations
import os, time, traceback, platform
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

LOG_DIR = Path("./logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
STAMP = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = LOG_DIR / f"selfcheck_{STAMP}.txt"

# 用相对稳定、流动性充足的标的做测试
TEST_SYMBOLS = [
    ("sh600000", "浦发银行"),   # 沪 A
    ("sz000001", "平安银行"),   # 深 A
]

START = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
END   = date.today().strftime("%Y%m%d")

def log(msg: str):
    print(msg, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def short_err(e: Exception, maxlen: int = 400) -> str:
    s = f"{type(e).__name__}: {e}"
    if len(s) > maxlen: s = s[:maxlen] + " ..."
    return s

def header():
    log("="*80)
    log("AkShare 数据源连通性自检")
    log(f"时间：{pd.Timestamp.now()}")
    log(f"系统：{platform.platform()}")
    log(f"Python：{platform.python_version()}")
    try:
        import akshare as ak
        log(f"AkShare：{ak.__version__}")
    except Exception as e:
        log(f"AkShare 导入失败：{short_err(e)}")
    log("-"*80)
    # 打印代理环境变量
    for k in ["HTTP_PROXY","http_proxy","HTTPS_PROXY","https_proxy","ALL_PROXY","all_proxy","NO_PROXY","no_proxy"]:
        log(f"{k} = {os.environ.get(k)}")
    log("="*80)

def check_sina(symbol: str):
    import akshare as ak
    name = f"SINA.daily({symbol})"
    t0 = time.time()
    try:
        df = ak.stock_zh_a_daily(symbol=symbol, start_date=START, end_date=END, adjust="qfq")
        ok = df is not None and not df.empty
        rows = 0 if df is None else len(df)
        cost = time.time() - t0
        log(f"[OK] {name} rows={rows} time={cost:.2f}s") if ok else log(f"[FAIL(empty)] {name} time={cost:.2f}s")
        return ok, cost, rows, ""
    except Exception as e:
        cost = time.time() - t0
        log(f"[FAIL] {name} time={cost:.2f}s err={short_err(e)}")
        return False, cost, 0, short_err(e)

def check_em_hist(symbol: str):
    import akshare as ak
    name = f"EM.hist({symbol})"
    t0 = time.time()
    try:
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=START, end_date=END, adjust="qfq")
        ok = df is not None and not df.empty
        rows = 0 if df is None else len(df)
        cost = time.time() - t0
        log(f"[OK] {name} rows={rows} time={cost:.2f}s") if ok else log(f"[FAIL(empty)] {name} time={cost:.2f}s")
        return ok, cost, rows, ""
    except Exception as e:
        cost = time.time() - t0
        log(f"[FAIL] {name} time={cost:.2f}s err={short_err(e)}")
        return False, cost, 0, short_err(e)

def check_em_spot():
    import akshare as ak
    name = "EM.spot(all)"
    t0 = time.time()
    try:
        df = ak.stock_zh_a_spot_em()
        ok = df is not None and not df.empty
        rows = 0 if df is None else len(df)
        cost = time.time() - t0
        log(f"[OK] {name} rows={rows} time={cost:.2f}s") if ok else log(f"[FAIL(empty)] {name} time={cost:.2f}s")
        return ok, cost, rows, ""
    except Exception as e:
        cost = time.time() - t0
        log(f"[FAIL] {name} time={cost:.2f}s err={short_err(e)}")
        return False, cost, 0, short_err(e)

def check_tx(symbol: str):
    import akshare as ak
    name = f"TX.hist({symbol})"
    t0 = time.time()
    try:
        df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=START, end_date=END, adjust="qfq")
        ok = df is not None and not df.empty
        rows = 0 if df is None else len(df)
        cost = time.time() - t0
        log(f"[OK] {name} rows={rows} time={cost:.2f}s") if ok else log(f"[FAIL(empty)] {name} time={cost:.2f}s")
        return ok, cost, rows, ""
    except Exception as e:
        cost = time.time() - t0
        log(f"[FAIL] {name} time={cost:.2f}s err={short_err(e)}")
        return False, cost, 0, short_err(e)

def check_baostock(symbol: str):
    # 可选兜底；未安装会直接跳过
    name = f"BAOSTOCK.hist({symbol})"
    t0 = time.time()
    try:
        import baostock as bs  # type: ignore
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"login error: {lg.error_code} {lg.error_msg}")
        market = symbol[:2]  # sh/sz
        code6 = symbol[2:]
        code = f"{market}.{code6}"
        rs = bs.query_history_k_data_plus(
            code,
            "date,open,high,low,close,volume,amount",
            start_date=pd.to_datetime(START).strftime("%Y-%m-%d"),
            end_date=pd.to_datetime(END).strftime("%Y-%m-%d"),
            frequency="d",
            adjustflag="2"  # 2=前复权
        )
        data = []
        while rs.error_code == "0" and rs.next():
            data.append(rs.get_row_data())
        bs.logout()
        rows = len(data)
        ok = rows > 0
        cost = time.time() - t0
        log(f"[OK] {name} rows={rows} time={cost:.2f}s") if ok else log(f"[FAIL(empty)] {name} time={cost:.2f}s")
        return ok, cost, rows, ""
    except ModuleNotFoundError:
        cost = time.time() - t0
        log(f"[SKIP] {name} 未安装 baostock（可 pip install baostock）")
        return None, cost, 0, "baostock not installed"
    except Exception as e:
        cost = time.time() - t0
        log(f"[FAIL] {name} time={cost:.2f}s err={short_err(e)}")
        return False, cost, 0, short_err(e)

def main():
    header()
    results = []

    for sym, cname in TEST_SYMBOLS:
        log(f"\n--- 测试标的：{sym} ({cname}) 最近30天 ---")
        results.append(("sina", sym, *check_sina(sym)))
        results.append(("eastmoney_hist", sym, *check_em_hist(sym)))
        results.append(("tencent_hist", sym, *check_tx(sym)))
        # baostock 可选
        results.append(("baostock", sym, *check_baostock(sym)))

    log("\n--- 测试东财快照（全市场）---")
    results.append(("eastmoney_spot", "all", *check_em_spot()))

    # 汇总
    log("\n===== 汇总 =====")
    ok_cnt = 0; fail_cnt = 0
    for prov, sym, ok, cost, rows, err in results:
        if ok is True:
            ok_cnt += 1
            log(f"[OK]   {prov:16s} sym={sym:9s} rows={rows:5d} time={cost:.2f}s")
        elif ok is False:
            fail_cnt += 1
            log(f"[FAIL] {prov:16s} sym={sym:9s} time={cost:.2f}s err={err}")
        else:
            # None -> 跳过
            log(f"[SKIP] {prov:16s} sym={sym:9s} note={err}")

    log(f"\n合计：成功 {ok_cnt}，失败 {fail_cnt}，日志：{LOG_FILE}")
    log("="*80)

if __name__ == "__main__":
    main()
