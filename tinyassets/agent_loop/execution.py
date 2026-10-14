"""One task loop per execution owner; blocking persistence stays off that loop."""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import functools
import threading
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeVar

T = TypeVar("T")


async def off_loop(function: Callable[..., T], /, *args, **kwargs) -> T:
    """Drain an admitted synchronous operation before propagating cancellation.

    Cancelling ``to_thread`` cannot stop SQLite or a lock acquisition. Waiting for
    the worker keeps its commit/cleanup ordered before the turn releases ownership.
    The bounded shared executor is for short operations, never waiting model turns.
    """
    context = contextvars.copy_context()
    task = asyncio.get_running_loop().run_in_executor(
        None, context.run, functools.partial(function, *args, **kwargs),
    )
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                result = task.result()
                break
    if cancelled:
        raise asyncio.CancelledError
    return result


class ExecutionOwner:
    """Submit turns in the caller's context; no per-turn loop or thread."""

    def __init__(self):
        self._ready = threading.Event()
        self._turns = set()
        self._thread = threading.Thread(target=self._serve, name="agent-execution", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _serve(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.close()

    def submit(self, turn: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        # call_soon_threadsafe captures contextvars, including the bound box and
        # execution lease. run_coroutine_threadsafe creates the task in it.
        async def owned():
            task = asyncio.current_task()
            self._turns.add(task)
            try:
                return await turn
            finally:
                self._turns.discard(task)
        return asyncio.run_coroutine_threadsafe(owned(), self.loop)

    def run(self, turn: Coroutine[Any, Any, T]) -> T:
        if threading.current_thread() is self._thread:
            turn.close()
            raise RuntimeError("cannot synchronously wait on the execution loop")
        future = self.submit(turn)
        try:
            return future.result()
        except concurrent.futures.CancelledError as exc:
            raise asyncio.CancelledError from exc
        except BaseException:
            future.cancel()
            raise

    def close(self):
        """Drain all live turns before stopping the owner (also useful in tests)."""
        async def drain():
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.run(drain())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join()


_OWNER: ExecutionOwner | None = None
_OWNER_LOCK = threading.Lock()


def execution_owner() -> ExecutionOwner:
    global _OWNER
    with _OWNER_LOCK:
        if _OWNER is None:
            _OWNER = ExecutionOwner()
        return _OWNER


@asynccontextmanager
async def off_loop_context(manager):
    """Enter/exit a synchronous resource without orphaning it on cancellation.

    Only for contexts without thread affinity. Admission locks use their own
    task-aware async API instead.
    """
    entered = []

    def enter():
        value = manager.__enter__()
        entered.append(value)
        return value

    try:
        value = await off_loop(enter)
    except BaseException:
        if entered:
            await off_loop(manager.__exit__, None, None, None)
        raise
    try:
        yield value
    except BaseException as exc:
        if not await off_loop(manager.__exit__, type(exc), exc, exc.__traceback__):
            raise
    else:
        await off_loop(manager.__exit__, None, None, None)
