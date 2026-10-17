"""A universe belongs to an authenticated person. That is the whole gate.

Founder rule, 2026-08-28: no universe should exist that is not bound to a WorkOS
user. Founder directive, 2026-09-30: an account has exactly two limits -- the
cloud bytes it occupies and the agent runs it may have at once -- so HOW MANY
universes a person owns is not a condition of creating one. The subscription gate
that used to live here is gone; a second universe costs storage, and storage is
already metered.

Enforced on the public surface rather than in `_action_create_universe`, which is
a shared primitive that fixtures, migrations and internal seeding call with no
authenticated subject. Gating it there refused 23 legitimate internal callers;
that was the signal the rule is about PEOPLE and belongs where a person asks.
"""

from __future__ import annotations

import pytest

from tinyassets import universe_server as us


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    return tmp_path


def _actor(monkeypatch, actor_id):
    from tinyassets.api import permissions

    monkeypatch.setattr(permissions, "current_request_actor_id", lambda: actor_id)


def _home(monkeypatch, value):
    import tinyassets.daemon_server as ds

    monkeypatch.setattr(ds, "get_founder_home", lambda *_a, **_kw: value)


def test_anonymous_cannot_birth_a_universe(monkeypatch):
    _actor(monkeypatch, "anonymous")
    out = us._universe_birth_refusal()
    assert out is not None
    assert out["failure_class"] == "universe_requires_authenticated_subject"


def test_an_empty_actor_cannot_birth_a_universe(monkeypatch):
    _actor(monkeypatch, "")
    assert us._universe_birth_refusal() is not None


def test_a_signup_with_no_home_gets_one(monkeypatch):
    _actor(monkeypatch, "user_01SIGNUP")
    _home(monkeypatch, "")
    assert us._universe_birth_refusal() is None


def test_a_free_subject_may_create_a_second_universe(monkeypatch, tmp_path):
    """The removed limit, asserted gone: free tier, one home already, still yes."""
    from tinyassets.broker.owner_reads import Plans

    _actor(monkeypatch, "user_01ALREADY")
    _home(monkeypatch, "u-existing")
    udir = tmp_path / "u-existing"
    udir.mkdir(parents=True, exist_ok=True)
    Plans(tmp_path).apply("user_01ALREADY", tier="free", event_created=1000.0)
    assert us._universe_birth_refusal() is None


def test_a_free_subject_may_create_a_tenth_universe(monkeypatch):
    """No count anywhere: the gate never reads how many homes exist."""
    _actor(monkeypatch, "user_01MANY")
    _home(monkeypatch, "u-ninth")
    assert us._universe_birth_refusal() is None


def test_the_gate_names_no_tier_and_no_subscription(monkeypatch):
    """Mutation guard: re-adding a tier read would have to re-add the import."""
    import inspect

    src = inspect.getsource(us._universe_birth_refusal)
    for forbidden in ("get_tier", "TIER_PAID", "subscription", "get_founder_home"):
        assert forbidden not in src, f"the birth gate must not consult {forbidden}"


def test_a_broken_binding_store_no_longer_blocks_creation(monkeypatch):
    """The gate does not read the binding store at all, so it cannot fail on it."""
    import tinyassets.daemon_server as ds

    _actor(monkeypatch, "user_01BROKEN")

    def _boom(*_a, **_kw):
        raise OSError("binding store unavailable")

    monkeypatch.setattr(ds, "get_founder_home", _boom)
    assert us._universe_birth_refusal() is None


def test_the_birth_route_consults_the_gate_before_creating():
    """Asserted against the source: the check must precede the create call."""
    import pathlib

    src = pathlib.Path(us.__file__).read_text(encoding="utf-8")
    # Narrow window, not "anywhere before". The first version split on the whole file
    # and matched the function DEFINITION, which of course precedes the call site --
    # so deleting the actual call left it green. Mutation testing caught it.
    call_at = src.index('action="create_universe"')
    window = src[max(0, call_at - 700):call_at]
    assert "_universe_birth_refusal()" in window, (
        "the birth route must CALL the gate immediately before create_universe; "
        "defining it elsewhere is not enforcement"
    )
    # And it must act on the answer, not merely compute it.
    assert "_json.dumps(_refused)" in window
