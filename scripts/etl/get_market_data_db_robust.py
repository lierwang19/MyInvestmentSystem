# -*- coding: utf-8 -*-
"""
get_market_data_db_robust.py  — 尾段增量优先 + TuShare 兜底 + end-target=prev
------------------------------------------------------------------------
功能概要
1) 读取 stock_industry_base.csv（需含: ts_code,name,tdx_l1），自动跳过北交所 .BJ
2) 建/连 SQLite market.db：daily_price, daily_snapshot
3) 快照：可选择跳过（默认跳过；东财快照常被限）
4) 历史日线：按可配置顺序兜底抓取，默认  腾讯→新浪→TuShare→东财
5) 增量更新：仅补“库内该票 max(trade_date)+1”之后 → 直到 end-target 指定的目标日期
6) 支持 end-target：
   - today：以最近交易日为目标（默认）
   - prev ：以前一交易日为目标（适合白天校验避免误报）
   - date ：自定义 --end-date=YYYY-MM-DD
7) 覆盖刷新：--force-refresh N 以“近 N 自然日”覆盖式更新，处理晚披露/修订
8) 导出 market_enriched.csv（含近 252 交易日低点/日期/涨幅）
9) 失败日志：logs/failed_hist.csv / logs/failed_spot.csv
10) 可选：--retry-failed-only 仅处理上次 logs/failed_hist.csv 的清单

依赖
  pip install -U akshare pandas
  可选兜底：pip install -U tushare
  （如使用 TuShare，需设置环境变量 TUSHARE_TOKEN）

常用示例
  # 白天追尾建议以前一交易日为目标、跳过快照、扩大覆盖窗口
  python get_market_data_db_robust.py ^
    --skip-spot 1 ^
    --order tx,sina,ts,em ^
    --hist-max-retry 5 ^
    --sleep 0.40 ^
    --force-refresh 900 ^
    --end-target prev

  # 指定固定截止日
  python get_market_data_db_robust.py --end-target date --end-date 2025-11-08 --skip-spot 1

说明
- 若使用 TuShare，请先：
  Windows PowerShell:
    $env:TUSHARE_TOKEN="你的token"
  或在系统环境变量中设置 TUSHARE_TOKEN 后再运行

"""


from __future__ import annotations

import argparse
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional, List
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
import pandas as pd

try:
    import akshare as ak
except ModuleNotFoundError:
    raise SystemExit(
        "未检测到 AkShare：请先安装\n"
        "  pip install -U akshare pandas\n"
        "或使用镜像：\n"
        "  pip install -U akshare -i https://pypi.tuna.tsinghua.edu.cn/simple"
    )

# ===================== 默认配置（可被命令行覆盖） =====================
DEFAULT_BASE_FILE   = "stock_industry_base.csv"
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
DEFAULT_DB_PATH     = _default_db_path()
DEFAULT_OUT_FILE    = "market_enriched.csv"

DEFAULT_SKIP_SPOT   = True
DEFAULT_ORDER       = "tx,sina,em,ts"  # 可选：tx/sina/ts/em
DEFAULT_HIST_MAX_RETRY = 3
DEFAULT_SPOT_MAX_RETRY = 4
DEFAULT_SLEEP       = 0.40
DEFAULT_BACKOFF_BASE   = 1.0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_JITTER_MIN     = 0.10
DEFAULT_JITTER_MAX     = 0.40

DEFAULT_RETRY_FAILED_ONLY = 0
DEFAULT_BATCH        = 0
DEFAULT_SAMPLE_MODE  = "random"  # head|random|per_industry

DEFAULT_ADJUST_MODE  = "qfq"     # 对 ak/tx/sina/em 的复权口径；ts 会自动映射为 qfq/hfq/None
DEFAULT_LOOKBACK_CAL_DAYS_INIT = 600
DEFAULT_FORCE_REFRESH_CAL_DAYS = 0

DEFAULT_END_TARGET   = "today"   # today|prev|date
DEFAULT_END_DATE     = ""        # 当 end-target=date 时必填 YYYY-MM-DD

N_TRADING_DAYS_FOR_LOW = 252

LOG_DIR = Path("./logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ===================== 工具函数 =====================
def ts_to_ak_symbol(ts_code: str) -> str:
    code6, exch = ts_code.split(".")
    e = exch.upper()
    if e == "SZ": return f"sz{code6}"
    if e == "SH": return f"sh{code6}"
    if e == "BJ": return f"bj{code6}"
    raise ValueError(f"未知交易所: {ts_code}")

def code6_to_ts(code6: str) -> str:
    s = str(code6).zfill(6)
    if s.startswith("6"):
        return f"{s}.SH"
    if s.startswith(("43","83","87","88","89")):
        return f"{s}.BJ"
    return f"{s}.SZ"

def last_trade_day_and_prev(today: Optional[date] = None) -> tuple[str, str]:
    base_day = today or date.today()
    cal = ak.tool_trade_date_hist_sina()
    # 统一日期列
    if "trade_date" in cal.columns:
        cal["dt"] = pd.to_datetime(cal["trade_date"])
    elif "日期" in cal.columns:
        cal["dt"] = pd.to_datetime(cal["日期"])
    else:
        cal["dt"] = pd.to_datetime(cal.iloc[:, 0])
    cal = cal[cal["dt"] <= pd.to_datetime(base_day)].sort_values("dt")
    last_dt = cal["dt"].max()
    prev_dt = cal["dt"].iloc[-2] if len(cal) >= 2 else last_dt
    return last_dt.strftime("%Y-%m-%d"), prev_dt.strftime("%Y-%m-%d")

def yyyymmdd(s: str) -> str:
    return pd.to_datetime(s).strftime("%Y%m%d")

def sleep_backoff(attempt: int, base=DEFAULT_BACKOFF_BASE, factor=DEFAULT_BACKOFF_FACTOR,
                  jitter=(DEFAULT_JITTER_MIN, DEFAULT_JITTER_MAX)):
    wait = base * (factor ** attempt) + random.uniform(*jitter)
    time.sleep(wait)


# ===================== DB 基础 =====================
def connect_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-50000;")  # ~50MB
    return conn

def init_db(conn: sqlite3.Connection):
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
    if df_hist is None or df_hist.empty:
        return
    rename_map = {
        "date": "日期", "trade_date": "日期",
        "open": "开盘", "open_price": "开盘", "开盘价": "开盘",
        "close": "收盘", "close_price": "收盘", "收盘价": "收盘",
        "high": "最高", "high_price": "最高", "最高价": "最高",
        "low": "最低", "low_price": "最低", "最低价": "最低",
        "vol": "成交量", "volume": "成交量",
        "amount": "成交额", "成交金额": "成交额",
    }
    for k, v in rename_map.items():
        if k in df_hist.columns and v not in df_hist.columns:
            df_hist.rename(columns={k: v}, inplace=True)

    must = ["日期","开盘","最高","最低","收盘"]
    if any(c not in df_hist.columns for c in must):
        return

    vol_col = "成交量" if "成交量" in df_hist.columns else None
    amt_col = "成交额" if "成交额" in df_hist.columns else None

    recs = []
    for _, row in df_hist.iterrows():
        trade_date = pd.to_datetime(row["日期"]).strftime("%Y-%m-%d")

        def _f(x):
            try:
                return float(x)
            except Exception:
                return None

        recs.append((
            ts_code, trade_date,
            _f(row["开盘"]), _f(row["最高"]), _f(row["最低"]), _f(row["收盘"]),
            _f(row[vol_col]) if vol_col and (vol_col in row.index) else None,
            _f(row[amt_col]) if amt_col and (amt_col in row.index) else None,
        ))

    conn.executemany("""
        INSERT INTO daily_price (ts_code, trade_date, open, high, low, close, volume, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ts_code, trade_date) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume, amount=excluded.amount;
    """, recs)
    conn.commit()

def upsert_snapshot_for_trade_date(conn: sqlite3.Connection, spot_df: pd.DataFrame, trade_date: str) -> None:
    if spot_df.empty:
        return
    nowts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    recs = []

    def _f(x):
        try:
            return float(x)
        except Exception:
            return None

    for _, row in spot_df.iterrows():
        recs.append((
            row["ts_code"], trade_date,
            _f(row.get("latest")), _f(row.get("pb")), _f(row.get("pe_ttm")), nowts
        ))
    conn.executemany("""
        INSERT INTO daily_snapshot (ts_code, trade_date, latest, pb, pe_ttm, asof_time)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(ts_code, trade_date) DO UPDATE SET
            latest=excluded.latest, pb=excluded.pb, pe_ttm=excluded.pe_ttm, asof_time=excluded.asof_time;
    """, recs)
    conn.commit()

def get_max_trade_date(conn: sqlite3.Connection, ts_code: str) -> Optional[str]:
    row = conn.execute("SELECT MAX(trade_date) FROM daily_price WHERE ts_code=?", (ts_code,)).fetchone()
    return row[0] if row and row[0] else None

def get_tail_from_db(conn: sqlite3.Connection, ts_code: str, n: int) -> pd.DataFrame:
    df = pd.read_sql_query("""
        SELECT trade_date, open, high, low, close, volume, amount
        FROM daily_price
        WHERE ts_code=?
        ORDER BY trade_date DESC
        LIMIT ?;
    """, conn, params=(ts_code, n))
    if df.empty:
        return df
    return df.sort_values("trade_date")


# ===================== 抓取适配（AK） =====================
def fetch_hist_em(symbol: str, start: str, end: str, adjust: Optional[str]) -> pd.DataFrame:
    return ak.stock_zh_a_hist(symbol=symbol, period="daily",
                              start_date=start, end_date=end, adjust=adjust)

def fetch_hist_sina(symbol: str, start: str, end: str, adjust: Optional[str]) -> pd.DataFrame:
    return ak.stock_zh_a_daily(symbol=symbol, start_date=start, end_date=end, adjust=adjust)

def fetch_hist_tx(symbol: str, start: str, end: str, adjust: Optional[str]) -> pd.DataFrame:
    df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end, adjust=adjust)
    # TX API returns 'amount' which is actually volume in 手(lots), not monetary amount
    # Convert: lots × 100 = shares
    if df is not None and not df.empty and 'amount' in df.columns and 'volume' not in df.columns:
        df['volume'] = df['amount'] * 100
        df.drop(columns=['amount'], inplace=True)
    return df

# ===================== TuShare 兜底（可选） =====================
def fetch_hist_ts(ts_code: str, start: str, end: str, adjust: Optional[str]) -> pd.DataFrame:
    """
    TuShare 兜底：优先 pro_bar（带复权），退化到 daily（不复权）。
    - start/end: YYYYMMDD
    - adjust: 'qfq'|'hfq'|None
    统一返回列: 日期/开盘/最高/最低/收盘/成交量/成交额
    注意：daily 的 vol 为手、amount 为千元；此处统一转为股与元。
    """
    token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("ts_token")
    if not token:
        raise ModuleNotFoundError("缺少 TUSHARE_TOKEN 环境变量")

    import tushare as ts  # type: ignore
    ts.set_token(token)
    pro = ts.pro_api()

    # 映射复权口径
    adj = None
    if adjust in ("qfq", "hfq"):
        adj = adjust

    # 先试 pro_bar（复权、性能更好）
    try:
        df = ts.pro_bar(ts_code=ts_code, start_date=start, end_date=end, adj=adj, freq="D")
        if df is not None and not df.empty:
            df = df.sort_values("trade_date")
            out = pd.DataFrame({
                "日期": pd.to_datetime(df["trade_date"]),
                "开盘": pd.to_numeric(df["open"], errors="coerce"),
                "最高": pd.to_numeric(df["high"], errors="coerce"),
                "最低": pd.to_numeric(df["low"], errors="coerce"),
                "收盘": pd.to_numeric(df["close"], errors="coerce"),
                "成交量": pd.to_numeric(df["vol"], errors="coerce") * 100.0,      # 手->股
                "成交额": pd.to_numeric(df["amount"], errors="coerce") * 1000.0,  # 千元->元
            })
            return out
    except Exception:
        pass

    # 退化到 daily（不复权）
    df2 = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
    if df2 is None or df2.empty:
        return pd.DataFrame()

    df2 = df2.sort_values("trade_date")
    out2 = pd.DataFrame({
        "日期": pd.to_datetime(df2["trade_date"]),
        "开盘": pd.to_numeric(df2["open"], errors="coerce"),
        "最高": pd.to_numeric(df2["high"], errors="coerce"),
        "最低": pd.to_numeric(df2["low"], errors="coerce"),
        "收盘": pd.to_numeric(df2["close"], errors="coerce"),
        "成交量": pd.to_numeric(df2["vol"], errors="coerce") * 100.0,     # 手->股
        "成交额": pd.to_numeric(df2["amount"], errors="coerce") * 1000.0, # 千元->元
    })
    return out2


# ===================== 兜底路由 =====================
def fetch_hist_with_fallback(ts_code: str, symbol: str, start: str, end: str,
                             adjust: Optional[str], order: List[str],
                             hist_max_retry: int, sleep_step: float
                             ) -> tuple:  # -> (pd.DataFrame, str) hit_source
    """
    根据 order 顺序尝试若干来源；每个来源带重试。
    支持来源：'tx' | 'sina' | 'ts' | 'em'

    Returns:
        (df, hit_source): df 为结果 DataFrame，hit_source 为命中的数据源名称
    """
    providers = []
    for tag in order:
        t = tag.strip().lower()
        if t in ("tx", "sina", "ts", "em"):
            providers.append(t)

    last_err = None

    for prov in providers:
        for attempt in range(hist_max_retry):
            try:
                if prov == "tx":
                    df = fetch_hist_tx(symbol, start, end, adjust)
                elif prov == "sina":
                    df = fetch_hist_sina(symbol, start, end, adjust)
                elif prov == "em":
                    df = fetch_hist_em(symbol, start, end, adjust)
                elif prov == "ts":
                    df = fetch_hist_ts(ts_code, start, end, adjust)
                else:
                    df = pd.DataFrame()

                if df is not None and not df.empty:
                    return df, prov
            except Exception as e:
                last_err = e
                sleep_backoff(attempt)
        time.sleep(sleep_step)  # 换源前小憩，避免触发风控

    raise RuntimeError(f"hist failed with order={providers}, last_err={last_err}")


# ===================== 主流程 =====================
def main():
    ap = argparse.ArgumentParser(description="稳健抓取 A 股日线（尾段增量 + TuShare 兜底 + end-target=prev）")
    ap.add_argument("--base", default=DEFAULT_BASE_FILE)
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    ap.add_argument("--out", default=DEFAULT_OUT_FILE)

    ap.add_argument("--skip-spot", type=int, default=1 if DEFAULT_SKIP_SPOT else 0)
    ap.add_argument("--order", default=DEFAULT_ORDER)  # 逗号分隔
    ap.add_argument("--hist-max-retry", type=int, default=DEFAULT_HIST_MAX_RETRY)
    ap.add_argument("--spot-max-retry", type=int, default=DEFAULT_SPOT_MAX_RETRY)
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)

    ap.add_argument("--retry-failed-only", type=int, default=DEFAULT_RETRY_FAILED_ONLY)
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--log-dir", default="", help="隔离日志目录（drip_sync 使用），为空时用默认 logs/")
    ap.add_argument("--sample-mode", default=DEFAULT_SAMPLE_MODE, choices=["head","random","per_industry"])

    ap.add_argument("--adjust", default=DEFAULT_ADJUST_MODE, choices=["qfq","hfq","none","None"])
    ap.add_argument("--lookback-init", type=int, default=DEFAULT_LOOKBACK_CAL_DAYS_INIT)
    ap.add_argument("--force-refresh", type=int, default=DEFAULT_FORCE_REFRESH_CAL_DAYS)

    ap.add_argument("--end-target", default=DEFAULT_END_TARGET, choices=["today","prev","date"])
    ap.add_argument("--end-date", default=DEFAULT_END_DATE)

    args = ap.parse_args()

    global LOG_DIR
    if args.log_dir:
        LOG_DIR = Path(args.log_dir)
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    BASE_FILE = args.base
    DB_PATH   = args.db
    OUT_FILE  = args.out

    SKIP_SPOT = bool(args.skip-spot) if hasattr(args, "skip-spot") else bool(args.skip_spot)  # 兼容属性名
    SKIP_SPOT = bool(args.skip_spot)  # 修正
    ORDER_SOURCES = [x.strip() for x in args.order.split(",") if x.strip()]
    HIST_MAX_RETRY = args.hist_max_retry
    SPOT_MAX_RETRY = args.spot_max_retry
    SLEEP_PER_FETCH = args.sleep

    RETRY_FAILED_ONLY = bool(args.retry_failed_only)
    BATCH_LIMIT = args.batch
    SAMPLE_MODE = args.sample_mode

    ADJUST_MODE = args.adjust if args.adjust.lower() != "none" else None
    LOOKBACK_CAL_DAYS_INIT = args.lookback_init
    FORCE_REFRESH_CAL_DAYS = args.force_refresh

    END_TARGET = args.end_target
    END_DATE   = args.end_date

    # 读取基础表与过滤
    base = pd.read_csv(BASE_FILE, dtype={"ts_code": str})[["ts_code","name","tdx_l1"]].drop_duplicates("ts_code")
    base = base[~base["ts_code"].str.endswith(".BJ")].reset_index(drop=True)  # 双保险跳过北交所

    # 仅重试失败清单
    if RETRY_FAILED_ONLY and (LOG_DIR/"failed_hist.csv").exists():
        failed = pd.read_csv(LOG_DIR/"failed_hist.csv")
        codes = set(str(x).strip() for x in failed["ts_code"] if pd.notna(x))
        base = base[base["ts_code"].isin(codes)].reset_index(drop=True)

    # 采样
    if BATCH_LIMIT and BATCH_LIMIT > 0:
        if SAMPLE_MODE == "head":
            base = base.head(BATCH_LIMIT)
        elif SAMPLE_MODE == "random":
            base = base.sample(n=min(BATCH_LIMIT, len(base)), random_state=2025) \
                       .sort_values("ts_code").reset_index(drop=True)
        elif SAMPLE_MODE == "per_industry":
            k = max(1, BATCH_LIMIT // max(1, base["tdx_l1"].nunique()))
            base = base.groupby("tdx_l1", group_keys=False).head(k).head(BATCH_LIMIT).reset_index(drop=True)

    conn = connect_db(DB_PATH)
    init_db(conn)

    # 计算 end-target（交易日日历）
    last_td, prev_td = last_trade_day_and_prev()
    if END_TARGET == "today":
        end_td = last_td
    elif END_TARGET == "prev":
        end_td = prev_td
    else:  # "date"
        if not END_DATE:
            raise SystemExit("end-target=date 时必须提供 --end-date=YYYY-MM-DD")
        end_td = END_DATE
    end_cal = yyyymmdd(end_td)
    print(f"目标截止交易日：{end_td}（end-target={END_TARGET}）")

    # 快照（默认跳过；若启用，尽量一次性全市场聚合写入）
    failed_spot = []
    if not SKIP_SPOT:
        try:
            spot = ak.stock_zh_a_spot_em()  # 可能被限
            spot = spot.rename(columns={"代码":"code6","最新价":"latest","市净率":"pb","市盈率-动态":"pe_ttm"})
            spot["ts_code"] = spot["code6"].apply(code6_to_ts)
            upsert_snapshot_for_trade_date(conn, spot[["ts_code","latest","pb","pe_ttm"]].drop_duplicates("ts_code"), trade_date=end_td)
        except Exception as e:
            failed_spot.append({"when":"spot","error":str(e)})
            pd.DataFrame(failed_spot).to_csv(LOG_DIR/"failed_spot.csv", index=False, encoding="utf-8-sig")
    else:
        print("SKIP_SPOT=True：跳过快照；latest 将用历史收盘兜底，PB/PE_TTM 建议用财报推导脚本计算。")

    # 主抓取循环（尾段优先理念：每票只抓“库内最大日期+1 ~ end_td” 或 覆盖窗）
    failed_hist = []
    today_d = date.today()

    for i, base_row in base.iterrows():
        ts = base_row["ts_code"]
        symbol = ts_to_ak_symbol(ts)

        start_cal = ""
        try:
            max_dt = get_max_trade_date(conn, ts)  # YYYY-MM-DD or None
            if FORCE_REFRESH_CAL_DAYS > 0:
                start_cal = (today_d - timedelta(days=FORCE_REFRESH_CAL_DAYS)).strftime("%Y%m%d")
            elif max_dt:
                start_cal = yyyymmdd((pd.to_datetime(max_dt) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
            else:
                start_cal = (today_d - timedelta(days=LOOKBACK_CAL_DAYS_INIT)).strftime("%Y%m%d")

            # 已经追到目标截止日
            if int(start_cal) > int(end_cal):
                if i % 200 == 0:
                    print(f"[{i}/{len(base)}] {ts} 已在目标日之前，无需增量")
                continue

            df_hist, hit_source = fetch_hist_with_fallback(
                ts_code=ts, symbol=symbol, start=start_cal, end=end_cal,
                adjust=ADJUST_MODE, order=ORDER_SOURCES,
                hist_max_retry=HIST_MAX_RETRY, sleep_step=SLEEP_PER_FETCH
            )
            # 可追溯性：命中非主源时打印 fallback 记录
            if ORDER_SOURCES and hit_source != ORDER_SOURCES[0]:
                print(f"[fallback] {ts}  主源={ORDER_SOURCES[0]} 失败 → 命中={hit_source}")
            if df_hist is not None and not df_hist.empty:
                upsert_daily_price(conn, ts, df_hist)

            # 节奏控制
            time.sleep(SLEEP_PER_FETCH)

        except Exception as e:
            failed_hist.append({
                "ts_code": ts, "symbol": symbol,
                "start": start_cal, "end": end_cal,
                # 记录尝试过的源顺序，便于离线排障时知道"所有源都失败了"
                "hit_source": f"failed(tried={','.join(ORDER_SOURCES)})",
                "message": str(e)
            })

    if failed_hist:
        pd.DataFrame(failed_hist).to_csv(LOG_DIR/"failed_hist.csv", index=False, encoding="utf-8-sig")

    # 统计近 252 交易日低点及涨幅
    print("计算 252 交易日低点与涨幅 …")
    # 读取当日快照（如无，则为空；latest 用历史收盘兜底）
    snap = pd.read_sql_query("""
        SELECT ts_code, latest, pb, pe_ttm
        FROM daily_snapshot
        WHERE trade_date = ?
    """, conn, params=(end_td,)).drop_duplicates("ts_code")

    low_list, low_date_list, latest_list, pb_list, pe_list = [], [], [], [], []

    for _, row in base.iterrows():
        ts = row["ts_code"]
        tail = get_tail_from_db(conn, ts, n=N_TRADING_DAYS_FOR_LOW)

        # 低点
        if tail.empty:
            low_list.append(None); low_date_list.append(None)
        else:
            idxmin = tail["low"].astype(float).idxmin()
            low_list.append(float(tail.loc[idxmin, "low"]))
            low_date_list.append(str(tail.loc[idxmin, "trade_date"]))

        # latest/pb/pe_ttm
        srow = snap[snap["ts_code"] == ts]
        if not srow.empty and pd.notna(srow["latest"].iloc[0]):
            latest_list.append(float(srow["latest"].iloc[0]))
        else:
            last_close = float(tail["close"].astype(float).iloc[-1]) if not tail.empty else None
            latest_list.append(last_close)

        pb_list.append(float(srow["pb"].iloc[0]) if (not srow.empty and pd.notna(srow["pb"].iloc[0])) else None)
        pe_list.append(float(srow["pe_ttm"].iloc[0]) if (not srow.empty and pd.notna(srow["pe_ttm"].iloc[0])) else None)

    df_out = base.copy()
    df_out["latest"] = latest_list
    df_out["pb"] = pb_list
    df_out["pe_ttm"] = pe_list
    df_out["low_252d"] = low_list
    df_out["low_252d_date"] = low_date_list
    df_out["rise_from_1y_low_pct"] = ((df_out["latest"] - df_out["low_252d"]) / df_out["low_252d"] * 100).where(df_out["low_252d"] > 0)
    df_out["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df_out["snapshot_trade_date"] = end_td

    df_out[[
        "ts_code","name","tdx_l1",
        "latest","pb","pe_ttm",
        "low_252d","low_252d_date","rise_from_1y_low_pct",
        "snapshot_trade_date","update_time"
    ]].to_csv(OUT_FILE, index=False, encoding="utf-8-sig")

    conn.close()
    print(f"\n✅ 完成：{OUT_FILE}")
    print(f"✅ 本地数据库：{DB_PATH} 已增量更新；失败日志见 {LOG_DIR}/failed_*.csv（若存在）")
    print(f"参数：end-target={END_TARGET} → {end_td}；复权={ADJUST_MODE or 'None'}；覆盖窗={FORCE_REFRESH_CAL_DAYS} 天；顺序={ORDER_SOURCES}")
    if SKIP_SPOT:
        print("提示：本次跳过了快照抓取；PB/PE_TTM 建议后续用财报推导脚本计算。")


if __name__ == "__main__":
    main()
