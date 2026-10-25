"""Profile and comparison endpoints always use the session's owner."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.common.dependencies import require_csrf, require_user
from app.common.models import (
    ApplicantProfile, ComparisonInput, DestinationComparison, Items, ProfileInput, UserContext,
)


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["profile"], dependencies=[Depends(no_store)])
IMPLEMENTED = {"x-package": 3, "x-implementation": "implemented"}


@router.get("/profile", response_model=ApplicantProfile, openapi_extra=IMPLEMENTED)
def get_profile(request: Request, owner: Annotated[UserContext, Depends(require_user)]):
    return request.app.state.profile_service.get(owner)


@router.put("/profile", response_model=ApplicantProfile, openapi_extra=IMPLEMENTED,
            description="Полностью заменить поля профиля; пропуски становятся null. Изменение увеличивает revision и помечает зависимые результаты устаревшими.")
def put_profile(body: ProfileInput, request: Request, owner: Annotated[UserContext, Depends(require_csrf)]):
    return request.app.state.profile_service.replace(owner, body)


@router.post("/comparisons", response_model=Items[DestinationComparison], openapi_extra=IMPLEMENTED,
             description="Применить проверенные правила 12 направлений к текущему профилю и поездке. Это не проверка документов и не вероятность визы.")
def compare(body: ComparisonInput, request: Request, owner: Annotated[UserContext, Depends(require_csrf)]):
    profile = request.app.state.profile_service.get(owner)
    return Items(items=request.app.state.applicability_evaluator.compare(profile, body.trip, body.answers))
