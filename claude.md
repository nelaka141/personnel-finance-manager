# Expense Routine Logic Engine

## Reporting Period
- Analyze a rolling 30-day window (the 30 days ending on the run date), regardless of
  any different default period (e.g. "last 7 days") stated in the scheduling prompt —
  this file's period definition takes precedence.
- Use the prior 30-day window (days 31–60 back) for period-over-period balance
  comparisons.

## Data Sourcing
- You have native access to the Truthifi MCP connector tools.
- Use the appropriate tool to pull live accounts and transaction lists for all the accounts.

## Expense Management & Budget Rules
1. DE-DUPLICATE TRANSFERS: Automatically filter out internal credit card payments (e.g., money moving from BofA checking to pay off Robinhood Credit) so expenses aren't double-counted.
2. CATEGORIZATION: Group all transactions into strict 50/30/20 buckets:
   - Needs (50%): Housing, utilities, grocers, insurance, minimum card payments.
   - Wants (30%): Dining out, shopping, streaming services, entertainment.
   - Savings (20%): Transfers to savings accounts, Robinhood brokerage investments.

## Output Format
Provide a clean summary showing:
- Total expenditures for the period.
- Breakdown of Needs vs. Wants vs. Savings.
- Balance remaining in checking/savings accounts.

## Email Delivery (after the analysis)
After producing the summary, draft a detailed email using the Gmail MCP connector:
1. Create a Gmail DRAFT (do not send directly) addressed to the account owner's own
   email address, with subject "Personal Finance Summary — <period>".
2. The email body must contain the full detailed report: total expenditures, the
   Needs/Wants/Savings breakdown with per-category detail and notable merchants,
   end-of-period account balances with period-over-period change (vs. the prior 30
   days), and any flags (over-target buckets, zero savings, missing income,
   untracked cash withdrawals). Note anything de-duplicated or excluded.
3. Apply the Gmail label `Send-With-Claude` to the draft's thread (look up the label
   ID via list_labels; create the label if it does not exist).
4. If the Gmail connector is unavailable, report that explicitly and still present
   the summary in the response.
