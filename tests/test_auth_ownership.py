"""Real auth + resource guards. Feature HTTP adapters below are test fixtures."""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from app.auth.dependencies import (
    ResourceKind, require_csrf, require_owned, require_owned_case_document, require_user,
)
from app.common.errors import AppError
from app.common.models import UserContext
from app.main import create_app

NOW = datetime.now(timezone.utc).isoformat()
PASSWORD = "SAMPLE-Password-123"


def sign_in(client, username):
    body = {"username": username, "password": PASSWORD}
    headers = {"Origin": "http://testserver"}
    assert client.post("/api/v1/auth/register", json=body, headers=headers).status_code == 201
    response = client.post("/api/v1/auth/login", json=body, headers=headers)
    assert response.status_code == 200
    return response.json()


def seed_case(connection, owner_id):
    case_id = str(uuid4())
    connection.execute(
        "INSERT INTO cases (id, owner_id, scenario_id, profile_revision, ruleset_version, "
        "trip_json, answers_json, created_at, updated_at) "
        "VALUES (?, ?, 'FIXTURE', 1, 'fixture', '{}', '{}', ?, ?)",
        (case_id, owner_id, NOW, NOW),
    )
    return case_id


def seed_document(connection, owner_id, key):
    document_id = str(uuid4())
    connection.execute(
        "INSERT INTO documents (id, owner_id, document_type, storage_key, original_filename, "
        "mime_type, size_bytes, sha256, created_at, updated_at) "
        "VALUES (?, ?, 'passport', ?, 'SAMPLE.txt', 'application/pdf', 100, ?, ?, ?)",
        (document_id, owner_id, key, "a" * 64, NOW, NOW),
    )
    return document_id


def test_download_and_attachment_isolation_with_real_cookie_sessions(settings):
    documents = APIRouter()
    cases = APIRouter()

    @documents.get("/documents/{document_id}/file")
    def download(
        document_id: UUID, request: Request,
        owner: Annotated[UserContext, Depends(require_user)],
    ):
        with request.app.state.database.connection() as connection:
            row = require_owned(connection, owner, ResourceKind.DOCUMENT, document_id)
        return FileResponse(request.app.state.settings.uploads_dir / row["storage_key"])

    @cases.put("/cases/{case_id}/documents/{document_id}", status_code=204)
    def attach(
        case_id: UUID, document_id: UUID, request: Request,
        owner: Annotated[UserContext, Depends(require_csrf)],
    ):
        with request.app.state.database.transaction() as connection:
            require_owned_case_document(connection, owner, case_id, document_id)
            connection.execute(
                "INSERT INTO case_documents VALUES (?, ?, ?, ?)",
                (str(case_id), str(document_id), str(owner.user_id), NOW),
            )
        return Response(status_code=204)

    application = create_app(settings, feature_routers={4: cases, 5: documents})
    operations = application.openapi()["paths"]
    assert operations["/api/v1/documents/{document_id}/file"]["get"]["security"] == [{"SessionCookie": []}]
    assert operations["/api/v1/cases/{case_id}/documents/{document_id}"]["put"]["security"] == [{"SessionCookie": []}]
    with TestClient(application) as alice, TestClient(application) as bob:
        a = sign_in(alice, "alice")
        b = sign_in(bob, "bob")
        with application.state.database.transaction() as connection:
            case_a = seed_case(connection, a["user"]["id"])
            case_b = seed_case(connection, b["user"]["id"])
            doc_a = seed_document(connection, a["user"]["id"], "sample-a")
            doc_b = seed_document(connection, b["user"]["id"], "sample-b")
        settings.uploads_dir.joinpath("sample-a").write_bytes(b"SAMPLE private A")
        settings.uploads_dir.joinpath("sample-b").write_bytes(b"SAMPLE private B")
        assert alice.get(f"/api/v1/documents/{doc_a}/file").content == b"SAMPLE private A"
        assert bob.get(f"/api/v1/documents/{doc_b}/file").content == b"SAMPLE private B"
        missing = alice.get(f"/api/v1/documents/{uuid4()}/file")
        for client, foreign_id in ((alice, doc_b), (bob, doc_a)):
            denied = client.get(f"/api/v1/documents/{foreign_id}/file")
            assert denied.status_code == missing.status_code == 404
            assert denied.json() == missing.json()
        # Both sides of the link are checked, even when a client supplies owner_id.
        csrf_a = {"X-CSRF-Token": a["csrf_token"]}
        for case_id, document_id in ((case_a, doc_b), (case_b, doc_a), (case_b, doc_b)):
            denied = alice.put(
                f"/api/v1/cases/{case_id}/documents/{document_id}", headers=csrf_a,
                json={"owner_id": b["user"]["id"]},
            )
            assert denied.status_code == 404
        assert alice.put(f"/api/v1/cases/{case_a}/documents/{doc_a}").status_code == 403
        assert alice.put(f"/api/v1/cases/{case_a}/documents/{doc_a}", headers=csrf_a).status_code == 204
        with application.state.database.connection() as connection:
            rows = connection.execute("SELECT * FROM case_documents").fetchall()
            assert len(rows) == 1 and rows[0]["owner_id"] == a["user"]["id"]
        assert alice.post("/api/v1/auth/logout", headers=csrf_a).status_code == 204
        assert alice.get(f"/api/v1/documents/{doc_a}/file").status_code == 401
        assert alice.put(f"/api/v1/cases/{case_a}/documents/{doc_a}", headers=csrf_a).status_code == 401


def test_all_direct_and_nested_resources_use_case_owner(client):
    a = sign_in(client, "alice")
    # Register B without changing A's authenticated context.
    b = client.post(
        "/api/v1/auth/register", headers={"Origin": "http://testserver"},
        json={"username": "bob", "password": PASSWORD},
    ).json()
    owner = UserContext(user_id=a["user"]["id"])
    stranger = UserContext(user_id=b["id"])
    with client.app.state.database.transaction() as connection:
        case_id = seed_case(connection, str(owner.user_id))
        document_id = seed_document(connection, str(owner.user_id), "test-resource")
        profile_id = connection.execute("SELECT id FROM profiles WHERE owner_id = ?", (str(owner.user_id),)).fetchone()[0]
        check_id, letter_id, event_id = (str(uuid4()) for _ in range(3))
        connection.execute("INSERT INTO check_results VALUES (?, ?, ?, '{}', '[]', 1)", (check_id, case_id, NOW))
        connection.execute("INSERT INTO routes VALUES (?, 1, '{}', NULL, NULL, 1, ?)", (case_id, NOW))
        connection.execute(
            "INSERT INTO letters VALUES (?, ?, 'cover_letter', 'en', 'SAMPLE', '[]', 'template', 0, '{}', 1, 0, ?, ?)",
            (letter_id, case_id, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO events VALUES (?, ?, 'submitted', ?, ?, 'manual', NULL, NULL)",
            (event_id, case_id, NOW, NOW),
        )
        ids = {
            ResourceKind.PROFILE: profile_id, ResourceKind.CASE: case_id,
            ResourceKind.DOCUMENT: document_id, ResourceKind.CHECK_RESULT: check_id,
            ResourceKind.ROUTE: case_id, ResourceKind.LETTER: letter_id, ResourceKind.EVENT: event_id,
        }
        for kind, resource_id in ids.items():
            assert require_owned(connection, owner, kind, UUID(resource_id)) is not None
            for denied_owner, denied_id in ((stranger, UUID(resource_id)), (owner, uuid4())):
                with pytest.raises(AppError) as denied:
                    require_owned(connection, denied_owner, kind, denied_id)
                assert denied.value.status_code == 404
                assert denied.value.error.code == "not_found"
        with pytest.raises(ValueError):
            require_owned(connection, owner, "documents; DROP TABLE users", uuid4())
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
