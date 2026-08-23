# Expense Routine Logic Engine

## Reporting Period
- Analyze a rolling 1-year window (the 365 days ending on the run date), regardless of
  any different default period (e.g. "last 7 days" or "last 30 days") stated in the
  scheduling prompt — this file's period definition takes precedence.
- Use the prior 1-year window (days 366–730 back) for period-over-period balance
  comparisons, where account history is available. If an account has less than a
  full year of history, note the limitation explicitly instead of estimating.

## Data Sourcing
- You have native access to the Truthifi MCP connector tools.
- Use the appropriate tool to pull live accounts and transaction lists for all the accounts.
- A 1-year pull spans multiple 90-day request windows and multiple pages per window —
  fetch all of them; do not sample or truncate the period.

## Expense Management & Budget Rules
1. DE-DUPLICATE TRANSFERS: Automatically filter out internal credit card payments (e.g., money moving from BofA checking to pay off Robinhood Credit) so expenses aren't double-counted.
   Watch especially for internal card payoffs that show up mislabeled under a normal
   expense category (e.g., "Shopping", "Loans") instead of being tagged as a transfer —
   identify these by matching amount and nearby date against a corresponding
   payment-received line on the credit account, and exclude them too.
2. CATEGORIZATION: Group all transactions into strict 50/30/20 buckets:
   - Needs (50%): Housing, utilities, grocers, insurance, minimum card payments.
   - Wants (30%): Dining out, shopping, streaming services, entertainment.
   - Savings (20%): Transfers to savings accounts, Robinhood brokerage investments.
3. TOP EXPENSE ITEMS: Within the Needs bucket and separately within the Wants bucket,
   rank the individual line-item transactions by amount (descending, after
   de-duplication) and identify the top 15 items in each bucket.

## Output Format
Provide a clean summary showing:
- Total expenditures for the period.
- Breakdown of Needs vs. Wants vs. Savings.
- The top 15 expense items within Needs and the top 15 within Wants, each rendered as
  its own HTML table (columns: Date, Merchant/Description, Category, Amount).
- Balance remaining in checking/savings accounts.

## Email Delivery (after the analysis)
After producing the summary, draft a detailed email using the Gmail MCP connector:
1. Create a Gmail DRAFT (do not send directly) addressed to the account owner's own
   email address, with subject "Personal Finance Summary — <period>". Use the
   `htmlBody` field (not just plain text) so the top-15 tables render as real HTML
   tables, not preformatted text.
2. The email body must contain the full detailed report: total expenditures, the
   Needs/Wants/Savings breakdown with per-category detail and notable merchants, the
   two top-15 HTML tables described above, end-of-period account balances with
   period-over-period change (vs. the prior 1-year window), and any flags (over-target
   buckets, zero savings, missing income, untracked cash withdrawals). Note anything
   de-duplicated or excluded.
3. Apply the Gmail label `Send-With-Claude` to the draft's thread (look up the label
   ID via list_labels; create the label if it does not exist).
4. If the Gmail connector is unavailable, report that explicitly and still present
   the summary in the response.
