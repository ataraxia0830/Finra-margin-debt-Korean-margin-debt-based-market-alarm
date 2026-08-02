from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup

from market_alarm.db import Store
from market_alarm.http import session


def collect(config: dict[str, Any], store: Store) -> dict[str, Any]:
    cfg = config["finra"]
    client = session(config)
    urls = [cfg["excel_url"]]

    # FINRA가 파일명을 바꾸는 경우 페이지의 최신 xlsx 링크를 우선 후보에 추가한다.
    try:
        page = client.get(cfg["page_url"], timeout=config["http"]["timeout_seconds"])
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")
        discovered = [
            a.get("href")
            for a in soup.select("a[href]")
            if ".xlsx" in (a.get("href") or "").lower()
        ]
        for href in discovered:
            if href.startswith("/"):
                href = "https://www.finra.org" + href
            if href and href not in urls:
                urls.insert(0, href)
    except Exception:
        # HTML 감시 페이지 장애와 Excel 원본 장애를 분리한다.
        pass

    last_error: Exception | None = None
    frame: pd.DataFrame | None = None
    used_url = ""
    for url in urls:
        try:
            response = client.get(url, timeout=config["http"]["timeout_seconds"])
            response.raise_for_status()
            frame = parse_excel(response.content)
            used_url = url
            break
        except Exception as exc:  # try the stable fallback URL
            last_error = exc
    if frame is None:
        raise RuntimeError(f"FINRA Excel을 읽지 못했습니다: {last_error}")

    rows = [
        (
            idx.strftime("%Y-%m-%dT00:00:00+00:00"),
            float(row["debit_balance"]),
            {
                "cash_credit": _number(row.get("cash_credit")),
                "margin_credit": _number(row.get("margin_credit")),
                "source_url": used_url,
            },
        )
        for idx, row in frame.iterrows()
    ]
    previous = store.latest("finra", "margin_debt")
    store.upsert_points("finra", "margin_debt", rows)
    latest = frame.iloc[-1]
    latest_date = frame.index[-1]
    if previous is None:
        is_new = True
    else:
        prev_ts = pd.Timestamp(previous["ts"])
        is_new = (prev_ts.year, prev_ts.month) < (latest_date.year, latest_date.month)
    return {
        "new_month": bool(is_new),
        "latest_month": latest_date.strftime("%Y-%m"),
        "latest_value": float(latest["debit_balance"]),
        "rows": len(rows),
        "source_url": used_url,
    }


def parse_excel(content: bytes) -> pd.DataFrame:
    raw = pd.read_excel(BytesIO(content), header=None)
    header_row = None
    for i, row in raw.head(20).iterrows():
        joined = " ".join(str(v) for v in row.tolist()).lower()
        if ("month" in joined or "year" in joined) and "debit" in joined:
            header_row = i
            break
    if header_row is None:
        raise ValueError("FINRA Excel에서 Month/Year 및 Debit 헤더를 찾지 못했습니다.")
    headers = [_flatten(v) for v in raw.iloc[header_row].tolist()]
    data = raw.iloc[header_row + 1 :].copy()
    data.columns = headers
    month_col = next(c for c in headers if "month" in c or "year" in c)
    debit_col = next(c for c in headers if "debit" in c)
    cash_col = next((c for c in headers if "cash" in c and "credit" in c), None)
    margin_col = next(
        (c for c in headers if "securities" in c and "credit" in c and c != cash_col),
        None,
    )
    raw_dates = data[month_col].astype(str).str.strip()
    dates = pd.to_datetime(raw_dates, format="mixed", errors="coerce")
    fallback = pd.to_datetime(raw_dates, format="%b-%y", errors="coerce")
    dates = dates.fillna(fallback)
    result = pd.DataFrame(
        {
            "date": dates,
            "debit_balance": data[debit_col].map(_number),
            "cash_credit": data[cash_col].map(_number) if cash_col else None,
            "margin_credit": data[margin_col].map(_number) if margin_col else None,
        }
    ).dropna(subset=["date", "debit_balance"])
    result["date"] = result["date"] + pd.offsets.MonthEnd(0)
    return result.drop_duplicates("date").set_index("date").sort_index()


def _flatten(value: Any) -> str:
    return " ".join(str(value).replace("\n", " ").split()).lower()


def _number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    cleaned = str(value).replace(",", "").replace("$", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None
