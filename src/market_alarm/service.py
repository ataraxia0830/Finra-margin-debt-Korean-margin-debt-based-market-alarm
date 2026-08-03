from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from market_alarm.collectors import binance, finra, freesis, kis
from market_alarm.db import Store
from market_alarm.reporting import render_crypto, render_finra, render_korea, render_status
from market_alarm.rules import (
    Assessment,
    assess_crypto,
    assess_finra,
    assess_korea,
    stock_snapshot,
)
from market_alarm.telegram import Telegram


class Monitor:
    def __init__(self, config: dict[str, Any], force_alert: bool = False):
        self.config = config
        self.store = Store(config["_database_path"])
        self.telegram = Telegram(config)
        self.force_alert = force_alert
        # Guards against the same alert key being rendered and pushed twice in a
        # single invocation.  ``--force-alert`` bypasses the state-change check,
        # so without this a shared assessment reached from two steps would send
        # two identical Telegram messages.
        self._sent_keys: set[str] = set()

    def run(self, task: str) -> str:
        run_id = self.store.start_run(task)
        try:
            if task == "finra":
                result = self.run_finra(collect=True)
            elif task == "us":
                result = self.run_us()
            elif task == "korea":
                result = self.run_korea(collect=True)
            elif task == "crypto":
                result = self.run_crypto(collect=True)
            elif task == "month-end":
                result = self.run_month_end()
            elif task == "monthly":
                result = self.run_monthly()
            elif task == "all":
                result = self.run_all()
            else:
                raise ValueError(f"알 수 없는 task: {task}")
            self.store.finish_run(run_id, "OK", result)
            return result
        except Exception as exc:
            self.store.finish_run(run_id, "ERROR", repr(exc))
            try:
                self.telegram.send(f"[시장 알람 시스템 오류]\n작업: {task}\n오류: {type(exc).__name__}: {exc}")
            except Exception:
                pass
            raise

    def run_finra(self, collect: bool, notify: bool = True) -> str:
        info = finra.collect(self.config, self.store) if collect else {}
        if not notify:
            # `all`에서는 FINRA 수집만 담당하고, 미국 알림은 해외 종가까지 갱신한
            # 뒤 `us` 단계가 한 번만 보낸다.  여기서 상태를 기록하면 `us` 단계의
            # 상태변화 판정이 소모되므로 판정도 보류한다.
            return json.dumps({"collector": info, "state": "DEFERRED"}, ensure_ascii=False)
        assessment, stocks = self._finra_assessment()
        message = render_finra(assessment, stocks)
        # 신규 월이 없으면 상태가 바뀌었더라도 FINRA 자체 반복점검 알림은 보내지 않는다.
        eligible = not collect or bool(info.get("new_month"))
        self._state_alert("us", assessment, message, eligible=eligible)
        return json.dumps({"collector": info, "state": assessment.state}, ensure_ascii=False)

    def run_us(self) -> str:
        info = kis.collect_overseas(self.config, self.store)
        assessment, stocks = self._finra_assessment()
        message = render_finra(assessment, stocks)
        self._state_alert("us", assessment, message)
        return json.dumps({"collector": info, "state": assessment.state}, ensure_ascii=False)

    def run_korea(self, collect: bool) -> str:
        info: dict[str, Any] = {}
        if collect:
            info["kis"] = kis.collect_domestic(self.config, self.store)
            try:
                info["freesis"] = freesis.collect(self.config, self.store)
                self.store.set_state("collector:freesis", "OK", info["freesis"])
            except Exception as exc:
                # FreeSIS is independent of KIS and Binance.  Keep any cached
                # Korean series, mark a first-run assessment as pending, and do
                # not abort the rest of an `all` test run.
                error = f"{type(exc).__name__}: {exc}"
                info["freesis"] = {"status": "ERROR", "error": error}
                changed = self.store.set_state(
                    "collector:freesis", "ERROR", {"error": error}
                )
                if changed or self.force_alert:
                    self.telegram.send(
                        "[시장 알람 데이터 수집 경고]\n"
                        "시장: 한국주식\n"
                        f"수집기: FreeSIS\n오류: {error}\n"
                        "한국 전체 신용잔고 신호만 판정 보류합니다. "
                        "미국·코인 수집은 계속됩니다."
                    )
        assessment = self._korea_assessment()
        message = render_korea(assessment)
        self._state_alert("korea", assessment, message)
        return json.dumps({"collector": info, "state": assessment.state}, ensure_ascii=False)

    def run_crypto(self, collect: bool) -> str:
        info = binance.collect(self.config, self.store) if collect else {}
        states: dict[str, str] = {}
        for symbol in self.config["binance"]["symbols"]:
            assessment = self._crypto_assessment(symbol)
            self._state_alert(f"crypto:{symbol}", assessment, render_crypto(assessment))
            states[symbol] = assessment.state
        return json.dumps({"collector": info, "states": states}, ensure_ascii=False)

    def run_month_end(self) -> str:
        now = datetime.now(ZoneInfo(self.config["timezone"]))
        tomorrow = now.date() + timedelta(days=1)
        if tomorrow.month == now.month:
            return json.dumps({"skipped": True, "reason": "오늘은 월말이 아님"}, ensure_ascii=False)
        results = {
            "finra": self.run_finra(collect=False),
            "korea": self.run_korea(collect=False),
        }
        return json.dumps(results, ensure_ascii=False)

    def run_monthly(self) -> str:
        finra_a, stocks = self._finra_assessment()
        parts = [render_finra(finra_a, stocks), render_korea(self._korea_assessment())]
        for symbol in self.config["binance"]["symbols"]:
            parts.append(render_crypto(self._crypto_assessment(symbol)))
        self.telegram.send(render_status(parts))
        return json.dumps({"monthly_report": "sent"}, ensure_ascii=False)

    def run_all(self) -> str:
        results: dict[str, Any] = {}
        # `finra`와 `us`는 같은 "미국" 판정을 공유한다.  FINRA는 수집만 하고
        # 알림은 해외 종가까지 갱신된 뒤 `us` 단계에서 한 번만 보낸다.  이렇게
        # 해야 중복 전송이 없고, 전송되는 판정도 최신 종가를 반영한다.
        steps = (
            ("finra", lambda: self.run_finra(collect=True, notify=False)),
            ("us", self.run_us),
            ("korea", lambda: self.run_korea(collect=True)),
            ("crypto", lambda: self.run_crypto(collect=True)),
        )
        for name, action in steps:
            try:
                results[name] = action()
                self.store.set_state(f"collector:{name}", "OK", {})
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                results[name] = {"status": "ERROR", "error": error}
                changed = self.store.set_state(
                    f"collector:{name}", "ERROR", {"error": error}
                )
                if changed or self.force_alert:
                    self.telegram.send(
                        "[시장 알람 데이터 수집 경고]\n"
                        f"작업: all / {name}\n오류: {error}\n"
                        "이 시장만 건너뛰고 나머지 수집을 계속합니다."
                    )
        return json.dumps(results, ensure_ascii=False)

    def _finra_assessment(self) -> tuple[Assessment, dict[str, dict[str, Any]]]:
        balance = self.store.frame("finra", "margin_debt")["value"]
        assessment = assess_finra(
            balance,
            self.config["finra"],
            as_of=pd.Timestamp.now(tz=self.config["timezone"]),
        )
        stocks: dict[str, dict[str, Any]] = {}
        for symbol, spec in self.config["kis"]["overseas"].items():
            prices = self.store.frame("kis", "close", symbol)["value"]
            if prices.empty:
                continue
            snap = stock_snapshot(prices)
            buy_threshold = float(spec["buy_ma200_gap"])
            sell_warning = float(spec["sell_ma120_warning"])
            sell_strong = float(spec["sell_ma120_strong"])
            snap.update(
                {
                    "name": spec["name"],
                    "buy_ma200_threshold": buy_threshold,
                    "buy_price_ok": bool(snap.get("ma200_gap", float("inf")) <= buy_threshold),
                    "sell_ma120_warning_threshold": sell_warning,
                    "sell_ma120_strong_threshold": sell_strong,
                    "sell_warning": bool(snap.get("ma120_gap", float("-inf")) >= sell_warning),
                    "sell_strong": bool(snap.get("ma120_gap", float("-inf")) >= sell_strong),
                }
            )
            stocks[symbol] = snap
        assessment.payload["stocks"] = stocks
        return assessment, stocks

    def _korea_assessment(self) -> Assessment:
        balance = self.store.frame("freesis", "credit_balance")["value"]
        stocks: dict[str, dict[str, Any]] = {}
        for symbol, spec in self.config["kis"]["domestic"].items():
            prices = self.store.frame("kis", "close", symbol)["value"]
            credit_q = self.store.frame("kis", "credit_quantity", symbol)["value"]
            credit_r = self.store.frame("kis", "credit_rate", symbol)["value"]
            snap = stock_snapshot(prices, credit_q, credit_r)
            snap["name"] = spec["name"]
            stocks[symbol] = snap
        return assess_korea(
            balance,
            stocks,
            self.config["freesis"],
            self.config["kis"]["domestic"],
        )

    def _crypto_assessment(self, symbol: str) -> Assessment:
        def series(name: str, sym: str = symbol) -> pd.Series:
            return self.store.frame("binance", name, sym)["value"]

        btc = series("spot_close", "BTCUSDT") if symbol == "SOLUSDT" else None
        return assess_crypto(
            symbol,
            series("spot_close"),
            series("open_interest_value"),
            series("funding_rate"),
            series("long_short_ratio"),
            series("basis_annualized"),
            self.config["binance"],
            btc_price=btc,
            mark_price=series("mark_price"),
            index_price=series("index_price"),
            basis_percent=series("basis_percent"),
        )

    def _state_alert(
        self, key: str, assessment: Assessment, message: str, eligible: bool = True
    ) -> None:
        changed = self.store.set_state(key, assessment.state, assessment.payload)
        if not ((eligible and changed) or self.force_alert):
            return
        if key in self._sent_keys:
            # Already delivered for this key in this run; a second push would be
            # a duplicate of the same market report.
            return
        self._sent_keys.add(key)
        self.telegram.send(message)


def doctor(config: dict[str, Any]) -> dict[str, Any]:
    import os

    required = ["KIS_APP_KEY", "KIS_APP_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    env_status = {name: bool(os.getenv(name, "").strip()) for name in required}
    project = Path(config["_project_dir"])
    issues: list[str] = []
    for name, present in env_status.items():
        if not present:
            issues.append(f"GitHub Secret {name} 누락")
    if not (project / "pyproject.toml").exists():
        issues.append("pyproject.toml이 프로젝트 최상단에 없음")
    if not (project / ".github" / "workflows" / "monitor.yml").exists():
        issues.append(".github/workflows/monitor.yml이 프로젝트 최상단에 없음")
    return {
        "ok": not issues,
        "version": "1.8.0",
        "python_config": "OK",
        "database_parent_writable": (Path(config["_database_path"]).parent.exists()),
        "secrets": env_status,
        "config_yml_exists": (project / "config.yml").exists(),
        "freesis_bootstrap_exists": any(
            (project / path).exists() for path in config["freesis"].get("bootstrap_files", [])
        ),
        "issues": issues,
    }
