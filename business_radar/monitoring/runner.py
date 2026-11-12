"""Detection run lifecycle: locking, execution, heartbeat, and failure capture.

Locking design:

* ``acquire_run`` takes a row lock on the Monitoring Rule (``SELECT ... FOR UPDATE``)
  for the short check-and-insert step only, then commits to release it.
* A new run is refused while another run for the same ``(rule, company)`` is
  Running with a fresh heartbeat. A run with an expired heartbeat is marked Stale
  and does not block.
* ``enqueue_run`` uses ``deduplicate=True`` with a stable ``job_id`` so the same
  rule and company is not queued twice.

A failed run is recorded and never raises out of the background job. The
dispatcher decides about retries from the recorded failure.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from business_radar.data_source.scope import system_scope
from business_radar.monitoring.registry import RuleContext, get_rule
from business_radar.monitoring.run_state import is_active_run

RULE_DOCTYPE = "Business Radar Monitoring Rule"
RUN_DOCTYPE = "Business Radar Detection Run"
SETTINGS_DOCTYPE = "Business Radar Settings"
ERROR_MESSAGE_LIMIT = 2000
DEFAULT_STALE_TIMEOUT_MINUTES = 30


def build_lock_key(rule_code: str, company: str) -> str:
	return f"{rule_code}|{company}"


def build_job_id(rule_code: str, company: str) -> str:
	return f"business_radar:{build_lock_key(rule_code, company)}"


def run_rule(
	rule_code: str,
	company: str,
	triggered_by: str = "Manual",
	retry_count: int = 0,
) -> str | None:
	"""Execute one rule for one company. Returns the run name, or None when skipped."""
	definition = get_rule(rule_code)
	if definition is None:
		frappe.throw(frappe._("Rule {0} is not registered.").format(rule_code))

	rule = frappe.get_doc(RULE_DOCTYPE, rule_code)
	if not rule.enabled:
		return None

	run = acquire_run(rule_code, company, triggered_by, retry_count)
	if run is None:
		return None

	started = now_datetime()
	period_end = started.date()
	period_start = period_end - timedelta(days=max(cint(rule.lookback_days), 1))
	frappe.db.set_value(
		RUN_DOCTYPE,
		run.name,
		{"period_start": period_start, "period_end": period_end},
		update_modified=False,
	)
	frappe.db.commit()

	try:
		context = RuleContext(
			rule_code=rule_code,
			company=company,
			period_start=period_start,
			period_end=period_end,
			scope=system_scope(company),
			parameters={
				**definition.default_parameters,
				**{row.param_key: row.param_value for row in rule.parameters},
			},
			min_samples=max(definition.min_samples, cint(rule.min_samples)),
		)
		outcome = definition.evaluate(context)
		finish_run(run.name, "Success", started, records_scanned=outcome.records_scanned)
		_update_rule_status(rule_code, "Success")
	except Exception as exc:
		frappe.db.rollback()
		frappe.log_error(
			title=f"Business Radar run failed: {rule_code}",
			message=frappe.get_traceback(),
		)
		finish_run(run.name, "Failed", started, error=str(exc))
		_update_rule_status(rule_code, "Failed")
	return run.name


def acquire_run(rule_code: str, company: str, triggered_by: str, retry_count: int = 0):
	"""Insert a Running detection run unless a fresh one already exists."""
	lock_key = build_lock_key(rule_code, company)
	now = now_datetime()
	timeout = _stale_timeout_minutes()

	frappe.get_doc(RULE_DOCTYPE, rule_code, for_update=True)

	active_runs = frappe.get_all(
		RUN_DOCTYPE,
		filters={"lock_key": lock_key, "status": "Running"},
		fields=["name", "status", "heartbeat_at"],
	)
	for active in active_runs:
		if is_active_run(active.status, get_datetime(active.heartbeat_at), now, timeout):
			frappe.db.commit()
			return None
		frappe.db.set_value(
			RUN_DOCTYPE,
			active.name,
			{"status": "Stale", "ended_at": now, "error_message": "Heartbeat expired"},
			update_modified=False,
		)

	run = frappe.get_doc(
		{
			"doctype": RUN_DOCTYPE,
			"rule": rule_code,
			"company": company,
			"status": "Running",
			"triggered_by": triggered_by,
			"job_id": build_job_id(rule_code, company),
			"lock_key": lock_key,
			"started_at": now,
			"heartbeat_at": now,
			"retry_count": retry_count,
		}
	)
	run.insert(ignore_permissions=True)
	frappe.db.commit()
	return run


def touch_heartbeat(run_name: str) -> None:
	"""Refresh the heartbeat of a running job. Call between batches of long rules."""
	frappe.db.set_value(RUN_DOCTYPE, run_name, "heartbeat_at", now_datetime(), update_modified=False)
	frappe.db.commit()


def finish_run(
	run_name: str,
	status: str,
	started,
	records_scanned: int = 0,
	error: str | None = None,
) -> None:
	ended = now_datetime()
	values = {
		"status": status,
		"ended_at": ended,
		"heartbeat_at": ended,
		"duration_ms": int((ended - get_datetime(started)).total_seconds() * 1000),
		"records_scanned": records_scanned,
		"error_message": (error or "")[:ERROR_MESSAGE_LIMIT] or None,
	}
	frappe.db.set_value(RUN_DOCTYPE, run_name, values, update_modified=False)
	frappe.db.commit()


def enqueue_run(rule_code: str, company: str, triggered_by: str = "Scheduler", retry_count: int = 0) -> None:
	"""Queue a run. A duplicate job for the same rule and company is not queued again."""
	frappe.enqueue(
		"business_radar.monitoring.runner.run_rule",
		queue="long",
		timeout=1800,
		job_id=build_job_id(rule_code, company),
		deduplicate=True,
		rule_code=rule_code,
		company=company,
		triggered_by=triggered_by,
		retry_count=retry_count,
	)


def _stale_timeout_minutes() -> int:
	value = frappe.db.get_single_value(SETTINGS_DOCTYPE, "stale_run_timeout_minutes")
	return cint(value) or DEFAULT_STALE_TIMEOUT_MINUTES


def _update_rule_status(rule_code: str, status: str) -> None:
	frappe.db.set_value(
		RULE_DOCTYPE,
		rule_code,
		{"last_run_on": now_datetime(), "last_status": status},
		update_modified=False,
	)
	frappe.db.commit()
