"""Allowed condition operators. No arbitrary expressions."""

from __future__ import annotations

from typing import Any, Mapping

from .errors import SkillError
from .models import Condition

SUPPORTED_OPS = frozenset({
    "eq",
    "ne",
    "in",
    "not_in",
    "contains",
    "gt",
    "gte",
    "lt",
    "lte",
    "exists",
    "not_exists",
})

SUPPORTED_FIELDS = frozenset({
    "intent",
    "entrypoint",
    "scan_status",
    "focus_count",
    "phase",
    "state",
    "waiting_reason",
    "last_termination",
    "event_reason",
    "event_reasons",
    "ready",
    "restarts",
    "present",
    "has_focus",
    "successful_tools",
    "stage_id",
    "capability_missing",
})


def validate_condition(condition: Condition, *, skill_id: str = "", path: str = "") -> None:
    if condition.field not in SUPPORTED_FIELDS:
        raise SkillError(
            f"unsupported condition field {condition.field!r}",
            skill_id=skill_id,
            path=path,
            field=condition.field,
        )
    if condition.op not in SUPPORTED_OPS:
        raise SkillError(
            f"unsupported operator {condition.op!r}",
            skill_id=skill_id,
            path=path,
            field=condition.field,
        )


def eval_condition(condition: Condition, facts: Mapping[str, Any]) -> bool:
    validate_condition(condition)
    actual = facts.get(condition.field)
    op = condition.op
    expected = condition.value
    if op == "exists":
        return actual not in (None, "", [], ())
    if op == "not_exists":
        return actual in (None, "", [], ())
    if op == "eq":
        return _norm(actual) == _norm(expected)
    if op == "ne":
        return _norm(actual) != _norm(expected)
    if op == "in":
        return _norm(actual) in {_norm(item) for item in _as_list(expected)}
    if op == "not_in":
        return _norm(actual) not in {_norm(item) for item in _as_list(expected)}
    if op == "contains":
        if isinstance(actual, (list, tuple, set, frozenset)):
            needle = _norm(expected)
            return any(_norm(item) == needle or needle in _norm(item) for item in actual)
        return _norm(expected) in _norm(actual)
    if op in {"gt", "gte", "lt", "lte"}:
        left = _as_number(actual)
        right = _as_number(expected)
        if left is None or right is None:
            return False
        if op == "gt":
            return left > right
        if op == "gte":
            return left >= right
        if op == "lt":
            return left < right
        return left <= right
    return False


def eval_all(conditions: list[Condition], facts: Mapping[str, Any]) -> bool:
    if not conditions:
        return True
    return all(eval_condition(item, facts) for item in conditions)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().lower()


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None
