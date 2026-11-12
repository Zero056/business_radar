"""Retention for detection run history. Action Logs are never purged here.

Purging runs in bounded batches so a large history never locks the table for long.
Only finished runs are purged. Running runs are never deleted.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.utils import cint, now_datetime

from business_radar.constants import DEFAULT_RETENTION_DAYS

RUN_DOCTYPE = "Business Radar Detection Run"
SETTINGS_DOCTYPE = "Business Radar Settings"
BATCH_SIZE = 500
FINISHED_STATUSES = ("Success", "Failed", "Stale")


def purge_expired_logs(now=None) -> int:
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	days = cint(settings.retention_days) or DEFAULT_RETENTION_DAYS
	cutoff = (now or now_datetime()) - timedelta(days=days)

	purged = 0
	while True:
		names = frappe.get_all(
			RUN_DOCTYPE,
			filters={"status": ["in", list(FINISHED_STATUSES)], "ended_at": ["<", cutoff]},
			pluck="name",
			limit=BATCH_SIZE,
		)
		if not names:
			break
		frappe.db.sql(
			"DELETE FROM `tabBusiness Radar Detection Run` WHERE name IN %(names)s",
			{"names": tuple(names)},
		)
		frappe.db.commit()
		purged += len(names)
	return purged
