"""CSV schema shared by the sync (write) and analysis (read) sides."""
import csv
import io

HEADER = [
    "Date", "Account Name", "Institution", "Account Type", "Last4",
    "Description", "Transaction Type", "Category", "Flow Direction",
    "Amount", "Quantity", "Price", "Fees", "Security",
]


def rows_to_csv_text(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=HEADER)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in HEADER})
    return buf.getvalue()


def csv_text_to_rows(text):
    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader]


def dedup_key(row):
    """Stable identity for a raw transaction row, used to avoid re-adding
    a record that's already present in a month's CSV during an incremental
    sync merge."""
    return (row.get("Date"), row.get("Account Name"), row.get("Description"),
            row.get("Transaction Type"), str(row.get("Amount")))


def truthifi_record_to_row(rec, accounts):
    """Convert one raw Truthifi get_transactions record (as returned by the
    MCP tool, with keys like accountId/date/description/...) into a CSV row
    dict using the shared HEADER schema."""
    acct = accounts.get(rec.get("accountId"), {})
    return {
        "Date": rec.get("date", ""),
        "Account Name": acct.get("name", rec.get("accountId", "")),
        "Institution": acct.get("institution", ""),
        "Account Type": acct.get("type", ""),
        "Last4": acct.get("last4", ""),
        "Description": rec.get("description", ""),
        "Transaction Type": rec.get("transactionType", ""),
        "Category": rec.get("budgetFlowDetailCategory", ""),
        "Flow Direction": rec.get("budgetFlowType", ""),
        "Amount": rec.get("amount", ""),
        "Quantity": rec.get("quantity", ""),
        "Price": rec.get("price", ""),
        "Fees": rec.get("fees", ""),
        "Security": rec.get("security", ""),
    }
