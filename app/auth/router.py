"""Real /auth endpoints. Bearer tokens are only sent as HttpOnly cookies."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from app.auth.service import AuthService, COOKIE_PATH
from app.common.dependencies import require_csrf, require_user
from app.common.models import Credentials, SessionResponse, User


def service(request: Request) -> AuthService:
    return request.app.state.auth_service


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def require_origin(request: Request) -> None:
    service(request).check_origin(request)


router = APIRouter(
    prefix="/auth", tags=["auth"], dependencies=[Depends(no_store)],
)
ORIGIN_PARAMETER = {
    "name": "Origin", "in": "header", "required": True,
    "schema": {"type": "string"},
    "description": "Разрешённый origin frontend, например http://127.0.0.1:8000",
}


@router.post(
    "/register", response_model=User, status_code=201,
    dependencies=[Depends(require_origin)],
    description="Создать аккаунт без входа. Нужен разрешённый Origin. Затем выполнить login.",
    openapi_extra={"x-package": 11, "x-implementation": "implemented", "parameters": [ORIGIN_PARAMETER]},
)
def register(body: Credentials, auth: Annotated[AuthService, Depends(service)]):
    return auth.register(body)


@router.post(
    "/login", response_model=SessionResponse, dependencies=[Depends(require_origin)],
    description="Войти с паролем, выдать новую cookie-сессию на 12 часов и CSRF. Нужен разрешённый Origin.",
    openapi_extra={"x-package": 11, "x-implementation": "implemented", "parameters": [ORIGIN_PARAMETER]},
)
def login(
    body: Credentials, request: Request, response: Response,
    auth: Annotated[AuthService, Depends(service)],
):
    session = auth.login(body, request.cookies.get(auth.settings.session_cookie_name))
    response.set_cookie(
        key=auth.settings.session_cookie_name, value=session.token,
        max_age=auth.settings.session_ttl_seconds, expires=session.expires_at,
        path=COOKIE_PATH, httponly=True, secure=auth.settings.cookie_secure, samesite="lax",
    )
    return SessionResponse(user=session.user, csrf_token=session.csrf_token)


@router.get(
    "/me", response_model=SessionResponse,
    dependencies=[Depends(require_user)],
    openapi_extra={"x-package": 11, "x-implementation": "implemented"},
)
async def me(
    request: Request, auth: Annotated[AuthService, Depends(service)],
):
    session = await auth.current_session(request)
    return SessionResponse(user=session.user, csrf_token=session.csrf_token)


@router.post(
    "/logout", status_code=204,
    dependencies=[Depends(require_csrf)],
    openapi_extra={"x-package": 11, "x-implementation": "implemented"},
)
async def logout(
    request: Request, response: Response,
    auth: Annotated[AuthService, Depends(service)],
):
    session = await auth.current_session(request)
    await run_in_threadpool(auth.revoke, session.token)
    response.delete_cookie(
        auth.settings.session_cookie_name, path=COOKIE_PATH,
        httponly=True, secure=auth.settings.cookie_secure, samesite="lax",
    )
