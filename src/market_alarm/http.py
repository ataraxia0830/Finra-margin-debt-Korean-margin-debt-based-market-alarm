from __future__ import annotations

import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def session(config: dict[str, Any]) -> requests.Session:
    retry = Retry(
        total=int(config["http"]["retries"]),
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    client = requests.Session()
    client.headers.update({"User-Agent": config["http"]["user_agent"]})
    client.mount("https://", adapter)
    return client


def checked_json(response: requests.Response) -> dict[str, Any] | list[Any]:
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("rt_cd") not in (None, "0"):
        raise RuntimeError(
            f"API 오류 {payload.get('msg_cd', '')}: {payload.get('msg1', payload)}"
        )
    return payload


def polite_pause(seconds: float = 0.12) -> None:
    time.sleep(seconds)

