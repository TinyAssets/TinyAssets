"""The chat turn is an agent call: it holds an INTERACTIVE seat of its account.

Driven through the real `universe_intelligence.converse`. The model call is not
what is under test; the seat around it is. The source is still an owner-bound
provider, the same shape as `connected_fantasy_model`, so the allocation gate
has an owner to admit.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import universe_intelligence as ui
from tinyassets import universe_seats as seats
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.base import ProviderResponse, UniverseContext
from tinyassets.universe_bundle import seed_okf_bundle


@pytest.fixture
def home(tmp_path: Path, monkeypatch):
    from tinyassets.daemon_server import grant_universe_ownership

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    udir = tmp_path / "u-chat"
    udir.mkdir()
    grant_universe_ownership(tmp_path, universe_id="u-chat", owner_id="alice")
    seed_okf_bundle(udir, purpose="Help my founder.")
    # Declared public so an in-process caller is served its public grounding;
    # disclosure is not what is under test here.
    import tinyassets.api.visibility as vis
    from tinyassets.daemon_server import ensure_universe_registered

    ensure_universe_registered(tmp_path, universe_id="u-chat", universe_path=udir)
    vis.set_universe_visibility("u-chat", "public", source="owner")
    monkeypatch.setattr(ui, "_request_universe", lambda universe_id="": "u-chat")
    monkeypatch.setattr(ui, "_universe_dir", lambda uid: udir)
    yield tmp_path
    seats.stop_refresher()


@pytest.fixture
def owner_bound_chat(home, monkeypatch):
    """Owner-bound deterministic source at the model-call executor seam.

    `connected_fantasy_model` binds a run-graph helper whose operation and owner
    are not this chat. This fixture is that same shape for `converse`: the
    command center's context carries Alice's served provider, and the router
    answers without leaving the allocation gate.
    """
    from tinyassets.providers import call as provider_call

    state = {"reply": "ok", "during": None}

    class Source:
        name = "connected-test-source"

        def call_sync(self, role, prompt, system, config, *, operation, universe_context):
            assert operation == "converse"
            assert universe_context.served_provider.owner_user_id == "alice"
            during = state["during"]
            if during is not None:
                during(prompt, system)
            return ProviderResponse(
                text=state["reply"], provider=self.name, model="deterministic-v1",
                reported_model="deterministic-v1", family="test", latency_ms=0.0,
                input_tokens=1, output_tokens=1,
            )

    def context(*args, **kwargs):
        kwargs.setdefault("served_provider", SimpleNamespace(owner_user_id="alice"))
        return UniverseContext(*args, **kwargs)

    monkeypatch.setattr(ui, "UniverseContext", context)
    monkeypatch.setattr(provider_call, "get_provider_router", lambda: Source())
    monkeypatch.setattr(provider_call, "register_open_providers_for", lambda _context: None)
    # The owner gate in `_allocation` still runs. Persisting the turn budget
    # needs the accounting broker, which this seat test does not start.
    monkeypatch.setattr(
        "tinyassets.request_budget.TurnRequestBudget.persist",
        lambda self, base_path: None,
    )
    return state


KEY = "account:alice"


def _occupancy(root):
    return seats.occupancy(KEY, db=seats.ledger_path(root))


def test_a_chat_turn_holds_an_interactive_seat_of_its_account(home, owner_bound_chat):
    seen = []

    def during(prompt, system=""):
        if "strict JSON" in system:
            return
        with seats._txn(seats.ledger_path(home)) as conn:
            seen.append([tuple(r) for r in conn.execute(
                "SELECT account_id, seat_class, kind, universe_id FROM account_seats"
            )])

    owner_bound_chat["reply"] = "hello back"
    owner_bound_chat["during"] = during
    assert ui.converse("u-chat", "hello") == "hello back"
    assert seen == [[(KEY, "interactive", "chat_turn", "u-chat")]]
    assert _occupancy(home)["running"] == 0, "released when the turn ends"


def test_a_chat_is_served_while_background_work_fills_the_account(home, owner_bound_chat):
    """Free tier: both background seats busy. The chat is served at once on the
    reserved seat -- never behind automation."""
    owner_bound_chat["reply"] = "ok"
    db = seats.ledger_path(home)
    background = [seats.acquire(KEY, db=db) for _ in range(2)]
    assert isinstance(seats.acquire(KEY, db=db), seats.Waiting)  # background is full
    replies = []
    turn = threading.Thread(
        target=lambda: replies.append(ui.converse("u-chat", "are you there?")), daemon=True,
    )
    turn.start()
    turn.join(10)
    try:
        assert not turn.is_alive() and replies == ["ok"], (
            "the chat waited behind background work instead of taking the reserved seat"
        )
    finally:
        for held in background:
            seats.release(held.seat_id, db=db)
        turn.join(10)


def test_a_chat_over_the_seat_count_waits_visibly_then_answers(home, owner_bound_chat):
    """Every seat held by other chats: this one WAITS (never refused), the owner's
    status shows this universe's chat waiting with the Upgrade link, and it
    answers once a seat frees."""
    owner_bound_chat["reply"] = "finally"
    db = seats.ledger_path(home)
    chats = [seats.acquire(KEY, seat_class=seats.CLASS_INTERACTIVE, db=db) for _ in range(3)]
    replies = []
    errors = []

    def run():
        try:
            replies.append(ui.converse("u-chat", "hi"))
        except Exception as exc:  # noqa: BLE001 - the assertion reports the crash
            errors.append(exc)

    turn = threading.Thread(target=run, daemon=True)
    turn.start()
    try:
        deadline = time.monotonic() + 10
        status = None
        while time.monotonic() < deadline:
            status = seats.status_for_owner("u-chat", root=home, actor_id="alice")
            if status and status.get("chat_waiting"):
                break
            time.sleep(0.05)
        assert status and status["chat_waiting"] is True
        assert "message" in status
        assert "[Upgrade](https://tinyassets.io/app?upgrade=1)" in status["message"]
        assert turn.is_alive() and not replies, "a waiting chat is not refused"
    finally:
        for held in chats:
            seats.release(held.seat_id, db=db)
        turn.join(15)
    assert errors == []
    assert replies == ["finally"]
    assert _occupancy(home)["running"] == 0


def test_a_chat_without_an_owner_bound_provider_is_an_error_with_a_message(home, monkeypatch):
    """Allocation refusal is status `error` plus `message`, not a blank wait."""
    from tinyassets.providers import call as provider_call

    class Source:
        def call_sync(self, *args, **kwargs):
            raise AssertionError("allocation must fail before the source is called")

    monkeypatch.setattr(provider_call, "get_provider_router", lambda: Source())
    monkeypatch.setattr(provider_call, "register_open_providers_for", lambda _context: None)
    with pytest.raises(ProviderAuthorityHeldError, match="owner-bound source"):
        ui.converse("u-chat", "hi")
    status = seats.status_for_owner("u-chat", root=home, actor_id="alice")
    assert status["chat_waiting"] is False
    assert status["error"] == "provider_allocation"
    assert "message" in status
    assert "Connect your provider" in status["message"]
