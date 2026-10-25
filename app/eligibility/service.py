"""Applicability of declared profile/trip inputs, separate from document checks."""

from datetime import date, datetime, timedelta, timezone

from app.common.dates import add_calendar_months
from app.common.errors import AppError
from app.common.models import (
    Answers, ApplicantProfile, Applicability, ApplicabilityResult, AppliedRequirement, DataQuality,
    DestinationComparison, Finding, RuleRef, Scenario, Severity, TripInput,
)
from app.eligibility.answers import validate_answers
from app.eligibility.conditions import evaluate_condition, lookup
from app.rules.registry import FileRuleRegistry, effective_scenario, source_quality
from app.rules.schema import (
    AssertionParameters, FundsParameters, ManualReviewParameters,
    PassportMaxAgeParameters, PassportValidityParameters, StayLimitParameters,
    condition_fields, parse_parameters,
)


def technical_finding(code, message, *, fields=None, sources=None):
    return Finding(
        code=code, severity="verify", message=message,
        next_action="Уточните условие или проверьте соответствующий официальный источник.",
        field_paths=fields or [], source_refs=sources or [], origin="technical",
    )


class EligibilityService:
    def __init__(self, registry: FileRuleRegistry):
        self.registry = registry
        self.questions = {}
        for destination in registry.destinations():
            for question in registry.get_scenario(destination.scenario_id).questions:
                previous = self.questions.get(question.key)
                if previous and (
                    previous.value_type, previous.choices, previous.minimum, previous.maximum
                ) != (question.value_type, question.choices, question.minimum, question.maximum):
                    raise ValueError("Inconsistent shared answer schema")
                self.questions[question.key] = question

    def evaluate_applicability(
        self, profile: ApplicantProfile, trip: TripInput, answers: Answers, scenario: Scenario,
        *, as_of: date | None = None,
    ) -> ApplicabilityResult:
        normalized = validate_answers(answers, {question.key: question for question in scenario.questions})
        return self._evaluate(profile, trip, normalized, scenario, as_of=as_of)

    def _evaluate(self, profile, trip, answers, scenario, *, as_of=None):
        today = as_of or datetime.now(timezone.utc).date()
        scenario = effective_scenario(scenario, today)
        if trip.destination_country and trip.destination_country != scenario.destination_country:
            raise AppError(422, "scenario_trip_mismatch", "Страна поездки не совпадает со сценарием.")
        context = {"profile": profile.model_dump(), "trip": trip.model_dump(), "answers": answers}
        missing, unsupported = set(), []
        for field, expected in (
            ("profile.citizenship", "RU"), ("profile.residence_country", "RU"),
            ("profile.has_other_citizenship", False), ("profile.has_foreign_residence_permit", False),
            ("trip.purpose", "tourism"),
        ):
            value = lookup(context, field)
            if value is None:
                missing.add(field)
            elif value != expected:
                unsupported.append(field)
        for field in ("profile.birth_date", "trip.starts_on", "trip.ends_on"):
            if lookup(context, field) is None:
                missing.add(field)
        if profile.birth_date and trip.starts_on:
            age = trip.starts_on.year - profile.birth_date.year - (
                (trip.starts_on.month, trip.starts_on.day) < (profile.birth_date.month, profile.birth_date.day)
            )
            if age < 18:
                unsupported.append("profile.birth_date")
        if unsupported:
            return ApplicabilityResult(
                status="unsupported_profile", requirements=[], missing_answers=[],
                barriers=[technical_finding(
                    "unsupported_profile", "Прототип покрывает одного взрослого туриста с гражданством и проживанием в РФ, без другого гражданства/ВНЖ.",
                    fields=sorted(unsupported),
                )],
            )
        if missing:
            return ApplicabilityResult(
                status="needs_input", requirements=[], barriers=[], missing_answers=sorted(missing),
                unknown_conditions=[technical_finding(
                    "profile_scope_unknown", "Для персональных требований сначала нужны сведения о поддерживаемом профиле и датах поездки.",
                    fields=sorted(missing),
                )],
            )
        ruleset = self.registry.get_ruleset(scenario.version, as_of=today)
        rules = [rule for rule in ruleset.rules if rule.scenario_id == scenario.id]
        sources = {source.id: source for source in scenario.sources}
        unknown, barriers, applied = {}, [], []

        def unverified_sources(source_ids, mandatory=True):
            bad = False
            for source_id in source_ids:
                source = sources.get(source_id)
                if source is None or source_quality(source, today) != DataQuality.SOURCE_CHECKED:
                    bad = True
                    finding = technical_finding(
                        "source_needs_verification", f"Источник {source_id} не подтверждён или требует пересмотра.", sources=[source_id],
                    )
                    if not mandatory:
                        finding.severity = Severity.INFO
                    key = "source:" + source_id
                    if key not in unknown or mandatory:
                        unknown[key] = finding
            return bad

        unverified_sources(scenario.entry_source_ids)
        for requirement in scenario.requirements:
            trace = evaluate_condition(requirement.applies_if, context)
            source_unknown = unverified_sources(requirement.source_ids, requirement.mandatory)
            if trace.value == Applicability.NO and not source_unknown:
                continue
            applicability = Applicability.UNKNOWN if source_unknown else trace.value
            if not source_unknown and requirement.mandatory:
                missing.update(trace.missing)
            refs = [RuleRef(id=rule.id, version=rule.version) for rule in rules if rule.requirement_id == requirement.id]
            applied.append(AppliedRequirement(
                **requirement.model_dump(), applicability=applicability, rule_refs=refs,
                explanation=(
                    "Нужно подтвердить источник требования." if source_unknown else
                    "Применимость зависит от неизвестного ответа." if applicability == Applicability.UNKNOWN else
                    "Применимо по анкете; наличие и проверка доказательства ещё не установлены."
                ),
            ))
        for rule in rules:
            if unverified_sources(rule.source_ids, rule.mandatory):
                continue
            trace = evaluate_condition(rule.applies_if, context)
            if trace.value == Applicability.NO:
                continue
            if trace.value == Applicability.UNKNOWN:
                if rule.mandatory:
                    missing.update(trace.missing)
                continue
            parameters = parse_parameters(rule)

            def finding(code, message, next_action, fields, severity="blocking"):
                item = Finding(
                    code=code, severity=severity, message=message, next_action=next_action,
                    field_paths=fields, rule_ref=RuleRef(id=rule.id, version=rule.version),
                    source_refs=rule.source_ids, origin="rule",
                )
                if severity in ("blocking", "fix"):
                    barriers.append(item)
                else:
                    unknown[rule.id] = item

            if isinstance(parameters, AssertionParameters):
                assertion = evaluate_condition(parameters.condition, context)
                if assertion.value == Applicability.UNKNOWN:
                    missing.update(assertion.missing)
                elif (assertion.value == Applicability.YES) != parameters.expected:
                    finding("condition_not_met", parameters.message, parameters.next_action,
                            sorted(condition_fields(parameters.condition)), parameters.severity)
            elif isinstance(parameters, (PassportValidityParameters, PassportMaxAgeParameters)):
                profile_field = "profile.passport_expires_on" if isinstance(parameters, PassportValidityParameters) else "profile.passport_issued_on"
                value, anchor = lookup(context, profile_field), lookup(context, parameters.anchor)
                absent = {path for path, item in ((profile_field, value), (parameters.anchor, anchor)) if item is None}
                missing.update(absent)
                if absent:
                    continue
                try:
                    if isinstance(parameters, PassportValidityParameters):
                        threshold = add_calendar_months(anchor, parameters.months) + timedelta(days=parameters.days)
                        if value < threshold:
                            finding(
                                "passport_validity", f"По заявленным датам паспорт должен действовать как минимум до {threshold.isoformat()}.",
                                "Проверьте дату, замените паспорт или пересмотрите даты поездки.",
                                [profile_field, parameters.anchor], parameters.severity,
                            )
                    else:
                        threshold = add_calendar_months(anchor, -12 * parameters.years)
                        if value < threshold or (value == threshold and not parameters.allow_anniversary):
                            finding("passport_age", "Дата выдачи паспорта выходит за разрешённый календарный срок.",
                                    "Проверьте дату выдачи или подготовьте другой паспорт.", [profile_field, parameters.anchor])
                        elif value > anchor:
                            finding("passport_issue_after_anchor", "Паспорт ещё не выдан на указанную дату.",
                                    "Проверьте даты профиля и поездки.", [profile_field, parameters.anchor], "fix")
                except (ValueError, OverflowError):
                    unknown[rule.id] = technical_finding("date_out_of_range", "Календарный расчёт выходит за допустимый диапазон дат.")
            elif isinstance(parameters, StayLimitParameters):
                self._evaluate_stay(parameters, trip, answers, missing, finding)
            elif isinstance(parameters, ManualReviewParameters):
                finding("manual_verification", parameters.message, parameters.next_action, [], "verify")
            elif isinstance(parameters, FundsParameters) and parameters.minimum is not None:
                for requirement in applied:
                    if requirement.id == rule.requirement_id:
                        requirement.explanation += (
                            f" Условие источника: {parameters.minimum.amount} {parameters.minimum.currency} за {parameters.period}; "
                            "расчёт суммы и проверка доказательств выполняются отдельно."
                        )
        if scenario.entry_mode == "needs_verification":
            unknown["entry_mode"] = technical_finding("entry_mode_unknown", "Базовый режим въезда требует проверки.", sources=scenario.entry_source_ids)
        for index, gap in enumerate(scenario.unverified_conditions):
            unknown[f"gap:{index}"] = technical_finding("coverage_gap", gap)
        if scenario.coverage != "complete" and not scenario.unverified_conditions:
            unknown["coverage"] = technical_finding("coverage_gap", "Полнота требований не подтверждена.")
        if profile.existing_permissions:
            relevant = {scenario.destination_country} | ({"FR", "ES"} if scenario.destination_country in ("FR", "ES") else set())
            if any(permission.country in relevant for permission in profile.existing_permissions):
                unknown["permission"] = technical_finding(
                    "existing_permission", "Условия уже имеющегося разрешения и необходимость новой подачи нужно сопоставить отдельно.",
                    fields=["profile.existing_permissions"],
                )
        status = (
            "blocked" if any(item.severity == "blocking" for item in barriers) else
            "needs_input" if missing else
            "needs_verification" if barriers or any(item.severity != "info" for item in unknown.values())
            or any(req.mandatory and req.applicability == "unknown" for req in applied) else "applicable"
        )
        return ApplicabilityResult(
            status=status, requirements=applied, barriers=barriers,
            missing_answers=sorted(missing), unknown_conditions=list(unknown.values()),
        )

    @staticmethod
    def _evaluate_stay(parameters, trip, answers, missing, finding):
        if not trip.starts_on or not trip.ends_on:
            missing.update(path for path, value in (("trip.starts_on", trip.starts_on), ("trip.ends_on", trip.ends_on)) if value is None)
            return
        duration = (trip.ends_on - trip.starts_on).days + 1
        if duration > parameters.days:
            finding("stay_too_long", f"Длительность выбранной поездки превышает {parameters.days} дней этого сценария.",
                    "Сократите поездку или проверьте другой визовый сценарий.", ["trip.starts_on", "trip.ends_on"])
            return
        if not parameters.history_question:
            return
        path = "answers." + parameters.history_question
        history = answers.get(parameters.history_question)
        if history is None:
            missing.add(path)
            return
        if any(stay.ends_on >= trip.starts_on for stay in history):
            raise AppError(422, "invalid_answers", "Предыдущие пребывания должны заканчиваться до новой поездки.", {"fields": [path]})
        if parameters.history_mode == "manual":
            if history:
                finding("history_needs_verification", "Предыдущие поездки требуют проверки начала и правил 180-дневного периода.",
                        "Сопоставьте историю с соглашением и официальным порядком подсчёта.", [path], "verify")
            return
        first_day = date.fromordinal(max(1, trip.starts_on.toordinal() - parameters.window_days + 1))
        occupied = set()
        for stay in history:
            start, end = max(stay.starts_on, first_day), min(stay.ends_on, trip.starts_on - timedelta(days=1))
            occupied.update(start + timedelta(days=offset) for offset in range(max(0, (end - start).days + 1)))
        for offset in range(duration):
            day = trip.starts_on + timedelta(days=offset)
            occupied.add(day)
            cutoff = date.fromordinal(max(1, day.toordinal() - parameters.window_days + 1))
            if sum(occupied_day >= cutoff for occupied_day in occupied) > parameters.days:
                finding("stay_window_exceeded", "С учётом указанных прежних поездок превышен лимит в скользящем окне.",
                        "Уточните историю и даты; сравните с официальным калькулятором.", [path, "trip.starts_on", "trip.ends_on"])
                return

    def compare(
        self, profile: ApplicantProfile, trip: TripInput, answers: Answers,
        *, as_of: date | None = None,
    ) -> list[DestinationComparison]:
        today = as_of or datetime.now(timezone.utc).date()
        normalized = validate_answers(answers, self.questions)
        result = []
        for destination in self.registry.destinations(as_of=today):
            scenario = self.registry.get_scenario(destination.scenario_id, as_of=today)
            candidate_trip = trip.model_copy(update={"destination_country": destination.country})
            applicability = self._evaluate(profile, candidate_trip, normalized, scenario, as_of=today)
            required = [item for item in applicability.requirements if item.mandatory]
            known_count = sum(item.kind == "document" and item.applicability == "yes" for item in required)
            complete = scenario.coverage == "complete" and scenario.data_quality == "source_checked" and not applicability.unknown_conditions and all(item.applicability == "yes" for item in required) and applicability.status != "unsupported_profile"
            unknown = [item.message for item in applicability.unknown_conditions]
            unknown.extend(f"Нужен ответ: {field}" for field in applicability.missing_answers)
            result.append(DestinationComparison(
                destination=destination, applicability=applicability,
                required_documents=known_count if complete else None,
                known_required_documents=known_count, requirements_complete=complete,
                external_actions=[item.title for item in required if item.kind == "process_action" and item.applicability == "yes"],
                route_notes=["Маршрут, транзит и наличие рейсов не проверялись этим сравнением."],
                unknown_conditions=unknown, profile_revision=profile.revision,
            ))
        return result


def evaluate_applicability(
    profile: ApplicantProfile, trip: TripInput, answers: Answers, scenario: Scenario,
    *, registry: FileRuleRegistry | None = None, as_of: date | None = None,
) -> ApplicabilityResult:
    return EligibilityService(registry or FileRuleRegistry()).evaluate_applicability(
        profile, trip, answers, scenario, as_of=as_of,
    )
