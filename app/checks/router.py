"""Package 6 routes replace the two pending checks routes as a unit."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.auth.dependencies import require_csrf, require_user
from app.common.models import CheckResult, UserContext
from app.documents.router import get_archive
from .service import ChecksService


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["checks"], dependencies=[Depends(private_response)])
METADATA = {"x-package": 6, "x-implementation": "implemented"}


def get_checks(request: Request) -> ChecksService:
    configured = getattr(request.app.state, "checks_service", None)
    if configured is not None:
        return configured
    return ChecksService(
        request.app.state.database, getattr(request.app.state, "rule_registry", None), get_archive(request),
        rebuilder=getattr(request.app.state, "case_rebuilder", None),
    )


@router.post("/cases/{case_id}/checks", response_model=CheckResult, openapi_extra=METADATA)
def run_checks(case_id: UUID, owner: Annotated[UserContext, Depends(require_csrf)],
               service: Annotated[ChecksService, Depends(get_checks)]):
    return service.run(owner, case_id)


@router.get("/cases/{case_id}/checks", response_model=CheckResult, openapi_extra=METADATA)
def get_case_checks(case_id: UUID, owner: Annotated[UserContext, Depends(require_user)],
                    service: Annotated[ChecksService, Depends(get_checks)]):
    return service.latest(owner, case_id)
