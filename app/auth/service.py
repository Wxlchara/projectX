"""SQLite-backed auth. Password work runs in a worker, outside transactions."""

import hashlib
import hmac
import re
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Request
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool

from app.common.config import Settings
from app.common.db import Database
from app.common.errors import AppError
from app.common.models import Credentials, ProfileInput, User, UserContext

PASSWORDS = PasswordHash.recommended()
# Unknown users still run Argon2 verification; this is not an account password.
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
CSRF_PATTERN = re.compile(r"[a-f0-9]{64}")
COOKIE_PATH = "/api/v1"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_for_token(token: str) -> str:
    # A deterministic session-bound token allows /me after a process restart
    # without storing a raw session token or an extra server secret in SQLite.
    return hmac.new(token.encode("ascii"), b"VisaPilot CSRF v1", hashlib.sha256).hexdigest()


def origin_key(value: str) -> tuple[str, str, int] | None:
    if not value or any(ord(char) <= 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
        ):
            return None
        port = parsed.port
        return parsed.scheme, parsed.hostname.lower(), port if port is not None else (
            443 if parsed.scheme == "https" else 80
        )
    except ValueError:
        return None


@dataclass(frozen=True)
class AuthenticatedSession:
    user: User
    token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    csrf_token_hash: str = field(repr=False)
    expires_at: datetime


class AuthService:
    def __init__(
        self, database: Database, settings: Settings,
        *, clock: Callable[[], datetime] = utc_now,
    ):
        self.database = database
        self.settings = settings
        self._clock = clock
        origins = [origin_key(value) for value in settings.allowed_origins]
        if not origins or any(value is None for value in origins):
            raise ValueError("allowed_origins must contain HTTP(S) origins without paths")
        self._allowed_origins = frozenset(origins)

    def check_origin(self, request: Request, *, required: bool = True) -> None:
        origins = request.headers.getlist("origin")
        if not origins and not required:
            return
        if len(origins) != 1 or origin_key(origins[0]) not in self._allowed_origins:
            raise AppError(403, "origin_not_allowed", "Источник запроса не разрешён.")

    def register(self, credentials: Credentials) -> User:
        username = credentials.username.lower()
        password_hash = PASSWORDS.hash(credentials.password.get_secret_value())
        user = User(id=uuid4(), username=username, created_at=self._clock())
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (str(user.id), username, password_hash, user.created_at.isoformat()),
                )
                # Account lifecycle only; profile CRUD belongs to package 3.
                connection.execute(
                    "INSERT INTO profiles (id, owner_id, revision, data_json, updated_at) "
                    "VALUES (?, ?, 1, ?, ?)",
                    (str(uuid4()), str(user.id), ProfileInput().model_dump_json(), user.created_at.isoformat()),
                )
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                raise AppError(409, "username_taken", "Это имя пользователя уже занято.") from None
            raise
        return user

    def login(self, credentials: Credentials, previous_token: str | None) -> AuthenticatedSession:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT id, username, password_hash, created_at FROM users WHERE username = ?",
                (credentials.username.lower(),),
            ).fetchone()
        password_hash = row["password_hash"] if row else DUMMY_HASH
        verified = PASSWORDS.verify(credentials.password.get_secret_value(), password_hash)
        if not row or not verified:
            raise AppError(401, "invalid_credentials", "Неверное имя пользователя или пароль.")
        user = User(id=row["id"], username=row["username"], created_at=row["created_at"])
        now = self._clock()
        token = secrets.token_urlsafe(32)
        csrf = csrf_for_token(token)
        session = AuthenticatedSession(
            user=user, token=token, csrf_token=csrf, csrf_token_hash=digest(csrf),
            expires_at=now + timedelta(seconds=self.settings.session_ttl_seconds),
        )
        with self.database.transaction() as connection:
            # Successful login always rotates the cookie, even for the same
            # user. A previous session never substitutes for credentials.
            if previous_token and TOKEN_PATTERN.fullmatch(previous_token):
                connection.execute(
                    "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                    (now.isoformat(), digest(previous_token)),
                )
            connection.execute(
                "INSERT INTO sessions (token_hash, user_id, csrf_token_hash, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (digest(token), str(user.id), session.csrf_token_hash, now.isoformat(), session.expires_at.isoformat()),
            )
        return session

    def _session_for_token(self, token: str | None) -> AuthenticatedSession:
        if not token or not TOKEN_PATTERN.fullmatch(token):
            raise AppError(401, "unauthenticated", "Войдите в аккаунт.")
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT u.id, u.username, u.created_at, s.csrf_token_hash, s.expires_at "
                "FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.revoked_at IS NULL",
                (digest(token),),
            ).fetchone()
        if row is None:
            raise AppError(401, "unauthenticated", "Сессия отсутствует или истекла.")
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at.tzinfo is None or expires_at <= self._clock():
            raise AppError(401, "unauthenticated", "Сессия отсутствует или истекла.")
        return AuthenticatedSession(
            user=User(id=row["id"], username=row["username"], created_at=row["created_at"]),
            token=token, csrf_token=csrf_for_token(token),
            csrf_token_hash=row["csrf_token_hash"], expires_at=expires_at,
        )

    async def current_session(self, request: Request) -> AuthenticatedSession:
        cached = getattr(request.state, "auth_session", None)
        if cached is None:
            cached = await run_in_threadpool(
                self._session_for_token, request.cookies.get(self.settings.session_cookie_name)
            )
            request.state.auth_session = cached
        return cached

    async def require_user(self, request: Request) -> UserContext:
        session = await self.current_session(request)
        return UserContext(user_id=session.user.id)

    async def require_csrf(self, request: Request) -> UserContext:
        session = await self.current_session(request)
        self.check_origin(request, required=False)
        headers = request.headers.getlist("x-csrf-token")
        if (
            len(headers) != 1 or not CSRF_PATTERN.fullmatch(headers[0])
            or not secrets.compare_digest(digest(headers[0]), session.csrf_token_hash)
        ):
            raise AppError(403, "csrf_failed", "CSRF-токен отсутствует или неверен.")
        return UserContext(user_id=session.user.id)

    def revoke(self, token: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (self._clock().isoformat(), digest(token)),
            )
