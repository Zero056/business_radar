"""KPI registry. Each KPI documents its formula, sources, and required roles.

Formulas here are the single source of truth for ``docs/kpi-formulas.md``.
Change a formula only together with its documentation and its tests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from business_radar.analytics.formulas import (
	collection_rate,
	gross_margin,
	percent_change,
	previous_period,
	safe_divide,
)
from business_radar.constants import FINANCIAL_KPI_ROLES
from business_radar.data_source import erp_queries
from business_radar.data_source.scope import AccessScope

# Expense accounts excluded from the Expenses KPI. Cost of Goods Sold is
# reported through Gross Profit instead.
EXCLUDED_EXPENSE_ACCOUNT_TYPES = ("Cost of Goods Sold",)


@dataclass(frozen=True)
class KPIContext:
	scope: AccessScope
	company: str
	start: date
	end: date

	@property
	def previous(self) -> tuple[date, date]:
		return previous_period(self.start, self.end)


@dataclass(frozen=True)
class KPIDefinition:
	code: str
	label: str
	group: str
	unit: str  # "currency" | "ratio" | "count"
	formula: str
	sources: tuple[str, ...]
	compute: Callable[[KPIContext], dict]
	required_roles: tuple[str, ...] = FINANCIAL_KPI_ROLES
	company_level_only: bool = True


def _net_revenue(ctx: KPIContext) -> dict:
	split = erp_queries.net_revenue_split(ctx.scope, ctx.company, ctx.start, ctx.end)
	return {
		"value": split["net_revenue"],
		"details": {
			"gross_invoiced": split["gross_invoiced"],
			"credit_notes": split["credit_notes"],
			"invoice_count": split["invoice_count"],
			"credit_note_count": split["credit_note_count"],
		},
	}


def _revenue_growth(ctx: KPIContext) -> dict:
	prev_start, prev_end = ctx.previous
	current = erp_queries.net_revenue_split(ctx.scope, ctx.company, ctx.start, ctx.end)["net_revenue"]
	previous = erp_queries.net_revenue_split(ctx.scope, ctx.company, prev_start, prev_end)["net_revenue"]
	return {
		"value": percent_change(current, previous),
		"details": {
			"current_net_revenue": current,
			"previous_net_revenue": previous,
			"previous_start": prev_start.isoformat(),
			"previous_end": prev_end.isoformat(),
		},
	}


def _gross_profit(ctx: KPIContext) -> dict:
	totals = erp_queries.gross_profit_totals(ctx.scope, ctx.company, ctx.start, ctx.end)
	return {
		"value": totals["gross_profit"],
		"details": {
			"revenue": totals["revenue"],
			"cost_of_goods_sold": totals["cost_of_goods_sold"],
			"gross_margin": gross_margin(totals["gross_profit"], totals["revenue"]),
		},
	}


def _accounts_receivable(ctx: KPIContext) -> dict:
	return {"value": erp_queries.receivable_balance(ctx.scope, ctx.company, ctx.end)}


def _overdue_receivables(ctx: KPIContext) -> dict:
	return {"value": erp_queries.overdue_receivables(ctx.scope, ctx.company, ctx.end)}


def _overdue_ratio(ctx: KPIContext) -> dict:
	overdue = erp_queries.overdue_receivables(ctx.scope, ctx.company, ctx.end)
	receivable = erp_queries.receivable_balance(ctx.scope, ctx.company, ctx.end)
	return {
		"value": safe_divide(overdue, receivable),
		"details": {"overdue_receivables": overdue, "accounts_receivable": receivable},
	}


def _collection_rate(ctx: KPIContext) -> dict:
	allocated = erp_queries.allocated_payments_to_invoices(ctx.scope, ctx.company, ctx.start, ctx.end)
	invoiced = erp_queries.invoiced_total(ctx.scope, ctx.company, ctx.start, ctx.end)
	return {
		"value": collection_rate(allocated, invoiced),
		"details": {"allocated_payments": allocated, "invoiced_total": invoiced},
	}


def _expenses(ctx: KPIContext) -> dict:
	amount = erp_queries.expenses_from_gl(
		ctx.scope, ctx.company, ctx.start, ctx.end, EXCLUDED_EXPENSE_ACCOUNT_TYPES
	)
	return {"value": amount, "details": {"excluded_account_types": list(EXCLUDED_EXPENSE_ACCOUNT_TYPES)}}


def _cash_collected(ctx: KPIContext) -> dict:
	result = erp_queries.cash_collected(ctx.scope, ctx.company, ctx.start, ctx.end)
	return {"value": result["amount"], "details": {"payment_entry_count": result["count"]}}


KPI_DEFINITIONS: tuple[KPIDefinition, ...] = (
	KPIDefinition(
		code="NET_REVENUE",
		label="Net Revenue",
		group="Financial",
		unit="currency",
		formula=(
			"Sum of base_net_total of submitted Sales Invoices in the period, excluding opening "
			"invoices. Credit notes (is_return = 1) are included as negative amounts. Taxes excluded."
		),
		sources=("Sales Invoice",),
		compute=_net_revenue,
	),
	KPIDefinition(
		code="REVENUE_GROWTH",
		label="Revenue Growth",
		group="Financial",
		unit="ratio",
		formula=(
			"(Net Revenue current period - Net Revenue previous period) / |Net Revenue previous period|. "
			"The previous period has the same length and ends the day before the current period starts."
		),
		sources=("Sales Invoice",),
		compute=_revenue_growth,
	),
	KPIDefinition(
		code="GROSS_PROFIT",
		label="Gross Profit",
		group="Financial",
		unit="currency",
		formula=(
			"ERPNext Gross Profit report calculation (GrossProfitGenerator, grouped by Item Code), "
			"summing base_amount minus buying_amount, as the report's total row does."
		),
		sources=("Sales Invoice", "Delivery Note", "Stock Ledger Entry"),
		compute=_gross_profit,
	),
	KPIDefinition(
		code="ACCOUNTS_RECEIVABLE",
		label="Accounts Receivable",
		group="Financial",
		unit="currency",
		formula=(
			"Sum of (debit - credit) on GL Entry for accounts of type Receivable in the company, "
			"non-cancelled, posted on or before the period end. Company currency."
		),
		sources=("GL Entry", "Account"),
		compute=_accounts_receivable,
	),
	KPIDefinition(
		code="OVERDUE_RECEIVABLES",
		label="Overdue Receivables",
		group="Financial",
		unit="currency",
		formula=(
			"Sum of outstanding_amount x conversion_rate for submitted, non-return Sales Invoices with "
			"due_date before the period end and positive outstanding. Excludes opening invoices."
		),
		sources=("Sales Invoice",),
		compute=_overdue_receivables,
	),
	KPIDefinition(
		code="OVERDUE_RECEIVABLES_RATIO",
		label="Overdue Receivables Ratio",
		group="Financial",
		unit="ratio",
		formula="Overdue Receivables / Accounts Receivable. Undefined when Accounts Receivable is zero.",
		sources=("Sales Invoice", "GL Entry", "Account"),
		compute=_overdue_ratio,
	),
	KPIDefinition(
		code="COLLECTION_RATE",
		label="Collection Rate",
		group="Financial",
		unit="ratio",
		formula=(
			"Allocated customer payments in the period / base_grand_total of ordinary Sales Invoices "
			"posted in the same period. Allocations are converted using each invoice's own rate. "
			"Undefined when nothing was invoiced. Can exceed 1 when payments settle earlier invoices."
		),
		sources=("Payment Entry", "Payment Entry Reference", "Sales Invoice"),
		compute=_collection_rate,
	),
	KPIDefinition(
		code="EXPENSES",
		label="Expenses",
		group="Financial",
		unit="currency",
		formula=(
			"Sum of (debit - credit) on GL Entry for Expense root accounts (non-group), non-cancelled, "
			"posted in the period, excluding Period Closing Vouchers and accounts of type Cost of Goods Sold. "
			"Read from the GL only, so each posting is counted once."
		),
		sources=("GL Entry", "Account"),
		compute=_expenses,
	),
	KPIDefinition(
		code="CASH_COLLECTED",
		label="Cash Collected",
		group="Financial",
		unit="currency",
		formula=(
			"Sum of base_paid_amount of submitted Payment Entries of type Receive from customers in the "
			"period. A receipt measure, not a cash-flow statement. Includes unallocated advances."
		),
		sources=("Payment Entry",),
		compute=_cash_collected,
	),
)

KPI_BY_CODE = {definition.code: definition for definition in KPI_DEFINITIONS}
