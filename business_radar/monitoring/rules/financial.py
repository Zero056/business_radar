"""Financial monitoring rules.

FIN_OVERDUE_AGING
    One finding per submitted, non-return Sales Invoice with outstanding balance
    past its due date. Metric: days past due. Severity bands on days (above):
    Low >= 30, Medium >= 60, High >= 90, Critical >= 120.
    Minimum data: none. Evidence: invoice, customer, due date, base outstanding,
    days overdue, customer's average days-to-pay in the lookback window.

FIN_PAY_DELAY_TREND
    Per customer. Payment allocations in the lookback window are sorted by payment
    date. Recent = last 3 allocations. Baseline = the earlier allocations.
    Metric: recent average days-to-pay minus baseline average (above).
    Bands: Low >= 10, Medium >= 20, High >= 30 days.
    Minimum data: at least ``min_samples`` allocations (default 5).

FIN_REVENUE_DROP
    Company net revenue. Current = last 30 days. Baseline = average of the three
    previous 30-day buckets. Metric: relative drop, (baseline - current) / baseline
    (above). Bands: Low >= 15%, Medium >= 25%, High >= 40%.
    Minimum data: at least ``min_samples`` invoices in the baseline, and a
    positive baseline. Otherwise the outcome is reported as insufficient data.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean

from frappe import _

from business_radar.data_source import monitoring_queries as q
from business_radar.monitoring.registry import RuleDefinition, RuleOutcome, register_rule
from business_radar.monitoring.rules.common import band_severity, days_between, evidence_row, money

ROW_LIMIT = 2000
HISTORY_LIMIT = 50000
RECENT_ALLOCATIONS = 3
BUCKET_DAYS = 30

OVERDUE_BANDS = ((30, "Low"), (60, "Medium"), (90, "High"), (120, "Critical"))
DELAY_BANDS = ((10, "Low"), (20, "Medium"), (30, "High"))
REVENUE_DROP_BANDS = ((0.15, "Low"), (0.25, "Medium"), (0.40, "High"))


def _customer_history(ctx) -> dict:
	rows = q.settled_invoice_payments(ctx.scope, ctx.company, ctx.period_start, ctx.period_end, HISTORY_LIMIT)
	days_by_customer = defaultdict(list)
	for row in rows:
		days_by_customer[row.customer].append(days_between(row.invoice_date, row.paid_date))
	return {customer: (mean(days), len(days)) for customer, days in days_by_customer.items()}


def _evaluate_overdue(ctx) -> RuleOutcome:
	rows = q.overdue_invoices(ctx.scope, ctx.company, ctx.period_end, ROW_LIMIT)
	outcome = RuleOutcome(records_scanned=len(rows), complete=len(rows) < ROW_LIMIT)
	history = _customer_history(ctx)

	for row in rows:
		days = days_between(row.due_date, ctx.period_end)
		severity = band_severity(ctx, days)
		if severity is None:
			continue
		base_outstanding = float(row.outstanding_amount or 0) * float(row.conversion_rate or 1)
		average, samples = history.get(row.customer, (None, 0))
		history_text = (
			_("{0:.0f} days on average across {1} settled invoices").format(average, samples)
			if average is not None
			else _("No settled invoices in the lookback window")
		)
		outcome.findings.append(
			{
				"entity_key": f"sales-invoice:{row.name}",
				"title": _("Overdue invoice {0} for {1}").format(row.name, row.customer_name or row.customer),
				"severity": severity,
				"explanation": _(
					"Invoice {0} is {1} days past its due date on {2}, with {3} still outstanding."
				).format(row.name, days, row.due_date, money(base_outstanding)),
				"potential_impact": _(
					"Cash from this invoice is delayed. Balances that stay overdue increase collection risk."
				),
				"recommended_actions": [
					_("Contact the customer about the outstanding invoice."),
					_("Check whether a payment is in transit or a dispute is open."),
				],
				"evidence_type": "Fact",
				"evidence": [
					evidence_row(1, _("Invoice"), row.name, "Sales Invoice", row.name, "name", as_of=ctx.period_end),
					evidence_row(2, _("Customer"), row.customer, "Customer", row.customer, "name"),
					evidence_row(3, _("Due date"), row.due_date, "Sales Invoice", row.name, "due_date"),
					evidence_row(
						4,
						_("Outstanding (company currency)"),
						round(base_outstanding, 2),
						"Sales Invoice",
						row.name,
						"outstanding_amount",
						_("outstanding_amount x conversion_rate"),
					),
					evidence_row(
						5,
						_("Days overdue"),
						days,
						None,
						None,
						None,
						_("as-of date minus due date"),
						ctx.period_end,
					),
					evidence_row(6, _("Customer payment history"), history_text, "Customer", row.customer, "name"),
				],
				"detected_value": float(days),
				"expected_value": 0.0,
				"unit": "days",
				"related_doctype": "Sales Invoice",
				"related_name": row.name,
				"affected_customer": row.customer,
				"snapshot": {
					"rule": "FIN_OVERDUE_AGING",
					"due_date": str(row.due_date),
					"days_overdue": days,
					"outstanding_base": round(base_outstanding, 2),
					"customer_average_days_to_pay": average,
					"customer_settled_invoices": samples,
				},
			}
		)
	return outcome


def _evaluate_pay_delay(ctx) -> RuleOutcome:
	rows = q.settled_invoice_payments(ctx.scope, ctx.company, ctx.period_start, ctx.period_end, HISTORY_LIMIT)
	outcome = RuleOutcome(records_scanned=len(rows), complete=len(rows) < HISTORY_LIMIT)
	by_customer = defaultdict(list)
	for row in rows:
		by_customer[row.customer].append(row)

	minimum = max(ctx.min_samples, RECENT_ALLOCATIONS + 2)
	for customer, items in by_customer.items():
		if len(items) < minimum:
			continue
		items.sort(key=lambda item: (item.paid_date, item.invoice))
		recent, baseline = items[-RECENT_ALLOCATIONS:], items[:-RECENT_ALLOCATIONS]
		recent_days = [days_between(item.invoice_date, item.paid_date) for item in recent]
		baseline_days = [days_between(item.invoice_date, item.paid_date) for item in baseline]
		recent_avg, baseline_avg = mean(recent_days), mean(baseline_days)
		delta = recent_avg - baseline_avg
		severity = band_severity(ctx, delta)
		if severity is None:
			continue
		customer_name = frappe_customer_name(customer)
		evidence = []
		for index, item in enumerate(recent, start=1):
			evidence.append(
				evidence_row(
					index,
					_("Recent payment for {0}").format(item.invoice),
					_("{0} days to pay").format(days_between(item.invoice_date, item.paid_date)),
					"Sales Invoice",
					item.invoice,
					"posting_date",
					_("payment date minus invoice date"),
					item.paid_date,
				)
			)
		evidence.append(
			evidence_row(
				len(recent) + 1,
				_("Baseline average days to pay"),
				_("{0:.1f} days across {1} earlier payments").format(baseline_avg, len(baseline)),
				"Customer",
				customer,
				"name",
				_("mean of earlier allocations"),
			)
		)
		outcome.findings.append(
			{
				"entity_key": f"customer:{customer}:payment-delay",
				"title": _("Payment delays increasing for {0}").format(customer_name),
				"severity": severity,
				"explanation": _(
					"Recent invoices are paid in {0:.1f} days on average, against a baseline of {1:.1f} days "
					"(+{2:.1f} days)."
				).format(recent_avg, baseline_avg, delta),
				"potential_impact": _(
					"A customer that pays more slowly ties up working capital and may signal financial stress."
				),
				"recommended_actions": [
					_("Review open invoices for this customer and agree payment dates."),
					_("Confirm credit limits and payment terms with the account owner."),
				],
				"evidence_type": "Statistical",
				"evidence": evidence,
				"detected_value": round(recent_avg, 2),
				"expected_value": round(baseline_avg, 2),
				"unit": "days",
				"related_doctype": "Customer",
				"related_name": customer,
				"affected_customer": customer,
				"snapshot": {
					"rule": "FIN_PAY_DELAY_TREND",
					"recent_average_days": round(recent_avg, 2),
					"baseline_average_days": round(baseline_avg, 2),
					"delta_days": round(delta, 2),
					"baseline_samples": len(baseline),
					"recent_samples": len(recent),
				},
			}
		)
	return outcome


def _evaluate_revenue_drop(ctx) -> RuleOutcome:
	rows = q.revenue_buckets(ctx.scope, ctx.company, ctx.period_end, BUCKET_DAYS, 4, by_customer_group=False)
	outcome = RuleOutcome(records_scanned=len(rows))
	buckets = {int(row.bucket): (float(row.amount or 0), int(row.document_count or 0)) for row in rows}

	current_amount, _current_count = buckets.get(0, (0.0, 0))
	baseline = [buckets.get(index, (0.0, 0)) for index in (1, 2, 3)]
	baseline_amount = sum(amount for amount, _count in baseline) / 3
	baseline_count = sum(count for _amount, count in baseline)

	if baseline_count < ctx.min_samples or baseline_amount <= 0:
		outcome.insufficient_data = True
		return outcome

	drop = (baseline_amount - current_amount) / baseline_amount
	severity = band_severity(ctx, drop)
	if severity is None:
		return outcome

	outcome.findings.append(
		{
			"entity_key": f"company:{ctx.company}:net-revenue",
			"title": _("Net revenue down {0:.0f}% against the baseline").format(drop * 100),
			"severity": severity,
			"explanation": _(
				"Net revenue in the last 30 days is {0}, against an average of {1} in the three previous 30-day periods."
			).format(money(current_amount), money(baseline_amount)),
			"potential_impact": _(
				"A sustained revenue decline reduces cash flow and profit, and can signal lost demand or pricing pressure."
			),
			"recommended_actions": [
				_("Compare revenue by customer group and sales person to find where the decline starts."),
				_("Check for delayed order intake or unusual credit notes in the period."),
			],
			"evidence_type": "Statistical",
			"evidence": [
				evidence_row(
					1, _("Current 30-day net revenue"), round(current_amount, 2), "Company", ctx.company, "name",
					_("sum of base_net_total, last 30 days"), ctx.period_end,
				),
				evidence_row(
					2, _("Baseline average (3 periods)"), round(baseline_amount, 2), "Company", ctx.company, "name",
					_("mean of the three previous 30-day sums"), ctx.period_end,
				),
				evidence_row(3, _("Relative drop"), f"{drop:.1%}", "Company", ctx.company, "name",
					_("(baseline - current) / baseline"), ctx.period_end),
			],
			"detected_value": round(current_amount, 2),
			"expected_value": round(baseline_amount, 2),
			"unit": "currency",
			"related_doctype": "Company",
			"related_name": ctx.company,
			"snapshot": {
				"rule": "FIN_REVENUE_DROP",
				"current": round(current_amount, 2),
				"baseline": round(baseline_amount, 2),
				"drop_ratio": round(drop, 4),
				"baseline_invoices": baseline_count,
			},
		}
	)
	return outcome


def frappe_customer_name(customer: str) -> str:
	import frappe

	return frappe.db.get_value("Customer", customer, "customer_name") or customer


register_rule(
	RuleDefinition(
		code="FIN_OVERDUE_AGING",
		name=_("Overdue invoice aging"),
		category="Financial",
		target_doctype="Sales Invoice",
		description=_("Submitted invoices with an outstanding balance past their due date."),
		evaluate=_evaluate_overdue,
		default_frequency="Daily",
		default_cooldown_hours=24,
		default_lookback_days=365,
		default_severity_bands=OVERDUE_BANDS,
		band_direction="above",
		default_enabled=True,
	)
)

register_rule(
	RuleDefinition(
		code="FIN_PAY_DELAY_TREND",
		name=_("Customer payment delay trend"),
		category="Financial",
		target_doctype="Customer",
		description=_("Customers whose recent days-to-pay exceed their own historical baseline."),
		evaluate=_evaluate_pay_delay,
		default_frequency="Daily",
		default_cooldown_hours=72,
		default_lookback_days=365,
		min_samples=5,
		default_severity_bands=DELAY_BANDS,
		band_direction="above",
		default_enabled=True,
	)
)

register_rule(
	RuleDefinition(
		code="FIN_REVENUE_DROP",
		name=_("Net revenue drop"),
		category="Financial",
		target_doctype="Company",
		description=_("Net revenue in the last 30 days compared with the average of the three previous 30-day periods."),
		evaluate=_evaluate_revenue_drop,
		default_frequency="Daily",
		default_cooldown_hours=168,
		default_lookback_days=120,
		min_samples=3,
		default_severity_bands=REVENUE_DROP_BANDS,
		band_direction="above",
		default_enabled=True,
	)
)
