import pandas as pd
import pytest

from market_alarm import service
from market_alarm.rules import Assessment


class FakeTelegram:
    def __init__(self, config):
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture
def monitor_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "Telegram", FakeTelegram)
    # Collectors are network-bound; `all` only needs them to be no-ops here.
    monkeypatch.setattr(service.finra, "collect", lambda c, s: {"new_month": True})
    monkeypatch.setattr(service.kis, "collect_overseas", lambda c, s: {})
    monkeypatch.setattr(service.kis, "collect_domestic", lambda c, s: {})
    monkeypatch.setattr(service.freesis, "collect", lambda c, s: {})
    monkeypatch.setattr(service.binance, "collect", lambda c, s: {})

    monkeypatch.setattr(
        service.Monitor,
        "_finra_assessment",
        lambda self: (Assessment("NEUTRAL", "관망", "WATCH", {}), {}),
    )
    monkeypatch.setattr(
        service.Monitor,
        "_korea_assessment",
        lambda self: Assessment("NEUTRAL", "관망", "WATCH", {}),
    )
    monkeypatch.setattr(
        service.Monitor,
        "_crypto_assessment",
        lambda self, symbol: Assessment("NEUTRAL", "관망", "WATCH", {}),
    )

    config = {
        "_database_path": str(tmp_path / "market.sqlite"),
        "timezone": "Asia/Seoul",
        "alerts": {"telegram_max_chars": 3500},
        "binance": {"symbols": {"BTCUSDT": {"name": "비트코인"}}},
    }

    def build(force_alert: bool):
        return service.Monitor(config, force_alert=force_alert)

    return build


def _us_messages(monitor):
    return [m for m in monitor.telegram.sent if "미국" in m or "FINRA" in m]


def test_force_alert_sends_us_report_once(monitor_factory):
    """--force-alert used to push the identical US report from both steps."""
    monitor = monitor_factory(force_alert=True)
    monitor.run("all")
    assert len(_us_messages(monitor)) == 1


def test_normal_run_sends_us_report_once(monitor_factory):
    monitor = monitor_factory(force_alert=False)
    monitor.run("all")
    assert len(_us_messages(monitor)) == 1


def test_second_normal_run_is_silent_when_state_unchanged(monitor_factory):
    monitor_factory(force_alert=False).run("all")
    second = monitor_factory(force_alert=False)
    second.run("all")
    assert _us_messages(second) == []


def test_standalone_finra_task_still_alerts(monitor_factory):
    monitor = monitor_factory(force_alert=False)
    monitor.run("finra")
    assert len(_us_messages(monitor)) == 1


def test_sent_keys_are_per_monitor_instance(monitor_factory):
    monitor = monitor_factory(force_alert=True)
    monitor.run("all")
    assert "us" in monitor._sent_keys
    assert len(monitor_factory(force_alert=True)._sent_keys) == 0


def test_us_sell_schedule_alerts_once_then_again_only_when_due(monitor_factory):
    scheduled = Assessment(
        "SELL_SCHEDULED_2026_11",
        "강한 매도 / 3개월 후 매도",
        "TRIGGERED",
        {"sell_target_month": "2026-11", "sell_due": False},
    )
    due = Assessment(
        "SELL_DUE_2026_11",
        "강한 매도 / 당월 매도 필요",
        "TRIGGERED",
        {"sell_target_month": "2026-11", "sell_due": True},
    )

    first = monitor_factory(force_alert=False)
    first._state_alert("us", scheduled, "3개월 후 매도")
    assert first.telegram.sent == ["3개월 후 매도"]

    middle = monitor_factory(force_alert=False)
    middle._state_alert("us", scheduled, "반복 금지")
    assert middle.telegram.sent == []

    target_month = monitor_factory(force_alert=False)
    target_month._state_alert("us", due, "당월 매도 필요")
    assert target_month.telegram.sent == ["당월 매도 필요"]
