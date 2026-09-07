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

## Commands

```
python -m bot.cli bootstrap-sync-state
python -m bot.cli get-sync-state
python -m bot.cli sync --transactions-json new_txns.json --advance-watermark-to 2026-09-07
python -m bot.cli analyze --as-of 2026-09-07 --window-days 365 --html-out report.html
```

`sync` expects `new_txns.json` to be the raw list of records returned by the
Truthifi `get_transactions` MCP tool (accountId/date/description/
transactionType/budgetFlowDetailCategory/budgetFlowType/amount/quantity/
price/fees/security) for the window since the last sync. It merges them into
the right `transactions_YYYY-MM.csv` file(s) in Drive, de-duplicating by
(date, account, description, transaction type, amount).

`analyze` never touches Truthifi -- it only reads the monthly CSVs already
archived in Drive.
