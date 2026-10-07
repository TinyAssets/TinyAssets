"""Connecting a subscription in the app is the whole gesture: deposit AND serve.

Live, 2026-09-01. The founder pasted a Codex credential through the app. The
deposit succeeded and chat worked on it, and every run failed:

    Connect your provider before running this universe.
    permission_denied:provider_not_bound

`get_status` said `no_serving_runtime` -- "registering a provider is not
selecting it". The deposit result carried `next: bind_serving_provider` as a
hint, to a surface where nobody reads hints. The claude path and the phone
already finish the gesture through `/app/serving/bind`; the pasted
credential path printed the receipt and stopped.

The deposit itself stays write-only: the spec
(`openspec/specs/byo-llm-deposit-surface`) says it SHALL NOT enable
serving, and a server-side auto-bind was withdrawn on Codex review. The app
finishes the gesture, and the helper the app calls is serialized per universe
so two first-time gestures cannot leave two bindings and nothing serving.
"""
from __future__ import annotations

import base64
import contextvars
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity


class _StaticAuthProvider(AuthProvider):
    def __init__(self, identity: Identity | None) -> None:
        self.identity = identity

    def resolve_token(self, token: str) -> Identity | None:
        return self.identity if token == "valid" else None

    def is_auth_required(self) -> bool:
        return True

    def register_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"client_id": "test-client", **metadata}

    def create_authorization(self, *a: Any, **k: Any) -> str:
        return "test-code"

    def exchange_code(self, *a: Any, **k: Any) -> dict[str, Any] | None:
        return None


def _login(user_id: str) -> None:
    set_provider(_StaticAuthProvider(Identity(
        user_id=user_id, username=user_id, capabilities=["tinyassets.universe.write"],
    )))
    auth_middleware("valid")


@pytest.fixture(autouse=True)
def _reset_auth():
    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.delenv("TINYASSETS_ALLOW_CLAUDE_SERVING", raising=False)
    return root


def _universe(base: Path, uid: str, admin: str) -> Path:
    from tinyassets.daemon_server import grant_universe_access

    udir = base / uid
    udir.mkdir(parents=True, exist_ok=True)
    grant_universe_access(
        base, universe_id=uid, actor_id=admin, permission="admin", granted_by=admin,
    )
    return udir


def _deposit(uid: str, service: str, material: str) -> dict[str, Any]:
    from tinyassets.api.llm_deposit import connect_llm

    b64 = base64.b64encode(material.encode("utf-8")).decode("ascii")
    return connect_llm(
        universe_id=uid,
        payload=json.dumps({"service": service, "auth_material_b64": b64}),
    )


_CODEX_BUNDLE = json.dumps({"tokens": {
    "access_token": "a", "refresh_token": "r", "account_id": "acct-1",
}})


def _bindings(base: Path, uid: str) -> list[dict[str, Any]]:
    from tinyassets.custom_agents import list_bindings

    return list(list_bindings(base, universe_id=uid, limit=100))


# ------------------------------------------ the deposit stays write-only


def test_the_deposit_itself_does_NOT_serve(base):
    """The spec requirement, pinned so the withdrawn server-side auto-bind does
    not come back by accident: the deposit names the re-point and enables
    nothing."""
    _universe(base, "u-1", "founder")
    _login("founder")

    result = _deposit("u-1", "codex", _CODEX_BUNDLE)

    assert result["status"] == "deposited"
    assert result["next"].startswith("write_graph target=agent_binding")
    assert [b for b in _bindings(base, "u-1") if b.get("status") == "serving"] == []


# --------------------------- the gesture the app calls is one at a time


def test_two_first_time_gestures_leave_ONE_binding_and_it_serves(base, monkeypatch):
    """Codex on #2760, S3. Two open tabs heal at once (or a paste and a phone
    connect): both list bindings, both see none, both create one, both go
    serving, and each quiesce pass disables the other -- two bindings, zero
    serving, every later call refusing them as ambiguous. The race is made
    deterministic by holding every lister for a moment after it looks."""
    import tinyassets.onboarding.serving as serving

    udir = _universe(base, "u-1", "founder")
    _login("founder")
    assert _deposit("u-1", "codex", _CODEX_BUNDLE)["status"] == "deposited"

    from tinyassets import custom_agents

    original = custom_agents.list_bindings
    started = threading.Barrier(2)

    def slow_list(*args, **kwargs):
        rows = original(*args, **kwargs)
        # Without the gesture lock both threads reach this barrier having LOOKED
        # and seen nothing, and then both create. With the lock the second
        # thread cannot look until the first is done, so the barrier times out
        # -- which is the fix working, not the test failing.
        try:
            started.wait(timeout=2)
        except threading.BrokenBarrierError:
            pass
        return rows

    monkeypatch.setattr(custom_agents, "list_bindings", slow_list)

    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def gesture():
        # A Context can be entered by one thread at a time: each thread gets
        # its own copy of the signed-in identity.
        ctx = contextvars.copy_context()
        try:
            results.append(ctx.run(
                serving.ensure_founder_serving,
                base_path=base, universe_dir=udir, owner_user_id="founder",
                universe_id="u-1", service="codex",
            ))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion
            errors.append(exc)

    threads = [threading.Thread(target=gesture) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)

    assert not errors, errors
    assert [r["status"] for r in results] == ["serving", "serving"], results
    bindings = _bindings(base, "u-1")
    assert len(bindings) == 1, f"two first-time gestures created {len(bindings)} bindings"
    assert bindings[0]["status"] == "serving", "the gesture finished with nothing serving"


def test_the_gesture_is_idempotent_once_the_binding_exists(base):
    import tinyassets.onboarding.serving as serving

    udir = _universe(base, "u-1", "founder")
    _login("founder")
    _deposit("u-1", "codex", _CODEX_BUNDLE)
    first = serving.ensure_founder_serving(
        base_path=base, universe_dir=udir, owner_user_id="founder",
        universe_id="u-1", service="codex",
    )
    second = serving.ensure_founder_serving(
        base_path=base, universe_dir=udir, owner_user_id="founder",
        universe_id="u-1", service="codex",
    )
    assert first["status"] == second["status"] == "serving"
    assert first["agent_binding_id"] == second["agent_binding_id"]
    assert len(_bindings(base, "u-1")) == 1


# ------------------------------------------------------------------ the app


@pytest.fixture
def app_html() -> str:
    from tinyassets.onboarding import render_app_html

    html, _csp = render_app_html()
    return html


