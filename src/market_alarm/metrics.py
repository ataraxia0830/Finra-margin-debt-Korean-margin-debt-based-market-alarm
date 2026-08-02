from __future__ import annotations

from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd


def yoy(series: pd.Series) -> pd.Series:
    s = month_end(series) if isinstance(series.index, pd.DatetimeIndex) else series.sort_index()
    return s.pct_change(12, fill_method=None) * 100


def month_end(series: pd.Series) -> pd.Series:
    s = series.dropna().sort_index()
    if s.empty or not isinstance(s.index, pd.DatetimeIndex):
        return s
    keys = pd.Series(list(zip(s.index.year, s.index.month)), index=s.index)
    grouped = s.groupby(keys).last()
    idx = pd.DatetimeIndex(
        [pd.Timestamp(year=y, month=m, day=1) + pd.offsets.MonthEnd(0) for y, m in grouped.index]
    )
    if s.index.tz is not None:
        idx = idx.tz_localize(s.index.tz)
    grouped.index = idx
    return grouped.sort_index()


def gap_percent(price: float, moving_average: float) -> float:
    if moving_average == 0:
        return float("nan")
    return (price / moving_average - 1) * 100


def drawdown(current: float, peak: float) -> float:
    if peak == 0:
        return float("nan")
    return (current / peak - 1) * 100


def relative_drop(current: float, peak: float) -> float:
    return drawdown(current, peak)


def months_between(earlier: pd.Timestamp | date, later: pd.Timestamp | date) -> float:
    a = pd.Timestamp(earlier)
    b = pd.Timestamp(later)
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 30.44


def flattening_3m(
    series: pd.Series,
) -> tuple[bool, dict[str, float | bool | list[float]]]:
    recent = series.dropna().sort_index().tail(3)
    if len(recent) < 3:
        return False, {
            "range_pct": float("nan"),
            "cumulative_pct": float("nan"),
            "monthly_changes_pct": [],
            "slowdown_pct_point": float("nan"),
            "any_up": False,
        }
    avg = float(recent.mean())
    range_pct = float((recent.max() - recent.min()) / avg * 100) if avg else float("nan")
    cumulative = float((recent.iloc[-1] / recent.iloc[0] - 1) * 100)
    changes = (recent.pct_change(fill_method=None).dropna() * 100).astype(float)
    monthly_changes = [float(value) for value in changes]
    slowdown = (
        float(monthly_changes[-1] - monthly_changes[-2])
        if len(monthly_changes) >= 2
        else float("nan")
    )
    any_up = bool((changes > 0).any())
    ok = range_pct <= 5 or abs(cumulative) <= 2 or any_up
    return ok, {
        "range_pct": range_pct,
        "cumulative_pct": cumulative,
        "monthly_changes_pct": monthly_changes,
        "slowdown_pct_point": slowdown,
        "any_up": any_up,
    }


def percentile_rank(values: Iterable[float], current: float) -> float:
    arr = np.asarray([v for v in values if pd.notna(v)], dtype=float)
    if len(arr) == 0:
        return float("nan")
    return float((arr <= current).mean() * 100)


def fmt_pct(value: float | None, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value:+.{digits}f}%"


def fmt_num(value: float | None, digits: int = 0) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value:,.{digits}f}"
