from datetime import date, timedelta
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from app.common.dates import add_calendar_months
from app.common.errors import AppError
from app.common.models import ApplicantProfile, Condition, ProfileInput, TripInput
from app.eligibility.answers import validate_answers
from app.eligibility.conditions import evaluate_condition
from app.eligibility.service import EligibilityService
from app.rules.registry import FileRuleRegistry
from app.rules.schema import validate_condition

CHECKED_ON = date(2026, 10, 25)


def profile(**changes):
    values = dict(
        id=uuid4(), revision=2, given_name="SAMPLE", surname="TOURIST",
        birth_date="1995-01-01", citizenship="RU", residence_country="RU",
        has_other_citizenship=False, has_foreign_residence_permit=False,
        passport_kind="biometric", passport_issued_on="2023-01-01",
        passport_expires_on="2033-01-01", employment_status="employed",
        existing_permissions=[],
    )
    return ApplicantProfile(**(values | changes))


def trip(**changes):
    return TripInput(**(dict(
        purpose="tourism", starts_on="2027-01-20", ends_on="2027-01-31",
        application_country="RU", accommodation_type="hotel", funding_type="self",
    ) | changes))


@pytest.fixture
def evaluator():
    return EligibilityService(FileRuleRegistry())


def evaluate(evaluator, country="FR", *, person=None, journey=None, answers=None, scenario=None):
    return evaluator.evaluate_applicability(
        person or profile(), journey or trip(), answers or {},
        scenario or evaluator.registry.get_scenario(country + "_TOURISM", as_of=CHECKED_ON),
        as_of=CHECKED_ON,
    )


def codes(result):
    return {item.code for item in [*result.barriers, *result.unknown_conditions]}


def requirements(result):
    return {item.id: item for item in result.requirements}


@pytest.mark.parametrize("changes", [
    {"citizenship": "BY"}, {"residence_country": "KZ"},
    {"has_other_citizenship": True}, {"has_foreign_residence_permit": True},
    {"birth_date": "2010-01-01"},
])
def test_unsupported_profiles_do_not_get_a_checklist(evaluator, changes):
    result = evaluate(evaluator, person=profile(**changes))
    assert result.status == "unsupported_profile" and result.requirements == []
    assert result.barriers[0].field_paths


def test_scope_dates_and_unknown_flags_are_not_assumed(evaluator):
    unknown = evaluate(evaluator, person=profile(has_other_citizenship=None))
    assert unknown.status == "needs_input" and unknown.requirements == []
    assert "profile.has_other_citizenship" in unknown.missing_answers
    assert evaluate(evaluator, journey=trip(purpose="work")).status == "unsupported_profile"
    birthday = evaluate(evaluator, person=profile(birth_date="2009-01-20"))
    assert birthday.status != "unsupported_profile"
    assert evaluate(evaluator, journey=trip(starts_on=None)).status == "needs_input"
    with pytest.raises(AppError, match="совпадает"):
        evaluate(evaluator, journey=trip(destination_country="JP"))


def test_passport_type_and_unknown_type_change_formal_barriers(evaluator):
    answers = {"schengen_prior_stays": []}
    good = evaluate(evaluator, answers=answers)
    bad = evaluate(evaluator, person=profile(passport_kind="non_biometric"), answers=answers)
    unknown = evaluate(evaluator, person=profile(passport_kind=None), answers=answers)
    assert good.status == "needs_verification"  # Coverage stays partial.
    assert bad.status == "blocked"
    barrier = next(item for item in bad.barriers if item.rule_ref.id == "FR.passport.biometric")
    assert barrier.origin == "rule" and barrier.source_refs == ["FR_RU"]
    assert "profile.passport_kind" in unknown.missing_answers
    assert "condition_not_met" not in codes(unknown)


def test_calendar_months_and_passport_exact_boundaries(evaluator):
    assert add_calendar_months(date(2027, 1, 31), 1) == date(2027, 2, 28)
    assert add_calendar_months(date(2028, 2, 29), 12) == date(2029, 2, 28)
    assert add_calendar_months(date(2027, 1, 31), 3) == date(2027, 4, 30)
    answers = {"schengen_prior_stays": []}
    assert "passport_validity" not in codes(evaluate(evaluator, person=profile(passport_expires_on="2027-04-30"), answers=answers))
    assert "passport_validity" in codes(evaluate(evaluator, person=profile(passport_expires_on="2027-04-29"), answers=answers))
    assert "passport_age" in codes(evaluate(evaluator, person=profile(passport_issued_on="2017-01-20"), answers=answers))
    assert "passport_age" not in codes(evaluate(evaluator, person=profile(passport_issued_on="2017-01-21"), answers=answers))


def test_conditional_requirements_and_answers(evaluator):
    self_paid = evaluate(evaluator, "ES", answers={"application_date": "2026-12-01", "blank_passport_pages": 2})
    assert requirements(self_paid)["ES.bank"].applicability == "yes"
    assert "ES.employment" in requirements(self_paid)
    sponsored = evaluate(evaluator, "ES", person=profile(employment_status="unemployed"), journey=trip(funding_type="sponsor"))
    assert "ES.bank" not in requirements(sponsored)
    assert "ES.employment" not in requirements(sponsored)
    unknown = evaluate(evaluator, "JP", answers={"jp_bank_original_available": True})
    assert requirements(unknown)["JP.multiple_letter"].applicability == "unknown"
    assert "answers.jp_multiple_entry" in unknown.missing_answers
    single = evaluate(evaluator, "JP", answers={"jp_multiple_entry": False, "jp_bank_original_available": True})
    assert "JP.multiple_letter" not in requirements(single)
    fix = evaluate(evaluator, "JP", answers={"jp_multiple_entry": True, "jp_bank_original_available": False})
    assert requirements(fix)["JP.multiple_letter"].applicability == "yes"
    assert any(item.severity == "fix" for item in fix.barriers)
    assert fix.status != "blocked"


def test_nested_funding_condition_asks_only_relevant_answer(evaluator):
    self_paid = evaluate(evaluator, "NZ")
    assert "answers.nz_accommodation_prepaid" in self_paid.missing_answers
    sponsored = evaluate(evaluator, "NZ", journey=trip(funding_type="sponsor"))
    assert "answers.nz_accommodation_prepaid" not in sponsored.missing_answers
    reduced = evaluate(evaluator, "NZ", answers={"nz_accommodation_prepaid": True})
    normal = evaluate(evaluator, "NZ", answers={"nz_accommodation_prepaid": False})
    assert any("400 NZD" in item.explanation for item in reduced.requirements)
    assert any("1000 NZD" in item.explanation for item in normal.requirements)
    assert all(item.code != "insufficient_funds" for item in normal.barriers)


def test_unverified_conflicting_and_expired_sources_cannot_prove_barrier(evaluator):
    scenario = evaluator.registry.get_scenario("FR_TOURISM", as_of=CHECKED_ON)
    for status in ("needs_verification", "conflicting"):
        changed = scenario.model_copy(deep=True)
        next(source for source in changed.sources if source.id == "FR_RU").status = status
        result = evaluate(evaluator, person=profile(passport_kind="non_biometric"), scenario=changed)
        assert not any(item.rule_ref and item.rule_ref.id == "FR.passport.biometric" for item in result.barriers)
        assert "source_needs_verification" in codes(result)
        assert requirements(result)["FR.appointment"].applicability == "unknown"
    expired = evaluator.evaluate_applicability(profile(passport_kind="non_biometric"), trip(), {}, scenario, as_of=date(2026, 11, 2))
    assert expired.status == "needs_verification" and expired.barriers == []
    assert all(item.applicability == "unknown" for item in expired.requirements)


def test_all_destinations_return_factors_without_a_score(evaluator):
    result = evaluator.compare(profile(), trip(), {}, as_of=CHECKED_ON)
    assert len(result) == 12
    assert all(item.profile_revision == 2 for item in result)
    assert all(item.required_documents is None and not item.requirements_complete for item in result)
    assert any(item.known_required_documents > 0 for item in result)
    assert all(item.route_notes and item.unknown_conditions for item in result)
    assert all(item.applicability.status != "applicable" for item in result)
    assert all(not {"approval_percent", "score", "probability"} & item.model_dump().keys() for item in result)
    assert all(item.applicability.status == "unsupported_profile" for item in evaluator.compare(profile(citizenship="BY"), trip(), {}, as_of=CHECKED_ON))


def test_rolling_days_deduplicate_overlap_and_move_window(evaluator):
    start = date(2027, 1, 20)
    def interval(first, last):
        return {"starts_on": first.isoformat(), "ends_on": last.isoformat()}
    history = [interval(start - timedelta(days=100), start - timedelta(days=12))]  # 89 days.
    two_days = trip(ends_on="2027-01-21")
    assert "stay_window_exceeded" in codes(evaluate(evaluator, journey=two_days, answers={"schengen_prior_stays": history}))
    duplicate_history = [interval(start - timedelta(days=15), start - timedelta(days=1))] * 2
    assert "stay_window_exceeded" not in codes(evaluate(evaluator, answers={"schengen_prior_stays": duplicate_history}))
    old = [interval(start - timedelta(days=180), start - timedelta(days=91))]
    ninety_days = trip(ends_on=(start + timedelta(days=89)).isoformat())
    assert "stay_window_exceeded" not in codes(evaluate(evaluator, journey=ninety_days, answers={"schengen_prior_stays": old}))
    assert "stay_too_long" in codes(evaluate(evaluator, journey=trip(ends_on=(start + timedelta(days=90)).isoformat())))
    assert "answers.schengen_prior_stays" in evaluate(evaluator).missing_answers
    assert "answers.schengen_prior_stays" not in evaluate(evaluator, answers={"schengen_prior_stays": []}).missing_answers


@pytest.mark.parametrize("country,key", [("BR", "br_prior_stays"), ("AR", "ar_prior_stays")])
def test_treaty_periods_are_not_silently_turned_into_rolling_schengen(evaluator, country, key):
    result = evaluate(evaluator, country, answers={key: [{"starts_on": "2026-10-01", "ends_on": "2026-10-10"}]})
    assert "history_needs_verification" in codes(result)
    assert "stay_window_exceeded" not in codes(result)


@pytest.mark.parametrize("answers", [
    {"blank_passport_pages": False}, {"blank_passport_pages": "2"}, {"blank_passport_pages": -1},
    {"jp_multiple_entry": "false"}, {"application_date": "2026-02-30"}, {"application_date": "20261025"},
    {"secret_key": "SAMPLE-NEVER-ECHO"}, {"schengen_prior_stays": [{"starts_on": "2026-02-01", "ends_on": "2026-01-01"}]},
    {"schengen_prior_stays": [{"starts_on": "2027-01-20", "ends_on": "2027-01-21"}]},
])
def test_invalid_answers_fail_explicitly_without_echo(evaluator, answers):
    with pytest.raises(AppError) as invalid:
        evaluate(evaluator, answers=answers)
    assert invalid.value.status_code == 422
    assert "SAMPLE-NEVER-ECHO" not in str(invalid.value.error)


def test_three_valued_logic_and_constant_type_validation(evaluator):
    parse = TypeAdapter(Condition).validate_python
    context = {"profile": ProfileInput().model_dump(), "trip": {}, "answers": {"flag": False, "n": 0}}
    false = {"field": "answers.flag", "op": "eq", "value": True}
    absent = {"field": "trip.purpose", "op": "eq", "value": "tourism"}
    assert evaluate_condition(parse(absent), context).value == "unknown"
    assert evaluate_condition(parse({"all": [false, absent]}), context).value == "no"
    known_true = {"field": "answers.n", "op": "eq", "value": 0}
    assert evaluate_condition(parse({"any": [known_true, absent]}), context).value == "yes"
    assert evaluate_condition(parse({"field": "answers.flag", "op": "exists", "value": True}), context).value == "yes"
    assert evaluate_condition(parse({"field": "answers.flag", "op": "eq", "value": 0}), context).value == "no"
    for malformed in (
        {"field": "answers.blank_passport_pages", "op": "gte", "value": True},
        {"field": "profile.has_other_citizenship", "op": "gte", "value": False},
        {"field": "trip.starts_on", "op": "eq", "value": "2027-02-30"},
    ):
        with pytest.raises(ValueError):
            validate_condition(parse(malformed), evaluator.questions)
    assert validate_answers({"jp_multiple_entry": None}, evaluator.questions) == {"jp_multiple_entry": None}
