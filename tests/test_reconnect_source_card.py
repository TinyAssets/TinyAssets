"""A refused sign-in raises one card, and it survives a restart.

The founder's live case had no card at all: the only record that a source's stored
sign-in had been refused was an in-memory `SOURCE_HEALTH` mark, which a deploy wipes.
So the app showed a powered universe with a dead connection and nothing to do.
"""

from __future__ import annotations

import json

import pytest

# ONE identity token and ONE metadata fixture for both suites, so the endpoint
# resolution these exercise is the same one the refresh tests pin.
from tests.test_subscription_credential_refresh import ID_TOKEN  # noqa: E402
from tinyassets.credential_vault import (
    clear_refresh_rejected,
    record_refresh_rejected,
    refresh_rejected_sources,
)

UID = "u-card"


def test_a_rejection_survives_a_restart(tmp_path):
    """The whole point of storing it: a fresh process still knows."""
    record_refresh_rejected(tmp_path, universe_id=UID, service="codex")
    # A brand-new connection, as a restarted daemon would open.
    assert set(refresh_rejected_sources(tmp_path, universe_id=UID)) == {"codex"}
    assert refresh_rejected_sources(tmp_path, universe_id="other") == {}


def test_recording_twice_keeps_one_row_and_moves_the_stamp(tmp_path):
    record_refresh_rejected(tmp_path, universe_id=UID, service="codex", when="2026-09-01T00:00:00Z")
    record_refresh_rejected(tmp_path, universe_id=UID, service="codex", when="2026-09-26T00:00:00Z")
    state = refresh_rejected_sources(tmp_path, universe_id=UID)
    assert state == {"codex": "2026-09-26T00:00:00Z"}


def test_clearing_is_per_source_and_idempotent(tmp_path):
    record_refresh_rejected(tmp_path, universe_id=UID, service="codex")
    record_refresh_rejected(tmp_path, universe_id=UID, service="claude")
    clear_refresh_rejected(tmp_path, universe_id=UID, service="codex")
    clear_refresh_rejected(tmp_path, universe_id=UID, service="codex")
    assert set(refresh_rejected_sources(tmp_path, universe_id=UID)) == {"claude"}


def test_the_rejection_is_not_stored_on_the_credential_record(tmp_path):
    """It must not touch the bytes custody is computed from.

    `_subscription_record_digest` hashes the whole record and that digest is pinned
    into the provider binding, so stamping the rejection there would invalidate
    custody and make serving refuse outright -- replacing a turn that falls back to
    the owner's next model with one that cannot run at all.
    """
    from tinyassets.credential_vault import (
        _subscription_record_digest,
        llm_subscription_credential_record,
        load_credential_vault,
        write_credential_vault,
    )

    universe = tmp_path / UID
    universe.mkdir()
    document = json.dumps({"tokens": {"access_token": "a", "refresh_token": "r"}})
    import base64

    write_credential_vault(
        universe,
        [llm_subscription_credential_record(
            service="codex",
            auth_json_b64=base64.b64encode(document.encode()).decode(),
        )],
        owner_user_id="owner", universe_id=UID,
    )
    record = next(
        r for r in load_credential_vault(universe)
        if r.get("credential_type") == "llm_subscription"
    )
    before = _subscription_record_digest(universe, "codex", record)

    record_refresh_rejected(tmp_path, universe_id=UID, service="codex")

    after_record = next(
        r for r in load_credential_vault(universe)
        if r.get("credential_type") == "llm_subscription"
    )
    assert "refresh_rejected_at" not in after_record
    assert after_record == record
    assert _subscription_record_digest(universe, "codex", after_record) == before


def test_a_refused_refresh_records_the_rejection_and_a_fixed_one_clears_it(
    tmp_path, monkeypatch,
):
    """The package runner keeps the reconnect card until a job succeeds."""
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from tinyassets import credential_vault
    from tinyassets.exceptions import ProviderAuthenticationError
    from tinyassets.external_agents import runner
    from tinyassets.providers.base import ModelConfig

    universe = tmp_path / UID
    universe.mkdir()
    _deposit(universe)
    custody = SimpleNamespace(universe_dir=universe, owner_user_id="owner",
                              reference=SimpleNamespace(service="codex"))
    held = False

    @asynccontextmanager
    async def unlocked(_):
        nonlocal held
        held = True
        try:
            yield
        finally:
            held = False

    original_record = credential_vault.record_refresh_rejected
    original_clear = credential_vault.clear_refresh_rejected

    def record_while_locked(*args, **kwargs):
        assert held
        return original_record(*args, **kwargs)

    def clear_while_locked(*args, **kwargs):
        assert held
        return original_clear(*args, **kwargs)

    monkeypatch.setattr(runner, "exclusive_refresh", unlocked)
    monkeypatch.setattr(credential_vault, "record_refresh_rejected", record_while_locked)
    monkeypatch.setattr(credential_vault, "clear_refresh_rejected", clear_while_locked)

    async def refused(*_args, **_kwargs):
        raise ProviderAuthenticationError("refresh token rejected")

    monkeypatch.setattr(runner, "_run_job_locked", refused)
    config = ModelConfig(invocation_owner_user_id="owner")

    with pytest.raises(ProviderAuthenticationError):
        asyncio.run(runner.run_job("codex", None, custody, prompt="", system="",
                                   config=config))
    assert set(refresh_rejected_sources(tmp_path, universe_id=UID)) == {"codex"}

    async def recovered(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(runner, "_run_job_locked", recovered)
    asyncio.run(runner.run_job("codex", None, custody, prompt="", system="",
                               config=config))
    assert refresh_rejected_sources(tmp_path, universe_id=UID) == {}


def test_a_rejection_for_another_source_is_still_recorded(tmp_path, monkeypatch):
    """One package's recovery cannot erase another package's reconnect card."""
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from tinyassets.external_agents import runner
    from tinyassets.providers.base import ModelConfig

    universe = tmp_path / UID
    universe.mkdir()
    _deposit(universe)

    @asynccontextmanager
    async def unlocked(_):
        yield

    async def recovered(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(runner, "exclusive_refresh", unlocked)
    monkeypatch.setattr(runner, "_run_job_locked", recovered)
    record_refresh_rejected(tmp_path, universe_id=UID, service="codex")
    custody = SimpleNamespace(universe_dir=universe, owner_user_id="owner",
                              reference=SimpleNamespace(service="claude"))
    asyncio.run(runner.run_job("claude", None, custody, prompt="", system="",
                               config=ModelConfig(invocation_owner_user_id="owner")))
    assert "codex" in refresh_rejected_sources(tmp_path, universe_id=UID)


def test_inline_source_refusal_still_raises_and_success_clears_its_card(tmp_path, monkeypatch):
    """The non-package refresh seam still owns its rejection and repair card."""
    from tinyassets import subscription_refresh
    from tinyassets.credential_refresh import RefreshRejected
    from tinyassets.exceptions import ProviderAuthenticationError

    universe = tmp_path / UID
    universe.mkdir()
    _deposit(universe, service="claude", refresh="r-dead")
    monkeypatch.setattr(subscription_refresh, "document_is_stale", lambda *_: True)
    monkeypatch.setattr(subscription_refresh, "_source_endpoint",
                        lambda *_: ("https://issuer.example/token", "client"))

    def refused(*_args, **_kwargs):
        raise RefreshRejected("stored sign-in rejected")

    monkeypatch.setattr(subscription_refresh, "_spend", refused)
    with pytest.raises(ProviderAuthenticationError):
        subscription_refresh.refresh_deposited_subscriptions(
            base_path=tmp_path, universe_dir=universe, owner_user_id="owner",
            universe_id=UID, launching="claude",
        )
    assert set(refresh_rejected_sources(tmp_path, universe_id=UID)) == {"claude"}

    monkeypatch.setattr(subscription_refresh, "_spend", lambda doc, **_: (
        subscription_refresh._rebuild(doc, access_token="a-2", refresh_token="r-live",
                                      id_token="")))
    subscription_refresh.refresh_deposited_subscriptions(
        base_path=tmp_path, universe_dir=universe, owner_user_id="owner",
        universe_id=UID, launching="claude",
    )
    assert refresh_rejected_sources(tmp_path, universe_id=UID) == {}


def test_nonlaunching_inline_source_refusal_does_not_spend_package_token(tmp_path, monkeypatch):
    """A refused secondary sign-in records its card without failing the package launch."""
    from tinyassets import subscription_refresh
    from tinyassets.credential_refresh import RefreshRejected

    universe = tmp_path / UID
    universe.mkdir()
    _deposit(universe, service="codex", refresh="r-package")
    _deposit(universe, service="claude", refresh="r-dead")
    monkeypatch.setattr(subscription_refresh, "document_is_stale", lambda *_: True)
    monkeypatch.setattr(subscription_refresh, "_source_endpoint",
                        lambda *_: ("https://issuer.example/token", "client"))
    spent = []

    def refused(doc, **_):
        spent.append(doc.refresh_token)
        raise RefreshRejected("stored sign-in rejected")

    monkeypatch.setattr(subscription_refresh, "_spend", refused)
    subscription_refresh.refresh_deposited_subscriptions(
        base_path=tmp_path, universe_dir=universe, owner_user_id="owner",
        universe_id=UID, launching="codex",
    )
    assert spent == ["r-dead"]
    assert set(refresh_rejected_sources(tmp_path, universe_id=UID)) == {"claude"}


def _deposit(universe_dir, service="codex", refresh="r-1"):
    """A real subscription record, because a card only stands for a live credential."""
    import base64

    from tinyassets.credential_vault import (
        llm_subscription_credential_record,
        write_credential_vault,
    )

    document = base64.b64encode(json.dumps({
        "tokens": {"id_token": ID_TOKEN, "access_token": "a", "refresh_token": refresh},
    }).encode()).decode()
    write_credential_vault(
        universe_dir,
        [llm_subscription_credential_record(service=service, auth_json_b64=document)],
        owner_user_id="owner", universe_id=UID,
    )


def test_the_card_names_the_source_from_the_record_and_asks_for_a_sign_in(tmp_path):
    from tinyassets.api.pending_requests import SIGN_IN_SHAPE, _reconnect_requests

    universe = tmp_path / UID
    universe.mkdir(exist_ok=True)
    _deposit(universe)
    record_refresh_rejected(tmp_path, universe_id=UID, service="codex")
    cards = _reconnect_requests(tmp_path, UID, universe)
    assert len(cards) == 1
    card = cards[0]
    assert card["title"] == "codex needs you to sign in again"
    assert card["status"] == "pending" and card["sticky"] is True
    # The SAME connect action the setup card uses, with the sign-in shape: there is
    # nothing here for the owner to paste.
    assert card["action"]["type"] == "connect" and card["action"]["use"] == "model"
    assert card["action"]["setup"]["shapes"] == [SIGN_IN_SHAPE]
    assert card["action"]["setup"]["service"] == "codex"
    assert "paste" not in card["body"].lower()


def test_no_rejection_means_no_card(tmp_path):
    from tinyassets.api.pending_requests import _reconnect_requests

    assert _reconnect_requests(tmp_path, UID, tmp_path / UID) == []



# --------------------------------------------------------------------------- #
# 5. The card matches what the surface can actually honour.
# --------------------------------------------------------------------------- #


def test_a_rejection_for_a_removed_credential_raises_no_card(tmp_path):
    """Codex refute-review round 2, item 3: a rejection must not outlive its credential.

    The row says a stored sign-in was refused. If the owner has since REMOVED that
    credential there is nothing to sign back in to, and the card would ask them to
    repair a connection they deleted.
    """
    from tinyassets.api.pending_requests import _reconnect_requests

    universe = tmp_path / UID
    universe.mkdir(exist_ok=True)
    record_refresh_rejected(tmp_path, universe_id=UID, service="codex")
    assert _reconnect_requests(tmp_path, UID, universe) == []


def test_only_one_reconnect_card_is_offered_at_a_time(tmp_path):
    """Codex refute-review round 2, P1: there is ONE connect panel and `connectBody`
    MOVES it, so a second card re-configured it on every render -- and the rail
    re-renders every 15 seconds, resetting a sign-in in progress and leaving the second
    card with no panel at all. One card at a time is what the surface can honour.
    """
    from tinyassets.api.pending_requests import _reconnect_requests

    universe = tmp_path / UID
    universe.mkdir(exist_ok=True)
    _deposit(universe, service="codex")
    _deposit(universe, service="claude")
    record_refresh_rejected(
        tmp_path, universe_id=UID, service="claude", when="2026-09-20T00:00:00Z")
    record_refresh_rejected(
        tmp_path, universe_id=UID, service="codex", when="2026-09-26T00:00:00Z")

    cards = _reconnect_requests(tmp_path, UID, universe)
    assert len(cards) == 1
    # The OLDEST rejection first: the one that has been broken longest.
    assert cards[0]["title"] == "claude needs you to sign in again"

    # ...and the next appears once that one is resolved.
    clear_refresh_rejected(tmp_path, universe_id=UID, service="claude")
    cards = _reconnect_requests(tmp_path, UID, universe)
    assert len(cards) == 1 and cards[0]["title"] == "codex needs you to sign in again"


def test_a_source_without_a_brokered_sign_in_gets_the_ordinary_shapes(tmp_path):
    from tinyassets.api.pending_requests import (
        _MODEL_CONNECT_SHAPES,
        SIGN_IN_SHAPE,
        _reconnect_requests,
    )

    universe = tmp_path / UID
    universe.mkdir(exist_ok=True)
    _deposit(universe, service="claude")
    record_refresh_rejected(tmp_path, universe_id=UID, service="claude")
    card = _reconnect_requests(tmp_path, UID, universe)[0]
    shapes = card["action"]["setup"]["shapes"]
    assert SIGN_IN_SHAPE not in shapes, "a sign-in was offered for a source without one"
    assert shapes == list(_MODEL_CONNECT_SHAPES)
    assert "one tap" not in card["body"]
