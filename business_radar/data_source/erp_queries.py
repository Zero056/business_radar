"""Read-only ERPNext aggregates used by KPIs and detection rules.

Rules for this module:

* Every public function takes an ``AccessScope`` and calls
  ``require_company_level`` first. Aggregates never run for users who have
  record-level or party-level restrictions.
* Only ``SELECT`` statements. No writes, no DDL.
* Values are parameterized. No user input is interpolated into SQL.
* Draft, cancelled, and opening documents are excluded as documented per query.
* Amounts are returned in company (base) currency unless stated otherwise.
"""

from __future__ import annotations

import frappe

from business_radar.data_source.scope import AccessScope, require_company_level

# Opening balances are excluded from revenue and receivable views, as ERPNext
# reports do.
_SI_NOT_OPENING = "ifnull(si.is_opening, 'No') != 'Yes'"


def _sql(query: str, values: dict) -> dict:
	rows = frappe.db.sql(query, values, as_dict=True)
	return rows[0] if rows else {}


def net_revenue_split(scope: AccessScope, company: str, start, end) -> dict:
	"""Submitted Sales Invoices grouped by ``is_return``.

	``gross_invoiced`` is the sum of ordinary invoices. ``credit_notes`` is the
	sum of return invoices, which ERPNext stores as negative amounts. Net revenue
	is their sum. Taxes are excluded (``base_net_total``), matching ERPNext's
	Gross Profit and Income GL amounts.
	"""
	require_company_level(scope, company)
	rows = frappe.db.sql(
		"""
		SELECT si.is_return, COUNT(si.name) AS document_count,
			COALESCE(SUM(si.base_net_total), 0) AS amount
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.company = %(company)s
			AND si.posting_date BETWEEN %(start)s AND %(end)s
			AND {not_opening}
		GROUP BY si.is_return
		""".format(not_opening=_SI_NOT_OPENING),
		{"company": company, "start": start, "end": end},
		as_dict=True,
	)
	result = {
		"gross_invoiced": 0.0,
		"credit_notes": 0.0,
		"invoice_count": 0,
		"credit_note_count": 0,
	}
	for row in rows:
		if row.is_return:
			result["credit_notes"] = float(row.amount)
			result["credit_note_count"] = int(row.document_count)
		else:
			result["gross_invoiced"] = float(row.amount)
			result["invoice_count"] = int(row.document_count)
	result["net_revenue"] = result["gross_invoiced"] + result["credit_notes"]
	return result


def invoiced_total(scope: AccessScope, company: str, start, end) -> float:
	"""Gross invoiced amount including taxes, ordinary invoices only (no credit notes)."""
	require_company_level(scope, company)
	row = _sql(
		"""
		SELECT COALESCE(SUM(si.base_grand_total), 0) AS amount
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.is_return = 0
			AND si.company = %(company)s
			AND si.posting_date BETWEEN %(start)s AND %(end)s
			AND {not_opening}
		""".format(not_opening=_SI_NOT_OPENING),
		{"company": company, "start": start, "end": end},
	)
	return float(row.get("amount") or 0)


def allocated_payments_to_invoices(scope: AccessScope, company: str, start, end) -> float:
	"""Customer receipts allocated to ordinary invoices, converted to base currency.

	Each allocation is converted with its invoice's own rate
	(``base_grand_total / grand_total``), so mixed-currency companies are handled
	without mixing currencies. Allocations from Journal Entries and unallocated
	advances are not included.
	"""
	require_company_level(scope, company)
	row = _sql(
		"""
		SELECT COALESCE(SUM(
			per.allocated_amount * (si.base_grand_total / NULLIF(si.grand_total, 0))
		), 0) AS amount
		FROM `tabPayment Entry Reference` per
		INNER JOIN `tabPayment Entry` pe
			ON pe.name = per.parent AND per.parenttype = 'Payment Entry'
		INNER JOIN `tabSales Invoice` si
			ON si.name = per.reference_name
		WHERE per.reference_doctype = 'Sales Invoice'
			AND pe.docstatus = 1
			AND pe.company = %(company)s
			AND pe.payment_type = 'Receive'
			AND pe.party_type = 'Customer'
			AND pe.posting_date BETWEEN %(start)s AND %(end)s
			AND si.docstatus = 1
			AND si.is_return = 0
			AND ifnull(si.is_opening, 'No') != 'Yes'
		""",
		{"company": company, "start": start, "end": end},
	)
	return float(row.get("amount") or 0)


def cash_collected(scope: AccessScope, company: str, start, end) -> dict:
	"""Submitted customer receipts (Payment Entries of type Receive), company currency.

	This is a cash-movement measure, not a cash-flow statement. It includes
	unallocated advances.
	"""
	require_company_level(scope, company)
	row = _sql(
		"""
		SELECT COUNT(pe.name) AS document_count,
			COALESCE(SUM(pe.base_paid_amount), 0) AS amount
		FROM `tabPayment Entry` pe
		WHERE pe.docstatus = 1
			AND pe.company = %(company)s
			AND pe.payment_type = 'Receive'
			AND pe.party_type = 'Customer'
			AND pe.posting_date BETWEEN %(start)s AND %(end)s
		""",
		{"company": company, "start": start, "end": end},
	)
	return {"amount": float(row.get("amount") or 0), "count": int(row.get("document_count") or 0)}


def receivable_balance(scope: AccessScope, company: str, as_of) -> float:
	"""Receivable account balance from GL Entry, as of ``as_of`` (inclusive)."""
	require_company_level(scope, company)
	row = _sql(
		"""
		SELECT COALESCE(SUM(gle.debit - gle.credit), 0) AS balance
		FROM `tabGL Entry` gle
		INNER JOIN `tabAccount` acc ON acc.name = gle.account
		WHERE acc.company = %(company)s
			AND acc.account_type = 'Receivable'
			AND gle.company = %(company)s
			AND gle.is_cancelled = 0
			AND gle.posting_date <= %(as_of)s
		""",
		{"company": company, "as_of": as_of},
	)
	return float(row.get("balance") or 0)


def overdue_receivables(scope: AccessScope, company: str, as_of) -> float:
	"""Positive outstanding of ordinary invoices whose due date is before ``as_of``.

	Uses Sales Invoice ``outstanding_amount`` converted with the invoice's
	``conversion_rate``. Unallocated credit notes and advances are not netted here,
	so this can differ from the GL receivable balance.
	"""
	require_company_level(scope, company)
	row = _sql(
		"""
		SELECT COALESCE(SUM(si.outstanding_amount * si.conversion_rate), 0) AS amount
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.is_return = 0
			AND si.company = %(company)s
			AND si.posting_date <= %(as_of)s
			AND si.due_date < %(as_of)s
			AND si.outstanding_amount > 0
			AND {not_opening}
		""".format(not_opening=_SI_NOT_OPENING),
		{"company": company, "as_of": as_of},
	)
	return float(row.get("amount") or 0)


def expenses_from_gl(
	scope: AccessScope, company: str, start, end, excluded_account_types: tuple[str, ...]
) -> float:
	"""Net debit balance of expense accounts posted in the period.

	Reads GL Entry only, so each posting is counted once regardless of the source
	document (Purchase Invoice, Journal Entry, Payment Entry, Stock adjustment).
	Excludes:

	* Period Closing Voucher entries, which zero expense accounts at year end.
	* Accounts whose ``account_type`` is in ``excluded_account_types``. The default
	  excludes "Cost of Goods Sold", which belongs to Gross Profit.
	"""
	require_company_level(scope, company)
	excluded = tuple(excluded_account_types) or ("",)
	row = _sql(
		"""
		SELECT COALESCE(SUM(gle.debit - gle.credit), 0) AS amount
		FROM `tabGL Entry` gle
		INNER JOIN `tabAccount` acc ON acc.name = gle.account
		WHERE acc.root_type = 'Expense'
			AND acc.is_group = 0
			AND gle.company = %(company)s
			AND gle.is_cancelled = 0
			AND gle.posting_date BETWEEN %(start)s AND %(end)s
			AND gle.voucher_type != 'Period Closing Voucher'
			AND ifnull(acc.account_type, '') NOT IN %(excluded)s
		""",
		{"company": company, "start": start, "end": end, "excluded": excluded},
	)
	return float(row.get("amount") or 0)


def gross_profit_totals(scope: AccessScope, company: str, start, end) -> dict:
	"""ERPNext's standard Gross Profit calculation, reused rather than re-implemented.

	Runs ERPNext's ``GrossProfitGenerator`` and sums its rows exactly as the
	report's total row does. Tests compare the result with the report output.
	"""
	require_company_level(scope, company)
	from erpnext.accounts.report.gross_profit.gross_profit import GrossProfitGenerator

	generator = GrossProfitGenerator(
		frappe._dict(
			{
				"company": company,
				"from_date": start,
				"to_date": end,
				"group_by": "Item Code",
				"currency": frappe.get_cached_value("Company", company, "default_currency"),
			}
		)
	)
	base_amount = 0.0
	buying_amount = 0.0
	for row in generator.grouped_data:
		base_amount += row.base_amount or 0.0
		buying_amount += row.buying_amount or 0.0
	return {
		"revenue": base_amount,
		"cost_of_goods_sold": buying_amount,
		"gross_profit": base_amount - buying_amount,
	}
