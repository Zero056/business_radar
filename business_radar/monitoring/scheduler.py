"""Scheduler dispatcher. Runs every 15 minutes and only enqueues work.

For each enabled rule and company it decides whether a run is due (by
frequency), or whether a failed run should be retried (with backoff). Heavy work
happens in the ``long`` queue through ``runner.run_rule``.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from business_radar.monitoring.registry import get_rule
from business_radar.monitoring.run_state import is_active_run, is_due, retry_due
from business_radar.monitoring.runner import RUN_DOCTYPE, RULE_DOCTYPE, enqueue_run

SETTINGS_DOCTYPE = "Business Radar Settings"
DEFAULT_MAX_RETRIES = 3


def dispatch_due_runs(now=None) -> list[tuple[str, str, str]]:
	"""Enqueue due and retryable runs. Returns ``(rule, company, triggered_by)`` tuples."""
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	if not cint(settings.monitoring_enabled):
		return []

	now = now or now_datetime()
	max_retries = cint(settings.max_run_retries) if settings.max_run_retries is not None else DEFAULT_MAX_RETRIES
	stale_timeout = cint(settings.stale_run_timeout_minutes) or 30
	queued: list[tuple[str, str, str]] = []

	rules = frappe.get_all(
		RULE_DOCTYPE,
		filters={"enabled": 1},
		fields=["name", "frequency", "company"],
		limit=0,
	)
	for rule in rules:
		if rule.frequency == "Manual" or get_rule(rule.name) is None:
			continue
		for company in _target_companies(rule.company):
			latest = _latest_run(rule.name, company)
			if latest and latest.status == "Running":
				if is_active_run("Running", get_datetime(latest.heartbeat_at), now, stale_timeout):
					continue

			if latest and latest.status == "Failed" and cint(latest.retry_count) < max_retries:
				if retry_due(cint(latest.retry_count), max_retries, get_datetime(latest.ended_at), now):
					retry_count = cint(latest.retry_count) + 1
					enqueue_run(rule.name, company, triggered_by="Retry", retry_count=retry_count)
					queued.append((rule.name, company, "Retry"))
				continue

			last_attempt = get_datetime(latest.ended_at) if latest and latest.ended_at else None
			if is_due(rule.frequency, last_attempt, now):
				enqueue_run(rule.name, company, triggered_by="Scheduler")
				queued.append((rule.name, company, "Scheduler"))

	return queued


def _target_companies(rule_company: str | None) -> list[str]:
	if rule_company:
		return [rule_company]
	return frappe.get_all("Company", pluck="name", limit=0)


def _latest_run(rule_code: str, company: str):
	rows = frappe.get_all(
		RUN_DOCTYPE,
		filters={"rule": rule_code, "company": company},
		fields=["name", "status", "ended_at", "heartbeat_at", "retry_count"],
		order_by="started_at desc, creation desc",
		limit=1,
	)
	return rows[0] if rows else None
