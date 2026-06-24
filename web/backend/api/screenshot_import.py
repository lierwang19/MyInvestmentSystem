# -*- coding: utf-8 -*-
"""
Screenshot Import API — 截图 OCR 识别 + 批量导入交易

Endpoints:
  POST /screenshot/parse          — OCR + 解析 + 名称匹配 + 去重预检
  POST /screenshot/import/{account_id} — 批量导入（双层去重 + BUY 自动进做T清单）
"""
import json
import logging
import re
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel

from web.backend.database import get_db
from web.backend.time_utils import sh_now
from web.backend.upload_security import MAX_IMAGE_UPLOAD_BYTES, read_limited_upload

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Default keyword mapping ──
DEFAULT_KEYWORDS = {
    "BUY":          ["买入", "证券买入"],
    "SELL":         ["卖出", "证券卖出"],
    "DIVIDEND":     ["利息入账", "红利入账", "红利派息", "股息"],
    "SPLIT":        ["送股到账", "送股"],
    "RIGHTS":       ["配股缴款", "配股"],
    "DIVIDEND_TAX": ["红利税", "利息税", "红利差异税"],
    "IGNORE":       ["证券转银行", "银行转证券"],
}


# ── Helpers ──

def _load_keywords() -> dict:
    """Read keyword mapping from system_settings, fall back to defaults."""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT value FROM system_settings WHERE key = 'screenshot_keywords'")
            row = c.fetchone()
            if row and row[0]:
                return json.loads(row[0])
    except Exception:
        pass
    return DEFAULT_KEYWORDS


def _classify_op(op_text: str, keywords: dict) -> Optional[str]:
    """Match operation text to trans_type using keyword list."""
    for trans_type, kws in keywords.items():
        for kw in kws:
            if kw in op_text:
                return trans_type
    return None


def _build_name_index() -> Optional[dict]:
    """
    Build name → record(s) reverse index.

    Priority:
      1. stock_industry table in user_data.db  — SQLite-backed, authoritative
      2. STOCK_CACHE (in-memory CSV)           — fallback if stock_industry is empty/unsynced
      3. holdings + transactions               — supplement; ensures user-traded stocks always match

    Returns None only if all three sources yield nothing.
    """
    index: dict = {}

    def _insert(record: dict) -> None:
        """Insert a record into index, creating a list on duplicate name."""
        name = record.get('name', '').strip()
        if not name:
            return
        if name in index:
            existing = index[name]
            if isinstance(existing, list):
                existing.append(record)
            else:
                index[name] = [existing, record]
        else:
            index[name] = record

    # Source 1: stock_industry table (primary — authoritative SQLite master)
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT ts_code, name, tdx_l1 AS industry "
                "FROM stock_industry WHERE name IS NOT NULL AND name != ''"
            )
            for row in c.fetchall():
                _insert({'ts_code': row['ts_code'], 'name': row['name'],
                         'industry': row['industry'] or ''})
        logger.info(f"_build_name_index: {len(index)} stocks from stock_industry table")
    except Exception as e:
        logger.warning(f"_build_name_index: stock_industry lookup failed: {e}")

    # Source 2: STOCK_CACHE fallback (only when stock_industry is empty/unsynced)
    if not index:
        try:
            from web.backend.api.market import STOCK_CACHE
            if STOCK_CACHE.get('list'):
                for r in STOCK_CACHE['list']:
                    _insert(r)
                logger.info(f"_build_name_index: stock_industry empty, fell back to STOCK_CACHE "
                            f"({len(index)} stocks)")
        except Exception as e:
            logger.warning(f"_build_name_index: STOCK_CACHE fallback failed: {e}")

    # Source 3: hk_stocks table (港股主档)
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT ts_code, name FROM hk_stocks WHERE name IS NOT NULL AND name != ''")
            for row in c.fetchall():
                _insert({'ts_code': row['ts_code'], 'name': row['name'], 'industry': '港股'})
    except Exception as e:
        logger.warning(f"_build_name_index: hk_stocks lookup failed: {e}")

    # Source 4: holdings + transactions (always supplement — user-traded stocks must match)
    added_from_user_db = 0
    try:
        with get_db() as conn:
            c = conn.cursor()
            for table in ('holdings', 'transactions'):
                c.execute(
                    f"SELECT DISTINCT ts_code, name FROM {table} "
                    "WHERE ts_code IS NOT NULL AND name IS NOT NULL AND name != ''"
                )
                for row in c.fetchall():
                    name = row['name'].strip()
                    if name and name not in index:
                        index[name] = {'ts_code': row['ts_code'], 'name': name, 'industry': ''}
                        added_from_user_db += 1
                        logger.debug(f"  supplement added: {name!r} ({row['ts_code']})")
        logger.info(f"_build_name_index: supplement added {added_from_user_db} stocks from holdings/transactions, total={len(index)}")
    except Exception as e:
        logger.warning(f"_build_name_index: holdings/transactions supplement failed: {e}")

    return index if index else None


def _match_stock(name: str, name_index: Optional[dict]):
    """Returns (ts_code, candidates, match_status)."""
    if name_index is None:
        return None, None, "cache_unavailable"
    clean = name.replace(' ', '').strip()
    hit = name_index.get(clean)
    if hit is None:
        # Log repr so any invisible chars / encoding differences are visible
        logger.warning(f"_match_stock: unmatched name={clean!r} (hex: {clean.encode().hex()})")
        return None, None, "unmatched"
    if isinstance(hit, list):
        candidates = [
            {"ts_code": r["ts_code"], "name": r["name"], "industry": r.get("industry", "")}
            for r in hit
        ]
        return None, candidates, "ambiguous"
    return hit["ts_code"], None, "matched"


def _check_duplicate(conn, account_id: str, ts_code: str, trans_type: str,
                     date: str, price: float, amount: int, time_str: str) -> str:
    """
    Check if a transaction is a duplicate.
    Returns: 'new' | 'maybe_duplicate' | 'duplicate'
    All SQL uses parameterized queries — no string interpolation of user data.
    """
    c = conn.cursor()
    c.execute(
        '''
        SELECT id, remarks FROM transactions
        WHERE account_id = ? AND ts_code = ? AND trans_type = ?
          AND date = ? AND price = ? AND abs(amount) = ?
        ''',
        (account_id, ts_code, trans_type, date, price, abs(amount))
    )
    rows = c.fetchall()
    if not rows:
        return "new"
    # Matching records found in DB
    if time_str:
        for row in rows:
            remarks = row["remarks"] or ""
            if time_str in remarks:
                return "duplicate"   # same time found in remarks → definite duplicate
        # Records exist but time not found in remarks (e.g. manually entered without screenshot remark)
        # → flag as maybe_duplicate so user can decide, NOT silently allow
        return "maybe_duplicate"
    return "maybe_duplicate"


def _ocr_to_lines(img_bytes: bytes) -> List[str]:
    """
    Run RapidOCR on image bytes.
    Groups OCR result items by Y proximity into text lines,
    sorts each line by X position, and returns joined text lines.
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
        import numpy as np
        import cv2

        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("无法解码图片，请确认图片格式正确（支持 PNG/JPG/BMP）")

        engine = RapidOCR()
        result, _ = engine(img)
        if not result:
            return []

        def _y(item):
            box = item[0]
            return sum(pt[1] for pt in box) / len(box)

        def _x(item):
            box = item[0]
            return sum(pt[0] for pt in box) / len(box)

        sorted_items = sorted(result, key=_y)

        # Adaptive threshold: 50% of median bounding-box height,
        # but at least 15px. For 1080p phone screenshots text is ~40px tall
        # → threshold ≈ 20px, which keeps same-row items together while
        # not merging adjacent rows (~90px apart).
        def _box_h(item):
            ys = [pt[1] for pt in item[0]]
            return max(ys) - min(ys)
        heights = sorted([_box_h(it) for it in sorted_items])
        median_h = heights[len(heights) // 2] if heights else 30
        y_threshold = max(15, int(median_h * 0.5))
        logger.info(f"OCR: {len(result)} regions, median_h={median_h:.0f}px, y_threshold={y_threshold}px")

        lines_items: List[List] = []
        cur_line = [sorted_items[0]]
        cur_y = _y(sorted_items[0])
        for item in sorted_items[1:]:
            y = _y(item)
            if abs(y - cur_y) <= y_threshold:
                cur_line.append(item)
            else:
                lines_items.append(cur_line)
                cur_line = [item]
                cur_y = y
        lines_items.append(cur_line)

        text_lines = []
        for line_items in lines_items:
            sorted_line = sorted(line_items, key=_x)
            text = ' '.join(item[1] for item in sorted_line)
            text_lines.append(text)

        logger.info(f"OCR grouped into {len(text_lines)} lines")
        for idx, tl in enumerate(text_lines):
            logger.debug(f"  ocr_line[{idx}]: {tl!r}")

        return text_lines

    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="rapidocr-onnxruntime 未安装，请运行: pip install rapidocr-onnxruntime opencv-python-headless"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"OCR 识别失败: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OCR failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"OCR 识别失败: {str(e)}")


def _compute_api_fields(trans_type: str, ocr_price: float,
                         ocr_quantity: int, ocr_total: float):
    """
    Compute (api_price, api_amount) from raw OCR numbers per trans_type rules.
    api_amount sign: BUY/DIVIDEND/SPLIT/RIGHTS = positive, SELL = negative.
    """
    if trans_type == "BUY":
        return ocr_price, abs(ocr_quantity)
    if trans_type == "SELL":
        return ocr_price, -abs(ocr_quantity)
    if trans_type == "DIVIDEND":
        if ocr_quantity > 0:
            return round(ocr_total / ocr_quantity, 4), abs(ocr_quantity)
        return ocr_total, 1     # only total amount available → price=total, amount=1
    if trans_type == "SPLIT":
        return 0.0, abs(ocr_quantity)
    if trans_type == "RIGHTS":
        return ocr_price, abs(ocr_quantity)
    if trans_type == "DIVIDEND_TAX":
        return ocr_total, 1
    return ocr_price, abs(ocr_quantity)


def _parse_lines(lines: List[str], year: int, keywords: dict) -> List[dict]:
    """
    Parse OCR text lines into raw transaction records.

    Each transaction occupies two consecutive lines:
      Line 1: "操作[-股票名]  价格  总金额"
      Line 2: "缩写  MM-DD [HH:MM]  数量  [手续费|--]"
    """

    def _is_skip(line: str) -> bool:
        s = line.strip()
        if not s:
            return True
        # Skip month separator lines like "2025-11 -12,425.70 -0.94%"
        if re.match(r'^\d{4}-\d{2}\b', s):
            return True
        return False

    filtered = [l.strip() for l in lines if not _is_skip(l)]
    logger.info(f"parse: {len(filtered)} non-skip lines")
    for idx, fl in enumerate(filtered):
        logger.debug(f"  filtered[{idx}]: {fl!r}")

    results = []
    row_index = 0
    i = 0

    # Standalone-number pattern: a single token that looks like a price/amount
    _num_re = re.compile(r'^-?[\d,]+\.?\d*$')

    while i < len(filtered):
        line1 = filtered[i]
        extra_advance = 0  # if we consumed an extra line for the total amount

        # Split line1 tokens; last 2 should be numbers (price, total)
        tokens1 = line1.split()

        # ── Fallback: if line1 has only 2 tokens (op-name + price),
        # the total amount may be a separate OCR region on the next line.
        if len(tokens1) == 2 and i + 1 < len(filtered):
            nxt = filtered[i + 1]
            if _num_re.match(nxt):
                tokens1 = tokens1 + [nxt]
                extra_advance = 1   # consumed filtered[i+1] as the total

        if len(tokens1) < 3:
            i += 1
            continue

        try:
            ocr_total = abs(float(tokens1[-1].replace(',', '')))
            ocr_price = float(tokens1[-2].replace(',', ''))
            op_name_part = ' '.join(tokens1[:-2])
        except ValueError:
            i += 1
            continue

        # Split operation and stock name on first "-"
        if '-' in op_name_part:
            op_text, stock_name = op_name_part.split('-', 1)
            op_text = op_text.strip()
            stock_name = stock_name.strip()
        else:
            op_text = op_name_part.strip()
            stock_name = ""

        trans_type = _classify_op(op_text, keywords)

        if trans_type == "IGNORE":
            i += 2 + extra_advance
            continue
        if trans_type is None:
            i += 1
            continue
        if not stock_name:
            # Non-trade operation without stock name (e.g. 证券转银行)
            i += 2 + extra_advance
            continue

        # ── Parse line2: short-code  MM-DD [HH:MM]  quantity  [fee|--]
        # line2 is right after the (possibly extended) line1
        line2 = filtered[i + 1 + extra_advance] if i + 1 + extra_advance < len(filtered) else ""
        date_str = ""
        time_str = ""
        ocr_quantity = 0

        tokens2 = line2.split()
        for j, tok in enumerate(tokens2):
            # Use re.search so we can match date inside merged tokens like "买03-23" or "03-2314:47"
            m_date = re.search(r'(\d{2}-\d{2})', tok)
            if not m_date:
                continue
            date_str = f"{year}-{m_date.group(1)}"
            # Check for time: either merged in the same token after the date, or in the next token
            after_date = tok[m_date.end():]
            m_time_inline = re.search(r'(\d{2}:\d{2})', after_date)
            if m_time_inline:
                time_str = m_time_inline.group(1)
                qty_idx = j + 1
            elif j + 1 < len(tokens2) and re.search(r'^\d{2}:\d{2}$', tokens2[j + 1]):
                time_str = tokens2[j + 1]
                qty_idx = j + 2
            else:
                qty_idx = j + 1
            # Find quantity: first integer-like token after date/time
            while qty_idx < len(tokens2):
                try:
                    val = abs(int(float(tokens2[qty_idx].replace(',', ''))))
                    if val > 0:
                        ocr_quantity = val
                        break
                except ValueError:
                    pass
                qty_idx += 1
            break

        if not date_str:
            logger.debug(f"  skip (no date): line1={line1!r} line2={line2!r}")
            i += 2 + extra_advance
            continue

        logger.debug(f"  parsed: {trans_type} {stock_name} price={ocr_price} qty={ocr_quantity} total={ocr_total} date={date_str} time={time_str}")

        api_price, api_amount = _compute_api_fields(
            trans_type, ocr_price, ocr_quantity, ocr_total
        )

        results.append({
            "row_index": row_index,
            "trans_type": trans_type,
            "stock_name": stock_name,
            "ts_code": None,
            "matched_name": None,
            "match_status": "unmatched",
            "duplicate_status": "unknown",
            "candidates": None,
            "ocr_price": ocr_price,
            "ocr_quantity": ocr_quantity,
            "ocr_total": ocr_total,
            "api_price": api_price,
            "api_amount": api_amount,
            "date": date_str,
            "time": time_str,
        })
        row_index += 1
        i += 2 + extra_advance

    return results


# ── POST /screenshot/parse ──

@router.post("/screenshot/parse")
async def parse_screenshot(
    file: UploadFile = File(...),
    year: Optional[int] = Form(None),
    account_id: Optional[str] = Form(None),
):
    """
    OCR a screenshot image, parse transactions, match stock names, and check duplicates.
    Returns structured parse results ready for frontend preview.
    """
    if year is None:
        year = sh_now().year

    filename = (file.filename or "").lower()
    if not filename.endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp")):
        raise HTTPException(status_code=400, detail="仅支持 PNG/JPG/JPEG/BMP/WEBP 图片")

    img_bytes = await read_limited_upload(file, MAX_IMAGE_UPLOAD_BYTES)
    lines = _ocr_to_lines(img_bytes)
    keywords = _load_keywords()
    records = _parse_lines(lines, year, keywords)

    # ── Stock name matching ──
    name_index = _build_name_index()
    for rec in records:
        ts_code, candidates, status = _match_stock(rec["stock_name"], name_index)
        rec["match_status"] = status
        rec["ts_code"] = ts_code
        rec["matched_name"] = rec["stock_name"] if status == "matched" else None
        if candidates:
            rec["candidates"] = candidates

    # ── Duplicate pre-check (parse stage) ──
    if account_id:
        with get_db() as conn:
            for rec in records:
                if rec["match_status"] == "matched" and rec["ts_code"]:
                    rec["duplicate_status"] = _check_duplicate(
                        conn,
                        account_id,
                        rec["ts_code"],
                        rec["trans_type"],
                        rec["date"],
                        rec["api_price"],
                        rec["api_amount"],
                        rec["time"],
                    )
                else:
                    rec["duplicate_status"] = "unknown"
    else:
        for rec in records:
            rec["duplicate_status"] = "unknown"

    # ── Build summary ──
    total = len(records)
    matched = sum(1 for r in records if r["match_status"] == "matched")
    unmatched = sum(1 for r in records if r["match_status"] == "unmatched")
    ambiguous = sum(1 for r in records if r["match_status"] == "ambiguous")
    duplicates = sum(1 for r in records if r["duplicate_status"] in ("duplicate", "maybe_duplicate"))
    errors = sum(1 for r in records if r["match_status"] == "cache_unavailable")

    # Remove None candidates fields from response
    clean_records = []
    for rec in records:
        r = dict(rec)
        if r.get("candidates") is None:
            r.pop("candidates", None)
        clean_records.append(r)

    return {
        "status": "success",
        "data": clean_records,
        "summary": {
            "total": total,
            "matched": matched,
            "unmatched": unmatched,
            "ambiguous": ambiguous,
            "duplicates": duplicates,
            "errors": errors,
        },
    }


# ── POST /screenshot/import/{account_id} ──

class ImportTransaction(BaseModel):
    ts_code: str
    name: str
    trans_type: str
    price: float
    amount: int
    date: str
    time: str = ""


class ImportRequest(BaseModel):
    transactions: List[ImportTransaction]


@router.post("/screenshot/import/{account_id}")
def import_screenshot(account_id: str, req: ImportRequest):
    """
    Batch-import confirmed transactions from screenshot parse result.
    Performs second-pass duplicate check before each insert.
    BUY transactions are automatically added to t_trading_items.
    """
    from web.backend.api.transactions import TransactionCreate, create_transaction

    results = []

    for item in req.transactions:
        # ── Second-pass duplicate check ──
        with get_db() as conn:
            dup_status = _check_duplicate(
                conn,
                account_id,
                item.ts_code,
                item.trans_type,
                item.date,
                item.price,
                abs(item.amount),
                item.time,
            )

        if dup_status == "duplicate":
            results.append({
                "ts_code": item.ts_code,
                "name": item.name,
                "status": "duplicate_skipped",
            })
            continue

        # ── Create transaction ──
        remarks = f"截图导入 {item.time}".strip() if item.time else "截图导入"
        try:
            trans = TransactionCreate(
                ts_code=item.ts_code,
                name=item.name,
                trans_type=item.trans_type,
                price=item.price,
                amount=item.amount,
                date=item.date,
                fee=0.0,
                stamp_tax=0.0,
                remarks=remarks,
            )
            resp = create_transaction(account_id, trans)
            trans_id = resp.get("id") if isinstance(resp, dict) else None

            # ── Auto-add BUY/SELL to t_trading_items ──
            # NOTE: do NOT rely on resp["id"] / c.lastrowid from create_transaction —
            # if a new holding row was INSERT-ed after the transaction row, lastrowid
            # reflects the holdings row ID, not the transaction row ID.
            # Instead, query transactions by the unique remarks string.
            if item.trans_type in ("BUY", "SELL"):
                trade_side = item.trans_type
                with get_db() as conn:
                    c2 = conn.cursor()
                    c2.execute(
                        "SELECT id FROM transactions "
                        "WHERE account_id = ? AND ts_code = ? AND trans_type = ? "
                        "  AND date = ? AND price = ? AND remarks = ? "
                        "ORDER BY id DESC LIMIT 1",
                        (account_id, item.ts_code, trade_side, item.date, item.price, remarks),
                    )
                    row = c2.fetchone()
                    if row:
                        c2.execute(
                            "INSERT INTO t_trading_items "
                            "  (account_id, transaction_id, ts_code, name, "
                            "   buy_price, buy_quantity, buy_date, trade_side) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(account_id, transaction_id, trade_side) DO UPDATE "
                            "  SET removed_at = NULL "
                            "  WHERE t_trading_items.removed_at IS NOT NULL",
                            (account_id, row["id"], item.ts_code, item.name,
                             abs(item.price), abs(item.amount), item.date, trade_side),
                        )
                        conn.commit()
                    else:
                        logger.warning(
                            f"t_trading_items: could not find transaction for "
                            f"{item.ts_code} date={item.date} price={item.price} remarks={remarks!r}"
                        )

            results.append({"ts_code": item.ts_code, "name": item.name, "status": "created"})

        except HTTPException as e:
            logger.error(f"HTTPException creating transaction {item.ts_code}: {e.detail}")
            results.append({
                "ts_code": item.ts_code,
                "name": item.name,
                "status": "error",
                "message": e.detail,
            })
        except Exception as e:
            logger.error(f"Error creating transaction {item.ts_code}: {e}", exc_info=True)
            results.append({
                "ts_code": item.ts_code,
                "name": item.name,
                "status": "error",
                "message": str(e),
            })

    created = sum(1 for r in results if r["status"] == "created")
    skipped = sum(1 for r in results if r["status"] == "duplicate_skipped")
    errors = sum(1 for r in results if r["status"] == "error")

    return {
        "status": "success",
        "results": results,
        "summary": {
            "created": created,
            "duplicate_skipped": skipped,
            "errors": errors,
        },
    }
