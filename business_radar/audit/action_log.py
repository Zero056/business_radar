"""Append-only audit trail for sensitive Business Radar operations.

Parameters are stored as a SHA-256 hash, never as plain values, so the log
records what was attempted without copying business data into it.
"""

from __future__ import annotations

import hashlib
import json

import frappe
from frappe.utils import now_datetime

ACTION_LOG_DOCTYPE = "Business Radar Action Log"
ALLOWED_EVENTS = (
	"Attempted",
	"Approved",
	"Rejected",
	"Executed",
	"Failed",
	"Denied",
	"Cancelled",
	"Recorded",
)
ALLOWED_SOURCES = ("User", "Scheduler", "System")
TEXT_LIMIT = 1000


def hash_parameters(parameters: dict | None) -> str | None:
	if parameters is None:
		return None
	canonical = json.dumps(parameters, sort_keys=True, default=str, separators=(",", ":"))
	return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_action(
	action_type: str,
	event: str,
	source: str = "System",
	actor: str | None = None,
	company: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	parameters: dict | None = None,
	result: str | None = None,
	error_message: str | None = None,
) -> str:
	if event not in ALLOWED_EVENTS:
		raise ValueError(f"Unknown audit event: {event!r}")
	if source not in ALLOWED_SOURCES:
		raise ValueError(f"Unknown audit source: {source!r}")

	doc = frappe.get_doc(
		{
			"doctype": ACTION_LOG_DOCTYPE,
			"occurred_at": now_datetime(),
			"event": event,
			"action_type": action_type[:140],
			"actor": actor or frappe.session.user,
			"source": source,
			"company": company,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"parameters_hash": hash_parameters(parameters),
			"result": (result or "")[:TEXT_LIMIT] or None,
			"error_message": (error_message or "")[:TEXT_LIMIT] or None,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name
