"""Phase 1 (see claude.md): merge newly-fetched Truthifi transactions into
the per-month CSVs on Google Drive, and advance the sync watermark.

This module does NOT call Truthifi itself -- that MCP tool is only reachable
from inside a Claude session. The caller (Claude, following claude.md) fetches
the raw transactions for the window since the last sync and passes them here
as a list of records (the same shape get_transactions returns) via
merge_new_transactions().
"""
import json
import os
from collections import defaultdict

from .csv_io import csv_text_to_rows, dedup_key, rows_to_csv_text, truthifi_record_to_row
from .drive_client import DriveClient

ACCOUNTS_PATH = os.path.join(os.path.dirname(__file__), "accounts.json")


def load_accounts():
    with open(ACCOUNTS_PATH) as f:
        return json.load(f)


def merge_new_transactions(records, drive=None):
    """records: list of raw Truthifi transaction dicts (accountId/date/
    description/transactionType/budgetFlowDetailCategory/budgetFlowType/
    amount/quantity/price/fees/security).

    Returns a report dict: {months_updated: [...], rows_added: N,
    rows_skipped_duplicate: N}.
    """
    drive = drive or DriveClient()
    accounts = load_accounts()

    by_month = defaultdict(list)
    for rec in records:
        date = rec.get("date", "")
        month = date[:7]  # "YYYY-MM"
        if not month:
            continue
        by_month[month].append(truthifi_record_to_row(rec, accounts))

    months_updated = []
    rows_added = 0
    rows_skipped = 0

    for month, new_rows in sorted(by_month.items()):
        existing_text = drive.read_month_csv(month)
        existing_rows = csv_text_to_rows(existing_text) if existing_text else []
        existing_keys = {dedup_key(r) for r in existing_rows}

        added_this_month = 0
        for row in new_rows:
            key = dedup_key(row)
            if key in existing_keys:
                rows_skipped += 1
                continue
            existing_rows.append(row)
            existing_keys.add(key)
            added_this_month += 1
            rows_added += 1

        if added_this_month == 0:
            continue

        existing_rows.sort(key=lambda r: (r.get("Date", ""), r.get("Account Name", "")))
        drive.write_month_csv(month, rows_to_csv_text(existing_rows))
        months_updated.append(month)

    return {
        "months_updated": months_updated,
        "rows_added": rows_added,
        "rows_skipped_duplicate": rows_skipped,
    }


def get_last_synced_date(drive=None):
    drive = drive or DriveClient()
    state = drive.read_sync_state()
    return state.get("last_synced_date") if state else None


def set_last_synced_date(date_str, drive=None):
    drive = drive or DriveClient()
    drive.write_sync_state({"last_synced_date": date_str})


def bootstrap_sync_state_from_existing_months(drive=None):
    """One-time helper: if sync_state.json doesn't exist yet, derive
    last_synced_date from the newest date already present across the
    monthly CSVs already archived in Drive."""
    drive = drive or DriveClient()
    if drive.read_sync_state() is not None:
        return get_last_synced_date(drive)

    root = drive.root_folder_id()
    res = drive.service.files().list(
        q=f"'{root}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        fields="files(name)",
    ).execute()
    months = sorted(f["name"] for f in res.get("files", []))
    if not months:
        return None
    latest_month = months[-1]
    text = drive.read_month_csv(latest_month)
    if not text:
        return None
    rows = csv_text_to_rows(text)
    latest_date = max((r.get("Date", "") for r in rows if r.get("Date")), default=None)
    if latest_date:
        set_last_synced_date(latest_date, drive)
    return latest_date
