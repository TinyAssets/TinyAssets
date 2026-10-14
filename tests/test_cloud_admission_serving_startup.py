"""Serving-startup and origin-ingress admission.

OpenSpec change ``cloud-only-runtime-admission``, tasks 7 (origin backstop) and
8 (startup). Written against the tree **before** those guards exist, so every
negative here fails at the missing-refusal assertion on the unfixed tree and
passes once admission is wired. The positives assert preserved behaviour and
pass at baseline — which is what makes the negatives refusals rather than a
broken harness.

What each case defends:

* serving boot refuses NOT_CLOUD, an unobserved process and a *failed*
  resolution, with a non-zero exit — and reaches no maintenance, thread, worker,
  provider child, listener or storage writer on the way out;
* the session seal is still armed FIRST, before admission resolves anything;
* ``create_streamable_http_app`` is admitted in its own right, because it can be
  launched without ``main()`` (uvicorn factory, an embedding host, a test
  client), and admission precedes the writer barrier, storage initialization
  and the scheduler;
* an origin request reads only the **cached** verdict: a request never resolves
  provenance, and unknown/not-cloud never reaches a request handler;
* an admitted process serves exactly what it served before — same principal,
  same canary-only diagnostic, no anonymous release-state read.

Fixture-only. No real metadata service, no network, no provider child and no
production authority: every verdict here is an injected
``ProcessProvenanceObservation``, never an environment flag, and every
downstream boot action is replaced by a sentinel. A green run establishes no
cloud fact and no custody.
"""

from __future__ import annotations

import pytest

from tests.test_cloud_only_admission_regressions import (
    COPIED_LABEL_VARIANTS,
    apply_copied_cloud_labels,
    maybe_copied_cloud_labels,
)
from tinyassets import platform_runtime_provenance as prov
from tinyassets import universe_server as us
from tinyassets.auth import middleware as mw
from tinyassets.auth.provider import DEV_USER_ENV, DevAuthProvider

_CANARY_TOKEN = "s" * 40

ADMITTED = prov.RuntimeProvenance(
    verdict=prov.CLOUD,
    reason="instance_match",
    metadata_reachable=True,
    expected_identity_prepared=True,
)
UNADMITTED = prov.RuntimeProvenance(
    verdict=prov.NOT_CLOUD,
    reason="instance_mismatch",
    metadata_reachable=True,
    expected_identity_prepared=True,
)


def _forbidden_resolver() -> prov.RuntimeProvenance:
    raise AssertionError("this path must never resolve provenance")


def _install(monkeypatch, observation: prov.ProcessProvenanceObservation):
    monkeypatch.setattr(prov, "_PROCESS_OBSERVATION", observation)
    return observation


@pytest.fixture
def cached(monkeypatch):
    """Install an already-resolved process verdict; resolving again is a bug."""

    def _install_cached(verdict: prov.RuntimeProvenance):
        observation = prov.ProcessProvenanceObservation(resolver=lambda: verdict)
        observation.observe()
        observation._resolver = _forbidden_resolver  # noqa: SLF001 - pin: no re-resolve
        return _install(monkeypatch, observation)

    return _install_cached


@pytest.fixture
def unresolved(monkeypatch):
    """Install an *unobserved* process: nothing has looked yet."""

    def _install_unresolved(resolver):
        return _install(monkeypatch, prov.ProcessProvenanceObservation(resolver=resolver))

    return _install_unresolved


# --------------------------------------------------------------------------
# serving startup: main()
# --------------------------------------------------------------------------


@pytest.fixture
def boot_sentinels(monkeypatch, tmp_path):
    """Replace every downstream boot action with a recorder.

    Nothing here starts a real service. The maintenance sentinel raises after
    recording so the rest of that best-effort block cannot open a database;
    ``main`` already swallows failures there, so the raise only bounds the test.
    """
    from tinyassets import engine_mcp_http, provider_assignment
    from tinyassets.broker import supervisor
    from tinyassets.control_plane import tick as assigned_queue_consumer
    from tinyassets.onboarding import session_store

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    reached: list[str] = []

    real_arm = session_store.arm

    def _arm():
        reached.append("seal-arm")
        return real_arm()

    def _maintenance(*_a, **_k):
        reached.append("boot-maintenance")
        raise RuntimeError("sentinel: boot maintenance must not run unadmitted")

    monkeypatch.setattr(session_store, "arm", _arm)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda *_a, **_k: object())
    monkeypatch.setattr(
        provider_assignment, "reconcile_orphaned_reservations_on_boot", _maintenance
    )
    monkeypatch.setattr(
        engine_mcp_http,
        "start_engine_mcp_http_servers",
        lambda *a, **k: (reached.append("engine-mcp-children"), [])[1],
    )
    monkeypatch.setattr(
        us,
        "create_streamable_http_app",
        lambda *a, **k: (reached.append("app-build"), object())[1],
    )
    class _Consumer:
        def __init__(self, *_a, **_k):
            reached.append("queue-worker-create")

        def start(self):
            reached.append("queue-worker-start")

        def stop(self):
            reached.append("queue-worker-stop")

    monkeypatch.setattr(assigned_queue_consumer, "AssignedQueueConsumer", _Consumer)
    monkeypatch.setattr(
        us.uvicorn, "run", lambda *a, **k: reached.append("listener"),
    )
    return reached


def _boot_refusal_cases():
    return [
        pytest.param(lambda: UNADMITTED, id="not_cloud"),
        pytest.param(_failing_resolver, id="resolution_failed"),
    ]


def _failing_resolver() -> prov.RuntimeProvenance:
    raise OSError("metadata read blew up")


@pytest.mark.parametrize("resolver", _boot_refusal_cases())
def test_serving_boot_refuses_unadmitted_with_a_nonzero_exit(
    resolver, unresolved, boot_sentinels
):
    unresolved(resolver)

    with pytest.raises(SystemExit) as excinfo:
        us.main(transport="streamable-http")

    code = excinfo.value.code
    assert isinstance(code, int) and code != 0, f"boot must exit non-zero, got {code!r}"
    assert boot_sentinels == ["seal-arm"], (
        "unadmitted boot reached a downstream action: " f"{boot_sentinels}"
    )


@pytest.mark.parametrize("transport", ["streamable-http", "stdio", "sse"])
def test_every_transport_refuses_unadmitted_before_startup(
    unresolved, boot_sentinels, transport,
):
    """No transport may reach downstream boot actions without admission."""
    calls: list[int] = []

    def _resolver():
        calls.append(1)
        return UNADMITTED

    unresolved(_resolver)
    with pytest.raises(SystemExit) as excinfo:
        us.main(transport=transport)

    assert calls == [1], "boot must resolve exactly once"
    assert excinfo.value.code == us.PLATFORM_NOT_CLOUD_EXIT_CODE
    assert boot_sentinels == ["seal-arm"]


def test_the_seal_is_armed_before_admission_resolves_anything(
    unresolved, boot_sentinels
):
    """Seal-first ordering is preserved: admission must not move ahead of it.

    The resolver records into the same list the seal sentinel writes, so the
    relative order is observed at runtime rather than read out of the source.
    """

    def _resolver():
        boot_sentinels.append("resolve")
        return UNADMITTED

    unresolved(_resolver)

    with pytest.raises(SystemExit):
        us.main(transport="streamable-http")

    assert boot_sentinels == ["seal-arm", "resolve"], boot_sentinels


@pytest.mark.parametrize("copied_labels", COPIED_LABEL_VARIANTS)
def test_admitted_boot_reaches_the_listener(
    copied_labels, cached, boot_sentinels, monkeypatch, tmp_path
):
    """Preserved behaviour, and the positive control for the row-5 boot case.

    The `copied_labels` leg dresses an *admitted* boot in the same copied
    container config as
    `test_boot_refuses_a_process_wearing_copied_cloud_labels`. That negative
    asserts `boot_sentinels == ["seal-arm"]`, which is exactly what a boot
    broken by the label set would also produce; this leg is what separates the
    two. `boot_sentinels` already points `TINYASSETS_DATA_DIR` at `tmp_path`
    and the label set re-roots it to the same place, so nothing moves.
    """
    cached(ADMITTED)
    maybe_copied_cloud_labels(copied_labels, monkeypatch, tmp_path)

    us.main(transport="streamable-http")

    assert boot_sentinels[0] == "seal-arm"
    assert "listener" in boot_sentinels, boot_sentinels
    assert "app-build" in boot_sentinels


# --------------------------------------------------------------------------
# serving startup: the HTTP app's own lifespan (main may never have run)
# --------------------------------------------------------------------------


@pytest.fixture
def lifespan_sentinels(monkeypatch, tmp_path):
    """Sentinels for everything the serving lifespan does after admission."""
    from tinyassets import consumer_runtime, scoped_reset
    from tinyassets.api import visibility

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    reached: list[str] = []

    class _Barrier:
        def release(self) -> None:
            reached.append("writer-barrier-release")

    monkeypatch.setattr(
        scoped_reset,
        "prepare_service_writer_barrier",
        lambda *a, **k: (reached.append("writer-barrier"), _Barrier())[1],
    )
    monkeypatch.setattr(
        consumer_runtime,
        "initialize",
        lambda *a, **k: reached.append("storage-initialize"),
    )
    monkeypatch.setattr(
        us, "start_scheduler_for_serving", lambda: (reached.append("scheduler"), True)[1]
    )
    monkeypatch.setattr(us, "stop_scheduler_for_serving", lambda: None)
    monkeypatch.setattr(
        visibility, "run_visibility_startup_gate", lambda *a, **k: reached.append("visibility")
    )
    return reached


def _enter_lifespan():
    from starlette.testclient import TestClient

    with TestClient(us.create_streamable_http_app()):
        pass


def test_lifespan_entered_without_main_refuses_unadmitted(
    unresolved, lifespan_sentinels
):
    """The app is constructible without `main`; the lifespan admits in its own right."""
    unresolved(lambda: UNADMITTED)

    with pytest.raises(PermissionError) as excinfo:
        _enter_lifespan()

    assert prov.PLATFORM_NOT_CLOUD_REASON in str(excinfo.value)
    assert lifespan_sentinels == [], (
        "serving lifespan reached a downstream action unadmitted: "
        f"{lifespan_sentinels}"
    )


def test_lifespan_refuses_before_the_writer_barrier_and_storage(
    cached, lifespan_sentinels
):
    cached(UNADMITTED)

    with pytest.raises(PermissionError):
        _enter_lifespan()

    assert "writer-barrier" not in lifespan_sentinels
    assert "storage-initialize" not in lifespan_sentinels
    assert "scheduler" not in lifespan_sentinels


def test_admitted_lifespan_reaches_initialization_in_order(cached, lifespan_sentinels):
    cached(ADMITTED)

    _enter_lifespan()

    assert lifespan_sentinels[:4] == [
        "writer-barrier",
        "storage-initialize",
        "scheduler",
        "visibility",
    ], lifespan_sentinels


def test_lifespan_admission_opens_no_database_transaction_first(
    unresolved, lifespan_sentinels
):
    """Resolution happens before the writer barrier, so no socket read can ever
    sit under the SQLite write lock."""
    seen: list[str] = []

    def _resolver():
        seen.append(f"resolve-after-{lifespan_sentinels}")
        return ADMITTED

    unresolved(_resolver)

    _enter_lifespan()

    assert seen == ["resolve-after-[]"], seen


# --------------------------------------------------------------------------
# origin ingress backstop
# --------------------------------------------------------------------------


@pytest.fixture
def origin_client(monkeypatch, tmp_path):
    """Build the real HTTP app WITHOUT entering its lifespan.

    Skipping the lifespan is the point: it isolates the origin backstop from
    startup admission, so these cases measure the request path only.
    """
    from starlette.testclient import TestClient

    mw.set_provider(DevAuthProvider(user_id="dev-tests"))
    mw.auth_middleware(None)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(DEV_USER_ENV, "operator-app")
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _CANARY_TOKEN)
    try:
        yield TestClient(us.create_streamable_http_app())
    finally:
        mw.set_provider(DevAuthProvider(user_id="dev-tests"))
        mw.auth_middleware(None)


def _canary_pulse(client):
    return client.get("/mcp/pulse", headers={"Authorization": f"Bearer {_CANARY_TOKEN}"})


def test_an_unobserved_origin_refuses_every_request(origin_client, unresolved):
    unresolved(_forbidden_resolver)

    response = _canary_pulse(origin_client)

    assert response.status_code == 503, response.status_code
    assert prov.PLATFORM_NOT_CLOUD_REASON in response.text
    assert "git_sha" not in response.text


def test_a_not_cloud_origin_refuses_every_request(origin_client, cached):
    cached(UNADMITTED)

    response = _canary_pulse(origin_client)

    assert response.status_code == 503
    assert prov.PLATFORM_NOT_CLOUD_REASON in response.text


def test_an_origin_request_never_triggers_resolution(origin_client, unresolved):
    """No health GET, no probe and no request may become the resolver's trigger."""
    observation = unresolved(_forbidden_resolver)

    response = origin_client.get("/mcp/pulse")

    assert response.status_code == 503
    assert observation.resolved is False, "a request resolved provenance"


def test_the_refusal_body_carries_no_identifier(origin_client, cached):
    cached(UNADMITTED)

    body = _canary_pulse(origin_client).text

    for leaked in ("instance", "169.254", "expected", _CANARY_TOKEN, "tinyassets.io"):
        assert leaked not in body, leaked


def test_an_admitted_origin_still_serves_the_canary_diagnostic(origin_client, cached):
    cached(ADMITTED)

    response = _canary_pulse(origin_client)

    assert response.status_code == 200
    payload = response.json()
    assert "git_sha" in payload
    assert payload["platform_runtime_provenance"]["verdict"] == prov.CLOUD


def test_an_admitted_origin_grants_no_anonymous_release_read(origin_client, cached):
    """Admission adds a condition; it must not widen the existing auth boundary."""
    cached(ADMITTED)

    payload = origin_client.get("/mcp/pulse").json()

    assert "platform_runtime_provenance" not in payload, (
        "the canary-only diagnostic leaked to an unauthenticated caller"
    )


# --------------------------------------------------------------------------
# matrix 5 at the startup site
# --------------------------------------------------------------------------


def test_boot_refuses_a_process_wearing_copied_cloud_labels(
    monkeypatch, tmp_path, unresolved, boot_sentinels
):
    """Matrix row 5, startup half.

    Same unadmitted boot as `test_serving_boot_refuses_unadmitted_with_a_
    nonzero_exit`, except the process now wears the full copied-container
    config: every compose env var, `mcp.tinyassets.io` as hostname and
    `socket.gethostname`, and the service name. `boot_sentinels` already
    pointed `TINYASSETS_DATA_DIR` at the temp root; the label set re-roots the
    same variable to the same temp root, which is the honest local stand-in for
    the container's `/data` (see `apply_copied_cloud_labels`). The boot must
    still exit non-zero having reached nothing past the seal.
    """
    apply_copied_cloud_labels(monkeypatch, tmp_path)
    unresolved(lambda: UNADMITTED)

    with pytest.raises(SystemExit) as excinfo:
        us.main(transport="streamable-http")

    code = excinfo.value.code
    assert isinstance(code, int) and code != 0, f"boot must exit non-zero, got {code!r}"
    assert boot_sentinels == ["seal-arm"], (
        f"a boot wearing copied cloud labels reached: {boot_sentinels}"
    )
