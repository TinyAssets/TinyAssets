"""Every goals WRITE action must leave an attributed ledger row.

This exists because the row silently stopped being written. `market.py` called
`_append_global_ledger(...)` without its required keyword-only `actor`, so every
goals write raised `TypeError`, hit the broad `except Exception` guarding the
ledger, and was logged as::

    WARNING  Ledger write failed for goals.propose:
             _append_global_ledger() missing 1 required keyword-only argument: 'actor'

The action itself still returned `{"status": "proposed"}`, so nothing user-facing
or test-facing went red — `ledger.json` simply never appeared. Attribution and
audit data for every goals write was lost with a warning nobody reads.

The swallow is deliberate (`_append_global_ledger` documents "Never raises:
failures are logged but don't roll back the mutation") and is NOT changed here:
a ledger failure should not roll back a completed mutation. That design is
exactly why the defect survived, so the guard has to be a test that reads the
ledger back rather than trust on the call site being right.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


def _become(user_id: str) -> None:
    """Sign in as ``user_id``.

    These tests used to set ``UNIVERSE_SERVER_USER``, which named the actor by
    environment variable -- authority from a string anybody can set. The
    autouse operator fixture rebinds between tests, so this does not leak.
    """
    from tinyassets.auth import middleware as _mw
    from tinyassets.auth.provider import Identity

    _mw._current_identity.set(
        Identity(
            user_id=user_id,
            username=user_id,
            display_name=user_id,
            capabilities=[
                "tinyassets.universe.read",
                "tinyassets.universe.write",
                "tinyassets.universe.admin",
                "tinyassets.extensions.read",
                "tinyassets.extensions.write",
            ],
        )
    )


@pytest.fixture
def goals_env(tmp_path, monkeypatch, authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    _become("alice")
    authenticate_request(
        "alice",
        capabilities=["tinyassets.goals.read", "tinyassets.goals.write"],
    )
    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us, base
    importlib.reload(us)


def _ledger_rows(base: Path) -> list[dict]:
    path = base / "ledger.json"
    if not path.exists():
        return []
    return json.loads(path.read_text("utf-8"))


def test_goals_propose_writes_an_attributed_ledger_row(goals_env) -> None:
    us, base = goals_env

    result = json.loads(us._goals_impl(action="propose", name="G", description="x"))
    assert result.get("status") == "proposed", result

    rows = _ledger_rows(base)
    assert rows, (
        "goals.propose wrote no ledger row at all. The mutation succeeded, so "
        "this is the silent-attribution-loss regression: check that "
        "_append_global_ledger is called with its required `actor` kwarg."
    )
    proposes = [r for r in rows if r.get("action") == "goals.propose"]
    assert len(proposes) == 1, [r.get("action") for r in rows]
    assert proposes[0].get("actor") == "alice", proposes[0]


def test_ledger_actor_is_the_credential_subject_not_the_env_user(
    goals_env, monkeypatch
) -> None:
    """The env var must not be able to forge the recorded actor.

    `UNIVERSE_SERVER_USER` is ambient; the credential subject is not. If the
    ledger ever falls back to the env var, an unauthenticated or differently
    authenticated caller would be attributed to whoever the environment names.
    """
    us, base = goals_env
    # The ENVIRONMENT, deliberately: this test exists to prove the env
    # cannot forge the recorded actor. Signing mallory in would make
    # them the real caller and the assertion would be about nothing.
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "mallory")

    json.loads(us._goals_impl(action="propose", name="G2", description="x"))

    rows = [r for r in _ledger_rows(base) if r.get("action") == "goals.propose"]
    assert rows, "no ledger row written"
    assert rows[0].get("actor") == "alice", (
        f"ledger recorded {rows[0].get('actor')!r}; the credential subject is "
        f"'alice' and the env var was changed to 'mallory' to prove the env "
        f"cannot forge attribution"
    )


def test_unauthenticated_write_is_anonymous_not_the_env_user(
    tmp_path, monkeypatch
) -> None:
    """With NO credential, there is no write at all -- and certainly not one
    attributed to whoever the environment names.

    This used to assert the row said `anonymous`, and said so deliberately:
    "production OAuth blocks anonymous writes at a different layer, and this
    test is about attribution honesty, not about relocating that gate". The
    gate moved. A write with nobody behind it is refused, which is the honest
    version of the same concern -- the audit trail cannot carry a forged
    identity if it carries no row.
    """
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    # Still set, and still ignored: the point of the test is that the ambient
    # environment confers nothing.
    # The ENVIRONMENT, deliberately: this test exists to prove the env
    # cannot forge the recorded actor. Signing mallory in would make
    # them the real caller and the assertion would be about nothing.
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "mallory")
    from tinyassets import universe_server as us
    from tinyassets.auth.middleware import clear_identity

    importlib.reload(us)
    try:
        clear_identity()
        answer = json.loads(us._goals_impl(action="propose", name="G3", description="x"))
        assert "error" in answer, f"an unauthenticated propose was accepted: {answer}"
        rows = [
            r for r in _ledger_rows(base) if r.get("action") == "goals.propose"
        ]
        assert rows == [], (
            f"an unauthenticated write left {len(rows)} ledger row(s); the "
            f"ambient UNIVERSE_SERVER_USER was 'mallory', so any row at all "
            f"risks the env forging an identity into the audit trail"
        )
    finally:
        importlib.reload(us)
