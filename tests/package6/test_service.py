"""Real profiles/rules/archive/sessions; only absent package 4's case creation is seeded."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.checks.application import create_app
from app.checks.engine import DocumentChecker
from app.common.models import UserContext

NOW = datetime(2026, 10, 25, 20, tzinfo=timezone.utc)
SAMPLES = Path(__file__).resolve().parents[2] / "demo" / "documents"
PASSWORD = "SAMPLE-checks-password"


def login(client, username):
    body = {"username": username, "password": PASSWORD}
    headers = {"Origin": "http://testserver"}
    assert client.post("/api/v1/auth/register", headers=headers, json=body).status_code == 201
    result = client.post("/api/v1/auth/login", headers=headers, json=body)
    assert result.status_code == 200
    return result.json(), {"X-CSRF-Token": result.json()["csrf_token"]}


@pytest.fixture
def setup_checks(settings, snapshot_factory):
    app = create_app(settings, clock=lambda: NOW)
    with TestClient(app) as alice, TestClient(app) as bob:
        session, headers = login(alice, "sample_checks_alice")
        other, other_headers = login(bob, "sample_checks_bob")
        profile = snapshot_factory().profile.model_dump(mode="json", exclude={"id", "revision"})
        assert alice.put("/api/v1/profile", json=profile, headers=headers).status_code == 200
        yield app, alice, bob, session, other, headers, other_headers, snapshot_factory


def upload_passport(client, headers):
    response = client.post("/api/v1/documents", headers=headers,
                           files={"file": ("SAMPLE.pdf", (SAMPLES / "good_passport.pdf").read_bytes(), "application/pdf")},
                           data={"document_type": "passport"})
    assert response.status_code == 201
    identifier = response.json()["id"]
    document = client.post(f"/api/v1/documents/{identifier}/extract", headers=headers).json()
    result = client.patch(f"/api/v1/documents/{identifier}/fields", headers=headers, json={
        "expected_facts_revision": document["facts_revision"], "fields": [
            {"key": f["key"], "value": f["value"], "user_confirmed": True} for f in document["fields"]
        ],
    })
    assert result.status_code == 200
    return result.json()


def seed_case(app, owner_id, snapshot, document_ids=()):
    identifier = str(uuid4())
    with app.state.database.transaction() as connection:
        revision = connection.execute("SELECT revision FROM profiles WHERE owner_id = ?", (owner_id,)).fetchone()[0]
        connection.execute(
            "INSERT INTO cases (id,owner_id,scenario_id,profile_revision,ruleset_version,trip_json,answers_json,"
            "projection_json,stale,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'{\"FIXTURE_PACKAGE4\":true}',0,?,?)",
            (identifier, owner_id, snapshot.case.scenario_id, revision, snapshot.case.ruleset_version,
             snapshot.case.trip.model_dump_json(), json.dumps(snapshot.case.answers), NOW.isoformat(), NOW.isoformat()),
        )
        for document_id in document_ids:
            connection.execute("INSERT INTO case_documents VALUES (?,?,?,?)", (identifier, document_id, owner_id, NOW.isoformat()))
    return identifier


@pytest.mark.parametrize("country", ["FR", "ES", "JP", "IN", "EG", "ZA", "US", "CA", "BR", "AR", "AU", "NZ"])
def test_routes_use_actual_registry_for_each_country(setup_checks, country):
    app, alice, _, session, _, headers, _, factory = setup_checks
    identifier = seed_case(app, session["user"]["id"], factory(country))
    path = f"/api/v1/cases/{identifier}/checks"
    assert alice.get(path).status_code == 404
    response = alice.post(path, headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["case_id"] == identifier and result["stale"] is False
    assert any(finding["code"] == "coverage_gap" for finding in result["findings"])
    assert alice.get(path).json() == result
    assert response.headers["cache-control"] == "no-store"
    with app.state.database.connection() as connection:
        row = connection.execute("SELECT * FROM cases WHERE id = ?", (identifier,)).fetchone()
        assert row["projection_json"] is None and row["stale_reason"] == "checks_updated"
        assert row["revision"] == 1  # A derived result is not a mutation of inputs.


def test_ownership_csrf_logout_and_payload_cannot_supply_foreign_facts(setup_checks):
    app, alice, bob, a, b, ah, bh, factory = setup_checks
    case = seed_case(app, a["user"]["id"], factory())
    foreign = seed_case(app, b["user"]["id"], factory())
    path = f"/api/v1/cases/{case}/checks"
    missing = bob.get(f"/api/v1/cases/{uuid4()}/checks")
    assert bob.get(path).status_code == 404 and bob.get(path).json() == missing.json()
    assert bob.post(path, headers=bh).status_code == 404
    assert alice.post(path).status_code == 403
    assert alice.post(f"/api/v1/cases/{foreign}/checks", headers=ah).status_code == 404
    result = alice.post(path, headers=ah, json={"owner_id": b["user"]["id"], "documents": [], "ruleset": {"fake_pass": True}})
    assert result.status_code == 200 and result.json()["case_id"] == case
    assert any(f["code"] == "required_document_missing" for f in result.json()["findings"])
    alice.post("/api/v1/auth/logout", headers=ah)
    assert alice.get(path).status_code == 401 and alice.post(path, headers=ah).status_code == 401


def test_one_real_archive_document_is_checked_for_two_trips(setup_checks):
    app, alice, _, session, _, headers, _, factory = setup_checks
    document = upload_passport(alice, headers)
    document = alice.patch(f"/api/v1/documents/{document['id']}/fields", headers=headers, json={
        "expected_facts_revision": document["facts_revision"],
        "fields": [{"key": "expires_on", "value": "2027-03-10", "user_confirmed": True}],
    }).json()
    first = factory()
    second = factory()
    second.case.trip.ends_on = second.case.trip.ends_on.replace(day=11)
    cases = [seed_case(app, session["user"]["id"], snapshot, [document["id"]]) for snapshot in (first, second)]
    results = [alice.post(f"/api/v1/cases/{identifier}/checks", headers=headers).json() for identifier in cases]
    assert not any(f["code"] == "passport_validity" and f["document_ids"] for f in results[0]["findings"])
    assert any(f["code"] == "passport_validity" and f["document_ids"] for f in results[1]["findings"])
    assert results[0]["id"] != results[1]["id"]
    assert results[0]["input_signature"]["facts_revisions"] == {document["id"]: document["facts_revision"]}


def test_real_field_correction_replaces_negative_result_and_keeps_history(setup_checks):
    app, alice, _, session, _, headers, _, factory = setup_checks
    document = upload_passport(alice, headers)
    document_path = f"/api/v1/documents/{document['id']}"
    changed = alice.patch(document_path + "/fields", headers=headers, json={
        "expected_facts_revision": document["facts_revision"],
        "fields": [{"key": "expires_on", "value": "2025-01-01", "user_confirmed": True}],
    }).json()
    identifier = seed_case(app, session["user"]["id"], factory(), [document["id"]])
    path = f"/api/v1/cases/{identifier}/checks"
    negative = alice.post(path, headers=headers).json()
    assert any(f["code"] == "passport_expired" for f in negative["findings"])
    fixed = alice.patch(document_path + "/fields", headers=headers, json={
        "expected_facts_revision": changed["facts_revision"],
        "fields": [{"key": "expires_on", "value": "2032-04-15", "user_confirmed": True}],
    })
    assert fixed.status_code == 200
    assert alice.get(path).json()["stale"] is True
    current = alice.post(path, headers=headers).json()
    assert current["id"] != negative["id"] and current["stale"] is False
    assert not any(f["code"] in ("passport_expired", "passport_validity") and f["document_ids"] for f in current["findings"])
    assert alice.get(path).json()["id"] == current["id"]
    with app.state.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM check_results WHERE case_id = ?", (identifier,)).fetchone()[0] == 2


@pytest.mark.parametrize("mutation", ["document_patch", "document_delete", "profile", "trip", "algorithm", "tomorrow", "active_ruleset"])
def test_stale_results_for_every_dependency(setup_checks, mutation):
    app, alice, _, session, _, headers, _, factory = setup_checks
    document = upload_passport(alice, headers)
    identifier = seed_case(app, session["user"]["id"], factory(), [document["id"]])
    path = f"/api/v1/cases/{identifier}/checks"
    assert alice.post(path, headers=headers).status_code == 200
    if mutation == "document_patch":
        alice.patch(f"/api/v1/documents/{document['id']}/fields", headers=headers, json={
            "expected_facts_revision": document["facts_revision"],
            "fields": [{"key": "expires_on", "value": "2025-01-01", "user_confirmed": True}],
        })
    elif mutation == "document_delete":
        assert alice.delete(f"/api/v1/documents/{document['id']}", headers=headers).status_code == 204
    elif mutation == "profile":
        profile = alice.get("/api/v1/profile").json()
        profile.pop("id")
        profile.pop("revision")
        profile["given_name"] = "OTHER"
        assert alice.put("/api/v1/profile", headers=headers, json=profile).status_code == 200
    elif mutation == "trip":
        with app.state.database.transaction() as connection:
            connection.execute("UPDATE cases SET revision = revision + 1 WHERE id = ?", (identifier,))
    elif mutation == "algorithm":
        with app.state.database.transaction() as connection:
            row = connection.execute("SELECT input_signature_json FROM check_results WHERE case_id = ?", (identifier,)).fetchone()
            signature = json.loads(row[0])
            signature["algorithm_version"] = "previous-algorithm"
            connection.execute("UPDATE check_results SET input_signature_json = ? WHERE case_id = ?", (json.dumps(signature), identifier))
    elif mutation == "tomorrow":
        app.state.checks_service.clock = lambda: NOW + timedelta(days=1)
    else:
        app.state.checks_service.registry.active_version = "next-version"
    result = alice.get(path)
    assert result.status_code == 200 and result.json()["stale"] is True


def test_profile_or_document_change_during_compute_cannot_save_old_result(setup_checks):
    app, alice, _, session, _, headers, _, factory = setup_checks
    identifier = seed_case(app, session["user"]["id"], factory())
    class RaceChecker:
        def check_case(self, snapshot):
            result = DocumentChecker(lambda: NOW).check_case(snapshot)
            with app.state.database.transaction() as connection:
                connection.execute("UPDATE profiles SET revision = revision + 1 WHERE owner_id = ?", (session["user"]["id"],))
            return result
    app.state.checks_service.checker = RaceChecker()
    result = alice.post(f"/api/v1/cases/{identifier}/checks", headers=headers)
    assert result.status_code == 409 and result.json()["error"]["code"] == "check_inputs_changed"
    with app.state.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM check_results").fetchone()[0] == 0


def test_case_rebuilder_receives_owner_and_reason(setup_checks):
    app, alice, _, session, _, headers, _, factory = setup_checks
    identifier = seed_case(app, session["user"]["id"], factory())
    calls = []
    class RebuilderFixture:
        def rebuild_case(self, owner, case_id, reason):
            calls.append((str(owner.user_id), str(case_id), reason))
    app.state.checks_service.rebuilder = RebuilderFixture()
    assert alice.post(f"/api/v1/cases/{identifier}/checks", headers=headers).status_code == 200
    assert calls == [(session["user"]["id"], identifier, "checks_updated")]


def test_result_persists_across_application_restart(setup_checks, settings):
    app, alice, _, session, _, headers, _, factory = setup_checks
    identifier = seed_case(app, session["user"]["id"], factory())
    path = f"/api/v1/cases/{identifier}/checks"
    result = alice.post(path, headers=headers).json()
    with TestClient(create_app(settings, clock=lambda: NOW)) as restarted:
        restarted.cookies.update(alice.cookies)
        assert restarted.get(path).json() == result


def test_unknown_profile_does_not_claim_successful_checks(setup_checks):
    app, _, bob, _, session, _, headers, factory = setup_checks
    identifier = seed_case(app, session["user"]["id"], factory())
    result = bob.post(f"/api/v1/cases/{identifier}/checks", headers=headers)
    assert result.status_code == 200
    assert {f["code"] for f in result.json()["findings"]} >= {"check_inputs_missing", "profile_scope_unknown"}
    assert not any(f["origin"] == "rule" for f in result.json()["findings"])


def test_missing_registry_is_unavailable_not_empty_pass(setup_checks):
    app, alice, _, session, _, headers, _, factory = setup_checks
    identifier = seed_case(app, session["user"]["id"], factory())
    app.state.checks_service.registry = None
    result = alice.post(f"/api/v1/cases/{identifier}/checks", headers=headers)
    assert result.status_code == 503 and result.json()["error"]["code"] == "rules_unavailable"


def test_openapi_uses_existing_check_result_and_security(setup_checks):
    _, alice, _, _, _, _, _, _ = setup_checks
    schema = alice.get("/openapi.json").json()
    path = schema["paths"]["/api/v1/cases/{case_id}/checks"]
    for method, operation in path.items():
        assert operation["x-implementation"] == "implemented"
        assert operation["security"] == [{"SessionCookie": []}]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/CheckResult"}
    assert any(p["name"] == "X-CSRF-Token" for p in path["post"]["parameters"])
    assert "requestBody" not in path["post"]
