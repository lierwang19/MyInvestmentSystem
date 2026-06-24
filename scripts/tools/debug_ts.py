
import tushare as ts
import os
import pandas as pd

token = os.environ.get("TUSHARE_TOKEN")
if not token:
    raise SystemExit("未检测到环境变量 TUSHARE_TOKEN，请先设置。")
ts.set_token(token)
pro = ts.pro_api()

print("Querying Tushare for 002772.SZ on 20260126...")
df = pro.daily_basic(ts_code='002772.SZ', trade_date='20260126', fields='ts_code,trade_date,close,pb,pe_ttm')
print("Result:")
print(df)

print("\nQuerying Tushare for 002772.SZ on 20260127 (Today)...")
df2 = pro.daily_basic(ts_code='002772.SZ', trade_date='20260127', fields='ts_code,trade_date,close,pb,pe_ttm')
print("Result:")
print(df2)
