"""Alarm text must spell out every rule it is judging, with real thresholds."""

import numpy as np
import pandas as pd
import pytest
import yaml

from market_alarm.reporting import render_crypto, render_korea
from market_alarm.rules import assess_crypto, assess_korea, stock_snapshot


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load(open("config.example.yml", encoding="utf-8"))


def _korea_balance(overheated: bool) -> pd.Series:
    idx = pd.date_range("2018-01-31", "2026-07-31", freq="ME", tz="UTC")
    if overheated:
        values = np.concatenate(
            [np.linspace(10, 12, 26), np.linspace(12, 26, 12), np.linspace(26, 19, len(idx) - 38)]
        )
    else:
        values = np.linspace(10, 13, len(idx))
    return pd.Series(values, index=idx)


def _korea_assessment(cfg, overheated=True):
    days = pd.date_range("2020-01-01", "2026-08-01", freq="B", tz="UTC")
    rng = np.random.default_rng(3)
    prices = pd.Series(70_000 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days)))), index=days)
    credit_q = pd.Series(1e6 * np.exp(np.cumsum(rng.normal(0, 0.005, len(days)))), index=days)
    credit_r = pd.Series(np.abs(rng.normal(1.0, 0.3, len(days))), index=days)
    stocks = {}
    for symbol, name in (("005930", "삼성전자"), ("000660", "SK하이닉스")):
        snap = stock_snapshot(prices, credit_q, credit_r)
        snap["name"] = name
        stocks[symbol] = snap
    return assess_korea(
        _korea_balance(overheated), stocks, cfg["freesis"], cfg["kis"]["domestic"]
    )


def _crypto_assessment(cfg, symbol):
    idx = pd.date_range("2021-01-01", "2026-08-01", freq="D", tz="UTC")
    rng = np.random.default_rng(7)
    price = pd.Series(60_000 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))), index=idx)
    oi_idx = pd.date_range("2023-01-01", "2026-08-01", freq="4h", tz="UTC")
    oi = pd.Series(1e10 * np.exp(np.cumsum(rng.normal(0, 0.005, len(oi_idx)))), index=oi_idx)
    f_idx = pd.date_range("2023-01-01", "2026-08-01", freq="8h", tz="UTC")
    funding = pd.Series(rng.normal(0.0001, 0.0002, len(f_idx)), index=f_idx)
    long_short = pd.Series(rng.normal(1.5, 0.1, len(oi_idx)), index=oi_idx)
    basis = pd.Series(rng.normal(8, 3, len(oi_idx)), index=oi_idx)
    return assess_crypto(
        symbol,
        price,
        oi,
        funding,
        long_short,
        basis,
        cfg["binance"],
        btc_price=price if symbol == "SOLUSDT" else None,
        mark_price=price,
        index_price=price,
        basis_percent=basis,
    )


# --------------------------------------------------------------------- korea


def test_korea_replaces_leading_peak_with_prior_peak_yoy(cfg):
    message = render_korea(_korea_assessment(cfg))
    assert "선행 최고 YoY" not in message
    assert "직전 전고 YoY:" in message


def test_korea_lists_market_buy_condition(cfg):
    message = render_korea(_korea_assessment(cfg))
    assert (
        "[매수조건] 전고점 YoY +60.0% 이상 + 신용공여 잔고추이 YoY -25.0% 이하:" in message
    )


def test_korea_lists_market_sell_condition_with_all_four_parts(cfg):
    message = render_korea(_korea_assessment(cfg))
    assert "[매도조건] 18개월 내 -45.0% 이상 붕괴 없음" in message
    assert "전고점 YoY +60.0% 이상" in message
    assert "고점 이후 YoY 15.0%p 하락" in message
    assert "신용공여 절댓값 최초 감소전환:" in message


def test_korea_lists_absolute_balance_buy_condition_after_drawdown(cfg):
    message = render_korea(_korea_assessment(cfg))
    lines = message.splitlines()
    dd = next(i for i, line in enumerate(lines) if line.startswith("고점 대비 잔고:"))
    assert "신용공여 잔고 절댓값 전고점 대비 -30.0% 이하:" in lines[dd + 1]


def test_korea_without_overheat_history_still_renders_conditions(cfg):
    """The NORMAL early return must carry the same condition flags."""
    message = render_korea(_korea_assessment(cfg, overheated=False))
    assert "직전 전고 YoY:" in message
    assert "[매수조건]" in message
    assert "[매도조건]" in message


# -------------------------------------------------------------------- crypto


@pytest.mark.parametrize("symbol", ["BTCUSDT", "SOLUSDT"])
def test_crypto_shows_buy_and_sell_time_windows(cfg, symbol):
    message = render_crypto(_crypto_assessment(cfg, symbol))
    assert "[매수조건] 전고점 후 9~15개월 경과" in message
    assert "[매도조건] 전고점 후 42~54개월 경과" in message


def test_btc_price_gate_names_the_ma120_percentile_rule(cfg):
    message = render_crypto(_crypto_assessment(cfg, "BTCUSDT"))
    assert "가격 필수조건 120일선 상방 이격률이 최근 3년 상위 10%:" in message
    assert "저점 대비 10배" not in message


def test_sol_price_gate_adds_the_ten_bagger_alternative(cfg):
    message = render_crypto(_crypto_assessment(cfg, "SOLUSDT"))
    assert (
        "가격 필수조건 저점 대비 10배 이상 상승 or "
        "120일선 상방 이격률이 최근 3년 상위 10%:" in message
    )
    assert "저점 대비 상승배수" in message


@pytest.mark.parametrize(
    "symbol,weak,strong",
    [("BTCUSDT", "-60.0%", "-70.0%"), ("SOLUSDT", "-80.0%", "-85.0%")],
)
def test_crypto_buy_rules_are_spelled_out(cfg, symbol, weak, strong):
    message = render_crypto(_crypto_assessment(cfg, symbol))
    assert f"약한 매수 = 시간조건 + 고점 대비 {weak}:" in message
    assert f"강한 매수 = 시간조건 + 고점 대비 {strong} + 파생조건 2개 이상:" in message
    # The old collapsed wording must be gone.
    assert "약한 매수 조건:" not in message
    assert "강한 매수 조건:" not in message


@pytest.mark.parametrize("symbol", ["BTCUSDT", "SOLUSDT"])
def test_crypto_sell_rules_list_each_overheat_and_reversal_item(cfg, symbol):
    message = render_crypto(_crypto_assessment(cfg, symbol))
    assert "파생 과열:" in message
    assert "OI 최근 3년 상위 10% (현재" in message or "SOL OI 최근 3년 상위 10% (현재" in message
    assert "꺾임 확인 (신고가 대비 10% 이내일 때만 인정):" in message
    assert "약한 매도 = 시간창 + 가격 필수조건 + 파생 과열 2개 이상:" in message
    assert "강한 매도 = 약한 매도 + 꺾임 1개 이상 확인:" in message


@pytest.mark.parametrize("symbol", ["BTCUSDT", "SOLUSDT"])
def test_crypto_message_fits_a_single_telegram_chunk(cfg, symbol):
    message = render_crypto(_crypto_assessment(cfg, symbol))
    assert len(message) <= cfg["alerts"]["telegram_max_chars"]


# -------------------------------------------------------------------- config


def test_config_time_windows_match_the_reported_text(cfg):
    for spec in cfg["binance"]["symbols"].values():
        assert spec["buy_months_min"] == 9
        assert spec["buy_months_max"] == 15
    assert cfg["binance"]["sell_observe_month"] == 42
    assert cfg["binance"]["sell_core_end_month"] == 54


def test_reported_verdicts_match_the_payload_flags(cfg):
    """Rendering must never disagree with the assessment it was given."""
    for symbol in ("BTCUSDT", "SOLUSDT"):
        a = _crypto_assessment(cfg, symbol)
        p = a.payload
        message = render_crypto(a)
        expected_weak = p["time_ok"] and p["opportunity_price_ok"]
        assert p["weak_buy_ok"] == expected_weak
        verdict = "달성" if expected_weak else "미달성"
        assert f"고점 대비 {p['opportunity_price_threshold']:+.1f}%: {verdict}" in message
