"""Read-only queries for monitoring rules and operational KPIs.

Same rules as ``erp_queries``: every function takes an ``AccessScope`` and runs
``require_company_level`` first. Parameterized SQL only. Each function that can
return many rows takes an explicit ``limit``. Rules that hit the limit mark their
outcome as incomplete, so they never auto-resolve alerts from a partial view.
"""

from __future__ import annotations

import frappe

from business_radar.data_source.scope import AccessScope, require_company_level

NOT_OPENING_SI = "ifnull(si.is_opening, 'No') != 'Yes'"


def overdue_invoices(scope: AccessScope, company: str, as_of, limit: int) -> list[dict]:
	require_company_level(scope, company)
	return frappe.db.sql(
		f"""
		SELECT si.name, si.customer, si.customer_name, si.posting_date, si.due_date,
			si.outstanding_amount, si.conversion_rate, si.currency
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.is_return = 0
			AND si.company = %(company)s
			AND si.posting_date <= %(as_of)s
			AND si.due_date < %(as_of)s
			AND si.outstanding_amount > 0
			AND {NOT_OPENING_SI}
		ORDER BY si.due_date ASC
		LIMIT %(limit)s
		""",
		{"company": company, "as_of": as_of, "limit": limit},
		as_dict=True,
	)


def settled_invoice_payments(scope: AccessScope, company: str, since, as_of, limit: int) -> list[dict]:
	"""Each payment allocation to an ordinary invoice, with dates for days-to-pay."""
	require_company_level(scope, company)
	return frappe.db.sql(
		f"""
		SELECT si.customer, si.name AS invoice, si.posting_date AS invoice_date,
			pe.posting_date AS paid_date, per.allocated_amount
		FROM `tabPayment Entry Reference` per
		INNER JOIN `tabPayment Entry` pe
			ON pe.name = per.parent AND per.parenttype = 'Payment Entry' AND pe.docstatus = 1
		INNER JOIN `tabSales Invoice` si
			ON si.name = per.reference_name
		WHERE per.reference_doctype = 'Sales Invoice'
			AND pe.company = %(company)s
			AND pe.payment_type = 'Receive'
			AND pe.party_type = 'Customer'
			AND si.docstatus = 1
			AND si.is_return = 0
			AND {NOT_OPENING_SI}
			AND pe.posting_date BETWEEN %(since)s AND %(as_of)s
			AND pe.posting_date >= si.posting_date
		ORDER BY si.customer, pe.posting_date
		LIMIT %(limit)s
		""",
		{"company": company, "since": since, "as_of": as_of, "limit": limit},
		as_dict=True,
	)


def revenue_buckets(
	scope: AccessScope, company: str, as_of, bucket_days: int, buckets: int, by_customer_group: bool
) -> list[dict]:
	"""Net revenue per period bucket. Bucket 0 is the latest ``bucket_days`` days up to ``as_of``.

	Returns rows with ``grp`` (customer group, or empty for company totals), ``bucket``,
	``amount`` (base net total, credit notes negative) and ``document_count``.
	"""
	require_company_level(scope, company)
	since = as_of - _days(bucket_days * buckets - 1)
	group_expr = "ifnull(si.customer_group, '')" if by_customer_group else "''"
	return frappe.db.sql(
		f"""
		SELECT {group_expr} AS grp,
			FLOOR(DATEDIFF(%(as_of)s, si.posting_date) / %(bucket_days)s) AS bucket,
			COALESCE(SUM(si.base_net_total), 0) AS amount,
			COUNT(si.name) AS document_count
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.company = %(company)s
			AND si.posting_date BETWEEN %(since)s AND %(as_of)s
			AND {NOT_OPENING_SI}
		GROUP BY grp, bucket
		""",
		{"company": company, "as_of": as_of, "since": since, "bucket_days": bucket_days},
		as_dict=True,
	)


def customer_order_dates(scope: AccessScope, company: str, since, as_of, limit: int) -> list[dict]:
	require_company_level(scope, company)
	return frappe.db.sql(
		f"""
		SELECT si.customer, si.customer_name, si.posting_date
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.is_return = 0
			AND si.company = %(company)s
			AND si.posting_date BETWEEN %(since)s AND %(as_of)s
			AND {NOT_OPENING_SI}
		ORDER BY si.customer, si.posting_date
		LIMIT %(limit)s
		""",
		{"company": company, "since": since, "as_of": as_of, "limit": limit},
		as_dict=True,
	)


def bins_at_or_below_reorder(scope: AccessScope, company: str, limit: int) -> list[dict]:
	require_company_level(scope, company)
	return frappe.db.sql(
		"""
		SELECT b.name AS bin_name, b.item_code, i.item_name, b.warehouse,
			b.actual_qty, ir.warehouse_reorder_level
		FROM `tabBin` b
		INNER JOIN `tabWarehouse` w ON w.name = b.warehouse AND w.company = %(company)s
		INNER JOIN `tabItem Reorder` ir
			ON ir.parent = b.item_code AND ir.parenttype = 'Item' AND ir.warehouse = b.warehouse
		INNER JOIN `tabItem` i ON i.name = b.item_code
		WHERE ir.warehouse_reorder_level > 0
			AND b.actual_qty <= ir.warehouse_reorder_level
		ORDER BY b.actual_qty ASC
		LIMIT %(limit)s
		""",
		{"company": company, "limit": limit},
		as_dict=True,
	)


def bins_negative(scope: AccessScope, company: str, limit: int) -> list[dict]:
	require_company_level(scope, company)
	return frappe.db.sql(
		"""
		SELECT b.name AS bin_name, b.item_code, i.item_name, b.warehouse, b.actual_qty
		FROM `tabBin` b
		INNER JOIN `tabWarehouse` w ON w.name = b.warehouse AND w.company = %(company)s
		INNER JOIN `tabItem` i ON i.name = b.item_code
		WHERE b.actual_qty < 0
		ORDER BY b.actual_qty ASC
		LIMIT %(limit)s
		""",
		{"company": company, "limit": limit},
		as_dict=True,
	)


def stock_outflows(scope: AccessScope, company: str, since, as_of, limit: int) -> list[dict]:
	"""Bins with stock on hand that also had outgoing movements in the window."""
	require_company_level(scope, company)
	return frappe.db.sql(
		"""
		SELECT b.name AS bin_name, b.item_code, i.item_name, b.warehouse, b.actual_qty,
			i.lead_time_days, o.outflow, o.outflow_days
		FROM `tabBin` b
		INNER JOIN `tabWarehouse` w ON w.name = b.warehouse AND w.company = %(company)s
		INNER JOIN `tabItem` i ON i.name = b.item_code
		INNER JOIN (
			SELECT sle.item_code, sle.warehouse,
				SUM(-sle.actual_qty) AS outflow,
				COUNT(DISTINCT sle.posting_date) AS outflow_days
			FROM `tabStock Ledger Entry` sle
			WHERE sle.is_cancelled = 0
				AND sle.company = %(company)s
				AND sle.actual_qty < 0
				AND sle.posting_date BETWEEN %(since)s AND %(as_of)s
			GROUP BY sle.item_code, sle.warehouse
		) o ON o.item_code = b.item_code AND o.warehouse = b.warehouse
		WHERE b.actual_qty > 0
		LIMIT %(limit)s
		""",
		{"company": company, "since": since, "as_of": as_of, "limit": limit},
		as_dict=True,
	)


def delayed_sales_orders(scope: AccessScope, company: str, as_of, limit: int) -> list[dict]:
	require_company_level(scope, company)
	return frappe.db.sql(
		"""
		SELECT so.name, so.customer, so.customer_name, so.transaction_date,
			so.delivery_date, so.per_delivered, so.status
		FROM `tabSales Order` so
		WHERE so.docstatus = 1
			AND so.company = %(company)s
			AND so.status NOT IN ('Closed', 'Completed')
			AND so.per_delivered < 100
			AND so.delivery_date < %(as_of)s
		ORDER BY so.delivery_date ASC
		LIMIT %(limit)s
		""",
		{"company": company, "as_of": as_of, "limit": limit},
		as_dict=True,
	)


def customer_retention(scope: AccessScope, company: str, prev_start, prev_end, start, end) -> dict:
	"""Customers invoiced in the previous period that were also invoiced in the current period."""
	require_company_level(scope, company)
	rows = frappe.db.sql(
		f"""
		SELECT
			COUNT(DISTINCT CASE WHEN si.posting_date BETWEEN %(prev_start)s AND %(prev_end)s
				THEN si.customer END) AS previous_customers,
			COUNT(DISTINCT CASE WHEN si.posting_date BETWEEN %(prev_start)s AND %(prev_end)s
				AND si.customer IN (
					SELECT DISTINCT s2.customer FROM `tabSales Invoice` s2
					WHERE s2.docstatus = 1 AND s2.is_return = 0 AND s2.company = %(company)s
						AND s2.posting_date BETWEEN %(start)s AND %(end)s
				) THEN si.customer END) AS retained_customers
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.is_return = 0
			AND si.company = %(company)s
			AND si.posting_date BETWEEN %(prev_start)s AND %(prev_end)s
			AND {NOT_OPENING_SI}
		""",
		{
			"company": company,
			"prev_start": prev_start,
			"prev_end": prev_end,
			"start": start,
			"end": end,
		},
		as_dict=True,
	)
	row = rows[0] if rows else {}
	return {
		"previous_customers": int(row.get("previous_customers") or 0),
		"retained_customers": int(row.get("retained_customers") or 0),
	}


def stock_reorder_counts(scope: AccessScope, company: str) -> dict:
	"""Stocked item-warehouse rows that have a reorder level, and how many are at or below it."""
	require_company_level(scope, company)
	row = frappe.db.sql(
		"""
		SELECT COUNT(*) AS configured,
			SUM(CASE WHEN b.actual_qty <= ir.warehouse_reorder_level THEN 1 ELSE 0 END) AS low
		FROM `tabItem Reorder` ir
		INNER JOIN `tabBin` b ON b.item_code = ir.parent AND b.warehouse = ir.warehouse
		INNER JOIN `tabWarehouse` w ON w.name = b.warehouse AND w.company = %(company)s
		WHERE ir.parenttype = 'Item' AND ir.warehouse_reorder_level > 0
		""",
		{"company": company},
		as_dict=True,
	)
	row = row[0] if row else {}
	return {"configured": int(row.get("configured") or 0), "low": int(row.get("low") or 0)}


def open_sales_order_counts(scope: AccessScope, company: str, as_of) -> dict:
	require_company_level(scope, company)
	row = frappe.db.sql(
		"""
		SELECT COUNT(so.name) AS open_orders,
			SUM(CASE WHEN so.delivery_date < %(as_of)s THEN 1 ELSE 0 END) AS delayed_orders
		FROM `tabSales Order` so
		WHERE so.docstatus = 1
			AND so.company = %(company)s
			AND so.status NOT IN ('Closed', 'Completed')
			AND so.per_delivered < 100
		""",
		{"company": company, "as_of": as_of},
		as_dict=True,
	)
	row = row[0] if row else {}
	return {
		"open_orders": int(row.get("open_orders") or 0),
		"delayed_orders": int(row.get("delayed_orders") or 0),
	}


def _days(count: int):
	from datetime import timedelta

	return timedelta(days=count)
