from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from market_alarm.metrics import fmt_num, fmt_pct
from market_alarm.rules import Assessment


def render_finra(a: Assessment, stocks: dict[str, dict[str, Any]] | None = None) -> str:
    p = a.payload
    lines = _header("미국주식", a, p.get("reference_month", "N/A"))
    if "reason" in p:
        return "\n".join(lines + [p["reason"]])
    lines += [
        f"시장 국면: {p.get('market_regime', 'N/A')}",
        f"최근 {p.get('lookback_months', 'N/A')}개월 +{p.get('overheat_yoy', 50):g}% 달성: "
        f"{'있음' if p.get('overheat_in_lookback') else '없음'}",
        f"48개월 내 최고 YoY: {fmt_pct(p.get('lookback_peak_yoy'))} "
        f"({p.get('lookback_peak_month', 'N/A')})",
        f"현재 FINRA YoY: {fmt_pct(p.get('current_yoy'))}",
        f"약한 매수 기준 {fmt_pct(p.get('opportunity_yoy'))}: "
        f"{'달성' if _at_or_below(p.get('current_yoy'), p.get('opportunity_yoy')) else '미달성'}",
        f"강한 매수 기준 {fmt_pct(p.get('strong_yoy'))}: "
        f"{'달성' if _at_or_below(p.get('current_yoy'), p.get('strong_yoy')) else '미달성'}",
    ]
    if p.get("overheat_in_lookback"):
        lines += [
            f"선행 최고 YoY: {fmt_pct(p.get('peak_yoy'))} ({p.get('peak_yoy_month', 'N/A')})",
            f"고점 대비 절대잔고: {fmt_pct(p.get('balance_drawdown'))}",
            f"YoY 정점 대비 상대하락: {fmt_pct(p.get('yoy_relative_drop'))}",
            f"약한 매도 기준 {fmt_pct(p.get('sell_warning_relative_drop'))}: "
            f"{'달성' if _at_or_below(p.get('yoy_relative_drop'), p.get('sell_warning_relative_drop')) else '미달성'}",
            f"강한 매도 기준 {fmt_pct(p.get('sell_strong_relative_drop'))}: "
            f"{'달성' if _at_or_below(p.get('yoy_relative_drop'), p.get('sell_strong_relative_drop')) else '미달성'}",
            f"3개월 하락 둔화: {'달성' if p.get('flattening') else '미달성'}",
        ]
    if p.get("balance_drawdown_message"):
        lines.append(f"절대잔고 추가경고: {p['balance_drawdown_message']}")
    if p.get("sell_target_month"):
        lines += [
            f"매도 기준: {p.get('sell_reference_kind')} {p.get('sell_reference_month')}",
            f"매도 목표월: {p.get('sell_target_month')}",
            f"목표월 도달: {'예·실행 알람' if p.get('sell_due') else '아니오·예약만 안내'}",
        ]
    if stocks:
        lines.append("")
        lines.append("[종목별 이동평균 보조조건]")
        for snap in stocks.values():
            lines += [
                "",
                f"[{snap['name']}]",
                f"현재가: {fmt_num(snap.get('price'), 2)}",
                f"120/200 이격률: {fmt_pct(snap.get('ma120_gap'))} / {fmt_pct(snap.get('ma200_gap'))}",
                "[매수 보조조건]",
                f"200일선 하방 이격률 {fmt_pct(snap.get('buy_ma200_threshold'))}: "
                f"{'달성' if snap.get('buy_price_ok') else '미달성'}",
                "[매도 보조조건]",
                f"120일선 상방 이격률 {fmt_pct(snap.get('sell_ma120_warning_threshold'))}: "
                f"{'달성' if snap.get('sell_warning') else '미달성'}",
                f"120일선 상방 이격률 {fmt_pct(snap.get('sell_ma120_strong_threshold'))}: "
                f"{'달성' if snap.get('sell_strong') else '미달성'}",
            ]
    return "\n".join(lines)


def render_korea(a: Assessment) -> str:
    p = a.payload
    lines = _header("한국주식", a, p.get("reference_month", "N/A"))
    if "reason" in p:
        lines.append(p["reason"])
    else:
        overheat_yoy = p.get("overheat_yoy_threshold", 60)
        buy_yoy = p.get("buy_yoy_threshold", -25)
        crash_cutoff = p.get("prior_crash_cutoff", -45)
        crash_months = p.get("prior_crash_lookback_months", 18)
        sell_drop = p.get("sell_relative_drop_threshold", -15)
        balance_limit = p.get("balance_drawdown_threshold", -30)
        first_month = p.get("first_balance_decrease_month")
        lines += [
            f"전체 신용잔고 YoY: {fmt_pct(p.get('current_yoy'))}",
            f"직전 전고 YoY: {fmt_pct(p.get('peak_yoy'))} ({p.get('peak_yoy_month', 'N/A')}, "
            f"최근 {p.get('lookback_months', 48)}개월 내)",
            "",
            f"[매수조건] 전고점 YoY {fmt_pct(overheat_yoy)} 이상 + 신용공여 잔고추이 "
            f"YoY {fmt_pct(buy_yoy)} 이하: {_ok(p.get('market_buy_yoy_ok'))}",
            f" · 전고점 YoY {fmt_pct(overheat_yoy)} 이상 ({fmt_pct(p.get('peak_yoy'))}): "
            f"{_ok(p.get('peak_yoy_overheat_ok'))}",
            f" · 잔고추이 YoY {fmt_pct(buy_yoy)} 이하 ({fmt_pct(p.get('current_yoy'))}): "
            f"{_ok(p.get('buy_yoy_ok'))}",
            "",
            f"[매도조건] {crash_months}개월 내 {fmt_pct(crash_cutoff)} 이상 붕괴 없음 + "
            f"전고점 YoY {fmt_pct(overheat_yoy)} 이상 + 고점 이후 낙폭 {fmt_pct(sell_drop)} + "
            f"신용공여 절댓값 최초 감소전환: {_ok(p.get('market_sell_all_ok'))}",
            f" · {crash_months}개월 내 {fmt_pct(crash_cutoff)} 이상 붕괴 없음: "
            f"{_ok(p.get('no_prior_crash_ok'))}",
            f" · 전고점 YoY {fmt_pct(overheat_yoy)} 이상 ({fmt_pct(p.get('peak_yoy'))}): "
            f"{_ok(p.get('peak_yoy_overheat_ok'))}",
            f" · 고점 이후 낙폭 {fmt_pct(sell_drop)} ({fmt_pct(p.get('yoy_relative_drop'))}): "
            f"{_ok(p.get('sell_relative_drop_ok'))}",
            f" · 신용공여 절댓값 전월 대비 최초 감소전환"
            f"{f' ({first_month})' if first_month else ''}: {_ok(p.get('first_decrease_ok'))}",
            "",
            f"고점 대비 잔고: {fmt_pct(p.get('balance_drawdown'))}",
            f"[매수조건] 신용공여 잔고 절댓값 전고점 대비 {fmt_pct(balance_limit)} 이하: "
            f"{_ok(p.get('market_buy_balance_ok'))}",
            f"YoY 정점 대비 상대하락: {fmt_pct(p.get('yoy_relative_drop'))}",
        ]
    for symbol, snap in p.get("stocks", {}).items():
        lines += [
            "",
            f"[{snap.get('name', symbol)}]",
            f"현재가: {fmt_num(snap.get('price'))}",
            f"120/200 이격률: {fmt_pct(snap.get('ma120_gap'))} / {fmt_pct(snap.get('ma200_gap'))}",
            f"개별 신용잔고 고점 대비: {fmt_pct(snap.get('credit_drawdown'))}",
            f"신용잔고율 5년 분위: {fmt_pct(snap.get('credit_rate_percentile_5y'))}",
            "",
            "[매수 보조조건]",
            f"200일선 하방 이격률 {fmt_pct(snap.get('buy_ma200_threshold'))}: "
            f"{'달성' if snap.get('buy_price_ok') else '미달성'}",
            f"개별 신용잔고 고점 대비 {fmt_pct(snap.get('buy_credit_drawdown_threshold'))}: "
            f"{'달성' if snap.get('buy_credit_drawdown_ok') else '미달성'} OR "
            f"신용잔고율 최근 5년 하위 20%: "
            f"{'달성' if snap.get('buy_credit_percentile_ok') else '미달성'}",
            "",
            "[매도 보조조건]",
            f"120일선 상방 이격률 {fmt_pct(snap.get('sell_ma120_warning_threshold'))}: "
            f"{'달성' if snap.get('sell_warning') else '미달성'}",
            f"120일선 상방 이격률 {fmt_pct(snap.get('sell_ma120_strong_threshold'))}: "
            f"{'달성' if snap.get('sell_strong') else '미달성'}",
            f"신용잔고율 최근 5년 상위 10%: "
            f"{'달성' if snap.get('credit_top_10pct') else '미달성'}",
        ]
    return "\n".join(lines)


def render_crypto(a: Assessment) -> str:
    p = a.payload
    symbol = p.get("symbol", "")
    name = symbol.replace("USDT", "") or "코인"
    lines = _header(name, a, p.get("reference_time", "N/A"))
    if a.state == "INSUFFICIENT_DATA":
        return "\n".join(lines + ["가격·OI·펀딩비 자료가 충분하지 않습니다."])

    buy_min = p.get("buy_months_min", float("nan"))
    buy_max = p.get("buy_months_max", float("nan"))
    sell_min = p.get("sell_time_window_min", float("nan"))
    sell_max = p.get("sell_time_window_max", float("nan"))
    elapsed = p.get("months_since_high", float("nan"))
    sell_elapsed = p.get("sell_cycle_months", float("nan"))
    weak_price = p.get("opportunity_price_threshold")
    strong_price = p.get("strong_price_threshold")
    required = int(p.get("derivative_required", 0) or 0)

    lines += [
        f"현재 가격: {fmt_num(p.get('price'), 2)}",
        f"고점일·경과: {p.get('high_date')} / {_months(elapsed)}",
        f"[매수조건] 전고점 후 {_span(buy_min, buy_max)} 경과 ({_months(elapsed)}): "
        f"{_ok(p.get('time_ok'))}",
        f"[매도조건] 전고점 후 {_span(sell_min, sell_max)} 경과 ({_months(sell_elapsed)}): "
        f"{_ok(p.get('sell_time_window_ok'))}",
        f"고점 대비 가격: {fmt_pct(p.get('price_drawdown'))}",
        "",
        "[매수 규칙]",
        f"약한 매수 = 시간조건 + 고점 대비 {fmt_pct(weak_price)}: "
        f"{_ok(p.get('weak_buy_ok'))}",
        f" · 시간조건 {_span(buy_min, buy_max)} ({_months(elapsed)}): {_ok(p.get('time_ok'))}",
        f" · 가격조건 {fmt_pct(weak_price)} ({fmt_pct(p.get('price_drawdown'))}): "
        f"{_ok(p.get('opportunity_price_ok'))}",
        f"강한 매수 = 시간조건 + 고점 대비 {fmt_pct(strong_price)} + 파생조건 {required}개 이상: "
        f"{_ok(p.get('strong_buy_ok'))}",
        f" · 가격조건 {fmt_pct(strong_price)} ({fmt_pct(p.get('price_drawdown'))}): "
        f"{_ok(p.get('strong_price_ok'))}",
        f" · 파생조건 {p.get('derivative_met', 0)}/{p.get('derivative_total', 0)}개 충족"
        f" (기준 {required}개): "
        f"{_ok(int(p.get('derivative_met', 0) or 0) >= required)}",
        "",
        f"[파생조건] {p.get('derivative_met', 0)}/{p.get('derivative_total', 0)}개 충족",
    ]
    lines += [_cond_line(c) for c in p.get("derivative_conditions", [])]
    lines += [
        "",
        f"OI 고점 대비: {fmt_pct(p.get('oi_drawdown'))}",
        f"7일/30일 펀딩비: {fmt_pct(p.get('funding_7d_percent'), 4)} / "
        f"{fmt_pct(p.get('funding_30d_percent'), 4)}",
    ]

    if p.get("sell_time_stage") != "대기" or a.signal.endswith("매도"):
        excess = int(p.get("overheat_excess", 0) or 0)
        extra = f" (기준보다 +{excess}개)" if excess > 0 else ""
        hot_total = p.get("overheat_total", 0)
        pct_top = _top_share(p.get("sell_price_percentile_threshold"))
        if symbol == "BTCUSDT":
            price_rule = f"120일선 상방 이격률이 최근 3년 상위 {pct_top}"
        else:
            price_rule = (
                f"저점 대비 10배 이상 상승 or 120일선 상방 이격률이 최근 3년 상위 {pct_top}"
            )
        lines += [
            "",
            "[매도 규칙]",
            f"시간창: {p.get('sell_time_stage')} ({_months(sell_elapsed)})",
            f" · {_span(sell_min, sell_max)} 경과: {_ok(p.get('sell_time_window_ok'))}",
            f"가격 필수조건 {price_rule}: {_ok(p.get('sell_price_ok'))}",
            f" · 120일선 이격률 분위 {fmt_pct(p.get('ma120_gap_percentile'))} "
            f"(기준 상위 {pct_top}): {_ok(p.get('sell_price_ma120_hot'))}",
        ]
        if symbol != "BTCUSDT":
            lines.append(
                f" · 저점 대비 상승배수 {fmt_num(p.get('sell_price_gain_multiple'), 1)}배 "
                f"(기준 10배): {_ok(p.get('sell_price_gain_ok'))}"
            )
        lines.append(
            f"파생 과열: {hot_total}개 중 {required}개 이상 → "
            f"{p.get('overheat_met', 0)}/{hot_total}개{extra}: "
            f"{_ok(int(p.get('overheat_met', 0) or 0) >= required)}"
        )
        lines += [_cond_line(c) for c in p.get("overheat_conditions", [])]
        lines.append(
            f"꺾임 확인 (신고가 대비 10% 이내일 때만 인정): {p.get('reversal_met', 0)}개"
        )
        lines += [_cond_line(c) for c in p.get("reversal_conditions", [])]
        lines += [
            "",
            f"약한 매도 = 시간창 + 가격 필수조건 + 파생 과열 {required}개 이상: "
            f"{_ok(p.get('weak_sell_ok'))}",
            f"강한 매도 = 약한 매도 + 꺾임 1개 이상 확인: {_ok(p.get('strong_sell_ok'))}",
        ]
    return "\n".join(lines)


def render_status(parts: list[str]) -> str:
    stamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    return f"[전체 월간 상태보고]\n판정시각: {stamp}\n\n" + "\n\n──────────\n\n".join(parts)


def _header(market: str, a: Assessment, reference: str) -> list[str]:
    stamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    return [
        f"[시장] {market}",
        f"[신호] {a.signal}",
        f"[기준] {reference}",
        f"[판정시각] {stamp}",
        "",
    ]


def _ok(flag: Any) -> str:
    return "달성" if flag else "미달성"


def _months(value: Any) -> str:
    try:
        return f"{float(value):.1f}개월"
    except (TypeError, ValueError):
        return "N/A"


def _span(low: Any, high: Any) -> str:
    try:
        return f"{float(low):g}~{float(high):g}개월"
    except (TypeError, ValueError):
        return "N/A"


def _top_share(percentile: Any) -> str:
    """Turn a percentile threshold (90) into the phrasing used in the rules (상위 10%)."""
    try:
        return f"{100 - float(percentile):g}%"
    except (TypeError, ValueError):
        return "N/A"


def _cond_line(condition: dict[str, Any]) -> str:
    name = condition.get("name", "조건")
    actual = condition.get("actual")
    operator = str(condition.get("operator", ""))
    threshold = condition.get("threshold")
    # Simple comparison operators read naturally next to the number; the
    # composite ones ("가격≤-10/OI≤-20") already spell out their own threshold.
    if operator in {"≤", "<", "≥", ">"}:
        criterion = f"기준 {operator}{_g(threshold)}"
    elif operator == "|값|≤":
        criterion = f"기준 |값|≤{_g(threshold)}"
    else:
        criterion = f"기준 {operator}"
    return f" · {name} (현재 {_g(actual)}, {criterion}): {_ok(condition.get('met'))}"


def _g(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if number != number:  # NaN
        return "N/A"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _at_or_below(value: Any, threshold: Any) -> bool:
    try:
        return float(value) <= float(threshold)
    except (TypeError, ValueError):
        return False


def _flattening_line(payload: dict[str, Any]) -> str:
    detail = payload.get("flattening_detail") or {}
    changes = detail.get("monthly_changes_pct") or []
    changes_text = " → ".join(fmt_pct(value) for value in changes) if changes else "N/A"
    return (
        f"3개월 하락 둔화: {'충족' if payload.get('flattening') else '미충족'} | "
        f"월별 변화 {changes_text}, 둔화폭 {fmt_pct(detail.get('slowdown_pct_point'))}p, "
        f"누적 {fmt_pct(detail.get('cumulative_pct'))}, 범위 {fmt_pct(detail.get('range_pct'))}"
    )
