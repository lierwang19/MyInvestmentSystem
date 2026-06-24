# -*- coding: utf-8 -*-
import sqlite3, os, pandas as pd
import argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="market.db")
    ap.add_argument("--codes", default="600653.SH,600604.SH,600606.SH,600608.SH")
    ap.add_argument("--token", default=os.getenv("TUSHARE_TOKEN",""))
    args = ap.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    conn = sqlite3.connect(args.db)

    # 取库内最早交易日
    rows = []
    for ts in codes:
        min_dt = conn.execute("SELECT MIN(trade_date) FROM daily_price WHERE ts_code=?", (ts,)).fetchone()[0]
        rows.append({"ts_code": ts, "min_db_date": min_dt})
    conn.close()

    # 取 TuShare 的 list_date
    try:
        import tushare as ts
        pro = ts.pro_api(args.token)
        df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,list_date')
        map_ld = dict(zip(df["ts_code"], df["list_date"]))
    except Exception as e:
        print(f"[WARN] 取 TuShare list_date 失败：{e}")
        map_ld = {}

    out = []
    for r in rows:
        ld = map_ld.get(r["ts_code"])
        out.append({"ts_code": r["ts_code"], "list_date": ld, "min_db_date": r["min_db_date"]})
    print(pd.DataFrame(out).to_string(index=False))

if __name__ == "__main__":
    main()
