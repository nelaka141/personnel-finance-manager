"""A local directory standing in for the Drive archive.

Exists so the 21 months of history already in Drive never have to be pulled
from Truthifi again. The historical CSVs were written by the Drive MCP
connector before this Python bot existed, so the bot's own OAuth client did
not create them and cannot see them under the `drive.file` scope -- but the
connector still can. That makes a local directory the handover point:
whichever credential can read the archive downloads it, and the bot works
from the directory.

Two uses:

- `analyze --from-dir DIR` runs the whole Phase 2 report off local CSVs,
  needing no Drive credential at all.
- `import-archive --from-dir DIR` uploads those CSVs through the bot's own
  client, so it owns them from then on and plain Drive-backed runs work
  again. Neither path calls Truthifi.

Only the handful of methods analysis and sync actually use are implemented,
which is what lets this be passed wherever a DriveClient is expected.
"""
import json
import os
import re

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
SYNC_STATE_NAME = "sync_state.json"


class LocalArchive:
    """Directory-backed stand-in for DriveClient.

    A month's CSV is looked for both at `DIR/YYYY-MM/transactions_YYYY-MM.csv`
    (the Drive layout) and at `DIR/transactions_YYYY-MM.csv`, so a download
    works whether it kept the folder-per-month nesting or was flattened.
    """

    def __init__(self, root, create=False):
        self.root = os.path.abspath(root)
        if create:
            os.makedirs(self.root, exist_ok=True)
        elif not os.path.isdir(self.root):
            raise FileNotFoundError(f"{self.root} is not a directory")
        self.warnings = []

    def _csv_name(self, month):
        return f"transactions_{month}.csv"

    def _candidate_paths(self, month):
        name = self._csv_name(month)
        return [
            os.path.join(self.root, month, name),
            os.path.join(self.root, name),
        ]

    def month_csv_path(self, month):
        for path in self._candidate_paths(month):
            if os.path.isfile(path):
                return path
        return None

    # -- the DriveClient surface analysis and sync rely on -----------------

    def read_month_csv(self, month):
        path = self.month_csv_path(month)
        if path is None:
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def write_month_csv(self, month, text):
        folder = os.path.join(self.root, month)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, self._csv_name(month))
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def list_month_folders(self):
        """Months with a CSV here, in either layout."""
        months = set()
        for entry in os.listdir(self.root):
            if MONTH_RE.match(entry) and os.path.isdir(os.path.join(self.root, entry)):
                if self.month_csv_path(entry):
                    months.add(entry)
                continue
            match = re.match(r"^transactions_(\d{4}-\d{2})\.csv$", entry)
            if match:
                months.add(match.group(1))
        return sorted(months)

    def read_sync_state(self):
        path = os.path.join(self.root, SYNC_STATE_NAME)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def write_sync_state(self, state):
        current = self.read_sync_state() or {}
        merged = dict(current)
        merged.update(state)
        path = os.path.join(self.root, SYNC_STATE_NAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
        return merged


def import_into_drive(local, drive, months=None):
    """Upload each month's CSV from `local` into the Drive archive.

    Returns a report of what moved. Months already byte-identical in Drive
    are skipped, so this is safe to re-run and safe to interrupt.
    """
    months = months or local.list_month_folders()
    uploaded, skipped, failed = [], [], {}
    for month in months:
        text = local.read_month_csv(month)
        if text is None:
            failed[month] = "no CSV in the local directory"
            continue
        try:
            if drive.read_month_csv(month) == text:
                skipped.append(month)
                continue
        except Exception:  # noqa: BLE001 -- unreadable is a reason to upload
            pass
        try:
            drive.write_month_csv(month, text)
            uploaded.append(month)
        except Exception as err:  # noqa: BLE001 -- reported per month
            failed[month] = f"{type(err).__name__}: {err}"
    return {
        "uploaded": uploaded,
        "already_identical": skipped,
        "failed": failed,
        "months_seen_locally": months,
    }
