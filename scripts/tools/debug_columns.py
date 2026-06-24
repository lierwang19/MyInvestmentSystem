import pandas as pd
import os

BASE = r"D:\MyInvestmentSystem\data\uploads"
MARKET = os.path.join(BASE, "market_20260123_085928.xlsx")
ACC_DIR = os.path.join(BASE, "holdings", "acc_dad3656a")

print("--- Market Columns ---")
try:
    df_m = pd.read_excel(MARKET, nrows=0)
    print(df_m.columns.tolist())
except Exception as e:
    print(e)

print("\n--- Holdings Columns ---")
try:
    if os.path.exists(ACC_DIR):
        files = os.listdir(ACC_DIR)
        if files:
            path = os.path.join(ACC_DIR, files[0])
            print(f"File: {files[0]}")
            df_h = pd.read_excel(path, nrows=0)
            print(df_h.columns.tolist())
        else:
            print("No files in acc dir")
    else:
        print("Acc dir not found")
except Exception as e:
    print(e)
