"""Tests for the reporting layer: net cash flow and HTML formatting.

These cover the two things claude.md's report format depends on that are
easy to get silently wrong -- a percentage rendered as a dollar figure, and
a prior-window comparison presented as if it were fully covered.
"""
import bot.analysis as analysis
from bot.analysis import _fmt_money, _fmt_pct, _table, net_cash_flow


def _row(date, category, amount, flow="outflow", account="Chase Checking",
         acct_type="Banking", description="TEST MERCHANT"):
    return {
        "Date": date, "Account Name": account, "Institution": "Test",
        "Account Type": acct_type, "Last4": "0000", "Description": description,
        "Transaction Type": "purchase", "Category": category,
        "Flow Direction": flow, "Amount": str(amount), "Quantity": "",
        "Price": "", "Fees": "", "Security": "",
    }


def test_net_cash_flow_is_income_minus_outflow():
    rows = [
        _row("2026-01-05", "Paycheck/Salary/Wages", 5000, flow="inflow"),
        _row("2026-01-06", "Groceries", 200),
        _row("2026-01-07", "Food/Drink", 100),
    ]
    flow = net_cash_flow(rows)
    assert flow["income"] == 5000
    assert flow["outflow"] == 300
    assert flow["net"] == 4700


def test_net_cash_flow_can_be_negative():
    rows = [
        _row("2026-01-05", "Paycheck/Salary/Wages", 100, flow="inflow"),
        _row("2026-01-06", "Rent/Mortgage", 900),
    ]
    assert net_cash_flow(rows)["net"] == -800


def test_percent_columns_render_as_percentages_not_dollars():
    rows = [{"category": "Taxes", "latest_value": 20000.0,
             "pct_change_vs_prior": 359.12}]
    out = _table(
        rows, ["category", "latest_value", "pct_change_vs_prior"],
        ["Category", "Latest", "% Change"],
        percent_columns=("pct_change_vs_prior",),
    )
    assert "359.1%" in out
    assert "$359" not in out       # the bug this guards: a % shown as money
    assert "$20,000.00" in out     # real money columns still render as money


def test_fmt_helpers():
    assert _fmt_money(1234.5) == "$1,234.50"
    assert _fmt_pct(-93.87) == "-93.9%"


def test_render_flags_a_partly_covered_prior_window():
    report = {
        "as_of": "2026-09-20", "window_days": 365,
        "months_in_window": ["2026-09"], "missing_months": [],
        "totals": {"needs": 1.0, "wants": 1.0, "savings": 1.0,
                   "income": 5.0, "total_tracked_outflow": 3.0},
        "top_needs": [], "top_wants": [], "matrix": {},
        "trends": {"up": [], "down": []}, "category_breakdowns": {},
        "excluded_count": 0,
        "cash_flow": {"income": 5.0, "outflow": 3.0, "net": 2.0},
        "prior_cash_flow": {"income": 4.0, "outflow": 1.0, "net": 3.0,
                            "months_in_window": ["2025-09"],
                            "months_missing": ["2024-10"]},
    }
    out = analysis.render_html(report)
    assert "only partly covered" in out
    assert "2024-10" in out


def test_render_says_so_when_there_is_no_prior_window():
    report = {
        "as_of": "2026-09-20", "window_days": 365,
        "months_in_window": ["2026-09"], "missing_months": [],
        "totals": {"needs": 1.0, "wants": 1.0, "savings": 1.0,
                   "income": 5.0, "total_tracked_outflow": 3.0},
        "top_needs": [], "top_wants": [], "matrix": {},
        "trends": {"up": [], "down": []}, "category_breakdowns": {},
        "excluded_count": 0,
        "cash_flow": {"income": 5.0, "outflow": 3.0, "net": 2.0},
        "prior_cash_flow": None,
    }
    out = analysis.render_html(report)
    assert "no comparison is shown" in out


# -- balance table (one get_balance_history call + the static accounts map) --

ACCOUNTS = {
    "chk1": {"name": "Chase Checking", "type": "Banking (checking)"},
    "sav1": {"name": "BofA Savings", "type": "Banking (savings)"},
    "cc1": {"name": "Robinhood Credit Card", "type": "Credit"},
    "inv1": {"name": "Robinhood Brokerage", "type": "Investing"},
}


def test_balance_summary_keeps_only_cash_accounts():
    records = [
        {"accountId": "chk1", "initialBalance": 1000.0, "endingBalance": 1500.0},
        {"accountId": "sav1", "initialBalance": 5000.0, "endingBalance": 4000.0},
        {"accountId": "cc1", "initialBalance": -200.0, "endingBalance": -350.0},
        {"accountId": "inv1", "initialBalance": 10.0, "endingBalance": 20.0},
    ]
    out = analysis.balance_summary(records, ACCOUNTS)
    assert [r["account"] for r in out["accounts"]] == ["BofA Savings", "Chase Checking"]
    assert out["total_start"] == 6000.0
    assert out["total_end"] == 5500.0
    assert out["accounts"][1]["change"] == 500.0
    assert out["unknown_account_ids"] == []


def test_balance_summary_reports_unmapped_accounts_rather_than_dropping_them():
    records = [
        {"accountId": "chk1", "initialBalance": 1.0, "endingBalance": 2.0},
        {"accountId": "brand-new", "initialBalance": 99.0, "endingBalance": 99.0},
    ]
    out = analysis.balance_summary(records, ACCOUNTS)
    assert out["unknown_account_ids"] == ["brand-new"]
    assert len(out["accounts"]) == 1


def test_balance_summary_leaves_a_missing_balance_as_unknown():
    records = [{"accountId": "chk1", "initialBalance": None, "endingBalance": 5.0}]
    out = analysis.balance_summary(records, ACCOUNTS)
    assert out["accounts"][0]["change"] is None


def _report_with(balances):
    return {
        "as_of": "2026-09-20", "window_days": 365,
        "months_in_window": ["2026-09"], "missing_months": [],
        "totals": {"needs": 1.0, "wants": 1.0, "savings": 1.0,
                   "income": 5.0, "total_tracked_outflow": 3.0},
        "top_needs": [], "top_wants": [], "matrix": {},
        "trends": {"up": [], "down": []}, "category_breakdowns": {},
        "excluded_count": 0,
        "cash_flow": {"income": 5.0, "outflow": 3.0, "net": 2.0},
        "prior_cash_flow": None, "balances": balances,
    }


def test_render_includes_the_balance_table_and_its_total():
    out = analysis.render_html(_report_with(analysis.balance_summary(
        [{"accountId": "chk1", "initialBalance": 1000.0, "endingBalance": 1500.0}],
        ACCOUNTS)))
    assert "Checking &amp; savings balances" in out
    assert "$1,500.00" in out
    assert "Total" in out


def test_render_omits_the_balance_section_when_no_lookup_was_made():
    out = analysis.render_html(_report_with(None))
    assert "Checking &amp; savings balances" not in out


def test_render_names_unmapped_accounts():
    out = analysis.render_html(_report_with(analysis.balance_summary(
        [{"accountId": "chk1", "initialBalance": 1.0, "endingBalance": 2.0},
         {"accountId": "brand-new", "initialBalance": 9.0, "endingBalance": 9.0}],
        ACCOUNTS)))
    assert "brand-new" in out
    assert "accounts.json" in out
