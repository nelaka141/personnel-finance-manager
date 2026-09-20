"""CLI for the personal finance bot described in claude.md.

This process never calls Truthifi itself (that MCP tool only exists inside
a Claude session) -- Claude fetches transactions and passes them to `sync`
as a JSON file. Everything Drive-side and all the budget/trend math is pure
Python here, using google-api-python-client (see drive_client.py).
"""
import argparse
import json
import sys
from datetime import date, datetime

from . import sync
from .analysis import render_html, run_analysis
from .local_archive import LocalArchive, import_into_drive
from .drive_client import (
    BROAD_DRIVE_SCOPES,
    DRIVE_FILE_SCOPE,
    ROOT_FOLDER_NAME,
    DriveAccessError,
    DriveClient,
    DriveConfigError,
    _build_credentials,
    _load_token,
    _requested_scopes,
)


def cmd_bootstrap(args):
    drive = DriveClient()
    last = sync.bootstrap_sync_state_from_existing_months(
        drive, from_date=args.from_date
    )
    print(json.dumps({"last_synced_date": last}))


def cmd_import_archive(args):
    """Upload monthly CSVs from a local directory into the Drive archive.

    The way to hand the existing history to this app's own OAuth client
    without re-fetching a single transaction from Truthifi: download the
    archive with a credential that can read it, then run this.
    """
    local = LocalArchive(args.from_dir)
    drive = DriveClient()
    report = import_into_drive(local, drive, months=args.months or None)
    report["warnings"] = drive.warnings
    print(json.dumps(report, indent=2))
    return 1 if report["failed"] else 0


def cmd_doctor(args):
    """Check the Drive credential and what this app can actually see.

    Run this first when a run fails on Drive: it separates the three
    failures that look alike from the outside -- a refresh the OAuth server
    rejects, an archive the drive.file scope hides, and an archive that is
    genuinely empty.
    """
    report = {"scope_check": {}, "access_check": {}, "warnings": []}

    token = _load_token()
    declared = token.get("scopes") or []
    requested = _requested_scopes(token)
    stale = sorted(set(declared) & BROAD_DRIVE_SCOPES)
    report["scope_check"] = {
        "declared_in_token_json": declared,
        "sent_on_refresh": requested,
        "stale_broad_scopes_dropped": stale,
        "has_refresh_token": bool(token.get("refresh_token")),
        "has_cached_access_token": bool(token.get("token")),
    }

    creds = _build_credentials()
    try:
        from google.auth.transport.requests import Request
    except ImportError:
        # No explicit refresh available; the access checks below exercise it.
        report["scope_check"]["refresh"] = "deferred to the first API call"
    else:
        try:
            creds.refresh(Request())
        except Exception as err:  # noqa: BLE001 -- the message is the finding
            report["scope_check"]["refresh"] = f"FAILED: {type(err).__name__}: {err}"
            if "invalid_scope" in str(err):
                report["scope_check"]["hint"] = (
                    "The OAuth server rejected the scopes this refresh asked "
                    f"for. The grant is {DRIVE_FILE_SCOPE}; re-authorize the "
                    "client and store the new token JSON in "
                    "GOOGLE_DRIVE_TOKEN_JSON."
                )
            print(json.dumps(report, indent=2))
            return 2
        report["scope_check"]["refresh"] = "ok"
        # None here means the token endpoint did not echo a scope back, not
        # that nothing was granted.
        report["scope_check"]["granted_scopes"] = list(creds.scopes) if creds.scopes else None

    drive = DriveClient()
    try:
        root = drive.root_folder_id()
        report["access_check"]["root_folder"] = {"name": ROOT_FOLDER_NAME, "id": root}
    except DriveAccessError as err:
        report["access_check"]["root_folder"] = f"UNREACHABLE: {err}"
        report["warnings"] = drive.warnings
        print(json.dumps(report, indent=2))
        return 2

    # Read the sync state first: it seeds the client's id map, so the month
    # checks below go by id exactly as a real run would.
    report["access_check"]["sync_state_file_id"] = drive.sync_state_file_id()
    try:
        report["access_check"]["last_synced_date"] = sync.get_last_synced_date(drive)
    except DriveAccessError as err:
        report["access_check"]["last_synced_date"] = f"UNREADABLE: {err}"

    months = drive.list_month_folders()
    readable, unreadable = [], []
    for month in months:
        try:
            if drive.month_csv_id(month):
                readable.append(month)
            else:
                unreadable.append(month)
        except DriveAccessError:
            unreadable.append(month)
    report["access_check"]["month_folders"] = months
    report["access_check"]["months_with_readable_csv"] = readable
    report["access_check"]["months_without_readable_csv"] = unreadable
    report["warnings"] = drive.warnings

    print(json.dumps(report, indent=2))
    return 0 if readable else 2


def cmd_get_sync_state(args):
    drive = DriveClient()
    print(json.dumps({"last_synced_date": sync.get_last_synced_date(drive)}))


def cmd_sync(args):
    with open(args.transactions_json) as f:
        records = json.load(f)
    drive = DriveClient()
    report = sync.merge_new_transactions(records, drive)
    if args.advance_watermark_to:
        sync.set_last_synced_date(args.advance_watermark_to, drive)
        report["last_synced_date"] = args.advance_watermark_to
    print(json.dumps(report, indent=2))


def cmd_analyze(args):
    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else date.today()
    store = LocalArchive(args.from_dir) if args.from_dir else DriveClient()
    report = run_analysis(as_of=as_of, window_days=args.window_days, drive=store)
    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(report, f, indent=2, default=str)
    html_report = render_html(report)
    if args.html_out:
        with open(args.html_out, "w") as f:
            f.write(html_report)
    else:
        print(html_report)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="bot")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("bootstrap-sync-state", help="Initialize sync_state.json from existing Drive archive")
    p.add_argument("--from-date", help="YYYY-MM-DD to use as the watermark instead of deriving it from the archived CSVs")
    p.set_defaults(func=cmd_bootstrap)

    p = sub.add_parser("doctor", help="Check the Drive credential and what this app can see under drive.file")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("get-sync-state", help="Print the current last_synced_date")
    p.set_defaults(func=cmd_get_sync_state)

    p = sub.add_parser("sync", help="Merge freshly-fetched Truthifi records into Drive (Phase 1)")
    p.add_argument("--transactions-json", required=True, help="Path to a JSON file: a list of raw Truthifi get_transactions records")
    p.add_argument("--advance-watermark-to", help="YYYY-MM-DD to set as the new last_synced_date after a successful merge")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("import-archive", help="Upload monthly CSVs from a local directory into Drive (no Truthifi calls)")
    p.add_argument("--from-dir", required=True, help="Directory holding transactions_YYYY-MM.csv, nested in YYYY-MM/ folders or flat")
    p.add_argument("--months", nargs="*", help="Limit to these YYYY-MM months (default: every month found)")
    p.set_defaults(func=cmd_import_archive)

    p = sub.add_parser("analyze", help="Run the rolling-window budget + trend analysis (Phase 2)")
    p.add_argument("--as-of", help="YYYY-MM-DD, defaults to today")
    p.add_argument("--from-dir", help="Read the monthly CSVs from this local directory instead of Drive")
    p.add_argument("--window-days", type=int, default=365)
    p.add_argument("--json-out", help="Write the raw report as JSON to this path")
    p.add_argument("--html-out", help="Write the rendered HTML report to this path (default: stdout)")
    p.set_defaults(func=cmd_analyze)

    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except (DriveAccessError, DriveConfigError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
