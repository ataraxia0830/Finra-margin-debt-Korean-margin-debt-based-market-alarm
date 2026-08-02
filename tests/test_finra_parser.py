from io import BytesIO

import pandas as pd

from market_alarm.collectors.finra import parse_excel


def test_parse_finra_excel():
    source = pd.DataFrame(
        [
            ["Customer Margin Balances", None, None, None],
            [
                "Month/Year",
                "Debit Balances in Customers' Securities Margin Accounts",
                "Free Credit Balances in Customers' Cash Accounts",
                "Free Credit Balances in Customers' Securities Margin Accounts",
            ],
            ["Jan-25", "1,000", "100", "200"],
            ["Feb-25", "1,100", "110", "220"],
        ]
    )
    bio = BytesIO()
    source.to_excel(bio, index=False, header=False)
    result = parse_excel(bio.getvalue())
    assert list(result["debit_balance"]) == [1000.0, 1100.0]
    assert result.index[-1].strftime("%Y-%m-%d") == "2025-02-28"

