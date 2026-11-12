"""Inventory monitoring rules. Stock quantities come from Bin and Stock Ledger Entry.

INV_BELOW_REORDER
    Bin rows where actual quantity is at or below the item's reorder level for the
    warehouse. Metric: shortfall = max(0, 1 - actual / reorder level) (above).
    Bands: Low >= 0, Medium >= 0.5, High >= 1 (zero or negative stock).

INV_NEGATIVE_STOCK
    Bin rows with negative actual quantity. Metric: magnitude of the negative
    quantity (above). Any negative value is High. Negative stock is usually a
    posting or valuation error, so the alert asks for review and does not suggest
    an adjustment.

INV_STOCKOUT_RISK
    Bin rows with stock on hand and outgoing movements in the last 30 days.
    Average daily outflow = outflow / 30. Days of cover = on-hand / daily outflow.
    Metric: days of cover divided by lead time (below). Lead time is the item's
    lead_time_days, or the ``default_lead_time_days`` parameter (default 14).
    Bands: Critical <= 0.25, High <= 0.5, Medium <= 0.75, Low <= 1.0.
    Minimum data: outflow on at least ``min_samples`` (default 5) distinct days.
    Otherwise the item is counted as insufficient data and no alert is raised.
"""

from __future__ import annotations

from frappe import _

from business_radar.data_source import monitoring_queries as q
from business_radar.monitoring.registry import RuleDefinition, RuleOutcome, register_rule
from business_radar.monitoring.rules.common import band_severity, evidence_row
from business_radar.monitoring.severity import BELOW

ROW_LIMIT = 5000
WINDOW_DAYS = 30
REORDER_BANDS = ((1.0, "High"), (0.5, "Medium"), (0.0, "Low"))
NEGATIVE_BANDS = ((0.0, "High"),)
STOCKOUT_BANDS = ((0.25, "Critical"), (0.5, "High"), (0.75, "Medium"), (1.0, "Low"))


def _evaluate_below_reorder(ctx) -> RuleOutcome:
	rows = q.bins_at_or_below_reorder(ctx.scope, ctx.company, ROW_LIMIT)
	outcome = RuleOutcome(records_scanned=len(rows), complete=len(rows) < ROW_LIMIT)
	for row in rows:
		level = float(row.warehouse_reorder_level or 0)
		actual = float(row.actual_qty or 0)
		if level <= 0:
			continue
		shortfall = max(0.0, 1 - actual / level)
		severity = band_severity(ctx, shortfall)
		if severity is None:
			continue
		outcome.findings.append(
			{
				"entity_key": f"bin:{row.bin_name}:reorder",
				"title": _("Stock at or below reorder level: {0} in {1}").format(row.item_code, row.warehouse),
				"severity": severity,
				"explanation": _(
					"{0} has {1} units in {2}, at or below the reorder level of {3} units."
				).format(row.item_name or row.item_code, _format_qty(actual), row.warehouse, _format_qty(level)),
				"potential_impact": _(
					"Replenishment is due. If stock runs out before the next receipt, sales and deliveries will stop."
				),
				"recommended_actions": [
					_("Review open purchase orders and raise a material request if none is open."),
					_("Confirm the reorder level still matches current demand."),
				],
				"evidence_type": "Fact",
				"evidence": [
					evidence_row(1, _("Item"), row.item_code, "Item", row.item_code, "name"),
					evidence_row(2, _("Warehouse"), row.warehouse, "Warehouse", row.warehouse, "name"),
					evidence_row(3, _("Actual quantity"), _format_qty(actual), "Bin", row.bin_name, "actual_qty",
						as_of=ctx.period_end),
					evidence_row(4, _("Reorder level"), _format_qty(level), "Item", row.item_code, "reorder_levels"),
				],
				"detected_value": actual,
				"expected_value": level,
				"unit": "qty",
				"related_doctype": "Bin",
				"related_name": row.bin_name,
				"affected_item": row.item_code,
				"snapshot": {"rule": "INV_BELOW_REORDER", "actual": actual, "reorder_level": level,
					"shortfall": round(shortfall, 4)},
			}
		)
	return outcome


def _evaluate_negative(ctx) -> RuleOutcome:
	rows = q.bins_negative(ctx.scope, ctx.company, ROW_LIMIT)
	outcome = RuleOutcome(records_scanned=len(rows), complete=len(rows) < ROW_LIMIT)
	for row in rows:
		actual = float(row.actual_qty or 0)
		severity = band_severity(ctx, -actual)
		if severity is None:
			continue
		outcome.findings.append(
			{
				"entity_key": f"bin:{row.bin_name}:negative",
				"title": _("Negative stock: {0} in {1}").format(row.item_code, row.warehouse),
				"severity": severity,
				"explanation": _("{0} shows {1} units on hand in {2}.").format(
					row.item_name or row.item_code, _format_qty(actual), row.warehouse),
				"potential_impact": _(
					"Negative stock means goods were issued before they were recorded as received. "
					"Costing and valuation may be wrong."
				),
				"recommended_actions": [
					_("Check stock entries and delivery notes for this item and warehouse."),
					_("Do not correct the balance until the source movement is confirmed."),
				],
				"evidence_type": "Fact",
				"evidence": [
					evidence_row(1, _("Item"), row.item_code, "Item", row.item_code, "name"),
					evidence_row(2, _("Warehouse"), row.warehouse, "Warehouse", row.warehouse, "name"),
					evidence_row(3, _("Actual quantity"), _format_qty(actual), "Bin", row.bin_name, "actual_qty",
						as_of=ctx.period_end),
				],
				"detected_value": actual,
				"expected_value": 0.0,
				"unit": "qty",
				"related_doctype": "Bin",
				"related_name": row.bin_name,
				"affected_item": row.item_code,
				"snapshot": {"rule": "INV_NEGATIVE_STOCK", "actual": actual},
			}
		)
	return outcome


def _evaluate_stockout(ctx) -> RuleOutcome:
	from datetime import timedelta

	since = ctx.period_end - timedelta(days=WINDOW_DAYS - 1)
	default_lead = float(ctx.parameters.get("default_lead_time_days", 14))
	min_outflow_days = int(max(ctx.min_samples, ctx.parameters.get("min_outflow_days", 5)))
	rows = q.stock_outflows(ctx.scope, ctx.company, since, ctx.period_end, ROW_LIMIT)
	outcome = RuleOutcome(records_scanned=len(rows), complete=len(rows) < ROW_LIMIT)

	for row in rows:
		if int(row.outflow_days or 0) < min_outflow_days:
			outcome.insufficient_data = True
			continue
		actual = float(row.actual_qty or 0)
		daily = float(row.outflow or 0) / WINDOW_DAYS
		if daily <= 0:
			continue
		cover = actual / daily
		lead = float(row.lead_time_days or 0) or default_lead
		ratio = cover / lead
		severity = band_severity(ctx, ratio)
		if severity is None:
			continue
		outcome.findings.append(
			{
				"entity_key": f"bin:{row.bin_name}:stockout",
				"title": _("Stockout risk: {0} in {1}").format(row.item_code, row.warehouse),
				"severity": severity,
				"explanation": _(
					"{0} has {1} days of cover at the last 30-day usage rate, against a lead time of {2} days."
				).format(row.item_name or row.item_code, f"{cover:.1f}", f"{lead:.0f}"),
				"potential_impact": _(
					"If the next receipt arrives after stock runs out, deliveries and production will be delayed."
				),
				"recommended_actions": [
					_("Place or expedite a purchase order for this item."),
					_("Confirm the expected receipt date with the supplier."),
				],
				"evidence_type": "Statistical",
				"evidence": [
					evidence_row(1, _("Actual quantity"), _format_qty(actual), "Bin", row.bin_name, "actual_qty",
						as_of=ctx.period_end),
					evidence_row(2, _("Outflow in last 30 days"), _format_qty(float(row.outflow or 0)), "Item",
						row.item_code, "name", _("sum of negative actual_qty in Stock Ledger Entry")),
					evidence_row(3, _("Days with outflow"), int(row.outflow_days or 0), None, None, None,
						_("distinct posting dates")),
					evidence_row(4, _("Days of cover"), f"{cover:.1f}", None, None, None,
						_("actual quantity / (30-day outflow / 30)")),
					evidence_row(5, _("Lead time (days)"), f"{lead:.0f}", "Item", row.item_code, "lead_time_days"),
				],
				"detected_value": round(cover, 2),
				"expected_value": lead,
				"unit": "days",
				"related_doctype": "Bin",
				"related_name": row.bin_name,
				"affected_item": row.item_code,
				"snapshot": {"rule": "INV_STOCKOUT_RISK", "days_of_cover": round(cover, 2), "lead_time_days": lead,
					"coverage_ratio": round(ratio, 4)},
			}
		)
	return outcome


def _format_qty(value: float) -> str:
	return f"{value:,.2f}".rstrip("0").rstrip(".")


register_rule(
	RuleDefinition(
		code="INV_BELOW_REORDER",
		name=_("Stock at or below reorder level"),
		category="Inventory",
		target_doctype="Bin",
		description=_("Item stock in a warehouse is at or below its configured reorder level."),
		evaluate=_evaluate_below_reorder,
		default_frequency="Daily",
		default_cooldown_hours=24,
		default_lookback_days=1,
		default_severity_bands=REORDER_BANDS,
		band_direction="above",
		default_enabled=True,
	)
)

register_rule(
	RuleDefinition(
		code="INV_NEGATIVE_STOCK",
		name=_("Negative stock"),
		category="Inventory",
		target_doctype="Bin",
		description=_("Bin rows with a negative quantity on hand."),
		evaluate=_evaluate_negative,
		default_frequency="Daily",
		default_cooldown_hours=24,
		default_lookback_days=1,
		default_severity_bands=NEGATIVE_BANDS,
		band_direction="above",
		default_enabled=True,
	)
)

register_rule(
	RuleDefinition(
		code="INV_STOCKOUT_RISK",
		name=_("Stockout risk"),
		category="Inventory",
		target_doctype="Bin",
		description=_("Days of stock cover are short of the item's lead time at the recent usage rate."),
		evaluate=_evaluate_stockout,
		default_frequency="Daily",
		default_cooldown_hours=48,
		default_lookback_days=30,
		min_samples=5,
		default_parameters={"default_lead_time_days": 14, "min_outflow_days": 5},
		default_severity_bands=STOCKOUT_BANDS,
		band_direction=BELOW,
		default_enabled=True,
	)
)
