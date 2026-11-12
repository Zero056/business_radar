"""Record-level visibility for Business Radar DocTypes.

Alerts are created by the system (which has company-wide scope). What a user may
read is decided here, on every list query and every document read:

* A user needs a Business Radar role (or Administrator).
* An alert is visible when its company is unrestricted for the user, OR its
  affected customer is a customer the user may read. Customer visibility already
  reflects portfolio rules such as tst's permission hooks.
* A customer-restricted user therefore sees customer-level alerts only, never
  company-wide alerts for companies where they have no unrestricted access.

Health snapshots show company totals. They are visible only to users with a
financial role and unrestricted company scope.
"""

from __future__ import annotations

import frappe
from frappe.model.db_query import DatabaseQuery

from business_radar.constants import BUSINESS_RADAR_ROLES, FINANCIAL_KPI_ROLES
from business_radar.data_source.scope import get_access_scope

ALERT = "Business Radar Alert"
RECOMMENDATION = "Business Radar Recommendation"
SNAPSHOT = "Business Radar Health Snapshot"


def _roles(user: str) -> set[str]:
	return set(frappe.get_roles(user))


def _has_radar_role(user: str) -> bool:
	return user == "Administrator" or bool(_roles(user).intersection(BUSINESS_RADAR_ROLES))


def _unrestricted_companies(user: str) -> list[str]:
	companies = frappe.get_all("Company", pluck="name", limit=0)
	return [company for company in companies if get_access_scope(company, user).company_level]


def _visible_customers(user: str) -> list[str]:
	rows = DatabaseQuery("Customer", user=user).execute(fields=["`tabCustomer`.`name`"], limit_page_length=0)
	return [row[0] if isinstance(row, (list, tuple)) else row.get("name") for row in rows]


def _in_list(values: list[str]) -> str:
	return ", ".join(frappe.db.escape(value) for value in values)


def _alert_clause(user: str, alias: str) -> str:
	"""SQL condition for alert visibility on the given table alias."""
	if user == "Administrator":
		return ""
	if not _has_radar_role(user):
		return "1=0"

	companies = _unrestricted_companies(user)
	all_companies = frappe.get_all("Company", pluck="name", limit=0)
	clauses = []
	if companies:
		clauses.append(f"{alias}.`company` IN ({_in_list(companies)})")
	if len(companies) < len(all_companies):
		customers = _visible_customers(user)
		if customers:
			clauses.append(f"{alias}.`affected_customer` IN ({_in_list(customers)})")
	return "(" + " OR ".join(clauses) + ")" if clauses else "1=0"


def alert_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	return _alert_clause(user or frappe.session.user, "`tabBusiness Radar Alert`")


def alert_has_permission(doc, ptype: str | None = None, user: str | None = None):
	user = user or frappe.session.user
	if user == "Administrator":
		return None
	if not _has_radar_role(user):
		return False
	if doc.company in _unrestricted_companies(user):
		return None
	if doc.affected_customer and doc.affected_customer in _visible_customers(user):
		return None
	return False


def recommendation_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	user = user or frappe.session.user
	if user == "Administrator":
		return ""
	if not _has_radar_role(user):
		return "1=0"
	alert_condition = _alert_clause(user, "`tabBusiness Radar Alert`")
	companies = _unrestricted_companies(user)
	clauses = []
	if companies:
		clauses.append(f"`tabBusiness Radar Recommendation`.`company` IN ({_in_list(companies)})")
	clauses.append(
		"`tabBusiness Radar Recommendation`.`alert` IN ("
		"SELECT `tabBusiness Radar Alert`.`name` FROM `tabBusiness Radar Alert` "
		f"WHERE {alert_condition})"
	)
	return "(" + " OR ".join(clauses) + ")"


def recommendation_has_permission(doc, ptype: str | None = None, user: str | None = None):
	user = user or frappe.session.user
	if user == "Administrator":
		return None
	if not _has_radar_role(user):
		return False
	if doc.company in _unrestricted_companies(user):
		return None
	if doc.alert and frappe.db.exists(ALERT, doc.alert) and alert_has_permission(
		frappe.get_doc(ALERT, doc.alert), ptype, user
	) is None:
		return None
	return False


def snapshot_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	user = user or frappe.session.user
	if user == "Administrator":
		return ""
	if not _roles(user).intersection(FINANCIAL_KPI_ROLES):
		return "1=0"
	companies = _unrestricted_companies(user)
	if not companies:
		return "1=0"
	return f"`tabBusiness Radar Health Snapshot`.`company` IN ({_in_list(companies)})"


def snapshot_has_permission(doc, ptype: str | None = None, user: str | None = None):
	user = user or frappe.session.user
	if user == "Administrator":
		return None
	if not _roles(user).intersection(FINANCIAL_KPI_ROLES):
		return False
	return None if doc.company in _unrestricted_companies(user) else False
