# -*- coding: utf-8 -*-
"""
Transaction & Holdings CRUD APIs
- Create/Read/Delete transactions (BUY/SELL/SPLIT/RIGHTS/DIVIDEND)
- Stock summary for detail dialog
- Holdings cost update
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import logging

from web.backend.database import get_db
from web.backend.utils import valid_float
from web.backend.time_utils import sh_date_str, sh_timestamp_str, sh_today

logger = logging.getLogger(__name__)
router = APIRouter()

# --- Models ---

class TransactionCreate(BaseModel):
    ts_code: str
    name: str
    trans_type: str  # BUY, SELL, SPLIT, RIGHTS, DIVIDEND
    price: float
    amount: int
    date: str
    fee: float = 0.0
    stamp_tax: float = 0.0
    buy_pb: Optional[float] = None   # PB at buy time; stored for from_buy_pb sell bucketing
    remarks: Optional[str] = None

class CostUpdateRequest(BaseModel):
    cost_price: float
    total_cost: float
    quantity: Optional[int] = None   # if provided, also update quantity (manual override)


class ArchiveRequest(BaseModel):
    ts_code: str
    stock_name: Optional[str] = None


def archive_holding(account_id: str, req: ArchiveRequest):
    """
    Backward-compatible shim for callers that still import archive APIs from
    transactions.py after the archive module split.
    """
    from web.backend.api.transactions_archive import archive_holding as archive_holding_impl

    return archive_holding_impl(account_id, req)

# --- Transaction APIs ---

@router.get("/transactions/{account_id}")
def get_transactions(account_id: str, ts_code: Optional[str] = None):
    """Get all transactions for an account, optionally filtered by stock"""
    with get_db() as conn:
        c = conn.cursor()
        
        if ts_code:
            c.execute('''
                SELECT * FROM transactions 
                WHERE account_id = ? AND ts_code = ?
                ORDER BY date DESC, id DESC
            ''', (account_id, ts_code))
        else:
            c.execute('''
                SELECT * FROM transactions 
                WHERE account_id = ?
                ORDER BY date DESC, id DESC
            ''', (account_id,))
        
        rows = c.fetchall()
    
    transactions = []
    for row in rows:
        transactions.append({
            "id": row["id"],
            "account_id": row["account_id"],
            "ts_code": row["ts_code"],
            "name": row["name"],
            "trans_type": row["trans_type"],
            "price": row["price"],
            "amount": row["amount"],
            "total_amt": row["total_amt"],
            "fee": row["fee"],
            "stamp_tax": row["stamp_tax"],
            "date": row["date"],
            "remarks": row["remarks"],
            "created_at": row["created_at"]
        })
    
    return {"status": "success", "data": transactions}

@router.post("/transactions/{account_id}")
def create_transaction(account_id: str, trans: TransactionCreate):
    """Create a new transaction (Buy/Sell/Dividend)"""
    now_ts = sh_timestamp_str()
    with get_db() as conn:
        c = conn.cursor()
        
        # Get account's broker settings for fee calculation
        c.execute('''
            SELECT b.commission_rate, b.min_commission, b.stamp_tax_rate, b.transfer_fee_rate
            FROM accounts a
            LEFT JOIN brokers b ON a.broker_id = b.id
            WHERE a.id = ?
        ''', (account_id,))
        broker_row = c.fetchone()
        
        # Default rates if no broker linked
        commission_rate = 0.0003
        min_commission = 5.0
        stamp_tax_rate = 0.001
        transfer_fee_rate = 0.00002
        
        if broker_row and broker_row[0] is not None:
            commission_rate = broker_row[0]
            min_commission = broker_row[1]
            stamp_tax_rate = broker_row[2]
            transfer_fee_rate = broker_row[3] if len(broker_row) > 3 and broker_row[3] is not None else transfer_fee_rate
        
        # Calculate total amount
        total_amt = abs(trans.price * trans.amount)
        
        # Auto-calculate fees using broker rates if not provided
        fee = trans.fee
        stamp_tax = trans.stamp_tax
        is_hk = trans.ts_code.endswith(".HK")
        transfer_fee = 0.0 if is_hk else total_amt * transfer_fee_rate
        
        if trans.trans_type == 'BUY' and fee == 0:
            fee = max(total_amt * commission_rate, min_commission) + transfer_fee
        elif trans.trans_type == 'SELL':
            if fee == 0:
                fee = max(total_amt * commission_rate, min_commission) + transfer_fee
            if stamp_tax == 0:
                stamp_tax = total_amt * stamp_tax_rate
        
        try:
            c.execute('''
                INSERT INTO transactions
                (account_id, ts_code, name, trans_type, price, amount, total_amt, fee, stamp_tax, date, remarks, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                account_id, trans.ts_code, trans.name, trans.trans_type,
                trans.price, trans.amount, total_amt,
                round(fee, 2), round(stamp_tax, 2), trans.date, trans.remarks, now_ts
            ))
            # Capture immediately — subsequent INSERT (e.g. holdings) will overwrite c.lastrowid
            trans_id = c.lastrowid

            # Update cash_balance in accounts table
            # net_cash_change is computed here for BUY/SELL/RIGHTS;
            # DIVIDEND/DIVIDEND_TAX update it in their own handlers.
            net_cash_change = 0
            if trans.trans_type == 'BUY':
                net_cash_change = -(total_amt + fee)
            elif trans.trans_type == 'SELL':
                net_cash_change = total_amt - fee - stamp_tax
            elif trans.trans_type == 'RIGHTS':
                net_cash_change = -(total_amt + fee + stamp_tax)

            if net_cash_change != 0:
                c.execute('UPDATE accounts SET cash_balance = cash_balance + ? WHERE id = ?',
                         (net_cash_change, account_id))
            
            # Update holdings table
            if trans.trans_type in ('BUY', 'SELL'):
                c.execute('''
                    SELECT id, quantity, total_cost FROM holdings 
                    WHERE account_id = ? AND ts_code = ?
                ''', (account_id, trans.ts_code))
                existing = c.fetchone()
                
                if trans.trans_type == 'BUY':
                    buy_qty = abs(trans.amount)
                    buy_cost = total_amt + fee
                    
                    if existing:
                        new_qty = existing['quantity'] + buy_qty
                        new_total_cost = existing['total_cost'] + buy_cost
                        new_cost_price = new_total_cost / new_qty if new_qty > 0 else 0
                        c.execute('''
                            UPDATE holdings 
                            SET quantity = ?, total_cost = ?, cost_price = ?, updated_at = ?
                            WHERE id = ?
                        ''', (new_qty, round(new_total_cost, 3), round(new_cost_price, 4), now_ts, existing['id']))
                        # NOTE: buy_pb is NOT updated on add-position — keeps first-buy PB
                    else:
                        cost_price = buy_cost / buy_qty if buy_qty > 0 else 0
                        first_buy_pb = trans.buy_pb if trans.buy_pb is not None else 0.0
                        c.execute('''
                            INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost, buy_pb, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (account_id, trans.ts_code, trans.name, buy_qty,
                              round(cost_price, 4), round(buy_cost, 3), first_buy_pb, now_ts, now_ts))
                
                elif trans.trans_type == 'SELL':
                    sell_qty = abs(trans.amount)
                    if existing:
                        new_qty = max(0, existing['quantity'] - sell_qty)
                        net_proceeds = total_amt - fee - stamp_tax
                        new_total_cost = existing['total_cost'] - net_proceeds
                        # 清仓截断：卖出后持仓归零，立即重置成本为 0，开启新周期
                        if new_qty == 0:
                            new_total_cost = 0.0
                        new_cost_price = new_total_cost / new_qty if new_qty > 0 else 0
                        c.execute('''
                            UPDATE holdings
                            SET quantity = ?, total_cost = ?, cost_price = ?, updated_at = ?
                            WHERE id = ?
                        ''', (new_qty, round(new_total_cost, 3), round(new_cost_price, 4), now_ts, existing['id']))
            
            # Handle SPLIT/BONUS
            elif trans.trans_type == 'SPLIT':
                c.execute('''
                    SELECT id, quantity, total_cost FROM holdings 
                    WHERE account_id = ? AND ts_code = ?
                ''', (account_id, trans.ts_code))
                existing = c.fetchone()
                
                if existing:
                    bonus_shares = abs(trans.amount)
                    new_qty = existing['quantity'] + bonus_shares
                    new_cost_price = existing['total_cost'] / new_qty if new_qty > 0 else 0
                    c.execute('''
                        UPDATE holdings 
                        SET quantity = ?, cost_price = ?, updated_at = ?
                        WHERE id = ?
                    ''', (new_qty, round(new_cost_price, 4), now_ts, existing['id']))
                else:
                    c.execute('''
                        INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 0, 0, ?, ?)
                    ''', (account_id, trans.ts_code, trans.name, abs(trans.amount), now_ts, now_ts))
            
            # Handle RIGHTS
            elif trans.trans_type == 'RIGHTS':
                c.execute('''
                    SELECT id, quantity, total_cost FROM holdings 
                    WHERE account_id = ? AND ts_code = ?
                ''', (account_id, trans.ts_code))
                existing = c.fetchone()
                
                rights_qty = abs(trans.amount)
                rights_cost = total_amt + fee + stamp_tax
                
                if existing:
                    new_qty = existing['quantity'] + rights_qty
                    new_total_cost = existing['total_cost'] + rights_cost
                    new_cost_price = new_total_cost / new_qty if new_qty > 0 else 0
                    c.execute('''
                        UPDATE holdings 
                        SET quantity = ?, total_cost = ?, cost_price = ?, updated_at = ?
                        WHERE id = ?
                    ''', (new_qty, round(new_total_cost, 3), round(new_cost_price, 4), now_ts, existing['id']))
                else:
                    cost_price = rights_cost / rights_qty if rights_qty > 0 else 0
                    c.execute('''
                        INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (account_id, trans.ts_code, trans.name, rights_qty, round(cost_price, 4), round(rights_cost, 3), now_ts, now_ts))
                
            # Handle DIVIDEND
            elif trans.trans_type == 'DIVIDEND':
                c.execute('''
                    SELECT id, quantity, total_cost FROM holdings 
                    WHERE account_id = ? AND ts_code = ?
                ''', (account_id, trans.ts_code))
                existing = c.fetchone()
                
                dividend_amount = total_amt
                
                if existing and existing['quantity'] > 0:
                    new_total_cost = existing['total_cost'] - dividend_amount
                    new_cost_price = new_total_cost / existing['quantity'] if existing['quantity'] > 0 else 0
                    c.execute('''
                        UPDATE holdings 
                        SET total_cost = ?, cost_price = ?, updated_at = ?
                        WHERE id = ?
                    ''', (round(new_total_cost, 3), round(new_cost_price, 4), now_ts, existing['id']))
                
                c.execute('UPDATE accounts SET cash_balance = cash_balance + ? WHERE id = ?',
                         (dividend_amount, account_id))
                net_cash_change = dividend_amount

            # Handle DIVIDEND_TAX
            elif trans.trans_type == 'DIVIDEND_TAX':
                c.execute('''
                    SELECT id, quantity, total_cost FROM holdings
                    WHERE account_id = ? AND ts_code = ?
                ''', (account_id, trans.ts_code))
                existing = c.fetchone()

                tax_amount = total_amt

                if existing and existing['quantity'] > 0:
                    new_total_cost = existing['total_cost'] + tax_amount
                    new_cost_price = new_total_cost / existing['quantity']
                    c.execute('''
                        UPDATE holdings
                        SET total_cost = ?, cost_price = ?, updated_at = ?
                        WHERE id = ?
                    ''', (round(new_total_cost, 3), round(new_cost_price, 4), now_ts, existing['id']))

                c.execute('UPDATE accounts SET cash_balance = cash_balance - ? WHERE id = ?',
                         (tax_amount, account_id))
                net_cash_change = -tax_amount

            conn.commit()
            return {
                "status": "success",
                "message": "Transaction recorded successfully",
                "id": trans_id,
                "fee": round(fee, 2),
                "stamp_tax": round(stamp_tax, 2),
                "cash_change": round(net_cash_change, 2)
            }
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


# --- Holdings Rebuild Helpers ---

def _rebuild_single_stock_holdings(conn, account_id: str, ts_code: str, fallback_buy_pb: float = 0.0):
    """
    Replay all remaining transactions for account+stock to rebuild the correct
    holdings state. Follows the same 净投入法 + 清仓截断 rules as create_transaction.

    After rebuilding:
    - No transactions left  → delete holdings + archived_holdings
    - quantity > 0          → update/insert holdings, remove from archived_holdings
    - quantity == 0         → set holdings to 0, recalculate archived_holdings
    """
    c = conn.cursor()
    now_ts = sh_timestamp_str()

    # All remaining transactions in chronological order
    c.execute('''
        SELECT trans_type, amount, total_amt, fee, stamp_tax
        FROM transactions
        WHERE account_id = ? AND ts_code = ?
        ORDER BY date ASC, id ASC
    ''', (account_id, ts_code))
    txns = c.fetchall()

    # Replay using the same rules as create_transaction
    quantity = 0
    total_cost = 0.0

    for txn in txns:
        t = txn['trans_type']
        amt = txn['amount']
        total_amt_val = txn['total_amt']
        fee = txn['fee']
        stamp_tax = txn['stamp_tax']

        if t == 'BUY':
            quantity += abs(amt)
            total_cost += total_amt_val + fee
        elif t == 'SELL':
            quantity -= abs(amt)
            total_cost -= (total_amt_val - fee - stamp_tax)
            if quantity <= 0:
                quantity = 0
                total_cost = 0.0   # 清仓截断
        elif t == 'SPLIT':
            quantity += abs(amt)   # total_cost unchanged
        elif t == 'RIGHTS':
            quantity += abs(amt)
            total_cost += total_amt_val + fee + stamp_tax
        elif t == 'DIVIDEND':
            # Only reduce cost when currently holding (mirrors create_transaction logic)
            if quantity > 0:
                total_cost -= total_amt_val
        elif t == 'DIVIDEND_TAX':
            # Only increase cost when currently holding (mirrors create_transaction logic)
            if quantity > 0:
                total_cost += total_amt_val

    quantity = max(0, quantity)
    cost_price = round(total_cost / quantity, 4) if quantity > 0 else 0.0

    # Stock name (best-effort from remaining transactions)
    c.execute('SELECT name FROM transactions WHERE account_id = ? AND ts_code = ? LIMIT 1',
              (account_id, ts_code))
    name_row = c.fetchone()
    stock_name = name_row['name'] if name_row else ts_code

    # Existing holdings record — preserve buy_pb (transactions table has no buy_pb column)
    c.execute('SELECT id, buy_pb FROM holdings WHERE account_id = ? AND ts_code = ?',
              (account_id, ts_code))
    existing_holding = c.fetchone()

    if not txns:
        # No transactions remain — clean up both tables
        if existing_holding:
            c.execute('DELETE FROM holdings WHERE account_id = ? AND ts_code = ?',
                      (account_id, ts_code))
        c.execute('DELETE FROM archived_holdings WHERE account_id = ? AND ts_code = ?',
                  (account_id, ts_code))

    elif quantity > 0:
        # Still holding — update/insert holdings, remove from archived
        if existing_holding:
            # buy_pb is NOT in transactions table; preserve whatever is already in holdings
            c.execute('''UPDATE holdings
                         SET quantity = ?, total_cost = ?, cost_price = ?,
                             updated_at = ?
                         WHERE id = ?''',
                     (quantity, round(total_cost, 3), cost_price, now_ts, existing_holding['id']))
        else:
            # New record: use fallback_buy_pb (caller pre-reads holdings.buy_pb before calling rebuild)
            c.execute('''INSERT INTO holdings
                         (account_id, ts_code, name, quantity, cost_price, total_cost, buy_pb, created_at, updated_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (account_id, ts_code, stock_name, quantity,
                      cost_price, round(total_cost, 3), fallback_buy_pb, now_ts, now_ts))
        c.execute('DELETE FROM archived_holdings WHERE account_id = ? AND ts_code = ?',
                  (account_id, ts_code))

    else:
        # Cleared — set holdings to 0, recalculate archived stats
        if existing_holding:
            c.execute('''UPDATE holdings
                         SET quantity = 0, total_cost = 0, cost_price = 0,
                             updated_at = ?
                         WHERE id = ?''', (now_ts, existing_holding['id']))
        _recalculate_archived_holdings(c, account_id, ts_code, stock_name)


def _recalculate_archived_holdings(c, account_id: str, ts_code: str, stock_name: str):
    """Recalculate and upsert archived_holdings from the full transaction history."""
    c.execute('''
        SELECT trans_type, total_amt, fee, stamp_tax, date
        FROM transactions
        WHERE account_id = ? AND ts_code = ?
        ORDER BY date ASC
    ''', (account_id, ts_code))
    txns = c.fetchall()

    total_buy = total_sell = total_dividend = total_dividend_tax = total_fees = 0.0
    first_buy_date = last_sell_date = None

    for txn in txns:
        t = txn['trans_type']
        amt = txn['total_amt']
        fee = txn['fee']
        stamp_tax = txn['stamp_tax']

        total_fees += fee + stamp_tax
        if t == 'BUY':
            total_buy += amt + fee
            if not first_buy_date:
                first_buy_date = txn['date']
        elif t == 'SELL':
            total_sell += amt - fee - stamp_tax
            last_sell_date = txn['date']
        elif t == 'DIVIDEND':
            total_dividend += amt
        elif t == 'DIVIDEND_TAX':
            total_dividend_tax += amt
            total_fees += amt

    net_dividend = total_dividend - total_dividend_tax
    total_profit = total_sell + net_dividend - total_buy
    profit_pct = round(total_profit / total_buy * 100, 2) if total_buy > 0 else 0.0

    c.execute('SELECT id FROM archived_holdings WHERE account_id = ? AND ts_code = ?',
              (account_id, ts_code))
    existing = c.fetchone()

    if existing:
        c.execute('''UPDATE archived_holdings
                     SET stock_name = ?, total_buy_amount = ?, total_sell_amount = ?,
                         total_dividend = ?, total_profit = ?, profit_pct = ?,
                         total_fees = ?, first_buy_date = ?, last_sell_date = ?
                     WHERE id = ?''',
                 (stock_name, round(total_buy, 2), round(total_sell, 2),
                  round(net_dividend, 2), round(total_profit, 2), profit_pct,
                  round(total_fees, 2), first_buy_date, last_sell_date, existing['id']))
    else:
        c.execute('''INSERT INTO archived_holdings
                     (account_id, ts_code, stock_name, total_buy_amount, total_sell_amount,
                      total_dividend, total_profit, profit_pct, total_fees,
                      first_buy_date, last_sell_date)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (account_id, ts_code, stock_name, round(total_buy, 2), round(total_sell, 2),
                  round(net_dividend, 2), round(total_profit, 2), profit_pct,
                  round(total_fees, 2), first_buy_date, last_sell_date))


@router.delete("/transactions/{transaction_id}")
def delete_transaction(transaction_id: int):
    """Delete a transaction (with cash balance reversal)"""
    with get_db() as conn:
        c = conn.cursor()
        
        c.execute('SELECT * FROM transactions WHERE id = ?', (transaction_id,))
        row = c.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Transaction not found")
        
        total_amt = row["total_amt"]
        fee = row["fee"]
        stamp_tax = row["stamp_tax"]
        trans_type = row["trans_type"]
        account_id = row["account_id"]
        
        reverse_cash = 0
        if trans_type == 'BUY':
            reverse_cash = total_amt + fee
        elif trans_type == 'SELL':
            reverse_cash = -(total_amt - fee - stamp_tax)
        elif trans_type == 'DIVIDEND':
            reverse_cash = -total_amt
        elif trans_type == 'DIVIDEND_TAX':
            reverse_cash = total_amt  # 还原被扣的现金

        ts_code = row["ts_code"]

        # Pre-read buy_pb before deletion so rebuild can use it as fallback if INSERT is needed
        c.execute('SELECT buy_pb FROM holdings WHERE account_id = ? AND ts_code = ?',
                  (account_id, ts_code))
        pb_row = c.fetchone()
        existing_buy_pb = pb_row['buy_pb'] if pb_row and pb_row['buy_pb'] is not None else 0.0

        c.execute('DELETE FROM transactions WHERE id = ?', (transaction_id,))

        # 同步清理做T观察清单中关联此交易的孤儿记录（物理删除，释放 UNIQUE 槽位）
        c.execute('DELETE FROM t_trading_items WHERE transaction_id = ?', (transaction_id,))

        if reverse_cash != 0:
            c.execute('UPDATE accounts SET cash_balance = cash_balance + ? WHERE id = ?',
                     (reverse_cash, account_id))

        # Rebuild holdings from remaining transactions (correct for any historical deletion)
        _rebuild_single_stock_holdings(conn, account_id, ts_code, fallback_buy_pb=existing_buy_pb)

        conn.commit()
        return {"status": "success", "message": "Transaction deleted", "cash_reversed": round(reverse_cash, 2)}

# --- Holdings Rebuild API ---

@router.post("/holdings/{account_id}/{ts_code}/rebuild")
def rebuild_holding(account_id: str, ts_code: str):
    """Recalculate holdings quantity and cost from transaction history."""
    with get_db() as conn:
        c = conn.cursor()
        # Pre-read buy_pb so INSERT (if needed) inherits the correct value
        c.execute('SELECT buy_pb FROM holdings WHERE account_id = ? AND ts_code = ?',
                  (account_id, ts_code))
        pb_row = c.fetchone()
        existing_buy_pb = pb_row['buy_pb'] if pb_row and pb_row['buy_pb'] is not None else 0.0
        _rebuild_single_stock_holdings(conn, account_id, ts_code, fallback_buy_pb=existing_buy_pb)
        conn.commit()
        c = conn.cursor()
        c.execute('SELECT quantity, cost_price, total_cost FROM holdings WHERE account_id = ? AND ts_code = ?',
                  (account_id, ts_code))
        row = c.fetchone()
    return {
        "status": "success",
        "message": f"Holdings rebuilt for {ts_code}",
        "quantity": row["quantity"] if row else 0,
        "cost_price": row["cost_price"] if row else 0,
        "total_cost": row["total_cost"] if row else 0,
    }

# --- Stock Summary API (for Stock Detail Dialog) ---

@router.get("/stock-summary/{account_id}/{ts_code}")
def get_stock_summary(account_id: str, ts_code: str):
    """Get summary stats for a specific stock (for Stock Detail Dialog)"""
    with get_db() as conn:
        c = conn.cursor()
        
        c.execute('''
            SELECT * FROM transactions 
            WHERE account_id = ? AND ts_code = ?
            ORDER BY date ASC
        ''', (account_id, ts_code))
        rows = c.fetchall()
    
    if not rows:
        return {"status": "warning", "message": "No transactions found for this stock"}
    
    # Calculate summary
    total_buy_amt = total_sell_amt = total_dividend = total_dividend_tax = total_fees = 0
    first_buy_date = last_sell_date = None
    current_quantity = total_buy_qty = 0

    for row in rows:
        trans_type = row["trans_type"]
        amount = row["amount"]
        total_amt = row["total_amt"]
        fee = row["fee"]
        stamp_tax = row["stamp_tax"]
        date = row["date"]

        total_fees += fee + stamp_tax

        if trans_type == 'BUY':
            total_buy_amt += total_amt + fee
            current_quantity += amount
            total_buy_qty += amount
            if not first_buy_date:
                first_buy_date = date
        elif trans_type == 'SELL':
            total_sell_amt += total_amt - fee - stamp_tax
            current_quantity -= abs(amount)
            last_sell_date = date
        elif trans_type == 'RIGHTS':
            total_buy_amt += total_amt + fee + stamp_tax
            current_quantity += abs(amount)
            total_buy_qty += abs(amount)
        elif trans_type == 'SPLIT':
            current_quantity += abs(amount)
        elif trans_type == 'DIVIDEND':
            total_dividend += total_amt
        elif trans_type == 'DIVIDEND_TAX':
            total_dividend_tax += total_amt
            total_fees += total_amt  # 差异税也是支出，纳入总费用

    total_buy_amt = valid_float(total_buy_amt)
    total_buy_qty = valid_float(total_buy_qty)
    # 净分红 = 实收分红 - 追缴差异税（与 _rebuild_holdings 和 create_transaction 的成本语义一致）
    net_dividend = total_dividend - total_dividend_tax

    if current_quantity == 0:
        realized_pnl = total_sell_amt + net_dividend - total_buy_amt
    else:
        if total_buy_qty > 0:
            avg_cost = total_buy_amt / total_buy_qty
            sold_qty = max(0, total_buy_qty - current_quantity)
            realized_pnl = total_sell_amt - (avg_cost * sold_qty) + net_dividend
        else:
            realized_pnl = 0

    # Holding days
    holding_days = 0
    if first_buy_date:
        try:
            first_date = datetime.strptime(first_buy_date, "%Y-%m-%d").date()
            holding_days = (sh_today() - first_date).days
        except Exception:
            pass
    
    avg_cost = valid_float(round(total_buy_amt / total_buy_qty, 2)) if total_buy_qty > 0 else 0

    return {
        "status": "success",
        "data": {
            "ts_code": ts_code,
            "current_quantity": current_quantity,
            "avg_cost": avg_cost,
            "total_buy_amount": valid_float(round(total_buy_amt, 2)),
            "total_sell_amount": valid_float(round(total_sell_amt, 2)),
            "total_dividend": valid_float(round(total_dividend, 2)),
            "total_dividend_tax": valid_float(round(total_dividend_tax, 2)),
            "net_dividend": valid_float(round(net_dividend, 2)),
            "total_fees": valid_float(round(total_fees, 2)),
            "realized_pnl": valid_float(round(realized_pnl, 2)),
            "first_buy_date": first_buy_date,
            "last_sell_date": last_sell_date,
            "holding_days": holding_days
        }
    }

# --- Holdings Cost Update API ---

@router.put("/holdings/{account_id}/{ts_code}/cost")
def update_holding_cost(account_id: str, ts_code: str, req: CostUpdateRequest):
    """Update cost price and total cost for a holding"""
    with get_db() as conn:
        c = conn.cursor()
        
        c.execute('SELECT id, quantity FROM holdings WHERE account_id = ? AND ts_code = ?', (account_id, ts_code))
        holding = c.fetchone()
        
        if not holding:
            raise HTTPException(status_code=404, detail="Holding not found")
        
        if req.quantity is not None:
            c.execute('''
                UPDATE holdings
                SET quantity = ?, cost_price = ?, total_cost = ?, updated_at = ?
                WHERE id = ?
            ''', (req.quantity, round(req.cost_price, 4), round(req.total_cost, 3), sh_timestamp_str(), holding['id']))
        else:
            c.execute('''
                UPDATE holdings
                SET cost_price = ?, total_cost = ?, updated_at = ?
                WHERE id = ?
            ''', (round(req.cost_price, 4), round(req.total_cost, 3), sh_timestamp_str(), holding['id']))

        conn.commit()

    return {
        "status": "success",
        "message": f"Holding updated for {ts_code}",
        "cost_price": round(req.cost_price, 4),
        "total_cost": round(req.total_cost, 3),
        "quantity": req.quantity
    }


# --- Holdings Delete API ---

@router.delete("/holdings/{account_id}/{ts_code}")
def delete_holding(account_id: str, ts_code: str):
    """Delete a holding and ALL its transactions for this account.
    This is a destructive operation — use with caution.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT id FROM holdings WHERE account_id = ? AND ts_code = ?', (account_id, ts_code))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Holding not found")

        # 计算该持仓所有交易对现金的净影响，删除后反向回滚 cash_balance
        # BUY/RIGHTS 减少现金，SELL/DIVIDEND 增加现金
        c.execute("""
            SELECT SUM(
                CASE trans_type
                    WHEN 'BUY'          THEN -(total_amt + COALESCE(fee,0) + COALESCE(stamp_tax,0))
                    WHEN 'RIGHTS'       THEN -(total_amt + COALESCE(fee,0) + COALESCE(stamp_tax,0))
                    WHEN 'SELL'         THEN  (total_amt - COALESCE(fee,0) - COALESCE(stamp_tax,0))
                    WHEN 'DIVIDEND'     THEN  total_amt
                    WHEN 'DIVIDEND_TAX' THEN -total_amt
                    ELSE 0
                END
            ) AS cash_delta
            FROM transactions
            WHERE account_id = ? AND ts_code = ?
        """, (account_id, ts_code))
        row = c.fetchone()
        cash_delta = row["cash_delta"] if row and row["cash_delta"] is not None else 0.0

        # 1. 删除 holdings 记录
        c.execute('DELETE FROM holdings WHERE account_id = ? AND ts_code = ?', (account_id, ts_code))
        # 2. 删除该股票全部交易记录
        c.execute('DELETE FROM transactions WHERE account_id = ? AND ts_code = ?', (account_id, ts_code))
        # 3. 删除相关资金流水（SELL 产生的）
        c.execute("""
            DELETE FROM capital_flows
            WHERE account_id = ? AND remarks LIKE ?
        """, (account_id, f"%{ts_code}%"))
        # 4. 删除该股票的做 T 记录（避免孤儿数据出现在做T分析页）
        c.execute('DELETE FROM t_trading_items WHERE account_id = ? AND ts_code = ?', (account_id, ts_code))
        # 5. 删除该股票的清仓归档记录（同步清理，避免归档页出现残影）
        c.execute('DELETE FROM archived_holdings WHERE account_id = ? AND ts_code = ?', (account_id, ts_code))
        # 6. 回滚 cash_balance：撤销该持仓所有交易的现金影响
        if cash_delta != 0.0:
            c.execute("""
                UPDATE accounts SET cash_balance = cash_balance - ?
                WHERE id = ?
            """, (cash_delta, account_id))
        conn.commit()

    return {
        "status": "success",
        "message": f"Holding and all transactions for {ts_code} deleted"
    }
