"""Ownership queries shared by packages 3–9, including nested case resources."""

import sqlite3
from enum import StrEnum
from uuid import UUID

from app.common.errors import AppError
from app.common.models import UserContext


class ResourceKind(StrEnum):
    PROFILE = "profile"
    CASE = "case"
    DOCUMENT = "document"
    CHECK_RESULT = "check_result"
    ROUTE = "route"
    LETTER = "letter"
    EVENT = "event"


# Only these fixed identifiers enter SQL; resource IDs and owner IDs are bound.
DIRECT_TABLES = {
    ResourceKind.PROFILE: "profiles", ResourceKind.CASE: "cases",
    ResourceKind.DOCUMENT: "documents",
}
CHILD_TABLES = {
    ResourceKind.CHECK_RESULT: ("check_results", "id"),
    ResourceKind.ROUTE: ("routes", "case_id"),
    ResourceKind.LETTER: ("letters", "id"),
    ResourceKind.EVENT: ("events", "id"),
}


def require_owned(
    connection: sqlite3.Connection, owner: UserContext,
    kind: ResourceKind, resource_id: UUID,
) -> sqlite3.Row:
    """Use the same transaction for this check and the subsequent mutation."""
    if kind in DIRECT_TABLES:
        table = DIRECT_TABLES[kind]
        row = connection.execute(
            f"SELECT * FROM {table} WHERE id = ? AND owner_id = ?",
            (str(resource_id), str(owner.user_id)),
        ).fetchone()
    elif kind in CHILD_TABLES:
        table, key = CHILD_TABLES[kind]
        row = connection.execute(
            f"SELECT resource.* FROM {table} resource JOIN cases c ON c.id = resource.case_id "
            f"WHERE resource.{key} = ? AND c.owner_id = ?",
            (str(resource_id), str(owner.user_id)),
        ).fetchone()
    else:
        raise ValueError("Unsupported resource kind")
    if row is None:
        raise AppError(404, "not_found", "Ресурс не найден.")
    return row


def require_owned_case_document(
    connection: sqlite3.Connection, owner: UserContext, case_id: UUID, document_id: UUID,
) -> tuple[sqlite3.Row, sqlite3.Row]:
    case = require_owned(connection, owner, ResourceKind.CASE, case_id)
    document = require_owned(connection, owner, ResourceKind.DOCUMENT, document_id)
    return case, document
