from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path = "config.yml") -> dict[str, Any]:
    load_dotenv()
    project = Path(__file__).resolve().parents[2]
    example = project / "config.example.yml"
    with example.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    requested = Path(path)
    if requested.exists():
        with requested.open(encoding="utf-8") as f:
            override = yaml.safe_load(f) or {}
        legacy_finra = override.get("finra", {})
        # Early releases shipped +60 as the FINRA overheat gate.  The user's
        # final rule is +50.  Without this migration an existing config.yml
        # silently overrides the corrected example config and can label a
        # recent +50~59% episode as a sideways/normal regime.
        if legacy_finra.get("overheat_yoy") == 60:
            legacy_finra["overheat_yoy"] = 50
        # Older configurations expressed sell thresholds as a relative change
        # in the YoY number.  Preserve their numeric thresholds but migrate the
        # meaning to the user's intended percentage-point drop.
        legacy_v12_pair = (
            legacy_finra.get("sell_warning_relative_drop") == -15
            and legacy_finra.get("sell_arm_relative_drop") == -20
            and "sell_strong_relative_drop" not in legacy_finra
        )
        if "sell_warning_yoy_drop_points" not in legacy_finra:
            old_warning = legacy_finra.get("sell_warning_relative_drop")
            if old_warning is not None:
                legacy_finra["sell_warning_yoy_drop_points"] = (
                    10 if legacy_v12_pair else abs(float(old_warning))
                )
        if "sell_strong_yoy_drop_points" not in legacy_finra:
            old_strong = legacy_finra.get(
                "sell_strong_relative_drop", legacy_finra.get("sell_arm_relative_drop")
            )
            if old_strong is not None:
                # v1.2's -20 scheduler was superseded by the final 15%p rule.
                legacy_finra["sell_strong_yoy_drop_points"] = (
                    15 if float(old_strong) == -20 else abs(float(old_strong))
                )
        for old_key in (
            "sell_warning_relative_drop",
            "sell_strong_relative_drop",
            "sell_arm_relative_drop",
        ):
            legacy_finra.pop(old_key, None)

        legacy_freesis = override.get("freesis", {})
        if "sell_yoy_drop_points" not in legacy_freesis:
            old_korea = legacy_freesis.get("sell_relative_drop")
            if old_korea is not None:
                legacy_freesis["sell_yoy_drop_points"] = abs(float(old_korea))
        legacy_freesis.pop("sell_relative_drop", None)
        config = deep_merge(config, override)
    config["_project_dir"] = str(project)
    config["_database_path"] = str((project / config["database"]).resolve())
    return config


def env(name: str, required: bool = False) -> str:
    value = os.getenv(name, "").strip()
    if required and not value:
        raise RuntimeError(f"필수 환경변수 {name}가 없습니다.")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
