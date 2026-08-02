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
        lines += [
            f"전체 신용잔고 YoY: {fmt_pct(p.get('current_yoy'))}",
            f"선행 최고 YoY: {fmt_pct(p.get('peak_yoy'))} ({p.get('peak_yoy_month', 'N/A')})",
            f"고점 대비 잔고: {fmt_pct(p.get('balance_drawdown'))}",
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
    name = p.get("symbol", "코인").replace("USDT", "")
    lines = _header(name, a, p.get("reference_time", "N/A"))
    if a.state == "INSUFFICIENT_DATA":
        return "\n".join(lines + ["가격·OI·펀딩비 자료가 충분하지 않습니다."])
    lines += [
        f"현재 가격: {fmt_num(p.get('price'), 2)}",
        f"고점일·경과: {p.get('high_date')} / {p.get('months_since_high', float('nan')):.1f}개월",
        f"고점 대비 가격: {fmt_pct(p.get('price_drawdown'))}",
        f"약한 매수 조건: {'달성' if p.get('time_ok') and p.get('opportunity_price_ok') else '미달성'}",
        f"강한 매수 조건: {'달성' if p.get('time_ok') and p.get('strong_price_ok') and p.get('derivative_met', 0) >= p.get('derivative_required', 0) else '미달성'}",
        f"OI 고점 대비: {fmt_pct(p.get('oi_drawdown'))}",
        f"7일/30일 펀딩비: {fmt_pct(p.get('funding_7d_percent'), 4)} / {fmt_pct(p.get('funding_30d_percent'), 4)}",
        f"파생조건: {p.get('derivative_met')}/{p.get('derivative_total')}개 충족",
    ]
    if p.get("sell_time_stage") != "대기" or a.signal.endswith("매도"):
        excess = int(p.get("overheat_excess", 0) or 0)
        extra = f" (기준보다 +{excess}개)" if excess > 0 else ""
        lines += [
            "",
            "[매도 규칙]",
            f"시간창: {p.get('sell_time_stage')} ({p.get('sell_cycle_months', float('nan')):.1f}개월)",
            f"가격 필수조건: {'달성' if p.get('sell_price_ok') else '미달성'}",
            f"파생 과열: {p.get('overheat_met', 0)}/{p.get('overheat_total', 0)}개{extra}",
            f"꺾임 확인: {p.get('reversal_met', 0)}개",
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
