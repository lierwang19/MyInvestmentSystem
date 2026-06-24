import sqlite3
import os

DB_PATH = 'd:/MyInvestmentSystem/user_data.db'

def check():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    print("--- Accounts ---")
    c.execute("SELECT id, name FROM accounts")
    for row in c.fetchall():
        print(f"  {row['id']}: {row['name']}")
    
    print("\n--- Holdings Count per Account ---")
    c.execute("SELECT account_id, COUNT(*) as cnt FROM holdings GROUP BY account_id")
    for row in c.fetchall():
        print(f"  {row['account_id']}: {row['cnt']} holdings")
    
    print("\n--- Sample Holdings for A-02 (if exists) ---")
    # Find account with "A-02" in name or id
    c.execute("SELECT id FROM accounts WHERE name LIKE '%A-02%' OR id LIKE '%02%'")
    a02 = c.fetchone()
    if a02:
        a02_id = a02['id']
        print(f"  A-02 Account ID: {a02_id}")
        c.execute("SELECT ts_code, quantity, total_cost FROM holdings WHERE account_id = ? LIMIT 5", (a02_id,))
        rows = c.fetchall()
        if rows:
            for r in rows:
                print(f"    {r['ts_code']}: qty={r['quantity']}, cost={r['total_cost']}")
        else:
            print("    (No holdings in DB for A-02)")
    else:
        print("  (A-02 account not found in DB)")
    
    conn.close()

if __name__ == "__main__":
    check()
