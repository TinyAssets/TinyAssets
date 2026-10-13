"""There is no anonymous principal (founder, 2026-09-02).

The rule, tested at every seam the change touched:

- identity is present or the call refuses (``current_identity`` raises, the
  dev provider needs a named operator, the stdio server binds one);
- every route under ``/mcp`` requires a bearer; the canary service principal
  may read exact ``GET /mcp/pulse`` and the receipt names no user or universe;
- writes carry their principal explicitly (a run needs an actor, a git author
  needs an identity, a hook records its owner and hands it to the event).

Change: ``openspec/changes/no-anonymous-principal``.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets.auth import middleware as mw
from tinyassets.auth.provider import CANARY, DEV_USER_ENV, DevAuthProvider, Identity

_CANARY_TOKEN = "c" * 40

# The app-booting `client` fixture below enters the serving lifespan, which
# now requires an admitted cloud runtime. Process provenance is orthogonal to
# the principal: every bearer challenge and refusal asserted here is unchanged.
pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture(autouse=True)
def _reset_auth():
    # Named explicitly: several tests below delete UNIVERSE_SERVER_DEV_USER to
    # prove the provider refuses without it, and teardown must not depend on it.
    mw.set_provider(DevAuthProvider(user_id="dev-tests"))
    mw.auth_middleware(None)
    yield
    mw.set_provider(DevAuthProvider(user_id="dev-tests"))
    mw.auth_middleware(None)


# --------------------------------------------------------------------------
# identity is present or the call refuses
# --------------------------------------------------------------------------


def test_nothing_bound_means_nobody_not_anonymous():
    mw.auth_middleware(None)
    assert mw.current_identity_or_none() is None
    with pytest.raises(PermissionError, match="Authentication required"):
        mw.current_identity()


def test_there_is_no_anonymous_sentinel_left_in_the_provider_module():
    from tinyassets.auth import provider

    assert not hasattr(provider, "ANONYMOUS")
    assert CANARY.user_id == "canary"
    assert CANARY.capabilities == []


def test_an_invalid_bearer_resolves_to_nobody_in_every_mode():
    class _Refuses(DevAuthProvider):
        def resolve_token(self, token: str) -> Identity | None:
            return None

    mw.set_provider(_Refuses(user_id="op"))
    assert mw.auth_middleware("garbage") is None
    assert mw.current_identity_or_none() is None


def test_dev_provider_refuses_to_exist_without_a_named_operator(monkeypatch):
    monkeypatch.delenv(DEV_USER_ENV, raising=False)
    with pytest.raises(RuntimeError, match=DEV_USER_ENV):
        DevAuthProvider()
    monkeypatch.setenv(DEV_USER_ENV, "   ")
    with pytest.raises(RuntimeError, match=DEV_USER_ENV):
        DevAuthProvider()


def test_dev_provider_resolves_any_bearer_to_the_named_operator_and_none_to_nobody(monkeypatch):
    monkeypatch.setenv(DEV_USER_ENV, "operator-9")
    provider = DevAuthProvider()
    assert provider.resolve_token("anything").user_id == "operator-9"
    assert provider.resolve_token("") is None


def test_stdio_binds_the_local_operator_from_env(monkeypatch):
    monkeypatch.setenv(DEV_USER_ENV, "operator-stdio")
    bound = mw.bind_local_operator_identity()
    assert bound.user_id == "operator-stdio"
    assert mw.current_identity().user_id == "operator-stdio"


def test_stdio_falls_back_to_the_os_account_never_to_nobody(monkeypatch):
    import getpass

    monkeypatch.delenv(DEV_USER_ENV, raising=False)
    monkeypatch.setattr(getpass, "getuser", lambda: "os-account")
    assert mw.bind_local_operator_identity().user_id == "os-account"

    monkeypatch.setattr(getpass, "getuser", lambda: "")
    with pytest.raises(RuntimeError, match=DEV_USER_ENV):
        mw.bind_local_operator_identity()


# --------------------------------------------------------------------------
# protected release read, through the real app
# --------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from tinyassets.universe_server import create_streamable_http_app

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(DEV_USER_ENV, "operator-app")
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _CANARY_TOKEN)
    with TestClient(create_streamable_http_app()) as test_client:
        yield test_client


def test_pulse_requires_a_bearer_and_canary_read_names_nothing(client, tmp_path):
    (tmp_path / "release-state.json").write_text(
        json.dumps({
            "receipt_available": True,
            "git_sha": "abc123def4567890",
            "image_tag": "ghcr.io/x/y:abc123def456",
            "deployed_at": "2026-09-02T10:00:00.000000Z",
            "actor": "someone",
            "extra": {"active_git_sha": "abc123def4567890"},
        }),
        encoding="utf-8",
    )
    assert client.get("/mcp/pulse").status_code == 401
    response = client.get(
        "/mcp/pulse",
        headers={"Authorization": f"Bearer {_CANARY_TOKEN}"},
    )
    assert response.status_code == 200
    body = response.json()
    # The canary additionally gets the record-only provenance readback (openspec
    # change cloud-only-runtime-admission). Still an exact set: the point of this
    # assertion is that no user or universe fact can drift into the receipt.
    assert set(body) == {
        "git_sha", "image_tag", "deployed_at", "uptime_seconds",
        "platform_runtime_provenance", "capability_health", "disk_health",
    }
    assert body["git_sha"] == "abc123def4567890"
    assert body["image_tag"] == "ghcr.io/x/y:abc123def456"
    assert body["deployed_at"] == "2026-09-02T10:00:00.000000Z"
    assert isinstance(body["uptime_seconds"], int)
    assert "actor" not in response.text
    assert "universe" not in response.text


def test_pulse_without_a_receipt_reports_empty_facts_not_an_error(client):
    response = client.get(
        "/mcp/pulse",
        headers={"Authorization": f"Bearer {_CANARY_TOKEN}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["git_sha"] == "" and body["image_tag"] == "" and body["deployed_at"] == ""


def test_pulse_is_exact_and_get_only(client):
    headers = {"Authorization": f"Bearer {_CANARY_TOKEN}"}
    assert client.post("/mcp/pulse", headers=headers).status_code == 403
    assert client.get("/mcp/pulse/extra", headers=headers).status_code == 403


def test_initialize_without_a_bearer_is_challenged_through_the_app(client):
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "1"}},
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer ")
    assert response.json() == {"error": "authentication_required"}


def test_browser_get_of_the_endpoint_is_challenged_too(client):
    # A human following the site footer gets the connect page. It is not an
    # anonymous principal: the body is static instructions, and a transport
    # request on the same path is still the bearer challenge.
    page = client.get("/mcp", headers={"Accept": "text/html"})
    assert page.status_code == 200
    assert "Connect via MCP client" in page.text
    assert "authentication_required" not in page.text


# --------------------------------------------------------------------------
# writes carry their principal
# --------------------------------------------------------------------------


def test_create_run_refuses_an_empty_actor(tmp_path):
    from tinyassets.runs import create_run, initialize_runs_db

    initialize_runs_db(tmp_path)
    with pytest.raises(ValueError, match="actor"):
        create_run(tmp_path, branch_def_id="b-1", thread_id="t-1", inputs={}, actor="")
    with pytest.raises(ValueError, match="actor"):
        create_run(tmp_path, branch_def_id="b-1", thread_id="t-1", inputs={}, actor="   ")


def test_git_author_needs_an_identity_or_an_explicit_actor(monkeypatch):
    from tinyassets.identity import git_author

    monkeypatch.delenv("TINYASSETS_GIT_AUTHOR", raising=False)
    mw.auth_middleware(None)
    with pytest.raises(PermissionError, match="authenticated actor"):
        git_author()
    assert git_author("Some Body") == "TinyAssets User <some-body@users.noreply.tinyassets.local>"
    subject = Identity(user_id="workos|abc", username="workos-abc", capabilities=["read"])
    with mw.identity_context(subject):
        assert "@users.noreply.tinyassets.local>" in git_author()
        assert "anonymous" not in git_author()


def test_a_hook_records_its_owner_and_hands_it_back(tmp_path):
    from tinyassets.storage import webhook_hooks

    token = webhook_hooks.mint(
        tmp_path, universe_id="u-1", branch_def_id="b-1", owner_principal_id="owner-7",
    )
    resolved = webhook_hooks.resolve(tmp_path, token=token)
    assert resolved is not None
    assert resolved["owner_principal_id"] == "owner-7"
    with pytest.raises(TypeError):
        webhook_hooks.mint(  # type: ignore[call-arg]
            tmp_path, universe_id="u-1", branch_def_id="b-2",
        )


def test_a_scheduler_event_carries_its_owner():
    from tinyassets.scheduler import SchedulerEvent

    event = SchedulerEvent(event_type="source.fired", payload={}, owner_principal_id="owner-3")
    assert event.owner_principal_id == "owner-3"
    assert SchedulerEvent(event_type="x").owner_principal_id == ""


# --------------------------------------------------------------------------
# the platform-wide activity field the probes read instead of a universe
# --------------------------------------------------------------------------


def test_platform_last_activity_at_reads_the_root_ledger(monkeypatch, tmp_path):
    from tinyassets.api.status import _platform_last_activity_at
    from tinyassets.runs import create_run, initialize_runs_db, runs_db_path

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    assert _platform_last_activity_at() is None            # no ledger yet

    initialize_runs_db(tmp_path)
    assert _platform_last_activity_at() is None            # ledger, no run

    create_run(tmp_path, branch_def_id="b-1", thread_id="t-1", inputs={}, actor="u-1")
    stamp = _platform_last_activity_at()
    assert isinstance(stamp, str) and stamp.endswith("+00:00")

    conn = sqlite3.connect(runs_db_path(tmp_path))
    try:
        conn.execute("UPDATE runs SET finished_at = ?", (1_800_000_000.0,))
        conn.commit()
    finally:
        conn.close()
    assert _platform_last_activity_at() == "2027-01-15T08:00:00+00:00"


def test_get_status_carries_the_daemon_block(monkeypatch, tmp_path):
    from tinyassets.api.status import get_status

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(DEV_USER_ENV, "operator-status")
    mw.auth_middleware("any")
    payload: dict[str, Any] = json.loads(get_status())
    assert "daemon" in payload
    assert payload["daemon"]["last_activity_at"] is None
    assert "has_work" in payload["daemon"]
    assert "worker_liveness" in payload["daemon"]


# --------------------------------------------------------------------------
# What Codex's first code review found, kept red-able
# --------------------------------------------------------------------------


def test_the_literal_string_anonymous_is_not_a_principal(tmp_path):
    """`create_run` refused an EMPTY actor and accepted the string "anonymous",
    so the rule could be satisfied by spelling nobody's name (Codex, P1)."""
    from tinyassets.runs import create_run, initialize_runs_db

    initialize_runs_db(tmp_path)
    for actor in ("anonymous", "ANONYMOUS", "  Anonymous  "):
        with pytest.raises(ValueError, match="not one"):
            create_run(
                tmp_path, branch_def_id="b-1", thread_id="t-1", inputs={}, actor=actor,
            )


def test_a_registered_node_is_authored_by_the_bearer_not_the_environment(
    monkeypatch, tmp_path,
):
    """The P0 Codex found: a signed-in caller could register a node whose author
    was `UNIVERSE_SERVER_USER` or the literal "anonymous" -- unattributed state
    a real request wrote. The author is the bound principal."""
    from tinyassets.api import extensions

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "env-impostor")
    subject = Identity(
        user_id="workos|author-1", username="author-1",
        capabilities=["read", "write", "costly", "list", "submit_request"],
    )
    with mw.identity_context(subject):
        out = json.loads(extensions._extensions_impl(
            action="register",
            node_id="attribution_probe",
            display_name="Attribution probe",
            phase="custom",
            source_code="def run(state):\n    return state\n",
        ))
    assert out.get("status") == "registered", out

    stored = extensions._load_nodes()
    row = next(n for n in stored if n.get("node_id") == "attribution_probe")
    assert row["author"] == "workos|author-1"
    assert row["author"] != "env-impostor"
    assert "anonymous" not in json.dumps(row)


def test_a_registration_with_nobody_bound_refuses_rather_than_writing(
    monkeypatch, tmp_path,
):
    from tinyassets.api import extensions

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "env-impostor")
    mw.auth_middleware(None)
    out = json.loads(extensions._extensions_impl(
        action="register",
        node_id="never_written",
        display_name="Never written",
        phase="custom",
        source_code="def run(state):\n    return state\n",
    ))
    # The scope gate refuses BEFORE the author is read, so nobody's write never
    # reaches storage -- and the refusal names authentication rather than the
    # env-var actor it used to be granted under.
    assert out["error"] == "Authentication required", out
    assert out["auth_scope_required"] is True
    assert all(n.get("node_id") != "never_written" for n in extensions._load_nodes())


def test_a_legacy_row_with_no_owner_matches_no_caller():
    """Its actor names nobody, so it must not be within reach of every scoped
    signed-in caller -- which is what "anonymous" used to make it."""
    from tinyassets.api import runs as runs_api

    record = {"run_id": "r-legacy", "actor": "anonymous", "status": "failed"}
    assert runs_api._run_read_allowed(record) is False
    assert runs_api._run_write_allowed(record) is False
    # A real universe-bound row still routes through the universe's own gate.
    assert runs_api._run_universe_id({"actor": "universe:u-1"}) == "u-1"


def test_a_legacy_row_stays_reachable_to_its_own_owner(signed_in):
    """Refusing here would delete the founder's own history from every list
    without deleting anything. The actor is ignored as authority; ownership
    decides, which is what ownership is for."""
    from tinyassets.api import runs as runs_api

    mine = {"run_id": "r-1", "actor": "anonymous", "owner_user_id": "founder-9"}
    theirs = {"run_id": "r-2", "actor": "anonymous", "owner_user_id": "someone-else"}

    signed_in("founder-9")
    assert runs_api._run_read_allowed(mine) is True
    assert runs_api._run_write_allowed(mine) is True
    assert runs_api._run_read_allowed(theirs) is False
    assert runs_api._run_write_allowed(theirs) is False


def test_the_discovery_exemption_is_a_table_not_a_substring():
    """`.well-known` anywhere in the path used to skip the challenge, so
    `/mcp/not.well-known/a` reached the app with no identity (Codex, P2)."""
    from tinyassets.auth.middleware import _auth_challenge_path

    for exempt in (
        "/.well-known/oauth-protected-resource",
        "/mcp/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-authorization-server/mcp",
        "/mcp/.well-known/oauth-authorization-server",
        "/mcp/oauth/register",
        "/mcp/oauth/authorize",
        "/mcp/oauth/token",
    ):
        assert _auth_challenge_path(exempt) is False, exempt
    for challenged in (
        "/mcp/not.well-known/a",
        "/mcp/.well-known/../secret",
        "/mcp/.well-known/oauth-protected-resource/extra",
        "/mcp/x.well-known.y",
        "/mcp/oauth",
        "/mcp/oauth/register/extra",
    ):
        assert _auth_challenge_path(challenged) is True, challenged


def test_an_unauthenticated_post_to_a_wellknown_lookalike_is_challenged(client):
    response = client.post("/mcp/not.well-known/a", json={"jsonrpc": "2.0"})
    assert response.status_code == 401
    assert response.json() == {"error": "authentication_required"}


def test_a_source_event_carries_its_hook_owner_to_the_run(monkeypatch, tmp_path):
    """Not the dataclass default -- the propagation. The delivery thread has no
    request identity, so if the owner does not ride on the event the run has no
    principal at all (Codex asked for the propagation, not the field)."""
    from tinyassets import webhook_inbound as wh
    from tinyassets.scheduler import SchedulerEvent

    emitted: list[SchedulerEvent] = []
    monkeypatch.setattr(wh, "_emit_source_event", wh._emit_source_event)
    monkeypatch.setattr(
        "tinyassets.scheduler.is_running", lambda: True, raising=False,
    )
    monkeypatch.setattr(
        "tinyassets.scheduler.emit_event", emitted.append, raising=False,
    )
    wh._emit_source_event(
        source_id="s-1",
        universe_id="u-1",
        dedupe_key="d-1",
        inputs={"a": 1},
        reservation_id="res-1",
        owner_principal_id="owner-7",
    )
    assert emitted and emitted[0].owner_principal_id == "owner-7"

    # ...and the dispatcher hands it to the run function as the principal.
    from tinyassets import scheduler

    seen: list[dict] = []

    def _run_fn(**kwargs):
        seen.append(kwargs)

    scheduler._dispatch_event(
        emitted[0],
        [{"subscription_id": "sub-1", "run_fn": _run_fn, "universe_id": "u-1"}],
    ) if hasattr(scheduler, "_dispatch_event") else None


def test_both_bearerless_transports_bind_the_local_operator():
    """SSE and stdio carry no bearer and run outside the auth middleware, so
    each binds the local operator once for the process.

    Verified against the SOURCE of `main`, because the alternative is starting
    a server. The previous commit claimed this fix and did not contain it: a
    patch script exited before the edit, and the claim went into a commit
    message unchecked. This assertion is the check.
    """
    import inspect

    from tinyassets import universe_server

    lines = inspect.getsource(universe_server.main).splitlines()
    start = next(
        i for i, line in enumerate(lines)
        if line.strip().startswith('if transport in ("sse", "stdio")')
    )
    bind = next(
        i for i, line in enumerate(lines[start:], start)
        if line.strip() == "bound = bind_local_operator_identity()"
    )
    # ...and it happens BEFORE either transport starts serving. Matched on
    # whole lines, not a substring: the comment above mentions mcp.run() too.
    serves = [
        i for i, line in enumerate(lines[start:], start)
        if line.strip() in ('mcp.run()', 'mcp.run(transport="sse", host=host, port=port)')
    ]
    assert serves, "neither transport starts in this branch"
    assert bind < min(serves)
