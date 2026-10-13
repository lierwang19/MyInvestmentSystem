
import sqlite3
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from web.backend.config import USER_DB_PATH
from web.backend.time_utils import sh_timestamp_str

DB_PATH = USER_DB_PATH

def get_db_connection():
    """DEPRECATED: Use get_db() context manager instead. Kept for backward compatibility only."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

from contextlib import contextmanager

@contextmanager
def get_db():
    """Context manager for safe DB access. Usage: `with get_db() as conn:`"""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    # WAL: 定时任务/API/行情轮询并发写入时，避免 "database is locked"。
    # user_data.db 现位于 data/ 目录（Docker 卷挂载），WAL 附属文件与主库同卷，安全。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()

from web.backend.config import PROJECT_ROOT, MARKET_DB_PATH

@contextmanager
def get_market_db():
    """Read-only context manager for market.db. Yields None if market.db doesn't exist.

    业务 API 只读行情库；需要写入时使用 get_market_db_rw()。
    WAL 由写入侧（ETL / get_market_db_rw）负责开启。
    """
    if not os.path.exists(MARKET_DB_PATH):
        yield None
        return
    conn = sqlite3.connect(Path(MARKET_DB_PATH).resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000") # wait up to 15s before raising lock error
    try:
        yield conn
    finally:
        conn.close()

@contextmanager
def get_market_db_rw():
    """Read-write context manager for market.db, only for legitimate writes. Yields None if missing."""
    if not os.path.exists(MARKET_DB_PATH):
        yield None
        return
    conn = sqlite3.connect(MARKET_DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # allow concurrent reads during ETL writes
    conn.execute("PRAGMA busy_timeout=15000") # wait up to 15s before raising lock error
    try:
        yield conn
    finally:
        conn.close()

def pending_restore_path(db_path=None):
    return str(db_path or DB_PATH) + ".restore-pending"


def validate_user_db(path):
    """Reject corrupt, empty, or unrelated SQLite files before accepting a restore."""
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        results = conn.execute("PRAGMA integrity_check").fetchall()
        if results != [("ok",)]:
            raise ValueError("备份数据库完整性检查失败")
        required = {
            "accounts": {"id", "name", "cash_balance"},
            "brokers": {"id", "name"},
            "holdings": {"account_id", "ts_code", "quantity", "cost_price"},
            "transactions": {"account_id", "ts_code", "trans_type", "date"},
            "system_settings": {"key", "value"},
        }
        for table, columns in required.items():
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not columns <= actual:
                raise ValueError(f"备份缺少必要的业务表或字段：{table}")
    finally:
        conn.close()


def apply_pending_restore():
    """Apply a validated snapshot at startup, before pollers and jobs start.

    Keep the live file's inode and let SQLite update its pages and WAL. An
    exclusive connection rejects an overlapping backend/ETL reader or writer;
    on failure the pending snapshot is retained and startup fails closed.
    """
    pending = pending_restore_path()
    if not os.path.isfile(pending):
        return
    validate_user_db(pending)
    dst = sqlite3.connect(DB_PATH, timeout=15)
    try:
        dst.execute("PRAGMA locking_mode=EXCLUSIVE")
        dst.execute("BEGIN EXCLUSIVE")
        dst.rollback()  # exclusive locking mode retains the file lock
        src = sqlite3.connect(Path(pending).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            src.backup(dst)
        finally:
            src.close()
    finally:
        dst.close()
    os.unlink(pending)
    logger.info("已在启动阶段恢复用户数据库：%s", DB_PATH)


def init_db():
    apply_pending_restore()
    with get_db() as conn:
        _create_tables(conn)

def _create_tables(conn):
    c = conn.cursor()
    
    # 1. Brokers Table (券商信息)
    c.execute('''
    CREATE TABLE IF NOT EXISTS brokers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        commission_rate REAL DEFAULT 0.0003,
        min_commission REAL DEFAULT 5.0,
        stamp_tax_rate REAL DEFAULT 0.001,
        transfer_fee_rate REAL DEFAULT 0.00002,
        created_at TEXT
    )
    ''')
    
    # 2. Accounts Table (账户表 - 扩展现有accounts.json逻辑到DB)
    # Note: 现有账户存在 data/accounts/accounts.json 中，后续可迁移到此表
    c.execute('''
    CREATE TABLE IF NOT EXISTS accounts (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        broker_id INTEGER,
        remarks TEXT,
        cash_balance REAL DEFAULT 0.0,
        migrated INTEGER DEFAULT 0,
        created_at TEXT,
        FOREIGN KEY (broker_id) REFERENCES brokers(id)
    )
    ''')
    
    # 3. Holdings Table (Real-time snapshot)
    # This replaces the need for Excel files. 
    # 'account_id' allows multi-account support in one DB.
    c.execute('''
    CREATE TABLE IF NOT EXISTS holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        name TEXT,
        quantity INTEGER NOT NULL DEFAULT 0,
        cost_price REAL NOT NULL DEFAULT 0.0,
        total_cost REAL NOT NULL DEFAULT 0.0,
        buy_pb REAL DEFAULT 0.0,   -- PB at first-buy time; used by from_buy_pb sell bucketing
        created_at TEXT,
        updated_at TEXT,
        UNIQUE(account_id, ts_code)
    )
    ''')
    
    # 4. Transactions Table (History)
    # Records every action: Buy, Sell, Dividend, Split
    c.execute('''
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        name TEXT,
        trans_type TEXT NOT NULL, -- 'BUY', 'SELL', 'DIVIDEND', 'SPLIT'
        price REAL NOT NULL, -- Price per share or Dividend amount
        amount INTEGER NOT NULL, -- Quantity (negative for SELL)
        total_amt REAL NOT NULL, -- Total Money (price * amount)
        fee REAL DEFAULT 0.0, -- Commission fee
        stamp_tax REAL DEFAULT 0.0, -- Stamp tax (for SELL only, 0.05%)
        date TEXT NOT NULL, -- YYYY-MM-DD
        remarks TEXT,
        created_at TEXT
    )
    ''')
    
    # 5. Capital Flows Table (资金转入/转出记录)
    c.execute('''
    CREATE TABLE IF NOT EXISTS capital_flows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        flow_type TEXT NOT NULL, -- 'DEPOSIT'(转入) / 'WITHDRAW'(转出)
        amount REAL NOT NULL,
        date TEXT NOT NULL, -- YYYY-MM-DD
        remarks TEXT,
        created_at TEXT
    )
    ''')
    
    # 6. Archived Holdings Table (已清仓股票归档)
    c.execute('''
    CREATE TABLE IF NOT EXISTS archived_holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        stock_name TEXT,
        total_buy_amount REAL DEFAULT 0, -- Cumulative buy amount
        total_sell_amount REAL DEFAULT 0, -- Cumulative sell amount
        total_dividend REAL DEFAULT 0, -- Cumulative dividend received
        total_profit REAL DEFAULT 0, -- Final P&L
        profit_pct REAL DEFAULT 0, -- P&L percentage
        total_fees REAL DEFAULT 0, -- Total fees paid
        first_buy_date TEXT, -- First buy date
        last_sell_date TEXT, -- Last sell date
        archived_at TEXT,
        FOREIGN KEY (account_id) REFERENCES accounts(id),
        UNIQUE(account_id, ts_code)  -- 防止同一账户同一股票重复归档
    )
    ''')
    
    # NOTE: market_monthly table has been migrated to market.db (see market.py)
    
    # 8. System Settings Table (系统设置表)
    c.execute('''
    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        description TEXT,
        updated_at TEXT
    )
    ''')

    # 9. Stock Pool Table (大数优选池)
    c.execute('''
    CREATE TABLE IF NOT EXISTS stock_pool (
        ts_code TEXT PRIMARY KEY,
        name TEXT,
        industry TEXT,
        pb REAL,
        pe REAL,
        anchor_pb REAL,
        score REAL, -- win_prob * 100
        updated_at TEXT
    )
    ''')

    # 10. Asset History (净值曲线)
    c.execute('''
    CREATE TABLE IF NOT EXISTS asset_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, -- YYYY-MM-DD
        account_id TEXT NOT NULL,
        total_assets REAL NOT NULL, -- 总资产 = 持仓市值 + 现金
        total_market_value REAL NOT NULL, -- 持仓市值
        cash_balance REAL NOT NULL, -- 现金余额
        total_profit REAL, -- 当日累计盈亏 (简单记录)
        updated_at TEXT,
        UNIQUE(date, account_id)
    )
    ''')

    # 11. Stock Industry Table (行业分类持久化 — 由 sync_industry 写入)
    c.execute('''
    CREATE TABLE IF NOT EXISTS stock_industry (
        ts_code TEXT PRIMARY KEY,
        name TEXT,
        tdx_l1 TEXT,
        tdx_l2 TEXT,
        tdx_l3 TEXT,
        anchor_industry TEXT,
        list_date TEXT,
        synced_at TEXT,
        name_initials TEXT DEFAULT ''
    )
    ''')
    # NOTE: name_initials 列的迁移逻辑已移到 migrate.py 的 migrate_stock_industry_columns()

    # 11b. HK Stocks Table (港股主档 — 港股通标的，由 /api/hk-stocks/sync 写入)
    c.execute('''
    CREATE TABLE IF NOT EXISTS hk_stocks (
        ts_code   TEXT PRIMARY KEY,   -- e.g. 00700.HK
        name      TEXT,               -- 股票名称
        sector    TEXT,               -- 行业/板块
        synced_at TEXT
    )
    ''')

    # 11c. IPO Calendar Table (股票打新日历)
    c.execute('''
    CREATE TABLE IF NOT EXISTS ipo_calendar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market TEXT NOT NULL,             -- 沪市主板 / 深市主板 / 创业板 / 科创板 / 北交所
        board TEXT DEFAULT '',
        ts_code TEXT NOT NULL,            -- 统一后缀代码，如 603293.SH / 920012.BJ
        sub_code TEXT DEFAULT '',         -- 申购代码
        name TEXT NOT NULL,
        subscribe_date TEXT NOT NULL,     -- YYYY-MM-DD
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

    # 12. Risk Flags Table (风险标记 — 三负 + 一重大)
    c.execute('''
    CREATE TABLE IF NOT EXISTS risk_flags (
        ts_code TEXT PRIMARY KEY,
        name TEXT,
        industry TEXT,
        debt_ratio_pct REAL,             -- 资产负债率 (%)
        industry_debt_median REAL,       -- 行业负债率中位数 (%)
        ok_debt INTEGER DEFAULT 1,       -- 1=通过(低于行业中位数), 0=不通过
        goodwill_intangibles_pct REAL,   -- (商誉+无形资产)/总资产 (%)
        ok_goodwill INTEGER DEFAULT 1,   -- 1=通过(<30%), 0=不通过
        controller_pledge_pct REAL,      -- 控股股东质押比例 (%)
        ok_pledge INTEGER DEFAULT 1,     -- 1=通过(<30%), 0=不通过
        is_st INTEGER DEFAULT 0,         -- 1=ST/退市
        has_major_event INTEGER DEFAULT 0, -- 1=有重大负面事项
        risk_level TEXT DEFAULT 'low',   -- low/medium/high
        negative_count INTEGER DEFAULT 0, -- 触发的负面条件数
        updated_at TEXT
    )
    ''')

    # 13. Strategies Table (策略定义)
    c.execute('''
    CREATE TABLE IF NOT EXISTS strategies (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT,
        strategy_type TEXT DEFAULT 'value_accumulation',
        selector_type TEXT DEFAULT 'scoring',
        allocator_type TEXT DEFAULT 'dashu_positioning',
        status      TEXT DEFAULT 'active',
        config_path TEXT,
        created_at  TEXT,
        updated_at  TEXT
    )
    ''')

    # 14. Strategy Runs Table (策略运行记录)
    c.execute('''
    CREATE TABLE IF NOT EXISTS strategy_runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id TEXT NOT NULL,
        account_id  TEXT,
        run_date    TEXT NOT NULL,
        run_type    TEXT DEFAULT 'daily',
        signal_count INTEGER DEFAULT 0,
        decision_count INTEGER DEFAULT 0,
        status      TEXT DEFAULT 'completed',
        error_msg   TEXT,
        created_at  TEXT,
        FOREIGN KEY (strategy_id) REFERENCES strategies(id)
    )
    ''')

    # 15. Strategy Signals Table (策略信号 — 候选资格)
    c.execute('''
    CREATE TABLE IF NOT EXISTS strategy_signals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      INTEGER NOT NULL,
        strategy_id TEXT NOT NULL,
        account_id  TEXT,
        run_date    TEXT NOT NULL,
        ts_code     TEXT NOT NULL,
        name        TEXT,
        industry    TEXT,
        signal      TEXT NOT NULL,
        score       REAL,
        pb          REAL,
        pe_ttm      REAL,
        pb_anchor   REAL,
        pb_ratio    REAL,
        rise_from_1y_low_pct REAL,
        reason_code TEXT,
        reason_text TEXT,
        explain_json TEXT,
        created_at  TEXT,
        FOREIGN KEY (run_id) REFERENCES strategy_runs(id)
    )
    ''')

    # 16. Strategy Decisions Table (策略决策 — 最终操作建议)
    c.execute('''
    CREATE TABLE IF NOT EXISTS strategy_decisions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      INTEGER NOT NULL,
        strategy_id TEXT NOT NULL,
        account_id  TEXT,
        run_date    TEXT NOT NULL,
        ts_code     TEXT NOT NULL,
        name        TEXT,
        industry    TEXT,
        action      TEXT NOT NULL,
        quantity    INTEGER DEFAULT 0,
        reason_code TEXT,
        reason_text TEXT,
        blocked_by  TEXT,
        current_price REAL,
        target_amount REAL,
        industry_position_pct REAL,
        total_position_pct REAL,
        explain_json TEXT,
        priority    INTEGER DEFAULT 0,
        created_at  TEXT,
        FOREIGN KEY (run_id) REFERENCES strategy_runs(id)
    )
    ''')

    # 17. Backtests Table (回测任务 — 第二阶段使用)
    c.execute('''
    CREATE TABLE IF NOT EXISTS backtests (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id TEXT NOT NULL,
        name        TEXT,
        start_date  TEXT NOT NULL,
        end_date    TEXT NOT NULL,
        init_cash   REAL DEFAULT 1000000,
        status      TEXT DEFAULT 'pending',
        result_json TEXT,
        created_at  TEXT,
        FOREIGN KEY (strategy_id) REFERENCES strategies(id)
    )
    ''')

    # 18. Backtest Positions Table (回测持仓快照 — 第二阶段使用)
    c.execute('''
    CREATE TABLE IF NOT EXISTS backtest_positions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        backtest_id INTEGER NOT NULL,
        date        TEXT NOT NULL,
        ts_code     TEXT NOT NULL,
        name        TEXT,
        industry    TEXT,
        quantity    INTEGER DEFAULT 0,
        avg_cost    REAL DEFAULT 0,
        market_value REAL DEFAULT 0,
        batch_count INTEGER DEFAULT 0,
        FOREIGN KEY (backtest_id) REFERENCES backtests(id)
    )
    ''')

    # 19. Backtest NAV Table (回测净值曲线)
    c.execute('''
    CREATE TABLE IF NOT EXISTS backtest_nav (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        backtest_id  INTEGER NOT NULL,
        date         TEXT NOT NULL,
        total_nav    REAL,
        cash         REAL,
        market_value REAL,
        position_pct REAL,
        holding_count INTEGER DEFAULT 0,
        FOREIGN KEY (backtest_id) REFERENCES backtests(id)
    )
    ''')

    # 20. Backtest Trades Table (回测交易记录)
    c.execute('''
    CREATE TABLE IF NOT EXISTS backtest_trades (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        backtest_id  INTEGER NOT NULL,
        date         TEXT NOT NULL,
        ts_code      TEXT NOT NULL,
        name         TEXT,
        action       TEXT NOT NULL,
        quantity     INTEGER NOT NULL,
        price        REAL,
        amount       REAL,
        commission   REAL DEFAULT 0,
        stamp_tax    REAL DEFAULT 0,
        reason_text  TEXT,
        FOREIGN KEY (backtest_id) REFERENCES backtests(id)
    )
    ''')

    # 21. T-Trading Items Table (做T观察清单)
    # trade_side: 'BUY' = 买入分析, 'SELL' = 卖出分析
    # buy_price/buy_quantity/buy_date: 通用存储列，SELL 场景下存卖出价/量/日期
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
        trade_side TEXT NOT NULL DEFAULT 'BUY',
        removed_at TEXT DEFAULT NULL,
        created_at TEXT,
        UNIQUE(account_id, transaction_id, trade_side)
    )
    ''')

    # 23. Watchlist Stocks Table (自选股跟踪)
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

    # 24. Drip Sync Progress Table (持续滴灌队列状态)
    c.execute('''
    CREATE TABLE IF NOT EXISTS drip_sync_progress (
        ts_code          TEXT PRIMARY KEY,
        priority         INTEGER DEFAULT 99,
        last_price_date  TEXT,
        last_val_date    TEXT,
        fail_count       INTEGER DEFAULT 0,
        last_fail_at     TEXT,
        last_sync_at     TEXT,
        cycle_seq        INTEGER DEFAULT 0
    )
    ''')

    # 25. AI Briefings Table (AI 持仓简报)
    c.execute('''
    CREATE TABLE IF NOT EXISTS ai_briefings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT NOT NULL,
        run_date TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        name TEXT,
        content TEXT,
        status TEXT DEFAULT 'pending',
        error_msg TEXT,
        duration_sec REAL,
        created_at TEXT,
        UNIQUE(run_date, ts_code)
    )
    ''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_ai_briefings_run_date ON ai_briefings(run_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ai_briefings_batch_id ON ai_briefings(batch_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ai_briefings_created_at ON ai_briefings(created_at)")

    # Insert default strategy: 大数投资
    strategy_now = sh_timestamp_str()
    c.execute('''
        INSERT OR IGNORE INTO strategies (id, name, description, strategy_type, selector_type, allocator_type, status, config_path, created_at, updated_at)
        VALUES ('dashu_v1', '大数投资', '基于PB/PE低估值的组合投资策略，分批建仓，从一年低点计算减仓', 'value_accumulation', 'scoring', 'dashu_positioning', 'active', 'config/strategies/dashu_v1.yaml', ?, ?)
    ''', (strategy_now, strategy_now))

    # Insert default settings if not exist
    default_settings = [
        ('pb_bottom', '1.0', 'PB底部值 (对应最大仓位)'),
        ('pb_average', '2.5', '市场平均PB (对应0%仓位)'),
        ('max_position', '0.95', '最大仓位比例 (保留5%现金)'),
        ('theme', 'dark', '主题: light/dark/system'),
        ('font_size', 'normal', '字体大小: normal/large'),
        ('tushare_token', '', 'Tushare API Token'),
        ('etl_source', 'akshare', '数据入库数据源: tushare/akshare/baostock'),
        ('industry_source', 'pytdx3', '行业分类数据源: pytdx3/excel'),
        ('drip_sync_enabled', '1', '持续滴灌：是否启用 (1=启用, 0=禁用)'),
        ('drip_sync_batch_size', '40', '持续滴灌：每批处理股票数'),
        ('drip_sync_interval_min', '8', '持续滴灌：每批间隔分钟数'),
        ('system_timezone', 'Asia/Shanghai', '系统时区（影响定时任务触发时间）'),
        ('screenshot_keywords', '{"BUY":["买入","证券买入"],"SELL":["卖出","证券卖出"],"DIVIDEND":["利息入账","红利入账","红利派息","股息"],"SPLIT":["送股到账","送股"],"RIGHTS":["配股缴款","配股"],"DIVIDEND_TAX":["红利税","利息税","红利差异税"],"IGNORE":["证券转银行","银行转证券"]}', '截图识别关键词映射（JSON）'),
    ]
    
    for key, value, desc in default_settings:
        c.execute('''
            INSERT OR IGNORE INTO system_settings (key, value, description, updated_at)
            VALUES (?, ?, ?, ?)
        ''', (key, value, desc, sh_timestamp_str()))
    
    conn.commit()
    logger.info(f"✅ Database initialized at {DB_PATH}")

if __name__ == "__main__":
    init_db()
