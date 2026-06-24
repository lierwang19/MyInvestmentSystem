
import sqlite3
import os
import sys

# Setup Python Path to reach web.backend
PROJECT_ROOT = "d:/MyInvestmentSystem"
sys.path.append(PROJECT_ROOT)

from web.backend.database import get_db
from web.backend.api.transactions import archive_holding, ArchiveRequest
from fastapi import HTTPException

def run_test():
    with get_db() as conn:
        c = conn.cursor()
        
        # 1. Test Case A: Holding exists (Qty=0) + Transactions Exist
        acc_id_a = "TEST_ARCHIVE_A"
        ts_code_a = "000001.SZ"
        
        # Cleanup
        c.execute("DELETE FROM accounts WHERE id = ?", (acc_id_a,))
        c.execute("DELETE FROM transactions WHERE account_id = ?", (acc_id_a,))
        c.execute("DELETE FROM holdings WHERE account_id = ?", (acc_id_a,))
        c.execute("DELETE FROM archived_holdings WHERE account_id = ?", (acc_id_a,))
        
        # Setup
        c.execute("INSERT INTO accounts (id, name, broker_id) VALUES (?, ?, NULL)", (acc_id_a, "Test A"))
        c.execute("INSERT INTO transactions (account_id, ts_code, trans_type, price, amount, total_amt, fee, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
                  (acc_id_a, ts_code_a, "BUY", 10.0, 100, 1000.0, 5.0, "2023-01-01"))
        c.execute("INSERT INTO transactions (account_id, ts_code, trans_type, price, amount, total_amt, fee, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
                  (acc_id_a, ts_code_a, "SELL", 12.0, -100, 1200.0, 5.0, "2023-01-02"))
        c.execute("INSERT INTO holdings (account_id, ts_code, quantity, total_cost, cost_price) VALUES (?, ?, ?, ?, ?)",
                  (acc_id_a, ts_code_a, 0, -190.0, 0))
        conn.commit()
        
        print("\n--- Testing Case A (Has Transactions) ---")
        try:
            req = ArchiveRequest(ts_code=ts_code_a, stock_name="Test Stock A")
            res = archive_holding(acc_id_a, req)
            print("Success:", res)
        except Exception as e:
            print("Error:", e)

        # 2. Test Case B: Holding exists (Qty=0) + NO Transactions
        acc_id_b = "TEST_ARCHIVE_B"
        ts_code_b = "000002.SZ"
        
        # Cleanup
        c.execute("DELETE FROM accounts WHERE id = ?", (acc_id_b,))
        c.execute("DELETE FROM transactions WHERE account_id = ?", (acc_id_b,))
        c.execute("DELETE FROM holdings WHERE account_id = ?", (acc_id_b,))
        c.execute("DELETE FROM archived_holdings WHERE account_id = ?", (acc_id_b,))
        
        # Setup
        c.execute("INSERT INTO accounts (id, name, broker_id) VALUES (?, ?, NULL)", (acc_id_b, "Test B"))
        c.execute("INSERT INTO holdings (account_id, ts_code, quantity, total_cost, cost_price) VALUES (?, ?, ?, ?, ?)",
                  (acc_id_b, ts_code_b, 0, -500.0, 0))
        conn.commit()

    print("\n--- Testing Case B (No Transactions) ---")
    try:
        req = ArchiveRequest(ts_code=ts_code_b, stock_name="Test Stock B")
        res = archive_holding(acc_id_b, req)
        print("Success:", res)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    run_test()
