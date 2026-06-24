# -*- coding: utf-8 -*-
from fastapi import APIRouter, UploadFile, File, HTTPException
import shutil
import os
import pandas as pd

router = APIRouter()

from web.backend.config import PROJECT_ROOT
from web.backend.time_utils import sh_compact_timestamp, sh_timestamp_str
from web.backend.upload_security import (
    MAX_GENERIC_UPLOAD_BYTES,
    ensure_within_root,
    read_limited_upload,
    validate_storage_component,
)

UPLOAD_DIR = os.path.join(PROJECT_ROOT, "data", "uploads")
ICON_DIR = os.path.join(PROJECT_ROOT, "data")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# NOTE: Specific routes MUST be declared before generic {file_type} route
@router.post("/upload/incon")
async def upload_incon(file: UploadFile = File(...)):
    """Upload incon.dat file for TDX industry classification"""
    filename = file.filename or ""
    if not filename.endswith('.dat'):
        raise HTTPException(status_code=400, detail="Only .dat files are allowed")

    save_path = os.path.join(ICON_DIR, "incon.dat")
    try:
        file_bytes = await read_limited_upload(file, MAX_GENERIC_UPLOAD_BYTES)
        with open(save_path, "wb") as buffer:
            buffer.write(file_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save incon.dat: {str(e)}")

    return {
        "status": "success",
        "filename": "incon.dat",
        "path": save_path
    }

@router.post("/upload/{file_type}")
async def upload_file(file_type: str, file: UploadFile = File(...), account_id: str = None):
    """
    Upload Excel files.
    file_type: 'industry', 'market', 'holdings'
    account_id: required only for 'holdings'
    """
    valid_types = ["industry", "market", "holdings"]
    if file_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid file type. Must be one of {valid_types}")

    filename = file.filename or ""
    if not filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(status_code=400, detail="Only Excel/CSV files are allowed")

    # Generate filename with timestamp to avoid overwriting and allow history
    timestamp = sh_compact_timestamp()
    ext = os.path.splitext(filename)[1]
    save_name = f"{file_type}_{timestamp}{ext}"
    # Determine save path
    if file_type == "holdings":
        if not account_id:
            raise HTTPException(status_code=400, detail="account_id is required for uploading holdings")
        account_id = validate_storage_component(account_id, "account_id")
        
        # Create account specific folder
        acc_dir = os.path.join(UPLOAD_DIR, "holdings", account_id)
        os.makedirs(acc_dir, exist_ok=True)
        save_path = ensure_within_root(acc_dir, os.path.join(acc_dir, save_name))
    else:
        # Global data (industry, market)
        save_path = ensure_within_root(UPLOAD_DIR, os.path.join(UPLOAD_DIR, save_name))

    # Save file
    try:
        file_bytes = await read_limited_upload(file, MAX_GENERIC_UPLOAD_BYTES)
        with open(save_path, "wb") as buffer:
            buffer.write(file_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    # Try validating/parsing content
    try:
        if ext == ".csv":
            df = pd.read_csv(save_path, nrows=5)
        else:
            df = pd.read_excel(save_path, nrows=5)
        
        # Here you could add specific validation logic for each file_type
        # e.g. checking required columns
        columns = df.columns.tolist()

    except Exception as e:
        # If parsing fails, maybe delete the file? Or keep it for debug.
        # os.remove(save_path)
        return {
            "status": "warning", 
            "message": f"File saved as {save_name}, but parsing failed: {str(e)}",
            "path": save_path
        }

    # If this is a holdings file, migrate to database
    migrated_count = 0
    if file_type == "holdings" and account_id:
        try:
            migrated_count = migrate_holdings_to_db(save_path, account_id)
        except Exception as e:
            logger.warning(f"Warning: Holdings migration failed: {e}")

    return {
        "status": "success",
        "filename": save_name,
        "path": save_path,
        "preview_columns": columns,
        "rows_preview": len(df),
        "migrated_to_db": migrated_count
    }

from web.backend.utils import normalize_code
import logging

logger = logging.getLogger(__name__)

def migrate_holdings_to_db(file_path: str, account_id: str) -> int:
    """Migrate holdings from Excel/CSV file to database"""
    from web.backend.database import get_db
    
    # Read file
    if file_path.endswith(".csv"):
        df = pd.read_csv(file_path, dtype=str)
    else:
        df = pd.read_excel(file_path, dtype=str)
    
    cols = df.columns
    
    def find_col(keywords):
        for k in keywords:
            for c in cols:
                if k in c: return c
        return None
    
    code_col = find_col(["证券代码", "代码", "Code", "ts_code"])
    name_col = find_col(["证券名称", "名称", "Name"])
    qty_col = find_col(["证券数量", "数量", "股份余额", "Quantity", "Amount"])
    cost_col = find_col(["成本价", "Cost"])
    
    if not code_col or not qty_col:
        raise ValueError(f"Cannot find required columns. Available: {cols.tolist()}")
    
    from web.backend.database import get_db
    
    with get_db() as conn:
        c = conn.cursor()
        
        # Clear existing holdings for this account (fresh import)
        c.execute('DELETE FROM holdings WHERE account_id = ?', (account_id,))
        
        migrated = 0
        failed = 0
        for _, row in df.iterrows():
            try:
                ts_code = normalize_code(row[code_col])
                name = row[name_col] if name_col else ""
                quantity = int(float(row[qty_col])) if pd.notna(row[qty_col]) else 0
                cost = float(row[cost_col]) if cost_col and pd.notna(row.get(cost_col)) else 0
                
                if quantity <= 0:
                    continue
                
                total_cost = quantity * cost
                now_ts = sh_timestamp_str()
                
                c.execute('''
                    INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (account_id, ts_code, name, quantity, round(cost, 4), round(total_cost, 2), now_ts, now_ts))
                migrated += 1
            except Exception as e:
                logger.error(f"Error migrating row: {e}")
                failed += 1
                continue
        
        conn.commit()

    if failed and migrated == 0:
        raise ValueError(f"Holdings migration failed for all {failed} rows")
    
    logger.info(f"✅ Migrated {migrated} holdings to database for account {account_id}")
    return migrated


@router.get("/files/status")
def get_files_status():
    """List most recent files for each type"""
    files = os.listdir(UPLOAD_DIR)
    status = {}
    
    for ft in ["industry", "market", "holdings"]:
        # Filter files starting with file_type
        relevant = [f for f in files if f.startswith(ft)]
        relevant.sort(reverse=True) # Descending, so latest timestamp first
        
        if relevant:
            latest = relevant[0]
            status[ft] = {
                "latest_file": latest,
                "upload_time": latest.split("_")[1] if "_" in latest else "Unknown",
                "path": os.path.join(UPLOAD_DIR, latest)
            }
        else:
            status[ft] = None

    # incon.dat status
    incon_path = os.path.join(ICON_DIR, "incon.dat")
    if os.path.exists(incon_path):
        mtime = os.path.getmtime(incon_path)
        from datetime import datetime as dt
        from web.backend.time_utils import SH_TZ
        status["incon_dat"] = {
            "exists": True,
            "time": dt.fromtimestamp(mtime, tz=SH_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "size": os.path.getsize(incon_path)
        }
    else:
        status["incon_dat"] = None

    return status

@router.get("/files/status/holdings/{account_id}")
def get_holdings_status(account_id: str):
    """Get holdings status for specific account"""
    account_id = validate_storage_component(account_id, "account_id")
    acc_dir = os.path.join(UPLOAD_DIR, "holdings", account_id)
    if not os.path.exists(acc_dir):
        return None
        
    files = os.listdir(acc_dir)
    files.sort(reverse=True)
    
    if files:
        latest = files[0]
        return {
             "latest_file": latest,
             "upload_time": latest.split("_")[1] if "_" in latest else "Unknown",
             "path": os.path.join(acc_dir, latest)
        }
    return None
