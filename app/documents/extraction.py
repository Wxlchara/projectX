"""Async, bounded extraction API independent of HTTP/session/SQLite scaffolding."""

import asyncio
import json
import os
import signal
import sys
import tempfile
import threading
from pathlib import Path

from .types import (DOCUMENT_TYPES, MAX_FILE_BYTES, MAX_PAGES, MAX_PIXELS,
                    DocumentType, ExtractionResult, QualityIssue)

_DOCUMENT_LOCK = threading.Lock()


class DocumentExtractor:
    def __init__(self, *, timeout_seconds: float = 60, temporary_root: Path | None = None,
                 max_pages: int = MAX_PAGES, max_pixels: int = MAX_PIXELS,
                 tessdata_dir: Path | None = None):
        if not 0 < timeout_seconds <= 60:
            raise ValueError("Document timeout must be in (0, 60].")
        self.timeout_seconds = timeout_seconds
        self.temporary_root = temporary_root
        self.tessdata_dir = tessdata_dir.resolve() if tessdata_dir else None
        self.limits = {"max_pages": min(max_pages, MAX_PAGES), "max_pixels": min(max_pixels, MAX_PIXELS)}
        if min(self.limits.values()) < 1:
            raise ValueError("Document limits must be positive.")

    async def extract(self, content: bytes, document_type: DocumentType) -> ExtractionResult:
        return await self._run(content, document_type, "extract")

    async def inspect(self, content: bytes, document_type: DocumentType) -> ExtractionResult:
        return await self._run(content, document_type, "inspect")

    async def _run(self, content: bytes, document_type: DocumentType, mode: str) -> ExtractionResult:
        if document_type not in DOCUMENT_TYPES:
            raise ValueError("Unsupported document_type.")
        if len(content) > MAX_FILE_BYTES:
            return self._failure(document_type, "file_too_large", "Максимальный размер файла — 10 MiB.")
        if not _DOCUMENT_LOCK.acquire(blocking=False):
            return self._failure(document_type, "processing_busy", "Другой документ уже обрабатывается.")
        try:
            with tempfile.TemporaryDirectory(prefix="visapilot-extract-", dir=self.temporary_root) as directory:
                # Input bytes travel via stdin; no user filenames or commands are accepted.
                environment = dict(os.environ)
                if self.tessdata_dir is not None:
                    environment["TESSDATA_PREFIX"] = str(self.tessdata_dir)
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "app.documents.worker", document_type,
                    directory, str(self.timeout_seconds), mode, json.dumps(self.limits),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
                    cwd=Path(__file__).resolve().parents[2],
                    env=environment,
                )
                try:
                    stdout, _ = await asyncio.wait_for(process.communicate(content), self.timeout_seconds)
                except (TimeoutError, asyncio.CancelledError) as exc:
                    self._kill(process)
                    await process.wait()
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    return self._failure(document_type, "processing_timeout", "Время обработки истекло; заполните поля вручную.")
                if process.returncode or len(stdout) > 256_000:
                    return self._failure(document_type, "processing_failed", "Обработка недоступна; заполните поля вручную.")
                return ExtractionResult.from_dict(json.loads(stdout))
        except (OSError, ValueError, KeyError, TypeError):
            return self._failure(document_type, "processing_failed", "Обработка недоступна; заполните поля вручную.")
        finally:
            _DOCUMENT_LOCK.release()

    @staticmethod
    def _kill(process: asyncio.subprocess.Process) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def _failure(document_type: DocumentType, code: str, message: str) -> ExtractionResult:
        state = "needs_input" if code in {"processing_busy", "processing_timeout"} else "failed"
        return ExtractionResult(document_type, state, quality_issues=(QualityIssue(code, message),))
