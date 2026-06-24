
import pandas as pd
import sqlite3
import os

def connect_db(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)

def get_tail_from_db(conn: sqlite3.Connection, ts_code: str, n: int) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT close
        FROM daily_price
        WHERE ts_code = ?
        ORDER BY trade_date DESC
        LIMIT ?;
        """,
        conn,
        params=(ts_code, n),
    )
    if df.empty:
        return df
    return df # Note: DESC order in query, so iloc[0] is latest, iloc[-1] is oldest

def patch():
    base_file = "market_enriched.csv"
    db_path = "market.db"
    
    if not os.path.exists(base_file) or not os.path.exists(db_path):
        print("Missing files.")
        return

    df = pd.read_csv(base_file, dtype={"ts_code": str})
    print(f"Loaded {len(df)} rows.")

    conn = connect_db(db_path)
    
    yearly_growth = []
    
    for i, row in df.iterrows():
        ts = row["ts_code"]
        # We need N=252.
        # Query: ORDER BY trade_date DESC LIMIT 252
        # So .iloc[0] is LATEST. .iloc[-1] is OLDEST (~1y ago).
        
        tail = pd.read_sql_query(
            "SELECT close FROM daily_price WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 252",
            conn, params=(ts,)
        )
        
        growth = None
        if not tail.empty and len(tail) > 200: # Ensure enough data
            try:
                latest = float(tail.iloc[0]["close"])
                oldest = float(tail.iloc[-1]["close"])
                if oldest > 0:
                    growth = (latest - oldest) / oldest * 100
                    growth = round(growth, 2)
            except:
                pass
        
        yearly_growth.append(growth)
        
        if (i+1) % 500 == 0:
            print(f"Processed {i+1}")

    conn.close()
    
    df["yearly_growth"] = yearly_growth
    df.to_csv(base_file, index=False, encoding="utf-8-sig")
    print(f"Patched {base_file} with yearly_growth.")

if __name__ == "__main__":
    patch()
