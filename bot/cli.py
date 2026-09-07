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
from .drive_client import DriveClient


def cmd_bootstrap(args):
    drive = DriveClient()
    last = sync.bootstrap_sync_state_from_existing_months(drive)
    print(json.dumps({"last_synced_date": last}))


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
    drive = DriveClient()
    report = run_analysis(as_of=as_of, window_days=args.window_days, drive=drive)
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
    p.set_defaults(func=cmd_bootstrap)

    p = sub.add_parser("get-sync-state", help="Print the current last_synced_date")
    p.set_defaults(func=cmd_get_sync_state)

    p = sub.add_parser("sync", help="Merge freshly-fetched Truthifi records into Drive (Phase 1)")
    p.add_argument("--transactions-json", required=True, help="Path to a JSON file: a list of raw Truthifi get_transactions records")
    p.add_argument("--advance-watermark-to", help="YYYY-MM-DD to set as the new last_synced_date after a successful merge")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("analyze", help="Run the rolling-window budget + trend analysis (Phase 2)")
    p.add_argument("--as-of", help="YYYY-MM-DD, defaults to today")
    p.add_argument("--window-days", type=int, default=365)
    p.add_argument("--json-out", help="Write the raw report as JSON to this path")
    p.add_argument("--html-out", help="Write the rendered HTML report to this path (default: stdout)")
    p.set_defaults(func=cmd_analyze)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
