import pandas as pd

from scripts.etl.sync_hk_daily_prices import _normalize_hk_hist_df


def test_normalize_hk_hist_df_accepts_sina_shape_and_filters_dates():
    df = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-02-01")],
            "open": [10.0, 11.0],
            "high": [10.5, 11.5],
            "low": [9.8, 10.8],
            "close": [10.2, 11.2],
            "volume": [1000, 2000],
        }
    )

    normalized = _normalize_hk_hist_df(df, "20240101", "20240131")

    assert list(normalized.columns) == ["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]
    assert normalized.to_dict(orient="records") == [
        {
            "日期": "2024-01-02",
            "开盘": 10.0,
            "最高": 10.5,
            "最低": 9.8,
            "收盘": 10.2,
            "成交量": 1000,
            "成交额": None,
        }
    ]
