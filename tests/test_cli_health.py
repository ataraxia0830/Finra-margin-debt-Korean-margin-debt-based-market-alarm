import pytest

from market_alarm.cli import main
from market_alarm.telegram import Telegram


def test_doctor_strict_fails_when_required_secrets_are_missing(monkeypatch):
    for name in (
        "KIS_APP_KEY",
        "KIS_APP_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    assert main(["doctor", "--strict"]) == 1


def test_telegram_missing_secrets_is_not_silently_printed(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    telegram = Telegram({"alerts": {"telegram_max_chars": 3500}})

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        telegram.send("test")


def test_telegram_dry_run_still_works_without_secrets(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("DRY_RUN", "true")
    telegram = Telegram({"alerts": {"telegram_max_chars": 3500}})

    telegram.send("test message")

    assert "test message" in capsys.readouterr().out
