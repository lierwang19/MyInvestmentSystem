
import sqlite3
import os
import sys
from datetime import datetime

# Setup DB
DB_PATH = 'd:/MyInvestmentSystem/user_data.db'

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# Mock HTTPException
class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)

# Mock ArchiveRequest
class ArchiveRequest:
    def __init__(self, ts_code, stock_name=None):
        self.ts_code = ts_code
        self.stock_name = stock_name


# Mock get_stock_summary (Simplified logic for test)
def get_stock_summary(account_id, ts_code):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM transactions WHERE account_id = ? AND ts_code = ?', (account_id, ts_code))
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        return {"status": "warning", "message": "No transactions found"}
    
    # Simulate data that might cause issues (e.g. infinite profit pct)
    # Total Buy = 0, Profit > 0 => Infinite %
    if ts_code == 'NAN.SZ':
         return {
            "status": "success",
            "data": {
                "current_quantity": 0,
                "total_buy_amount": 0, # Denom for % calc
                "total_sell_amount": 100.0,
                "total_dividend": 0,
                "total_fees": 0,
                "realized_pnl": 100.0,
                "first_buy_date": None,
                "last_sell_date": "2023-01-02"
            }
        }
    
    # Simple summary for Case A
    return {
        "status": "success",
        "data": {
            "current_quantity": 0,
            "total_buy_amount": 1000.0,
            "total_sell_amount": 1200.0,
            "total_dividend": 0,
            "total_fees": 10.0,
            "realized_pnl": 190.0,
            "first_buy_date": "2023-01-01",
            "last_sell_date": "2023-01-02"
        }
    }

# Ensure valid_float is available or copied for test
import math
def valid_float(val):
    try:
        if val is None: return 0.0
        f = float(val)
        if math.isnan(f) or math.isinf(f): return 0.0
        return f
    except:
        return 0.0


# --- The Function Under Test ---
def archive_holding(account_id: str, req: ArchiveRequest):
    """Archive a cleared stock (move from active to archived)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Init data container
    data = None
    
    # 1. Try to get summary from transactions
    summary_result = get_stock_summary(account_id, req.ts_code)
    
    if summary_result.get("status") == "success":
        data = summary_result["data"]
    
    # 2. Check current holding status in DB
    c.execute('SELECT * FROM holdings WHERE account_id = ? AND ts_code = ?', (account_id, req.ts_code))
    holding_row = c.fetchone()
    
    if not holding_row and not data:
        conn.close()
        raise HTTPException(status_code=404, detail="Holding not found")
        
    current_qty = holding_row['quantity'] if holding_row else (data['current_quantity'] if data else 0)
    
    # Check if still has holdings
    if current_qty > 0:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Cannot archive: still holding {current_qty} shares")
    
    # 3. Prepare Archive Data
    if data:
        # Use transaction-based data
        total_buy = data["total_buy_amount"]
        total_sell = data["total_sell_amount"]
        total_dividend = data["total_dividend"]
        total_fees = data["total_fees"]
        profit = data["realized_pnl"]
        first_buy_date = data["first_buy_date"]
        last_sell_date = data["last_sell_date"]
    elif holding_row:
        print("DEBUG: Using Holding Data (Fallback)")
        profit = -holding_row['total_cost']
        
        # Heuristic for Buy/Sell amounts:
        if profit >= 0:
            total_buy = 0
            total_sell = profit
        else:
            # We lost money. holding_row['total_cost'] is positive.
            total_buy = holding_row['total_cost']
            total_sell = 0
            
        total_dividend = 0
        total_fees = 0
        first_buy_date = holding_row['updated_at'].split(' ')[0] if holding_row['updated_at'] else None
        last_sell_date = datetime.now().strftime("%Y-%m-%d")
        
    profit_pct = 0
    if valid_float(total_buy) > 0:
        profit_pct = (profit / total_buy * 100)
    elif valid_float(profit) > 0 and total_buy == 0:
        profit_pct = 100 
        
    try:
        # Insert into archived_holdings
        c.execute('''
            INSERT INTO archived_holdings 
            (account_id, ts_code, stock_name, total_buy_amount, total_sell_amount, 
             total_dividend, total_profit, profit_pct, total_fees, first_buy_date, last_sell_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            account_id,
            req.ts_code,
            req.stock_name or req.ts_code,
            valid_float(round(total_buy, 2)),
            valid_float(round(total_sell, 2)),
            valid_float(round(total_dividend, 2)),
            valid_float(round(profit, 2)),
            valid_float(round(profit_pct, 2)),
            valid_float(round(total_fees, 2)),
            first_buy_date,
            last_sell_date
        ))
        
        # Delete from holdings table (now archived)
        c.execute('DELETE FROM holdings WHERE account_id = ? AND ts_code = ?', (account_id, req.ts_code))
        
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "profit": round(profit, 2),
            "total_buy": round(total_buy, 2),
            "total_sell": round(total_sell, 2)
        }
        
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

def run_test():
    conn = get_db_connection()
    c = conn.cursor()
    
    # 1. Test Case A: Holding exists (Qty=0) + Transactions Exist
    acc_id_a = "TEST_ARCHIVE_A"
    ts_code_a = "000001.SZ"
    
    # Setup
    c.execute("DELETE FROM holdings WHERE account_id=? AND ts_code=?", (acc_id_a, ts_code_a))
    c.execute("INSERT INTO holdings (account_id, ts_code, quantity, total_cost) VALUES (?, ?, 0, -190.0)", (acc_id_a, ts_code_a))
    conn.commit()
    
    print("\n--- Testing Case A (Normal) ---")
    try:
        req = ArchiveRequest(ts_code=ts_code_a, stock_name="Test Stock A")
        res = archive_holding(acc_id_a, req)
        print("Success:", res)
    except Exception as e:
        print("Error:", e)

    # 4. Test Case D: Loss Fallback (No Transactions)
    # Total Cost = 200 (Positive means Loss of 200)
    acc_id_d = "TEST_ARCHIVE_D"
    ts_code_d = "LOSS.SZ"
    
    c.execute("DELETE FROM holdings WHERE account_id=? AND ts_code=?", (acc_id_d, ts_code_d))
    c.execute("INSERT INTO holdings (account_id, ts_code, quantity, total_cost) VALUES (?, ?, 0, 200.0)", (acc_id_d, ts_code_d))
    conn.commit()

    print("\n--- Testing Case D (Loss Fallback) ---")
    try:
        req = ArchiveRequest(ts_code=ts_code_d, stock_name="Loss Stock")
        res = archive_holding(acc_id_d, req)
        print("Success:", res)
        # Expected: Profit = -200, Total Buy = 200, Total Sell = 0
    except Exception as e:
        print("Error:", e)
    
    conn.close()

if __name__ == "__main__":
    run_test()
