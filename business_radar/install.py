"""Idempotent installation and migration hooks.

Every function here may run many times. Existing administrator changes to KPI
configuration are never overwritten. Only labels and groups, which come from code,
are kept in sync.
"""

from __future__ import annotations

import frappe

from business_radar.constants import BUSINESS_RADAR_ROLES

KPI_CONFIG_DOCTYPE = "Business Radar KPI Configuration"


def before_install() -> None:
	ensure_roles()


def after_install() -> None:
	ensure_roles()
	sync_kpi_configurations()
	frappe.db.commit()


def after_migrate() -> None:
	ensure_roles()
	sync_kpi_configurations()
	frappe.db.commit()


def ensure_roles() -> None:
	for role_name in BUSINESS_RADAR_ROLES:
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
				ignore_permissions=True
			)


def sync_kpi_configurations() -> None:
	from business_radar.analytics.kpi.definitions import KPI_DEFINITIONS

	for definition in KPI_DEFINITIONS:
		if frappe.db.exists(KPI_CONFIG_DOCTYPE, definition.code):
			frappe.db.set_value(
				KPI_CONFIG_DOCTYPE,
				definition.code,
				{"kpi_label": definition.label, "kpi_group": definition.group},
				update_modified=False,
			)
			continue
		frappe.get_doc(
			{
				"doctype": KPI_CONFIG_DOCTYPE,
				"kpi_code": definition.code,
				"kpi_label": definition.label,
				"kpi_group": definition.group,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
