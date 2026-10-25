"""Only allowlisted data paths and explicit operators; null never means false."""

from dataclasses import dataclass
from datetime import date

from app.common.models import AllCondition, Applicability, ComparisonCondition, Condition


@dataclass(frozen=True)
class ConditionResult:
    value: Applicability
    missing: frozenset[str] = frozenset()


def lookup(context, field):
    group, separator, key = field.partition(".")
    if not separator or group not in ("profile", "trip", "answers") or "." in key or key.startswith("_"):
        raise ValueError("Unsupported field path")
    return context[group].get(key)


def strict_equal(actual, expected):
    if isinstance(actual, date) and isinstance(expected, str):
        try:
            expected = date.fromisoformat(expected)
        except ValueError:
            return False
    return type(actual) is type(expected) and actual == expected


def evaluate_condition(condition: Condition | None, context, depth=0) -> ConditionResult:
    if condition is None:
        return ConditionResult(Applicability.YES)
    if depth > 8:
        raise ValueError("Condition is too deep")
    if not isinstance(condition, ComparisonCondition):
        children = condition.all if isinstance(condition, AllCondition) else condition.any
        if not children:
            raise ValueError("Empty logical condition")
        results = [evaluate_condition(child, context, depth + 1) for child in children]
        is_all = isinstance(condition, AllCondition)
        decisive = Applicability.NO if is_all else Applicability.YES
        if any(result.value == decisive for result in results):
            return ConditionResult(decisive)
        if any(result.value == Applicability.UNKNOWN for result in results):
            missing = frozenset().union(*(result.missing for result in results))
            return ConditionResult(Applicability.UNKNOWN, missing)
        return ConditionResult(Applicability.YES if is_all else Applicability.NO)
    actual = lookup(context, condition.field)
    if actual is None or actual == "":
        return ConditionResult(Applicability.UNKNOWN, frozenset({condition.field}))
    expected = condition.value
    if condition.op == "exists":
        passed = True
    elif condition.op == "eq":
        passed = strict_equal(actual, expected)
    elif condition.op == "in":
        passed = any(strict_equal(actual, item) for item in expected)
    else:
        if isinstance(actual, date) and isinstance(expected, str):
            expected = date.fromisoformat(expected)
        if isinstance(actual, bool) or type(actual) is not type(expected):
            raise ValueError("Incompatible comparison types")
        passed = actual >= expected if condition.op == "gte" else actual <= expected
    return ConditionResult(Applicability.YES if passed else Applicability.NO)
