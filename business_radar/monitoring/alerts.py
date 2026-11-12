"""Turns rule findings into alerts, safely and idempotently.

Identity: an alert is identified by ``fingerprint = sha256(rule | entity)``. A
finding never creates a second alert for the same entity.

For each finding:

* No alert yet: create one (status New), attach evidence and a recommendation,
  and notify managers for High and Critical severity.
* Open alert: refresh its values and evidence, keep its status, and notify only
  if severity went up to High or Critical.
* Closed alert within its cooldown: count it as suppressed and leave it alone.
* Closed alert after its cooldown: reopen it (status New). Reopens are counted,
  and the alert is notified again.

After a complete run, open alerts of this rule and company that were not detected
again are auto-resolved with a system resolution. An incomplete run never does
this, because it has not seen every entity.
"""

from __future__ import annotations

import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime

from business_radar.constants import ROLE_ADMINISTRATOR, ROLE_MANAGER
from business_radar.monitoring.findings import Finding, build_fingerprint

ALERT_DOCTYPE = "Business Radar Alert"
RECOMMENDATION_DOCTYPE = "Business Radar Recommendation"
OPEN_STATUSES = ("New", "Acknowledged", "Investigating", "Action Required", "Waiting for Approval", "In Progress")
CLOSED_STATUSES = ("Resolved", "Dismissed", "False Positive")
SEVERITY_RANK = {"Critical": 5, "High": 4, "Medium": 3, "Low": 2, "Informational": 1}
NOTIFY_SEVERITIES = ("Critical", "High")
MAX_EVIDENCE_ROWS = 50
RECOMMENDATION_ACTION_BY_CATEGORY = {
	"Financial": "Internal Follow-up",
	"Sales": "Internal Follow-up",
	"Inventory": "Review Stock Level",
	"Operations": "Open Document",
}


def persist_outcome(rule_code: str, category: str, company: str, outcome, cooldown_hours: int, now) -> dict:
	counts = {"created": 0, "updated": 0, "suppressed": 0, "resolved": 0}
	seen: set[str] = set()

	for item in outcome.findings:
		finding = item if isinstance(item, Finding) else Finding(**item)
		fingerprint = build_fingerprint(rule_code, finding.entity_key)
		seen.add(fingerprint)
		counts[_upsert(rule_code, category, company, finding, fingerprint, cooldown_hours, now)] += 1

	if outcome.complete and not outcome.insufficient_data:
		counts["resolved"] = _auto_resolve(rule_code, company, seen, cooldown_hours, now)
	return counts


def _upsert(rule_code, category, company, finding, fingerprint, cooldown_hours, now) -> str:
	existing = frappe.db.get_value(
		ALERT_DOCTYPE,
		{"fingerprint": fingerprint},
		["name", "status", "severity", "cooldown_until"],
		as_dict=True,
	)
	if not existing:
		_create(rule_code, category, company, finding, fingerprint, now)
		return "created"

	if existing.status in OPEN_STATUSES:
		_refresh(existing.name, finding, now)
		return "updated"

	if existing.cooldown_until and now < get_datetime(existing.cooldown_until):
		return "suppressed"

	_reopen(existing.name, finding, now)
	return "updated"


def _create(rule_code, category, company, finding, fingerprint, now) -> None:
	doc = frappe.get_doc(
		{
			"doctype": ALERT_DOCTYPE,
			"alert_title": _truncate(finding.title, 140),
			"fingerprint": fingerprint,
			"rule": rule_code,
			"rule_code": rule_code,
			"category": category,
			"severity": finding.severity,
			"status": "New",
			"company": company,
			"evidence_type": finding.evidence_type,
			"related_doctype": finding.related_doctype,
			"related_name": finding.related_name,
			"affected_customer": finding.affected_customer,
			"affected_item": finding.affected_item,
			"unit": finding.unit,
			"detected_value": finding.detected_value,
			"expected_value": finding.expected_value,
			"deviation": _deviation(finding),
			"detected_on": now,
			"last_evaluated_on": now,
			"occurrences": 1,
			"explanation": finding.explanation,
			"potential_impact": finding.potential_impact,
			"recommended_actions": "\n".join(finding.recommended_actions),
			"data_snapshot": json.dumps(finding.snapshot, default=str, sort_keys=True),
		}
	)
	_set_evidence(doc, finding)
	doc.insert(ignore_permissions=True)
	_create_recommendation(doc, category, finding)
	_notify(doc, _("New {0} alert: {1}").format(doc.severity, doc.alert_title))


def _refresh(name: str, finding: Finding, now) -> None:
	doc = frappe.get_doc(ALERT_DOCTYPE, name)
	previous_rank = SEVERITY_RANK.get(doc.severity, 0)
	doc.severity = finding.severity
	doc.alert_title = _truncate(finding.title, 140)
	doc.detected_value = finding.detected_value
	doc.expected_value = finding.expected_value
	doc.deviation = _deviation(finding)
	doc.last_evaluated_on = now
	doc.occurrences = (doc.occurrences or 0) + 1
	doc.explanation = finding.explanation
	doc.potential_impact = finding.potential_impact
	doc.recommended_actions = "\n".join(finding.recommended_actions)
	doc.data_snapshot = json.dumps(finding.snapshot, default=str, sort_keys=True)
	_set_evidence(doc, finding)
	doc.flags.system_update = True
	doc.save(ignore_permissions=True)
	if SEVERITY_RANK.get(finding.severity, 0) > previous_rank and finding.severity in NOTIFY_SEVERITIES:
		_notify(doc, _("Alert escalated to {0}: {1}").format(finding.severity, doc.alert_title))


def _reopen(name: str, finding: Finding, now) -> None:
	doc = frappe.get_doc(ALERT_DOCTYPE, name)
	doc.status = "New"
	doc.reopen_count = (doc.reopen_count or 0) + 1
	doc.closed_on = None
	doc.cooldown_until = None
	doc.resolved_by = None
	doc.resolved_on = None
	doc.severity = finding.severity
	doc.detected_value = finding.detected_value
	doc.expected_value = finding.expected_value
	doc.deviation = _deviation(finding)
	doc.detected_on = now
	doc.last_evaluated_on = now
	doc.occurrences = (doc.occurrences or 0) + 1
	doc.explanation = finding.explanation
	doc.potential_impact = finding.potential_impact
	doc.recommended_actions = "\n".join(finding.recommended_actions)
	doc.data_snapshot = json.dumps(finding.snapshot, default=str, sort_keys=True)
	_set_evidence(doc, finding)
	doc.flags.system_reopen = True
	doc.save(ignore_permissions=True)
	if finding.severity in NOTIFY_SEVERITIES:
		_notify(doc, _("Alert reopened: {0}").format(doc.alert_title))


def _auto_resolve(rule_code: str, company: str, seen: set[str], cooldown_hours: int, now) -> int:
	filters = {"rule_code": rule_code, "company": company, "status": ["in", list(OPEN_STATUSES)]}
	if seen:
		filters["fingerprint"] = ["not in", list(seen)]
	names = frappe.get_all(ALERT_DOCTYPE, filters=filters, pluck="name", limit=0)
	for name in names:
		doc = frappe.get_doc(ALERT_DOCTYPE, name)
		doc.status = "Resolved"
		doc.resolution = _(
			"Resolved automatically: the condition was not detected in the latest complete monitoring run."
		)
		doc.resolved_by = None
		doc.resolved_on = now
		doc.closed_on = now
		doc.cooldown_until = add_to_date(now, hours=cooldown_hours)
		doc.flags.system_close = True
		doc.save(ignore_permissions=True)
	return len(names)


def _set_evidence(doc, finding: Finding) -> None:
	doc.set("evidence", [])
	allowed = {
		"evidence_order",
		"fact_label",
		"fact_value",
		"source_doctype",
		"source_name",
		"source_field",
		"calculation",
		"as_of",
	}
	for row in finding.evidence[:MAX_EVIDENCE_ROWS]:
		doc.append("evidence", {key: value for key, value in row.items() if key in allowed})


def _create_recommendation(alert, category: str, finding: Finding) -> None:
	if not finding.recommended_actions:
		return
	frappe.get_doc(
		{
			"doctype": RECOMMENDATION_DOCTYPE,
			"title": _truncate(finding.recommended_actions[0], 140),
			"alert": alert.name,
			"company": alert.company,
			"action_type": RECOMMENDATION_ACTION_BY_CATEGORY.get(category, "Open Document"),
			"priority": alert.severity,
			"status": "Proposed",
			"source": "System",
			"description": "\n".join(finding.recommended_actions),
			"risk": alert.potential_impact,
			"related_doctype": alert.related_doctype,
			"related_name": alert.related_name,
			"approval_required": 1,
			"requested_by": "Administrator",
		}
	).insert(ignore_permissions=True)


def _notify(alert, subject: str) -> None:
	if alert.severity not in NOTIFY_SEVERITIES and not alert.assigned_to:
		return
	for user in _recipients(alert):
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"subject": subject,
				"for_user": user,
				"from_user": "Administrator",
				"type": "Alert",
				"document_type": ALERT_DOCTYPE,
				"document_name": alert.name,
			}
		).insert(ignore_permissions=True)


def _recipients(alert) -> list[str]:
	users = set(
		frappe.get_all(
			"Has Role",
			filters={"parenttype": "User", "role": ["in", [ROLE_ADMINISTRATOR, ROLE_MANAGER]]},
			pluck="parent",
			limit=0,
		)
	)
	if alert.assigned_to:
		users.add(alert.assigned_to)
	users.discard("Guest")
	if not users:
		return []
	enabled = set(frappe.get_all("User", filters={"name": ["in", list(users)], "enabled": 1}, pluck="name", limit=0))
	return sorted(enabled)


def _deviation(finding: Finding) -> float | None:
	if finding.detected_value is None or finding.expected_value is None:
		return None
	return round(finding.detected_value - finding.expected_value, 4)


def _truncate(text: str, limit: int) -> str:
	return text if len(text) <= limit else text[: limit - 1] + "…"


def closed_alert_cooldown(now, cooldown_hours: int):
	return now + timedelta(hours=cooldown_hours)
