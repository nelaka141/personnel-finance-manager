"""Phase 2 (see claude.md): read transactions back out of Google Drive
(never Truthifi) and run the budget + trend analysis over a rolling window.
"""
import html
from collections import defaultdict
from datetime import date, datetime, timedelta

from .csv_io import csv_text_to_rows
from .drive_client import DriveClient
from .rules import classify

DEFAULT_WINDOW_DAYS = 365
# Categories to always render a full-window per-merchant breakdown table for,
# in addition to the top-15 Needs/Wants lists (see claude.md Output Format).
BREAKDOWN_CATEGORIES = (
    "Utilities/Bills", "Entertainment", "Home Improvement", "Shopping",
    "Food/Drink", "Groceries",
)


def _months_between(start, end):
    months = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        months.append(cur.strftime("%Y-%m"))
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return months


def load_window_rows(as_of=None, window_days=DEFAULT_WINDOW_DAYS, drive=None):
    """Downloads every monthly CSV touched by the window and returns
    (rows, months, missing_months).

    The window is month-granular, not day-granular: every calendar month
    that the [as_of - window_days, as_of] range overlaps at all is included
    in full (matching claude.md's "the (up to 13) calendar months touched by
    the rolling-year window" and "download them ... and concatenate" -- the
    matrix and trend tables are meant to show whole-month totals). The only
    day-level clip is the upper bound, so a still-in-progress current month
    doesn't pull in future-dated rows.
    """
    drive = drive or DriveClient()
    as_of = as_of or date.today()
    start = as_of - timedelta(days=window_days)
    months = _months_between(start, as_of)

    rows, missing = [], []
    for month in months:
        text = drive.read_month_csv(month)
        if text is None:
            missing.append(month)
            continue
        for r in csv_text_to_rows(text):
            if r.get("Date", "") <= as_of.isoformat():
                rows.append(r)
    return rows, months, missing


def top_n(bucket_rows, n=15):
    return sorted(bucket_rows, key=lambda r: -r["_signed_amount"])[:n]


_MERCHANT_STOP_WORDS = {"PPD", "WEB", "ID:", "ID"}


def normalize_merchant(description):
    """Strips per-transaction reference numbers/IDs off a raw transaction
    description to get a stable merchant key -- e.g. "MIDDLESEXWATER
    UTILITYPMT 9993047 WEB ID: 0000007041" and "MIDDLESEXWATER UTILITYPMT
    2810973 WEB ID: 0000007041" (different reference numbers, same biller,
    different months) both normalize to "MIDDLESEXWATER UTILITYPMT" so they
    aggregate as one item instead of fragmenting into one row per month.
    Also drops an immediately-repeated word (e.g. "Sunrun Sunrun ...") that
    shows up in some of Truthifi's own description formats for one biller."""
    out = []
    for tok in description.split():
        bare = tok.rstrip(":")
        if bare.upper() in _MERCHANT_STOP_WORDS:
            break
        if len(tok) >= 4 and any(c.isdigit() for c in tok):
            break
        if out and out[-1].upper() == tok.upper():
            continue
        out.append(tok)
    return " ".join(out) if out else description


def category_item_breakdown(all_bucket_rows, category):
    """Aggregates every line item in `category` across the whole window by
    normalized merchant (not just the top 15), for a full-window per-item
    total. Returns a list of {description, total, count} sorted by total
    descending. Grouping is case-insensitive on the normalized merchant key
    (Truthifi doesn't always capitalize a given biller's name the same way
    across transaction types), while the displayed description keeps the
    first-seen casing."""
    totals = defaultdict(lambda: {"display": None, "total": 0.0, "count": 0})
    for r in all_bucket_rows:
        if r["Category"] != category:
            continue
        merchant = normalize_merchant(r["Description"])
        key = merchant.upper()
        entry = totals[key]
        if entry["display"] is None:
            entry["display"] = merchant
        entry["total"] += r["_signed_amount"]
        entry["count"] += 1
    rows = [
        {"description": v["display"], "total": v["total"], "count": v["count"]}
        for v in totals.values()
    ]
    return sorted(rows, key=lambda r: -r["total"])


def month_category_matrix(rows):
    """rows: needs+wants rows (each has Category, Date, _signed_amount).
    Returns {category: {month: total}}."""
    matrix = defaultdict(lambda: defaultdict(float))
    for r in rows:
        month = r["Date"][:7]
        matrix[r["Category"]][month] += r["_signed_amount"]
    return matrix


def bucket_totals_by_month(needs_rows, wants_rows, savings_by_month):
    totals = defaultdict(lambda: {"Needs": 0.0, "Wants": 0.0, "Savings": 0.0})
    for r in needs_rows:
        totals[r["Date"][:7]]["Needs"] += r["_signed_amount"]
    for r in wants_rows:
        totals[r["Date"][:7]]["Wants"] += r["_signed_amount"]
    for month, amt in savings_by_month.items():
        totals[month]["Savings"] += amt
    return totals


def trend_report(matrix, months, top_k=5):
    """matrix: {category: {month: total}}. months: sorted list of the
    calendar months in the window (oldest first). Returns categories ranked
    by movement in the most recent month vs prior month and vs trailing
    3-month average."""
    if len(months) < 2:
        return {"up": [], "down": []}
    latest, prior = months[-1], months[-2]
    trailing = months[-4:-1] if len(months) >= 4 else months[:-1]

    rows = []
    for category, by_month in matrix.items():
        latest_val = by_month.get(latest, 0.0)
        prior_val = by_month.get(prior, 0.0)
        trailing_avg = (sum(by_month.get(m, 0.0) for m in trailing) / len(trailing)) if trailing else 0.0
        dollar_change = latest_val - prior_val
        pct_change = (dollar_change / prior_val * 100) if prior_val else (100.0 if latest_val else 0.0)
        pct_vs_trailing = ((latest_val - trailing_avg) / trailing_avg * 100) if trailing_avg else (100.0 if latest_val else 0.0)
        rows.append({
            "category": category, "latest_month": latest, "latest_value": latest_val,
            "prior_value": prior_val, "dollar_change": dollar_change,
            "pct_change_vs_prior": pct_change, "trailing_3mo_avg": trailing_avg,
            "pct_change_vs_trailing": pct_vs_trailing,
        })

    up = sorted([r for r in rows if r["dollar_change"] > 0], key=lambda r: -r["dollar_change"])[:top_k]
    down = sorted([r for r in rows if r["dollar_change"] < 0], key=lambda r: r["dollar_change"])[:top_k]
    return {"up": up, "down": down}


def run_analysis(as_of=None, window_days=DEFAULT_WINDOW_DAYS, drive=None):
    drive = drive or DriveClient()
    rows, months, missing_months = load_window_rows(as_of, window_days, drive)
    result = classify(rows)
    needs, wants = result["needs"], result["wants"]

    needs_total = sum(r["_signed_amount"] for r in needs)
    wants_total = sum(r["_signed_amount"] for r in wants)
    savings_total = result["savings_total"]

    # Approximate month-by-month savings split evenly isn't right; instead
    # recompute savings per month directly from the excluded/transfer pass.
    # classify() only returns a window-wide savings_total, so for the
    # per-month matrix we treat Savings as a single window-level figure and
    # only break Needs/Wants out by month (Savings trend needs the raw
    # per-month transfer rows, which is a possible follow-up enhancement).
    matrix = month_category_matrix(needs + wants)
    sorted_months = sorted(set(months) - set(missing_months))
    trends = trend_report(matrix, sorted_months)

    return {
        "as_of": (as_of or date.today()).isoformat(),
        "window_days": window_days,
        "months_in_window": sorted_months,
        "missing_months": missing_months,
        "totals": {
            "needs": needs_total, "wants": wants_total, "savings": savings_total,
            "income": result["income_total"],
            "total_tracked_outflow": needs_total + wants_total + savings_total,
        },
        "top_needs": top_n(needs),
        "top_wants": top_n(wants),
        "matrix": {cat: dict(by_month) for cat, by_month in matrix.items()},
        "trends": trends,
        "category_breakdowns": {
            cat: category_item_breakdown(needs + wants, cat)
            for cat in BREAKDOWN_CATEGORIES
        },
        "excluded_count": len(result["excluded"]),
    }


def _fmt_money(x):
    return f"${x:,.2f}"


def _table(rows, columns, headers):
    out = ["<table border='1' cellpadding='4' cellspacing='0'>", "<tr>"]
    out += [f"<th>{html.escape(h)}</th>" for h in headers]
    out.append("</tr>")
    for r in rows:
        out.append("<tr>")
        for c in columns:
            val = r.get(c, "")
            if isinstance(val, float):
                val = _fmt_money(val)
            out.append(f"<td>{html.escape(str(val))}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "\n".join(out)


def render_html(report):
    t = report["totals"]
    parts = [
        f"<h2>Personal Finance Summary — {report['as_of']} (rolling {report['window_days']} days)</h2>",
        "<h3>Totals</h3>",
        "<ul>",
        f"<li>Needs: {_fmt_money(t['needs'])}</li>",
        f"<li>Wants: {_fmt_money(t['wants'])}</li>",
        f"<li>Savings: {_fmt_money(t['savings'])}</li>",
        f"<li>Income: {_fmt_money(t['income'])}</li>",
        f"<li>Total tracked outflow: {_fmt_money(t['total_tracked_outflow'])}</li>",
        "</ul>",
    ]
    if report["missing_months"]:
        parts.append(
            "<p><b>Note:</b> no data found in Drive for: "
            + ", ".join(report["missing_months"]) + "</p>"
        )

    parts.append("<h3>Top 15 — Needs</h3>")
    parts.append(_table(
        report["top_needs"], ["Date", "Description", "Category", "_signed_amount"],
        ["Date", "Merchant/Description", "Category", "Amount"],
    ))
    parts.append("<h3>Top 15 — Wants</h3>")
    parts.append(_table(
        report["top_wants"], ["Date", "Description", "Category", "_signed_amount"],
        ["Date", "Merchant/Description", "Category", "Amount"],
    ))

    months = report["months_in_window"]
    parts.append("<h3>Month &times; Category matrix</h3>")
    parts.append("<table border='1' cellpadding='4' cellspacing='0'><tr><th>Category</th>"
                  + "".join(f"<th>{m}</th>" for m in months) + "</tr>")
    for cat, by_month in sorted(report["matrix"].items()):
        parts.append("<tr><td>" + html.escape(cat) + "</td>"
                      + "".join(f"<td>{_fmt_money(by_month.get(m, 0.0))}</td>" for m in months)
                      + "</tr>")
    parts.append("</table>")

    for category, items in report["category_breakdowns"].items():
        parts.append(f"<h3>{html.escape(category)} — full-window breakdown by item</h3>")
        parts.append(_table(
            items, ["description", "total", "count"],
            ["Merchant/Description", "Total", "# Transactions"],
        ))

    for label, key in (("Trending Up", "up"), ("Trending Down", "down")):
        parts.append(f"<h3>{label}</h3>")
        parts.append(_table(
            report["trends"][key],
            ["category", "latest_value", "dollar_change", "pct_change_vs_prior", "pct_change_vs_trailing"],
            ["Category", "Latest Month $", "$ Change vs Prior Month",
             "% Change vs Prior Month", "% Change vs Trailing 3-mo Avg"],
        ))

    return "\n".join(parts)
