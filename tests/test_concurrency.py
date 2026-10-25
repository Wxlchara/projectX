import asyncio
import threading

import pytest

from app.common.concurrency import WorkGate
from app.common.errors import AppError


def test_slow_work_does_not_block_loop_and_second_request_is_busy():
    async def scenario():
        gate = WorkGate()
        started = threading.Event()
        release = threading.Event()

        def slow_operation():
            started.set()
            assert release.wait(5)
            return "done"

        first = asyncio.create_task(gate.run(slow_operation))
        try:
            async with asyncio.timeout(2):
                while not started.is_set():
                    await asyncio.sleep(0.001)
            # This coroutine can run while the synchronous OCR-like work waits.
            with pytest.raises(AppError) as busy:
                await gate.run(lambda: "must not execute")
            assert busy.value.status_code == 409
            assert busy.value.error.code == "busy"
        finally:
            release.set()
        assert await first == "done"
        assert await gate.run(lambda: "again") == "again"

    asyncio.run(scenario())


def test_cancelled_request_keeps_gate_busy_until_worker_finishes():
    async def scenario():
        gate = WorkGate()
        started = threading.Event()
        release = threading.Event()

        def operation():
            started.set()
            assert release.wait(5)

        first = asyncio.create_task(gate.run(operation))
        try:
            async with asyncio.timeout(2):
                while not started.is_set():
                    await asyncio.sleep(0.001)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            with pytest.raises(AppError):
                await gate.run(lambda: "overlap")
        finally:
            release.set()
        async with asyncio.timeout(2):
            while True:
                try:
                    assert await gate.run(lambda: "next") == "next"
                    break
                except AppError:
                    await asyncio.sleep(0.001)

        def failure():
            raise ValueError("worker failed")

        with pytest.raises(ValueError):
            await gate.run(failure)
        assert await gate.run(lambda: "recovered") == "recovered"

    asyncio.run(scenario())
