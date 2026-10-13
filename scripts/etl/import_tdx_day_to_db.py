# -*- coding: utf-8 -*-
"""
import_tdx_day_to_db.py
-------------------------------------
从通达信本地日线(.day)文件导入 A 股日线到 SQLite 的 daily_price 表。

要求：
1）通达信已下载完整日线数据（vipdoc\\sh\\lday, vipdoc\\sz\\lday）
2）已有 stock_industry_base.csv（含 ts_code 列）
3）market.db 已存在或自动创建（表结构与 get_market_data_db_robust.py 一致）

用法示例（PowerShell）：
  python import_tdx_day_to_db.py `
    --tdx-root "D:\Stocks\平安证券" `
    --base "stock_industry_base.csv" `
    --db "market.db"

说明：
- 多次运行是幂等的：相同 (ts_code, trade_date) 会被 UPDATE 覆盖，不会重复插入。
- 默认跳过 .BJ 北交所标的。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import struct
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


# =========================================================
# 通用工具
# =========================================================

def code6_to_ts(code6: str, market: str) -> str:
    """根据 6 位代码和市场（sh/sz/bj）生成 ts_code（仅用于打印调试）"""
    s = str(code6).zfill(6)
    m = market.lower()
    if m == "sh":
        return f"{s}.SH"
    if m == "sz":
        return f"{s}.SZ"
    if m == "bj":
        return f"{s}.BJ"
    return f"{s}.??"


def ts_to_tdx_path(root: Path, ts_code: str) -> Optional[Path]:
    """
    根据 ts_code 生成通达信 .day 文件路径。
    示例：000001.SZ -> vipdoc\\sz\\lday\\sz000001.day
         600000.SH -> vipdoc\\sh\\lday\\sh600000.day
    """
    if not isinstance(ts_code, str) or "." not in ts_code:
        return None
    code6, exch = ts_code.split(".")
    exch = exch.upper()
    if exch == "SZ":
        sub = "sz"
    elif exch == "SH":
        sub = "sh"
    elif exch == "BJ":
        sub = "bj"
    else:
        return None

    return root / "vipdoc" / sub / "lday" / f"{sub}{code6}.day"


# =========================================================
# SQLite 相关
# =========================================================

def connect_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-50000;")  # ~50MB
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_price (
        ts_code     TEXT NOT NULL,
        trade_date  TEXT NOT NULL,     -- YYYY-MM-DD
        open        REAL,
        high        REAL,
        low         REAL,
        close       REAL,
        volume      REAL,
        amount      REAL,
        PRIMARY KEY (ts_code, trade_date)
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_snapshot (
        ts_code     TEXT NOT NULL,
        trade_date  TEXT NOT NULL,
        latest      REAL,
        pb          REAL,
        pe_ttm      REAL,
        asof_time   TEXT,
        PRIMARY KEY (ts_code, trade_date)
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dp_ts_date ON daily_price(ts_code, trade_date DESC);")
    conn.commit()


def upsert_daily_price(conn: sqlite3.Connection, ts_code: str, df_hist: pd.DataFrame) -> None:
    """
    将 DataFrame 写入 daily_price。
    df_hist 需至少包含列：日期, 开盘, 最高, 最低, 收盘, 成交量, 成交额
    """
    if df_hist is None or df_hist.empty:
        return

    must = ["日期", "开盘", "最高", "最低", "收盘"]
    if any(c not in df_hist.columns for c in must):
        return

    vol_col = "成交量" if "成交量" in df_hist.columns else None
    amt_col = "成交额" if "成交额" in df_hist.columns else None

    recs = []
    for _, row in df_hist.iterrows():
        try:
            trade_date = pd.to_datetime(row["日期"]).strftime("%Y-%m-%d")
        except Exception:
            continue

        def _f(x):
            try:
                return float(x)
            except Exception:
                return None

        recs.append((
            ts_code,
            trade_date,
            _f(row["开盘"]),
            _f(row["最高"]),
            _f(row["最低"]),
            _f(row["收盘"]),
            _f(row[vol_col]) if vol_col and (vol_col in row.index) else None,
            _f(row[amt_col]) if amt_col and (amt_col in row.index) else None,
        ))

    if not recs:
        return

    conn.executemany("""
        INSERT INTO daily_price (ts_code, trade_date, open, high, low, close, volume, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ts_code, trade_date) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            amount=excluded.amount;
    """, recs)
    conn.commit()


# =========================================================
# 通达信 .day 解析
# =========================================================

def parse_tdx_day_file(path: Path) -> pd.DataFrame:
    """
    解析通达信日线 .day 文件。

    每条记录 32 字节，小端：
      int   date        YYYYMMDD
      int   open        *100
      int   high        *100
      int   low         *100
      int   close       *100
      float amount      成交额，元
      int   vol         成交量，股
      int   reserved    预留

    返回列：日期, 开盘, 最高, 最低, 收盘, 成交量, 成交额
    """
    data = path.read_bytes()
    if not data:
        return pd.DataFrame()

    rec_size = 32
    n = len(data) // rec_size
    if n == 0:
        return pd.DataFrame()

    recs = []
    for i in range(n):
        chunk = data[i * rec_size : (i + 1) * rec_size]
        if len(chunk) < rec_size:
            continue
        # < 小端, I 无符号 int, f float
        date_i, open_i, high_i, low_i, close_i, amount_f, vol_i, _ = struct.unpack("<IIIIIfII", chunk)
        try:
            dt = datetime.strptime(str(date_i), "%Y%m%d").strftime("%Y-%m-%d")
        except Exception:
            continue

        recs.append({
            "日期": dt,
            "开盘": open_i / 100.0,
            "最高": high_i / 100.0,
            "最低": low_i / 100.0,
            "收盘": close_i / 100.0,
            "成交额": float(amount_f),
            "成交量": int(vol_i),
        })

    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs)
    # 按日期排序，避免乱序
    df.sort_values("日期", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# =========================================================
# 主流程
# =========================================================

def _default_db_path():
    """data/market.db 优先；兼容项目根目录下的旧文件。"""
    try:
        from web.backend.config import resolve_db_path
        return resolve_db_path("market.db")
    except ImportError:
        pass
    # 独立运行、无法导入 web.backend 时：按脚本位置定位项目根目录，
    # 用绝对路径并确保 data/ 存在，避免 CWD 相关的相对路径打不开库。
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_dir = os.path.join(root, "data")
    new_path = os.path.join(data_dir, "market.db")
    old_path = os.path.join(root, "market.db")
    if os.path.exists(new_path) or not os.path.exists(old_path):
        os.makedirs(data_dir, exist_ok=True)
        return new_path
    return old_path


def main():
    ap = argparse.ArgumentParser(description="从通达信本地 .day 日线导入到 market.db 的 daily_price 表")
    ap.add_argument("--tdx-root", required=True, help="通达信安装根目录，例如 C:\\new_tdx")
    ap.add_argument("--base", default="stock_industry_base.csv", help="基础股票清单 CSV（需含 ts_code 列）")
    ap.add_argument("--db", default=_default_db_path(), help="SQLite 数据库路径")
    ap.add_argument("--skip-bj", type=int, default=1, help="是否跳过 .BJ 北交所标的，1=跳过，0=不跳过")
    ap.add_argument("--batch", type=int, default=0, help="调试用：只处理前 N 只股票（0 表示全量）")

    args = ap.parse_args()
    tdx_root = Path(args.tdx_root).expanduser().resolve()
    if not tdx_root.exists():
        raise SystemExit(f"通达信目录不存在：{tdx_root}")

    print(f"通达信根目录：{tdx_root}")

    # 读基础表
    base = pd.read_csv(args.base, dtype={"ts_code": str})
    if "ts_code" not in base.columns:
        raise SystemExit("基础表中缺少 ts_code 列")

    base = base.drop_duplicates("ts_code").copy()
    if args.skip_bj:
        base = base[~base["ts_code"].str.endswith(".BJ")].reset_index(drop=True)

    if args.batch and args.batch > 0:
        base = base.head(args.batch).reset_index(drop=True)

    print(f"标的数量：{len(base)}（skip_bj={args.skip_bj}）")

    conn = connect_db(args.db)
    init_db(conn)

    missing_files = []
    empty_files = []
    imported = 0

    for i, row in base.iterrows():
        ts_code = row["ts_code"]
        day_path = ts_to_tdx_path(tdx_root, ts_code)
        if day_path is None:
            missing_files.append({"ts_code": ts_code, "reason": "无法映射路径"})
            continue

        if not day_path.exists():
            missing_files.append({"ts_code": ts_code, "reason": f"文件不存在：{day_path.name}"})
            if i % 500 == 0:
                print(f"[{i}/{len(base)}] {ts_code} 缺少 .day 文件：{day_path.name}")
            continue

        df_hist = parse_tdx_day_file(day_path)
        if df_hist.empty:
            empty_files.append({"ts_code": ts_code, "file": str(day_path)})
            if i % 500 == 0:
                print(f"[{i}/{len(base)}] {ts_code} 文件为空或解析失败：{day_path.name}")
            continue

        upsert_daily_price(conn, ts_code, df_hist)
        imported += 1

        if (i + 1) % 500 == 0 or (i + 1) == len(base):
            print(f"[{i+1}/{len(base)}] 已导入 {imported} 只标的，当前：{ts_code}")

    conn.close()

    # 输出简单统计
    print("\n=== 导入完成 ===")
    print(f"成功导入标的数：{imported}")
    if missing_files:
        df_miss = pd.DataFrame(missing_files)
        miss_path = Path("logs/tdx_missing_files.csv")
        miss_path.parent.mkdir(parents=True, exist_ok=True)
        df_miss.to_csv(miss_path, index=False, encoding="utf-8-sig")
        print(f"缺失 .day 文件的标的数：{len(missing_files)}，清单：{miss_path}")

    if empty_files:
        df_empty = pd.DataFrame(empty_files)
        empty_path = Path("logs/tdx_empty_files.csv")
        empty_path.parent.mkdir(parents=True, exist_ok=True)
        df_empty.to_csv(empty_path, index=False, encoding="utf-8-sig")
        print(f".day 文件为空/解析失败的标的数：{len(empty_files)}，清单：{empty_path}")


if __name__ == "__main__":
    main()
