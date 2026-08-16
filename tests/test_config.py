from market_alarm.config import load_config


def test_v12_finra_sell_config_is_migrated(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        "finra:\n"
        "  sell_warning_relative_drop: -15\n"
        "  sell_arm_relative_drop: -20\n",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config["finra"]["sell_warning_yoy_drop_points"] == 10
    assert config["finra"]["sell_strong_yoy_drop_points"] == 15
    assert "sell_warning_relative_drop" not in config["finra"]
    assert "sell_strong_relative_drop" not in config["finra"]
    assert "sell_arm_relative_drop" not in config["finra"]


def test_legacy_korea_relative_drop_is_migrated_to_percentage_points(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        "freesis:\n  sell_relative_drop: -15\n",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config["freesis"]["sell_yoy_drop_points"] == 15
    assert "sell_relative_drop" not in config["freesis"]


def test_legacy_finra_overheat_60_is_migrated_to_final_50(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("finra:\n  overheat_yoy: 60\n", encoding="utf-8")

    config = load_config(config_file)

    assert config["finra"]["overheat_yoy"] == 50
