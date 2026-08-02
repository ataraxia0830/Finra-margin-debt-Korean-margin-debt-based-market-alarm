import pandas as pd

from market_alarm.collectors.freesis import normalize, parse_official_json


def test_freesis_normalize_korean_columns():
    raw = pd.DataFrame(
        {
            "일자": ["2026/07/29", "2026/07/30"],
            "신용거래융자-전체": ["32,100,000", "32,200,000"],
            "신용거래융자-유가증권": ["20,000,000", "20,100,000"],
        }
    )
    result = normalize(raw)
    assert result.iloc[-1]["credit_balance"] == 32_200_000
    assert result.index[-1].strftime("%Y-%m-%d") == "2026-07-30"


def test_freesis_official_json_parser_uses_total_credit_column():
    result = parse_official_json(
        {
            "unit": "",
            "ds1": [
                {"TMPV1": "20260529", "TMPV2": 38_022_681, "TMPV3": 28_024_472},
                {"TMPV1": "20260630", "TMPV2": 37_328_228, "TMPV3": 29_234_568},
            ],
        }
    )
    assert result.index[-1].strftime("%Y-%m-%d") == "2026-06-30"
    assert result.iloc[-1]["credit_balance"] == 37_328_228
