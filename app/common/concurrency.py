"""Shared non-blocking admission gates; work runs outside the HTTP event loop."""

import asyncio
from collections.abc import Callable
from threading import Lock
from typing import TypeVar

from app.common.errors import AppError

T = TypeVar("T")


class WorkGate:
    def __init__(self):
        self._lock = Lock()

    async def run(self, operation: Callable[[], T]) -> T:
        if not self._lock.acquire(blocking=False):
            raise AppError(409, "busy", "Обработчик занят. Повторите запрос позже.")

        def run_and_release():
            try:
                return operation()
            finally:
                # The worker owns release: a disconnected HTTP client must not
                # admit a second operation while this thread is still running.
                self._lock.release()

        try:
            future = asyncio.get_running_loop().run_in_executor(None, run_and_release)
        except BaseException:
            self._lock.release()
            raise
        # Retrieve a worker failure even if the HTTP request was cancelled.
        future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(future)


class ProcessingGuards:
    def __init__(self):
        self.documents = WorkGate()
        self.ai = WorkGate()
        self.pdfium_mutex = Lock()
