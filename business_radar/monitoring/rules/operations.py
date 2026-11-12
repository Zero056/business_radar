"""Operational monitoring rules.

OPS_DELAYED_DELIVERY
    Submitted, open Sales Orders whose delivery date is before the as-of date and
    whose delivered percentage is below 100. Metric: days late (above).
    Bands: Low >= 1, Medium >= 14, High >= 30.
    Minimum data: none. Evidence: order, customer, delivery date, delivered percentage.
"""

from __future__ import annotations

from frappe import _

from business_radar.data_source import monitoring_queries as q
from business_radar.monitoring.registry import RuleDefinition, RuleOutcome, register_rule
from business_radar.monitoring.rules.common import band_severity, days_between, evidence_row
from business_radar.monitoring.rules.financial import frappe_customer_name

ROW_LIMIT = 2000
DELAY_BANDS = ((30, "High"), (14, "Medium"), (1, "Low"))


def _evaluate_delayed(ctx) -> RuleOutcome:
	rows = q.delayed_sales_orders(ctx.scope, ctx.company, ctx.period_end, ROW_LIMIT)
	outcome = RuleOutcome(records_scanned=len(rows), complete=len(rows) < ROW_LIMIT)
	for row in rows:
		days_late = days_between(row.delivery_date, ctx.period_end)
		severity = band_severity(ctx, days_late)
		if severity is None:
			continue
		delivered = float(row.per_delivered or 0)
		customer_name = row.customer_name or frappe_customer_name(row.customer)
		outcome.findings.append(
			{
				"entity_key": f"sales-order:{row.name}:delayed",
				"title": _("Delayed delivery {0} for {1}").format(row.name, customer_name),
				"severity": severity,
				"explanation": _(
					"Sales Order {0} was due for delivery on {1} and is {2:.0f}% delivered, {3} days late."
				).format(row.name, row.delivery_date, delivered, days_late),
				"potential_impact": _(
					"Late deliveries damage customer trust and can trigger penalties or cancellations."
				),
				"recommended_actions": [
					_("Confirm the delivery plan with the warehouse and update the customer."),
					_("Check whether the delay comes from stock availability or from a Delivery Note that is not submitted."),
				],
				"evidence_type": "Fact",
				"evidence": [
					evidence_row(1, _("Sales Order"), row.name, "Sales Order", row.name, "name", as_of=ctx.period_end),
					evidence_row(2, _("Customer"), row.customer, "Customer", row.customer, "name"),
					evidence_row(3, _("Delivery date"), row.delivery_date, "Sales Order", row.name, "delivery_date"),
					evidence_row(4, _("Delivered"), f"{delivered:.0f}%", "Sales Order", row.name, "per_delivered"),
					evidence_row(5, _("Days late"), days_late, None, None, None,
						_("as-of date minus delivery date"), ctx.period_end),
				],
				"detected_value": float(days_late),
				"expected_value": 0.0,
				"unit": "days",
				"related_doctype": "Sales Order",
				"related_name": row.name,
				"affected_customer": row.customer,
				"snapshot": {"rule": "OPS_DELAYED_DELIVERY", "days_late": days_late, "delivered_percent": delivered},
			}
		)
	return outcome


register_rule(
	RuleDefinition(
		code="OPS_DELAYED_DELIVERY",
		name=_("Delayed sales order delivery"),
		category="Operations",
		target_doctype="Sales Order",
		description=_("Open sales orders delivered later than their delivery date."),
		evaluate=_evaluate_delayed,
		default_frequency="Daily",
		default_cooldown_hours=24,
		default_lookback_days=365,
		default_severity_bands=DELAY_BANDS,
		band_direction="above",
		default_enabled=True,
	)
)
