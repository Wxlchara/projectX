"""Owner-scoped archive using Matvey's schema, models and auth guards."""

import hashlib
import logging
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from starlette.concurrency import run_in_threadpool

from app.auth.ownership import ResourceKind, require_owned
from app.common.config import Settings
from app.common.db import Database
from app.common.errors import AppError
from app.common.interfaces import CaseRebuilder
from app.common.models import (
    ConfirmedDocumentFacts, Document, DocumentFieldsPatch, DocumentType,
    Field, Finding, UserContext,
)
from .extraction import DocumentExtractor
from .fields import FIELD_KEYS, LOW_CONFIDENCE, normalize
from .types import ExtractionResult, MAX_FILE_BYTES

FIELDS = TypeAdapter(list[Field])
FINDINGS = TypeAdapter(list[Finding])
STORAGE_KEY = re.compile(r"^[0-9a-f]{32}\.(?:pdf|png|jpg)$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def original_name(filename: str | None) -> str:
    # Display only. Never used to choose a storage path or an extraction result.
    name = (filename or "document").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if char.isprintable()).strip()[:255]
    return name if name not in {"", ".", ".."} else "document"


class DocumentArchive:
    def __init__(self, database: Database, settings: Settings, *,
                 extractor: DocumentExtractor | None = None,
                 rebuilder: CaseRebuilder | None = None):
        self.database = database
        self.settings = settings
        local_models = settings.data_dir / "tessdata"
        use_local_models = (not os.environ.get("TESSDATA_PREFIX")
                            and all((local_models / f"{lang}.traineddata").is_file() for lang in ("rus", "eng")))
        self.extractor = extractor or DocumentExtractor(
            timeout_seconds=min(settings.document_timeout_seconds, 60),
            temporary_root=settings.temporary_dir,
            max_pages=settings.max_document_pages, max_pixels=settings.max_image_pixels,
            tessdata_dir=local_models if use_local_models else None,
        )
        self.rebuilder = rebuilder

    @staticmethod
    def _document(row) -> Document:
        return Document(
            id=row["id"], document_type=row["document_type"],
            original_filename=row["original_filename"], mime_type=row["mime_type"],
            size_bytes=row["size_bytes"], sha256=row["sha256"],
            extraction_state=row["extraction_state"], facts_revision=row["facts_revision"],
            fields=FIELDS.validate_json(row["fields_json"]),
            quality_findings=FINDINGS.validate_json(row["quality_findings_json"]),
            created_at=row["created_at"],
        )

    def _path(self, storage_key: str) -> Path:
        if not STORAGE_KEY.fullmatch(storage_key):
            raise AppError(404, "not_found", "Ресурс не найден.")
        return self.settings.uploads_dir / storage_key

    def get(self, owner_id: UUID, document_id: UUID) -> Document:
        with self.database.connection() as connection:
            return self._document(require_owned(
                connection, UserContext(user_id=owner_id), ResourceKind.DOCUMENT, document_id,
            ))

    def list_documents(self, owner_id: UUID) -> list[Document]:
        with self.database.connection() as connection:
            return [self._document(row) for row in connection.execute(
                "SELECT * FROM documents WHERE owner_id = ? ORDER BY created_at DESC, id",
                (str(owner_id),),
            ).fetchall()]

    def read_file(self, owner_id: UUID, document_id: UUID) -> tuple[Document, bytes]:
        with self.database.connection() as connection:
            row = require_owned(connection, UserContext(user_id=owner_id), ResourceKind.DOCUMENT, document_id)
        path = self._path(row["storage_key"])
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            raise AppError(404, "not_found", "Ресурс не найден.") from exc
        if len(content) != row["size_bytes"] or hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise AppError(503, "stored_file_invalid", "Файл архива недоступен; загрузите новую копию.")
        return self._document(row), content

    async def upload_document(self, owner_id: UUID, content: bytes,
                              document_type: DocumentType, filename: str | None) -> Document:
        if len(content) > min(self.settings.max_upload_bytes, MAX_FILE_BYTES):
            raise AppError(413, "file_too_large", "Файл превышает допустимый размер.")
        inspection = await self.extractor.inspect(content, document_type.value)
        self._check_processing(inspection, uploading=True)
        return await run_in_threadpool(self._save_upload, owner_id, content, document_type, filename, inspection.mime_type)

    def _save_upload(self, owner_id, content, document_type, filename, mime) -> Document:
        identifier = uuid4()
        extension = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}[mime]
        storage_key = f"{identifier.hex}.{extension}"
        path = self._path(storage_key)
        timestamp = now()
        created = False
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO documents (id, owner_id, document_type, storage_key, original_filename, "
                    "mime_type, size_bytes, sha256, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(identifier), str(owner_id), document_type.value, storage_key, original_name(filename),
                     mime, len(content), hashlib.sha256(content).hexdigest(), timestamp, timestamp),
                )
                row = connection.execute("SELECT * FROM documents WHERE id = ?", (str(identifier),)).fetchone()
                document = self._document(row)
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise
        return document

    @staticmethod
    def _check_processing(result: ExtractionResult, *, uploading: bool = False) -> None:
        codes = {issue.code for issue in result.quality_issues}
        if "processing_busy" in codes:
            raise AppError(409, "busy", "Обработчик документов занят. Повторите запрос позже.")
        if uploading and result.extraction_state != "uploaded":
            issue = result.quality_issues[0]
            status = 413 if issue.code == "file_too_large" else 415 if issue.code == "unsupported_type" else 422
            if issue.code in {"processing_timeout", "processing_failed"}:
                status = 503
            raise AppError(status, issue.code, issue.message)

    async def extract_document(self, owner_id: UUID, document_id: UUID) -> Document:
        document, content = await run_in_threadpool(self.read_file, owner_id, document_id)
        result = await self.extractor.extract(content, document.document_type.value)
        self._check_processing(result)
        return await run_in_threadpool(self._save_extraction, owner_id, document, result)

    def _save_extraction(self, owner_id, previous: Document, result: ExtractionResult) -> Document:
        fields = [Field.model_validate(asdict(field)) for field in result.fields]
        findings = [Finding(
            code=issue.code, severity="verify", message=issue.message,
            next_action="Сверьте поля с оригиналом, исправьте и подтвердите их или повторите извлечение.",
            document_ids=[previous.id], field_paths=[f"fields.{issue.field_key}"] if issue.field_key else [],
            origin="technical",
        ) for issue in result.quality_issues]
        owner = UserContext(user_id=owner_id)
        with self.database.transaction() as connection:
            row = require_owned(connection, owner, ResourceKind.DOCUMENT, previous.id)
            self._expect_revision(row, previous.facts_revision)
            connection.execute(
                "UPDATE documents SET extraction_state = ?, facts_revision = facts_revision + 1, "
                "fields_json = ?, quality_findings_json = ?, raw_text = NULL, updated_at = ? WHERE id = ? AND owner_id = ?",
                (result.extraction_state, FIELDS.dump_json(fields).decode(), FINDINGS.dump_json(findings).decode(),
                 now(), str(previous.id), str(owner_id)),
            )
            affected = self._invalidate(connection, owner_id, previous.id, "document_extracted")
            current = self._document(require_owned(connection, owner, ResourceKind.DOCUMENT, previous.id))
        self._rebuild(owner, affected, "document_extracted")
        return current

    @staticmethod
    def _expect_revision(row, expected: int) -> None:
        if row["facts_revision"] != expected:
            raise AppError(409, "revision_conflict", "Поля изменились. Обновите документ и повторите действие.",
                           {"facts_revision": row["facts_revision"]})

    def update_fields(self, owner_id: UUID, document_id: UUID, patch: DocumentFieldsPatch) -> Document:
        owner = UserContext(user_id=owner_id)
        with self.database.transaction() as connection:
            row = require_owned(connection, owner, ResourceKind.DOCUMENT, document_id)
            self._expect_revision(row, patch.expected_facts_revision)
            previous = self._document(row)
            keys = [item.key for item in patch.fields]
            if len(set(keys)) != len(keys) or any(key not in FIELD_KEYS[previous.document_type.value] for key in keys):
                raise AppError(422, "invalid_field_keys", "Ключи полей не поддерживаются или повторяются.")
            fields = {field.key: field.model_copy() for field in previous.fields}
            for item in patch.fields:
                if item.value is not None and not isinstance(item.value, str):
                    raise AppError(422, "invalid_field_value", "Значение поля должно быть строкой или null.")
                value = normalize(item.key, item.value) if isinstance(item.value, str) else None
                if (item.value is not None and value is None) or (item.user_confirmed and value is None):
                    raise AppError(422, "invalid_field_value", "Проверьте формат и заполненность поля.", {"key": item.key})
                existing = fields.get(item.key)
                if existing is not None and existing.value == value:
                    fields[item.key] = existing.model_copy(update={"user_confirmed": item.user_confirmed})
                else:
                    fields[item.key] = Field(key=item.key, value=value, source_kind="manual", user_confirmed=item.user_confirmed)
            if not patch.fields:
                return previous
            revision = previous.facts_revision + 1
            for field in fields.values():
                # Preserve confirmation only for values which did not change, or
                # which the current PATCH explicitly confirmed. Re-extraction resets all.
                field.confirmed_revision = revision if field.user_confirmed else None
            findings = self._refresh_quality(previous, fields)
            state = "needs_input" if any(not item.resolved for item in findings) else "extracted"
            connection.execute(
                "UPDATE documents SET fields_json = ?, facts_revision = ?, quality_findings_json = ?, "
                "extraction_state = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                (FIELDS.dump_json(list(fields.values())).decode(), revision, FINDINGS.dump_json(findings).decode(),
                 state, now(), str(document_id), str(owner_id)),
            )
            affected = self._invalidate(connection, owner_id, document_id, "document_fields_changed")
            current = self._document(require_owned(connection, owner, ResourceKind.DOCUMENT, document_id))
        self._rebuild(owner, affected, "document_fields_changed")
        return current

    @staticmethod
    def _refresh_quality(document: Document, fields: dict[str, Field]) -> list[Finding]:
        missing = {key for key in FIELD_KEYS[document.document_type.value] if key not in fields or fields[key].value is None}
        all_confirmed = not missing and all(field.user_confirmed for field in fields.values())
        result = []
        for finding in document.quality_findings:
            changed = finding.model_copy(deep=True)
            referenced = [path.removeprefix("fields.") for path in finding.field_paths]
            if referenced:
                changed.resolved = all(
                    key not in missing and key in fields and (
                        fields[key].source_kind == "manual" or fields[key].user_confirmed
                        or (finding.code == "field_missing" and fields[key].value is not None)
                    ) for key in referenced
                )
            elif all_confirmed:
                changed.resolved = True
            result.append(changed)
        for key in missing:
            if not any(f.code == "field_missing" and f.field_paths == [f"fields.{key}"] and not f.resolved for f in result):
                result.append(Finding(
                    code="field_missing", severity="verify", message="Заполните поле документа.",
                    next_action="Сверьте значение с оригиналом и подтвердите его.",
                    document_ids=[document.id], field_paths=[f"fields.{key}"], origin="technical",
                ))
        # A revoked confirmation on low-confidence OCR must restore the warning.
        for key, field in fields.items():
            if field.source_kind == "ocr" and not field.user_confirmed and (field.confidence is None or field.confidence < LOW_CONFIDENCE):
                for finding in result:
                    if finding.code == "low_confidence" and finding.field_paths == [f"fields.{key}"]:
                        finding.resolved = False
        return result

    def get_confirmed_facts(self, owner_id: UUID, document_ids: list[UUID]) -> list[ConfirmedDocumentFacts]:
        owner = UserContext(user_id=owner_id)
        with self.database.transaction() as connection:
            result = []
            for identifier in dict.fromkeys(document_ids):
                document = self._document(require_owned(connection, owner, ResourceKind.DOCUMENT, identifier))
                result.append(ConfirmedDocumentFacts(
                    document_id=document.id, document_type=document.document_type,
                    facts_revision=document.facts_revision,
                    fields=[field for field in document.fields if field.user_confirmed
                            and field.confirmed_revision == document.facts_revision and field.value is not None],
                ))
            return result

    def delete_document(self, owner_id: UUID, document_id: UUID) -> None:
        owner = UserContext(user_id=owner_id)
        tombstone = self.settings.temporary_dir / f"delete-{uuid4().hex}"
        moved = False
        try:
            with self.database.transaction() as connection:
                row = require_owned(connection, owner, ResourceKind.DOCUMENT, document_id)
                path = self._path(row["storage_key"])
                if path.exists() or path.is_symlink():
                    os.replace(path, tombstone)
                    moved = True
                affected = self._invalidate(connection, owner_id, document_id, "document_deleted")
                connection.execute("DELETE FROM documents WHERE id = ? AND owner_id = ?", (str(document_id), str(owner_id)))
        except BaseException:
            if moved:
                os.replace(tombstone, path)
            raise
        if moved:
            tombstone.unlink()
        self._rebuild(owner, affected, "document_deleted")

    @staticmethod
    def _invalidate(connection, owner_id, document_id, reason) -> list[UUID]:
        affected = [UUID(row[0]) for row in connection.execute(
            "SELECT case_id FROM case_documents WHERE document_id = ? AND owner_id = ?",
            (str(document_id), str(owner_id)),
        ).fetchall()]
        for case_id in affected:
            connection.execute(
                "UPDATE cases SET stale = 1, stale_reason = ?, projection_json = NULL, "
                "revision = revision + 1, updated_at = ? WHERE id = ? AND owner_id = ?",
                (reason, now(), str(case_id), str(owner_id)),
            )
            for table in ("check_results", "letters", "routes"):
                connection.execute(f"UPDATE {table} SET stale = 1 WHERE case_id = ?", (str(case_id),))
        return affected

    def _rebuild(self, owner: UserContext, affected: list[UUID], reason: str) -> None:
        if self.rebuilder is None:
            return  # Safe stale/NULL projections until package 4 is connected.
        for case_id in affected:
            try:
                self.rebuilder.rebuild_case(owner, case_id, reason)
            except Exception as exc:
                # The mutation is committed; dirty state remains available for a retry.
                logging.getLogger(__name__).warning("Case rebuild deferred: %s", type(exc).__name__)
