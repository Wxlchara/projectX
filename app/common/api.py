"""Contract-only routers. Replaced as a whole by each feature's real router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response

from app.common import models as m
from app.common.dependencies import require_csrf, require_user
from app.common.errors import ERROR_RESPONSES, module_unavailable

PACKAGE_NAMES = {
    11: "auth", 2: "rules", 3: "profile", 4: "cases", 5: "documents",
    6: "checks", 7: "itinerary", 8: "letters", 9: "events",
}
PENDING_ROUTERS = {
    package: APIRouter(tags=[name]) for package, name in PACKAGE_NAMES.items()
}


def endpoint(package, method, path, response_model=None, *, status=200, public=False, media=None):
    dependencies = []
    if not public:
        guard = require_user if method == "GET" else require_csrf
        dependencies = [Depends(guard)]
    responses = dict(ERROR_RESPONSES)
    if media:
        responses[status] = {
            "description": "Защищённое скачивание после реализации пакета",
            "content": {media: {"schema": {"type": "string", "format": "binary"}}},
        }
    return PENDING_ROUTERS[package].api_route(
        path, methods=[method], response_model=response_model, status_code=status,
        response_class=Response if media else JSONResponse,
        responses=responses, dependencies=dependencies,
        description=(
            f"Контракт пакета {package}. Реализация отсутствует: сейчас возвращает 503 "
            "module_not_ready. Указанный успешный ответ — целевой интерфейс."
        ),
        openapi_extra={"x-package": package, "x-implementation": "pending"},
    )


@endpoint(11, "POST", "/auth/register", m.User, status=201, public=True)
async def register(body: m.Credentials):
    raise module_unavailable(11)


@endpoint(11, "POST", "/auth/login", m.SessionResponse, public=True)
async def login(body: m.Credentials):
    raise module_unavailable(11)


@endpoint(11, "GET", "/auth/me", m.SessionResponse)
async def me():
    raise module_unavailable(11)


@endpoint(11, "POST", "/auth/logout", status=204)
async def logout():
    raise module_unavailable(11)


@endpoint(2, "GET", "/destinations", m.Items[m.Destination], public=True)
async def destinations():
    raise module_unavailable(2)


@endpoint(2, "GET", "/scenarios/{scenario_id}", m.Scenario, public=True)
async def scenario(scenario_id: str):
    raise module_unavailable(2)


@endpoint(3, "GET", "/profile", m.ApplicantProfile)
async def profile():
    raise module_unavailable(3)


@endpoint(3, "PUT", "/profile", m.ApplicantProfile)
async def update_profile(body: m.ProfileInput):
    raise module_unavailable(3)


@endpoint(3, "POST", "/comparisons", m.Items[m.DestinationComparison])
async def compare(body: m.ComparisonInput):
    raise module_unavailable(3)


@endpoint(4, "POST", "/cases", m.Case, status=201)
async def create_case(body: m.CaseCreate):
    raise module_unavailable(4)


@endpoint(4, "GET", "/cases", m.Items[m.Case])
async def cases():
    raise module_unavailable(4)


@endpoint(4, "GET", "/cases/{case_id}", m.Case)
async def case(case_id: UUID):
    raise module_unavailable(4)


@endpoint(4, "PATCH", "/cases/{case_id}", m.Case)
async def update_case(case_id: UUID, body: m.CasePatch):
    raise module_unavailable(4)


@endpoint(4, "DELETE", "/cases/{case_id}", status=204)
async def delete_case(case_id: UUID):
    raise module_unavailable(4)


@endpoint(4, "PUT", "/cases/{case_id}/documents/{document_id}", m.Case)
async def attach_document(case_id: UUID, document_id: UUID):
    raise module_unavailable(4)


@endpoint(4, "DELETE", "/cases/{case_id}/documents/{document_id}", status=204)
async def detach_document(case_id: UUID, document_id: UUID):
    raise module_unavailable(4)


@endpoint(4, "PATCH", "/cases/{case_id}/checklist/{item_id}", m.Case)
async def checklist_action(case_id: UUID, item_id: str, body: m.ChecklistPatch):
    raise module_unavailable(4)


@endpoint(5, "POST", "/documents", m.Document, status=201)
async def upload_document(
    file: Annotated[UploadFile, File()],
    document_type: Annotated[m.DocumentType, Form()],
):
    raise module_unavailable(5)


@endpoint(5, "GET", "/documents", m.Items[m.Document])
async def documents():
    raise module_unavailable(5)


@endpoint(5, "GET", "/documents/{document_id}", m.Document)
async def document(document_id: UUID):
    raise module_unavailable(5)


@endpoint(5, "GET", "/documents/{document_id}/file", media="application/octet-stream")
async def download_document(document_id: UUID):
    raise module_unavailable(5)


@endpoint(5, "POST", "/documents/{document_id}/extract", m.Document)
async def extract_document(document_id: UUID):
    raise module_unavailable(5)


@endpoint(5, "PATCH", "/documents/{document_id}/fields", m.Document)
async def confirm_fields(document_id: UUID, body: m.DocumentFieldsPatch):
    raise module_unavailable(5)


@endpoint(5, "DELETE", "/documents/{document_id}", status=204)
async def delete_document(document_id: UUID):
    raise module_unavailable(5)


@endpoint(6, "POST", "/cases/{case_id}/checks", m.CheckResult)
async def check_case(case_id: UUID):
    raise module_unavailable(6)


@endpoint(6, "GET", "/cases/{case_id}/checks", m.CheckResult)
async def checks(case_id: UUID):
    raise module_unavailable(6)


@endpoint(7, "PUT", "/cases/{case_id}/route", m.Route)
async def update_route(case_id: UUID, body: m.Route):
    raise module_unavailable(7)


@endpoint(7, "POST", "/cases/{case_id}/route/check", m.RouteResult)
async def check_route(case_id: UUID):
    raise module_unavailable(7)


@endpoint(8, "POST", "/cases/{case_id}/letters", m.Letter, status=201)
async def create_letter(case_id: UUID, body: m.LetterCreate):
    raise module_unavailable(8)


@endpoint(8, "GET", "/cases/{case_id}/letters", m.Items[m.Letter])
async def letters(case_id: UUID):
    raise module_unavailable(8)


@endpoint(8, "PATCH", "/letters/{letter_id}", m.Letter)
async def update_letter(letter_id: UUID, body: m.LetterPatch):
    raise module_unavailable(8)


@endpoint(8, "GET", "/letters/{letter_id}/file", media="text/plain")
async def download_letter(letter_id: UUID):
    raise module_unavailable(8)


@endpoint(9, "POST", "/cases/{case_id}/events", m.Event, status=201)
async def create_event(case_id: UUID, body: m.EventCreate):
    raise module_unavailable(9)


@endpoint(9, "GET", "/cases/{case_id}/events", m.Items[m.Event])
async def events(case_id: UUID):
    raise module_unavailable(9)


@endpoint(9, "GET", "/cases/{case_id}/notifications", m.Items[m.Notification])
async def notifications(case_id: UUID):
    raise module_unavailable(9)
