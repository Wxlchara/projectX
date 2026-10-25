import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.common.db import Database
from app.main import create_app

NOW = datetime.now(timezone.utc).isoformat()


def seed_user(connection, username):
    user_id = str(uuid4())
    connection.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (user_id, username, "test-hash", NOW))
    return user_id


def seed_case(connection, owner):
    case_id = str(uuid4())
    connection.execute(
        "INSERT INTO cases (id, owner_id, scenario_id, profile_revision, ruleset_version, "
        "trip_json, answers_json, created_at, updated_at) VALUES (?, ?, 'FIXTURE', 1, 'fixture', '{}', '{}', ?, ?)",
        (case_id, owner, NOW, NOW),
    )
    return case_id


def seed_document(connection, owner):
    document_id = str(uuid4())
    connection.execute(
        "INSERT INTO documents (id, owner_id, document_type, storage_key, original_filename, "
        "mime_type, size_bytes, sha256, created_at, updated_at) "
        "VALUES (?, ?, 'passport', ?, 'SAMPLE.pdf', 'application/pdf', 100, ?, ?, ?)",
        (document_id, owner, document_id, "a" * 64, NOW, NOW),
    )
    return document_id


def test_schema_persists_across_application_restarts(settings):
    with TestClient(create_app(settings)) as client:
        database = client.app.state.database
        with database.transaction() as connection:
            user = seed_user(connection, "SAMPLE")
        assert client.get("/api/v1/health").status_code == 200
    with TestClient(create_app(settings)) as client:
        with client.app.state.database.connection() as connection:
            assert connection.execute("SELECT id FROM users").fetchone()[0] == user
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert tables == {
                "users", "sessions", "profiles", "cases", "documents", "case_documents",
                "checklist_actions", "check_results", "routes", "letters", "events",
            }


def test_transactions_rollback_and_connections_close(client):
    database = client.app.state.database
    with pytest.raises(RuntimeError):
        with database.transaction() as connection:
            seed_user(connection, "must_rollback")
            raise RuntimeError("rollback")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_ownership_and_document_lifecycle_constraints(client):
    database = client.app.state.database
    with database.transaction() as connection:
        owner = seed_user(connection, "owner")
        other = seed_user(connection, "other")
        first = seed_case(connection, owner)
        second = seed_case(connection, owner)
        own_document = seed_document(connection, owner)
        foreign_document = seed_document(connection, other)
        for case_id in (first, second):
            connection.execute("INSERT INTO case_documents VALUES (?, ?, ?, ?)", (case_id, own_document, owner, NOW))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO case_documents VALUES (?, ?, ?, ?)", (first, foreign_document, owner, NOW))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO case_documents VALUES (?, ?, ?, ?)", (first, foreign_document, other, NOW))
        connection.execute("DELETE FROM cases WHERE id = ?", (first,))
        assert connection.execute("SELECT COUNT(*) FROM documents WHERE id = ?", (own_document,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM case_documents").fetchone()[0] == 1
        connection.execute("DELETE FROM documents WHERE id = ?", (own_document,))
        assert connection.execute("SELECT COUNT(*) FROM case_documents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM cases WHERE id = ?", (second,)).fetchone()[0] == 1


def test_invalid_json_is_rejected_and_future_schema_not_overwritten(client, tmp_path):
    with client.app.state.database.transaction() as connection:
        owner = seed_user(connection, "profile-owner")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO profiles VALUES (?, ?, 1, 'bad json', ?)", (str(uuid4()), owner, NOW))
    database = Database(tmp_path / "future.db")
    with database.connection() as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(RuntimeError, match="Unsupported database schema"):
        database.initialize()
    with database.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
