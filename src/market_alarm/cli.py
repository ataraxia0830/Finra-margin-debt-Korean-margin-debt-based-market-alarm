from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from market_alarm.config import load_config
from market_alarm.service import Monitor, doctor
from market_alarm.telegram import Telegram


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="주식·코인 시장 신호 알람")
    parser.add_argument("--config", default="config.yml")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run")
    run.add_argument(
        "task",
        choices=["finra", "us", "korea", "crypto", "month-end", "monthly", "all"],
    )
    run.add_argument("--force-alert", action="store_true")
    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--strict", action="store_true")
    sub.add_parser("test-telegram")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.config)
        if args.command == "doctor":
            report = doctor(config)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if args.strict and not report["ok"]:
                return 1
        elif args.command == "test-telegram":
            telegram = Telegram(config)
            telegram.require_configured()
            telegram.send("[시장 알람 시스템 점검]\nTelegram 연결: 정상\n버전: v1.8.0")
            print("Telegram connection: OK")
        else:
            print(Monitor(config, force_alert=args.force_alert).run(args.task))
        return 0
    except Exception as exc:
        logging.exception("작업 실패")
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
