# -*- coding: utf-8 -*-
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web.backend.database import get_db

router = APIRouter()

class BrokerCreate(BaseModel):
    name: str
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.001
    transfer_fee_rate: float = 0.00002

class BrokerUpdate(BaseModel):
    name: str = None
    commission_rate: float = None
    min_commission: float = None
    stamp_tax_rate: float = None
    transfer_fee_rate: float = None

@router.get("/brokers")
def list_brokers():
    """获取所有券商"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM brokers ORDER BY created_at DESC")
        rows = c.fetchall()
    
    brokers = []
    for row in rows:
        brokers.append({
            "id": row["id"],
            "name": row["name"],
            "commission_rate": row["commission_rate"],
            "min_commission": row["min_commission"],
            "stamp_tax_rate": row["stamp_tax_rate"],
            "transfer_fee_rate": row["transfer_fee_rate"],
            "created_at": row["created_at"]
        })
    
    return {"status": "success", "data": brokers}

@router.post("/brokers")
def create_broker(broker: BrokerCreate):
    """新增券商"""
    with get_db() as conn:
        c = conn.cursor()
        try:
            c.execute('''
                INSERT INTO brokers (name, commission_rate, min_commission, stamp_tax_rate, transfer_fee_rate)
                VALUES (?, ?, ?, ?, ?)
            ''', (broker.name, broker.commission_rate, broker.min_commission, 
                  broker.stamp_tax_rate, broker.transfer_fee_rate))
            conn.commit()
            broker_id = c.lastrowid
            return {"status": "success", "message": f"券商 {broker.name} 添加成功", "id": broker_id}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

@router.put("/brokers/{broker_id}")
def update_broker(broker_id: int, broker: BrokerUpdate):
    """更新券商信息"""
    updates = []
    params = []
    
    if broker.name is not None:
        updates.append("name = ?"); params.append(broker.name)
    if broker.commission_rate is not None:
        updates.append("commission_rate = ?"); params.append(broker.commission_rate)
    if broker.min_commission is not None:
        updates.append("min_commission = ?"); params.append(broker.min_commission)
    if broker.stamp_tax_rate is not None:
        updates.append("stamp_tax_rate = ?"); params.append(broker.stamp_tax_rate)
    if broker.transfer_fee_rate is not None:
        updates.append("transfer_fee_rate = ?"); params.append(broker.transfer_fee_rate)
    
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    params.append(broker_id)
    query = f"UPDATE brokers SET {', '.join(updates)} WHERE id = ?"
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute(query, params)
        conn.commit()
        
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="Broker not found")
    
    return {"status": "success", "message": "券商信息已更新"}

@router.delete("/brokers/{broker_id}")
def delete_broker(broker_id: int):
    """删除券商"""
    with get_db() as conn:
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM accounts WHERE broker_id = ?", (broker_id,))
        count = c.fetchone()[0]
        
        if count > 0:
            raise HTTPException(status_code=400, detail=f"无法删除：有 {count} 个账户正在使用此券商")
        
        c.execute("DELETE FROM brokers WHERE id = ?", (broker_id,))
        conn.commit()
        
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="Broker not found")
    
    return {"status": "success", "message": "券商已删除"}
