"""Package 5 HTTP routes. Multipart is bounded before file spooling."""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from python_multipart.multipart import parse_options_header
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.auth.dependencies import require_csrf, require_user
from app.common.errors import AppError
from app.common.models import Document, DocumentFieldsPatch, DocumentType, Items, UserContext
from .service import DocumentArchive
from .types import MAX_FILE_BYTES

METADATA = {"x-package": 5, "x-implementation": "implemented"}


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["documents"], dependencies=[Depends(private_response)])


def get_archive(request: Request) -> DocumentArchive:
    archive = getattr(request.app.state, "document_archive", None)
    if archive is not None:
        return archive
    return DocumentArchive(
        request.app.state.database, request.app.state.settings,
        rebuilder=getattr(request.app.state, "case_rebuilder", None),
    )


Archive = Annotated[DocumentArchive, Depends(get_archive)]
ReadOwner = Annotated[UserContext, Depends(require_user)]
WriteOwner = Annotated[UserContext, Depends(require_csrf)]


class BoundedMultipart(MultiPartParser):
    def __init__(self, headers, stream, file_limit):
        super().__init__(headers, stream, max_files=1, max_fields=1, max_part_size=1024)
        self.file_limit = file_limit
        self.file_bytes = 0
        self.opened_files: list[UploadFile] = []

    def on_headers_finished(self):
        super().on_headers_finished()
        if self._current_part.file is not None:
            self.opened_files.append(self._current_part.file)

    def on_part_data(self, data, start, end):
        if self._current_part.file is not None:
            self.file_bytes += end - start
            if self.file_bytes > self.file_limit:
                raise AppError(413, "file_too_large", "Файл превышает допустимый размер.")
        super().on_part_data(data, start, end)


UPLOAD_BODY = {
    **METADATA,
    "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["file", "document_type"], "additionalProperties": False,
        "properties": {"file": {"type": "string", "format": "binary"},
                       "document_type": {"$ref": "#/components/schemas/DocumentType"}},
    }}}},
}


@router.post("/documents", response_model=Document, status_code=201, openapi_extra=UPLOAD_BODY)
async def upload_document(request: Request, owner: WriteOwner, archive: Archive):
    # No FastAPI body parameter: session/CSRF dependencies finish before parsing.
    content_type, _ = parse_options_header(request.headers.get("content-type", ""))
    if content_type != b"multipart/form-data":
        raise AppError(415, "unsupported_media_type", "Ожидается multipart/form-data.")
    limit = min(archive.settings.max_upload_bytes, MAX_FILE_BYTES)
    body_limit = limit + 64 * 1024
    length = request.headers.get("content-length")
    if length is not None:
        try:
            size = int(length)
            if size < 0:
                raise ValueError
        except ValueError as exc:
            raise AppError(422, "invalid_multipart", "Некорректная длина запроса.") from exc
        if size > body_limit:
            raise AppError(413, "file_too_large", "Запрос превышает допустимый размер.")

    async def bounded_stream():
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > body_limit:
                raise AppError(413, "file_too_large", "Запрос превышает допустимый размер.")
            yield chunk

    parser = BoundedMultipart(request.headers, bounded_stream(), limit)
    try:
        try:
            form = await parser.parse()
        except MultiPartException as exc:
            raise AppError(422, "invalid_multipart", "Некорректный multipart-запрос.") from exc
        if set(form) != {"file", "document_type"} or len(form.multi_items()) != 2:
            raise AppError(422, "invalid_multipart", "Требуются только file и document_type.")
        file = form["file"]
        if not isinstance(file, UploadFile) or not isinstance(form["document_type"], str):
            raise AppError(422, "invalid_multipart", "Некорректные поля загрузки.")
        try:
            kind = DocumentType(form["document_type"])
        except ValueError as exc:
            raise AppError(422, "invalid_document_type", "Тип документа не поддерживается.") from exc
        content = await file.read(limit + 1)
        return await archive.upload_document(owner.user_id, content, kind, file.filename)
    finally:
        # Also close incomplete file parts which never reached FormData.items.
        for opened in parser.opened_files:
            await opened.close()


@router.get("/documents", response_model=Items[Document], openapi_extra=METADATA)
def documents(owner: ReadOwner, archive: Archive):
    return Items[Document](items=archive.list_documents(owner.user_id))


@router.get("/documents/{document_id}", response_model=Document, openapi_extra=METADATA)
def document(document_id: UUID, owner: ReadOwner, archive: Archive):
    return archive.get(owner.user_id, document_id)


@router.get("/documents/{document_id}/file", response_class=Response, openapi_extra=METADATA,
            responses={200: {"description": "Скачать свой оригинал", "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            }}})
def download_document(document_id: UUID, owner: ReadOwner, archive: Archive):
    metadata, content = archive.read_file(owner.user_id, document_id)
    return Response(content, media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename=\"document\"; filename*=UTF-8''{quote(metadata.original_filename, safe='')}",
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


@router.post("/documents/{document_id}/extract", response_model=Document, openapi_extra=METADATA)
async def extract_document(document_id: UUID, owner: WriteOwner, archive: Archive):
    return await archive.extract_document(owner.user_id, document_id)


@router.patch("/documents/{document_id}/fields", response_model=Document, openapi_extra=METADATA)
def patch_fields(document_id: UUID, body: DocumentFieldsPatch, owner: WriteOwner, archive: Archive):
    return archive.update_fields(owner.user_id, document_id, body)


@router.delete("/documents/{document_id}", status_code=204, response_class=Response,
               response_model=None, openapi_extra=METADATA)
def delete_document(document_id: UUID, owner: WriteOwner, archive: Archive):
    archive.delete_document(owner.user_id, document_id)
    return Response(status_code=204)
