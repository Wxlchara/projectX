"""One error envelope, including framework validation and unexpected failures."""

import logging
import sqlite3

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.common.models import Error, ErrorResponse


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details=None):
        self.status_code = status_code
        self.error = Error(code=code, message=message, details=details or {})


def module_unavailable(package: int) -> AppError:
    return AppError(
        503, "module_not_ready", "Пакет ещё не реализован.", {"package": package}
    )


def error_response(status_code: int, error: Error, headers=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(error=error).model_dump(mode="json"),
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def application_error(request: Request, exc: AppError):
        return error_response(exc.status_code, exc.error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic's input/ctx/message can contain passwords and uploaded text.
        fields = [
            {"location": list(item["loc"]), "type": item["type"]}
            for item in exc.errors()
        ]
        return error_response(422, Error(
            code="validation_error", message="Некорректные поля запроса.",
            details={"fields": fields},
        ))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        codes = {
            401: "unauthenticated", 403: "csrf_failed", 404: "not_found",
            405: "method_not_allowed", 409: "conflict", 413: "file_too_large",
            415: "unsupported_media_type", 422: "validation_error",
            503: "unavailable",
        }
        return error_response(exc.status_code, Error(
            code=codes.get(exc.status_code, "http_error"),
            message=str(exc.detail) if isinstance(exc.detail, str) else "Ошибка запроса.",
        ), headers=exc.headers)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request: Request, exc: sqlite3.OperationalError):
        return error_response(503, Error(
            code="database_unavailable", message="База данных временно недоступна."
        ))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logging.getLogger(__name__).error("Unhandled error: %s", type(exc).__name__)
        return error_response(500, Error(
            code="internal_error", message="Внутренняя ошибка приложения."
        ))


ERROR_RESPONSES = {
    status: {"model": ErrorResponse, "description": description}
    for status, description in {
        401: "Сессия отсутствует или истекла (после пакета 11)",
        403: "CSRF не прошёл проверку (после пакета 11)",
        404: "Ресурс отсутствует или принадлежит другому аккаунту",
        409: "Конфликт ревизии или обработчик занят",
        413: "Файл слишком большой",
        415: "Неподдерживаемый тип файла",
        422: "Некорректные поля запроса",
        500: "Внутренняя ошибка без технических подробностей",
        503: "Пакет/AI/база данных недоступны",
    }.items()
}
