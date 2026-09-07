# Expense Routine Logic Engine

This routine runs in two phases every day: **Phase 1** syncs new transactions from
Truthifi into Google Drive; **Phase 2** analyzes the transaction history by reading
it back out of Drive (not Truthifi). Keeping these separate means the daily Truthifi
API budget only has to cover "what changed since yesterday," while the analysis can
freely look back over a full year of already-synced data without touching Truthifi's
rate limit at all.

## Phase 1 — Daily Sync (Truthifi → Google Drive)

- Drive root folder: `personnel-finances` (look it up by name/mimeType if the id
  isn't already known). Each calendar month has its own subfolder `YYYY-MM`
  containing one file, `transactions_YYYY-MM.csv`.
- All Drive reads/writes in this routine (both phases) go through
  `google-api-python-client`, authenticated from the OAuth token JSON in env var
  `GOOGLE_DRIVE_TOKEN_JSON` (keys: token, refresh_token, token_uri, client_id,
  client_secret, scopes) — not the Drive MCP connector tool. Use a venv if the
  system Python's native `cryptography`/`_cffi_backend` install is broken.
- Sync state file: `personnel-finances/sync_state.json`, holding
  `{"last_synced_date": "YYYY-MM-DD"}` — the last calendar date already pulled from
  Truthifi and written to Drive.
  - If this file doesn't exist yet, bootstrap it from the newest date already
    present across the existing monthly CSVs in Drive.
- Each run:
  1. Read `last_synced_date` from `sync_state.json`.
  2. Call Truthifi `get_transactions` for **all** accounts with **no category or
     type filter** (the full raw ledger — every debit and credit, including
     investment activity, matching what's already in the historical Drive
     archive), for the window `[last_synced_date, today]`, paginating fully via
     cursor until `hasMore` is false.
  3. Group the newly-fetched records by calendar month (a sync window usually
     lands in one month, occasionally two if it spans a month boundary).
  4. For each affected month: download the existing `transactions_YYYY-MM.csv`
     from Drive if present, merge in the new records, de-duplicate against the
     existing rows by (date, accountId, description, amount), and re-upload,
     overwriting the file in place. Create the `YYYY-MM` subfolder first if it
     doesn't exist yet.
  5. Only after every affected month has uploaded successfully, update
     `sync_state.json` with `last_synced_date = today`.
  6. If Truthifi's daily rate limit is hit mid-sync, stop without advancing
     `last_synced_date` past the last fully-synced day, and report the gap
     explicitly — the next day's run will naturally catch up on the missed days.
     Never estimate or fabricate figures for a day that failed to sync.

## Category Overrides
Truthifi's own category tag is sometimes wrong or inconsistent for a given
merchant across months (e.g. a school meals payment tagged Education in one
month and Charitable Giving in another). Maintain a merchant → category
correction list in `bot/category_overrides.json` (pattern = case-insensitive
substring of the transaction description, category = what it should be
tagged as instead). Apply it at analysis time, not sync time, so a new
override immediately corrects everything already archived in Drive without
rewriting those CSVs.

## Phase 2 — Analysis (reads Drive CSVs only, never calls Truthifi)

### Reporting Period
- Rolling **365-day** window ending on the run date (a full year, not 30 days),
  regardless of any different default period stated in the scheduling prompt —
  this file's period definition takes precedence.
- Use the prior rolling-year window (days 366–730 back) for period-over-period
  balance comparisons.
- Determine which monthly CSVs overlap the window (normally the current month
  plus the prior 12), download them from Drive, and concatenate into one dataset
  before analyzing.

### Expense Management & Budget Rules
1. DE-DUPLICATE TRANSFERS: Automatically filter out internal credit card payments (e.g., money moving from BofA checking to pay off Robinhood Credit) so expenses aren't double-counted.
   Watch especially for internal card payoffs that show up mislabeled under a normal
   expense category (e.g., "Shopping", "Loans") instead of being tagged as a transfer —
   identify these by matching amount and nearby date against a corresponding
   payment-received line on the credit account, and exclude them too. Known recurring
   examples: the monthly "SAMS CLUB PAYMENT" from Chase checking (tagged Shopping) is
   a Sam's Club Mastercard payoff, and "Online Scheduled Payment to ACCT# 7298" from
   BofA checking (tagged Loans) is a BofA Travel Rewards Visa payoff.
   Likewise, a "Robinhood" transfer that matches a same-day Robinhood Credit Card
   payment line is a card payoff, not brokerage funding — do not count it as Savings.
2. CATEGORIZATION: Group all transactions into strict 50/30/20 buckets:
   - Needs (50%): Housing, utilities, grocers, insurance, minimum card payments.
   - Wants (30%): Dining out, shopping, streaming services, entertainment.
   - Savings (20%): Transfers to savings accounts, Robinhood brokerage investments.
3. TOP EXPENSE ITEMS: Within the Needs bucket and separately within the Wants bucket,
   rank the individual line-item transactions by amount (descending, after
   de-duplication) and identify the top 15 items in each bucket over the rolling-year
   window. If a bucket has fewer than 15 items, list all of them.
4. MONTH-WISE, CATEGORY-WISE TREND ANALYSIS (new):
   - For every `budgetFlowDetailCategory` present, plus the three roll-up buckets
     Needs/Wants/Savings, compute a monthly total for each of the (up to 13)
     calendar months touched by the rolling-year window.
   - For each category, compute month-over-month % change for every consecutive
     pair of months in the window, and compare the most recent month against the
     trailing 3-month average.
   - Rank categories by movement and report the top 5 trending **up** and top 5
     trending **down** (by both latest-month $ change and % change — call out
     when the two disagree, e.g. a small category with a huge % swing vs. a large
     category with a modest % swing but big dollar impact).

### Output Format
Provide a clean summary showing:
- Total expenditures for the rolling-year period.
- Breakdown of Needs vs. Wants vs. Savings.
- The top 15 expense items within Needs and the top 15 within Wants, each rendered as
  its own HTML table (columns: Date, Merchant/Description, Category, Amount).
- A month × category matrix (rows = category, columns = the up-to-13 months in the
  window, values = $ total per month) rendered as an HTML table.
- A "Trending Up" and "Trending Down" table (columns: Category, Latest Month $,
  % change vs. prior month, % change vs. trailing 3-month average).
- A per-category item breakdown table, one per category, for `Utilities/Bills`,
  `Entertainment`, `Home Improvement`, `Shopping`, `Food/Drink`, and `Groceries`
  (columns: Merchant/Description, Total over the window, # of transactions),
  aggregating every line item in that category across the full rolling-year
  window by merchant — not just the top 15. Useful for spotting a specific
  recurring cost's real annual total even when no single instance of it is
  large enough to land in the top-15 Needs/Wants table.
- Balance remaining in checking/savings accounts, with change vs. one year ago.

## Email Delivery (after the analysis)
After producing the summary, draft a detailed email using the Gmail MCP connector:
1. Create a Gmail DRAFT (do not send directly) addressed to the account owner's own
   email address, with subject "Personal Finance Summary — <period>". Use the
   `htmlBody` field (not just plain text) so all HTML tables render as real HTML
   tables, not preformatted text.
2. The email body must contain the full detailed report: total expenditures, the
   Needs/Wants/Savings breakdown with per-category detail and notable merchants, the
   two top-15 HTML tables, the month × category trend matrix and the trending
   up/down table described above, end-of-period account balances with
   period-over-period change (vs. one year ago), and any flags (over-target
   buckets, zero savings, missing income, untracked cash withdrawals, or a Phase 1
   sync gap from a rate-limit stop). Note anything de-duplicated or excluded.
3. Apply the Gmail label `Send-With-Claude` to the draft's thread (look up the label
   ID via list_labels; create the label if it does not exist).
4. If the Gmail connector is unavailable, report that explicitly and still present
   the summary in the response.
