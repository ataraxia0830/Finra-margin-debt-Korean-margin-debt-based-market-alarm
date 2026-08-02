from __future__ import annotations

import os
import time
from datetime import date, timedelta
from typing import Any

import pandas as pd

from market_alarm.db import Store
from market_alarm.http import checked_json, polite_pause, session


# KIS limits access-token issuance.  An `all` run creates separate domestic
# and overseas clients, so reuse the first token for the lifetime of the
# Python process instead of immediately requesting a second token.
_TOKEN_CACHE: dict[tuple[str, str, str], tuple[str, float]] = {}


class KISClient:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.base = config["kis"]["base_url"].rstrip("/")
        self.app_key = os.getenv("KIS_APP_KEY", "").strip()
        self.app_secret = os.getenv("KIS_APP_SECRET", "").strip()
        if not self.app_key or not self.app_secret:
            raise RuntimeError("KIS_APP_KEY와 KIS_APP_SECRET이 필요합니다.")
        self.http = session(config)
        self.token = self._issue_token()

    def _issue_token(self) -> str:
        cache_key = (self.base, self.app_key, self.app_secret)
        cached = _TOKEN_CACHE.get(cache_key)
        if cached and time.monotonic() < cached[1]:
            return cached[0]

        response = self.http.post(
            f"{self.base}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=self.config["http"]["timeout_seconds"],
        )
        if response.status_code == 403:
            try:
                detail = response.json()
                message = detail.get("msg1") or detail.get("message") or detail
            except ValueError:
                message = response.text[:300] or "응답 본문 없음"
            raise RuntimeError(
                "KIS 토큰 발급이 403으로 거부됐습니다. 같은 실행의 중복 발급은 "
                f"차단했으므로 App Key/Secret·실전/모의 URL을 확인하십시오. 상세: {message}"
            )
        payload = checked_json(response)
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise RuntimeError(f"KIS 토큰 응답에 access_token이 없습니다: {payload}")
        token = str(payload["access_token"])
        # KIS currently returns expires_in as seconds.  Keep a five-minute
        # safety margin; the default still confines the cache to this process.
        try:
            expires_in = max(60, int(payload.get("expires_in", 86400)))
        except (TypeError, ValueError):
            expires_in = 86400
        _TOKEN_CACHE[cache_key] = (token, time.monotonic() + max(60, expires_in - 300))
        return token

    def _get(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        response = self.http.get(
            f"{self.base}{path}",
            headers={
                "authorization": f"Bearer {self.token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            },
            params=params,
            timeout=self.config["http"]["timeout_seconds"],
        )
        payload = checked_json(response)
        if not isinstance(payload, dict):
            raise RuntimeError(f"KIS 응답 형식이 객체가 아닙니다: {type(payload)}")
        polite_pause()
        return payload

    def domestic_prices(self, symbol: str, days: int = 280) -> pd.DataFrame:
        end = date.today()
        frames: list[pd.DataFrame] = []
        for _ in range(5):
            start = end - timedelta(days=130)
            payload = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                "FHKST03010100",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            )
            items = payload.get("output2") or []
            frame = pd.DataFrame(items)
            if frame.empty:
                break
            frames.append(frame)
            earliest = pd.to_datetime(frame["stck_bsop_date"], format="%Y%m%d").min()
            end = earliest.date() - timedelta(days=1)
            combined = pd.concat(frames).drop_duplicates("stck_bsop_date")
            if len(combined) >= days:
                break
        if not frames:
            raise RuntimeError(f"KIS 국내주식 시세가 비어 있습니다: {symbol}")
        data = pd.concat(frames).drop_duplicates("stck_bsop_date")
        result = pd.DataFrame(
            {
                "date": pd.to_datetime(data["stck_bsop_date"], format="%Y%m%d"),
                "close": pd.to_numeric(data["stck_clpr"], errors="coerce"),
            }
        ).dropna()
        return result.set_index("date").sort_index().tail(days)

    def domestic_credit(self, symbol: str, days: int = 1300) -> pd.DataFrame:
        ref = date.today()
        frames: list[pd.DataFrame] = []
        for _ in range(50):
            payload = self._get(
                "/uapi/domestic-stock/v1/quotations/daily-credit-balance",
                "FHPST04760000",
                {
                    "fid_cond_mrkt_div_code": "J",
                    "fid_cond_scr_div_code": "20476",
                    "fid_input_iscd": symbol,
                    "fid_input_date_1": ref.strftime("%Y%m%d"),
                },
            )
            items = payload.get("output") or []
            frame = pd.DataFrame(items)
            if frame.empty:
                break
            frames.append(frame)
            dates = pd.to_datetime(frame["deal_date"], format="%Y%m%d", errors="coerce")
            earliest = dates.min()
            if pd.isna(earliest):
                break
            ref = earliest.date() - timedelta(days=1)
            combined = pd.concat(frames).drop_duplicates("deal_date")
            if len(combined) >= days:
                break
        if not frames:
            raise RuntimeError(f"KIS 신용잔고가 비어 있습니다: {symbol}")
        data = pd.concat(frames).drop_duplicates("deal_date")
        result = pd.DataFrame(
            {
                "date": pd.to_datetime(data["deal_date"], format="%Y%m%d", errors="coerce"),
                "quantity": pd.to_numeric(data["whol_loan_rmnd_stcn"], errors="coerce"),
                "amount": pd.to_numeric(data.get("whol_loan_rmnd_amt"), errors="coerce"),
                "rate": pd.to_numeric(data["whol_loan_rmnd_rate"], errors="coerce"),
                "price": pd.to_numeric(data.get("stck_prpr"), errors="coerce"),
            }
        ).dropna(subset=["date", "quantity"])
        return result.set_index("date").sort_index().tail(days)

    def overseas_prices(self, exchange: str, symbol: str, days: int = 280) -> pd.DataFrame:
        bymd = date.today()
        frames: list[pd.DataFrame] = []
        for _ in range(5):
            payload = self._get(
                "/uapi/overseas-price/v1/quotations/dailyprice",
                "HHDFS76240000",
                {
                    "AUTH": "",
                    "EXCD": exchange,
                    "SYMB": symbol,
                    "GUBN": "0",
                    "BYMD": bymd.strftime("%Y%m%d"),
                    "MODP": "1",
                },
            )
            items = payload.get("output2") or []
            frame = pd.DataFrame(items)
            if frame.empty:
                break
            frames.append(frame)
            dates = pd.to_datetime(frame["xymd"], format="%Y%m%d", errors="coerce")
            earliest = dates.min()
            if pd.isna(earliest):
                break
            bymd = earliest.date() - timedelta(days=1)
            combined = pd.concat(frames).drop_duplicates("xymd")
            if len(combined) >= days:
                break
        if not frames:
            raise RuntimeError(f"KIS 해외주식 시세가 비어 있습니다: {symbol}")
        data = pd.concat(frames).drop_duplicates("xymd")
        result = pd.DataFrame(
            {
                "date": pd.to_datetime(data["xymd"], format="%Y%m%d", errors="coerce"),
                "close": pd.to_numeric(data["clos"], errors="coerce"),
            }
        ).dropna()
        return result.set_index("date").sort_index().tail(days)


def collect_domestic(config: dict[str, Any], store: Store) -> dict[str, Any]:
    client = KISClient(config)
    summary: dict[str, Any] = {}
    for symbol, spec in config["kis"]["domestic"].items():
        existing_prices = store.frame("kis", "close", symbol)
        existing_credit = store.frame("kis", "credit_quantity", symbol)
        prices = client.domestic_prices(symbol, days=280 if len(existing_prices) < 200 else 40)
        credit = client.domestic_credit(symbol, days=1300 if len(existing_credit) < 1000 else 60)
        _store_series(store, "kis", "close", symbol, prices["close"])
        _store_series(store, "kis", "credit_quantity", symbol, credit["quantity"])
        _store_series(store, "kis", "credit_rate", symbol, credit["rate"])
        if "amount" in credit:
            _store_series(store, "kis", "credit_amount", symbol, credit["amount"].dropna())
        summary[symbol] = {
            "name": spec["name"],
            "price_rows": len(prices),
            "credit_rows": len(credit),
            "latest_price": float(prices["close"].iloc[-1]),
        }
    return summary


def collect_overseas(config: dict[str, Any], store: Store) -> dict[str, Any]:
    client = KISClient(config)
    summary: dict[str, Any] = {}
    for symbol, spec in config["kis"]["overseas"].items():
        existing = store.frame("kis", "close", symbol)
        prices = client.overseas_prices(
            spec["exchange"], symbol, days=280 if len(existing) < 200 else 40
        )
        _store_series(store, "kis", "close", symbol, prices["close"])
        summary[symbol] = {
            "name": spec["name"],
            "rows": len(prices),
            "latest_price": float(prices["close"].iloc[-1]),
        }
    return summary


def _store_series(
    store: Store, source: str, series: str, symbol: str, values: pd.Series
) -> None:
    rows = [
        (pd.Timestamp(idx).tz_localize("UTC").isoformat(), float(value), None)
        for idx, value in values.dropna().items()
    ]
    store.upsert_points(source, series, rows, symbol=symbol)
