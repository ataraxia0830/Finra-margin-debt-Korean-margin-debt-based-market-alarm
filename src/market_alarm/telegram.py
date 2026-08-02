from __future__ import annotations

import os
from typing import Any

import requests


class Telegram:
    def __init__(self, config: dict[str, Any]):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.max_chars = int(config["alerts"]["telegram_max_chars"])
        self.dry_run = os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def require_configured(self) -> None:
        missing = []
        if not self.token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            raise RuntimeError(
                "GitHub Secrets에 필수 Telegram 값이 없습니다: " + ", ".join(missing)
            )

    def send(self, text: str) -> None:
        if self.dry_run:
            print(text)
            return
        self.require_configured()
        chunks = _split(text, self.max_chars)
        for chunk in chunks:
            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram 전송 실패: {payload}")


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit and current:
            chunks.append(current.rstrip())
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current += line
    if current:
        chunks.append(current.rstrip())
    return chunks
