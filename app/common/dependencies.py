"""Fail-closed auth bridge; package 11 supplies the actual AuthGateway."""

from typing import Annotated

from fastapi import Header, Request
from fastapi.security import APIKeyCookie

from app.common.db import Database
from app.common.errors import module_unavailable
from app.common.models import UserContext

# main.py reflects a custom cookie name in the generated OpenAPI document.
session_cookie = APIKeyCookie(
    name="visapilot_session", scheme_name="SessionCookie", auto_error=False
)


def get_database(request: Request) -> Database:
    return request.app.state.database


async def require_user(request: Request) -> UserContext:
    gateway = request.app.state.auth_gateway
    if gateway is None:
        raise module_unavailable(11)
    return await gateway.require_user(request)


async def require_csrf(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> UserContext:
    gateway = request.app.state.auth_gateway
    if gateway is None:
        raise module_unavailable(11)
    return await gateway.require_csrf(request)
