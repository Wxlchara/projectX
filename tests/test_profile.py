import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.common.models import ProfileInput, UserContext
from app.main import create_app
from test_database import NOW, seed_case, seed_document
from test_eligibility import profile, trip


@pytest.fixture(autouse=True)
def catalog_snapshot_time(monkeypatch):
    # HTTP decisions use the reviewed catalog snapshot, independent of test day.
    class SnapshotClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 25, 13, 4, tzinfo=timezone.utc)
    monkeypatch.setattr("app.eligibility.service.datetime", SnapshotClock)
    monkeypatch.setattr("app.rules.registry.datetime", SnapshotClock)


def login(client, username="sample_profile"):
    credentials = {"username": username, "password": "SAMPLE-Password-123"}
    headers = {"Origin": "http://testserver"}
    assert client.post("/api/v1/auth/register", json=credentials, headers=headers).status_code == 201
    response = client.post("/api/v1/auth/login", json=credentials, headers=headers)
    assert response.status_code == 200
    return response.json(), {"X-CSRF-Token": response.json()["csrf_token"], **headers}


def input_profile(**changes):
    return profile(**changes).model_dump(mode="json", exclude={"id", "revision"})


def test_empty_profile_ownership_revision_idempotence_and_full_replace(client):
    session, headers = login(client)
    response = client.get("/api/v1/profile")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    initial = response.json()
    assert initial["revision"] == 1 and initial["has_other_citizenship"] is None
    changed = client.put("/api/v1/profile", json=input_profile(), headers=headers).json()
    assert changed["id"] == initial["id"] and changed["revision"] == 2
    assert "owner_id" not in changed
    assert client.put("/api/v1/profile", json=input_profile(), headers=headers).json() == changed
    cleared = client.put("/api/v1/profile", json={"citizenship": "RU"}, headers=headers).json()
    assert cleared["revision"] == 3 and cleared["surname"] is None
    assert cleared["has_other_citizenship"] is None
    with client.app.state.database.connection() as connection:
        assert connection.execute("SELECT owner_id FROM profiles WHERE id = ?", (initial["id"],)).fetchone()[0] == session["user"]["id"]


def test_auth_csrf_and_server_owned_input(client):
    assert client.get("/api/v1/profile").status_code == 401
    assert client.put("/api/v1/profile", json={}).status_code == 401
    assert client.post("/api/v1/comparisons", json={"trip": {}}).status_code == 401
    _, headers = login(client)
    assert client.put("/api/v1/profile", json={}).status_code == 403
    assert client.post("/api/v1/comparisons", json={"trip": {}}).status_code == 403
    for extra in ({"owner_id": str(uuid4())}, {"id": str(uuid4())}, {"revision": 900}, {"has_other_citizenship": "false"}):
        assert client.put("/api/v1/profile", json=extra, headers=headers).status_code == 422
    assert client.post("/api/v1/comparisons", json={"trip": {}, "owner_id": str(uuid4())}, headers=headers).status_code == 422


@pytest.mark.parametrize("changes", [
    {"birth_date": "2999-01-01"},
    {"passport_issued_on": "2027-02-01", "passport_expires_on": "2027-01-01"},
])
def test_invalid_profile_dates_do_not_write(client, changes):
    _, headers = login(client)
    response = client.put("/api/v1/profile", json=input_profile(**changes), headers=headers)
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_profile"
    assert client.get("/api/v1/profile").json()["revision"] == 1


def test_comparisons_use_live_profile_and_revisions(client):
    _, headers = login(client)
    body = {"trip": trip().model_dump(mode="json"), "answers": {"schengen_prior_stays": []}}
    initial = client.post("/api/v1/comparisons", json=body, headers=headers)
    assert initial.status_code == 200
    assert all(item["applicability"]["status"] == "needs_input" for item in initial.json()["items"])
    client.put("/api/v1/profile", json=input_profile(), headers=headers)
    good = client.post("/api/v1/comparisons", json=body, headers=headers)
    assert good.status_code == 200 and good.headers["cache-control"] == "no-store"
    items = good.json()["items"]
    assert len(items) == 12 and all(item["profile_revision"] == 2 for item in items)
    fr = next(item for item in items if item["destination"]["country"] == "FR")
    assert fr["applicability"]["status"] == "needs_verification"
    client.put("/api/v1/profile", json=input_profile(passport_kind="non_biometric"), headers=headers)
    bad = client.post("/api/v1/comparisons", json=body, headers=headers).json()["items"]
    fr_bad = next(item for item in bad if item["destination"]["country"] == "FR")
    assert fr_bad["profile_revision"] == 3 and fr_bad["applicability"]["status"] == "blocked"
    body["answers"] = {"jp_multiple_entry": "SAMPLE-secret-answer"}
    invalid = client.post("/api/v1/comparisons", json=body, headers=headers)
    assert invalid.status_code == 422 and "SAMPLE-secret-answer" not in invalid.text


def test_two_accounts_and_session_survive_restart(settings):
    with TestClient(create_app(settings)) as alice, TestClient(create_app(settings)) as bob:
        a, a_headers = login(alice, "sample_alice")
        b, b_headers = login(bob, "sample_bob")
        alice.put("/api/v1/profile", json=input_profile(), headers=a_headers)
        bob.put("/api/v1/profile", json=input_profile(citizenship="BY"), headers=b_headers)
        a_profile = alice.get("/api/v1/profile").json()
        b_profile = bob.get("/api/v1/profile").json()
        assert a_profile["id"] != b_profile["id"] and b_profile["citizenship"] == "BY"
        assert alice.put("/api/v1/profile", json={"owner_id": b["user"]["id"]}, headers=a_headers).status_code == 422
        assert alice.get("/api/v1/profile").json() == a_profile
        cookie = alice.cookies.get("visapilot_session")
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.set("visapilot_session", cookie)
        assert restarted.get("/api/v1/profile").json() == a_profile
        comparison = restarted.post("/api/v1/comparisons", json={"trip": trip().model_dump(mode="json")}, headers=a_headers)
        assert comparison.status_code == 200


def seed_dependents(connection, owner):
    case_id, document_id = seed_case(connection, owner), seed_document(connection, owner)
    signature = '{"profile_revision":1,"evidence":"SAMPLE"}'
    connection.execute("UPDATE cases SET stale = 0, projection_json = '{\"ready\":true}' WHERE id = ?", (case_id,))
    connection.execute("INSERT INTO case_documents VALUES (?, ?, ?, ?)", (case_id, document_id, owner, NOW))
    connection.execute("INSERT INTO check_results VALUES (?, ?, ?, ?, '[]', 0)", (str(uuid4()), case_id, NOW, signature))
    connection.execute("INSERT INTO routes VALUES (?, 1, '{}', '{}', ?, 0, ?)", (case_id, signature, NOW))
    connection.execute("INSERT INTO letters VALUES (?, ?, 'cover_letter', 'en', 'SAMPLE user-edited text', '[]', 'template', 0, ?, 0, 1, ?, ?)",
                       (str(uuid4()), case_id, signature, NOW, NOW))
    connection.execute("INSERT INTO checklist_actions VALUES (?, 'SAMPLE.action', 1, ?, ?)", (case_id, signature, NOW))
    connection.execute("INSERT INTO events VALUES (?, ?, 'submitted', ?, ?, 'manual', 'SAMPLE history', NULL)", (str(uuid4()), case_id, NOW, NOW))
    return case_id, document_id


def test_change_invalidates_only_owners_snapshots_and_preserves_evidence(settings):
    with TestClient(create_app(settings)) as alice, TestClient(create_app(settings)) as bob:
        a, a_headers = login(alice, "sample_alice")
        b, _ = login(bob, "sample_bob")
        database = alice.app.state.database
        with database.transaction() as connection:
            a_case, a_doc = seed_dependents(connection, a["user"]["id"])
            b_case, _ = seed_dependents(connection, b["user"]["id"])
        assert alice.put("/api/v1/profile", json=input_profile(), headers=a_headers).status_code == 200
        with database.connection() as connection:
            case = connection.execute("SELECT * FROM cases WHERE id = ?", (a_case,)).fetchone()
            assert case["stale"] == 1 and case["stale_reason"] == "profile_changed" and case["projection_json"] is None
            assert case["profile_revision"] == 1  # Old snapshot's inputs stay auditable.
            assert connection.execute("SELECT stale FROM cases WHERE id = ?", (b_case,)).fetchone()[0] == 0
            for table in ("check_results", "routes", "letters"):
                assert connection.execute(f"SELECT stale FROM {table} WHERE case_id = ?", (a_case,)).fetchone()[0] == 1
                assert connection.execute(f"SELECT stale FROM {table} WHERE case_id = ?", (b_case,)).fetchone()[0] == 0
            letter = connection.execute("SELECT * FROM letters WHERE case_id = ?", (a_case,)).fetchone()
            assert letter["content"] == "SAMPLE user-edited text" and letter["edited_by_user"] == 1
            assert json.loads(letter["input_signature_json"])["profile_revision"] == 1
            assert connection.execute("SELECT COUNT(*) FROM documents WHERE id = ?", (a_doc,)).fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM case_documents WHERE case_id = ?", (a_case,)).fetchone()[0] == 1
            assert connection.execute("SELECT confirmed FROM checklist_actions WHERE case_id = ?", (a_case,)).fetchone()[0] == 1
            assert connection.execute("SELECT note FROM events WHERE case_id = ?", (a_case,)).fetchone()[0] == "SAMPLE history"
        # A failed downstream write rolls profile and all invalidations back together.
        with database.connection() as connection:
            connection.execute("CREATE TRIGGER stop_invalidation BEFORE UPDATE ON letters BEGIN SELECT RAISE(ABORT, 'SAMPLE rollback'); END")
        with pytest.raises(sqlite3.IntegrityError, match="SAMPLE rollback"):
            alice.app.state.profile_service.replace(UserContext(user_id=a["user"]["id"]), ProfileInput(given_name="CHANGED"))
        assert alice.get("/api/v1/profile").json()["revision"] == 2
