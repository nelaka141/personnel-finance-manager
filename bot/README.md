# Personal finance bot

Python implementation of the two-phase routine described in `../claude.md`.
This code is the deterministic half (Drive I/O, dedup rules, 50/30/20
categorization, month-over-month trend math); Truthifi and Gmail calls stay
MCP tool calls made by Claude, since those tools only exist inside a Claude
session. Claude is expected to run these commands as part of following
`claude.md`.

**Whenever the logic in `claude.md` changes, this code should change with
it in the same commit** -- `rules.py` in particular mirrors the "Expense
Management & Budget Rules" section by hand.

## Setup

```
pip install -r bot/requirements.txt
export GOOGLE_DRIVE_TOKEN_JSON='...'   # OAuth token JSON, see drive_client.py
```

Optional, and worth setting: `GOOGLE_DRIVE_ROOT_FOLDER_ID` pins the archive
folder by id so nothing depends on a name lookup. `GOOGLE_DRIVE_SYNC_STATE_FILE_ID`
does the same for `sync_state.json`, and `GOOGLE_DRIVE_SCOPES` overrides the
scopes sent on token refresh.

## The drive.file scope

The Drive grant is `drive.file`, not full drive access, which means this app
can only reach files it created itself or that were explicitly opened with it.
`drive_client.py` is built around that:

- A stale full-drive scope left in the token JSON is **not** re-requested on
  refresh. Asking for more than was granted makes Google reject the refresh
  outright with `invalid_scope`, which is what stopped the 2026-09-19 run
  before a single Drive call went out.
- Lookups go by id first (env var, then the `file_ids` map cached in
  `sync_state.json`), and by name only as a fallback, because a name query
  cannot see another app's files.
- A file that should exist but comes back 403/404 raises `DriveAccessError`
  rather than reading as "absent", so an unreadable month is never counted as
  a month with no spending. If nothing in the analysis window is readable, the
  run aborts instead of rendering a $0 report.
- Writes reuse a known file id, so a run can no longer leave a second
  `sync_state.json` behind when it fails to see the first one. Where several
  files do share a name, the most recently modified one wins and the
  duplicates are reported on stderr.

If the historical archive turns out to be invisible to this OAuth client
under the narrowed scope, **none of the options involve re-fetching anything
from Truthifi.** The 21 months from 2025-01 onward are still readable through
the Drive MCP connector that created them, so:

- `analyze --from-dir DIR` runs the whole Phase 2 report off a local copy of
  the CSVs and needs no Drive credential at all; or
- `import-archive --from-dir DIR` uploads that local copy through this
  client, which then owns the files and can read them directly from then on
  (idempotent -- months already identical in Drive are skipped); or
- open the `personnel-finances` folder with this OAuth client so Drive grants
  it per-file access to what is already there.

`bootstrap-sync-state --from-date YYYY-MM-DD` only sets the watermark; it
never re-pulls history.

## Commands

```
python -m bot.cli doctor
python -m bot.cli bootstrap-sync-state [--from-date 2026-09-19]
python -m bot.cli get-sync-state
python -m bot.cli sync --transactions-json new_txns.json --advance-watermark-to 2026-09-07
python -m bot.cli analyze --as-of 2026-09-07 --window-days 365 --html-out report.html
```

`doctor` is the first thing to run when a run fails on Drive. It reports the
scopes declared in the token JSON, which ones are actually sent on refresh,
whether the refresh succeeds, whether the root folder is reachable, and which
months have a readable CSV — enough to separate a rejected refresh from an
archive the scope is hiding from an archive that is genuinely empty. It exits
non-zero when Drive is not usable.

## Tests

```
python -m unittest discover -s bot -t . -p 'test_*.py'
```

`sync` expects `new_txns.json` to be the raw list of records returned by the
Truthifi `get_transactions` MCP tool (accountId/date/description/
transactionType/budgetFlowDetailCategory/budgetFlowType/amount/quantity/
price/fees/security) for the window since the last sync. It merges them into
the right `transactions_YYYY-MM.csv` file(s) in Drive, de-duplicating by
(date, account, description, transaction type, amount).

`analyze` never touches Truthifi -- it only reads the monthly CSVs already
archived in Drive.
