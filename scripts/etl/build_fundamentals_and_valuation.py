# -*- coding: utf-8 -*-
"""
build_fundamentals_and_valuation.py — 因子沉淀 + 估值推导（含增量与"最近披露只重算"）
依赖：已运行 get_market_data_db.py 生成/更新 daily_price（至少近 252 交易日）
输入：stock_industry_base.csv（ts_code,name,tdx_l1）
输出：
  - SQLite market.db:
      fin_indicators_q(ts_code, period_end, announce_date, bvps, eps_ttm, source)
      daily_valuation(ts_code, trade_date, pb, pe_ttm, method)
  - CSV: market_enriched.csv

因子来源优先级：
  BVPS：新浪"财务指标"(stock_financial_analysis_indicator) → 同花顺"按报告期"
  EPS_TTM：同花顺"按单季度"滚动4季 → 新浪利润表兜底 → 新浪"财务指标"年度EPS兜底
估值推导：
  以 announce_date（缺失用 period_end）为生效点，与逐日价格对齐，向前填充，计算 PB / PE(TTM)

常用示例：
  python build_fundamentals_and_valuation.py --batch 200
  python build_fundamentals_and_valuation.py --ts-file ts_sample_20.csv --batch 0 --start-year 2015 --recent-ann-days 30
  python build_fundamentals_and_valuation.py --ts 000001.SZ,600000.SH --since 2024-01-01
"""

from __future__ import annotations
import os, sys, time, sqlite3, argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Iterable

import pandas as pd

# —— 防止代理干扰（放在 import akshare 前）
for k in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","all_proxy"):
    os.environ.pop(k, None)
os.environ.setdefault("NO_PROXY", "*")

try:
    import akshare as ak
except ModuleNotFoundError:
    ak = None  # akshare 可选，使用 --source tushare 时不需要

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
DEFAULT_BATCH       = 0
DEFAULT_START_YEAR  = "2015"
DEFAULT_SAFETY_DAYS = 15
N_TRADING_DAYS_FOR_LOW = 252

LOG_DIR = Path("./logs"); LOG_DIR.mkdir(parents=True, exist_ok=True)


# ===================== 工具函数 =====================
def ts_code_to_plain(ts_code: str) -> str:
    """000001 / 600000 / 430047（无交易所）"""
    return ts_code.split(".")[0]

def ts_code_to_sina(ts_code: str) -> str:
    """sz000001 / sh600000 / bj430047（新浪三大表用）"""
    code, ex = ts_code.split(".")
    ex = ex.upper()
    if ex == "SZ": return f"sz{code}"
    if ex == "SH": return f"sh{code}"
    if ex == "BJ": return f"bj{code}"
    raise ValueError(f"未知交易所: {ts_code}")

def connect_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-50000;")
    return conn

def init_tables(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS fin_indicators_q (
        ts_code       TEXT NOT NULL,
        period_end    TEXT NOT NULL,  -- YYYY-MM-DD
        announce_date TEXT,           -- YYYY-MM-DD（缺失时用 period_end）
        bvps          REAL,
        eps_ttm       REAL,
        source        TEXT,
        PRIMARY KEY (ts_code, period_end)
    );""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_valuation (
        ts_code     TEXT NOT NULL,
        trade_date  TEXT NOT NULL,    -- YYYY-MM-DD
        pb          REAL,
        pe_ttm      REAL,
        method      TEXT,
        PRIMARY KEY (ts_code, trade_date)
    );""")
    conn.commit()

def read_ts_list_from_file(path: str) -> List[str]:
    df = None
    try:
        if path.lower().endswith(".csv"):
            df = pd.read_csv(path, dtype=str)
        elif path.lower().endswith((".tsv",".txt")):
            df = pd.read_csv(path, dtype=str, sep="\t", header=None, names=["ts_code"])
    except Exception:
        pass
    if df is not None:
        if "ts_code" in df.columns:
            return [str(x).strip() for x in df["ts_code"].dropna().tolist()]
        else:
            return [str(x).strip() for x in df.iloc[:,0].dropna().tolist()]
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


# ===================== 因子来源 1：新浪"财务指标"（年度，适合 BVPS） =====================
def fetch_fin_indicator_sina_series(ts_code: str, start_year: str) -> pd.DataFrame:
    """
    AkShare: stock_financial_analysis_indicator(symbol, start_year)
    返回：period_end(该年12-31)、bvps（优先"调整后"）、eps_annual（年度EPS）
    """
    try:
        code6 = ts_code_to_plain(ts_code)  # 600004
        df = ak.stock_financial_analysis_indicator(symbol=code6, start_year=start_year)
        if df is None or df.empty:
            return pd.DataFrame()

        date_col = "日期" if "日期" in df.columns else df.columns[0]
        out = pd.DataFrame()
        # 将"年份或日期"规范到"YYYY-12-31"
        out["period_end"] = pd.to_datetime(df[date_col].astype(str).str.replace(r"[^0-9]", "", regex=True)).dt.strftime("%Y-12-31")

        # BVPS（优先调整后）
        bvps = None
        for cand in ["调整后的每股净资产(元)","每股净资产_调整后(元)","每股净资产_调整前(元)","每股净资产(元)"]:
            if cand in df.columns:
                bvps = pd.to_numeric(df[cand], errors="coerce"); break
        if bvps is None:
            # 宽松匹配含"每股净资产"的列
            cand_cols = [c for c in df.columns if "每股净资产" in str(c)]
            if cand_cols:
                bvps = pd.to_numeric(df[cand_cols[0]], errors="coerce")

        # 年度 EPS（兜底）
        eps = None
        for cand in ["摊薄每股收益(元)","加权每股收益(元)","每股收益_调整后(元)","扣除非经常性损益后的每股收益(元)"]:
            if cand in df.columns:
                eps = pd.to_numeric(df[cand], errors="coerce"); break

        out["bvps"] = bvps
        out["eps_annual"] = eps
        out["source"] = "sina_indicator"
        out = out.dropna(subset=["period_end"]).drop_duplicates("period_end").sort_values("period_end")
        return out
    except Exception:
        return pd.DataFrame()


# ===================== 因子来源 2：同花顺"关键指标" =====================
def ths_fetch_report(code6: str, indicator: str) -> pd.DataFrame:
    """
    AkShare: stock_financial_abstract_ths(symbol="000063", indicator in {"按报告期","按单季度"})
    返回含"报告期"、每股净资产/基本每股收益 等
    """
    df = ak.stock_financial_abstract_ths(symbol=code6, indicator=indicator)
    if df is None or df.empty or "报告期" not in df.columns:
        return pd.DataFrame()
    return df

def extract_bvps_from_ths(code6: str) -> pd.Series:
    df = ths_fetch_report(code6, indicator="按报告期")
    if df.empty: return pd.Series(dtype=float)
    cand_cols = [c for c in df.columns if "每股净资产" in str(c)]
    if not cand_cols: return pd.Series(dtype=float)
    col = cand_cols[0]
    s = df.set_index("报告期")[col]
    s.index = pd.to_datetime(s.index.astype(str).str.replace(r"[^0-9]", "", regex=True))
    return pd.to_numeric(s, errors="coerce").dropna().sort_index()

def extract_eps_ttm_from_ths(code6: str) -> pd.Series:
    df = ths_fetch_report(code6, indicator="按单季度")
    if df.empty: return pd.Series(dtype=float)
    cand_cols = [c for c in df.columns if "每股收益" in str(c) and "基本" in str(c)]
    if not cand_cols: return pd.Series(dtype=float)
    col = cand_cols[0]
    s_q = df.set_index("报告期")[col]
    s_q.index = pd.to_datetime(s_q.index.astype(str).str.replace(r"[^0-9]", "", regex=True))
    s_q = pd.to_numeric(s_q, errors="coerce").dropna().sort_index()
    return s_q.rolling(4).sum().dropna() if not s_q.empty else pd.Series(dtype=float)


# ===================== 因子来源 3：新浪"三大表"兜底（严格参数） =====================
def sina_fetch_table(ts_code: str, table: str) -> pd.DataFrame:
    """table ∈ {"资产负债表","利润表","现金流量表"}；stock=sh600000/sz000001/bj430047"""
    stock = ts_code_to_sina(ts_code)
    return ak.stock_financial_report_sina(stock=stock, symbol=table)

def extract_eps_ttm_from_sina(ts_code: str) -> pd.Series:
    """
    用利润表兜底 EPS_TTM：近四期归母净利之和 / 估算股本（近似）
    注：严格股本变动口径需额外建"股本变动表"，此处做尽力兜底。
    """
    try:
        lrb = sina_fetch_table(ts_code, "利润表")
        if lrb is None or lrb.empty:
            return pd.Series(dtype=float)
        profit_cols = [c for c in lrb.columns if "归属于母公司" in c and "净利润" in c]
        if not profit_cols:
            return pd.Series(dtype=float)
        pc = profit_cols[0]
        s_np = lrb.set_index("报告日")[pc]
        s_np.index = pd.to_datetime(s_np.index.astype(str))
        s_np = pd.to_numeric(s_np, errors="coerce").dropna().sort_index()
        if s_np.empty:
            return pd.Series(dtype=float)

        # 用 THS "按报告期"的"基本每股收益"近似反推股本：shares ≈ net_profit / EPS
        code6 = ts_code_to_plain(ts_code)
        df_ths_r = ths_fetch_report(code6, "按报告期")
        if df_ths_r.empty:
            return pd.Series(dtype=float)
        eps_cols = [c for c in df_ths_r.columns if "每股收益" in str(c) and "基本" in str(c)]
        if not eps_cols:
            return pd.Series(dtype=float)
        eps_col = eps_cols[0]
        df_tmp = df_ths_r[["报告期", eps_col]].copy()
        df_tmp["报告期"] = pd.to_datetime(df_tmp["报告期"].astype(str).str.replace(r"[^0-9]", "", regex=True))
        df_tmp.set_index("报告期", inplace=True)
        s_eps = pd.to_numeric(df_tmp[eps_col], errors="coerce").dropna().sort_index()

        common_idx = s_np.index.intersection(s_eps.index)
        if len(common_idx) < 4:
            return pd.Series(dtype=float)
        shares_est = (s_np.loc[common_idx] / s_eps.loc[common_idx]).replace([pd.NA, pd.NaT], pd.NA)
        shares_est = pd.to_numeric(shares_est, errors="coerce").dropna()
        if shares_est.empty:
            return pd.Series(dtype=float)

        shares_last = float(shares_est.iloc[-1])
        s_np_ttm = s_np.rolling(4).sum().dropna()
        s_eps_ttm = s_np_ttm / shares_last
        return s_eps_ttm
    except Exception:
        return pd.Series(dtype=float)


# ===================== 因子组装：统一产出 period_end, bvps, eps_ttm =====================
def assemble_fin_indicators(ts_code: str, start_year: str) -> pd.DataFrame:
    """
    汇聚单只股票因子（按报告期）：
      BVPS：新浪财务指标优先 → THS按报告期
      EPS_TTM：THS按单季度滚动 → 新浪利润表 → 新浪财务指标年度EPS兜底
    返回列：period_end, announce_date(None), bvps, eps_ttm, source
    """
    # —— 初始化为空的 Series，避免 NameError / ambiguous truth ——
    bvps_series = pd.Series(dtype=float)
    eps_ttm_series = pd.Series(dtype=float)

    # 1) BVPS：新浪财务指标优先
    sina_ind = fetch_fin_indicator_sina_series(ts_code, start_year=start_year)
    if not sina_ind.empty and "bvps" in sina_ind.columns and sina_ind["bvps"].notna().any():
        s = sina_ind.set_index("period_end")["bvps"].dropna()
        s = pd.to_numeric(s, errors="coerce").dropna()
        if not s.empty:
            s.index = pd.to_datetime(s.index)
            bvps_series = s

    # 若仍为空，用 THS "按报告期"兜底
    if bvps_series.empty:
        code6 = ts_code_to_plain(ts_code)
        s_bvps_ths = extract_bvps_from_ths(code6)  # 已返回 DatetimeIndex
        if isinstance(s_bvps_ths, pd.Series) and not s_bvps_ths.empty:
            bvps_series = s_bvps_ths

    # 2) EPS_TTM：THS "按单季度"滚动4季 → 新浪利润表兜底 → 新浪财务指标年度EPS兜底
    s_eps_ths = extract_eps_ttm_from_ths(ts_code_to_plain(ts_code))  # 已返回 DatetimeIndex
    if isinstance(s_eps_ths, pd.Series) and not s_eps_ths.empty:
        eps_ttm_series = s_eps_ths
    else:
        s_eps_sina = extract_eps_ttm_from_sina(ts_code)  # 近四期净利/估算股本
        if isinstance(s_eps_sina, pd.Series) and not s_eps_sina.empty:
            eps_ttm_series = s_eps_sina
        elif not sina_ind.empty and "eps_annual" in sina_ind.columns and sina_ind["eps_annual"].notna().any():
            s = sina_ind.set_index("period_end")["eps_annual"].dropna()
            s = pd.to_numeric(s, errors="coerce").dropna()
            if not s.empty:
                s.index = pd.to_datetime(s.index)
                eps_ttm_series = s

    # 两者都为空直接返回空
    if bvps_series.empty and eps_ttm_series.empty:
        return pd.DataFrame()

    # —— 安全合并索引 ——
    idx_bv = bvps_series.index if not bvps_series.empty else pd.DatetimeIndex([])
    idx_ep = eps_ttm_series.index if not eps_ttm_series.empty else pd.DatetimeIndex([])
    idx_all = pd.DatetimeIndex(idx_bv).union(pd.DatetimeIndex(idx_ep)).sort_values()

    out = pd.DataFrame({"period_end": idx_all.strftime("%Y-%m-%d")})
    out["announce_date"] = None  # 暂无公告日，用报告期代替；后续可补

    # 合并 BVPS（用位置改名，避免列名不一致）
    if not bvps_series.empty:
        tmp = bvps_series.copy()
        tmp.index = pd.to_datetime(tmp.index)
        df_bv = tmp.rename("bvps").reset_index()
        df_bv.columns = ["period_end", "bvps"]
        df_bv["period_end"] = pd.to_datetime(df_bv["period_end"]).dt.strftime("%Y-%m-%d")
        out = out.merge(df_bv, on="period_end", how="left")

    # 合并 EPS_TTM（同理）
    if not eps_ttm_series.empty:
        tmp = eps_ttm_series.copy()
        tmp.index = pd.to_datetime(tmp.index)
        df_eps = tmp.rename("eps_ttm").reset_index()
        df_eps.columns = ["period_end", "eps_ttm"]
        df_eps["period_end"] = pd.to_datetime(df_eps["period_end"]).dt.strftime("%Y-%m-%d")
        out = out.merge(df_eps, on="period_end", how="left")

    out["source"] = "mix_sina_ind_ths_sina_lrb"
    return out.drop_duplicates("period_end").sort_values("period_end")


# ===================== 因子来源 4：Tushare fina_indicator（替代 akshare） =====================
def fetch_fin_indicators_tushare(ts_code: str, token: str, start_year: str = "2015") -> pd.DataFrame:
    """
    通过 Tushare fina_indicator 接口获取每股净资产(bps)和EPS，推导 EPS_TTM。

    接口说明（2000 积分档）：
      - 每次最多返回 100 条，按 5 年分段分页（≤20条/段，远低于上限）
      - 优先使用 q_eps（单季度）；q_eps 缺失时回退到从累计 eps 还原单季度值
      - report_type='1' 表示合并报表，过滤掉调整表/母公司报表
      - 去重前按 [end_date, ann_date] 排序，保留最新披露版本

    返回列：period_end, announce_date, bvps, eps_ttm, source
    """
    try:
        import tushare as ts
        import time as _time
        pro = ts.pro_api(token, timeout=60)  # 默认30s太短，改为60s

        # 按 5 年分段拉取，避免单次超过 100 条上限
        start_year_int = int(start_year)
        end_year_int = datetime.now().year

        all_frames = []
        for chunk_start in range(start_year_int, end_year_int + 1, 5):
            chunk_end = min(chunk_start + 4, end_year_int)
            # 带重试的 API 调用（最多 2 次重试）
            df_chunk = None
            for attempt in range(3):
                try:
                    df_chunk = pro.fina_indicator(
                        ts_code=ts_code,
                        start_date=f"{chunk_start}0101",
                        end_date=f"{chunk_end}1231",
                        fields="ts_code,ann_date,end_date,bps,q_eps,eps,report_type",
                    )
                    break  # 成功则跳出重试循环
                except Exception as retry_err:
                    if attempt < 2:
                        sys.stderr.write(f"[RETRY] fina_indicator {ts_code} chunk {chunk_start}-{chunk_end} attempt {attempt+1}: {retry_err}\n")
                        _time.sleep(2 * (attempt + 1))  # 递增等待：2s, 4s
                    else:
                        raise  # 最后一次仍失败则抛出
            if df_chunk is not None and not df_chunk.empty:
                all_frames.append(df_chunk)
            _time.sleep(0.2)  # Tushare 接口限频保护

        if not all_frames:
            return pd.DataFrame()

        df = pd.concat(all_frames, ignore_index=True)

        # 只取合并报表（report_type='1'），排除调整表/母公司报表
        if "report_type" in df.columns:
            df = df[df["report_type"].astype(str) == "1"]

        df = df.dropna(subset=["end_date"]).copy()
        df["end_date"] = pd.to_datetime(df["end_date"].astype(str), errors="coerce")
        df["ann_date"] = pd.to_datetime(df["ann_date"].astype(str).replace("None", "NaT"), errors="coerce")
        df["bps"]      = pd.to_numeric(df["bps"],   errors="coerce")
        df["q_eps"]    = pd.to_numeric(df.get("q_eps"),  errors="coerce")
        df["eps"]      = pd.to_numeric(df.get("eps"),    errors="coerce")

        # [P2] 按 [end_date, ann_date] 排序后去重，保证保留同一报告期最新披露版本
        df = (df.dropna(subset=["end_date"])
                .sort_values(["end_date", "ann_date"], na_position="first")
                .drop_duplicates("end_date", keep="last")
                .reset_index(drop=True))

        # [P1] 构建单季度 EPS：优先用 q_eps；q_eps 缺失时从累计 eps 还原
        df["month"] = df["end_date"].dt.month
        df["year"]  = df["end_date"].dt.year

        eps_q_values: list = []
        for i, row in df.iterrows():
            q = row["q_eps"]
            if pd.notna(q):
                eps_q_values.append(q)  # 直接用 API 提供的单季度值
                continue
            # q_eps 缺失 → 从累计 eps 推算
            m = row["month"]
            if m == 3:
                # Q1：累计值本就等于单季度
                eps_q_values.append(row["eps"])
            else:
                prev_m = {6: 3, 9: 6, 12: 9}.get(m)
                if prev_m is None:
                    eps_q_values.append(None)
                    continue
                mask = (df["year"] == row["year"]) & (df["month"] == prev_m)
                prev_rows = df[mask]
                if not prev_rows.empty and pd.notna(prev_rows.iloc[-1]["eps"]) and pd.notna(row["eps"]):
                    eps_q_values.append(row["eps"] - prev_rows.iloc[-1]["eps"])
                else:
                    eps_q_values.append(None)

        df["eps_q_final"] = pd.to_numeric(eps_q_values, errors="coerce")
        df["eps_ttm"]     = df["eps_q_final"].rolling(4, min_periods=4).sum()

        out = pd.DataFrame({
            "period_end":    df["end_date"].dt.strftime("%Y-%m-%d"),
            "announce_date": df["ann_date"].dt.strftime("%Y-%m-%d").where(df["ann_date"].notna(), None),
            "bvps":          df["bps"].values,
            "eps_ttm":       df["eps_ttm"].values,
            "source":        "tushare_fina_indicator",
        })
        return out.dropna(subset=["period_end"]).reset_index(drop=True)
    except Exception as e:
        sys.stderr.write(f"[WARN] Tushare fina_indicator failed for {ts_code}: {e}\n")
        return pd.DataFrame()


# ===================== DB 写入 & 估值推导 =====================
def upsert_fin_indicators(conn: sqlite3.Connection, ts_code: str, df: pd.DataFrame):
    if df is None or df.empty: return
    recs = []
    for _, r in df.iterrows():
        recs.append((ts_code,
                     r["period_end"],
                     r["announce_date"] if pd.notna(r.get("announce_date")) else None,
                     float(r["bvps"]) if pd.notna(r.get("bvps")) else None,
                     float(r["eps_ttm"]) if pd.notna(r.get("eps_ttm")) else None,
                     str(r.get("source") or "unknown")))
    conn.executemany("""
        INSERT INTO fin_indicators_q (ts_code, period_end, announce_date, bvps, eps_ttm, source)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(ts_code, period_end) DO UPDATE SET
          announce_date=COALESCE(excluded.announce_date, announce_date),
          bvps=COALESCE(excluded.bvps, bvps),
          eps_ttm=COALESCE(excluded.eps_ttm, eps_ttm),
          source=excluded.source;
    """, recs)
    conn.commit()

def get_last_valuation_date(conn: sqlite3.Connection, ts_code: str) -> Optional[str]:
    row = conn.execute("SELECT MAX(trade_date) FROM daily_valuation WHERE ts_code=?", (ts_code,)).fetchone()
    return row[0] if row and row[0] else None

def statutory_disclosure_deadline(period_end) -> Optional[str]:
    """A 股定期报告法定最晚披露日：一季报 4/30、半年报 8/31、三季报 10/31、年报次年 4/30。"""
    try:
        pe = pd.to_datetime(period_end)
    except Exception:
        return None
    if pd.isna(pe):
        return None
    deadlines = {3: (pe.year, 4, 30), 6: (pe.year, 8, 31), 9: (pe.year, 10, 31), 12: (pe.year + 1, 4, 30)}
    if pe.month in deadlines:
        y, m, d = deadlines[pe.month]
        return f"{y:04d}-{m:02d}-{d:02d}"
    # 非标准报告期：保守按 4 个月后视为已知
    return (pe + pd.DateOffset(months=4)).strftime("%Y-%m-%d")


def compute_daily_valuation_for_stock(conn: sqlite3.Connection, ts_code: str,
                                      since: Optional[str]=None, safety_days: int=DEFAULT_SAFETY_DAYS):
    """公告日口径对齐→向前填充→按每日 close 推导 pb/pe_ttm（支持增量）"""
    fi = pd.read_sql_query("""
        SELECT period_end, announce_date AS ad, bvps, eps_ttm
        FROM fin_indicators_q
        WHERE ts_code=?
    """, conn, params=(ts_code,))
    if fi.empty: return
    # 公告日未知时不能用报告期末（会引入未来数据），改用法定披露截止日作为最早已知日
    fi["ad"] = [a if pd.notna(a) and a else statutory_disclosure_deadline(p)
                for a, p in zip(fi["ad"], fi["period_end"])]
    fi = fi.dropna(subset=["ad"])
    if fi.empty: return
    px = pd.read_sql_query("""
        SELECT trade_date, close FROM daily_price
        WHERE ts_code=? ORDER BY trade_date ASC
    """, conn, params=(ts_code,))
    if px.empty: return

    px["trade_date"] = pd.to_datetime(px["trade_date"])
    fi["ad"] = pd.to_datetime(fi["ad"])

    # 计算起始日期（自动增量 + 安全回算）
    if since:
        start_dt = pd.to_datetime(since) - pd.Timedelta(days=safety_days)
    else:
        last_val = get_last_valuation_date(conn, ts_code)
        start_dt = (pd.to_datetime(last_val) - pd.Timedelta(days=safety_days)) if last_val else px["trade_date"].min()

    px_slice = px[px["trade_date"] >= start_dt].copy()
    if px_slice.empty: return

    merged = pd.merge_asof(px_slice,
                           fi.rename(columns={"ad":"announce_date"}).sort_values("announce_date"),
                           left_on="trade_date", right_on="announce_date", direction="backward")
    merged[["bvps","eps_ttm"]] = merged[["bvps","eps_ttm"]].ffill()

    def safe_div(a, b, positive_only=False):
        try:
            if b is None or pd.isna(b): return None
            if b == 0: return None
            if positive_only and b <= 0: return None
            return float(a) / float(b)
        except Exception:
            return None

    merged["pb"]     = [safe_div(c, v, positive_only=True) for c, v in zip(merged["close"], merged["bvps"])]
    merged["pe_ttm"] = [safe_div(c, v, positive_only=True) for c, v in zip(merged["close"], merged["eps_ttm"])]

    recs = []
    for _, r in merged.iterrows():
        recs.append((ts_code,
                     r["trade_date"].strftime("%Y-%m-%d"),
                     None if pd.isna(r["pb"]) else float(r["pb"]),
                     None if pd.isna(r["pe_ttm"]) else float(r["pe_ttm"]),
                     "computed_v1"))
    conn.executemany("""
        INSERT INTO daily_valuation (ts_code, trade_date, pb, pe_ttm, method)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ts_code, trade_date) DO UPDATE SET
          pb=excluded.pb, pe_ttm=excluded.pe_ttm, method=excluded.method;
    """, recs)
    conn.commit()

def export_enriched(conn: sqlite3.Connection, base: pd.DataFrame, out_csv: str):
    last_td_row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
    last_td = last_td_row[0] if last_td_row and last_td_row[0] else None

    lows, low_dates, latests = [], [], []
    for ts in base["ts_code"]:
        tail = pd.read_sql_query("""
            SELECT trade_date, low, close FROM daily_price
            WHERE ts_code=? ORDER BY trade_date DESC LIMIT ?;
        """, conn, params=(ts, N_TRADING_DAYS_FOR_LOW))
        if tail.empty:
            lows.append(None); low_dates.append(None); latests.append(None); continue
        tail = tail.sort_values("trade_date")
        idx = tail["low"].astype(float).idxmin()
        lows.append(float(tail.loc[idx, "low"]))
        low_dates.append(str(tail.loc[idx, "trade_date"]))
        latests.append(float(tail["close"].astype(float).iloc[-1]))

    val = pd.read_sql_query("""
        SELECT ts_code, pb, pe_ttm
        FROM daily_valuation
        WHERE trade_date = (SELECT MAX(trade_date) FROM daily_valuation)
    """, conn)

    df = base.copy()
    df["latest"] = latests
    df["low_252d"] = lows
    df["low_252d_date"] = low_dates
    df = df.merge(val, on="ts_code", how="left")
    df["rise_from_1y_low_pct"] = ((df["latest"] - df["low_252d"]) / df["low_252d"] * 100).where(df["low_252d"] > 0)
    df["snapshot_trade_date"] = last_td
    df["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df[[
        "ts_code","name","tdx_l1",
        "latest","pb","pe_ttm",
        "low_252d","low_252d_date","rise_from_1y_low_pct",
        "snapshot_trade_date","update_time"
    ]].to_csv(out_csv, index=False, encoding="utf-8-sig")
    print("✅ 已导出：", out_csv)


# ===================== 最近披露筛选（只重算） =====================
def get_max_period_end(conn: sqlite3.Connection, ts_code: str) -> Optional[pd.Timestamp]:
    row = conn.execute("SELECT MAX(period_end) FROM fin_indicators_q WHERE ts_code=?", (ts_code,)).fetchone()
    return pd.to_datetime(row[0]) if row and row[0] else None

def list_recent_announced_ts(conn: sqlite3.Connection, days: int) -> list[str]:
    """在库里找到最近 N 天内有公告期（或报告期）的股票，用于兜底重算"""
    cutoff = (pd.Timestamp.today() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    df = pd.read_sql_query("""
        SELECT DISTINCT ts_code
        FROM fin_indicators_q
        WHERE COALESCE(announce_date, period_end) >= ?
    """, conn, params=(cutoff,))
    return df["ts_code"].dropna().astype(str).tolist()


# ===================== 主程序（含命令行参数） =====================
def main():
    ap = argparse.ArgumentParser(description="按公告日口径推导每日 PB/PE(TTM)（含增量重算、多源兜底与最近披露只重算）")
    ap.add_argument("--base-file", type=str, default=DEFAULT_BASE_FILE, help="基础表 CSV，含 ts_code,name,tdx_l1")
    ap.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="SQLite 数据库路径（与 get_market_data_db.py 一致）")
    ap.add_argument("--out", type=str, default=DEFAULT_OUT_FILE, help="导出 CSV 文件名")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH, help="仅处理前 N 只；0=全量")
    ap.add_argument("--ts", type=str, default="", help="逗号分隔的 ts_code 列表，如 000001.SZ,600000.SH")
    ap.add_argument("--ts-file", type=str, default="", help="从文件读取 ts_code 列表（CSV/TXT/TSV）")
    ap.add_argument("--since", type=str, default="", help="从该日期（含）起重算；留空=自动增量")
    ap.add_argument("--start-year", type=str, default=DEFAULT_START_YEAR, help="新浪财务指标起始年份")
    ap.add_argument("--sleep", type=float, default=0.3, help="单只抓因子间隔秒（防屏蔽）")
    ap.add_argument("--safety-days", type=int, default=DEFAULT_SAFETY_DAYS, help="自动增量安全回算自然日")
    ap.add_argument("--recent-ann-days", type=int, default=0,
                    help="仅重算最近 N 天内新增/更新报告期的股票；0 表示不启用")
    ap.add_argument("--source", type=str, default="akshare", choices=["akshare", "tushare"],
                    help="财务数据来源：akshare（默认，新浪/同花顺）或 tushare（需 Token）")
    ap.add_argument("--token", type=str, default="",
                    help="Tushare Token（也可通过环境变量 TUSHARE_TOKEN 传入）")
    ap.add_argument("--force-refresh", action="store_true",
                    help="忽略已有记录，强制对全部股票重新抓取因子数据（默认跳过已有数据的股票）")
    ap.add_argument("--log-dir", type=str, default="", help="隔离日志目录（drip_sync 使用），为空时用默认 logs/")
    args = ap.parse_args()

    global LOG_DIR
    if args.log_dir:
        LOG_DIR = Path(args.log_dir)
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 数据源校验
    use_tushare = (args.source == "tushare")
    tushare_token = args.token or os.environ.get("TUSHARE_TOKEN", "")
    if use_tushare and not tushare_token:
        raise SystemExit("--source tushare 需要提供 Tushare Token（--token 或环境变量 TUSHARE_TOKEN）")
    if not use_tushare and ak is None:
        raise SystemExit("请先安装 akshare：pip install -U akshare pandas")

    base = pd.read_csv(args.base_file, dtype={"ts_code": str})[["ts_code","name","tdx_l1"]].drop_duplicates("ts_code")

    # 选择子集
    chosen: Iterable[str] = base["ts_code"]
    if args.ts:
        chosen = [s.strip() for s in args.ts.split(",") if s.strip()]
    elif args.ts_file:
        chosen = read_ts_list_from_file(args.ts_file)
    chosen = list(dict.fromkeys(chosen))  # 去重并保持顺序
    if args.batch and args.batch > 0:
        chosen = chosen[:min(args.batch, len(chosen))]
    base = base[base["ts_code"].isin(set(chosen))].reset_index(drop=True)

    conn = connect_db(args.db)
    init_tables(conn)

    # 1) 抓/组装"报告期因子"并入库；同时记录"新增了报告期"的股票集合
    failed = []
    skipped = 0
    changed_ts: set[str] = set()

    source_label = "Tushare" if use_tushare else "AkShare"
    force_refresh = args.force_refresh
    total = len(base)
    print(f"[ETL] source={source_label}  force_refresh={force_refresh}  total={total}")

    for i, ts in enumerate(base["ts_code"], 1):
        prev_max = get_max_period_end(conn, ts)  # 入库前的最大报告期（可能为 None）

        # ── 增量跳过：已有数据且非强制刷新时跳过 fetch，直接复用库内数据 ──
        if not force_refresh and prev_max is not None:
            skipped += 1
            if skipped % 500 == 0 or i % 500 == 0:
                print(f"[ETL] 进度 {i}/{total}，已跳过 {skipped} 只（已有数据），待抓取 {i - skipped - len(failed)} 只")
            time.sleep(0)  # 跳过时不等待
            continue

        if use_tushare:
            df_ind = fetch_fin_indicators_tushare(ts, token=tushare_token, start_year=args.start_year)
        else:
            df_ind = assemble_fin_indicators(ts, start_year=args.start_year)

        if df_ind.empty:
            failed.append(ts)
        else:
            upsert_fin_indicators(conn, ts, df_ind)
            # 若本次抓到的报告期里有大于 prev_max 的，视为"新增"
            try:
                new_max = pd.to_datetime(df_ind["period_end"]).max()
                if prev_max is None or (pd.notna(new_max) and new_max > prev_max):
                    changed_ts.add(ts)
            except Exception:
                pass

        if i % 100 == 0:
            print(f"[ETL] 进度 {i}/{total}，已跳过 {skipped}，失败 {len(failed)}")

        time.sleep(args.sleep)

    print(f"[ETL] 因子抓取完成：总计 {total}，跳过 {skipped}，失败 {len(failed)}，新增/更新 {len(changed_ts)}")

    if failed:
        pd.DataFrame({"ts_code": failed}).to_csv(LOG_DIR/"failed_factors.csv", index=False, encoding="utf-8-sig")
        print(f"⚠️ 因子抓取/推导失败 {len(failed)} 条，见 {LOG_DIR/'failed_factors.csv'}")

    # 2) 估值推导（增量 + 仅重算最近公告期/本次新增）
    since = args.since if args.since else None

    targets = base["ts_code"].tolist()
    if args.recent_ann_days and args.recent_ann_days > 0:
        if changed_ts:
            targets = sorted(changed_ts)
            print(f"最近披露：本次检测到 {len(targets)} 只新增报告期，将仅重算这些。")
        else:
            recent_list = list_recent_announced_ts(conn, args.recent_ann_days)
            if recent_list:
                # 仅重算 base 交集（若 --ts/--ts-file 过滤过，只重算其中的最近集）
                targets = sorted(set(recent_list) & set(targets)) or recent_list
                print(f"最近披露：近 {args.recent_ann_days} 天在库内检测到 {len(targets)} 只（去重后）需要重算。")
            else:
                print(f"最近披露：近 {args.recent_ann_days} 天未检测到需要重算的标的，跳过估值阶段。")
                targets = []

    for ts in targets:
        compute_daily_valuation_for_stock(conn, ts, since=since, safety_days=args.safety_days)

    # 3) 汇总导出
    export_enriched(conn, base, args.out)

    # 覆盖率统计
    stat = pd.read_sql_query("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN pb     IS NOT NULL THEN 1 ELSE 0 END) AS pb_ok,
               SUM(CASE WHEN pe_ttm IS NOT NULL THEN 1 ELSE 0 END) AS pe_ok
        FROM daily_valuation
        WHERE trade_date = (SELECT MAX(trade_date) FROM daily_valuation)
    """, conn)
    conn.close()

    print("\n✅ 全流程完成；口径：公告日(缺用报告期)对齐→向前填充→收盘价推导")
    print(stat.to_string(index=False))
    if since:
        print(f"增量重算 since={since}，安全回算={args.safety_days} 天")
    else:
        print(f"自动增量：安全回算={args.safety_days} 天")


if __name__ == "__main__":
    main()
