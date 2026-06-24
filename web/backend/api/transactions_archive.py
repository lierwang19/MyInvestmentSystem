# -*- coding: utf-8 -*-
"""
Archive APIs — manages cleared/archived stock holdings.
Extracted from transactions.py for cleaner module separation.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

from web.backend.database import get_db
from web.backend.utils import valid_float
from web.backend.time_utils import sh_date_str, sh_timestamp_str

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Models ---

class ArchiveRequest(BaseModel):
    ts_code: str
    stock_name: Optional[str] = None

class ManualArchiveEntry(BaseModel):
    ts_code: str
    stock_name: str
    total_buy_amount: float = 0
    total_sell_amount: float = 0
    total_dividend: float = 0
    total_fees: float = 0
    total_profit: float = 0
    profit_pct: float = 0
    first_buy_date: Optional[str] = None
    last_sell_date: Optional[str] = None


# --- Endpoints ---

@router.get("/archived/{account_id}")
def get_archived_holdings(account_id: str):
    """Get all archived (cleared) holdings for an account"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT * FROM archived_holdings 
            WHERE account_id = ?
            ORDER BY archived_at DESC
        ''', (account_id,))
        rows = c.fetchall()
    
    archived = []
    for row in rows:
        archived.append({
            "id": row["id"],
            "ts_code": row["ts_code"],
            "stock_name": row["stock_name"],
            "total_buy_amount": valid_float(row["total_buy_amount"]),
            "total_sell_amount": valid_float(row["total_sell_amount"]),
            "total_dividend": valid_float(row["total_dividend"]),
            "total_profit": valid_float(row["total_profit"]),
            "profit_pct": valid_float(row["profit_pct"]),
            "total_fees": valid_float(row["total_fees"]),
            "first_buy_date": row["first_buy_date"],
            "last_sell_date": row["last_sell_date"],
            "archived_at": row["archived_at"]
        })
    
    return {"status": "success", "data": archived}


@router.post("/archived/{account_id}/manual")
def add_manual_archive(account_id: str, entry: ManualArchiveEntry):
    """Manually add an archived stock record"""
    archived_at = sh_timestamp_str()
    with get_db() as conn:
        c = conn.cursor()
        
        c.execute('SELECT id FROM archived_holdings WHERE account_id = ? AND ts_code = ?', 
                  (account_id, entry.ts_code))
        if c.fetchone():
            raise HTTPException(status_code=400, detail=f"Stock {entry.ts_code} already exists in archived holdings")
        
        c.execute('''
            INSERT INTO archived_holdings 
            (account_id, ts_code, stock_name, total_buy_amount, total_sell_amount, 
             total_dividend, total_profit, profit_pct, total_fees, 
             first_buy_date, last_sell_date, archived_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            account_id, entry.ts_code, entry.stock_name,
            round(entry.total_buy_amount, 2), round(entry.total_sell_amount, 2),
            round(entry.total_dividend, 2), round(entry.total_profit, 2),
            round(entry.profit_pct, 2), round(entry.total_fees, 2),
            entry.first_buy_date, entry.last_sell_date, archived_at
        ))
        conn.commit()
    
    return {
        "status": "success",
        "message": f"Archived record for {entry.stock_name} ({entry.ts_code}) added successfully"
    }


@router.post("/archived/{account_id}")
def archive_holding(account_id: str, req: ArchiveRequest):
    """Archive a cleared stock (upsert — idempotent).

    清仓时 _recalculate_archived_holdings 已自动写入一条记录；
    用户手动点击「归档」只会刷新统计数据，不会产生重复行。
    """
    # Import get_stock_summary from transactions module (cross-module dep)
    from web.backend.api.transactions import get_stock_summary
    
    with get_db() as conn:
        c = conn.cursor()
        
        # 1. Try to get summary from transactions
        summary_result = get_stock_summary(account_id, req.ts_code)
        data = summary_result.get("data") if summary_result.get("status") == "success" else None
        
        # 2. Check current holding status
        c.execute('SELECT * FROM holdings WHERE account_id = ? AND ts_code = ?', (account_id, req.ts_code))
        holding_row = c.fetchone()
        
        if not holding_row and not data:
            raise HTTPException(status_code=404, detail="Holding not found")
            
        current_qty = holding_row['quantity'] if holding_row else (data['current_quantity'] if data else 0)
        
        if current_qty > 0:
            raise HTTPException(status_code=400, detail=f"Cannot archive: still holding {current_qty} shares")
        
        # 3. Prepare archive data
        total_buy = total_sell = total_dividend = total_fees = profit = 0
        first_buy_date = last_sell_date = None

        if data:
            total_buy = data["total_buy_amount"]
            total_sell = data["total_sell_amount"]
            total_dividend = data["total_dividend"]
            total_fees = data["total_fees"]
            profit = data["realized_pnl"]
            first_buy_date = data["first_buy_date"]
            last_sell_date = data["last_sell_date"]
            
            # Fix for incomplete history
            if data['current_quantity'] != 0:
                implied_buy = total_sell + total_dividend - profit
                if abs(implied_buy - total_buy) > 1.0:
                    logger.info(f"Adjusting Total Buy for incomplete history. Old={total_buy}, New={implied_buy}")
                    total_buy = implied_buy
        elif holding_row:
            profit = -holding_row['total_cost']
            if profit >= 0:
                total_buy = 0; total_sell = profit
            else:
                total_buy = holding_row['total_cost']; total_sell = 0
            first_buy_date = holding_row['updated_at'].split(' ')[0] if holding_row['updated_at'] else None
            last_sell_date = sh_date_str()
            
        profit_pct = 0
        if valid_float(total_buy) > 0:
            profit_pct = (profit / total_buy * 100)
        elif valid_float(profit) > 0 and total_buy == 0:
            profit_pct = 100 
            
        # 4. Upsert archived_holdings.
        # Do not rely on UNIQUE(account_id, ts_code) being present in old user DBs.
        archived_at = sh_timestamp_str()
        archived_payload = (
            req.stock_name or req.ts_code,
            valid_float(round(total_buy, 2)),
            valid_float(round(total_sell, 2)),
            valid_float(round(total_dividend, 2)),
            valid_float(round(profit, 2)),
            valid_float(round(profit_pct, 2)),
            valid_float(round(total_fees, 2)),
            first_buy_date,
            last_sell_date,
            archived_at,
        )

        c.execute(
            'SELECT id FROM archived_holdings WHERE account_id = ? AND ts_code = ? ORDER BY id DESC LIMIT 1',
            (account_id, req.ts_code),
        )
        existing = c.fetchone()

        if existing:
            archive_id = existing["id"]
            c.execute('''
                UPDATE archived_holdings
                SET stock_name = ?, total_buy_amount = ?, total_sell_amount = ?,
                    total_dividend = ?, total_profit = ?, profit_pct = ?, total_fees = ?,
                    first_buy_date = ?, last_sell_date = ?, archived_at = ?
                WHERE id = ?
            ''', (*archived_payload, archive_id))
        else:
            c.execute('''
                INSERT INTO archived_holdings
                (account_id, ts_code, stock_name, total_buy_amount, total_sell_amount,
                 total_dividend, total_profit, profit_pct, total_fees, first_buy_date, last_sell_date, archived_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                account_id,
                req.ts_code,
                *archived_payload,
            ))
            archive_id = c.lastrowid

        # 5. 仅当持仓数量为 0 时才从 holdings 删除（已清仓记录）
        if holding_row and holding_row['quantity'] == 0:
            c.execute('DELETE FROM holdings WHERE account_id = ? AND ts_code = ?', (account_id, req.ts_code))
        
        conn.commit()
    
    return {
        "status": "success",
        "message": f"Stock {req.ts_code} archived successfully",
        "id": archive_id,
        "profit": valid_float(round(profit, 2)),
        "profit_pct": valid_float(round(profit_pct, 2))
    }


@router.put("/archived/{account_id}/{id}")
def update_archived_holding(account_id: str, id: int, entry: ManualArchiveEntry):
    """Update an existing archived holding record"""
    with get_db() as conn:
        c = conn.cursor()
        
        c.execute('SELECT id FROM archived_holdings WHERE id = ? AND account_id = ?', (id, account_id))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Archived record not found")
        
        c.execute('''
            UPDATE archived_holdings 
            SET ts_code = ?, stock_name = ?, total_buy_amount = ?, total_sell_amount = ?, 
                total_dividend = ?, total_profit = ?, profit_pct = ?, total_fees = ?, 
                first_buy_date = ?, last_sell_date = ?
            WHERE id = ?
        ''', (
            entry.ts_code, entry.stock_name,
            round(entry.total_buy_amount, 2), round(entry.total_sell_amount, 2),
            round(entry.total_dividend, 2), round(entry.total_profit, 2),
            round(entry.profit_pct, 2), round(entry.total_fees, 2),
            entry.first_buy_date, entry.last_sell_date, id
        ))
        conn.commit()
    
    return {
        "status": "success",
        "message": f"Archived record for {entry.stock_name} updated successfully"
    }


@router.delete("/archived/{account_id}/{id}")
def delete_archived_holding(account_id: str, id: int):
    """Delete an archived holding record by id"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT ts_code FROM archived_holdings WHERE id = ? AND account_id = ?', (id, account_id))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Archived record not found")

        c.execute('DELETE FROM archived_holdings WHERE id = ? AND account_id = ?', (id, account_id))
        conn.commit()

    return {
        "status": "success",
        "message": f"Archived record {id} deleted successfully"
    }
