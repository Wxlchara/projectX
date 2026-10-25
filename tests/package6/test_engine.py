from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.checks import ALGORITHM_VERSION, check_case
from app.checks.engine import signature
from app.common.models import Rule, Source

NOW = datetime(2026, 10, 25, 20, tzinfo=timezone.utc)


def value(snapshot, kind, key, value):
    document = next(doc for doc in snapshot.documents if doc.document_type == kind)
    field = next(field for field in document.fields if field.key == key)
    field.value = value
    return field


def run(snapshot):
    return check_case(snapshot, evaluated_at=NOW)


def codes(result):
    return {finding.code for finding in result.findings}


def synthetic_funds(snapshot, amount="100.10", currency="EUR", period="trip"):
    # Artificial threshold for numeric tests only; never inserted in destination data.
    source = Source(id="SYNTHETIC_TEST_ONLY", url="https://example.invalid/NOT-A-VISA-SOURCE",
                    authority="SYNTHETIC UNIT TEST, NOT OFFICIAL", checked_at=NOW, review_after="2026-11-01",
                    status="source_checked", evidence="Artificial test threshold, no legal meaning.")
    snapshot.scenario.sources.append(source)
    snapshot.ruleset.sources.append(source)
    snapshot.ruleset.rules.append(Rule(
        id="SYNTHETIC_TEST_ONLY.minimum", version=snapshot.ruleset.version, scenario_id=snapshot.scenario.id,
        kind="minimum_funds", requirement_id="FR.bank", mandatory=True, source_ids=[source.id],
        parameters={"minimum": {"amount": amount, "currency": currency}, "period": period},
    ))


@pytest.mark.parametrize("country", ["FR", "ES", "JP", "IN", "EG", "ZA", "US", "CA", "BR", "AR", "AU", "NZ"])
def test_all_real_scenarios_retain_partial_coverage_and_inputs_unchanged(snapshot_factory, country):
    snapshot = snapshot_factory(country)
    before = snapshot.model_dump_json()
    result = run(snapshot)
    assert not result.stale
    assert "coverage_gap" in codes(result)
    assert result.case_id == snapshot.case.id and result.input_signature.algorithm_version == ALGORITHM_VERSION
    assert result.input_signature.facts_revisions == {doc.document_id: 1 for doc in snapshot.documents}
    assert before == snapshot.model_dump_json()
    assert all(finding.next_action for finding in result.findings)


def test_expired_passport_rule_reference_and_actual_correction(snapshot_factory):
    snapshot = snapshot_factory()
    value(snapshot, "passport", "expires_on", "2025-04-15")
    result = run(snapshot)
    assert {"passport_expired", "passport_validity"} <= codes(result)
    finding = next(f for f in result.findings if f.code == "passport_validity" and f.document_ids)
    assert finding.origin == "rule" and finding.rule_ref.id == "FR.passport.validity" and finding.source_refs
    value(snapshot, "passport", "expires_on", "2032-04-15")
    assert not {"passport_expired", "passport_validity"} & codes(run(snapshot))


def test_calendar_months_boundary_and_tenth_anniversary(snapshot_factory):
    snapshot = snapshot_factory()
    snapshot.case.trip.ends_on = snapshot.case.trip.ends_on.replace(month=11, day=30)
    snapshot.case.trip.starts_on = snapshot.case.trip.starts_on.replace(month=11, day=20)
    value(snapshot, "passport", "expires_on", "2027-02-28")
    assert not any(f.code == "passport_validity" and f.document_ids for f in run(snapshot).findings)
    value(snapshot, "passport", "expires_on", "2027-02-27")
    assert any(f.code == "passport_validity" and f.document_ids for f in run(snapshot).findings)
    value(snapshot, "passport", "issued_on", "2016-11-20")
    assert any(f.code == "passport_age" and f.document_ids for f in run(snapshot).findings)
    value(snapshot, "passport", "issued_on", "2016-11-21")
    assert not any(f.code == "passport_age" and f.document_ids for f in run(snapshot).findings)


def test_application_date_anchor_is_used(snapshot_factory):
    snapshot = snapshot_factory("IN", kinds=("passport",))
    snapshot.case.answers["application_date"] = "2026-10-31"
    value(snapshot, "passport", "expires_on", "2027-04-30")
    assert not any(f.code == "passport_validity" and f.document_ids for f in run(snapshot).findings)
    value(snapshot, "passport", "expires_on", "2027-04-29")
    assert any(f.code == "passport_validity" and f.document_ids for f in run(snapshot).findings)


@pytest.mark.parametrize("key,bad,expected", [
    ("starts_on", "2026-12-02", "insurance_dates_not_covered"),
    ("ends_on", "2026-12-09", "insurance_dates_not_covered"),
    ("territory", "Brazil", "insurance_territory_mismatch"),
    ("territory", "Worldwide except unspecified exclusions", "insurance_territory_unknown"),
    ("coverage_amount", "29999.99", "insurance_cover_insufficient"),
    ("currency", "USD", "money_currency_mismatch"),
    ("currency", "XYZ", "money_currency_unknown"),
])
def test_insurance_negative_cases(snapshot_factory, key, bad, expected):
    snapshot = snapshot_factory()
    value(snapshot, "insurance", key, bad)
    result = run(snapshot)
    assert expected in codes(result)
    finding = next(f for f in result.findings if f.code == expected)
    assert finding.rule_ref.id == "FR.insurance.coverage" and finding.document_ids
    if key == "currency":
        assert "insurance_cover_insufficient" not in codes(result)


def test_owner_mismatch_ambiguous_transliteration_and_normalization(snapshot_factory):
    snapshot = snapshot_factory()
    value(snapshot, "bank_statement", "holder_name", "OTHER PERSON")
    result = run(snapshot)
    assert {"holder_mismatch", "document_names_mismatch"} <= codes(result)
    assert all(f.severity == "verify" and f.origin == "heuristic" for f in result.findings if f.code in {"holder_mismatch", "document_names_mismatch"})
    value(snapshot, "bank_statement", "holder_name", "aleksei sampleov")
    assert "holder_mismatch" in codes(run(snapshot))
    value(snapshot, "bank_statement", "holder_name", "  alexei   sampleov ")
    assert "holder_mismatch" not in codes(run(snapshot))


@pytest.mark.parametrize("mutation", ["missing", "unconfirmed", "old_confirmation", "duplicate", "null", "invalid"])
def test_missing_or_unconfirmed_fields_never_prove_validity(snapshot_factory, mutation):
    snapshot = snapshot_factory()
    doc = snapshot.documents[0]
    field = next(f for f in doc.fields if f.key == "expires_on")
    if mutation == "missing":
        doc.fields.remove(field)
    elif mutation == "unconfirmed":
        field.user_confirmed = False
    elif mutation == "old_confirmation":
        doc.facts_revision = 2
        snapshot.input_signature = signature(snapshot)
    elif mutation == "duplicate":
        doc.fields.append(field.model_copy())
    elif mutation == "null":
        field.value = None
    else:
        field.value = "2032-02-31"
    result = run(snapshot)
    assert any(f.code == "document_field_unverified" and "fields.expires_on" in f.field_paths for f in result.findings)
    assert not any(f.code == "passport_validity" and f.document_ids for f in result.findings)


@pytest.mark.parametrize("balance,expected", [("100.09", True), ("100.10", False), ("100.100000000000000000000000000001", False)])
def test_exact_decimal_comparison_with_synthetic_threshold(snapshot_factory, balance, expected):
    snapshot = snapshot_factory()
    synthetic_funds(snapshot)
    value(snapshot, "bank_statement", "balance", balance)
    assert ("funds_insufficient" in codes(run(snapshot))) is expected


def test_monthly_funds_remain_manual_and_no_universal_threshold(snapshot_factory):
    snapshot = snapshot_factory("NZ")
    result = run(snapshot)
    assert "funds_manual_calculation" in codes(result)
    assert "funds_insufficient" not in codes(result)
    snapshot = snapshot_factory("JP", kinds=("passport",))
    missing = [f for f in run(snapshot).findings if f.code == "required_document_missing"]
    assert any("JP.bank" in f.field_paths[0] for f in missing)
    assert not any("insurance" in str(f.field_paths) for f in missing)


def test_false_and_unknown_rule_conditions_differ(snapshot_factory):
    snapshot = snapshot_factory("NZ")
    snapshot.case.answers["nz_accommodation_prepaid"] = False
    false_result = run(snapshot)
    snapshot.case.answers["nz_accommodation_prepaid"] = None
    unknown_result = run(snapshot)
    assert "funds_manual_calculation" in codes(false_result)
    assert "document_rule_inputs_missing" in codes(unknown_result) or "check_inputs_missing" in codes(unknown_result)
    assert not any(f.code == "funds_insufficient" for f in unknown_result.findings)


def test_unverified_source_cannot_create_formal_blocker(snapshot_factory):
    snapshot = snapshot_factory()
    value(snapshot, "passport", "expires_on", "2026-12-11")
    for source in snapshot.scenario.sources:
        if source.id == "FR_ARRIVAL":
            source.status = "needs_verification"
    result = run(snapshot)
    assert "source_needs_verification" in codes(result)
    assert not any(f.code == "passport_validity" and f.document_ids for f in result.findings)


def test_snapshot_revision_mismatch_and_unsupported_profile(snapshot_factory):
    snapshot = snapshot_factory()
    snapshot.case.revision += 1
    assert run(snapshot).stale and "snapshot_mismatch" in codes(run(snapshot))
    snapshot.input_signature = signature(snapshot)
    snapshot.profile.has_other_citizenship = True
    result = run(snapshot)
    assert "unsupported_profile" in codes(result)
    assert not any(f.origin == "rule" for f in result.findings)


def test_same_archive_document_different_trip_dates(snapshot_factory):
    first = snapshot_factory()
    value(first, "passport", "expires_on", "2027-03-10")
    second = first.model_copy(deep=True)
    second.case.id = uuid4()
    second.case.trip.ends_on = second.case.trip.ends_on.replace(day=11)
    assert not any(f.code == "passport_validity" and f.document_ids for f in run(first).findings)
    assert any(f.code == "passport_validity" and f.document_ids for f in run(second).findings)


def test_unknown_rule_and_expired_sources_never_return_clean_result(snapshot_factory):
    snapshot = snapshot_factory()
    snapshot.ruleset.rules[0].kind = "unimplemented_TEST_ONLY"
    result = run(snapshot)
    assert result.stale and "snapshot_rules_invalid" in codes(result)
    snapshot = snapshot_factory()
    result = check_case(snapshot, evaluated_at=datetime(2026, 11, 2, tzinfo=timezone.utc))
    assert "source_needs_verification" in codes(result)
    assert not any(f.rule_ref and f.document_ids and f.severity == "blocking" for f in result.findings)


def test_missing_optional_documents_and_zero_amount(snapshot_factory):
    snapshot = snapshot_factory("BR", kinds=("passport",))
    assert not any(f.code == "required_document_missing" and "bank" in str(f.field_paths) for f in run(snapshot).findings)
    snapshot = snapshot_factory()
    value(snapshot, "insurance", "coverage_amount", "0.00")
    assert "insurance_cover_insufficient" in codes(run(snapshot))
