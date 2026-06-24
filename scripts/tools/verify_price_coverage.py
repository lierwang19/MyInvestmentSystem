# verify_price_coverage.py
# -*- coding: utf-8 -*-
import os, sqlite3, argparse
from datetime import datetime, date
import pandas as pd

def get_trade_calendar():
    import akshare as ak
    cal = ak.tool_trade_date_hist_sina()
    # 统一一列为 dt，并只要 <= 今日
    if "trade_date" in cal.columns: cal["dt"] = pd.to_datetime(cal["trade_date"])
    elif "日期" in cal.columns:     cal["dt"] = pd.to_datetime(cal["日期"])
    else:                           cal["dt"] = pd.to_datetime(cal.iloc[:,0])
    cal = cal[cal["dt"] <= pd.to_datetime(date.today())].copy()
    cal["d"] = cal["dt"].dt.strftime("%Y-%m-%d")
    return cal["d"].tolist(), cal["dt"].max().strftime("%Y-%m-%d")

def load_min_max_from_db(db):
    conn = sqlite3.connect(db)
    df = pd.read_sql_query("""
        SELECT ts_code,
               MIN(trade_date) AS first_dt,
               MAX(trade_date) AS last_dt,
               COUNT(*)        AS rows_cnt
        FROM daily_price
        GROUP BY ts_code
    """, conn)
    conn.close()
    return df

def get_list_date_from_tushare():
    import tushare as ts
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise SystemExit("未检测到环境变量 TUSHARE_TOKEN，请先设置。")
    ts.set_token(token)
    pro = ts.pro_api()
    # 仅沪深主板/创业板/科创板，排除北交所；list_status L=上市
    df = pro.stock_basic(
        exchange='', list_status='L',
        fields='ts_code,name,market,exchange,list_date'
    )
    # 过滤北交所（BSE）与 BJ 代码
    df = df[~df['exchange'].str.upper().str.contains('BSE', na=False)]
    df = df[~df['ts_code'].str.endswith('.BJ')]
    df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return df[["ts_code","name","list_date"]]

def trading_days_between(trade_days, start, end):
    """返回[start,end]区间内的交易日数量；start/end 为 'YYYY-MM-DD'"""
    if pd.isna(start) or pd.isna(end): return None
    if start > end: start, end = end, start
    s = pd.to_datetime(start); e = pd.to_datetime(end)
    # trade_days 是字符串列表
    td = pd.Series(trade_days)
    mask = (pd.to_datetime(td) >= s) & (pd.to_datetime(td) <= e)
    return int(mask.sum())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="market.db")
    ap.add_argument("--allow-start-gap", type=int, default=5,
                    help="允许首日相对 IPO （上市后第一个交易日）延后多少个交易日，默认 5")
    ap.add_argument("--out", default="coverage_report.csv")
    args = ap.parse_args()

    trade_days, last_trade_day = get_trade_calendar()
    df_db = load_min_max_from_db(args.db)
    df_list = get_list_date_from_tushare()

    # 合并
    df = df_list.merge(df_db, on="ts_code", how="left")

    # 计算IPO之后的第一个交易日（若IPO是非交易日就顺延）
    ipo_trade_start = []
    for _, r in df.iterrows():
        ld = r.get("list_date")
        if pd.isna(ld):
            ipo_trade_start.append(None)
            continue
        d = pd.to_datetime(ld).strftime("%Y-%m-%d")
        # 找到 >= d 的第一个交易日
        cand = next((x for x in trade_days if x >= d), None)
        ipo_trade_start.append(cand)
    df["ipo_trade_start"] = ipo_trade_start

    # 缺口计算
    gaps = []
    end_gaps = []
    for _, r in df.iterrows():
        start_dt = r.get("first_dt")
        ipo0 = r.get("ipo_trade_start")
        last_dt = r.get("last_dt")

        # start gap（从IPO第一个交易日起，DB首日还差多少个交易日；负数/None 表示无法评估或领先）
        if pd.notna(start_dt) and pd.notna(ipo0):
            gap = trading_days_between(trade_days, ipo0, start_dt) - 1  # 同一天算0
            gap = max(gap, 0)
        else:
            gap = None
        gaps.append(gap)

        # end gap（距离最近交易日还差几天）
        if pd.notna(last_dt):
            eg = trading_days_between(trade_days, last_dt, last_trade_day) - 1
            eg = max(eg, 0)
        else:
            eg = None
        end_gaps.append(eg)

    df["start_gap_td"] = gaps
    df["end_gap_td"]   = end_gaps

    # 评估通过与否
    df["start_ok"] = df["start_gap_td"].apply(lambda x: (x is not None) and (x <= args.allow_start_gap))
    df["end_ok"]   = df["end_gap_td"].apply(lambda x: (x is not None) and (x == 0))

    df = df[[
        "ts_code","name","list_date","ipo_trade_start",
        "first_dt","last_dt","rows_cnt","start_gap_td","end_gap_td","start_ok","end_ok"
    ]].sort_values(["start_ok","end_ok","start_gap_td","end_gap_td", "ts_code"])

    df.to_csv(args.out, index=False, encoding="utf-8-sig")

    # 汇总打印
    n_all = len(df)
    n_have_rows = int(df["rows_cnt"].fillna(0).gt(0).sum())
    n_start_ok  = int(df["start_ok"].fillna(False).sum())
    n_end_ok    = int(df["end_ok"].fillna(False).sum())
    print(f"覆盖股票数：{n_have_rows}/{n_all}")
    print(f"首日对齐（<= {args.allow_start_gap} 个交易日缺口）：{n_start_ok}/{n_all}")
    print(f"已更新到最近交易日（{last_trade_day}）：{n_end_ok}/{n_all}")
    print(f"详情见：{args.out}")

if __name__ == "__main__":
    main()
