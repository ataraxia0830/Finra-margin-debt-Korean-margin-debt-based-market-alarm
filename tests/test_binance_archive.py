from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from market_alarm.collectors.binance import (
    _archive_futures,
    _estimated_funding_from_premium,
)


def _zip_csv(name: str, text: str) -> bytes:
    payload = BytesIO()
    with ZipFile(payload, "w", ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
    return payload.getvalue()


class _ArchiveResponse:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


class _ArchiveClient:
    def get(self, url, **kwargs):
        timestamp = int(pd.Timestamp("2026-07-31T20:00:00Z").timestamp() * 1000)
        if "/metrics/" in url:
            return _ArchiveResponse(
                _zip_csv(
                    "metrics.csv",
                    "create_time,symbol,sum_open_interest,sum_open_interest_value,"
                    "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
                    "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
                    "2026-07-31 20:00:00,BTCUSDT,100,6000000000,1.1,1.2,1.3,1.4\n",
                )
            )
        if "/fundingRate/" in url:
            return _ArchiveResponse(
                _zip_csv(
                    "funding.csv",
                    "calc_time,funding_interval_hours,last_funding_rate\n"
                    f"{timestamp},8,0.0001\n",
                )
            )
        close = "-0.0002"
        if "/markPriceKlines/" in url:
            close = "59988"
        elif "/indexPriceKlines/" in url:
            close = "60000"
        return _ArchiveResponse(
            _zip_csv(
                "kline.csv",
                "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
                "taker_buy_volume,taker_buy_quote_volume,ignore\n"
                f"{timestamp},{close},{close},{close},{close},0,{timestamp},0,1,0,0,0\n",
            )
        )


def test_official_archive_fallback_parses_oi_funding_ratio_and_premium():
    oi, funding, ratio, premium = _archive_futures(
        _ArchiveClient(),
        {"http": {"timeout_seconds": 30}},
        "https://data.binance.vision",
        "BTCUSDT",
        days=1,
        workers=1,
    )
    assert oi["oi_value"].iloc[-1] == 6_000_000_000
    assert funding["funding_rate"].iloc[-1] == 0.0001
    assert ratio["long_short_ratio"].iloc[-1] == 1.3
    assert round(premium["basis_percent"].iloc[-1], 8) == -0.02
    assert premium["mark_price"].iloc[-1] == 59_988


def test_missing_current_month_funding_can_be_estimated_from_premium_formula():
    index = pd.date_range("2026-08-01", periods=8, freq="1h", tz="UTC")
    # Premium within the damper band results in the standard 0.01% rate.
    result = _estimated_funding_from_premium(pd.Series([0.0002] * 8, index=index))
    assert len(result) == 1
    assert round(result["funding_rate"].iloc[-1], 8) == 0.0001
