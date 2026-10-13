# -*- coding: utf-8 -*-
# get_stock_list.py
"""
从 stock_industry_base.csv 读取股票清单，仅保留沪深 A 股（.SH/.SZ）。
可选输出随机抽样 ts_sample_XXX.csv，方便小批量试跑。
"""

import argparse
import pandas as pd
from pathlib import Path

BASE_FILE = "stock_industry_base.csv"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-file", default=BASE_FILE)
    ap.add_argument("--out-all", default="ts_all_shsz.csv")
    ap.add_argument("--sample", type=int, default=0, help=">0 时输出随机抽样 N 只到 ts_sample_N.csv")
    args = ap.parse_args()

    df = pd.read_csv(args.base_file, dtype={"ts_code": str})
    df = df[df["ts_code"].str.endswith((".SH", ".SZ"))].drop_duplicates("ts_code")

    df[["ts_code"]].to_csv(args.out_all, index=False, encoding="utf-8-sig")
    print(f"✅ 全量沪深清单：{args.out_all}（{len(df)} 条）")

    if args.sample and args.sample > 0:
        n = min(args.sample, len(df))
        df_s = df.sample(n=n, random_state=2025)[["ts_code"]].reset_index(drop=True)
        Path("data").mkdir(parents=True, exist_ok=True)
        out = f"data/ts_sample_{n}.csv"
        df_s.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"✅ 抽样清单：{out}（{n} 条）")

if __name__ == "__main__":
    main()
