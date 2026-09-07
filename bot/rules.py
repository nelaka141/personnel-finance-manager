"""Expense Management & Budget Rules from claude.md, as code.

Keep this in sync with claude.md by hand: whenever the rules section of
claude.md changes, this file should change with it in the same commit.
"""
from datetime import datetime, timedelta

NEEDS_CATEGORIES = {
    "Rent/Mortgage", "Utilities/Bills", "Groceries", "Insurance/Financial",
    "Loans", "Health", "Transportation", "Home Improvement", "Education",
    "Childcare", "Taxes", "Credit Card",
}
WANTS_CATEGORIES = {
    "Food/Drink", "Shopping", "Entertainment", "Personal Care", "Travel",
    "Pets", "Business/Office", "Charitable Giving",
}
# "Other Expenses" is split by keyword rather than a flat bucket assignment.
OTHER_EXPENSES_NEEDS_KEYWORDS = ("USPS", "PASSPORT", "DMV", " IRS ", "TAX")

INCOME_CATEGORIES = {"Paycheck/Salary/Wages", "Other Income", "Deposits"}
IGNORED_CATEGORIES = {"Investment Income", ""}  # "" = blank-category investment trades
TRANSFER_CATEGORIES = {"Transfer", "CreditCardPaymentSent", "CreditCardPaymentReceived"}

NEAR_DATE_WINDOW_DAYS = 3

# Known recurring card payoffs that show up mislabeled under a normal
# expense category instead of being tagged as a transfer. Matched by
# description substring alone (case-insensitive) -- kept in addition to the
# generalized amount+nearby-date match in classify() because the paired
# CreditCardPaymentReceived record on the target card's own account isn't
# always present (e.g. an account whose Truthifi history doesn't go back
# far enough to have synced that side yet).
KNOWN_MISLABELED_PAYOFF_DESCRIPTIONS = (
    "SAMS CLUB PAYMENT",  # Sam's Club Mastercard payoff, shows up as Shopping
    "ONLINE SCHEDULED PAYMENT TO ACCT# 7298",  # BofA Travel Rewards Visa payoff, shows up as Loans
)


def _parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d")


def _amounts_match(a, b, eps=0.01):
    try:
        return abs(float(a) - float(b)) < eps
    except (TypeError, ValueError):
        return False


def _near(date_a, date_b, days=NEAR_DATE_WINDOW_DAYS):
    try:
        return abs((_parse_date(date_a) - _parse_date(date_b)).days) <= days
    except (TypeError, ValueError):
        return False


def other_expenses_bucket(description):
    desc_upper = f" {description.upper()} "
    if any(kw in desc_upper for kw in OTHER_EXPENSES_NEEDS_KEYWORDS):
        return "Needs"
    return "Wants"


def classify(rows):
    """Apply de-duplication + 50/30/20 categorization to a list of CSV row
    dicts (see csv_io.HEADER). Returns a dict:
      {
        "needs": [rows...], "wants": [rows...],
        "savings_total": float,
        "income_total": float,
        "excluded": [rows...],   # de-duplicated internal transfers/payoffs
      }
    Each row in needs/wants gets a "_signed_amount" field: positive for a
    normal charge, negative for a refund/credit against that category (so
    totals net out correctly).
    """
    cc_received = [
        r for r in rows if r.get("Category") == "CreditCardPaymentReceived"
    ]
    savings_transfer_inflows = [
        r for r in rows
        if r.get("Category") == "Transfer" and r.get("Flow Direction") == "inflow"
        and "saving" in (r.get("Account Type") or "").lower()
    ]
    rh_card_payments = [
        r for r in rows
        if r.get("Category") == "CreditCardPaymentSent"
        and "robinhood" in (r.get("Account Name") or "").lower()
    ]

    needs, wants, excluded = [], [], []
    savings_total = 0.0
    income_total = 0.0

    for row in rows:
        category = row.get("Category", "")
        flow = row.get("Flow Direction", "")
        amount = row.get("Amount", 0)
        description = row.get("Description", "")

        if category in IGNORED_CATEGORIES:
            continue

        if category in ("CreditCardPaymentSent", "CreditCardPaymentReceived"):
            excluded.append(row)
            continue

        if category == "Transfer":
            if flow == "inflow":
                excluded.append(row)  # mirror side of some other outflow
                continue
            desc_lower = description.lower()
            if "robinhood" in desc_lower:
                is_card_payoff = any(
                    _amounts_match(amount, p["Amount"]) and _near(row["Date"], p["Date"])
                    for p in rh_card_payments
                )
                if is_card_payoff:
                    excluded.append(row)
                else:
                    savings_total += float(amount)
                continue
            is_to_savings = any(
                _amounts_match(amount, s["Amount"]) and _near(row["Date"], s["Date"])
                for s in savings_transfer_inflows
            )
            if is_to_savings:
                savings_total += float(amount)
            else:
                excluded.append(row)  # ATM withdrawal, Zelle-to-self, check, etc.
            continue

        # Mislabeled card payoff hiding under a normal category: matches a
        # CreditCardPaymentReceived line by amount + nearby date, or matches
        # one of the known recurring patterns by description alone.
        desc_upper = description.upper()
        is_known_payoff_pattern = any(
            pat in desc_upper for pat in KNOWN_MISLABELED_PAYOFF_DESCRIPTIONS
        )
        is_mislabeled_payoff = is_known_payoff_pattern or any(
            _amounts_match(amount, c["Amount"]) and _near(row["Date"], c["Date"])
            for c in cc_received
        )
        if is_mislabeled_payoff:
            excluded.append(row)
            continue

        if category in INCOME_CATEGORIES:
            if flow == "inflow":
                income_total += float(amount)
            continue

        bucket = None
        if category in NEEDS_CATEGORIES:
            bucket = "needs"
        elif category in WANTS_CATEGORIES:
            bucket = "wants"
        elif category == "Other Expenses":
            bucket = "needs" if other_expenses_bucket(description) == "Needs" else "wants"

        if bucket is None:
            continue

        signed = float(amount) if flow == "outflow" else -float(amount)
        row = dict(row)
        row["_signed_amount"] = signed
        (needs if bucket == "needs" else wants).append(row)

    return {
        "needs": needs,
        "wants": wants,
        "savings_total": savings_total,
        "income_total": income_total,
        "excluded": excluded,
    }
