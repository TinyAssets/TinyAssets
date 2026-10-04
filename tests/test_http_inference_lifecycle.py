"""HTTP inference never blocks its loop or detaches a cancelled live request."""

import asyncio
import contextvars
import json
import threading
from types import SimpleNamespace

import pytest

from tests import test_api_key_http_provider as http_tests
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.inference_usage_helpers import accounting_resolver
from tinyassets.exceptions import ProviderProtocolError, ProviderRateLimitedError
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

base = http_tests.base


pytestmark = pytest.mark.usefixtures("cloud_runtime")


def response(status=200, body=None):
    return {
        "status": status,
        "body": json.dumps(
            {"choices": [{"message": {"content": "answer"}}]} if body is None else body,
        ),
    }


class Proxy:
    def __init__(self, result=None, failure=None, close_failure=None):
        self.result = response() if result is None else result
        self.failure = failure
        self.close_failure = close_failure
        self.calls = 0
        self.closed = 0

    def request(self, verb, wire):
        self.calls += 1
        if self.failure:
            raise self.failure
        return self.result

    def close(self):
        self.closed += 1
        if self.close_failure:
            raise self.close_failure


def provider_with_owned_proxy(base, monkeypatch, proxy):
    http_tests._seed(base)
    provider = ApiKeyHttpProvider(http_tests._definition())
    monkeypatch.setattr(provider, "_resolve_proxy", accounting_resolver(lambda **kwargs: proxy))
    return provider


def test_http_wait_does_not_block_another_task_and_copies_context(base, monkeypatch):
    progressed = threading.Event()
    request_context = contextvars.ContextVar("inference_test_context")
    loop_thread = threading.get_ident()

    class Waiting(Proxy):
        def request(self, verb, wire):
            assert threading.get_ident() != loop_thread
            assert request_context.get() == "request-owned"
            assert progressed.wait(2), "HTTP inference blocked the event loop"
            return super().request(verb, wire)

    proxy = Waiting()
    provider = provider_with_owned_proxy(base, monkeypatch, proxy)

    async def scenario():
        token = request_context.set("request-owned")

        async def unrelated():
            await asyncio.sleep(0)
            progressed.set()

        try:
            result, _ = await asyncio.gather(
                provider.complete("p", "s", http_tests._config(), universe_dir=base / "u-x"),
                unrelated(),
            )
            return result
        finally:
            request_context.reset(token)

    assert asyncio.run(scenario()).text == "answer"
    assert proxy.calls == 1 and proxy.closed == 1


@pytest.mark.parametrize("late_error", [False, True])
@pytest.mark.parametrize("repeat_cancel", [False, True])
def test_cancel_drains_original_request_and_never_returns_late_result_or_error(
    base,
    monkeypatch,
    late_error,
    repeat_cancel,
):
    started, release = threading.Event(), threading.Event()

    class Waiting(Proxy):
        def request(self, verb, wire):
            self.calls += 1
            started.set()
            assert release.wait(5), "test controller failed to release HTTP worker"
            if late_error:
                raise ProviderRateLimitedError("late provider limit")
            return self.result

    proxy = Waiting()
    provider = provider_with_owned_proxy(base, monkeypatch, proxy)

    async def scenario():
        task = asyncio.create_task(
            provider.complete(
                "p",
                "s",
                http_tests._config(),
                universe_dir=base / "u-x",
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done() and proxy.closed == 0
            if repeat_cancel:
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done() and proxy.closed == 0
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert proxy.calls == proxy.closed == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "result,error",
    [
        (response(), None),
        (response(429), ProviderRateLimitedError),
        (response(body={}), ProviderProtocolError),
    ],
)
def test_owned_proxy_closed_on_success_status_or_decode_error(base, monkeypatch, result, error):
    proxy = Proxy(result)
    provider = provider_with_owned_proxy(base, monkeypatch, proxy)
    if error is None:
        assert http_tests._run(provider, base / "u-x").text == "answer"
    else:
        with pytest.raises(error):
            http_tests._run(provider, base / "u-x")
    assert proxy.calls == proxy.closed == 1


def test_request_exception_still_closes_owned_proxy(base, monkeypatch):
    failure = PermissionError("exact grant refused")
    proxy = Proxy(failure=failure)
    provider = provider_with_owned_proxy(base, monkeypatch, proxy)
    with pytest.raises(PermissionError) as error:
        http_tests._run(provider, base / "u-x")
    assert error.value is failure
    assert proxy.calls == proxy.closed == 1


@pytest.mark.parametrize("status", [200, 429])
def test_cleanup_failure_cannot_erase_response_or_change_error(base, monkeypatch, caplog, status):
    proxy = Proxy(response(status), close_failure=RuntimeError("private / credential detail"))
    provider = provider_with_owned_proxy(base, monkeypatch, proxy)
    if status == 200:
        assert http_tests._run(provider, base / "u-x").text == "answer"
    else:
        with pytest.raises(ProviderRateLimitedError):
            http_tests._run(provider, base / "u-x")
    assert proxy.closed == 1
    assert "cleanup" in caplog.text.lower()
    assert "credential detail" not in caplog.text


def test_borrowed_proxy_lifecycle_stays_with_owner(base):
    http_tests._seed(base)
    proxy = Proxy()
    provider = ApiKeyHttpProvider(http_tests._definition(), proxy_override=proxy)
    assert http_tests._run(provider, base / "u-x").text == "answer"
    assert proxy.calls == 1 and proxy.closed == 0


def test_reported_latency_excludes_worker_cleanup(base, monkeypatch):
    from tinyassets.providers import api_key_http_provider as module

    clock = [10.0]

    class TimedProxy(Proxy):
        def request(self, verb, wire):
            clock[0] = 12.0
            return super().request(verb, wire)

        def close(self):
            clock[0] = 14.0
            super().close()

    proxy = TimedProxy()
    provider = provider_with_owned_proxy(base, monkeypatch, proxy)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    result = http_tests._run(provider, base / "u-x")
    assert result.latency_ms == 2000.0
    assert proxy.closed == 1


def test_asyncio_run_shutdown_does_not_release_while_executor_still_runs(base, monkeypatch):
    from tinyassets.providers import api_key_http_provider as module

    entered, release = threading.Event(), threading.Event()
    cancelled, checked, slot_released = threading.Event(), threading.Event(), threading.Event()
    loops, observations, errors = [], [], []
    real_shield = asyncio.shield

    def shield(future):
        protected = real_shield(future)
        protected.add_done_callback(lambda done: cancelled.set() if done.cancelled() else None)
        return protected

    class Waiting(Proxy):
        def request(self, verb, wire):
            entered.set()
            assert release.wait(5)
            return super().request(verb, wire)

    proxy = Waiting()
    provider = provider_with_owned_proxy(base, monkeypatch, proxy)
    monkeypatch.setattr(
        module,
        "asyncio",
        SimpleNamespace(
            get_running_loop=asyncio.get_running_loop,
            shield=shield,
            CancelledError=asyncio.CancelledError,
        ),
    )

    def observe_then_release():
        try:
            assert cancelled.wait(3)

            def snapshot():
                observations.append(slot_released.is_set())
                checked.set()

            loops[0].call_soon_threadsafe(snapshot)
            assert checked.wait(3)
        except BaseException as exc:
            errors.append(exc)
        finally:
            release.set()

    async def held_call():
        try:
            await provider.complete("p", "s", http_tests._config(), universe_dir=base / "u-x")
        finally:
            slot_released.set()

    async def main():
        loops.append(asyncio.get_running_loop())
        task = asyncio.create_task(held_call())
        assert await asyncio.to_thread(entered.wait, 3)
        assert not task.done()
        # Intentionally return with a live child: asyncio.run now cancels every
        # Task directly, not merely the task whose await was shielded.

    controller = threading.Thread(target=observe_then_release)
    controller.start()
    try:
        asyncio.run(main())
    finally:
        release.set()
        controller.join(timeout=5)
    assert not controller.is_alive() and not errors
    assert observations == [False]
    assert slot_released.is_set() and proxy.calls == proxy.closed == 1


def test_served_http_cancellation_holds_slot_and_budget_until_request_finishes(
    tmp_path,
    monkeypatch,
):
    from contextlib import asynccontextmanager

    from tests.test_provider_served_router import _fresh_served_request
    from tests.test_run_provider_session import _seed_open_serving_assignment
    from tinyassets.auth import middleware as auth
    from tinyassets.custom_agents import list_bindings
    from tinyassets.providers import router as routing
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    _seed_open_serving_assignment(
        tmp_path,
        monkeypatch,
        owner_user_id="owner-1",
        universe_id="u-owner",
    )
    serving = list_bindings(tmp_path, universe_id="u-owner")[0]
    capability, context = _fresh_served_request(
        tmp_path / "u-owner",
        serving,
        request_id="http-cancel",
    )
    entered, release = threading.Event(), threading.Event()
    slots = [0]

    @asynccontextmanager
    async def slot(**kwargs):
        slots[0] += 1
        try:
            yield
        finally:
            slots[0] -= 1

    class Waiting(Proxy):
        def request(self, verb, wire):
            entered.set()
            assert release.wait(5)
            return super().request(verb, wire)

    proxy = Waiting()
    monkeypatch.setattr(routing, "_provider_slot", slot)
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda *a, **k: proxy))

    def reservations():
        with SQLiteProviderWorkAuthorityStore(tmp_path).connection() as conn:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT state FROM served_provider_budget_reservations",
                )
            ]

    async def scenario():
        task = asyncio.create_task(
            routing.ProviderRouter({}).call(
                "writer",
                "hello",
                "system",
                operation="converse",
                universe_context=context,
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert reservations() == ["reserved"]
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done() and slots == [1]
                assert proxy.closed == 0 and reservations() == ["reserved"]
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert slots == [0] and proxy.calls == proxy.closed == 1
            assert reservations() == ["indeterminate"]
            assert auth._active_provider_request(capability)["invocations"] == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(scenario())
    finally:
        auth.revoke_provider_request(capability)
