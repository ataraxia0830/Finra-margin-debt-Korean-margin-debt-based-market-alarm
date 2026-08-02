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
        # v1.2 used -15 as a warning and -20 as the sell scheduler.  Migrate
        # that exact legacy pair so an existing config.yml receives the new
        # -10 weak / -15 strong rule without manual cleanup.
        legacy_finra = override.get("finra", {})
        # Early releases shipped +60 as the FINRA overheat gate.  The user's
        # final rule is +50.  Without this migration an existing config.yml
        # silently overrides the corrected example config and can label a
        # recent +50~59% episode as a sideways/normal regime.
        if legacy_finra.get("overheat_yoy") == 60:
            legacy_finra["overheat_yoy"] = 50
        if (
            legacy_finra.get("sell_warning_relative_drop") == -15
            and legacy_finra.get("sell_arm_relative_drop") == -20
            and "sell_strong_relative_drop" not in legacy_finra
        ):
            legacy_finra["sell_warning_relative_drop"] = -10
            legacy_finra["sell_strong_relative_drop"] = -15
            legacy_finra.pop("sell_arm_relative_drop", None)
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
