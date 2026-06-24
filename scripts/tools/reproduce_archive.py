
import sqlite3
import requests
import os
import sys

# Setup DB
# Setup DB
    # The actual DB is d:/MyInvestmentSystem/user_data.db (from project root)
    # But checking database.py it seems it might be in web/backend? 
    # database.py says: PROJECT_ROOT = ... (3 levels up from backend/database.py)
    # web/backend -> web -> MyInvestmentSystem. 
    # So user_data.db is in MyInvestmentSystem root.
    
DB_PATH = 'd:/MyInvestmentSystem/user_data.db' 

def get_db():
    return sqlite3.connect(DB_PATH)

def run_test():
    conn = get_db()
    c = conn.cursor()
    
    # Ensure archived_holdings table exists (since migrate.py failed)
    c.execute('''
    CREATE TABLE IF NOT EXISTS archived_holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        stock_name TEXT,
        total_buy_amount REAL DEFAULT 0, 
        total_sell_amount REAL DEFAULT 0, 
        total_dividend REAL DEFAULT 0, 
        total_profit REAL DEFAULT 0, 
        profit_pct REAL DEFAULT 0, 
        total_fees REAL DEFAULT 0, 
        first_buy_date TEXT, 
        last_sell_date TEXT, 
        archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # 1. Test Case A: Holding exists (Qty=0) + Transactions Exist
    acc_id_a = "TEST_ARCHIVE_A"
    ts_code_a = "000001.SZ"
    
    # Cleanup
    c.execute("DELETE FROM accounts WHERE id = ?", (acc_id_a,))
    c.execute("DELETE FROM transactions WHERE account_id = ?", (acc_id_a,))
    c.execute("DELETE FROM holdings WHERE account_id = ?", (acc_id_a,))
    c.execute("DELETE FROM archived_holdings WHERE account_id = ?", (acc_id_a,))
    
    # Setup
    c.execute("INSERT INTO accounts (id, name) VALUES (?, ?)", (acc_id_a, "Test A"))
    # Transaction: Buy
    c.execute("INSERT INTO transactions (account_id, ts_code, trans_type, price, amount, total_amt, fee, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
              (acc_id_a, ts_code_a, "BUY", 10.0, 100, 1000.0, 5.0, "2023-01-01"))
    # Transaction: Sell (Clear)
    c.execute("INSERT INTO transactions (account_id, ts_code, trans_type, price, amount, total_amt, fee, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
              (acc_id_a, ts_code_a, "SELL", 12.0, -100, 1200.0, 5.0, "2023-01-02"))
    
    # Holding (Cleared) - Profit 200 - 10 = 190. Total Cost (Diluted) = 1000+5 - (1200-5) = 1005 - 1195 = -190.
    c.execute("INSERT INTO holdings (account_id, ts_code, quantity, total_cost, cost_price) VALUES (?, ?, ?, ?, ?)",
              (acc_id_a, ts_code_a, 0, -190.0, 0))
    
    conn.commit()
    
    # 2. Test Case B: Holding exists (Qty=0) + NO Transactions (Simulate Import)
    acc_id_b = "TEST_ARCHIVE_B"
    ts_code_b = "000002.SZ"
    
    # Cleanup
    c.execute("DELETE FROM accounts WHERE id = ?", (acc_id_b,))
    c.execute("DELETE FROM transactions WHERE account_id = ?", (acc_id_b,))
    c.execute("DELETE FROM holdings WHERE account_id = ?", (acc_id_b,))
    c.execute("DELETE FROM archived_holdings WHERE account_id = ?", (acc_id_b,))
    
    # Setup
    c.execute("INSERT INTO accounts (id, name) VALUES (?, ?)", (acc_id_b, "Test B"))
    # Holding Only
    c.execute("INSERT INTO holdings (account_id, ts_code, quantity, total_cost, cost_price) VALUES (?, ?, ?, ?, ?)",
              (acc_id_b, ts_code_b, 0, -500.0, 0)) # Simulating 500 profit
    
    conn.commit()
    conn.close()
    
    # 3. Call API to Archive
    import json
    
    print("\n--- Testing Case A (Has Transactions) ---")
    try:
        url = f"http://127.0.0.1:8000/api/archived/{acc_id_a}"
        payload = {"ts_code": ts_code_a, "stock_name": "Test Stock A"}
        res = requests.post(url, json=payload)
        print(f"Status: {res.status_code}")
        print(f"Response: {res.text}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n--- Testing Case B (No Transactions) ---")
    try:
        url = f"http://127.0.0.1:8000/api/archived/{acc_id_b}"
        payload = {"ts_code": ts_code_b, "stock_name": "Test Stock B"}
        res = requests.post(url, json=payload)
        print(f"Status: {res.status_code}")
        print(f"Response: {res.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_test()
