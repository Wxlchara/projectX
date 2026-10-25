"""Real package 1/11/5 integration. Case/check/letter rows are labelled fixtures."""

import asyncio
import io
import json
import sqlite3
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.common.errors import AppError
from app.common.models import DocumentFieldsPatch
from app.documents.application import create_app
from app.documents.types import ExtractionResult, QualityIssue

SAMPLES = Path(__file__).resolve().parents[2] / "demo" / "documents"
PREFIX = "/api/v1/documents"
PASSWORD = "SAMPLE-password-123"


def login(client, username):
    body = {"username": username, "password": PASSWORD}
    headers = {"Origin": "http://testserver"}
    assert client.post("/api/v1/auth/register", headers=headers, json=body).status_code == 201
    result = client.post("/api/v1/auth/login", headers=headers, json=body)
    assert result.status_code == 200
    return result.json(), {"X-CSRF-Token": result.json()["csrf_token"]}


@pytest.fixture
def archive(settings):
    app = create_app(settings)
    with TestClient(app) as alice, TestClient(app) as bob:
        a, ah = login(alice, "sample_alice")
        b, bh = login(bob, "sample_bob")
        yield app, alice, bob, a, b, ah, bh


def upload(client, headers, *, filename="good_passport.pdf", data=None, kind="passport", mime="application/pdf"):
    content = data if data is not None else (SAMPLES / "good_passport.pdf").read_bytes()
    return client.post(PREFIX, headers=headers, files={"file": (filename, content, mime)}, data={"document_type": kind})


def extracted(client, headers):
    created = upload(client, headers)
    assert created.status_code == 201, created.text
    response = client.post(f"{PREFIX}/{created.json()['id']}/extract", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def confirm_patch(document):
    return {"expected_facts_revision": document["facts_revision"], "fields": [
        {"key": field["key"], "value": field["value"], "user_confirmed": True} for field in document["fields"]
    ]}


def test_upload_actual_format_uuid_storage_and_repeated_original(archive, settings):
    app, alice, _, a, _, ah, _ = archive
    data = (SAMPLES / "good_passport.pdf").read_bytes()
    created = upload(alice, ah, filename="../../SAMPLE.png", mime="image/png")
    assert created.status_code == 201, created.text
    document = created.json()
    assert document["mime_type"] == "application/pdf"
    assert document["original_filename"] == "SAMPLE.png"
    assert document["extraction_state"] == "uploaded" and document["fields"] == []
    assert document["facts_revision"] == 1
    assert not {"owner_id", "storage_key", "raw_text"} & document.keys()
    second = upload(alice, ah).json()
    assert second["id"] != document["id"] and second["sha256"] == document["sha256"]
    listing = alice.get(PREFIX)
    assert {item["id"] for item in listing.json()["items"]} == {document["id"], second["id"]}
    with app.state.database.connection() as connection:
        rows = connection.execute("SELECT * FROM documents").fetchall()
    assert all(row["owner_id"] == a["user"]["id"] for row in rows)
    for row in rows:
        path = settings.uploads_dir / row["storage_key"]
        assert path.read_bytes() == data and path.stat().st_mode & 0o777 == 0o600
        assert alice.get("/local_data/uploads/" + row["storage_key"]).status_code == 404
    for _ in range(2):
        download = alice.get(f"{PREFIX}/{document['id']}/file")
        assert download.content == data
        assert download.headers["content-type"] == "application/octet-stream"
        assert download.headers["cache-control"] == "no-store"
        assert download.headers["x-content-type-options"] == "nosniff"


def test_real_auth_ownership_csrf_and_logout(archive):
    _, alice, bob, _, _, ah, bh = archive
    document = upload(alice, ah).json()
    identifier = document["id"]
    assert bob.get(PREFIX).json() == {"items": []}
    assert upload(bob, {}).status_code == 403
    missing = bob.get(f"{PREFIX}/{uuid4()}").json()
    for method, suffix, kwargs in [
        ("get", "", {}), ("get", "/file", {}), ("post", "/extract", {}),
        ("patch", "/fields", {"json": {"expected_facts_revision": 1, "fields": []}}),
        ("delete", "", {}),
    ]:
        result = getattr(bob, method)(f"{PREFIX}/{identifier}{suffix}", headers=bh, **kwargs)
        assert result.status_code == 404 and result.json() == missing
    assert alice.post(f"{PREFIX}/{identifier}/extract").status_code == 403
    assert alice.delete(f"{PREFIX}/{identifier}").status_code == 403
    assert alice.post("/api/v1/auth/logout", headers=ah).status_code == 204
    assert alice.get(f"{PREFIX}/{identifier}/file").status_code == 401
    assert upload(alice, ah).status_code == 401


def test_extraction_confirmation_correction_and_facts_revision(archive):
    app, alice, _, a, _, ah, _ = archive
    document = extracted(alice, ah)
    service = app.state.document_archive
    owner, identifier = UUID(a["user"]["id"]), UUID(document["id"])
    assert document["facts_revision"] == 2 and document["extraction_state"] == "extracted"
    assert service.get_confirmed_facts(owner, [identifier])[0].fields == []
    response = alice.patch(f"{PREFIX}/{identifier}/fields", json=confirm_patch(document), headers=ah)
    assert response.status_code == 200
    confirmed = response.json()
    assert confirmed["facts_revision"] == 3
    assert all(field["confirmed_revision"] == 3 and field["source_kind"] == "pdf_text" for field in confirmed["fields"])
    assert len(service.get_confirmed_facts(owner, [identifier])[0].fields) == 6
    assert alice.patch(f"{PREFIX}/{identifier}/fields", json=confirm_patch(document), headers=ah).status_code == 409
    changed = alice.patch(f"{PREFIX}/{identifier}/fields", json={
        "expected_facts_revision": 3,
        "fields": [{"key": "expires_on", "value": "2033-04-15", "user_confirmed": False}],
    }, headers=ah).json()
    expiry = next(field for field in changed["fields"] if field["key"] == "expires_on")
    assert expiry == {"key": "expires_on", "value": "2033-04-15", "confidence": None,
                      "source_page": None, "source_kind": "manual", "user_confirmed": False, "confirmed_revision": None}
    facts = service.get_confirmed_facts(owner, [identifier])[0]
    assert facts.facts_revision == 4 and len(facts.fields) == 5
    assert all(field.confirmed_revision == 4 for field in facts.fields)
    again = alice.post(f"{PREFIX}/{identifier}/extract", headers=ah).json()
    assert again["facts_revision"] == 5
    assert all(not field["user_confirmed"] and field["confirmed_revision"] is None for field in again["fields"])
    assert next(field["value"] for field in again["fields"] if field["key"] == "expires_on") == "2032-04-15"


@pytest.mark.parametrize("fields", [
    [{"key": "owner_id", "value": "spoof", "user_confirmed": True}],
    [{"key": "birth_date", "value": "1996-02-31", "user_confirmed": True}],
    [{"key": "given_name", "value": None, "user_confirmed": True}],
    [{"key": "given_name", "value": {"command": "ignored"}, "user_confirmed": False}],
    [{"key": "surname", "value": "A", "user_confirmed": True}] * 2,
])
def test_invalid_manual_fields_do_not_mutate_revision(archive, fields):
    _, alice, _, _, _, ah, _ = archive
    document = upload(alice, ah).json()
    result = alice.patch(f"{PREFIX}/{document['id']}/fields", headers=ah,
                         json={"expected_facts_revision": 1, "fields": fields})
    assert result.status_code == 422
    assert alice.get(f"{PREFIX}/{document['id']}").json()["facts_revision"] == 1


@pytest.mark.parametrize("content,status", [(b"", 422), (b"arbitrary text", 415), (b"%PDF-1.7\nbroken", 422)])
def test_invalid_uploads_do_not_leave_files_or_rows(archive, settings, content, status):
    _, alice, _, _, _, ah, _ = archive
    result = upload(alice, ah, data=content)
    assert result.status_code == status
    assert alice.get(PREFIX).json() == {"items": []}
    assert not list(settings.uploads_dir.iterdir()) and not list(settings.temporary_dir.iterdir())


def test_encrypted_and_page_limit_uploads_rejected(archive):
    _, alice, _, _, _, ah, _ = archive
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("SAMPLE")
    stream = io.BytesIO()
    writer.write(stream)
    assert upload(alice, ah, data=stream.getvalue()).json()["error"]["code"] == "encrypted_pdf"
    writer = PdfWriter()
    for _ in range(11):
        writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO()
    writer.write(stream)
    assert upload(alice, ah, data=stream.getvalue()).json()["error"]["code"] == "page_limit"


def test_chunked_oversize_body_rejected_before_spooling_and_closed(archive, settings, monkeypatch):
    _, alice, _, _, _, ah, _ = archive
    settings.max_upload_bytes = 1024
    opened = []
    def spool(*args, **kwargs):
        file = tempfile.SpooledTemporaryFile(*args, **kwargs)
        opened.append(file)
        return file
    monkeypatch.setattr("starlette.formparsers.SpooledTemporaryFile", spool)
    boundary = "SAMPLE_BOUNDARY"
    chunks = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="document_type"\r\n\r\npassport\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode(),
        b"x" * 512, b"x" * 513, f"\r\n--{boundary}--\r\n".encode(),
    ]
    result = alice.post(PREFIX, content=iter(chunks), headers={**ah, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert result.status_code == 413
    assert opened and all(file.closed for file in opened)
    assert alice.get(PREFIX).json() == {"items": []}
    assert not list(settings.uploads_dir.iterdir())


def test_unauthorized_upload_does_not_consume_body(settings):
    consumed = []
    def content():
        consumed.append(True)
        yield b"must not be parsed"
    with TestClient(create_app(settings)) as client:
        result = client.post(PREFIX, content=content(), headers={"Content-Type": "multipart/form-data; boundary=SAMPLE"})
        assert result.status_code == 401
    assert consumed == []


def test_multipart_extra_owner_invalid_kind_and_broken_boundary(archive):
    _, alice, _, _, _, ah, _ = archive
    assert upload(alice, ah, kind="unsupported").status_code == 422
    result = alice.post(PREFIX, files={"file": ("sample.pdf", b"data")},
                        data={"document_type": "passport", "owner_id": str(uuid4())}, headers=ah)
    assert result.status_code == 422
    result = alice.post(PREFIX, content=b"broken", headers={**ah, "Content-Type": "multipart/form-data"})
    assert result.status_code == 422


def test_restart_preserves_archive_confirmations_and_session(archive, settings):
    _, alice, _, _, _, ah, _ = archive
    document = extracted(alice, ah)
    confirmed = alice.patch(f"{PREFIX}/{document['id']}/fields", json=confirm_patch(document), headers=ah).json()
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.update(alice.cookies)
        assert restarted.get("/api/v1/auth/me").status_code == 200
        assert restarted.get(f"{PREFIX}/{document['id']}").json() == confirmed
        assert restarted.get(f"{PREFIX}/{document['id']}/file").content == (SAMPLES / "good_passport.pdf").read_bytes()


def seed_dependencies(app, owner, document_id):
    """Explicit DB fixtures for absent packages 4/6/7/8; no real checks claimed."""
    identifiers = []
    signature = '{"fixture":true}'
    with app.state.database.transaction() as connection:
        for index in range(2):
            case_id = str(uuid4())
            identifiers.append(case_id)
            connection.execute(
                "INSERT INTO cases (id,owner_id,scenario_id,profile_revision,ruleset_version,trip_json,answers_json,"
                "projection_json,stale,created_at,updated_at) VALUES (?,?,'FIXTURE',1,'fixture',?,'{}',?,0,?,?)",
                (case_id, owner, json.dumps({"starts_on": f"2026-12-{index+1:02}"}),
                 '{"fixture":true,"readiness":{"ready":true}}', "2026-10-25T00:00:00+00:00", "2026-10-25T00:00:00+00:00"),
            )
            connection.execute("INSERT INTO case_documents VALUES (?,?,?,?)", (case_id, document_id, owner, "2026-10-25T00:00:00+00:00"))
            connection.execute("INSERT INTO check_results VALUES (?,?,?,?,?,0)", (str(uuid4()), case_id, "2026-10-25T00:00:00+00:00", signature, "[]"))
            connection.execute("INSERT INTO routes VALUES (?,1,'{}','{}',?,0,?)", (case_id, signature, "2026-10-25T00:00:00+00:00"))
            connection.execute(
                "INSERT INTO letters VALUES (?,?,'cover_letter','en','LOCAL FIXTURE','[]','template',0,?,0,0,?,?)",
                (str(uuid4()), case_id, signature, "2026-10-25T00:00:00+00:00", "2026-10-25T00:00:00+00:00"),
            )
    return identifiers


def assert_invalidated(app, cases, reason):
    with app.state.database.connection() as connection:
        for identifier in cases:
            row = connection.execute("SELECT * FROM cases WHERE id = ?", (identifier,)).fetchone()
            assert row["stale"] == 1 and row["projection_json"] is None
            assert row["stale_reason"] == reason and row["revision"] == 2
            for table in ("check_results", "routes", "letters"):
                assert connection.execute(f"SELECT stale FROM {table} WHERE case_id = ?", (identifier,)).fetchone()[0] == 1


@pytest.mark.parametrize("action,reason", [("patch", "document_fields_changed"), ("extract", "document_extracted"), ("delete", "document_deleted")])
def test_mutation_invalidates_all_linked_cases_and_calls_rebuilder(archive, settings, action, reason):
    app, alice, _, a, _, ah, _ = archive
    document = extracted(alice, ah)
    cases = seed_dependencies(app, a["user"]["id"], document["id"])
    calls = []
    class RecordingRebuilder:
        def rebuild_case(self, owner, case_id, reason):
            calls.append((str(owner.user_id), str(case_id), reason))
    app.state.document_archive.rebuilder = RecordingRebuilder()
    if action == "patch":
        response = alice.patch(f"{PREFIX}/{document['id']}/fields", json=confirm_patch(document), headers=ah)
    elif action == "extract":
        response = alice.post(f"{PREFIX}/{document['id']}/extract", headers=ah)
    else:
        response = alice.delete(f"{PREFIX}/{document['id']}", headers=ah)
        assert response.content == b""
        assert not list(settings.uploads_dir.iterdir()) and not list(settings.temporary_dir.iterdir())
        assert alice.get(f"{PREFIX}/{document['id']}/file").status_code == 404
        with app.state.database.connection() as connection:
            assert connection.execute("SELECT count(*) FROM case_documents").fetchone()[0] == 0
    assert response.status_code == (204 if action == "delete" else 200), response.text
    assert_invalidated(app, cases, reason)
    assert sorted(calls) == sorted((a["user"]["id"], identifier, reason) for identifier in cases)


def test_confirmed_facts_reject_foreign_ids(archive):
    app, alice, _, a, b, ah, _ = archive
    document = extracted(alice, ah)
    with pytest.raises(AppError) as raised:
        app.state.document_archive.get_confirmed_facts(UUID(b["user"]["id"]), [UUID(document["id"])])
    assert raised.value.status_code == 404


def test_upload_database_failure_removes_new_original(archive, settings):
    app, _, _, _, _, _, _ = archive
    from app.common.models import DocumentType
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(app.state.document_archive.upload_document(
            uuid4(), (SAMPLES / "good_passport.pdf").read_bytes(), DocumentType.PASSPORT, "SAMPLE.pdf",
        ))
    assert not list(settings.uploads_dir.iterdir())


def test_failed_delete_rolls_back_database_and_restores_file(archive, settings, monkeypatch):
    app, alice, _, a, _, ah, _ = archive
    document = upload(alice, ah).json()
    service = app.state.document_archive
    def failure(*args):
        raise RuntimeError("LOCAL TRANSACTION FAILURE FIXTURE")
    monkeypatch.setattr(service, "_invalidate", failure)
    with pytest.raises(RuntimeError):
        service.delete_document(UUID(a["user"]["id"]), UUID(document["id"]))
    assert alice.get(f"{PREFIX}/{document['id']}/file").content == (SAMPLES / "good_passport.pdf").read_bytes()
    assert not list(settings.temporary_dir.iterdir())


def test_storage_symlink_is_never_downloaded_and_delete_preserves_target(archive, settings, tmp_path):
    app, alice, _, _, _, ah, _ = archive
    document = upload(alice, ah).json()
    with app.state.database.connection() as connection:
        key = connection.execute("SELECT storage_key FROM documents WHERE id = ?", (document["id"],)).fetchone()[0]
    original = settings.uploads_dir / key
    external = tmp_path / "SAMPLE-outside-archive.txt"
    external.write_text("SAMPLE target must remain untouched")
    original.unlink()
    original.symlink_to(external)
    assert alice.get(f"{PREFIX}/{document['id']}/file").status_code == 404
    assert alice.delete(f"{PREFIX}/{document['id']}", headers=ah).status_code == 204
    assert external.read_text() == "SAMPLE target must remain untouched"


@pytest.mark.parametrize("concurrent_action", ["patch", "delete"])
def test_inflight_extraction_cannot_overwrite_manual_edit_or_recreate_deleted(archive, concurrent_action):
    app, alice, _, a, _, ah, _ = archive
    document = upload(alice, ah).json()
    service = app.state.document_archive
    owner, identifier = UUID(a["user"]["id"]), UUID(document["id"])
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        class BlockingFixture:
            async def extract(self, *args):
                started.set()
                await finish.wait()
                return ExtractionResult("passport", "needs_input")
        service.extractor = BlockingFixture()
        task = asyncio.create_task(service.extract_document(owner, identifier))
        await started.wait()
        if concurrent_action == "patch":
            service.update_fields(owner, identifier, DocumentFieldsPatch(expected_facts_revision=1, fields=[
                {"key": "given_name", "value": "MANUAL", "user_confirmed": True},
            ]))
        else:
            service.delete_document(owner, identifier)
        finish.set()
        with pytest.raises(AppError) as raised:
            await task
        assert raised.value.status_code == (409 if concurrent_action == "patch" else 404)
    asyncio.run(scenario())
    if concurrent_action == "patch":
        current = alice.get(f"{PREFIX}/{identifier}").json()
        assert current["fields"][0]["value"] == "MANUAL" and current["facts_revision"] == 2
    else:
        assert alice.get(PREFIX).json() == {"items": []}


@pytest.mark.parametrize("code,state,status", [("processing_busy", "needs_input", 409), ("processing_timeout", "needs_input", 200)])
def test_busy_does_not_overwrite_and_timeout_allows_manual_input(archive, code, state, status):
    app, alice, _, _, _, ah, _ = archive
    document = extracted(alice, ah)
    class ProcessingFixture:
        async def extract(self, *args):
            return ExtractionResult("passport", state, quality_issues=(QualityIssue(code, "LOCAL PROCESSING FIXTURE"),))
    app.state.document_archive.extractor = ProcessingFixture()
    response = alice.post(f"{PREFIX}/{document['id']}/extract", headers=ah)
    assert response.status_code == status
    current = alice.get(f"{PREFIX}/{document['id']}").json()
    if status == 409:
        assert current == document
    else:
        assert current["extraction_state"] == "needs_input"
        assert current["quality_findings"][0]["origin"] == "technical"
        manual = confirm_patch(document)
        manual["expected_facts_revision"] = current["facts_revision"]
        corrected = alice.patch(f"{PREFIX}/{document['id']}/fields", json=manual, headers=ah).json()
        assert corrected["extraction_state"] == "extracted"
        assert all(item["resolved"] for item in corrected["quality_findings"])


def test_document_openapi_matches_shared_models_without_changing_main(archive):
    app, alice, _, _, _, _, _ = archive
    schema = alice.get("/openapi.json").json()
    for path, operations in schema["paths"].items():
        if not path.startswith(PREFIX):
            continue
        for method, operation in operations.items():
            assert operation["x-implementation"] == "implemented"
            assert operation["security"] == [{"SessionCookie": []}]
            if method != "get":
                assert any(parameter["name"] == "X-CSRF-Token" for parameter in operation["parameters"])
    assert schema["paths"][PREFIX]["post"]["responses"]["201"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Document"}
    assert "multipart/form-data" in schema["paths"][PREFIX]["post"]["requestBody"]["content"]
    assert "content" not in schema["paths"][PREFIX + "/{document_id}"]["delete"]["responses"]["204"]
