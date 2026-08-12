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


def test_workflow_uses_reduced_schedule_and_accepts_queued_legacy_schedules():
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "monitor.yml"
    ).read_text(encoding="utf-8")
    schedule_block = workflow.split("  schedule:", 1)[1].split("\n\npermissions:", 1)[0]

    # Active schedule: Korea 1/day, US 1/day, crypto every 12 hours,
    # FINRA on four selected dates, and one cron entry each for month-end/monthly.
    active_schedules = (
        '"37 9 * * 1-5"',
        '"17 23 * * *"',
        '"23 9,21 * * *"',
        '"27 6 15,18,21,24 * *"',
        '"47 14 28-31 * *"',
        '"27 0 1 * *"',
    )
    assert schedule_block.count("- cron:") == len(active_schedules)
    for current_schedule in active_schedules:
        assert f"- cron: {current_schedule}" in schedule_block

    # Daily backup schedules must not remain active.
    for removed_backup in (
        '"17 11 * * 1-5"',
        '"47 0 * * *"',
        '"47 13 28-31 * *"',
        '"27 3 1 * *"',
    ):
        assert f"- cron: {removed_backup}" not in schedule_block

    # Events queued before schedule migrations must remain routable.
    for legacy_schedule in (
        '"30 9 * * 1-5"',
        '"17 11 * * 1-5"',
        '"0 23 * * *"',
        '"47 0 * * *"',
        '"5 3,7,11,15,19,23 * * *"',
        '"23 3,7,11,15,19,23 * * *"',
        '"23 3,9,15,21 * * *"',
        '"0 6 15-25 * *"',
        '"27 6 15-25 * *"',
        '"50 14 28-31 * *"',
        '"47 13 28-31 * *"',
        '"0 0 1 * *"',
        '"27 3 1 * *"',
    ):
        assert legacy_schedule in workflow
