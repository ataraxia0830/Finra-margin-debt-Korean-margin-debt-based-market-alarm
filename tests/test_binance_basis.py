from datetime import datetime, timezone

import pandas as pd

from market_alarm.collectors.binance import _delivery_basis


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Client:
    def get(self, url, **kwargs):
        if url.endswith("exchangeInfo"):
            expiry = int(datetime(2030, 12, 27, tzinfo=timezone.utc).timestamp() * 1000)
            return _Response(
                {
                    "symbols": [
                        {
                            "symbol": "BTCUSD_301227",
                            "status": "TRADING",
                            "contractType": "CURRENT_QUARTER",
                            "deliveryDate": expiry,
                        }
                    ]
                }
            )
        return _Response({"price": "110"})


class _ListTickerClient(_Client):
    def get(self, url, **kwargs):
        result = super().get(url, **kwargs)
        if not url.endswith("exchangeInfo"):
            return _Response([result.payload])
        return result


def test_btc_delivery_basis_uses_quarterly_contract_not_perpetual_proxy():
    fallback = pd.DataFrame(
        {"basis_percent": [1.0], "basis_annualized": [9.0]},
        index=[pd.Timestamp("2026-08-01", tz="UTC")],
    )
    result = _delivery_basis(
        _Client(), {"http": {"timeout_seconds": 30}}, "https://dapi.binance.com",
        "BTCUSDT", 100.0, fallback,
    )
    assert result["contract"].iloc[-1] == "BTCUSD_301227"
    assert round(result["basis_percent"].iloc[-1], 8) == 10.0


def test_missing_sol_quarterly_contract_uses_perpetual_proxy():
    fallback = pd.DataFrame(
        {"basis_percent": [1.0], "basis_annualized": [9.0]},
        index=[pd.Timestamp("2026-08-01", tz="UTC")],
    )
    result = _delivery_basis(
        _Client(), {"http": {"timeout_seconds": 30}}, "https://dapi.binance.com",
        "SOLUSDT", 100.0, fallback,
    )
    assert result["contract"].iloc[-1] == "PERPETUAL_PROXY"
    assert result["basis_annualized"].iloc[-1] == 9.0


def test_www_gateway_list_ticker_response_is_supported():
    fallback = pd.DataFrame(
        {"basis_percent": [1.0], "basis_annualized": [9.0]},
        index=[pd.Timestamp("2026-08-01", tz="UTC")],
    )
    result = _delivery_basis(
        _ListTickerClient(),
        {"http": {"timeout_seconds": 30}},
        "https://www.binance.com",
        "BTCUSDT",
        100.0,
        fallback,
    )
    assert result["contract"].iloc[-1] == "BTCUSD_301227"
    assert round(result["basis_percent"].iloc[-1], 8) == 10.0
