from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup

from market_alarm.config import env_bool
from market_alarm.db import Store
from market_alarm.http import session


def collect(config: dict[str, Any], store: Store) -> dict[str, Any]:
    cfg = config["freesis"]
    frames: list[pd.DataFrame] = []
    methods: list[str] = []
    project = Path(config["_project_dir"])

    # FreeSIS is an eXBuilder application.  Its public page contains neither a
    # normal HTML table nor a stable Excel link; the page itself posts this
    # request to KOFIA's official JSON endpoint.  Use that endpoint first so a
    # headless browser is not required on GitHub Actions.
    try:
        frames.append(_fetch_official_json(config))
        methods.append("official-json")
    except Exception:
        pass

    for rel in cfg.get("bootstrap_files", []):
        path = project / rel
        if path.exists():
            frames.append(parse_file(path.read_bytes(), path.suffix))
            methods.append(f"bootstrap:{path.name}")

    direct_url = os.getenv("FREESIS_DOWNLOAD_URL", "").strip()
    if direct_url:
        frames.append(_fetch_override(config, direct_url))
        methods.append("override-download")

    if not frames:
        try:
            html_frame = _fetch_html_or_discovered_download(config)
            if not html_frame.empty:
                frames.append(html_frame)
                methods.append("html-or-discovered-download")
        except Exception:
            pass

    # GitHub Actions installs Chromium in this release, so browser fallback is
    # enabled by default.  It can still be explicitly disabled for local runs.
    if not frames and env_bool("FREESIS_USE_PLAYWRIGHT", default=False):
        try:
            frames.append(_fetch_playwright(config))
            methods.append("playwright")
        except Exception:
            if not frames:
                raise

    if not frames:
        raise RuntimeError(
            "FreeSIS 공식 JSON과 보조 수집 경로에서 데이터를 읽지 못했습니다. "
            "FreeSIS Excel/CSV를 bootstrap/freesis_credit 파일로 넣어 재시도하세요."
        )

    frame = pd.concat(frames).sort_index()
    frame = frame[~frame.index.duplicated(keep="last")].dropna(subset=["credit_balance"])
    rows = [
        (
            pd.Timestamp(idx).tz_localize("Asia/Seoul").tz_convert("UTC").isoformat(),
            float(row["credit_balance"]),
            {"source_methods": methods},
        )
        for idx, row in frame.iterrows()
    ]
    store.upsert_points("freesis", "credit_balance", rows)
    return {
        "rows": len(rows),
        "latest_date": frame.index[-1].strftime("%Y-%m-%d"),
        "latest_value": float(frame.iloc[-1]["credit_balance"]),
        "methods": methods,
    }


def _fetch_official_json(config: dict[str, Any]) -> pd.DataFrame:
    """Read the public KOFIA FreeSIS series through its own JSON backend."""

    cfg = config["freesis"]
    client = session(config)
    base = str(cfg.get("api_base_url", "https://freesis.kofia.or.kr")).rstrip("/")
    start_month = str(cfg.get("history_start_month", "201001"))
    end_month = datetime.now().strftime("%Y%m")
    payload = {
        "dmSearch": {
            "tmpV1": "M",
            "tmpV45": start_month,
            "tmpV46": end_month,
            # FreeSIS' displayed default unit is one million won.  Omitting
            # these fields returns dates with every numeric field set to null.
            "tmpV40": "1000000",
            "tmpV41": "1",
            "OBJ_NM": "STATSCU0100000070BO",
        }
    }
    response = client.post(
        f"{base}/meta/getMetaDataList.do",
        json=payload,
        headers={"Accept": "application/json"},
        timeout=config["http"]["timeout_seconds"],
    )
    response.raise_for_status()
    data = response.json()
    return parse_official_json(data)


def parse_official_json(payload: dict[str, Any]) -> pd.DataFrame:
    rows = payload.get("ds1") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("FreeSIS 공식 JSON에 ds1 데이터가 없습니다.")
    frame = pd.DataFrame(rows)
    if "TMPV1" not in frame or "TMPV2" not in frame:
        raise ValueError("FreeSIS 공식 JSON에 날짜/전체 신용융자 열이 없습니다.")
    result = pd.DataFrame(
        {
            "date": pd.to_datetime(frame["TMPV1"], format="%Y%m%d", errors="coerce"),
            "credit_balance": pd.to_numeric(frame["TMPV2"], errors="coerce"),
        }
    ).dropna()
    if result.empty:
        raise ValueError("FreeSIS 공식 JSON의 전체 신용융자 값이 비어 있습니다.")
    return result.drop_duplicates("date").set_index("date").sort_index()


def _fetch_override(config: dict[str, Any], url: str) -> pd.DataFrame:
    client = session(config)
    method = os.getenv("FREESIS_DOWNLOAD_METHOD", "GET").upper()
    payload_raw = os.getenv("FREESIS_DOWNLOAD_PAYLOAD_JSON", "").strip()
    payload = json.loads(payload_raw) if payload_raw else {}
    response = client.request(
        method,
        url,
        params=payload if method == "GET" else None,
        data=payload if method != "GET" else None,
        timeout=config["http"]["timeout_seconds"],
    )
    response.raise_for_status()
    return parse_file(response.content, _suffix(response.headers.get("content-type", ""), url))


def _fetch_html_or_discovered_download(config: dict[str, Any]) -> pd.DataFrame:
    url = config["freesis"]["page_url"]
    client = session(config)
    response = client.get(url, timeout=config["http"]["timeout_seconds"])
    response.raise_for_status()
    try:
        tables = pd.read_html(StringIO(response.text))
        candidates = []
        for table in tables:
            try:
                candidates.append(normalize(table))
            except ValueError:
                continue
        if candidates:
            return pd.concat(candidates).sort_index()
    except ValueError:
        pass

    soup = BeautifulSoup(response.text, "html.parser")
    links = [
        urljoin(url, a.get("href"))
        for a in soup.select("a[href]")
        if re.search(r"\.(xlsx?|csv)(?:\?|$)", a.get("href") or "", re.I)
    ]
    for link in links:
        download = client.get(link, timeout=config["http"]["timeout_seconds"])
        if download.ok:
            try:
                return parse_file(download.content, _suffix(download.headers.get("content-type", ""), link))
            except Exception:
                continue
    raise RuntimeError("FreeSIS HTML/다운로드 링크에서 표를 찾지 못했습니다.")


def _fetch_playwright(config: dict[str, Any]) -> pd.DataFrame:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)
        page.goto(
            config["freesis"]["page_url"],
            wait_until="domcontentloaded",
            timeout=90_000,
        )
        page.wait_for_timeout(5_000)

        # Some FreeSIS deployments render the statistics app inside a frame.
        # Try a visible 조회 button before reading the generated grid.
        for frame in page.frames:
            for selector in ("button:has-text('조회')", "a:has-text('조회')", "text=조회"):
                try:
                    locator = frame.locator(selector)
                    if locator.count() and locator.first.is_visible():
                        locator.first.click(timeout=5_000)
                        page.wait_for_timeout(3_000)
                        break
                except Exception:
                    continue

        # 페이지 기본 조회 결과가 있으면 DOM 표를 먼저 읽는다.
        for frame in page.frames:
            try:
                tables = pd.read_html(StringIO(frame.content()))
            except (ValueError, Exception):
                tables = []
            for table in tables:
                try:
                    result = normalize(table)
                    browser.close()
                    return result
                except ValueError:
                    continue

        selectors = [
            "a:has-text('Excel')",
            "button:has-text('Excel')",
            "a[title*='엑셀']",
            "button[title*='엑셀']",
            "a[aria-label*='엑셀']",
        ]
        for frame in page.frames:
            for selector in selectors:
                locator = frame.locator(selector)
                if locator.count() == 0:
                    continue
                try:
                    with page.expect_download(timeout=30_000) as info:
                        locator.first.click()
                    download = info.value
                    path = Path(tmp) / download.suggested_filename
                    download.save_as(path)
                    browser.close()
                    return parse_file(path.read_bytes(), path.suffix)
                except Exception:
                    continue
        browser.close()
    raise RuntimeError("Playwright가 FreeSIS 표 또는 Excel 다운로드 버튼을 찾지 못했습니다.")


def parse_file(content: bytes, suffix: str) -> pd.DataFrame:
    suffix = suffix.lower()
    if "csv" in suffix:
        for encoding in ("utf-8-sig", "cp949", "euc-kr"):
            try:
                return normalize(pd.read_csv(BytesIO(content), encoding=encoding))
            except UnicodeDecodeError:
                continue
    sheets = pd.read_excel(BytesIO(content), sheet_name=None)
    errors = []
    for frame in sheets.values():
        try:
            return normalize(frame)
        except ValueError as exc:
            errors.append(str(exc))
    raise ValueError(f"FreeSIS 파일에 인식 가능한 신용융자 표가 없습니다: {errors[:2]}")


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [" ".join(str(x) for x in col if str(x) != "nan") for col in data.columns]
    data.columns = [" ".join(str(c).replace("\n", " ").split()) for c in data.columns]

    date_col = next(
        (c for c in data.columns if any(k in c for k in ("일자", "날짜", "기준일", "Date", "date"))),
        None,
    )
    preferred = [
        c
        for c in data.columns
        if ("신용거래융자" in c or "신용융자" in c)
        and ("전체" in c or "합계" in c or len([x for x in data.columns if "신용" in x]) == 1)
    ]
    credit_col = preferred[0] if preferred else None
    if date_col is None or credit_col is None:
        raise ValueError(f"날짜/신용융자-전체 열을 못 찾음: {data.columns.tolist()}")
    dates = pd.to_datetime(
        data[date_col].astype(str).str.replace(".", "-", regex=False).str.replace("/", "-", regex=False),
        errors="coerce",
    )
    values = pd.to_numeric(
        data[credit_col].astype(str).str.replace(",", "", regex=False).str.replace(" ", "", regex=False),
        errors="coerce",
    )
    result = pd.DataFrame({"date": dates, "credit_balance": values}).dropna()
    if result.empty:
        raise ValueError("FreeSIS 날짜/신용융자 값이 모두 비어 있습니다.")
    return result.drop_duplicates("date").set_index("date").sort_index()


def _suffix(content_type: str, url: str) -> str:
    lowered = (content_type + " " + url).lower()
    if "csv" in lowered:
        return ".csv"
    return ".xlsx"
