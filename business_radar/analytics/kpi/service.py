"""KPI evaluation service. Enforces permissions in this order:

1. Period validity.
2. Company read access and record-level scope (``get_access_scope``).
3. KPI role requirement.
4. Read permission on every source DocType.
5. Company-level scope for aggregate KPIs.

Failures return an explicit status, never partial numbers. Unexpected errors are
logged with full detail in Error Log and reported to the user only as a generic
status.
"""

from __future__ import annotations

from datetime import date

import frappe
from frappe import _

from business_radar.analytics.kpi.definitions import KPI_DEFINITIONS, KPIContext, KPIDefinition
from business_radar.constants import MAX_PERIOD_DAYS
from business_radar.data_source.scope import get_access_scope, require_doctype_read

KPI_CONFIG_DOCTYPE = "Business Radar KPI Configuration"


def validate_period(start: date, end: date) -> None:
	if not isinstance(start, date) or not isinstance(end, date):
		frappe.throw(_("Start and end dates are required."))
	if start > end:
		frappe.throw(_("The start date must be on or before the end date."))
	if (end - start).days + 1 > MAX_PERIOD_DAYS:
		frappe.throw(_("The period must not exceed {0} days.").format(MAX_PERIOD_DAYS))


def _enabled_codes() -> dict[str, int]:
	rows = frappe.get_all(KPI_CONFIG_DOCTYPE, fields=["name", "enabled"], limit=0)
	return {row.name: int(row.enabled) for row in rows}


def _user_has_any_role(user: str, roles: tuple[str, ...]) -> bool:
	user_roles = set(frappe.get_roles(user))
	return bool(user_roles.intersection(roles))


def _result(
	definition: KPIDefinition,
	status: str,
	value=None,
	details=None,
) -> dict:
	return {
		"code": definition.code,
		"label": definition.label,
		"group": definition.group,
		"unit": definition.unit,
		"status": status,
		"value": value,
		"details": details or {},
		"formula": definition.formula,
		"sources": list(definition.sources),
	}


def _evaluate(definition: KPIDefinition, ctx: KPIContext, user: str) -> dict:
	if not _user_has_any_role(user, definition.required_roles):
		return _result(definition, "not_permitted")

	try:
		for doctype in definition.sources:
			require_doctype_read(doctype, user)
	except PermissionError:
		return _result(definition, "not_permitted")

	if definition.company_level_only and not ctx.scope.company_level:
		return _result(definition, "restricted")

	try:
		computed = definition.compute(ctx)
	except PermissionError:
		return _result(definition, "restricted")
	except Exception:
		frappe.log_error(
			title=f"Business Radar KPI failed: {definition.code}",
			message=frappe.get_traceback(),
		)
		return _result(definition, "error")

	value = computed.get("value")
	status = "ok" if value is not None else "n/a"
	return _result(definition, status, value, computed.get("details"))


def compute_kpis(
	company: str,
	start: date,
	end: date,
	codes: list[str] | None = None,
	user: str | None = None,
) -> list[dict]:
	user = user or frappe.session.user
	validate_period(start, end)

	enabled = _enabled_codes()
	selected = [
		definition
		for definition in KPI_DEFINITIONS
		if (codes is None or definition.code in codes) and enabled.get(definition.code, 1)
	]
	if not selected:
		return []

	scope = get_access_scope(company, user)
	ctx = KPIContext(scope=scope, company=company, start=start, end=end)
	return [_evaluate(definition, ctx, user) for definition in selected]
