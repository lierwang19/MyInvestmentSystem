# -*- coding: utf-8 -*-
"""
Account Management API
- List/Create/Update/Delete accounts (DB-based)
- Capital flow records (Deposit/Withdraw)
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Iterable
import logging
import math
import os
import shutil

from web.backend.database import get_db, get_market_db
from web.backend.config import PROJECT_ROOT
from web.backend.time_utils import sh_timestamp_str
from web.backend.utils import is_iso_date

logger = logging.getLogger(__name__)
router = APIRouter()


def _calc_holdings_market_values(account_ids: Optional[Iterable[str]] = None) -> dict[str, float]:
    """批量计算账户持仓市值，避免 /accounts-db 对 market.db 逐账户重复查询。"""
    with get_db() as conn:
        c = conn.cursor()
        params: list[str] = []
        query = (
            "SELECT account_id, ts_code, quantity FROM holdings "
            "WHERE quantity > 0"
        )
        if account_ids is not None:
            account_ids = list(account_ids)
            if not account_ids:
                return {}
            placeholders = ",".join("?" * len(account_ids))
            query += f" AND account_id IN ({placeholders})"
            params.extend(account_ids)
        query += " ORDER BY account_id, ts_code"
        c.execute(query, params)
        rows = c.fetchall()

    if not rows:
        return {}

    ts_codes = list(dict.fromkeys(r["ts_code"] for r in rows))

    # 从 market.db 取每只股票最新一条收盘价
    price_map: dict = {}
    try:
        placeholders = ",".join("?" * len(ts_codes))
        with get_market_db() as mconn:
            if mconn is not None:
                rows_price = mconn.execute(
                    f"""
                    SELECT dp.ts_code, dp.close
                    FROM daily_price dp
                    JOIN (
                        SELECT ts_code, MAX(trade_date) AS trade_date
                        FROM daily_price
                        WHERE ts_code IN ({placeholders})
                        GROUP BY ts_code
                    ) latest
                      ON latest.ts_code = dp.ts_code
                     AND latest.trade_date = dp.trade_date
                    """,
                    ts_codes,
                ).fetchall()
                price_map = {r["ts_code"]: r["close"] for r in rows_price if r["close"]}
    except Exception as e:
        logger.warning(f"market.db batch price query failed for accounts-db: {e}")

    # 缺价时用 0（与 analyze_holdings 对 DB 持仓的行为一致，两页口径统一）
    totals: dict[str, float] = {}
    for row in rows:
        account_id = row["account_id"]
        code = row["ts_code"]
        qty = row["quantity"]
        price = price_map.get(code, 0.0)
        totals[account_id] = totals.get(account_id, 0.0) + qty * price
    return totals


def _calc_holdings_market_value(account_id: str) -> float:
    """兼容单账户调用。"""
    return _calc_holdings_market_values([account_id]).get(account_id, 0.0)


def _apply_capital_flow_cash(c, account_id: str, flow_type: str, amount: float):
    """按资金流水增减现金余额；amount 为负表示冲销。"""
    delta = amount if flow_type == "DEPOSIT" else -amount
    c.execute("UPDATE accounts SET cash_balance = COALESCE(cash_balance, 0) + ? WHERE id = ?",
              (delta, account_id))


def _rebuild_account_state(conn, account_id: str):
    """资金流水变更后，用完整重放逻辑重建持仓和现金余额。
    复用 import_flow._rebuild_holdings，确保交易现金流一并纳入计算。
    """
    from web.backend.api.import_flow import _rebuild_holdings
    _rebuild_holdings(account_id, conn)

# --- Models ---

class AccountCreate(BaseModel):
    name: str
    broker_id: Optional[int] = None
    remarks: Optional[str] = None

class AccountUpdate(BaseModel):
    name: Optional[str] = None
    broker_id: Optional[int] = None
    remarks: Optional[str] = None
    cash_balance: Optional[float] = None

class CapitalFlowCreate(BaseModel):
    flow_type: str  # DEPOSIT or WITHDRAW
    amount: float
    date: str  # YYYY-MM-DD
    remarks: Optional[str] = None

# --- Account Endpoints ---

@router.get("/accounts-db")
def list_accounts_db():
    """获取所有账户（从数据库）"""
    with get_db() as conn:
        c = conn.cursor()
        
        c.execute('''
            SELECT a.id, a.name, a.broker_id, a.migrated, a.remarks, a.cash_balance, a.created_at,
                   b.name as broker_name
            FROM accounts a
            LEFT JOIN brokers b ON a.broker_id = b.id
            ORDER BY a.name ASC
        ''')
        rows = c.fetchall()

        account_ids = [row["id"] for row in rows]

        capital_totals: dict[str, dict[str, float]] = {}
        if account_ids:
            placeholders = ",".join("?" * len(account_ids))
            c.execute(f'''
                SELECT account_id,
                       COALESCE(SUM(CASE WHEN flow_type = 'DEPOSIT' THEN amount ELSE 0 END), 0) as total_deposit,
                       COALESCE(SUM(CASE WHEN flow_type = 'WITHDRAW' THEN amount ELSE 0 END), 0) as total_withdraw
                FROM capital_flows
                WHERE account_id IN ({placeholders})
                GROUP BY account_id
            ''', account_ids)
            capital_totals = {
                row["account_id"]: {
                    "total_deposit": row["total_deposit"],
                    "total_withdraw": row["total_withdraw"],
                }
                for row in c.fetchall()
            }

    holdings_values: dict[str, float] = {}
    try:
        holdings_values = _calc_holdings_market_values(account_ids)
    except Exception as e:
        logger.error(f"Error calculating holdings value for accounts-db: {e}")
        
    accounts = []
    for row in rows:
        account_id = row["id"]
        capital_row = capital_totals.get(account_id, {})
        total_deposit = capital_row.get("total_deposit", 0.0)
        total_withdraw = capital_row.get("total_withdraw", 0.0)
        net_invested = total_deposit - total_withdraw
        holdings_value = holdings_values.get(account_id, 0.0)
        cash_balance = row["cash_balance"] or 0.0
        total_assets = holdings_value + cash_balance
        profit = total_assets - net_invested if net_invested > 0 else 0
        profit_ratio = (profit / net_invested * 100) if net_invested > 0 else 0

        accounts.append({
            "id": account_id,
            "name": row["name"],
            "broker_id": row["broker_id"],
            "broker_name": row["broker_name"],
            "remarks": row["remarks"],
            "migrated": row["migrated"],
            "total_deposit": total_deposit,
            "total_withdraw": total_withdraw,
            "net_invested": net_invested,
            "holdings_value": holdings_value,
            "cash_balance": cash_balance,
            "total_assets": total_assets,
            "profit": profit,
            "profit_ratio": round(profit_ratio, 2),
            "created_at": row["created_at"]
        })
    
    return {"status": "success", "data": accounts}

@router.post("/accounts-db")
def create_account_db(account: AccountCreate):
    """创建新账户"""
    account_id = account.name
    created_at = sh_timestamp_str()
    
    with get_db() as conn:
        c = conn.cursor()
        try:
            c.execute('''
                INSERT INTO accounts (id, name, broker_id, remarks, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (account_id, account.name, account.broker_id, account.remarks, created_at))
            conn.commit()
            return {"status": "success", "message": f"账户 {account.name} 创建成功", "id": account_id}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

@router.put("/accounts-db/{account_id}")
def update_account_db(account_id: str, account: AccountUpdate):
    """更新账户信息"""
    updates = []
    params = []
    
    if account.name is not None:
        updates.append("name = ?"); params.append(account.name)
    if account.broker_id is not None:
        updates.append("broker_id = ?"); params.append(account.broker_id)
    if account.remarks is not None:
        updates.append("remarks = ?"); params.append(account.remarks)
    if account.cash_balance is not None:
        updates.append("cash_balance = ?"); params.append(account.cash_balance)
    
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    params.append(account_id)
    query = f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?"
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute(query, params)
        conn.commit()
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="Account not found")
    
    return {"status": "success", "message": "账户信息已更新"}

@router.delete("/accounts-db/{account_id}")
def delete_account_db(account_id: str):
    """删除账户（含所有关联数据：持仓、交易、资金流水、资产快照）"""
    with get_db() as conn:
        c = conn.cursor()

        c.execute("SELECT id FROM accounts WHERE id = ?", (account_id,))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="账户不存在")

        # 级联删除所有关联数据
        c.execute("DELETE FROM holdings WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM transactions WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM capital_flows WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM asset_history WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM archived_holdings WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM t_trading_items WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM strategy_runs WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM strategy_signals WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM strategy_decisions WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        conn.commit()

    # 清理持仓上传文件目录（路径穿越防护：确认解析后路径在允许范围内）
    holdings_base = os.path.realpath(os.path.join(PROJECT_ROOT, "web", "backend", "uploads", "holdings"))
    uploads_dir = os.path.realpath(os.path.join(holdings_base, account_id))
    if uploads_dir.startswith(holdings_base + os.sep) and os.path.isdir(uploads_dir):
        shutil.rmtree(uploads_dir, ignore_errors=True)
        logger.info(f"已删除上传目录: {uploads_dir}")

    return {"status": "success", "message": "账户及所有关联数据已删除"}

# --- Capital Flow Endpoints ---

@router.get("/accounts-db/{account_id}/capital-flows")
def list_capital_flows(account_id: str):
    """获取账户的资金流水记录"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT * FROM capital_flows 
            WHERE account_id = ? 
            ORDER BY date DESC, created_at DESC
        ''', (account_id,))
        rows = c.fetchall()
    
    flows = []
    for row in rows:
        flows.append({
            "id": row["id"],
            "flow_type": row["flow_type"],
            "amount": row["amount"],
            "date": row["date"],
            "remarks": row["remarks"],
            "created_at": row["created_at"]
        })
    
    return {"status": "success", "data": flows}

@router.post("/accounts-db/{account_id}/capital-flows")
def add_capital_flow(account_id: str, flow: CapitalFlowCreate):
    """添加资金流水记录"""
    if flow.flow_type not in ["DEPOSIT", "WITHDRAW"]:
        raise HTTPException(status_code=400, detail="Invalid flow_type. Must be DEPOSIT or WITHDRAW")
    if not math.isfinite(flow.amount) or flow.amount <= 0:
        raise HTTPException(status_code=400, detail="金额必须是大于 0 的有限数值")
    if not is_iso_date(flow.date):
        raise HTTPException(status_code=400, detail="日期格式必须为补零的 YYYY-MM-DD")
    created_at = sh_timestamp_str()
    
    with get_db() as conn:
        c = conn.cursor()
        
        c.execute("SELECT id FROM accounts WHERE id = ?", (account_id,))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Account not found")
        
        c.execute('''
            INSERT INTO capital_flows (account_id, flow_type, amount, date, remarks, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (account_id, flow.flow_type, flow.amount, flow.date, flow.remarks, created_at))
        flow_id = c.lastrowid
        # 资金流水只影响现金：增量调整，不重放持仓（快照导入的持仓可能没有交易历史）
        _apply_capital_flow_cash(c, account_id, flow.flow_type, flow.amount)
        conn.commit()

    action = "转入" if flow.flow_type == "DEPOSIT" else "转出"
    return {"status": "success", "message": f"{action} ¥{flow.amount} 记录成功", "id": flow_id}

@router.delete("/capital-flows/{flow_id}")
def delete_capital_flow(flow_id: int):
    """删除资金流水记录"""
    with get_db() as conn:
        c = conn.cursor()
        # 先取写锁再读：并发删除同一笔流水时只能有一个请求冲销现金
        conn.execute("BEGIN IMMEDIATE")
        c.execute("SELECT account_id, flow_type, amount FROM capital_flows WHERE id = ?", (flow_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Record not found")
        account_id = row["account_id"]
        flow_type = row["flow_type"]
        amount = row["amount"]
        c.execute("DELETE FROM capital_flows WHERE id = ?", (flow_id,))
        if c.rowcount != 1:
            raise HTTPException(status_code=404, detail="Record not found")
        # 反向冲销该笔流水的现金影响，不重放持仓
        _apply_capital_flow_cash(c, account_id, flow_type, -amount)
        conn.commit()

    return {"status": "success", "message": "记录已删除"}
