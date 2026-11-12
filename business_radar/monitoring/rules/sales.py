"""Sales monitoring rules.

SAL_CUSTOMER_STOPPED
    Per customer with at least ``min_samples`` (default 4) submitted invoices in
    the history window. Metric: days since last invoice divided by the customer's
    median gap between invoices (above). Bands: Informational >= 2.5, Low >= 4,
    Medium >= 6. A finding also requires days since last invoice >= the
    ``min_inactive_days`` parameter (default 60).
    The title says "potential inactive". A customer is never labeled lost.

SAL_REVENUE_DROP_SEGMENT
    Net revenue per customer group. Current = last 30 days, baseline = average of
    the three previous 30-day periods. Metric: relative drop (above). Bands:
    Low >= 20%, Medium >= 35%, High >= 50%. Groups with a baseline below
    ``min_baseline_amount`` are skipped. Invoices without a customer group are not
    assigned to any segment and are skipped.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from frappe import _

from business_radar.data_source import monitoring_queries as q
from business_radar.monitoring.registry import RuleDefinition, RuleOutcome, register_rule
from business_radar.monitoring.rules.common import band_severity, days_between, evidence_row, money
from business_radar.monitoring.rules.financial import frappe_customer_name

ROW_LIMIT = 20000
BUCKET_DAYS = 30
STOPPED_BANDS = ((2.5, "Informational"), (4, "Low"), (6, "Medium"))
SEGMENT_BANDS = ((0.20, "Low"), (0.35, "Medium"), (0.50, "High"))


def _evaluate_stopped(ctx) -> RuleOutcome:
	history_days = int(ctx.parameters.get("history_days", 365))
	min_inactive = int(ctx.parameters.get("min_inactive_days", 60))
	since = ctx.period_end - _days(history_days)
	rows = q.customer_order_dates(ctx.scope, ctx.company, since, ctx.period_end, ROW_LIMIT)
	outcome = RuleOutcome(records_scanned=len(rows), complete=len(rows) < ROW_LIMIT)

	dates_by_customer = defaultdict(list)
	for row in rows:
		dates_by_customer[row.customer].append(row.posting_date)

	for customer, dates in dates_by_customer.items():
		dates = sorted(set(dates))
		if len(dates) < max(ctx.min_samples, 4):
			continue
		gaps = [days_between(a, b) for a, b in zip(dates, dates[1:])]
		typical_gap = median(gaps)
		if typical_gap <= 0:
			continue
		days_since = days_between(dates[-1], ctx.period_end)
		if days_since < min_inactive:
			continue
		ratio = days_since / typical_gap
		severity = band_severity(ctx, ratio)
		if severity is None:
			continue
		customer_name = frappe_customer_name(customer)
		outcome.findings.append(
			{
				"entity_key": f"customer:{customer}:inactive",
				"title": _("Potential inactive customer: {0}").format(customer_name),
				"severity": severity,
				"explanation": _(
					"{0} has not been invoiced for {1} days. Its usual gap between purchases is {2:.0f} days, "
					"based on {3} invoices."
				).format(customer_name, days_since, typical_gap, len(dates)),
				"potential_impact": _(
					"A customer that stops buying reduces revenue. This is a warning sign to verify, not "
					"proof that the relationship has ended."
				),
				"recommended_actions": [
					_("Check with the account owner whether an order is planned or a competitor is involved."),
					_("Review recent quotations and open follow-ups for this customer."),
				],
				"evidence_type": "Statistical",
				"evidence": [
					evidence_row(1, _("Last invoice date"), dates[-1], "Customer", customer, "name", as_of=ctx.period_end),
					evidence_row(2, _("Days since last invoice"), days_since, None, None, None,
						_("as-of date minus last invoice date"), ctx.period_end),
					evidence_row(3, _("Median gap between invoices"), round(typical_gap, 1), None, None, None,
						_("median of consecutive invoice gaps")),
					evidence_row(4, _("Invoices in history window"), len(dates), None, None, None,
						_("count of submitted invoices, last {0} days").format(history_days)),
				],
				"detected_value": float(days_since),
				"expected_value": float(typical_gap),
				"unit": "days",
				"related_doctype": "Customer",
				"related_name": customer,
				"affected_customer": customer,
				"snapshot": {
					"rule": "SAL_CUSTOMER_STOPPED",
					"days_since_last": days_since,
					"median_gap_days": typical_gap,
					"ratio": round(ratio, 3),
					"invoices": len(dates),
				},
			}
		)
	return outcome


def _evaluate_segment_drop(ctx) -> RuleOutcome:
	min_baseline = float(ctx.parameters.get("min_baseline_amount", 1000))
	rows = q.revenue_buckets(ctx.scope, ctx.company, ctx.period_end, BUCKET_DAYS, 4, by_customer_group=True)
	outcome = RuleOutcome(records_scanned=len(rows))

	by_group = defaultdict(dict)
	for row in rows:
		if not row.grp:
			continue
		by_group[row.grp][int(row.bucket)] = float(row.amount or 0)

	for group, buckets in by_group.items():
		current = buckets.get(0, 0.0)
		baseline = sum(buckets.get(index, 0.0) for index in (1, 2, 3)) / 3
		if baseline < min_baseline:
			continue
		drop = (baseline - current) / baseline
		severity = band_severity(ctx, drop)
		if severity is None:
			continue
		outcome.findings.append(
			{
				"entity_key": f"customer-group:{group}:net-revenue",
				"title": _("Revenue down {0:.0f}% in customer group {1}").format(drop * 100, group),
				"severity": severity,
				"explanation": _(
					"Customer group {0} billed {1} in the last 30 days, against an average of {2} in the three "
					"previous 30-day periods."
				).format(group, money(current), money(baseline)),
				"potential_impact": _(
					"A segment-level decline can point to lost demand, pricing pressure, or a sales territory "
					"problem that a total view hides."
				),
				"recommended_actions": [
					_("Review the customers in this group that bought in the baseline but not in the last 30 days."),
					_("Check pricing and discounts for the group against the previous period."),
				],
				"evidence_type": "Statistical",
				"evidence": [
					evidence_row(1, _("Current 30-day net revenue"), round(current, 2), "Customer Group", group, "name",
						_("sum of base_net_total, last 30 days"), ctx.period_end),
					evidence_row(2, _("Baseline average (3 periods)"), round(baseline, 2), "Customer Group", group,
						"name", _("mean of the three previous 30-day sums"), ctx.period_end),
					evidence_row(3, _("Relative drop"), f"{drop:.1%}", None, None, None,
						_("(baseline - current) / baseline")),
				],
				"detected_value": round(current, 2),
				"expected_value": round(baseline, 2),
				"unit": "currency",
				"related_doctype": "Customer Group",
				"related_name": group,
				"snapshot": {
					"rule": "SAL_REVENUE_DROP_SEGMENT",
					"customer_group": group,
					"current": round(current, 2),
					"baseline": round(baseline, 2),
					"drop_ratio": round(drop, 4),
				},
			}
		)
	return outcome


def _days(count: int):
	from datetime import timedelta

	return timedelta(days=count)


register_rule(
	RuleDefinition(
		code="SAL_CUSTOMER_STOPPED",
		name=_("Customer purchase gap"),
		category="Sales",
		target_doctype="Customer",
		description=_("Customers that have not been invoiced for much longer than their usual gap between invoices."),
		evaluate=_evaluate_stopped,
		default_frequency="Daily",
		default_cooldown_hours=168,
		default_lookback_days=365,
		min_samples=4,
		default_parameters={"history_days": 365, "min_inactive_days": 60},
		default_severity_bands=STOPPED_BANDS,
		band_direction="above",
		default_enabled=True,
	)
)

register_rule(
	RuleDefinition(
		code="SAL_REVENUE_DROP_SEGMENT",
		name=_("Customer group revenue drop"),
		category="Sales",
		target_doctype="Customer Group",
		description=_("Revenue per customer group in the last 30 days compared with its three-period baseline."),
		evaluate=_evaluate_segment_drop,
		default_frequency="Daily",
		default_cooldown_hours=168,
		default_lookback_days=120,
		default_parameters={"min_baseline_amount": 1000},
		default_severity_bands=SEGMENT_BANDS,
		band_direction="above",
		default_enabled=True,
	)
)
