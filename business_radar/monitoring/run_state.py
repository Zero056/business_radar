"""Pure scheduling and lock-state decisions for detection runs. No Frappe imports."""

from __future__ import annotations

from datetime import datetime, timedelta

FREQUENCY_MINUTES = {
	"15 Minutes": 15,
	"Hourly": 60,
	"Daily": 24 * 60,
	"Weekly": 7 * 24 * 60,
	"Manual": None,
}

# Minutes to wait before retry attempt 1, 2, 3, ... (last value repeats).
RETRY_BACKOFF_MINUTES = (5, 15, 60)

ACTIVE_STATUSES = ("Running",)


def is_due(frequency: str, last_attempt_at: datetime | None, now: datetime) -> bool:
	"""Whether a rule with ``frequency`` should run now, given its last terminal attempt."""
	if frequency not in FREQUENCY_MINUTES:
		raise ValueError(f"Unknown frequency: {frequency!r}")
	minutes = FREQUENCY_MINUTES[frequency]
	if minutes is None:
		return False
	if last_attempt_at is None:
		return True
	return now - last_attempt_at >= timedelta(minutes=minutes)


def retry_due(retry_count: int, max_retries: int, failed_at: datetime | None, now: datetime) -> bool:
	"""Whether a failed run with ``retry_count`` retries already used may be retried."""
	if failed_at is None or retry_count >= max_retries:
		return False
	index = min(max(retry_count, 0), len(RETRY_BACKOFF_MINUTES) - 1)
	return now - failed_at >= timedelta(minutes=RETRY_BACKOFF_MINUTES[index])


def is_active_run(status: str, heartbeat_at: datetime | None, now: datetime, timeout_minutes: int) -> bool:
	"""A run blocks new runs only while it is Running and its heartbeat is fresh."""
	if status not in ACTIVE_STATUSES or heartbeat_at is None:
		return False
	return now - heartbeat_at <= timedelta(minutes=timeout_minutes)
