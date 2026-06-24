# -*- coding: utf-8 -*-
"""
make_market_enriched_from_db.py

功能：
1）从 SQLite market.db 的 daily_price 表中，按 ts_code 计算最近 1 年自然日窗口的最低价及对应日期；
2）推断全局 snapshot_trade_date = daily_price 表中的最大 trade_date；
3）可选：通过 TuShare 的 daily_basic(trade_date) 一次性拉全市场 PB / PE_TTM，
    与基表合并，生成 market_enriched.csv；
4）输出给估值打分脚本使用。

用法示例（PowerShell）：

    # 先设置 TuShare token（只需一次）
    # $env:TUSHARE_TOKEN="你的token"

    # 仅用 DB 里的收盘价+低点（pb/pe 都为空）
    # python .\make_market_enriched_from_db.py `
    #   --base stock_industry_base.csv `
    #   --db market.db `
    #   --out market_enriched.csv `
    #   --use-tushare 0

    # 推荐：启用 TuShare，自动补 PB / PE_TTM
    # python .\make_market_enriched_from_db.py `
    #   --base stock_industry_base.csv `
    #   --db market.db `
    #   --out market_enriched.csv `
    #   --use-tushare 1

"""

from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime
from typing import Optional

import pandas as pd


def connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-50000;")  # ~50MB
    return conn


def get_global_max_trade_date(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
    return row[0] if row and row[0] else None


def get_latest_from_db(conn: sqlite3.Connection, ts_code: str) -> pd.DataFrame:
    """取某个 ts_code 最新一条 K 线（按日期升序返回，最多 1 行）。"""
    df = pd.read_sql_query(
        """
        SELECT trade_date, open, high, low, close, volume, amount
        FROM daily_price
        WHERE ts_code = ?
        ORDER BY trade_date DESC
        LIMIT 1;
        """,
        conn,
        params=(ts_code,),
    )
    if df.empty:
        return df
    return df.sort_values("trade_date")


def calendar_window_start(snapshot_trade_date: str, years: int = 1) -> str:
    """
    计算自然日窗口起点（开区间）：
    例如 latest_date=2026-04-16，则 1 年窗口从 2025-04-17 开始。
    """
    ref_date = pd.to_datetime(snapshot_trade_date)
    return (ref_date - pd.DateOffset(years=years) + pd.DateOffset(days=1)).strftime("%Y-%m-%d")


def get_window_from_db(conn: sqlite3.Connection, ts_code: str, start_date: str) -> pd.DataFrame:
    """取某个 ts_code 在自然日窗口内的 K 线（按日期升序）。"""
    df = pd.read_sql_query(
        """
        SELECT trade_date, open, high, low, close, volume, amount
        FROM daily_price
        WHERE ts_code = ?
          AND trade_date >= ?
        ORDER BY trade_date ASC;
        """,
        conn,
        params=(ts_code, start_date),
    )
    if df.empty:
        return df
    return df.sort_values("trade_date")


def fetch_daily_basic_ts(trade_date_ymd: str) -> pd.DataFrame:
    """
    用 TuShare 的 daily_basic 拉全市场 PB / PE_TTM 快照。

    trade_date_ymd: 'YYYYMMDD'
    返回列：ts_code, pb, pe_ttm（都是 float，可能含 NaN）
    """
    token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("ts_token")
    if not token:
        print("⚠ 未检测到环境变量 TUSHARE_TOKEN / ts_token，PB/PE 将全部留空。")
        return pd.DataFrame()

    try:
        import tushare as ts  # type: ignore
    except ModuleNotFoundError:
        print("⚠ 未安装 tushare，PB/PE 将全部留空。请先：pip install tushare")
        return pd.DataFrame()

    ts.set_token(token)
    pro = ts.pro_api()

    print(f"调用 TuShare daily_basic 获取 {trade_date_ymd} PB/PE_TTM …")
    try:
        df = pro.daily_basic(
            trade_date=trade_date_ymd,
            fields="ts_code,trade_date,close,pb,pe_ttm",
        )
    except Exception as e:
        print(f"❌ TuShare daily_basic 调用失败：{e}")
        return pd.DataFrame()

    if df is None or df.empty:
        print("⚠ TuShare daily_basic 返回为空（可能当天不是交易日或账户权限问题），PB/PE 将留空。")
        return pd.DataFrame()

    df["pb"] = pd.to_numeric(df["pb"], errors="coerce")
    df["pe_ttm"] = pd.to_numeric(df["pe_ttm"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df[["ts_code", "pb", "pe_ttm", "close"]]


def main():
    ap = argparse.ArgumentParser(description="从 SQLite 日线库计算自然日低点 + 合并 TuShare PB/PE，生成 market_enriched.csv")
    ap.add_argument("--base", required=True, help="基表 CSV（需含 ts_code,name,tdx_l1）")
    ap.add_argument("--db", required=True, help="SQLite 数据库路径（含 daily_price 表）")
    ap.add_argument("--out", default="market_enriched.csv", help="输出文件名（默认：market_enriched.csv）")
    ap.add_argument(
        "--use-tushare",
        type=int,
        default=1,
        help="是否用 TuShare daily_basic 补 PB/PE_TTM（1=是，0=否），默认 1",
    )

    args = ap.parse_args()

    base_file = os.path.abspath(args.base)
    db_path = os.path.abspath(args.db)
    out_file = os.path.abspath(args.out)
    use_ts = bool(args.use_tushare)

    print(f"使用基表: {base_file}")
    print(f"使用 SQLite DB: {db_path}")
    print(f"输出文件: {out_file}")
    print("近低窗口 = 最近 1 年自然日")

    # 读基表
    df_base = pd.read_csv(base_file, dtype=str)
    if "ts_code" not in df_base.columns:
        raise SystemExit("基表缺少 ts_code 列")

    # 保留常用字段
    keep_cols = [c for c in ["ts_code", "name", "tdx_l1", "tdx_l2", "list_date"] if c in df_base.columns]
    df_base = df_base[keep_cols].drop_duplicates("ts_code").reset_index(drop=True)

    total = len(df_base)
    print(f"基表标的数: {total}")

    conn = connect_db(db_path)

    # 全局 snapshot_trade_date = daily_price 最大日期（YYYY-MM-DD）
    # 默认用 DB，但若启用 Tushare，应该尝试获取真实的最新交易日
    db_max_date = get_global_max_trade_date(conn)
    snapshot_trade_date = db_max_date
    
    real_latest_date = None
    
    if use_ts:
        try:
            import tushare as ts
            token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("ts_token")
            if token:
                ts.set_token(token)
                pro = ts.pro_api()
                # Get latest trading day relative to TODAY (System Time)
                today_str = datetime.now().strftime("%Y%m%d")
                # Look back 60 days to find latest open day (Cover longer holidays/delays)
                start_str = (datetime.now() - pd.DateOffset(days=60)).strftime("%Y%m%d")
                
                # Use SSE (Shanghai) as proxy for A-share calendar.
                cal = pro.trade_cal(exchange='SSE', start_date=start_str, end_date=today_str, is_open='1')
                if not cal.empty:
                    # Sort by date ascending
                    cal = cal.sort_values('cal_date')
                    
                    # Probe from latest date backwards to find one with actual data
                    dates_to_check = cal["cal_date"].values[::-1] # Newest first
                    
                    found_date = None
                    for date_str in dates_to_check:
                         print(f"Checking data availability for {date_str}...")
                         try:
                             # Check if daily_basic has data for this date. 
                             # '000001.SZ' is just a sample, usually safer to check without code or checking a major index comp.
                             # But daily_basic(trade_date=...) works for all.
                             # Limit to 1 row to be fast.
                             check_df = pro.daily_basic(trade_date=date_str, limit=1, fields='ts_code')
                             if check_df is not None and not check_df.empty:
                                 found_date = date_str
                                 print(f"📡 Found valid data for {date_str}. Using this as snapshot date.")
                                 break
                             else:
                                 print(f"⚠️ No daily_basic data for {date_str} yet.")
                         except Exception as e:
                             print(f"Error checking {date_str}: {e}")
                             
                    if found_date:
                        real_latest_date = found_date
                    else:
                        # Fallback to absolute latest if probe fails (unlikely if loop encompasses enough days)
                        real_latest_date = cal.iloc[-1]["cal_date"] 
                        print(f"⚠️ Probe failed, falling back to calendar latest: {real_latest_date}")
                        
                    print(f"📡 Final Tushare Snapshot Date: {real_latest_date}")
        except Exception as e:
            print(f"⚠️ 尝试获取 Tushare 最新日期失败: {e}")

    # Decision: If we use Tushare, we trust Tushare's date as 'snapshot_trade_date'
    if use_ts and real_latest_date:
        # Tushare returns YYYYMMDD, normalize to YYYY-MM-DD if needed or keep consistent
        # Our script uses YYYY-MM-DD for DB matching usually?
        # But DB uses whatever formatted string.
        # Let's keep snapshot_trade_date as the DISPLAY date.
        # Convert YYYYMMDD -> YYYY-MM-DD
        snapshot_trade_date = f"{real_latest_date[:4]}-{real_latest_date[4:6]}-{real_latest_date[6:]}"
        snapshot_trade_date_ymd = real_latest_date
    else:
        if not snapshot_trade_date:
             raise SystemExit("在 daily_price 表中未找到任何 trade_date，且无法连接 Tushare 获取最新日期。")
        snapshot_trade_date_ymd = pd.to_datetime(snapshot_trade_date).strftime("%Y%m%d")

    print(f"全局 snapshot_trade_date: {snapshot_trade_date} （TuShare 请求日: {snapshot_trade_date_ymd}）")

    # 先算近低点 & latest（来自 DB 日线）
    low_list = []
    low_date_list = []
    latest_list = []
    close_252d_list = []
    low_window_start = calendar_window_start(snapshot_trade_date, years=1)

    for i, row in df_base.iterrows():
        ts = row["ts_code"]
        latest_row = get_latest_from_db(conn, ts)
        window_df = get_window_from_db(conn, ts, start_date=low_window_start)

        if latest_row.empty:
            low_list.append(None)
            low_date_list.append(None)
            latest_list.append(None)
            close_252d_list.append(None)
        else:
            # latest 用最新一条 close
            try:
                latest_price = float(pd.to_numeric(latest_row["close"], errors="coerce").iloc[-1])
            except Exception:
                latest_price = None
            latest_list.append(latest_price)

            if window_df.empty:
                low_list.append(None)
                low_date_list.append(None)
                close_252d_list.append(None)
            else:
                # 最近 1 年自然日最低价及日期
                try:
                    window_df["low_f"] = pd.to_numeric(window_df["low"], errors="coerce")
                    idxmin = window_df["low_f"].idxmin()
                    low_price = float(window_df.loc[idxmin, "low_f"])
                    low_date = str(window_df.loc[idxmin, "trade_date"])
                except Exception:
                    low_price = None
                    low_date = None

                low_list.append(low_price)
                low_date_list.append(low_date)

                try:
                    c252 = float(pd.to_numeric(window_df["close"], errors="coerce").iloc[0])
                except Exception:
                    c252 = None
                close_252d_list.append(c252)

        if (i + 1) % 500 == 0 or i + 1 == total:
            print(f"[{i+1}/{total}] 已处理到 {ts}")

    conn.close()

    # 组装输出 DataFrame（先不含 PB / PE）
    df_out = df_base.copy()
    df_out["latest"] = latest_list
    df_out["low_252d"] = low_list
    df_out["low_252d_date"] = low_date_list
    df_out["close_252d"] = close_252d_list

    # 计算距近一年低点涨幅
    df_out["low_252d"] = pd.to_numeric(df_out["low_252d"], errors="coerce")
    df_out["latest"] = pd.to_numeric(df_out["latest"], errors="coerce")
    # df_out["close_252d"] = pd.to_numeric(df_out["close_252d"], errors="coerce") # No longer used
    
    df_out["rise_from_1y_low_pct"] = (
        (df_out["latest"] - df_out["low_252d"]) / df_out["low_252d"] * 100
    ).where(df_out["low_252d"] > 0)
    
    # 默认先给 PB / PE 列，占位
    df_out["pb"] = None
    df_out["pe_ttm"] = None
    
    # Yearly Growth Placeholder
    df_out["yearly_growth"] = None
    
    # Reference Date for 1Y Calculation
    # Default to DB Snapshot. If Tushare updates it, we update ref_date.
    ref_date_obj = pd.to_datetime(snapshot_trade_date)
    
    # 如启用 TuShare，则合并 PB / PE 及 最新收盘价
    if use_ts:
        df_ts = fetch_daily_basic_ts(snapshot_trade_date_ymd)
        if not df_ts.empty:
            df_out = df_out.merge(df_ts, on="ts_code", how="left", suffixes=("", "_ts"))
            # merge 后 pb_x / pb_y 的情况：前面占位列名是 pb，这里再赋值一次即可
            df_out["pb"] = df_out["pb_ts"]
            df_out["pe_ttm"] = df_out["pe_ttm_ts"]
            
            # Use Tushare Close as Latest Price (Real-time override)
            if "close" in df_out.columns:
                 # 'close' came from Tushare (df_ts) because df_out used 'latest'
                 df_out["latest"] = df_out["close"].combine_first(df_out["latest"])
            elif "close_ts" in df_out.columns:
                 # Fallback if column rename happened (unlikely unless base has 'close')
                 df_out["latest"] = df_out["close_ts"].combine_first(df_out["latest"])
            
            # Recalculate Rise from Low with new latest
            df_out["rise_from_1y_low_pct"] = (
                (df_out["latest"] - df_out["low_252d"]) / df_out["low_252d"] * 100
            ).where(df_out["low_252d"] > 0)
            
            # If we got data from Tushare, the 'Latest' date is snapshot_trade_date (which was used to query TS)
            # Actually, fetch_daily_basic_ts uses snapshot_trade_date_ymd.
            # So ref date is consistently snapshot_trade_date.
            
            df_out.drop(columns=[c for c in df_out.columns if c.endswith("_ts") or c == "close_ts"], inplace=True)
        else:
            print("⚠ 未能从 TuShare 获取 PB/PE，相关列将保持为空。")
    else:
        # --use-tushare 0：从本地 daily_valuation 表读取 PB/PE
        # 注意：第 279 行已 conn.close()，需重新打开连接
        try:
            conn_val = connect_db(db_path)
            val_df = pd.read_sql_query(
                """
                SELECT ts_code, pb, pe_ttm
                FROM daily_valuation
                WHERE trade_date = (SELECT MAX(trade_date) FROM daily_valuation)
                """,
                conn_val
            )
            conn_val.close()
            if not val_df.empty:
                val_df["pb"] = pd.to_numeric(val_df["pb"], errors="coerce")
                val_df["pe_ttm"] = pd.to_numeric(val_df["pe_ttm"], errors="coerce")
                df_out = df_out.merge(val_df, on="ts_code", how="left", suffixes=("", "_val"))
                df_out["pb"] = df_out["pb_val"].combine_first(df_out["pb"])
                df_out["pe_ttm"] = df_out["pe_ttm_val"].combine_first(df_out["pe_ttm"])
                df_out.drop(columns=[c for c in df_out.columns if c.endswith("_val")], inplace=True)
                non_na = int(val_df["pb"].notna().sum())
                print(f"✅ 从 daily_valuation 补充了 PB/PE（{non_na} / {len(val_df)} 条有值）")
            else:
                print("⚠ daily_valuation 表为空，PB/PE 列将保持为空")
        except Exception as e:
            print(f"⚠ 读取 daily_valuation 失败: {e}，PB/PE 列将保持为空")

    # --- Calcluate Calendar Year Growth ---
    # target_date = ref_date - 1 year
    target_date_obj = ref_date_obj - pd.DateOffset(years=1)
    target_date_str = target_date_obj.strftime("%Y%m%d")
    
    print(f"计算年涨幅: 参考日期 {snapshot_trade_date}, 目标日期 >= {target_date_str}")
    
    conn = connect_db(db_path)
    try:
        print(f"正在从数据库读取 {target_date_str} 之后的历史数据以计算涨幅...")
        if "-" in str(snapshot_trade_date):
            tgt_str = target_date_obj.strftime("%Y-%m-%d")
        else:
            tgt_str = target_date_obj.strftime("%Y%m%d")
            
        hist_df = pd.read_sql_query(
            "SELECT ts_code, trade_date, close FROM daily_price WHERE trade_date >= ?",
            conn, params=(tgt_str,)
        )
        
        if not hist_df.empty:
            # Sort by Date ASC
            hist_df = hist_df.sort_values("trade_date")
            # Group by ts_code, take first (earliest)
            start_prices = hist_df.groupby("ts_code").first()["close"]
            
            # Map back to df_out
            # df_out['latest'] is already set.
            # Align indices
            df_out_indexed = df_out.set_index("ts_code")
            df_out_indexed["close_1y"] = start_prices
            
            # Calc
            # Ensure types
            df_out_indexed["latest"] = pd.to_numeric(df_out_indexed["latest"], errors="coerce")
            df_out_indexed["close_1y"] = pd.to_numeric(df_out_indexed["close_1y"], errors="coerce")
            
            df_out_indexed["yearly_growth"] = (
                (df_out_indexed["latest"] - df_out_indexed["close_1y"]) / df_out_indexed["close_1y"] * 100
            ).where(df_out_indexed["close_1y"] > 0)
            
            # Restore
            df_out = df_out_indexed.reset_index()
            print("年涨幅计算完成。")
        else:
            print("⚠ 警告：未查询到历史基准数据，年涨幅可能为空。")
            
    except Exception as e:
        print(f"❌ 计算年涨幅时出错: {e}")
    finally:
        conn.close()

    # 时间戳
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df_out["update_time"] = now_str
    df_out["snapshot_trade_date"] = snapshot_trade_date

    # 输出
    cols_order = []
    for c in ["ts_code", "name", "tdx_l1", "tdx_l2", "list_date",
              "latest", "pb", "pe_ttm",
              "low_252d", "low_252d_date", "rise_from_1y_low_pct", "yearly_growth",
              "snapshot_trade_date", "update_time"]:
        if c in df_out.columns:
            cols_order.append(c)
    # 再把剩余列拼在后面，防止丢信息
    cols_order += [c for c in df_out.columns if c not in cols_order]

    df_out[cols_order].to_csv(out_file, index=False, encoding="utf-8-sig")

    print("\n✅ market_enriched.csv 已生成")
    print(f"  标的数: {len(df_out)}")
    print(f"  全局 snapshot_trade_date: {snapshot_trade_date}")
    if use_ts:
        non_na_pb = df_out["pb"].notna().sum()
        non_na_pe = df_out["pe_ttm"].notna().sum()
        print(f"  PB 非空数量: {non_na_pb} / {len(df_out)}")
        print(f"  PE_TTM 非空数量: {non_na_pe} / {len(df_out)}")


if __name__ == "__main__":
    main()
