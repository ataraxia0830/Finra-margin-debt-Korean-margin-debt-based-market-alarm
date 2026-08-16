import pandas as pd

from market_alarm.reporting import render_finra, render_korea
from market_alarm.rules import Assessment, assess_finra


def test_alert_output_is_clean_and_uses_fixed_signal_names():
    values = [100.0] * 60
    values[-1] = 89.0
    series = pd.Series(
        values,
        index=pd.date_range("2020-01-31", periods=len(values), freq="ME", tz="UTC"),
    )
    assessment = assess_finra(
        series,
        {
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
        },
    )
    message = render_finra(assessment)
    assert "[신호] 약한 매수" in message
    assert "[상태]" not in message
    assert "[신뢰" not in message
    assert "거리" not in message
    assert "약한 매수 기준 -10.0%: 달성" in message


def test_finra_scheduled_and_due_sell_wording():
    payload = {
        "reference_month": "2026-08",
        "market_regime": "선행 과열 후 디레버리징",
        "lookback_months": 48,
        "overheat_yoy": 50,
        "overheat_in_lookback": True,
        "lookback_peak_yoy": 50,
        "lookback_peak_month": "2026-07",
        "current_yoy": 35,
        "opportunity_yoy": -25,
        "strong_yoy": -30,
        "peak_yoy": 50,
        "peak_yoy_month": "2026-07",
        "balance_drawdown": -5,
        "yoy_drop_points": 15,
        "sell_warning_yoy_drop_points": 10,
        "sell_strong_yoy_drop_points": 15,
        "sell_delay_months": 3,
        "sell_reference_kind": "YoY 정점월",
        "sell_reference_month": "2026-08",
        "sell_target_month": "2026-11",
        "flattening": False,
    }

    scheduled = render_finra(
        Assessment(
            "SELL_SCHEDULED_2026_11",
            "강한 매도 / 3개월 후 매도",
            "TRIGGERED",
            {**payload, "sell_due": False},
        )
    )
    due = render_finra(
        Assessment(
            "SELL_DUE_2026_11",
            "강한 매도 / 당월 매도 필요",
            "TRIGGERED",
            {**payload, "sell_due": True},
        )
    )

    assert "[신호] 강한 매도 / 3개월 후 매도" in scheduled
    assert "매도 안내: 3개월 후 매도" in scheduled
    assert "[신호] 강한 매도 / 당월 매도 필요" in due
    assert "매도 안내: 당월 매도 필요" in due


def test_korea_output_lists_buy_and_both_sell_gap_thresholds():
    assessment = Assessment(
        "NORMAL",
        "평시 매수",
        "WATCH",
        {
            "reference_month": "2026-06",
            "stocks": {
                "005930": {
                    "name": "삼성전자",
                    "price": 262_500,
                    "ma120_gap": 27.0,
                    "ma200_gap": -26.0,
                    "buy_ma200_threshold": -25.0,
                    "buy_price_ok": True,
                    "buy_credit_drawdown_threshold": -30.0,
                    "buy_credit_drawdown_ok": False,
                    "buy_credit_percentile_ok": False,
                    "sell_ma120_warning_threshold": 25.0,
                    "sell_ma120_strong_threshold": 30.0,
                    "sell_warning": True,
                    "sell_strong": False,
                    "credit_top_10pct": False,
                },
                "000660": {
                    "name": "SK하이닉스",
                    "price": 1_718_000,
                    "ma120_gap": 41.0,
                    "ma200_gap": -29.0,
                    "buy_ma200_threshold": -30.0,
                    "buy_price_ok": False,
                    "buy_credit_drawdown_threshold": -35.0,
                    "buy_credit_drawdown_ok": True,
                    "buy_credit_percentile_ok": False,
                    "sell_ma120_warning_threshold": 35.0,
                    "sell_ma120_strong_threshold": 40.0,
                    "sell_warning": True,
                    "sell_strong": True,
                    "credit_top_10pct": True,
                },
            },
        },
    )
    message = render_korea(assessment)
    assert "[매수 보조조건]" in message
    assert "200일선 하방 이격률 -25.0%: 달성" in message
    assert "200일선 하방 이격률 -30.0%: 미달성" in message
    assert "120일선 상방 이격률 +25.0%: 달성" in message
    assert "120일선 상방 이격률 +30.0%: 미달성" in message
    assert "120일선 상방 이격률 +35.0%: 달성" in message
    assert "120일선 상방 이격률 +40.0%: 달성" in message


def test_us_output_lists_200d_buy_and_120d_sell_conditions():
    assessment = Assessment(
        "NORMAL",
        "평시 매수",
        "WATCH",
        {
            "reference_month": "2026-06",
            "market_regime": "평시/횡보장",
            "lookback_months": 48,
            "overheat_yoy": 50,
            "overheat_in_lookback": False,
            "lookback_peak_yoy": 20,
            "lookback_peak_month": "2025-01",
            "current_yoy": 5,
            "opportunity_yoy": -10,
            "strong_yoy": -15,
        },
    )
    stocks = {
        "GOOGL": {
            "name": "Alphabet",
            "price": 250,
            "ma120_gap": 27,
            "ma200_gap": -26,
            "buy_ma200_threshold": -25,
            "buy_price_ok": True,
            "sell_ma120_warning_threshold": 25,
            "sell_ma120_strong_threshold": 30,
            "sell_warning": True,
            "sell_strong": False,
        }
    }
    message = render_finra(assessment, stocks)
    assert "[종목별 이동평균 보조조건]" in message
    assert "[매수 보조조건]" in message
    assert "200일선 하방 이격률 -25.0%: 달성" in message
    assert "[매도 보조조건]" in message
    assert "120일선 상방 이격률 +25.0%: 달성" in message
    assert "120일선 상방 이격률 +30.0%: 미달성" in message
