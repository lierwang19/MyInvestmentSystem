"""
One-time migration script: Import holdings from Excel/CSV to database.
Usage: Called by API endpoint /api/migrate/from-file
"""
import pandas as pd
import os
import sys

# Add project root to path
from web.backend.config import PROJECT_ROOT
sys.path.append(PROJECT_ROOT)

from web.backend.database import get_db, init_db
from web.backend.api.upload import UPLOAD_DIR
from web.backend.time_utils import sh_timestamp_str
from web.backend.utils import normalize_code
import logging

logger = logging.getLogger(__name__)

def migrate_holdings_from_file(account_id: str):
    """
    Read the latest uploaded holdings file for an account and import to database.
    Returns: (success: bool, message: str, imported_count: int)
    """
    # 1. Find latest file
    acc_dir = os.path.join(UPLOAD_DIR, "holdings", account_id)
    if not os.path.exists(acc_dir):
        return False, f"No holdings folder for account {account_id}", 0
    
    files = os.listdir(acc_dir)
    files.sort(reverse=True)
    if not files:
        return False, "No holdings file found", 0
    
    file_path = os.path.join(acc_dir, files[0])
    
    # 2. Read file
    try:
        if file_path.endswith(".csv"):
            df = pd.read_csv(file_path, dtype=str)
        else:
            df = pd.read_excel(file_path, dtype=str)
    except Exception as e:
        return False, f"Failed to read file: {str(e)}", 0
    
    # 3. Identify columns (heuristic)
    cols = df.columns
    
    def find_col(keywords):
        for k in keywords:
            for c in cols:
                if k in c:
                    return c
        return None
    
    code_col = find_col(["证券代码", "代码", "Code", "ts_code"])
    name_col = find_col(["证券名称", "名称", "Name"])
    cost_col = find_col(["成本价", "Cost"])
    mv_col = find_col(["参考市值", "市值", "MarketValue", "Assets"])
    qty_col = find_col(["持仓数量", "数量", "Quantity", "Share", "Shares", "当前持仓"])
    
    if not code_col:
        return False, f"Could not identify Stock Code column. Available: {cols.tolist()}", 0
    
    # 4. Connect to DB
    init_db()  # Ensure tables exist
    with get_db() as conn:
        c = conn.cursor()
        
        imported = 0
        errors = []
        
        for _, row in df.iterrows():
            try:
                ts_code = normalize_code(row[code_col])
                name = row[name_col] if name_col else ""
                cost_price = float(pd.to_numeric(row.get(cost_col, 0), errors="coerce")) if cost_col else 0
                market_value = float(pd.to_numeric(row.get(mv_col, 0), errors="coerce")) if mv_col else 0
                
                # Calculate quantity
                quantity = 0
                if qty_col:
                    quantity = float(pd.to_numeric(row.get(qty_col, 0), errors="coerce"))
                
                total_cost = cost_price * quantity if quantity > 0 else market_value  # Use MV as fallback
                
                # Insert or update
                now_ts = sh_timestamp_str()
                c.execute('''
                    INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, ts_code) 
                    DO UPDATE SET 
                        name=excluded.name,
                        quantity=excluded.quantity,
                        cost_price=excluded.cost_price,
                        total_cost=excluded.total_cost,
                        updated_at=excluded.updated_at
                ''', (account_id, ts_code, name, quantity, cost_price, total_cost, now_ts, now_ts))
                
                imported += 1
            except Exception as e:
                errors.append(f"{row.get(code_col, 'Unknown')}: {str(e)}")
        
        conn.commit()
    
    if errors:
        return True, f"Imported {imported} stocks with {len(errors)} errors: {errors[:3]}", imported
    
    return True, f"Successfully imported {imported} holdings to database", imported

if __name__ == "__main__":
    # Test
    success, msg, count = migrate_holdings_from_file("acc_789e5522")
    logger.info(f"Success: {success}, Message: {msg}, Count: {count}")

def migrate_stock_industry_columns():
    """增量迁移：为 stock_industry 表补充 name_initials 列（建表由 init_db 负责）"""
    with get_db() as conn:
        c = conn.cursor()
        try:
            c.execute("ALTER TABLE stock_industry ADD COLUMN name_initials TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass  # 列已存在，忽略


def add_stamp_tax_column():
    """Add stamp_tax column to transactions table if it doesn't exist"""
    with get_db() as conn:
        c = conn.cursor()
        
        # Check if column exists
        c.execute("PRAGMA table_info(transactions)")
        columns = [col[1] for col in c.fetchall()]
        
        if 'stamp_tax' not in columns:
            logger.info("Adding stamp_tax column to transactions table...")
            c.execute('ALTER TABLE transactions ADD COLUMN stamp_tax REAL DEFAULT 0.0')
            conn.commit()
            logger.info("✅ stamp_tax column added successfully")
        else:
            logger.info("stamp_tax column already exists")


def migrate_brokers_transfer_fee():
    """为存量 brokers 表补充 transfer_fee_rate 列（幂等）"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("PRAGMA table_info(brokers)")
        cols = {row[1] for row in c.fetchall()}
        if "transfer_fee_rate" in cols:
            logger.info("brokers.transfer_fee_rate already exists — skipping")
            return
        c.execute("ALTER TABLE brokers ADD COLUMN transfer_fee_rate REAL DEFAULT 0.00002")
        conn.commit()
        logger.info("✅ Added brokers.transfer_fee_rate column")


def migrate_strategy_columns():
    """增量迁移：种子数据 + 补充 account_id 列（建表由 init_db 负责）"""
    with get_db() as conn:
        c = conn.cursor()
        now_ts = sh_timestamp_str()

        # Seed default strategy (idempotent)
        c.execute("""
            INSERT OR IGNORE INTO strategies (id, name, description, strategy_type, selector_type, allocator_type, status, config_path, created_at, updated_at)
            VALUES ('dashu_v1', '大数投资', '基于PB/PE低估值的组合投资策略', 'value_accumulation', 'scoring', 'dashu_positioning', 'active', 'config/strategies/dashu_v1.yaml', ?, ?)
        """, (now_ts, now_ts))

        # 存量表迁移：补充 account_id 列（已存在则忽略）
        for table in ("strategy_runs", "strategy_signals", "strategy_decisions"):
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN account_id TEXT")
            except Exception:
                pass  # column already exists

        conn.commit()
        logger.info("✅ Strategy columns migrated")


def migrate_holdings_buy_pb():
    """增量迁移：给存量 holdings 表加 buy_pb 列（已存在则跳过）"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("PRAGMA table_info(holdings)")
        columns = [col[1] for col in c.fetchall()]
        if "buy_pb" not in columns:
            logger.info("Adding buy_pb column to holdings table...")
            c.execute("ALTER TABLE holdings ADD COLUMN buy_pb REAL DEFAULT 0.0")
            conn.commit()
            logger.info("✅ buy_pb column added to holdings")
        else:
            logger.info("buy_pb column already exists in holdings")


def migrate_t_trading_table():
    """增量迁移：确保 t_trading_items 表及 removed_at 列存在（幂等）"""
    with get_db() as conn:
        c = conn.cursor()

        # 若表不存在则建表
        c.execute('''
            CREATE TABLE IF NOT EXISTS t_trading_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                transaction_id INTEGER NOT NULL,
                ts_code TEXT NOT NULL,
                name TEXT,
                buy_price REAL NOT NULL,
                buy_quantity INTEGER NOT NULL,
                buy_date TEXT NOT NULL,
                removed_at TEXT DEFAULT NULL,
                created_at TEXT,
                UNIQUE(account_id, transaction_id)
            )
        ''')

        # 若表已存在但缺少 removed_at 列（早期版本建表时可能没有），补列
        c.execute("PRAGMA table_info(t_trading_items)")
        columns = [col[1] for col in c.fetchall()]
        if "removed_at" not in columns:
            logger.info("Adding removed_at column to t_trading_items...")
            c.execute("ALTER TABLE t_trading_items ADD COLUMN removed_at TEXT DEFAULT NULL")
            logger.info("✅ removed_at column added to t_trading_items")

        conn.commit()


def migrate_t_trading_trade_side():
    """增量迁移：为 t_trading_items 补 trade_side 列并重建 UNIQUE 约束（幂等）"""
    with get_db() as conn:
        c = conn.cursor()

        # 检查 trade_side 列是否已存在
        c.execute("PRAGMA table_info(t_trading_items)")
        columns = [col[1] for col in c.fetchall()]

        if "trade_side" in columns:
            # 列已存在，用 PRAGMA index_list + index_info 检查唯一约束是否已包含 trade_side
            constraint_ok = False
            c.execute("PRAGMA index_list(t_trading_items)")
            for idx_row in c.fetchall():
                if idx_row[2]:  # unique == 1
                    idx_name = idx_row[1]
                    c.execute(f"PRAGMA index_info({idx_name})")
                    idx_cols = [r[2] for r in c.fetchall()]
                    if set(idx_cols) == {"account_id", "transaction_id", "trade_side"}:
                        constraint_ok = True
                        break
            if constraint_ok:
                logger.info("trade_side column and UNIQUE(account_id, transaction_id, trade_side) already present")
                return
            # 列存在但 UNIQUE 约束不包含 trade_side——仍然走重建流程
        else:
            # 先加列（为后续重建准备数据）
            c.execute("ALTER TABLE t_trading_items ADD COLUMN trade_side TEXT NOT NULL DEFAULT 'BUY'")
            logger.info("Added trade_side column to t_trading_items")

        # 重建表以更新 UNIQUE 约束：(account_id, transaction_id) → (account_id, transaction_id, trade_side)
        c.execute('''
            CREATE TABLE IF NOT EXISTS t_trading_items_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                transaction_id INTEGER NOT NULL,
                ts_code TEXT NOT NULL,
                name TEXT,
                buy_price REAL NOT NULL,
                buy_quantity INTEGER NOT NULL,
                buy_date TEXT NOT NULL,
                trade_side TEXT NOT NULL DEFAULT 'BUY',
                removed_at TEXT DEFAULT NULL,
                created_at TEXT,
                UNIQUE(account_id, transaction_id, trade_side)
            )
        ''')
        c.execute('''
            INSERT OR IGNORE INTO t_trading_items_new
                (id, account_id, transaction_id, ts_code, name,
                 buy_price, buy_quantity, buy_date, trade_side, removed_at, created_at)
            SELECT id, account_id, transaction_id, ts_code, name,
                   buy_price, buy_quantity, buy_date, trade_side, removed_at, created_at
            FROM t_trading_items
        ''')
        c.execute("DROP TABLE t_trading_items")
        c.execute("ALTER TABLE t_trading_items_new RENAME TO t_trading_items")

        conn.commit()
        logger.info("✅ t_trading_items rebuilt with trade_side column and updated UNIQUE constraint")


def migrate_watchlist_table():
    """增量迁移：确保 watchlist_stocks 表存在（对已初始化的老库补表，幂等）"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS watchlist_stocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_code TEXT NOT NULL,
                name TEXT,
                added_price REAL,
                added_date TEXT NOT NULL,
                note TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                removed_at TEXT DEFAULT NULL,
                created_at TEXT,
                UNIQUE(ts_code)
            )
        ''')
        conn.commit()
        logger.info("✅ watchlist_stocks table ensured")


def migrate_hk_stocks_table():
    """增量迁移：确保 hk_stocks 表存在（对已初始化的老库做补表）"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS hk_stocks (
                ts_code   TEXT PRIMARY KEY,
                name      TEXT,
                sector    TEXT,
                synced_at TEXT
            )
        ''')
        conn.commit()
        logger.info("✅ hk_stocks table ensured")


def migrate_ipo_calendar_table():
    """增量迁移：确保 ipo_calendar 表存在（对已初始化的老库做补表）"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS ipo_calendar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                board TEXT DEFAULT '',
                ts_code TEXT NOT NULL,
                sub_code TEXT DEFAULT '',
                name TEXT NOT NULL,
                subscribe_date TEXT NOT NULL,
                issue_price REAL,
                apply_limit REAL,
                lottery_date TEXT,
                listing_date TEXT,
                source TEXT DEFAULT 'akshare_em',
                updated_at TEXT,
                UNIQUE(ts_code, subscribe_date)
            )
        ''')
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_ipo_calendar_subscribe_date ON ipo_calendar (subscribe_date)"
        )
        conn.commit()
        logger.info("✅ ipo_calendar table ensured")
def migrate_archived_holdings_unique():
    """增量迁移：为存量库的 archived_holdings 表补充 UNIQUE(account_id, ts_code) 约束。

    SQLite 不支持直接 ALTER TABLE ADD CONSTRAINT，需重建表。
    迁移前会对重复行去重（保留 id 最大的那条，即最新记录）。
    已有约束的库重入此函数是幂等的（先检测索引是否存在）。

    安全性：重建前先 DROP TABLE IF EXISTS archived_holdings_new，
    确保上次迁移异常中断留下的半成品中间表不会干扰本次迁移。
    整个重建流程在同一事务内完成，保证原子性。
    """
    with get_db() as conn:
        c = conn.cursor()

        # 检查是否已有 UNIQUE(account_id, ts_code) 约束
        c.execute("PRAGMA index_list(archived_holdings)")
        for idx_row in c.fetchall():
            if idx_row[2]:  # unique == 1
                idx_name = idx_row[1]
                c.execute(f"PRAGMA index_info({idx_name})")
                idx_cols = {r[2] for r in c.fetchall()}
                if idx_cols == {"account_id", "ts_code"}:
                    logger.info("archived_holdings already has UNIQUE(account_id, ts_code) — skipping")
                    return

        logger.info("Rebuilding archived_holdings to add UNIQUE(account_id, ts_code)...")

        # 0. 清理上次可能中断留下的半成品中间表，防止 IF NOT EXISTS 静默复用旧表
        c.execute("DROP TABLE IF EXISTS archived_holdings_new")

        # 1. 建干净的新表（含唯一约束）
        c.execute('''
            CREATE TABLE archived_holdings_new (
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
                archived_at TEXT,
                FOREIGN KEY (account_id) REFERENCES accounts(id),
                UNIQUE(account_id, ts_code)
            )
        ''')

        # 2. 迁移数据：对重复 (account_id, ts_code) 仅保留 id 最大的那条（最新记录）
        c.execute('''
            INSERT INTO archived_holdings_new
                (id, account_id, ts_code, stock_name,
                 total_buy_amount, total_sell_amount, total_dividend,
                 total_profit, profit_pct, total_fees,
                 first_buy_date, last_sell_date, archived_at)
            SELECT id, account_id, ts_code, stock_name,
                   total_buy_amount, total_sell_amount, total_dividend,
                   total_profit, profit_pct, total_fees,
                   first_buy_date, last_sell_date, archived_at
            FROM archived_holdings
            WHERE id IN (
                SELECT MAX(id)
                FROM archived_holdings
                GROUP BY account_id, ts_code
            )
        ''')

        # 3. 原子替换：DROP + RENAME 在同一事务内
        c.execute("DROP TABLE archived_holdings")
        c.execute("ALTER TABLE archived_holdings_new RENAME TO archived_holdings")

        conn.commit()
        logger.info("✅ archived_holdings rebuilt with UNIQUE(account_id, ts_code)")
