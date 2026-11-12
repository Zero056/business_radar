"""Pure calculation helpers for KPIs and detection rules.

This module must not import Frappe. Every function returns ``None`` when the
result is mathematically undefined (for example a zero denominator). Callers
must present ``None`` as "n/a", never as zero.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal


def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
	if numerator is None or denominator is None or denominator == 0:
		return None
	return numerator / denominator


def percent_change(current: float | None, previous: float | None) -> float | None:
	"""Relative change as a ratio: ``(current - previous) / abs(previous)``.

	Returns ``None`` when ``previous`` is zero because the change is undefined.
	Using ``abs(previous)`` keeps the sign meaningful when the previous value is
	negative (for example, a period dominated by credit notes).
	"""
	if current is None or previous is None or previous == 0:
		return None
	return (current - previous) / abs(previous)


def collection_rate(allocated: float | None, invoiced: float | None) -> float | None:
	"""Allocated payments divided by invoiced amount for the same period, as a ratio.

	Returns ``None`` when nothing was invoiced. A ratio above 1 is possible when
	payments in the period settle invoices from earlier periods.
	"""
	if invoiced is None or invoiced <= 0:
		return None
	return safe_divide(allocated or 0.0, invoiced)


def gross_margin(gross_profit: float | None, revenue: float | None) -> float | None:
	return safe_divide(gross_profit, revenue)


def previous_period(start: date, end: date) -> tuple[date, date]:
	"""Equal-length period immediately preceding ``[start, end]``."""
	if start > end:
		raise ValueError("period start must not be after period end")
	length = (end - start).days + 1
	previous_end = start - timedelta(days=1)
	previous_start = previous_end - timedelta(days=length - 1)
	return previous_start, previous_end


def round_half_up(value: float | Decimal | None, places: int = 2) -> float | None:
	"""Round money-like values half-up, as ERPNext does for display amounts."""
	if value is None:
		return None
	quantum = Decimal(1).scaleb(-places)
	return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def mean_and_sample_stdev(values: list[float]) -> tuple[float, float] | None:
	"""Mean and sample standard deviation (n-1). Requires at least two values."""
	n = len(values)
	if n < 2:
		return None
	mean = sum(values) / n
	variance = sum((v - mean) ** 2 for v in values) / (n - 1)
	return mean, math.sqrt(variance)


def z_score(value: float | None, mean: float | None, stdev: float | None) -> float | None:
	if value is None or mean is None or stdev is None or stdev <= 0:
		return None
	return (value - mean) / stdev


def days_between(earlier: date, later: date) -> int:
	return (later - earlier).days
