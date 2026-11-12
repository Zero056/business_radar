"""Access scope evaluation.

Business Radar aggregates (totals, balances, rates) are computed with SQL for
performance. SQL does not evaluate per-document permissions, so the aggregate
path is only allowed when the user has an *unrestricted company-level scope*:

1. The user can read the Company.
2. The user has no party-level User Permissions (Customer, Customer Group,
   Territory, Sales Person, Supplier).
3. No ``permission_query_conditions`` (hooks or Server Scripts) restrict the
   user on any sensitive source DocType. This picks up custom portfolio or
   handover rules automatically.

Users who fail any check still receive KPI *status* information, but never
totals that could include records they are not permitted to see.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe
from frappe.model.db_query import DatabaseQuery

PARTY_DIMENSION_DOCTYPES = ("Customer", "Customer Group", "Territory", "Sales Person", "Supplier")

SENSITIVE_SOURCE_DOCTYPES = (
	"Sales Invoice",
	"Payment Entry",
	"Sales Order",
	"Delivery Note",
	"Customer",
	"GL Entry",
)


@dataclass(frozen=True)
class AccessScope:
	user: str
	company: str
	company_level: bool
	restrictions: tuple[str, ...] = ()


def get_access_scope(company: str, user: str | None = None) -> AccessScope:
	if not company:
		raise ValueError("company is required")
	user = user or frappe.session.user

	restrictions: list[str] = []

	if not frappe.has_permission("Company", "read", company, user=user):
		restrictions.append("no read access to company")

	if frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": ["in", list(PARTY_DIMENSION_DOCTYPES)]},
		pluck="name",
		limit=1,
	):
		restrictions.append("party-level user permission")

	for doctype in SENSITIVE_SOURCE_DOCTYPES:
		if DatabaseQuery(doctype, user=user).get_permission_query_conditions():
			restrictions.append(f"record-level rule on {doctype}")

	return AccessScope(
		user=user,
		company=company,
		company_level=not restrictions,
		restrictions=tuple(restrictions),
	)


def system_scope(company: str) -> AccessScope:
	"""Scope for trusted background monitoring of one company.

	Detection jobs run as the system. They produce findings that are visible only
	through permission-filtered reads (added in Phase 2), so they are not a way to
	expose data to users.
	"""
	return AccessScope(user="Administrator", company=company, company_level=True)


def require_company_level(scope: AccessScope, company: str) -> None:
	"""Gate for every aggregate query. Raises instead of returning partial data."""
	if scope.company != company or not scope.company_level:
		raise PermissionError("Company-level access is required for this aggregate")


def require_doctype_read(doctype: str, user: str) -> None:
	if not frappe.has_permission(doctype, "read", user=user):
		raise PermissionError(f"No read permission on {doctype}")
