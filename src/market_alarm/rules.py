from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from market_alarm.metrics import (
    drawdown,
    flattening_3m,
    gap_percent,
    month_end,
    months_between,
    percentile_rank,
    yoy,
)


@dataclass
class Assessment:
    state: str
    signal: str
    confidence: str
    payload: dict[str, Any]


def assess_finra(
    balance: pd.Series,
    cfg: dict[str, Any],
    as_of: pd.Timestamp | None = None,
) -> Assessment:
    monthly = month_end(balance)
    growth = yoy(monthly).dropna()
    if len(growth) < 1:
        return Assessment(
            "INSUFFICIENT_DATA",
            "판정 보류",
            "WATCH",
            {"reason": "FINRA YoY 계산에는 최소 13개월 자료가 필요합니다."},
        )
    current_month = growth.index[-1]
    # Stabilize exact threshold comparisons such as -10.0% that can otherwise
    # arrive as -9.999999999999998 from floating-point arithmetic.
    current_yoy = float(round(float(growth.iloc[-1]), 10))
    # "Within 48 months" is a calendar rule, not a count of non-null rows.
    # Select by month boundary so a missing FINRA observation cannot shorten
    # the regime window and the month exactly 48 months ago remains included.
    lookback_months = int(cfg["lookback_months"])
    lookback_start = current_month - pd.DateOffset(months=lookback_months)
    prior = growth.loc[lookback_start:current_month]
    overheat = prior[prior >= float(cfg["overheat_yoy"])]
    lookback_peak_month = prior.idxmax()
    lookback_peak_yoy = float(prior.loc[lookback_peak_month])
    base = {
        "reference_month": current_month.strftime("%Y-%m"),
        "current_yoy": current_yoy,
        "current_balance": float(monthly.iloc[-1]),
        "lookback_months": lookback_months,
        "lookback_start_month": lookback_start.strftime("%Y-%m"),
        "lookback_observations": int(len(prior)),
        "lookback_peak_yoy": lookback_peak_yoy,
        "lookback_peak_month": lookback_peak_month.strftime("%Y-%m"),
        "overheat_yoy": float(cfg["overheat_yoy"]),
        "overheat_in_lookback": not overheat.empty,
    }
    if overheat.empty:
        opportunity_yoy = float(cfg.get("sideways_buy_warning_yoy", -10))
        strong_yoy = float(cfg.get("sideways_buy_strong_yoy", -15))
        base.update(
            {
                "market_regime": "평시/횡보장",
                "opportunity_yoy": opportunity_yoy,
                "strong_yoy": strong_yoy,
                "warning_distance": current_yoy - opportunity_yoy,
                "strong_distance": current_yoy - strong_yoy,
            }
        )
        if current_yoy <= strong_yoy:
            return Assessment(
                "SIDEWAYS_BUY_STRONG_MINUS_15",
                "강한 매수",
                "TRIGGERED",
                base,
            )
        if current_yoy <= opportunity_yoy:
            return Assessment(
                "SIDEWAYS_BUY_OPPORTUNITY_MINUS_10",
                "약한 매수",
                "TRIGGERED",
                base,
            )
        return Assessment("NORMAL", "평시 매수", "WATCH", base)

    opportunity_yoy = float(cfg["buy_warning_yoy"])
    strong_yoy = float(cfg["buy_strong_yoy"])
    base.update(
        {
            "market_regime": "선행 과열 후 디레버리징",
            "opportunity_yoy": opportunity_yoy,
            "strong_yoy": strong_yoy,
            "sell_warning_relative_drop": float(cfg["sell_warning_relative_drop"]),
            "sell_strong_relative_drop": float(
                cfg.get("sell_strong_relative_drop", cfg.get("sell_arm_relative_drop", -15))
            ),
        }
    )

    peak_month = prior.idxmax()
    peak_yoy = float(prior.loc[peak_month])
    cycle_balance = monthly.loc[peak_month:]
    peak_balance = float(cycle_balance.max())
    balance_dd = drawdown(float(monthly.iloc[-1]), peak_balance)
    decline = (
        float(round((current_yoy / peak_yoy - 1) * 100, 10))
        if peak_yoy
        else float("nan")
    )
    flat, flat_detail = flattening_3m(monthly)
    base.update(
        {
            "peak_yoy": peak_yoy,
            "peak_yoy_month": peak_month.strftime("%Y-%m"),
            "peak_balance": peak_balance,
            "balance_drawdown": balance_dd,
            "yoy_relative_drop": decline,
            "flattening": flat,
            "flattening_detail": flat_detail,
            "recent_3m_change": float((monthly.iloc[-1] / monthly.iloc[-3] - 1) * 100)
            if len(monthly) >= 3
            else float("nan"),
        }
    )
    balance_level, balance_message = _balance_drawdown_alert(
        balance_dd,
        cfg.get("balance_drawdown_alert_levels", [-20, -25, -30]),
    )
    base["balance_drawdown_level"] = balance_level
    base["balance_drawdown_message"] = balance_message

    if current_yoy <= float(cfg["buy_strong_yoy"]):
        return Assessment(
            _state_with_balance_level("BUY_STRONG_MINUS_30", balance_level),
            "강한 매수",
            "TRIGGERED",
            base,
        )
    if current_yoy <= float(cfg["buy_warning_yoy"]):
        return Assessment(
            _state_with_balance_level("BUY_OPPORTUNITY_MINUS_25", balance_level),
            "약한 매수",
            "TRIGGERED",
            base,
        )

    after_peak = growth.loc[peak_month:]
    strong_sell_drop = float(
        cfg.get("sell_strong_relative_drop", cfg.get("sell_arm_relative_drop", -15))
    )
    armed = after_peak[((after_peak / peak_yoy - 1) * 100) <= strong_sell_drop]
    if not armed.empty:
        armed_month = armed.index[0]
        armed_yoy = float(armed.iloc[0])
        if armed_yoy > float(cfg["overheat_yoy"]):
            crossing = after_peak.loc[armed_month:]
            crossing = crossing[crossing <= float(cfg["overheat_yoy"])]
            if crossing.empty:
                return Assessment("WAIT_FOR_YOY_BELOW_50", "강한 매도", "TRIGGERED", base)
            reference = crossing.index[0]
            base["sell_reference_kind"] = "+50% 최초 하회월"
        else:
            reference = peak_month
            base["sell_reference_kind"] = "YoY 정점월"
        target = reference + relativedelta(months=3)
        base["sell_reference_month"] = reference.strftime("%Y-%m")
        base["sell_target_month"] = target.strftime("%Y-%m")
        effective_as_of = (
            pd.Timestamp(as_of)
            if as_of is not None
            else pd.Timestamp.now(tz=current_month.tz)
        )
        if current_month.tz is not None and effective_as_of.tzinfo is None:
            effective_as_of = effective_as_of.tz_localize(current_month.tz)
        elif current_month.tz is None and effective_as_of.tzinfo is not None:
            effective_as_of = effective_as_of.tz_localize(None)
        due = (effective_as_of.year, effective_as_of.month) >= (target.year, target.month)
        base["sell_due"] = bool(due)
        if due:
            return Assessment(
                f"SELL_DUE_{target.strftime('%Y_%m')}",
                "강한 매도",
                "TRIGGERED",
                base,
            )
        return Assessment(
            f"SELL_SCHEDULED_{target.strftime('%Y_%m')}",
            "강한 매도",
            "TRIGGERED",
            base,
        )
    if decline <= float(cfg["sell_warning_relative_drop"]):
        return Assessment("SELL_WARNING_DROP_10", "약한 매도", "TRIGGERED", base)
    return Assessment("FINRA_OVERHEAT_50", "평시 매수", "WATCH", base)


def stock_snapshot(
    prices: pd.Series,
    credit_quantity: pd.Series | None = None,
    credit_rate: pd.Series | None = None,
) -> dict[str, Any]:
    prices = prices.dropna().sort_index()
    if prices.empty:
        return {}
    current = float(prices.iloc[-1])
    ma120 = float(prices.tail(120).mean()) if len(prices) >= 120 else float("nan")
    ma200 = float(prices.tail(200).mean()) if len(prices) >= 200 else float("nan")
    result: dict[str, Any] = {
        "price": current,
        "ma120": ma120,
        "ma200": ma200,
        "ma120_gap": gap_percent(current, ma120),
        "ma200_gap": gap_percent(current, ma200),
        "price_date": prices.index[-1].strftime("%Y-%m-%d"),
    }
    if credit_quantity is not None and not credit_quantity.dropna().empty:
        q = credit_quantity.dropna().sort_index()
        window = _window(q, 1826) if isinstance(q.index, pd.DatetimeIndex) else q.tail(1300)
        result.update(
            {
                "credit_quantity": float(q.iloc[-1]),
                "credit_peak": float(window.max()),
                "credit_drawdown": drawdown(float(q.iloc[-1]), float(window.max())),
                "credit_yoy": float((q.iloc[-1] / q.asof(q.index[-1] - pd.DateOffset(years=1)) - 1) * 100)
                if q.index[-1] - q.index[0] >= pd.Timedelta(days=365)
                and pd.notna(q.asof(q.index[-1] - pd.DateOffset(years=1)))
                else float("nan"),
            }
        )
    if credit_rate is not None and not credit_rate.dropna().empty:
        r = credit_rate.dropna().sort_index()
        window = _window(r, 1826) if isinstance(r.index, pd.DatetimeIndex) else r.tail(1300)
        result["credit_rate"] = float(r.iloc[-1])
        result["credit_rate_percentile_5y"] = percentile_rank(window.values, float(r.iloc[-1]))
    return result


def assess_korea(
    balance: pd.Series,
    stocks: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
    stock_cfg: dict[str, Any],
) -> Assessment:
    _annotate_korea_stock_conditions(stocks, stock_cfg)
    monthly = month_end(balance)
    growth = yoy(monthly).dropna()
    if growth.empty:
        return Assessment(
            "INSUFFICIENT_DATA",
            "판정 보류",
            "WATCH",
            {"reason": "한국 전체 신용잔고 YoY 계산에는 최소 13개월 자료가 필요합니다.", "stocks": stocks},
        )
    current_month = growth.index[-1]
    current_yoy = float(growth.iloc[-1])
    overheat = growth[growth >= float(cfg["overheat_yoy"])]
    base: dict[str, Any] = {
        "reference_month": current_month.strftime("%Y-%m"),
        "current_yoy": current_yoy,
        "current_balance": float(monthly.iloc[-1]),
        "previous_month_change": float((monthly.iloc[-1] / monthly.iloc[-2] - 1) * 100)
        if len(monthly) >= 2
        else float("nan"),
        "stocks": stocks,
    }
    if overheat.empty:
        return Assessment("NORMAL", "평시 매수", "WATCH", base)

    peak_month = growth.idxmax()
    peak_yoy = float(growth.loc[peak_month])
    since_peak = monthly.loc[peak_month:]
    peak_balance = float(since_peak.max())
    balance_dd = drawdown(float(monthly.iloc[-1]), peak_balance)
    relative_yoy_drop = (current_yoy / peak_yoy - 1) * 100
    base.update(
        {
            "peak_yoy": peak_yoy,
            "peak_yoy_month": peak_month.strftime("%Y-%m"),
            "peak_balance": peak_balance,
            "balance_drawdown": balance_dd,
            "yoy_relative_drop": relative_yoy_drop,
        }
    )

    if current_yoy <= float(cfg["buy_strong_yoy"]):
        market_ready = balance_dd <= float(cfg["balance_drawdown_min"])
        ready = []
        for symbol, snap in stocks.items():
            price_ok = bool(snap.get("buy_price_ok"))
            credit_ok = bool(snap.get("buy_credit_ok"))
            if market_ready and price_ok and credit_ok:
                ready.append(symbol)
        base["ready_symbols"] = ready
        if ready:
            state = "SAMSUNG_BUY_READY" if ready == ["005930"] else (
                "HYNIX_BUY_READY" if ready == ["000660"] else "BUY_READY"
            )
            return Assessment(state, "강한 매수", "TRIGGERED", base)
        return Assessment("BUY_STRONG_MINUS_30", "강한 매수", "READY", base)
    if current_yoy <= float(cfg["buy_warning_yoy"]):
        return Assessment("BUY_WARNING_MINUS_25", "약한 매수", "TRIGGERED", base)

    # 매도: 정점 이후 최초 잔고 감소월과 YoY 상대하락 15%를 모두 확인한다.
    after_peak_bal = monthly.loc[peak_month:]
    first_decrease = after_peak_bal.pct_change(fill_method=None)
    first_decrease = first_decrease[first_decrease < 0]
    prior_window = monthly.loc[current_month - pd.DateOffset(months=int(cfg["prior_crash_lookback_months"])) : current_month]
    rolling_peak = prior_window.cummax()
    prior_crash = ((prior_window / rolling_peak - 1) * 100 <= float(cfg["prior_crash_cutoff"])).any()
    market_sell = (
        relative_yoy_drop <= float(cfg["sell_relative_drop"])
        and not first_decrease.empty
        and not bool(prior_crash)
    )
    base["first_balance_decrease_month"] = (
        first_decrease.index[0].strftime("%Y-%m") if not first_decrease.empty else None
    )
    base["prior_18m_crash"] = bool(prior_crash)

    stock_overheat = []
    for symbol, snap in stocks.items():
        warning = bool(snap.get("sell_warning"))
        credit_hot = bool(snap.get("credit_top_10pct"))
        if warning or credit_hot:
            stock_overheat.append(symbol)
    base["overheat_symbols"] = stock_overheat
    base["stock_sell_condition_met"] = bool(stock_overheat)
    if market_sell and stock_overheat:
        return Assessment("SELL_READY", "강한 매도", "TRIGGERED", base)
    if market_sell:
        return Assessment(
            "MARKET_SELL_READY_STOCK_WAIT",
            "약한 매도",
            "READY",
            base,
        )
    if "005930" in stock_overheat:
        return Assessment("SAMSUNG_OVERHEAT", "약한 매도", "WATCH", base)
    if "000660" in stock_overheat:
        return Assessment("HYNIX_OVERHEAT", "약한 매도", "WATCH", base)
    return Assessment("CREDIT_OVERHEAT_60", "평시 매수", "WATCH", base)


def _annotate_korea_stock_conditions(
    stocks: dict[str, dict[str, Any]], stock_cfg: dict[str, Any]
) -> None:
    """Attach every stock-level buy/sell threshold before any market return."""

    for symbol, snap in stocks.items():
        spec = stock_cfg[symbol]
        buy_ma200 = float(spec["buy_ma200_gap"])
        buy_credit_dd = float(spec["buy_credit_drawdown"])
        sell_warning = float(spec["sell_ma120_warning"])
        sell_strong = float(spec["sell_ma120_strong"])
        sell_credit_pct = float(spec.get("sell_credit_percentile", 90))

        snap.update(
            {
                "buy_ma200_threshold": buy_ma200,
                "buy_price_ok": bool(snap.get("ma200_gap", np.inf) <= buy_ma200),
                "buy_credit_drawdown_threshold": buy_credit_dd,
                "buy_credit_drawdown_ok": bool(
                    snap.get("credit_drawdown", np.inf) <= buy_credit_dd
                ),
                "buy_credit_percentile_threshold": 20.0,
                "buy_credit_percentile_ok": bool(
                    snap.get("credit_rate_percentile_5y", np.inf) <= 20.0
                ),
                "sell_ma120_warning_threshold": sell_warning,
                "sell_ma120_strong_threshold": sell_strong,
                "sell_warning": bool(
                    snap.get("ma120_gap", -np.inf) >= sell_warning
                ),
                "sell_strong": bool(
                    snap.get("ma120_gap", -np.inf) >= sell_strong
                ),
                "sell_credit_percentile_threshold": sell_credit_pct,
                "credit_top_10pct": bool(
                    snap.get("credit_rate_percentile_5y", -np.inf)
                    >= sell_credit_pct
                ),
            }
        )
        snap["buy_credit_ok"] = bool(
            snap["buy_credit_drawdown_ok"] or snap["buy_credit_percentile_ok"]
        )


def assess_crypto(
    symbol: str,
    price: pd.Series,
    oi: pd.Series,
    funding: pd.Series,
    long_short: pd.Series,
    basis: pd.Series,
    cfg: dict[str, Any],
    btc_price: pd.Series | None = None,
    mark_price: pd.Series | None = None,
    index_price: pd.Series | None = None,
    basis_percent: pd.Series | None = None,
) -> Assessment:
    spec = cfg["symbols"][symbol]
    price = price.dropna().sort_index()
    oi = oi.dropna().sort_index()
    funding = funding.dropna().sort_index()
    if price.empty or oi.empty or funding.empty:
        return Assessment("INSUFFICIENT_DATA", "판정 보류", "WATCH", {"symbol": symbol})
    current_time = max(price.index[-1], oi.index[-1])
    high_date = price.idxmax()
    high_price = float(price.max())
    current_price = float(price.iloc[-1])
    price_dd = drawdown(current_price, high_price)
    elapsed = months_between(high_date, current_time)
    oi_peak = float(oi.max())
    current_oi = float(oi.iloc[-1])
    oi_dd = drawdown(current_oi, oi_peak)
    funding_7d = float(_window(funding, 7).mean() * 100)
    funding_30d = float(_window(funding, 30).mean() * 100)
    oi_7d = _return(_window(oi, 7))
    price_7d = _return(_window(price, 7))
    oi_21d = _return(_window(oi, 21))
    oi_slow = abs(oi_21d) <= 2
    liquidation_proxy = oi_7d <= -20 and price_7d <= -10
    oi_price_ratio = current_oi / current_price
    aligned_price = price.reindex(oi.index, method="ffill")
    ratio_history = (oi / aligned_price).dropna()
    ratio_percentile = percentile_rank(ratio_history.values, oi_price_ratio)

    conditions: list[dict[str, Any]]
    if symbol == "BTCUSDT":
        conditions = [
            _condition("OI 고점 대비 하락", oi_dd <= spec["buy_oi_drawdown"], oi_dd, spec["buy_oi_drawdown"], "≤"),
            _condition("강한 OI 청산", oi_dd <= spec["buy_oi_strong_drawdown"], oi_dd, spec["buy_oi_strong_drawdown"], "≤"),
            _condition("7일 평균 펀딩비", funding_7d <= 0, funding_7d, 0, "≤"),
            _condition("30일 평균 펀딩비", funding_30d <= 0, funding_30d, 0, "≤"),
            _condition("가격·OI 동시 급락(청산 프록시)", liquidation_proxy, min(price_7d, oi_7d), -10, "가격≤-10/OI≤-20"),
            _condition("OI 21일 변화 횡보", oi_slow, oi_21d, 2, "|값|≤"),
        ]
    else:
        solbtc_slow = False
        solbtc_now = float("nan")
        if btc_price is not None and not btc_price.empty:
            joined = pd.concat(
                [price.rename("sol"), btc_price.rename("btc")], axis=1
            ).dropna()
            ratio = joined["sol"] / joined["btc"]
            ratio.index = pd.DatetimeIndex(pd.to_datetime(ratio.index, utc=True))
            solbtc_now = _return(_window(ratio, 30))
            ratio_end = pd.Timestamp(ratio.index[-1])
            prior = ratio.loc[
                ratio_end - timedelta(days=60) : ratio_end - timedelta(days=30)
            ]
            prior_return = _return(prior)
            solbtc_slow = solbtc_now > prior_return
        conditions = [
            _condition("SOL OI 고점 대비 하락", oi_dd <= spec["buy_oi_drawdown"], oi_dd, spec["buy_oi_drawdown"], "≤"),
            _condition("OI/가격 비율 하위 20%", ratio_percentile <= 20, ratio_percentile, 20, "≤"),
            _condition("7일 평균 펀딩비 음수", funding_7d < 0, funding_7d, 0, "<"),
            _condition("대규모 롱 청산(프록시)", liquidation_proxy, min(price_7d, oi_7d), -10, "가격≤-10/OI≤-20"),
            _condition("SOL/BTC 하락세 둔화", solbtc_slow, solbtc_now, 0, "직전30일보다 개선"),
            _condition("OI 21일 변화 횡보", oi_slow, oi_21d, 2, "|값|≤"),
        ]
    count = sum(c["met"] for c in conditions)
    min_count = int(cfg["derivative_min_count"])
    time_ok = spec["buy_months_min"] <= elapsed <= spec["buy_months_max"]
    opportunity_price_threshold = float(
        spec.get("buy_opportunity_price_drawdown", spec["buy_price_drawdown"])
    )
    strong_price_threshold = float(spec["buy_price_drawdown"])
    opportunity_price_ok = price_dd <= opportunity_price_threshold
    strong_price_ok = price_dd <= strong_price_threshold

    oi_percentile = percentile_rank(oi.values, current_oi)
    ls_current = float(long_short.dropna().iloc[-1]) if not long_short.dropna().empty else float("nan")
    basis_current = float(basis.dropna().iloc[-1]) if not basis.dropna().empty else float("nan")
    mark_current = (
        float(mark_price.dropna().iloc[-1])
        if mark_price is not None and not mark_price.dropna().empty
        else float("nan")
    )
    index_current = (
        float(index_price.dropna().iloc[-1])
        if index_price is not None and not index_price.dropna().empty
        else float("nan")
    )
    raw_basis_current = (
        float(basis_percent.dropna().iloc[-1])
        if basis_percent is not None and not basis_percent.dropna().empty
        else float("nan")
    )
    # 매수 기준점(가격의 현재 역대고점)과 매도 사이클 기준점(직전 확정
    # 대형고점)을 분리한다. 신고가가 생겨도 매도 경과개월은 초기화되지 않는다.
    cycle_ref = _configured_time(spec["sell_cycle_high_date"], price.index)
    cycle_price = price.loc[price.index >= cycle_ref]
    if cycle_price.empty:
        cycle_price = price
    cycle_high = float(cycle_price.max())
    cycle_low = float(cycle_price.min())
    cycle_high_date = cycle_price.idxmax()
    cycle_high_dd = drawdown(current_price, cycle_high)
    cycle_gain_from_low = (current_price / cycle_low) if cycle_low else float("nan")
    cycle_peak_gain_from_low = (cycle_high / cycle_low) if cycle_low else float("nan")
    sell_elapsed = months_between(cycle_ref, current_time)

    observe_month = float(cfg.get("sell_observe_month", 42))
    watch_month = float(cfg.get("sell_watch_month", 45))
    core_end = float(cfg.get("sell_core_end_month", 51))
    btc_ref = _configured_time(
        cfg["symbols"]["BTCUSDT"]["sell_cycle_high_date"], price.index
    )
    btc_elapsed = months_between(btc_ref, current_time)
    if symbol == "BTCUSDT":
        sell_time_active = sell_elapsed >= watch_month
        if sell_elapsed < observe_month:
            sell_time_stage = "대기"
        elif sell_elapsed < watch_month:
            sell_time_stage = "예비 관찰"
        elif sell_elapsed <= core_end:
            sell_time_stage = "핵심 고점창"
        else:
            sell_time_stage = "사이클 지연·계속 감시"
    else:
        # SOL은 BTC와 SOL 자체 기준이 모두 42개월 이상이어야 감시한다.
        sell_time_active = sell_elapsed >= observe_month and btc_elapsed >= observe_month
        if not sell_time_active:
            sell_time_stage = "대기"
        elif sell_elapsed <= core_end and btc_elapsed <= core_end:
            sell_time_stage = "핵심 고점창"
        else:
            sell_time_stage = "사이클 지연·계속 감시"

    price_lookback = int(cfg.get("sell_price_lookback_days", 1095))
    recent_hot_days = int(cfg.get("sell_recent_hot_days", 30))
    ma120 = price.rolling(120, min_periods=120).mean()
    ma120_gap = ((price / ma120) - 1) * 100
    ma120_history = _window(ma120_gap, price_lookback).dropna()
    ma120_current = float(ma120_gap.dropna().iloc[-1]) if not ma120_gap.dropna().empty else float("nan")
    ma120_recent_max = _series_max(_window(ma120_gap, recent_hot_days))
    ma120_current_percentile = percentile_rank(ma120_history.values, ma120_current)
    ma120_recent_percentile = percentile_rank(ma120_history.values, ma120_recent_max)
    price_pct_threshold = float(cfg.get("sell_price_ma120_percentile", 90))
    ma120_hot = ma120_current_percentile >= price_pct_threshold
    if symbol == "BTCUSDT":
        sell_price_ok = ma120_hot
    else:
        sell_price_ok = cycle_gain_from_low >= 10 or ma120_hot

    hot_cfg = cfg["overheat"]
    hot_days = int(cfg.get("sell_hot_lookback_days", 1095))
    min_hot_history = int(cfg.get("sell_min_derivative_history_days", 180))
    reversal_days = int(cfg.get("sell_reversal_lookback_days", 180))
    oi_history = _window(oi, hot_days)
    oi_recent_max = _series_max(_window(oi, recent_hot_days))
    oi_hot_percentile = percentile_rank(oi_history.values, oi_recent_max)
    oi_hot = (
        _span_days(oi_history) >= min_hot_history
        and oi_hot_percentile >= float(hot_cfg["oi_percentile"])
    )

    oi_180_window = _window(oi, 180)
    oi_180_growth = (
        _return(oi_180_window)
        if _span_days(oi_180_window) >= 150
        else float("nan")
    )
    oi_growth_hot = oi_180_growth >= float(hot_cfg["oi_180d_growth_percent"])

    funding_7_series = funding.rolling("7D", min_periods=1).mean() * 100
    funding_30_series = funding.rolling("30D", min_periods=1).mean() * 100
    f7_history = _window(funding_7_series, hot_days)
    f30_history = _window(funding_30_series, hot_days)
    f7_recent_max = _series_max(_window(funding_7_series, recent_hot_days))
    f30_recent_max = _series_max(_window(funding_30_series, recent_hot_days))
    f7_hot_percentile = percentile_rank(f7_history.values, f7_recent_max)
    f30_hot_percentile = percentile_rank(f30_history.values, f30_recent_max)
    funding_hot_percentile = max(f7_hot_percentile, f30_hot_percentile)
    funding_hot = (
        _span_days(funding) >= min_hot_history
        and funding_hot_percentile >= float(hot_cfg["funding_percentile"])
    )

    basis_history = _window(basis, hot_days)
    basis_recent_max = _series_max(_window(basis, recent_hot_days))
    basis_hot_percentile = percentile_rank(basis_history.values, basis_recent_max)
    basis_hot = (
        _span_days(basis) >= min_hot_history
        and basis_hot_percentile >= float(hot_cfg["basis_percentile"])
    )

    if symbol == "BTCUSDT":
        # 사용자가 열거한 BTC 과열항목은 OI·펀딩·베이시스의 3개다.
        overheat_conditions = [
            _condition("OI 최근 3년 상위 10%", oi_hot, oi_hot_percentile, hot_cfg["oi_percentile"], "≥"),
            _condition("펀딩비 최근 3년 상위 10%", funding_hot, funding_hot_percentile, hot_cfg["funding_percentile"], "≥"),
            _condition("선물 베이시스 최근 3년 상위 10%", basis_hot, basis_hot_percentile, hot_cfg["basis_percentile"], "≥"),
        ]
    else:
        overheat_conditions = [
            _condition("SOL OI 최근 3년 상위 10%", oi_hot, oi_hot_percentile, hot_cfg["oi_percentile"], "≥"),
            _condition("SOL OI 180일 증가율 +100%", oi_growth_hot, oi_180_growth, hot_cfg["oi_180d_growth_percent"], "≥"),
            _condition("7일·30일 펀딩비 상위 10%", funding_hot, funding_hot_percentile, hot_cfg["funding_percentile"], "≥"),
            _condition("SOL 선물 베이시스 상위 10%", basis_hot, basis_hot_percentile, hot_cfg["basis_percentile"], "≥"),
        ]
    hot_count = sum(c["met"] for c in overheat_conditions)

    reversal_cfg = cfg["reversal"]
    recent_oi_peak = _series_max(_window(oi, reversal_days))
    oi_reversal_dd = drawdown(current_oi, recent_oi_peak)
    oi_reversal_limit = float(
        reversal_cfg["btc_oi_drawdown"]
        if symbol == "BTCUSDT"
        else reversal_cfg["sol_oi_drawdown"]
    )
    near_high = cycle_high_dd >= float(reversal_cfg["near_high_drawdown"])
    oi_reversal = oi_reversal_dd <= oi_reversal_limit and near_high

    funding_peak = _series_max(_window(funding_7_series, reversal_days))
    funding_reversal_dd = drawdown(funding_7d, funding_peak)
    funding_reversal = (
        funding_peak > 0
        and funding_reversal_dd <= float(reversal_cfg["funding_drawdown"])
        and near_high
    )
    basis_peak = _series_max(_window(basis, reversal_days))
    basis_reversal_dd = drawdown(basis_current, basis_peak)
    basis_reversal = (
        basis_peak > 0
        and basis_reversal_dd <= float(reversal_cfg["basis_drawdown"])
        and near_high
    )
    oi_declining = current_oi < recent_oi_peak
    funding_declining = funding_7d < funding_peak
    price_reversal_limit = float(
        reversal_cfg["btc_price_drawdown"]
        if symbol == "BTCUSDT"
        else reversal_cfg["sol_price_drawdown"]
    )
    price_reversal = cycle_high_dd <= price_reversal_limit
    if symbol == "BTCUSDT":
        price_reversal = price_reversal and (oi_declining or funding_declining)

    solbtc_drawdown = float("nan")
    solbtc_reversal = False
    if symbol == "SOLUSDT" and btc_price is not None and not btc_price.empty:
        joined = pd.concat([price.rename("sol"), btc_price.rename("btc")], axis=1).dropna()
        joined = joined.loc[joined.index >= cycle_ref]
        if not joined.empty:
            solbtc = joined["sol"] / joined["btc"]
            solbtc_drawdown = drawdown(float(solbtc.iloc[-1]), float(solbtc.max()))
            solbtc_reversal = solbtc_drawdown <= float(reversal_cfg["solbtc_drawdown"])

    if symbol == "BTCUSDT":
        reversal_conditions = [
            _condition("OI 최고치 대비 하락", oi_reversal, oi_reversal_dd, oi_reversal_limit, "≤"),
            _condition("7일 펀딩비 최고치 대비 하락", funding_reversal, funding_reversal_dd, reversal_cfg["funding_drawdown"], "≤"),
            _condition("베이시스 최고치 대비 축소", basis_reversal, basis_reversal_dd, reversal_cfg["basis_drawdown"], "≤"),
            _condition("신고가 대비 가격 하락", price_reversal, cycle_high_dd, price_reversal_limit, "≤"),
        ]
    else:
        reversal_conditions = [
            _condition("OI 최고치 대비 하락", oi_reversal_dd <= oi_reversal_limit, oi_reversal_dd, oi_reversal_limit, "≤"),
            _condition("7일 펀딩비 최고치 대비 하락", funding_peak > 0 and funding_reversal_dd <= float(reversal_cfg["funding_drawdown"]), funding_reversal_dd, reversal_cfg["funding_drawdown"], "≤"),
            _condition("SOL/BTC 고점 대비 하락", solbtc_reversal, solbtc_drawdown, reversal_cfg["solbtc_drawdown"], "≤"),
            _condition("SOL 신고가 대비 하락", price_reversal, cycle_high_dd, price_reversal_limit, "≤"),
        ]
    reversal_count = sum(c["met"] for c in reversal_conditions)
    sell_ready = sell_time_active and sell_price_ok and hot_count >= min_count
    sell_trigger = sell_ready and reversal_count >= 1

    payload = {
        "symbol": symbol,
        "reference_time": current_time.isoformat(),
        "price": current_price,
        "high_price": high_price,
        "high_date": high_date.strftime("%Y-%m-%d"),
        "months_since_high": elapsed,
        "price_drawdown": price_dd,
        "oi": current_oi,
        "oi_peak": oi_peak,
        "oi_drawdown": oi_dd,
        "funding_7d_percent": funding_7d,
        "funding_30d_percent": funding_30d,
        "long_short_ratio": ls_current,
        "mark_price": mark_current,
        "index_price": index_current,
        "basis_percent": raw_basis_current,
        "basis_annualized_percent": basis_current,
        "derivative_conditions": conditions,
        "derivative_met": int(count),
        "derivative_total": len(conditions),
        "derivative_required": min_count,
        "derivative_excess": int(count - min_count),
        "time_ok": bool(time_ok),
        "opportunity_price_threshold": opportunity_price_threshold,
        "strong_price_threshold": strong_price_threshold,
        "opportunity_price_ok": bool(opportunity_price_ok),
        "strong_price_ok": bool(strong_price_ok),
        "price_ok": bool(strong_price_ok),
        "overheat_conditions": overheat_conditions,
        "overheat_met": int(hot_count),
        "overheat_total": len(overheat_conditions),
        "overheat_required": min_count,
        "overheat_excess": max(0, int(hot_count - min_count)),
        "sell_cycle_high_date": cycle_ref.strftime("%Y-%m-%d"),
        "sell_cycle_months": sell_elapsed,
        "btc_cycle_months": btc_elapsed,
        "sell_time_stage": sell_time_stage,
        "sell_time_active": bool(sell_time_active),
        "sell_price_ok": bool(sell_price_ok),
        "ma120_gap_percent": ma120_current,
        "ma120_gap_percentile": ma120_current_percentile,
        "ma120_gap_recent_percentile": ma120_recent_percentile,
        "cycle_high_price": cycle_high,
        "cycle_high_date": cycle_high_date.strftime("%Y-%m-%d"),
        "cycle_high_drawdown": cycle_high_dd,
        "cycle_gain_from_low": cycle_gain_from_low,
        "cycle_peak_gain_from_low": cycle_peak_gain_from_low,
        "oi_180d_growth_percent": oi_180_growth,
        "oi_history_days": _span_days(oi_history),
        "funding_history_days": _span_days(funding),
        "basis_history_days": _span_days(basis),
        "reversal_conditions": reversal_conditions,
        "reversal_met": int(reversal_count),
        "oi_reversal_drawdown": oi_reversal_dd,
        "funding_reversal_drawdown": funding_reversal_dd,
        "basis_reversal_drawdown": basis_reversal_dd,
        "solbtc_drawdown": solbtc_drawdown,
        "sell_window": bool(sell_time_active),
        "near_high": bool(near_high),
        "oi_reversal": bool(oi_reversal),
        "funding_reversal": bool(funding_reversal),
        "basis_reversal": bool(basis_reversal),
        "price_reversal": bool(price_reversal),
    }
    if sell_trigger:
        return Assessment("SELL_TRIGGERED", "강한 매도", "TRIGGERED", payload)
    if sell_ready:
        return Assessment("LEVERAGE_OVERHEAT", "약한 매도", "READY", payload)
    if time_ok and strong_price_ok and count >= min_count:
        return Assessment("STRONG_BUY_TRIGGERED", "강한 매수", "TRIGGERED", payload)
    if time_ok and strong_price_ok:
        return Assessment("STRONG_BUY_DERIVATIVES_WAIT", "약한 매수", "READY", payload)
    if time_ok and opportunity_price_ok:
        return Assessment("BUY_OPPORTUNITY_TRIGGERED", "약한 매수", "TRIGGERED", payload)
    if opportunity_price_ok:
        return Assessment("OPPORTUNITY_PRICE_WATCH", "약한 매수", "WATCH", payload)
    if time_ok:
        return Assessment("TIME_WINDOW_WATCH", "평시 매수", "WATCH", payload)
    return Assessment("NORMAL", "평시 매수", "WATCH", payload)


def _return(series: pd.Series) -> float:
    series = series.dropna()
    if len(series) < 2 or series.iloc[0] == 0:
        return float("nan")
    return float((series.iloc[-1] / series.iloc[0] - 1) * 100)


def _window(series: pd.Series, days: int) -> pd.Series:
    if series.empty:
        return series
    return series.loc[series.index[-1] - timedelta(days=days) :]


def _configured_time(value: str, index: pd.DatetimeIndex) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if index.tz is not None:
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(index.tz)
        else:
            stamp = stamp.tz_convert(index.tz)
    elif stamp.tzinfo is not None:
        stamp = stamp.tz_localize(None)
    return stamp


def _series_max(series: pd.Series) -> float:
    clean = series.dropna()
    return float(clean.max()) if not clean.empty else float("nan")


def _span_days(series: pd.Series) -> float:
    clean = series.dropna()
    if len(clean) < 2:
        return 0.0
    return float((clean.index[-1] - clean.index[0]).total_seconds() / 86_400)


def _condition(
    name: str, met: bool, actual: float, threshold: float, operator: str
) -> dict[str, Any]:
    return {
        "name": name,
        "met": bool(met),
        "actual": float(actual) if pd.notna(actual) else float("nan"),
        "threshold": float(threshold),
        "operator": operator,
        "gap": float(actual - threshold) if pd.notna(actual) else float("nan"),
    }


def _balance_drawdown_alert(
    balance_drawdown: float,
    levels: list[float] | tuple[float, ...],
) -> tuple[str | None, str | None]:
    thresholds = sorted((float(level) for level in levels), reverse=True)
    crossed = [level for level in thresholds if balance_drawdown <= level]
    if not crossed:
        return None, None
    deepest = min(crossed)
    label = f"DD{abs(int(deepest))}"
    messages = {
        -20.0: "절대잔고가 고점 대비 -20%를 넘어 초기 디레버리징 구간입니다.",
        -25.0: "절대잔고가 고점 대비 -25%를 넘어 매수 보조조건이 강해졌습니다.",
        -30.0: "절대잔고가 고점 대비 -30%를 넘어 극단적 디레버리징 구간입니다.",
    }
    return label, messages.get(
        deepest,
        f"절대잔고가 고점 대비 {deepest:.0f}% 기준을 넘었습니다.",
    )


def _state_with_balance_level(base_state: str, level: str | None) -> str:
    return f"{base_state}_{level}" if level else base_state
