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
    assert "Account balances are not reported" in out
