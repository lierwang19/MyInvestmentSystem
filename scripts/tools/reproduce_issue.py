
import sqlite3
import requests
import os
import sys

# Setup DB
DB_PATH = 'd:/MyInvestmentSystem/web/backend/data/investment.db'
if not os.path.exists(os.path.dirname(DB_PATH)):
    os.makedirs(os.path.dirname(DB_PATH))

def get_db():
    return sqlite3.connect(DB_PATH)

def setup_db():
    conn = get_db()
    c = conn.cursor()
    # Create tables if not exist (simplified version of migrate.py)
    c.execute('''
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            name TEXT,
            broker_id INTEGER,
            cash_balance REAL DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT,
            ts_code TEXT,
            name TEXT,
            trans_type TEXT,
            price REAL,
            amount INTEGER,
            total_amt REAL,
            fee REAL,
            stamp_tax REAL,
            date TEXT,
            remarks TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT,
            ts_code TEXT,
            name TEXT,
            quantity INTEGER,
            cost_price REAL,
            total_cost REAL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn

def run_test():
    conn = setup_db()
    c = conn.cursor()
    
    # 1. Create Test Account
    acc_id = "TEST_ACC_001"
    c.execute("DELETE FROM accounts WHERE id = ?", (acc_id,))
    c.execute("DELETE FROM transactions WHERE account_id = ?", (acc_id,))
    c.execute("DELETE FROM holdings WHERE account_id = ?", (acc_id,))
    c.execute("INSERT INTO accounts (id, name, cash_balance) VALUES (?, ?, ?)", (acc_id, "Test Account", 100000))
    conn.commit()
    
    print(f"Created account {acc_id}")
    
    # 2. Buy 1000 shares @ 10.0
    # Use API-like logic (direct DB insert to simulate backend logic)
    print("\n--- ACTION: Buying 1000 @ 10.0 ---")
    
    trans_buy = {
        "ts_code": "000001.SZ",
        "name": "PingAn",
        "trans_type": "BUY",
        "price": 10.0,
        "amount": 1000,
        "date": "2023-01-01"
    }
    
    # Logic from transactions.py (BUY)
    total_amt = trans_buy["price"] * trans_buy["amount"]
    fee = 5.0 # Min comm
    buy_cost = total_amt + fee
    
    c.execute('''
        INSERT INTO transactions (account_id, ts_code, name, trans_type, price, amount, total_amt, fee, stamp_tax, date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (acc_id, trans_buy["ts_code"], trans_buy["name"], "BUY", trans_buy["price"], trans_buy["amount"], total_amt, fee, 0, trans_buy["date"]))
    
    # Update Holdings (BUY)
    current_qty = 0
    current_total_cost = 0
    
    new_qty = current_qty + trans_buy["amount"]
    new_total_cost = current_total_cost + buy_cost
    new_cost_price = new_total_cost / new_qty
    
    c.execute('''
        INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (acc_id, trans_buy["ts_code"], trans_buy["name"], new_qty, new_cost_price, new_total_cost))
    conn.commit()
    
    print_holding(c, acc_id)
    
    # 3. Sell 500 shares @ 20.0 (High profit)
    print("\n--- ACTION: Selling 500 @ 20.0 ---")
    trans_sell = {
        "trans_type": "SELL",
        "price": 20.0,
        "amount": -500
    }
    
    # Logic from transactions.py (SELL - CURRENT)
    sell_qty = abs(trans_sell["amount"])
    
    # Fetch existing
    c.execute("SELECT * FROM holdings WHERE account_id = ?", (acc_id,))
    existing = c.fetchone()
    ex_qty = existing[4] # quantity
    ex_cost_price = existing[5] # cost_price
    ex_total_cost = existing[6] # total_cost
    
    # Current Implementation Re-Sim:
    new_qty_sell = max(0, ex_qty - sell_qty)
    # cost_price = existing['total_cost'] / existing['quantity']
    calc_cost_price = ex_total_cost / ex_qty
    new_total_cost_sell = calc_cost_price * new_qty_sell
    
    print(f"DEBUG: Calc Cost Price: {calc_cost_price}")
    print(f"DEBUG: New Total Cost (Standard): {new_total_cost_sell}")
    
    # 4. Simulate Diluted Logic
    sell_revenue = trans_sell["price"] * sell_qty
    sell_fee = max(sell_revenue * 0.0003, 5.0)
    sell_stamp = sell_revenue * 0.001
    net_proceeds = sell_revenue - sell_fee - sell_stamp
    
    diluted_total_cost = ex_total_cost - net_proceeds
    if new_qty_sell > 0:
        diluted_cost_price = diluted_total_cost / new_qty_sell
    else:
        diluted_cost_price = 0
        
    print(f"DEBUG: Net Proceeds: {net_proceeds}")
    print(f"DEBUG: Diluted Total Cost: {diluted_total_cost}")
    print(f"DEBUG: Diluted Cost Price: {diluted_cost_price}")

    conn.close()

def print_holding(c, acc_id):
    c.execute("SELECT quantity, cost_price, total_cost FROM holdings WHERE account_id = ?", (acc_id,))
    row = c.fetchone()
    print(f"HOLDING STATE: Qty={row[0]}, UnitCost={row[1]:.4f}, TotalCost={row[2]:.2f}")

if __name__ == "__main__":
    run_test()
