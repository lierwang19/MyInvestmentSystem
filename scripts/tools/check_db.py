import sqlite3

conn = sqlite3.connect('/app/market.db')

# Check daily_valuation table
try:
    cols = conn.execute("PRAGMA table_info(daily_valuation)").fetchall()
    print("daily_valuation columns:", [c[1] for c in cols])
    r = conn.execute("SELECT COUNT(*) FROM daily_valuation").fetchone()
    print(f"daily_valuation rows: {r[0]}")
    r2 = conn.execute("SELECT MIN(trade_date), MAX(trade_date) FROM daily_valuation").fetchone()
    print(f"daily_valuation date range: {r2[0]} to {r2[1]}")
    r3 = conn.execute("SELECT COUNT(*) FROM daily_valuation WHERE ts_code='600528.SH'").fetchone()
    print(f"daily_valuation rows for 600528.SH: {r3[0]}")
    r4 = conn.execute("SELECT * FROM daily_valuation WHERE ts_code='600528.SH' LIMIT 3").fetchall()
    print(f"daily_valuation sample 600528:", r4)
except Exception as e:
    print(f"daily_valuation error: {e}")

# Check fin_indicators_q table
try:
    cols = conn.execute("PRAGMA table_info(fin_indicators_q)").fetchall()
    print("\nfin_indicators_q columns:", [c[1] for c in cols])
    r = conn.execute("SELECT COUNT(*) FROM fin_indicators_q").fetchone()
    print(f"fin_indicators_q rows: {r[0]}")
    r2 = conn.execute("SELECT * FROM fin_indicators_q WHERE ts_code='600528.SH' LIMIT 3").fetchall()
    print(f"fin_indicators_q sample:", r2)
except Exception as e:
    print(f"fin_indicators_q error: {e}")

conn.close()
