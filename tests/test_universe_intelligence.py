"""S4 — the turn-scoped universe-intelligence runtime.

The intelligence speaks first-person AS the universe on its ASSIGNED engine,
grounded in the OKF bundle, in-process (no MCP transport auth gate).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import tinyassets.universe_intelligence as ui
from tinyassets.universe_bundle import seed_okf_bundle


@pytest.fixture(autouse=True)
def _reset_auth():
    """Put the auth provider back after every test in this module.

    `_become_founder` installs a static authenticated provider through the real
    middleware, and process-global auth state does not unwind itself. Without
    this, the LAST test here left `founder-1` authenticated for whatever ran
    next, and `test_universe_list_observability` / `test_universe_server_five_handles`
    failed several files later — passing in isolation, failing in a full run.
    Sibling module `test_interlocutor_tier.py` already had this fixture; adding
    `_become_founder` here without it is what introduced the leak.
    """
    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import DevAuthProvider

    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the resolvers at the test tree.

    Assembly now runs grounding through the tier ∩ visibility disclosure filter
    (relay task 6.6), which resolves against the universe registry — so these
    tests need a real data dir rather than a bare directory.
    """
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


def _seed(tmp_path: Path) -> Path:
    udir = tmp_path / "u-test"
    udir.mkdir()
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(udir.parent, udir.name)
    seed_okf_bundle(udir, purpose="To help my founder bring their projects to life.")
    from tinyassets.starter_release import starter_manifest
    from tinyassets.starter_seeds import seed_store
    with seed_store(udir, owner_id="test-owner::u-test", center_id="u-test") as seeds:
        seeds.install(starter_manifest(), fresh=True)
    # Register + declare so disclosure is evaluable. Declared `public`, so an
    # unauthenticated in-process caller (T0) is served the universe's public
    # grounding; founder-private grounding stays excluded by the filter itself.
    _declare(tmp_path, "u-test")
    return udir


def _declare(base: Path, uid: str) -> None:
    """Register + declare a universe so disclosure can be evaluated for it."""
    import tinyassets.api.visibility as vis
    from tinyassets.daemon_server import ensure_universe_registered

    ensure_universe_registered(base, universe_id=uid, universe_path=base / uid)
    vis.set_universe_visibility(uid, "public", source="owner")


def _become_founder(base: Path, uid: str = "u-test", actor_id: str = "founder-1") -> None:
    """Authenticate as a real admin-granted founder of ``uid``.

    Tests below used to assert founder tier by passing ``tier=FOUNDER`` and
    relying on the sink to take their word for it. It did, and that was the
    escalation Codex reproduced on 2026-08-28 -- a `write`-only actor got the
    founder's grounding the same way. Now the sink resolves and only narrows, so
    a test that wants founder authority has to actually hold it. That is
    strictly better evidence: these assertions now prove the founder path works
    end to end rather than that a keyword argument is honoured.
    """
    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import AuthProvider, Identity
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(base, universe_id=uid, actor_id=actor_id, permission="admin")

    identity = Identity(
        user_id=actor_id,
        username=actor_id,
        capabilities=[
            "tinyassets.universe.read",
            "tinyassets.universe.write",
            "tinyassets.universe.admin",
        ],
    )

    class _Static(AuthProvider):
        """Minimal concrete provider: this Resource Server never runs the OAuth
        flow itself, so the four flow methods are unreachable stubs."""

        def resolve_token(self, token: str):
            return identity if token == "ok" else None

        def is_auth_required(self) -> bool:
            return True

        def register_client(self, metadata: dict) -> dict:
            return {"client_id": "test-client", **metadata}

        def create_authorization(self, *_a, **_kw) -> str:
            raise NotImplementedError

        def exchange_code(self, *_a, **_kw) -> dict:
            raise NotImplementedError

    set_provider(_Static())
    auth_middleware("ok")


def _fm(path: Path, key: str) -> str:
    import yaml

    parts = path.read_text(encoding="utf-8").split("---", 2)
    meta = yaml.safe_load(parts[1]) or {}
    return str(meta.get(key, ""))














# ── learning persistence (Slice 1 — the universe is the sole brain-writer) ───
# Codex ADAPT 2026-07-02: commit is separate from the reply and grounded strictly
# in what the founder explicitly stated. This is the fix for the Finding A
# regression (identity was told to route to an unreachable save path, so nothing
# persisted).






























# -- cross-surface continuity directive (founder 2026-08-23; Codex adapt) --------

_CONTINUITY_MARKER = "CONTINUITY ACROSS SURFACES"


def _capture_writer_call(monkeypatch, udir):
    """Wire converse so the WRITER call's (prompt, system) are captured."""
    captured: dict = {}

    def fake_text_sync(prompt, system="", *, role="writer",
                           universe_context=None, **_kw):
        if "strict JSON" in system:  # the learning-extraction call, not the writer
            return "{}"
        captured.update(prompt=prompt, system=system)
        return "ok"

    monkeypatch.setattr(ui, "_request_universe", lambda universe_id="": "u-test")
    monkeypatch.setattr(ui, "_universe_dir", lambda uid: udir)
    monkeypatch.setattr(ui, "text_sync", fake_text_sync)
    return captured












# -- interactive sole-writer retry policy (no synchronous sleep on the ingress
#    path; the router no longer cools the sole writer on a transient timeout) --

def _exhausted(*statuses):
    """AllProvidersExhaustedError carrying attempts with the given statuses."""
    from types import SimpleNamespace

    from tinyassets.exceptions import AllProvidersExhaustedError

    return AllProvidersExhaustedError(
        "chain drained",
        attempts=[SimpleNamespace(status=s) for s in statuses],
    )






