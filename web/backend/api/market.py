# -*- coding: utf-8 -*-
"""
Market Overview API - 市场概况接口
Provides market data, target position calculation, accounts summary, and stock lookup
"""
from fastapi import APIRouter, HTTPException
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import os
import sys
import tushare as ts
import pandas as pd
import logging
from pypinyin import lazy_pinyin, Style

logger = logging.getLogger(__name__)

_TS_DAILY_BASIC_BACKOFF_UNTIL: Optional[datetime] = None
_TS_DAILY_BASIC_LAST_ERROR: Optional[str] = None

# Initialize Tushare with token
# Priority: settings DB > env var (no hardcoded fallback)
def _get_ts_token():
    try:
        from web.backend.api.settings import get_setting_value
        token = get_setting_value('tushare_token', '')
        if token:
            return token
    except Exception:
        pass
    return os.getenv("TUSHARE_TOKEN", "")

def _init_ts():
    """每次调用前动态读取最新 token，无需重启即可生效"""
    token = _get_ts_token()
    if token:
        ts.set_token(token)
        return True
    logger.warning("Tushare Token 未配置，请在系统设置中设置")
    return False

from web.backend.config import PROJECT_ROOT

from web.backend.database import get_db, get_market_db
from web.backend.api.settings import get_pb_settings
from web.backend.time_utils import sh_now, sh_timestamp_str

router = APIRouter()


def _should_skip_tushare_daily_basic() -> bool:
    global _TS_DAILY_BASIC_BACKOFF_UNTIL
    return bool(_TS_DAILY_BASIC_BACKOFF_UNTIL and sh_now() < _TS_DAILY_BASIC_BACKOFF_UNTIL)


def _remember_tushare_daily_basic_failure(message: str, cooldown_minutes: int = 10) -> None:
    global _TS_DAILY_BASIC_BACKOFF_UNTIL, _TS_DAILY_BASIC_LAST_ERROR
    _TS_DAILY_BASIC_LAST_ERROR = message
    _TS_DAILY_BASIC_BACKOFF_UNTIL = sh_now() + timedelta(minutes=cooldown_minutes)


def _get_tushare_failure_cooldown_minutes(message: str) -> int:
    text = (message or "").lower()
    # 权限不足通常是稳定配置问题，不是瞬时抖动，冷却时间要明显长于普通失败。
    if ("没有接口访问权限" in message) or ("permission" in text and "denied" in text):
        return 24 * 60
    return 10


def _clear_tushare_daily_basic_failure() -> None:
    global _TS_DAILY_BASIC_BACKOFF_UNTIL, _TS_DAILY_BASIC_LAST_ERROR
    _TS_DAILY_BASIC_BACKOFF_UNTIL = None
    _TS_DAILY_BASIC_LAST_ERROR = None

# --- Global Stock Cache ---
STOCK_CACHE = {
    "list": [],           # List of {ts_code, name, industry}
    "map": {},            # ts_code -> info
    "map_no_suffix": {}   # 000001 -> info
}

def load_stock_cache():
    """Load stock list from CSV on startup"""
    global STOCK_CACHE
    csv_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    
    if not os.path.exists(csv_path):
        logger.warning(f"Warning: {csv_path} not found. Stock lookup will rely on Tushare.")
        return

    try:
        # Read CSV with str dtype to preserve leading zeros
        df = pd.read_csv(csv_path, dtype=str)
        
        # Ensure columns exist
        if 'ts_code' not in df.columns or 'name' not in df.columns:
            logger.error("Error: CSV missing required columns (ts_code, name)")
            return
            
        # Fill NaN
        df = df.fillna('')
        
        # Build cache
        STOCK_CACHE['list'] = df.to_dict('records')

        for record in STOCK_CACHE['list']:
            ts_code = record['ts_code']
            STOCK_CACHE['map'][ts_code] = record

            # Map without suffix (e.g., 000001)
            code_no_suffix = ts_code.split('.')[0]
            STOCK_CACHE['map_no_suffix'][code_no_suffix] = record

            # Pre-compute pinyin initials for name (e.g., 招商银行 -> zsyh)
            try:
                record['_initials'] = ''.join(lazy_pinyin(record['name'], style=Style.FIRST_LETTER)).lower()
            except Exception:
                record['_initials'] = ''
            
        logger.info(f"Loaded {len(STOCK_CACHE['list'])} stocks into cache")
        
    except Exception as e:
        logger.error(f"Error loading stock cache: {e}")

# Load cache immediately
load_stock_cache()

# --- Target Position Calculation ---

def calculate_target_position(pb: float) -> float:
    """
    Calculate target position based on market PB.
    Uses dynamic settings from database.
    Formula: (pb_average - PB) / (pb_average - pb_bottom) * max_position
    """
    settings = get_pb_settings()
    pb_bottom = settings["pb_bottom"]
    pb_average = settings["pb_average"]
    max_position = settings["max_position"]
    
    if pb >= pb_average:
        return 0.0
    if pb <= pb_bottom:
        return max_position
    
    position = (pb_average - pb) / (pb_average - pb_bottom) * max_position
    return round(position, 4)

# --- Market Data APIs ---

def _get_current_from_market_db() -> Optional[dict]:
    """Fallback: read latest market PB from local market_monthly table."""
    try:
        with get_market_db() as conn:
            if not conn:
                return None
            c = conn.cursor()
            c.execute(
                "SELECT year_month, pb_close, trade_date, total_mv_high "
                "FROM market_monthly ORDER BY year_month DESC LIMIT 1"
            )
            row = c.fetchone()
            if not row:
                return None
            pb = float(row[1]) if row[1] else 0
            trade_date = row[2] or row[0]
            if pb <= 0:
                return None
            target_position = calculate_target_position(pb)
            return {
                "trade_date": trade_date,
                "pb": round(pb, 3),
                "target_position": target_position,
                "target_position_pct": f"{target_position * 100:.1f}%",
                "source": "local_db"
            }
    except Exception as e:
        logger.warning(f"market.db fallback failed: {e}")
        return None


@router.get("/market/current")
def get_current_market():
    """Get latest A-share market data. Tries Tushare first, falls back to market.db."""
    tushare_error = _TS_DAILY_BASIC_LAST_ERROR
    try:
        if not _init_ts():
            raise ValueError("Tushare Token 未配置")
        if _should_skip_tushare_daily_basic():
            raise ValueError(f"Tushare daily_basic 暂时跳过，最近错误：{_TS_DAILY_BASIC_LAST_ERROR or 'unknown'}")
        pro = ts.pro_api()

        # Try recent trading days (markets may be closed)
        for days_back in range(0, 10):
            trade_date = (sh_now() - timedelta(days=days_back)).strftime("%Y%m%d")
            df = pro.daily_basic(
                trade_date=trade_date,
                fields='ts_code,total_mv,pb'
            )
            if df is not None and not df.empty:
                break
        else:
            raise ValueError("No recent trading data from Tushare")

        # Filter A-shares only: SH (60xxxx, 68xxxx) + SZ (00xxxx, 30xxxx)
        # Exclude: B-shares (2xxxxx.SH, 9xxxxx.SZ), Beijing (xxxxx.BJ)
        a_shares = df[
            (df['ts_code'].str.endswith('.SH') | df['ts_code'].str.endswith('.SZ')) &
            ~df['ts_code'].str.startswith('2') &
            ~df['ts_code'].str.startswith('9')
        ].copy()

        a_shares['total_mv'] = pd.to_numeric(a_shares['total_mv'], errors='coerce').fillna(0)
        a_shares['pb'] = pd.to_numeric(a_shares['pb'], errors='coerce').fillna(0)

        # daily_basic total_mv unit is 万元
        total_mv_wanyuan = a_shares['total_mv'].sum()
        total_mv_wanyi = total_mv_wanyuan / 1e8  # 万元 -> 万亿

        # Net assets per stock = total_mv / pb (where pb > 0)
        valid = a_shares[a_shares['pb'] > 0].copy()
        valid['net_assets'] = valid['total_mv'] / valid['pb']
        total_net_wanyuan = valid['net_assets'].sum()
        total_net_wanyi = total_net_wanyuan / 1e8

        combined_pb = total_mv_wanyi / total_net_wanyi if total_net_wanyi > 0 else 0
        target_position = calculate_target_position(combined_pb)
        _clear_tushare_daily_basic_failure()

        # Split by exchange
        sh = a_shares[a_shares['ts_code'].str.endswith('.SH')]
        sz = a_shares[a_shares['ts_code'].str.endswith('.SZ')]

        return {
            "status": "success",
            "data": {
                "trade_date": trade_date,
                "total_mv": round(total_mv_wanyi, 2),
                "net_assets": round(total_net_wanyi, 2),
                "pb": round(combined_pb, 3),
                "target_position": target_position,
                "target_position_pct": f"{target_position * 100:.1f}%",
                "sh_mv": round(sh['total_mv'].sum() / 1e8, 2),
                "sz_mv": round(sz['total_mv'].sum() / 1e8, 2),
                "stock_count": len(a_shares)
            }
        }

    except Exception as e:
        tushare_error = str(e)
        if "daily_basic 暂时跳过" not in tushare_error:
            _remember_tushare_daily_basic_failure(
                tushare_error,
                cooldown_minutes=_get_tushare_failure_cooldown_minutes(tushare_error),
            )
        logger.warning(f"Tushare daily_basic failed, falling back to market.db: {e}")

    # Fallback: read from local market.db
    fallback = _get_current_from_market_db()
    if fallback:
        return {"status": "success", "data": fallback}

    return {"status": "error", "message": f"行情数据暂不可用（{tushare_error}）"}

@router.get("/market/monthly")
def get_monthly_history(years: int = 5):
    """Get monthly market history for chart"""
    with get_market_db() as conn:
        if not conn:
            return {"status": "error", "message": "market.db not found"}
        c = conn.cursor()
        
        # Calculate start date (N years ago)
        start_month = (sh_now() - timedelta(days=years*365)).strftime("%Y-%m")
        
        c.execute('''
            SELECT * FROM market_monthly 
            WHERE year_month >= ?
            ORDER BY year_month ASC
        ''', (start_month,))
        
        rows = c.fetchall()
    
    data = []
    for row in rows:
        data.append({
            "year_month": row["year_month"],
            "total_mv_high": row["total_mv_high"],
            "total_mv_low": row["total_mv_low"],
            "pb_high": row["pb_high"],
            "pb_low": row["pb_low"],
            "pb_close": row["pb_close"],
            "net_assets": row["net_assets"]
        })
    
    return {"status": "success", "data": data, "count": len(data)}

@router.post("/market/sync")
def sync_market_data(start_date: Optional[str] = None):
    """Sync market monthly data from Tushare (SH + SZ combined)"""
    from web.backend.api.market_kline import _ensure_market_monthly
    _ensure_market_monthly()
    try:
        _init_ts()
        pro = ts.pro_api()

        # Default: last 6 years
        if not start_date:
            start_date = (sh_now() - timedelta(days=6*365)).strftime("%Y%m%d")
        end_date = sh_now().strftime("%Y%m%d")
        
        # Fetch Shanghai Index (000001.SH)
        sh_df = pro.index_dailybasic(
            ts_code='000001.SH',
            start_date=start_date,
            end_date=end_date,
            fields='trade_date,total_mv,pb'
        )
        
        # Fetch Shenzhen Component (399001.SZ)
        sz_df = pro.index_dailybasic(
            ts_code='399001.SZ',
            start_date=start_date,
            end_date=end_date,
            fields='trade_date,total_mv,pb'
        )
        
        if sh_df.empty or sz_df.empty:
            return {"status": "error", "message": "Failed to fetch market data from Tushare"}
        
        # Merge SH + SZ by trade_date
        sh_df = sh_df.rename(columns={'total_mv': 'sh_mv', 'pb': 'sh_pb'})
        sz_df = sz_df.rename(columns={'total_mv': 'sz_mv', 'pb': 'sz_pb'})
        
        merged = pd.merge(sh_df, sz_df, on='trade_date', how='inner')
        
        # Calculate combined values
        merged['total_mv'] = merged['sh_mv'] + merged['sz_mv']
        merged['sh_net'] = merged['sh_mv'] / merged['sh_pb']
        merged['sz_net'] = merged['sz_mv'] / merged['sz_pb']
        merged['total_net'] = merged['sh_net'] + merged['sz_net']
        merged['combined_pb'] = merged['total_mv'] / merged['total_net']
        
        # Add year_month column
        merged['year_month'] = merged['trade_date'].str[:4] + '-' + merged['trade_date'].str[4:6]
        
        # Aggregate by month
        monthly = merged.groupby('year_month').agg({
            'total_mv': ['max', 'min', 'last'],
            'combined_pb': ['max', 'min', 'last'],
            'total_net': 'last',
            'trade_date': 'max'  # Last trading day of month
        }).reset_index()
        
        # Flatten column names
        monthly.columns = ['year_month', 'mv_high', 'mv_low', 'mv_close', 
                          'pb_high', 'pb_low', 'pb_close', 'net_assets', 'trade_date']
        
        # Insert into database (market.db)
        with get_market_db() as conn:
            if not conn:
                return {"status": "error", "message": "market.db not found"}
            c = conn.cursor()
            
            inserted = 0
            updated = 0
            
            for _, row in monthly.iterrows():
                try:
                    updated_at = sh_timestamp_str()
                    c.execute('''
                        INSERT INTO market_monthly 
                        (year_month, total_mv_high, total_mv_low, pb_high, pb_low, pb_close, net_assets, trade_date, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(year_month) DO UPDATE SET
                            total_mv_high = excluded.total_mv_high,
                            total_mv_low = excluded.total_mv_low,
                            pb_high = excluded.pb_high,
                            pb_low = excluded.pb_low,
                            pb_close = excluded.pb_close,
                            net_assets = excluded.net_assets,
                            trade_date = excluded.trade_date,
                            updated_at = ?
                    ''', (
                        row['year_month'],
                        round(row['mv_high'] / 1e12, 2),
                        round(row['mv_low'] / 1e12, 2),
                        round(row['pb_high'], 3),
                        round(row['pb_low'], 3),
                        round(row['pb_close'], 3),
                        round(row['net_assets'] / 1e12, 2),
                        row['trade_date'],
                        updated_at,
                        updated_at,
                    ))
                    inserted += 1
                except Exception as e:
                    logger.error(f"Error inserting {row['year_month']}: {e}")
            
            conn.commit()
        
        return {
            "status": "success",
            "message": f"Synced {inserted} months of market data",
            "months": inserted
        }
        
    except Exception as e:
        import traceback
        return {"status": "error", "message": str(e), "trace": traceback.format_exc()}

# --- Accounts Summary API ---

@router.get("/market/accounts-summary")
def get_accounts_summary():
    """Get summary of all accounts with market value"""
    with get_db() as conn:
        c = conn.cursor()
        
        c.execute('''
            SELECT id, name, broker_id, cash_balance
            FROM accounts
        ''')
        
        accounts = c.fetchall()
    
    if not accounts:
        return {
            "status": "success",
            "data": {
                "account_count": 0,
                "total_market_value": 0,
                "total_cash": 0,
                "total_assets": 0,
                "position_ratio": 0
            }
        }
    
    total_mv = 0
    total_cash = 0
    
    for acc in accounts:
        cash = acc["cash_balance"] or 0
        total_cash += cash
        
        # Compute holdings market value using analysis
        try:
            from web.backend.api.analysis import analyze_holdings
            result = analyze_holdings(acc["id"])
            if result.get("status") == "success":
                holdings = result.get("holdings", [])
                if holdings:
                    acc_mv = sum(item.get("market_value", 0) for item in holdings)
                    total_mv += acc_mv
        except Exception as e:
            logger.error(f"Error computing MV for account {acc['id']}: {e}")
    
    total_assets = total_mv + total_cash
    position_ratio = (total_mv / total_assets * 100) if total_assets > 0 else 0
    
    return {
        "status": "success",
        "data": {
            "account_count": len(accounts),
            "total_market_value": round(total_mv, 2),
            "total_cash": round(total_cash, 2),
            "total_assets": round(total_assets, 2),
            "position_ratio": round(position_ratio, 2),
            "accounts": [{"id": a["id"], "name": a["name"], "cash": a["cash_balance"] or 0} for a in accounts]
        }
    }

@router.get("/market/target-position")
def get_target_position():
    """Get current market PB and target position"""
    result = get_current_market()
    
    if result.get("status") != "success":
        return result
    
    data = result["data"]
    return {
        "status": "success",
        "data": {
            "pb": data["pb"],
            "target_position": data["target_position"],
            "target_position_pct": data["target_position_pct"],
            "formula": "(2.5 - PB) / 1.5 * 95%"
        }
    }

# --- Stock Info & Search API ---

@router.get("/market/search")
def search_stocks(q: str):
    """Search stocks by code or name (local cache + hk_stocks table)"""
    q = q.strip().upper()
    if not q:
        return {"status": "success", "data": []}

    results = []
    q_lower = q.lower()

    # A-share search from STOCK_CACHE
    for stock in STOCK_CACHE['list']:
        if (q in stock['ts_code'] or q in stock['name']
                or q_lower in stock.get('_initials', '')):
            results.append({
                "ts_code": stock['ts_code'],
                "name": stock['name'],
                "industry": stock.get('industry', ''),
                "value": stock['ts_code'],
                "label": f"{stock['ts_code']} {stock['name']}",
            })
            if len(results) >= 20:
                break

    # HK stock search from hk_stocks table (append up to 10 results)
    if len(results) < 20:
        try:
            from web.backend.database import get_db
            pattern = f"%{q}%"
            with get_db() as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT ts_code, name FROM hk_stocks "
                    "WHERE ts_code LIKE ? OR name LIKE ? ORDER BY ts_code LIMIT ?",
                    (pattern, pattern, 20 - len(results)),
                )
                for row in c.fetchall():
                    results.append({
                        "ts_code": row["ts_code"],
                        "name": row["name"] or "",
                        "industry": "港股",
                        "value": row["ts_code"],
                        "label": f"{row['ts_code']} {row['name'] or ''}",
                    })
        except Exception as e:
            logger.warning(f"search_stocks HK fallback failed: {e}")

    return {"status": "success", "data": results}

@router.get("/stocks/info/{ts_code}")
def get_stock_info(ts_code: str):
    """Get stock basic info and current price (Cache + Tushare)"""
    try:
        raw_code = ts_code
        ts_code = ts_code.upper().strip()
        logger.debug(f"get_stock_info received '{raw_code}' -> '{ts_code}' (len={len(ts_code)})")
        
        # Check if cache is loaded
        if not STOCK_CACHE['map']:
            logger.debug("Cache is empty!")

        # 1. Look up in local cache
        name = ""
        industry = ""
        cached_info = STOCK_CACHE['map'].get(ts_code)
        
        if cached_info:
            logger.debug(f"Found {ts_code} in cache")
        else:
            logger.debug(f"{ts_code} NOT found in cache map keys (sample: {list(STOCK_CACHE['map'].keys())[:5]})")
        
        # If not found, try no suffix (e.g. 600519)
        if not cached_info and '.' not in ts_code:
            cached_info = STOCK_CACHE['map_no_suffix'].get(ts_code)
            if cached_info:
                logger.debug(f"Found {ts_code} in no-suffix map -> {cached_info['ts_code']}")
                ts_code = cached_info['ts_code'] # Use full code
        
        if cached_info:
            name = cached_info['name']
            industry = cached_info.get('industry', '')
            # If we found in cache, we trust it exists.
        else:
            # Fallback to Tushare if not in cache (e.g. new stock not in CSV)
             # Get stock basic info - query all and filter
            _init_ts()
            pro = ts.pro_api()
            df_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
            stock_row = df_basic[df_basic['ts_code'] == ts_code]
            
            if stock_row.empty:
                 # Try adding suffix if missing
                if '.' not in ts_code:
                    if ts_code.startswith('6'): suffix = '.SH'
                    elif ts_code.startswith('0') or ts_code.startswith('3'): suffix = '.SZ'
                    elif ts_code.startswith('4') or ts_code.startswith('8'): suffix = '.BJ'
                    else: suffix = ''
                    
                    if suffix:
                        ts_code_suffix = ts_code + suffix
                        stock_row = df_basic[df_basic['ts_code'] == ts_code_suffix]
                        if not stock_row.empty:
                            ts_code = ts_code_suffix
            
            if not stock_row.empty:
                 name = stock_row.iloc[0]['name']
                 industry = stock_row.iloc[0]['industry']
            else:
                # 第三层 fallback：实时行情（覆盖可转债等 tushare 不收录的品种）
                try:
                    from web.backend.services.quote_manager import quote_manager
                    qt = quote_manager.fetch([ts_code])
                    q = (qt or {}).get(ts_code)
                    if q and q.get("price", 0) > 0:
                        name = q.get("name") or ts_code
                        industry = ""
                        return {"status": "success", "data": {"ts_code": ts_code, "name": name, "industry": industry, "price": q["price"]}}
                except Exception as _qe:
                    logger.warning(f"quote fallback for {ts_code} failed: {_qe}")
                return {"status": "error", "message": "Stock not found"}

        # 2. Get current price from Tushare
        price = 0.0
        try:
            _init_ts()
            pro = ts.pro_api() # init api
            today = sh_now().strftime('%Y%m%d')
            start = (sh_now() - timedelta(days=10)).strftime('%Y%m%d') # look back 10 days for trading halt
            
            df_price = pro.daily(ts_code=ts_code, start_date=start, end_date=today, fields='close')
            if not df_price.empty:
                price = float(df_price.iloc[0]['close'])
        except Exception as e:
            logger.error(f"Error fetching price for {ts_code}: {e}")
            # Non-fatal, return info without price (or 0)
        
        return {
            "status": "success",
            "data": {
                "ts_code": ts_code,
                "name": name,
                "industry": industry,
                "price": price
            }
        }
        
    except Exception as e:
        import traceback
        logger.info(traceback.format_exc())
        return {"status": "error", "message": str(e)}
# --- K-Line & Valuation APIs moved to market_kline.py ---
