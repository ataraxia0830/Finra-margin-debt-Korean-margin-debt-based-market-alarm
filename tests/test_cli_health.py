import pytest
from pathlib import Path

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


def test_telegram_uses_configured_retry_count():
    telegram = Telegram(
        {
            "alerts": {"telegram_max_chars": 3500},
            "http": {"retries": 5},
        }
    )

    adapter = telegram.client.get_adapter("https://")
    assert adapter.max_retries.total == 5
    assert adapter.max_retries.connect == 5
    assert adapter.max_retries.read == 5


def test_workflow_accepts_current_and_queued_legacy_schedules():
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "monitor.yml"
    ).read_text(encoding="utf-8")

    # Current six-hour crypto cadence.
    assert '- cron: "23 3,9,15,21 * * *"' in workflow

    # Events queued before either schedule migration must remain routable.
    for legacy_schedule in (
        '"30 9 * * 1-5"',
        '"0 23 * * *"',
        '"5 3,7,11,15,19,23 * * *"',
        '"23 3,7,11,15,19,23 * * *"',
        '"0 6 15-25 * *"',
        '"50 14 28-31 * *"',
        '"0 0 1 * *"',
    ):
        assert legacy_schedule in workflow
