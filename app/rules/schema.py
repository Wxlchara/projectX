"""Validate rule parameters and field paths before any data reaches services."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from app.common.models import (
    AllCondition, AnyCondition, ApplicantProfile, ComparisonCondition, Condition,
    Model, Money, Question, Rule, Scenario, Severity, TripInput,
)

PROFILE_FIELDS = {f"profile.{name}" for name in ApplicantProfile.model_fields} - {"profile.id", "profile.revision"}
TRIP_FIELDS = {f"trip.{name}" for name in TripInput.model_fields}


def condition_fields(condition: Condition | None) -> set[str]:
    if condition is None:
        return set()
    if isinstance(condition, ComparisonCondition):
        return {condition.field}
    children = condition.all if isinstance(condition, AllCondition) else condition.any
    return set().union(*(condition_fields(child) for child in children))


def validate_condition(condition: Condition | None, questions: dict[str, Question], depth=0) -> None:
    if condition is None:
        return
    if depth > 8:
        raise ValueError("Condition nesting exceeds 8")
    if isinstance(condition, (AllCondition, AnyCondition)):
        children = condition.all if isinstance(condition, AllCondition) else condition.any
        if not children or len(children) > 30:
            raise ValueError("Logical conditions need 1–30 children")
        for child in children:
            validate_condition(child, questions, depth + 1)
        return
    allowed = PROFILE_FIELDS | TRIP_FIELDS | {f"answers.{key}" for key in questions}
    if condition.field not in allowed:
        raise ValueError("Unknown condition field")
    if condition.op == "in" and (not isinstance(condition.value, list) or not condition.value):
        raise ValueError("in requires a nonempty list")
    if condition.op == "exists" and condition.value is not True:
        raise ValueError("exists requires value=true; missing values stay unknown")
    if condition.op not in ("in", "exists") and condition.value is None:
        raise ValueError("A comparison with null cannot prove a condition")
    if condition.op == "exists":
        return
    field = condition.field
    if field.startswith("answers."):
        value_type = questions[field.split(".", 1)[1]].value_type
    elif field in {"profile.has_other_citizenship", "profile.has_foreign_residence_permit"}:
        value_type = "boolean"
    elif field in {"profile.birth_date", "profile.passport_issued_on", "profile.passport_expires_on", "trip.starts_on", "trip.ends_on"}:
        value_type = "date"
    elif field == "profile.existing_permissions":
        raise ValueError("Permission lists support exists only")
    else:
        value_type = "enum"
    if value_type == "stays" or (condition.op in ("gte", "lte") and value_type not in ("integer", "date")):
        raise ValueError("Operator incompatible with field type")
    for value in condition.value if condition.op == "in" else [condition.value]:
        expected_type = {"boolean": bool, "integer": int, "date": str, "enum": str}[value_type]
        if type(value) is not expected_type:
            raise ValueError("Condition constant has wrong type")
        if value_type == "date":
            if len(value) != 10:
                raise ValueError("Expected ISO date")
            date.fromisoformat(value)
        if field.startswith("answers.") and value_type == "enum" and value not in questions[field.split(".", 1)[1]].choices:
            raise ValueError("Unknown enum constant")


class RequirementParameters(Model):
    pass


class AssertionParameters(Model):
    condition: Condition
    expected: StrictBool = True
    message: str
    next_action: str
    severity: Severity = Severity.BLOCKING


class PassportValidityParameters(Model):
    anchor: Literal["trip.starts_on", "trip.ends_on", "answers.application_date"]
    months: Annotated[int, Field(strict=True, ge=0, le=24)] = 0
    days: Annotated[int, Field(strict=True, ge=0, le=366)] = 0
    severity: Severity = Severity.BLOCKING


class PassportMaxAgeParameters(Model):
    anchor: Literal["trip.starts_on", "answers.application_date"]
    years: Annotated[int, Field(strict=True, ge=1, le=20)]
    allow_anniversary: StrictBool = True


class StayLimitParameters(Model):
    days: Annotated[int, Field(strict=True, ge=1, le=366)]
    window_days: Annotated[int, Field(strict=True, ge=1, le=730)] | None = None
    history_question: str | None = None
    history_mode: Literal["rolling", "manual"] = "manual"

    @model_validator(mode="after")
    def valid_window(self):
        if self.window_days is not None and self.days > self.window_days:
            raise ValueError("Stay limit exceeds its window")
        if self.history_mode == "rolling" and (not self.window_days or not self.history_question):
            raise ValueError("Rolling windows need history and window_days")
        return self


class InsuranceParameters(Model):
    territory: str
    minimum_cover: Money | None = None
    covers_trip: bool = True


class FundsParameters(Model):
    minimum: Money | None = None
    period: Literal["trip", "month"] = "trip"
    # Applying monthly amounts/proration and document verification belongs to 6.
    calculation: Literal["manual"] = "manual"


class ManualReviewParameters(Model):
    message: str
    next_action: str


PARAMETER_MODELS = {
    "requirement": RequirementParameters,
    "assertion": AssertionParameters,
    "passport_validity": PassportValidityParameters,
    "passport_max_age": PassportMaxAgeParameters,
    "stay_limit": StayLimitParameters,
    "insurance_coverage": InsuranceParameters,
    "minimum_funds": FundsParameters,
    "manual_review": ManualReviewParameters,
}


def parse_parameters(rule: Rule):
    model = PARAMETER_MODELS.get(rule.kind)
    if model is None:
        raise ValueError(f"Unknown rule kind: {rule.kind}")
    return model.model_validate(rule.parameters)


class CountryDefinition(Model):
    name: str
    scenario: Scenario
    rules: list[Rule]

    @model_validator(mode="after")
    def valid_references(self):
        scenario = self.scenario
        requirements = {item.id: item for item in scenario.requirements}
        questions = {item.key: item for item in scenario.questions}
        sources = {item.id: item for item in scenario.sources}
        if len(requirements) != len(scenario.requirements) or len(questions) != len(scenario.questions):
            raise ValueError("Duplicate requirement/question")
        if len(sources) != len(scenario.sources) or len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError("Duplicate source/rule")
        if not scenario.entry_source_ids or not set(scenario.entry_source_ids) <= sources.keys():
            raise ValueError("Entry mode needs referenced sources")
        if scenario.coverage == "partial" and not scenario.unverified_conditions:
            raise ValueError("Partial coverage must disclose its gaps")
        if scenario.coverage == "complete" and scenario.unverified_conditions:
            raise ValueError("Complete coverage cannot have known gaps")
        for item in [*scenario.requirements, *scenario.process_stages, *scenario.questions]:
            if not item.source_ids or not set(item.source_ids) <= sources.keys():
                raise ValueError("Unknown/empty source references")
            validate_condition(getattr(item, "applies_if", None), questions)
        if len({stage.id for stage in scenario.process_stages}) != len(scenario.process_stages):
            raise ValueError("Duplicate process stage")
        for rule in self.rules:
            if rule.version != scenario.version or rule.scenario_id != scenario.id:
                raise ValueError("Rule version/scenario mismatch")
            if rule.requirement_id and rule.requirement_id not in requirements:
                raise ValueError("Unknown requirement reference")
            if not rule.source_ids or not set(rule.source_ids) <= sources.keys():
                raise ValueError("Unknown/empty rule sources")
            validate_condition(rule.applies_if, questions)
            parameters = parse_parameters(rule)
            if isinstance(parameters, AssertionParameters):
                validate_condition(parameters.condition, questions)
            if hasattr(parameters, "anchor") and parameters.anchor.startswith("answers."):
                key = parameters.anchor.split(".", 1)[1]
                if key not in questions or questions[key].value_type != "date":
                    raise ValueError("Date anchor needs a date question")
            if isinstance(parameters, StayLimitParameters) and parameters.history_question:
                question = questions.get(parameters.history_question)
                if question is None or question.value_type != "stays":
                    raise ValueError("History needs a stays question")
        for requirement in scenario.requirements:
            if not any(rule.requirement_id == requirement.id for rule in self.rules):
                raise ValueError("Every requirement needs a rule")
        return self
