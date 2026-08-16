import pandas as pd
import pytest

from market_alarm.rules import assess_crypto, assess_finra, assess_korea


def _monthly(values):
    idx = pd.date_range("2020-01-31", periods=len(values), freq="ME", tz="UTC")
    return pd.Series(values, index=idx, dtype=float)


def _finra_cfg():
    return {
        "overheat_yoy": 50,
        "lookback_months": 48,
        "sideways_buy_warning_yoy": -10,
        "sideways_buy_strong_yoy": -15,
        "buy_warning_yoy": -25,
        "buy_strong_yoy": -30,
        "balance_drawdown_alert_levels": [-20, -25, -30],
        "sell_warning_yoy_drop_points": 10,
        "sell_strong_yoy_drop_points": 15,
        "sell_delay_months": 3,
    }


def _crypto_reversal_cfg():
    return {
        "btc_oi_drawdown": -10,
        "sol_oi_drawdown": -15,
        "funding_drawdown": -30,
        "basis_drawdown": -25,
        "btc_price_drawdown": -12,
        "sol_price_drawdown": -15,
        "solbtc_drawdown": -10,
        "near_high_drawdown": -10,
    }


def _crypto_sell_cfg():
    common_buy = {
        "buy_months_min": 12,
        "buy_months_max": 14,
        "buy_opportunity_price_drawdown": -60,
        "buy_price_drawdown": -70,
        "buy_oi_drawdown": -50,
        "buy_oi_strong_drawdown": -60,
    }
    return {
        "symbols": {
            "BTCUSDT": {**common_buy, "sell_cycle_high_date": "2021-11-10"},
            "SOLUSDT": {
                **common_buy,
                "buy_months_min": 10,
                "buy_opportunity_price_drawdown": -80,
                "buy_price_drawdown": -85,
                "sell_cycle_high_date": "2021-11-06",
            },
        },
        "derivative_min_count": 2,
        "sell_observe_month": 42,
        "sell_watch_month": 45,
        "sell_core_end_month": 51,
        "sell_price_ma120_percentile": 90,
        "sell_price_lookback_days": 1095,
        "sell_hot_lookback_days": 1095,
        "sell_min_derivative_history_days": 180,
        "sell_recent_hot_days": 30,
        "sell_reversal_lookback_days": 180,
        "overheat": {
            "oi_percentile": 90,
            "oi_180d_growth_percent": 100,
            "funding_percentile": 90,
            "basis_percentile": 90,
        },
        "reversal": _crypto_reversal_cfg(),
    }


def test_finra_sideways_opportunity_when_no_50pct_yoy_in_48_months():
    values = [100.0] * 60
    values[-1] = 89.0  # Current YoY -11%; no +50% reading in the lookback.
    result = assess_finra(_monthly(values), _finra_cfg())
    assert result.state == "SIDEWAYS_BUY_OPPORTUNITY_MINUS_10"
    assert result.signal == "약한 매수"
    assert result.payload["overheat_in_lookback"] is False


def test_finra_sideways_strong_when_no_50pct_yoy_in_48_months():
    values = [100.0] * 60
    values[-1] = 84.0  # Current YoY -16%; no +50% reading in the lookback.
    result = assess_finra(_monthly(values), _finra_cfg())
    assert result.state == "SIDEWAYS_BUY_STRONG_MINUS_15"
    assert result.signal == "강한 매수"


@pytest.mark.parametrize(
    ("current_yoy", "expected_state"),
    [
        (-9.9, "NORMAL"),
        (-10.0, "SIDEWAYS_BUY_OPPORTUNITY_MINUS_10"),
        (-14.9, "SIDEWAYS_BUY_OPPORTUNITY_MINUS_10"),
        (-15.0, "SIDEWAYS_BUY_STRONG_MINUS_15"),
    ],
)
def test_finra_sideways_threshold_boundaries(current_yoy, expected_state):
    values = [100.0] * 60
    values[-1] = 100.0 * (1 + current_yoy / 100)
    result = assess_finra(_monthly(values), _finra_cfg())
    assert result.state == expected_state


def test_finra_overheat_regime_suppresses_sideways_thresholds():
    values = [100.0] * 60
    values[47] = 151.0  # +51% remains within the latest 48 YoY observations.
    values[-1] = 84.0   # -16% would be strong only in the sideways regime.
    result = assess_finra(_monthly(values), _finra_cfg())
    assert result.state not in {
        "SIDEWAYS_BUY_OPPORTUNITY_MINUS_10",
        "SIDEWAYS_BUY_STRONG_MINUS_15",
    }
    assert result.payload["overheat_in_lookback"] is True


def test_finra_calendar_48_month_boundary_is_included():
    values = [100.0] * 73
    # The resulting +51% YoY point is exactly 48 calendar months before the
    # latest YoY point.  It must still suppress the sideways thresholds.
    values[12] = 100.0
    values[24] = 151.0
    values[-1] = 84.0
    result = assess_finra(_monthly(values), _finra_cfg())
    assert result.payload["overheat_in_lookback"] is True
    assert result.payload["lookback_peak_yoy"] >= 50
    assert result.state not in {
        "SIDEWAYS_BUY_OPPORTUNITY_MINUS_10",
        "SIDEWAYS_BUY_STRONG_MINUS_15",
    }


def test_finra_strong_buy_uses_yoy_as_independent_core_condition():
    values = [100.0] * 60
    values[35] = 100
    values[47] = 160  # YoY +60%
    values[48:57] = [150, 140, 130, 120, 110, 100, 90, 80, 75]
    values[57:] = [78, 76, 69]  # YoY -31%, but no flattening requirement.
    cfg = {
        "overheat_yoy": 50,
        "lookback_months": 48,
        "buy_warning_yoy": -25,
        "buy_strong_yoy": -30,
        "balance_drawdown_alert_levels": [-20, -25, -30],
        "sell_warning_yoy_drop_points": 10,
        "sell_strong_yoy_drop_points": 15,
        "sell_delay_months": 3,
    }
    result = assess_finra(_monthly(values), cfg)
    assert result.state.startswith("BUY_STRONG_MINUS_30")
    assert result.payload["current_yoy"] <= -30
    assert result.signal == "강한 매수"


def test_finra_opportunity_at_minus_25_without_balance_or_flattening_and():
    values = [100.0] * 61
    values[47] = 160
    values[49:60] = [150, 145, 140, 135, 130, 125, 120, 115, 110, 105, 100]
    values[60] = 74  # Versus index 48's 100: YoY -26%.
    cfg = {
        "overheat_yoy": 50,
        "lookback_months": 48,
        "buy_warning_yoy": -25,
        "buy_strong_yoy": -30,
        "balance_drawdown_alert_levels": [-20, -25, -30],
        "sell_warning_yoy_drop_points": 10,
        "sell_strong_yoy_drop_points": 15,
        "sell_delay_months": 3,
    }
    result = assess_finra(_monthly(values), cfg)
    assert result.state.startswith("BUY_OPPORTUNITY_MINUS_25")
    assert result.signal == "약한 매수"


def test_finra_strong_sell_waits_for_yoy_to_cross_below_50():
    values = [100.0] * 48
    values[35] = 100
    values[47] = 170  # current YoY +70%; peak
    # Next month remains >50 but is 16 percentage points below the +70 peak.
    values.append(154)  # versus prior-year 100 => +54%; 16%p below peak
    cfg = {
        "overheat_yoy": 50,
        "lookback_months": 48,
        "buy_warning_yoy": -25,
        "buy_strong_yoy": -30,
        "balance_drawdown_alert_levels": [-20, -25, -30],
        "sell_warning_yoy_drop_points": 10,
        "sell_strong_yoy_drop_points": 15,
        "sell_delay_months": 3,
    }
    series = _monthly(values)
    result = assess_finra(series, cfg, as_of=series.index[-1])
    assert result.state == "WAIT_FOR_YOY_BELOW_50"
    assert result.signal == "강한 매도 / YoY +50% 하회 대기"


def test_finra_10_percentage_point_drop_is_weak_sell():
    values = [100.0] * 48
    values[47] = 170
    values.append(160)  # +60%, exactly 10%p below the +70% peak.
    result = assess_finra(_monthly(values), _finra_cfg())
    assert result.state == "SELL_WARNING_DROP_10"
    assert result.signal == "약한 매도"


@pytest.mark.parametrize(
    ("current_yoy", "expected_signal"),
    [
        (45, "평시 매수"),  # 50 -> 45 is only a 5%p decline.
        (40, "약한 매도"),  # 50 -> 40 is a 10%p decline.
        (35, "강한 매도 / 3개월 후 매도"),  # 50 -> 35 is a 15%p decline.
    ],
)
def test_finra_sell_thresholds_are_yoy_percentage_points(current_yoy, expected_signal):
    values = [100.0] * 48
    values[47] = 150  # YoY peak +50%.
    values.append(100 * (1 + current_yoy / 100))
    series = _monthly(values)

    result = assess_finra(series, _finra_cfg(), as_of=series.index[-1])

    assert result.payload["yoy_drop_points"] == 50 - current_yoy
    assert result.signal == expected_signal


def test_finra_sell_schedules_then_alerts_in_target_month():
    values = [100.0] * 48
    values[47] = 170
    values.append(150)  # YoY +50%, 20%p below the +70% peak.
    cfg = {
        "overheat_yoy": 50,
        "lookback_months": 48,
        "buy_warning_yoy": -25,
        "buy_strong_yoy": -30,
        "balance_drawdown_alert_levels": [-20, -25, -30],
        "sell_warning_yoy_drop_points": 10,
        "sell_strong_yoy_drop_points": 15,
        "sell_delay_months": 3,
    }
    series = _monthly(values)
    peak_month = series.index[-2]
    target_month = peak_month + pd.DateOffset(months=3)
    scheduled = assess_finra(series, cfg, as_of=series.index[-1])
    due = assess_finra(series, cfg, as_of=target_month)
    assert scheduled.state.startswith("SELL_SCHEDULED_")
    assert scheduled.payload["sell_due"] is False
    assert scheduled.payload["sell_reference_month"] == peak_month.strftime("%Y-%m")
    assert scheduled.signal == "강한 매도 / 3개월 후 매도"
    assert due.state.startswith("SELL_DUE_")
    assert due.payload["sell_due"] is True
    assert due.signal == "강한 매도 / 당월 매도 필요"


@pytest.mark.parametrize(
    ("current_yoy", "drop_ok"),
    [(51, False), (45, True)],
)
def test_korea_sell_threshold_is_15_yoy_percentage_points(current_yoy, drop_ok):
    values = [100.0] * 48
    values[47] = 160  # YoY peak +60%.
    values.append(100 * (1 + current_yoy / 100))
    cfg = {
        "overheat_yoy": 60,
        "lookback_months": 48,
        "buy_warning_yoy": -25,
        "buy_strong_yoy": -30,
        "balance_drawdown_min": -30,
        "sell_yoy_drop_points": 15,
        "prior_crash_cutoff": -45,
        "prior_crash_lookback_months": 18,
    }

    result = assess_korea(_monthly(values), {}, cfg, {})

    assert result.payload["yoy_drop_points"] == 60 - current_yoy
    assert result.payload["sell_yoy_drop_points_ok"] is drop_ok


def test_crypto_buy_requires_time_price_and_two_derivatives():
    now = pd.Timestamp("2026-07-31", tz="UTC")
    price_idx = pd.date_range(end=now, periods=410, freq="D")
    price = pd.Series(100.0, index=price_idx)
    price.iloc[0] = 400.0
    oi_idx = pd.date_range(end=now, periods=180, freq="4h")
    oi = pd.Series(40.0, index=oi_idx)
    oi.iloc[0] = 100.0
    funding = pd.Series(-0.0001, index=oi_idx)
    long_short = pd.Series(0.9, index=oi_idx)
    basis = pd.Series(-2.0, index=oi_idx)
    cfg = {
        "symbols": {
            "BTCUSDT": {
                "buy_months_min": 12,
                "buy_months_max": 14,
                "buy_price_drawdown": -70,
                "buy_oi_drawdown": -50,
                "buy_oi_strong_drawdown": -60,
                "sell_cycle_high_date": "2025-06-17",
            }
        },
        "derivative_min_count": 2,
        "sell_observe_month": 42,
        "sell_watch_month": 45,
        "sell_core_end_month": 51,
        "sell_min_derivative_history_days": 180,
        "overheat": {
            "oi_percentile": 90,
            "oi_180d_growth_percent": 100,
            "funding_percentile": 90,
            "basis_percentile": 90,
        },
        "reversal": _crypto_reversal_cfg(),
    }
    result = assess_crypto(
        "BTCUSDT", price, oi, funding, long_short, basis, cfg
    )
    assert result.state == "STRONG_BUY_TRIGGERED"
    assert result.payload["derivative_met"] >= 2


def test_crypto_opportunity_uses_new_shallower_price_threshold():
    now = pd.Timestamp("2026-07-31", tz="UTC")
    price_idx = pd.date_range(end=now, periods=410, freq="D")
    price = pd.Series(40.0, index=price_idx)
    price.iloc[0] = 100.0  # BTC drawdown exactly -60%.
    oi_idx = pd.date_range(end=now, periods=180, freq="4h")
    oi = pd.Series(100.0, index=oi_idx)
    funding = pd.Series(0.0001, index=oi_idx)
    cfg = {
        "symbols": {
            "BTCUSDT": {
                "buy_months_min": 12,
                "buy_months_max": 14,
                "buy_opportunity_price_drawdown": -60,
                "buy_price_drawdown": -70,
                "buy_oi_drawdown": -50,
                "buy_oi_strong_drawdown": -60,
                "sell_cycle_high_date": "2025-06-17",
            }
        },
        "derivative_min_count": 2,
        "sell_observe_month": 42,
        "sell_watch_month": 45,
        "sell_core_end_month": 51,
        "sell_min_derivative_history_days": 180,
        "overheat": {
            "oi_percentile": 90,
            "oi_180d_growth_percent": 100,
            "funding_percentile": 90,
            "basis_percentile": 90,
        },
        "reversal": _crypto_reversal_cfg(),
    }
    result = assess_crypto(
        "BTCUSDT",
        price,
        oi,
        funding,
        pd.Series(1.0, index=oi_idx),
        pd.Series(0.0, index=oi_idx),
        cfg,
    )
    assert result.state == "BUY_OPPORTUNITY_TRIGGERED"


def test_btc_sell_prepares_after_time_price_and_two_overheat_conditions():
    now = pd.Timestamp("2025-09-10", tz="UTC")
    price_idx = pd.date_range(pd.Timestamp("2021-11-10", tz="UTC"), now, freq="D")
    price = pd.Series([100 * (1.004 ** i) for i in range(len(price_idx))], index=price_idx)
    deriv_idx = pd.date_range(end=now, periods=1200, freq="4h")
    oi = pd.Series([100 + i * 0.2 for i in range(len(deriv_idx))], index=deriv_idx)
    funding = pd.Series([0.0001 + i * 0.0000005 for i in range(len(deriv_idx))], index=deriv_idx)
    basis = pd.Series([1 + i * 0.01 for i in range(len(deriv_idx))], index=deriv_idx)
    result = assess_crypto(
        "BTCUSDT", price, oi, funding, pd.Series(1.0, index=deriv_idx), basis,
        _crypto_sell_cfg(),
    )
    assert result.state == "LEVERAGE_OVERHEAT"
    assert result.signal == "약한 매도"
    assert result.payload["sell_time_stage"] == "핵심 고점창"
    assert result.payload["overheat_met"] >= 2


def test_btc_sell_triggers_when_oi_falls_10pct_while_price_near_high():
    now = pd.Timestamp("2025-09-10", tz="UTC")
    price_idx = pd.date_range(pd.Timestamp("2021-11-10", tz="UTC"), now, freq="D")
    price = pd.Series([100 * (1.004 ** i) for i in range(len(price_idx))], index=price_idx)
    deriv_idx = pd.date_range(end=now, periods=1200, freq="4h")
    oi_values = [100 + i * 0.2 for i in range(len(deriv_idx))]
    peak = oi_values[-31]
    for i in range(30):
        oi_values[-30 + i] = peak * (1 - 0.12 * (i + 1) / 30)
    oi = pd.Series(oi_values, index=deriv_idx)
    funding = pd.Series([0.0001 + i * 0.0000005 for i in range(len(deriv_idx))], index=deriv_idx)
    basis = pd.Series([1 + i * 0.01 for i in range(len(deriv_idx))], index=deriv_idx)
    result = assess_crypto(
        "BTCUSDT", price, oi, funding, pd.Series(1.0, index=deriv_idx), basis,
        _crypto_sell_cfg(),
    )
    assert result.state == "SELL_TRIGGERED"
    assert result.signal == "강한 매도"
    assert result.payload["oi_reversal"] is True


def test_sol_sell_uses_btc_and_sol_time_and_triggers_at_15pct_price_reversal():
    now = pd.Timestamp("2025-09-10", tz="UTC")
    price_idx = pd.date_range(pd.Timestamp("2021-11-06", tz="UTC"), now, freq="D")
    sol_values = [10 + i * (140 / (len(price_idx) - 2)) for i in range(len(price_idx) - 1)]
    sol_values.append(sol_values[-1] * 0.84)
    sol = pd.Series(sol_values, index=price_idx)
    btc = pd.Series([100 + i * 0.1 for i in range(len(price_idx))], index=price_idx)
    deriv_idx = pd.date_range(end=now, periods=1200, freq="4h")
    oi = pd.Series([100 + i * 0.25 for i in range(len(deriv_idx))], index=deriv_idx)
    funding = pd.Series([0.0001 + i * 0.0000005 for i in range(len(deriv_idx))], index=deriv_idx)
    basis = pd.Series([1 + i * 0.01 for i in range(len(deriv_idx))], index=deriv_idx)
    result = assess_crypto(
        "SOLUSDT", sol, oi, funding, pd.Series(1.0, index=deriv_idx), basis,
        _crypto_sell_cfg(), btc_price=btc,
    )
    assert result.state == "SELL_TRIGGERED"
    assert result.signal == "강한 매도"
    assert result.payload["sell_time_active"] is True
    assert result.payload["price_reversal"] is True


def test_korea_stock_sell_auxiliary_is_ma120_or_credit_top_10pct():
    values = [100.0] * 48
    values[35] = 100
    values[47] = 170
    values.append(150)
    balance = _monthly(values)
    stocks = {
        "005930": {
            "ma120_gap": 10.0,
            "credit_rate_percentile_5y": 95.0,
        },
        "000660": {
            "ma120_gap": 40.0,
            "credit_rate_percentile_5y": 50.0,
        },
    }
    market_cfg = {
        "overheat_yoy": 60,
        "buy_warning_yoy": -25,
        "buy_strong_yoy": -30,
        "balance_drawdown_min": -30,
        "sell_yoy_drop_points": 15,
        "prior_crash_cutoff": -45,
        "prior_crash_lookback_months": 18,
    }
    stock_cfg = {
        "005930": {
            "buy_ma200_gap": -25,
            "buy_credit_drawdown": -30,
            "sell_ma120_warning": 25,
            "sell_ma120_strong": 30,
            "sell_credit_percentile": 90,
        },
        "000660": {
            "buy_ma200_gap": -30,
            "buy_credit_drawdown": -35,
            "sell_ma120_warning": 35,
            "sell_ma120_strong": 40,
            "sell_credit_percentile": 90,
        },
    }
    result = assess_korea(balance, stocks, market_cfg, stock_cfg)
    assert result.state == "SELL_READY"
    assert result.payload["overheat_symbols"] == ["005930", "000660"]
