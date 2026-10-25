"""Composition root owned by Matvey. One Uvicorn process, same-origin API."""

from collections.abc import Mapping
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.openapi.utils import get_openapi
from starlette.concurrency import run_in_threadpool

from app.auth.router import router as auth_router
from app.auth.service import AuthService
from app.common.api import PACKAGE_NAMES, PENDING_ROUTERS
from app.common.concurrency import ProcessingGuards
from app.common.config import Settings
from app.common.db import SCHEMA_VERSION, Database
from app.common.errors import AppError, ERROR_RESPONSES, install_error_handlers
from app.common.interfaces import AuthGateway
from app.common.models import Health

API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    *,
    feature_routers: Mapping[int, APIRouter] | None = None,
    auth_gateway: AuthGateway | None = None,
) -> FastAPI:
    config = settings if settings is not None else Settings()
    database = Database(config.database_path)
    installed = dict(feature_routers or {})
    if set(installed) - PACKAGE_NAMES.keys():
        raise ValueError("Unknown feature package")
    installed.setdefault(11, auth_router)
    authentication = AuthService(database, config)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        def initialize():
            config.uploads_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            config.temporary_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            database.initialize()

        await run_in_threadpool(initialize)
        yield

    application = FastAPI(
        title="VisaPilot API", version="0.1.0", lifespan=lifespan,
        description=(
            "Локальное демо на вымышленных документах. Работают health и аккаунты/сессии (пакеты 1/11); "
            "остальные маршруты документируют контракт и отвечают 503 после входа до реализации. "
            "Frontend использует один origin и credentials=include."
        ),
        responses=ERROR_RESPONSES,
    )
    application.state.settings = config
    application.state.database = database
    application.state.auth_service = authentication
    application.state.auth_gateway = auth_gateway if auth_gateway is not None else authentication
    application.state.processing = ProcessingGuards()
    install_error_handlers(application)

    @application.get(API_PREFIX + "/health", response_model=Health, tags=["health"])
    def health(request: Request):
        if not request.app.state.database.is_ready():
            raise AppError(503, "database_unavailable", "База данных не готова.")
        return Health(
            status="ok", demo_mode=config.demo_mode,
            database="ok", schema_version=SCHEMA_VERSION,
        )

    for package, pending_router in PENDING_ROUTERS.items():
        router = installed.get(package, pending_router)
        application.include_router(router, prefix=API_PREFIX)

    def openapi():
        if application.openapi_schema is None:
            schema = get_openapi(
                title=application.title, version=application.version,
                description=application.description, routes=application.routes,
            )
            security = schema.get("components", {}).get("securitySchemes", {})
            if "SessionCookie" in security:
                security["SessionCookie"]["name"] = config.session_cookie_name
            application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = openapi
    return application


app = create_app()
