# KPI formulas

Source of truth: `business_radar/analytics/kpi/definitions.py`. This page must match
that file. Each KPI is tested against standard ERPNext reports (see the validation
column). Amounts are in company (base) currency unless stated.

Conventions:

- Only submitted documents (`docstatus = 1`) count. Drafts and cancelled documents are excluded.
- Opening invoices (`is_opening = 'Yes'`) are excluded from revenue and receivable views.
- A KPI is `n/a` when its denominator is zero. It is never shown as zero.
- A user without company-level scope receives `restricted` status and no value.

| Code | Label | Formula | Sources | Validation method (status: not yet executed, see `docs/status.md`) |
|---|---|---|---|---|
| `NET_REVENUE` | Net Revenue | Σ `Sales Invoice.base_net_total` for submitted invoices in the period. Credit notes (`is_return = 1`) are negative and included. Taxes excluded. | Sales Invoice | Matches the GL income accounts in the *General Ledger* report (root type Income, excluding Period Closing Vouchers), up to explained differences such as Journal Entries posted to income accounts. |
| `REVENUE_GROWTH` | Revenue Growth | (Net Revenue current − Net Revenue previous) ÷ \|Net Revenue previous\|. The previous period has the same length and ends the day before the current period starts. | Sales Invoice | Derived from `NET_REVENUE` for both periods. |
| `GROSS_PROFIT` | Gross Profit | Reuses ERPNext `GrossProfitGenerator` (grouped by Item Code). Sums `base_amount − buying_amount` as the report's total row does. | Sales Invoice, Delivery Note, Stock Ledger Entry | Total row of the *Gross Profit* report with the same company and dates. |
| `ACCOUNTS_RECEIVABLE` | Accounts Receivable | Σ (debit − credit) on GL Entry for accounts of type Receivable in the company, not cancelled, posted on or before the period end. | GL Entry, Account | *Accounts Receivable* report total as of the same date. |
| `OVERDUE_RECEIVABLES` | Overdue Receivables | Σ (`outstanding_amount` × `conversion_rate`) for submitted, non-return invoices with `due_date < period end`, `posting_date <= period end`, and positive outstanding. | Sales Invoice | *Accounts Receivable* report, rows with overdue buckets. Differences come from unallocated credit notes and advances, which are not netted here. |
| `OVERDUE_RECEIVABLES_RATIO` | Overdue Receivables Ratio | `OVERDUE_RECEIVABLES` ÷ `ACCOUNTS_RECEIVABLE`. Used by the health score. | Sales Invoice, GL Entry | Derived. |
| `COLLECTION_RATE` | Collection Rate | Allocated customer payments in the period ÷ `base_grand_total` of ordinary invoices posted in the same period. Each allocation is converted with its invoice's own rate (`base_grand_total / grand_total`). | Payment Entry, Payment Entry Reference, Sales Invoice | Manual reconciliation against the *Payment Reconciliation* tool for a sample period. |
| `EXPENSES` | Expenses | Σ (debit − credit) on GL Entry for Expense root accounts (non-group), not cancelled, posted in the period. Excludes Period Closing Vouchers and accounts of type *Cost of Goods Sold*. | GL Entry, Account | *Profit and Loss* report, Expense section, for the same period (after the same exclusions). |
| `CASH_COLLECTED` | Cash Collected | Σ `base_paid_amount` of submitted Payment Entries of type *Receive* from customers in the period. Includes unallocated advances. | Payment Entry | Sum of matching Payment Entries in the *Payment Entry* list. |

## Limitations and edge cases

- **Revenue vs. GL income.** Sales Invoice amounts exclude taxes, so they match income accounts only when no other journals post to income accounts. The difference is shown during validation.
- **Collection rate can exceed 1.** Payments in the period can settle invoices from earlier periods. Read it as a cash-to-invoice ratio, not a percentage of collectible invoices.
- **Collection rate uses allocated payments only.** Journal Entry settlements and unallocated advances are excluded from the numerator.
- **Expenses are accrual-based.** They follow GL postings, not payments. Cash timing is not reflected here.
- **Stock adjustment accounts** with root type Expense are included in `EXPENSES` unless their account type is excluded. This is intentional for the MVP and documented.
- **Currency.** Sales Invoice and Payment Entry amounts are converted with base fields. Receivable and expense balances come from GL, which is in company currency.
- **Cash Collected is not cash flow.** Full cash-flow statements are deferred.
- **Restricted users** never receive company totals, even if the KPI is listed for their role.

## Deferred KPIs

Sales, inventory, and operations KPIs are defined in the approved plan and are added in Phase 2 with their own formulas and tests.
