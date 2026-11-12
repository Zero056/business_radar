"""Code registry of monitoring rules.

Rules are Python callables registered here. Configuration in the
``Business Radar Monitoring Rule`` DocType can only select, enable and tune
registered rules. It can never supply expressions, SQL, or code.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from business_radar.constants import CATEGORIES, SEVERITIES

RULE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

_REGISTRY: dict[str, "RuleDefinition"] = {}
_BUILTIN_RULES_LOADED = False


@dataclass(frozen=True)
class RuleContext:
	"""Everything a rule may read. Data access goes through ``data_source`` only."""

	rule_code: str
	company: str
	period_start: Any
	period_end: Any
	scope: Any
	parameters: Mapping[str, float]
	min_samples: int


@dataclass
class RuleOutcome:
	records_scanned: int = 0
	findings: list = field(default_factory=list)
	insufficient_data: bool = False


@dataclass(frozen=True)
class RuleDefinition:
	code: str
	name: str
	category: str
	target_doctype: str
	description: str
	evaluate: Callable[[RuleContext], RuleOutcome]
	default_frequency: str = "Daily"
	default_cooldown_hours: int = 24
	default_lookback_days: int = 90
	min_samples: int = 0
	default_parameters: Mapping[str, float] = field(default_factory=dict)
	default_severity_bands: tuple = ()
	default_enabled: bool = False


def register_rule(definition: RuleDefinition) -> None:
	code = definition.code
	if not RULE_CODE_PATTERN.match(code):
		raise ValueError(f"Invalid rule code: {code!r}")
	if code in _REGISTRY:
		raise ValueError(f"Duplicate rule code: {code!r}")
	if definition.category not in CATEGORIES:
		raise ValueError(f"Unknown category for {code}: {definition.category!r}")
	for _threshold, severity in definition.default_severity_bands:
		if severity not in SEVERITIES:
			raise ValueError(f"Unknown severity for {code}: {severity!r}")
	if not callable(definition.evaluate):
		raise ValueError(f"Rule {code} has no evaluator")
	_REGISTRY[code] = definition


def unregister_rule(code: str) -> None:
	"""Remove a rule. Intended for tests only."""
	_REGISTRY.pop(code, None)


def load_builtin_rules() -> None:
	global _BUILTIN_RULES_LOADED
	if _BUILTIN_RULES_LOADED:
		return
	import business_radar.monitoring.rules  # noqa: F401  (registers rules on import)

	_BUILTIN_RULES_LOADED = True


def get_rule(code: str) -> RuleDefinition | None:
	load_builtin_rules()
	return _REGISTRY.get(code)


def get_registered_rules() -> dict[str, RuleDefinition]:
	load_builtin_rules()
	return dict(_REGISTRY)


def validate_parameters(code: str, parameters: Mapping[str, float]) -> None:
	"""Reject parameter keys the rule does not declare."""
	definition = get_rule(code)
	if definition is None:
		raise ValueError(f"Rule {code!r} is not registered")
	unknown = sorted(set(parameters) - set(definition.default_parameters))
	if unknown:
		raise ValueError(f"Unknown parameters for {code}: {', '.join(unknown)}")
