# -*- coding: utf-8 -*-
from fastapi import APIRouter, HTTPException
from web.backend.database import get_db
from web.backend.utils import dict_factory
import pandas as pd
import os
import logging
from web.backend.time_utils import sh_date_str, sh_timestamp_str

logger = logging.getLogger(__name__)

from web.backend.config import PROJECT_ROOT

router = APIRouter()

def get_market_prices():
    """Load latest prices from market_enriched.csv"""
    prices = {}
    try:
        path = os.path.join(PROJECT_ROOT, "market_enriched.csv")
        if os.path.exists(path):
            df = pd.read_csv(path, dtype={"ts_code": str})
            price_col = 'latest' if 'latest' in df.columns else 'close'
            if price_col in df.columns:
                for _, row in df.iterrows():
                     val = pd.to_numeric(row[price_col], errors='coerce')
                     if pd.notna(val) and val > 0:
                         prices[row['ts_code']] = float(val)
    except Exception as e:
        logger.error(f"Error loading market prices: {e}")
    return prices

@router.post("/assets/snapshot")
def snapshot_assets(account_id: str = None):
    """Take a snapshot of current assets for specific account (or all)."""
    with get_db() as conn:
        c = conn.cursor()
        
        current_date = sh_date_str()
        updated_at = sh_timestamp_str()
        
        if account_id and account_id != 'all':
            acc_list = [(account_id,)]
        else:
            c.execute("SELECT id FROM accounts")
            acc_list = c.fetchall()
            
        prices = get_market_prices()
        results = []
        
        for acc_row in acc_list:
            aid = acc_row[0] if isinstance(acc_row, tuple) else acc_row['id']
            
            c.execute("SELECT cash_balance FROM accounts WHERE id = ?", (aid,))
            acc_data = c.fetchone()
            cash = acc_data['cash_balance'] if acc_data else 0.0
            
            c.execute("SELECT ts_code, quantity, total_cost FROM holdings WHERE account_id = ?", (aid,))
            holdings = c.fetchall()
            
            mv_total = 0.0
            cost_total = 0.0
            
            for h in holdings:
                qty = h['quantity']
                code = h['ts_code']
                cost_total += h['total_cost']
                price = prices.get(code, 0.0)
                mv_total += qty * price
            
            total_assets = cash + mv_total
            
            c.execute('''
                INSERT INTO asset_history (date, account_id, total_assets, total_market_value, cash_balance, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, account_id) DO UPDATE SET
                total_assets = excluded.total_assets,
                total_market_value = excluded.total_market_value,
                cash_balance = excluded.cash_balance,
                updated_at = excluded.updated_at
            ''', (current_date, aid, total_assets, mv_total, cash, updated_at))
            
            results.append({
                "account_id": aid,
                "total_assets": total_assets,
                "market_value": mv_total,
                "cash": cash
            })
            
        conn.commit()
        return {"status": "success", "data": results}

@router.get("/assets/history")
def get_asset_history(account_id: str = None, days: int = 30):
    """Get asset history for charting"""
    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()
        
        query = "SELECT * FROM asset_history"
        params = []
        
        if account_id and account_id != 'all':
            query += " WHERE account_id = ?"
            params.append(account_id)
            
        query += " ORDER BY date ASC"
        
        c.execute(query, params)
        rows = c.fetchall()
        
        return {"status": "success", "data": rows}
