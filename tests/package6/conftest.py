"""Typed snapshots with actual package 2 rules; all personal data are synthetic."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.checks.engine import signature
from app.common.models import (
    ApplicantProfile, Case, CaseSnapshot, ConfirmedDocumentFacts, Field,
    InputSignature, Process, Readiness, TripInput, UserContext,
)
from app.rules.registry import FileRuleRegistry

NOW = datetime(2026, 10, 25, 20, tzinfo=timezone.utc)
VALUES = {
    "passport": {"given_name": "ALEXEI", "surname": "SAMPLEOV", "birth_date": "1996-03-14",
                 "passport_kind": "biometric", "issued_on": "2022-04-15", "expires_on": "2032-04-15"},
    "bank_statement": {"holder_name": "ALEXEI SAMPLEOV", "statement_on": "2026-10-20", "balance": "3000.00", "currency": "EUR"},
    "insurance": {"holder_name": "ALEXEI SAMPLEOV", "starts_on": "2026-12-01", "ends_on": "2027-01-31",
                  "territory": "Schengen", "coverage_amount": "50000.00", "currency": "EUR"},
}


@pytest.fixture(scope="session")
def registry():
    return FileRuleRegistry()


@pytest.fixture
def snapshot_factory(registry):
    def make(country="FR", *, kinds=("passport", "bank_statement", "insurance")):
        scenario = registry.get_scenario(country + "_TOURISM", as_of=NOW.date())
        profile = ApplicantProfile(
            id=uuid4(), revision=1, given_name="ALEXEI", surname="SAMPLEOV", birth_date="1996-03-14",
            citizenship="RU", residence_country="RU", has_other_citizenship=False, has_foreign_residence_permit=False,
            passport_kind="biometric", passport_issued_on="2022-04-15", passport_expires_on="2032-04-15", employment_status="employed",
        )
        documents = [ConfirmedDocumentFacts(document_id=uuid4(), document_type=kind, facts_revision=1, fields=[
            Field(key=key, value=value, source_kind="manual", user_confirmed=True, confirmed_revision=1)
            for key, value in VALUES[kind].items()
        ]) for kind in kinds]
        answers = {}
        for question in scenario.questions:
            answers[question.key] = {
                "boolean": False, "date": "2026-10-25", "stays": [], "integer": 4,
                "enum": question.choices[0] if question.choices else None,
            }[question.value_type]
        case = Case(
            id=uuid4(), scenario_id=scenario.id, trip=TripInput(
                purpose="tourism", destination_country=country, starts_on="2026-12-01", ends_on="2026-12-10",
                application_country="RU", accommodation_type="hotel", funding_type="self",
            ), answers=answers, revision=1, profile_revision=1, ruleset_version=scenario.version,
            attached_document_ids=[doc.document_id for doc in documents], checklist=[], readiness=Readiness(),
            process=Process(), stale=True, created_at=NOW, updated_at=NOW,
        )
        snapshot = CaseSnapshot(
            owner=UserContext(user_id=uuid4()), case=case, profile=profile, scenario=scenario,
            ruleset=registry.get_ruleset(scenario.version, as_of=NOW.date()), documents=documents,
            input_signature=InputSignature(case_revision=1, profile_revision=1, facts_revisions={},
                                           ruleset_version=scenario.version, algorithm_version="fixture"),
        )
        snapshot.input_signature = signature(snapshot)
        return snapshot
    return make
