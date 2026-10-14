"""The chat turn is an agent call: it holds an INTERACTIVE seat of its account.

Driven through the real `universe_intelligence.converse`, with only the provider
replaced (the model call is not what is under test; the seat around it is).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tinyassets import universe_intelligence as ui
from tinyassets import universe_seats as seats
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


KEY = "account:alice"


def _occupancy(root):
    return seats.occupancy(KEY, db=seats.ledger_path(root))


def test_a_chat_turn_holds_an_interactive_seat_of_its_account(home, monkeypatch):
    seen = []

    def provider(prompt, system="", **_kw):
        if "strict JSON" in system:
            return "{}"
        with seats._txn(seats.ledger_path(home)) as conn:
            seen.append([tuple(r) for r in conn.execute(
                "SELECT account_id, seat_class, kind, universe_id FROM account_seats"
            )])
        return "hello back"

    monkeypatch.setattr(ui, "text_sync", provider)
    assert ui.converse("u-chat", "hello") == "hello back"
    assert seen == [[(KEY, "interactive", "chat_turn", "u-chat")]]
    assert _occupancy(home)["running"] == 0, "released when the turn ends"


def test_a_chat_is_served_while_background_work_fills_the_account(home, monkeypatch):
    """Free tier: both background seats busy. The chat is served at once on the
    reserved seat -- never behind automation."""
    monkeypatch.setattr(ui, "text_sync", lambda prompt, system="", **_kw: "ok")
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


def test_a_chat_over_the_seat_count_waits_visibly_then_answers(home, monkeypatch):
    """Every seat held by other chats: this one WAITS (never refused), the owner's
    status shows this universe's chat waiting with the Upgrade link, and it
    answers once a seat frees."""
    monkeypatch.setattr(ui, "text_sync", lambda prompt, system="", **_kw: "finally")
    db = seats.ledger_path(home)
    chats = [seats.acquire(KEY, seat_class=seats.CLASS_INTERACTIVE, db=db) for _ in range(3)]
    replies = []
    turn = threading.Thread(target=lambda: replies.append(ui.converse("u-chat", "hi")),
                            daemon=True)
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
        assert "[Upgrade](https://tinyassets.io/app?upgrade=1)" in status["message"]
        assert turn.is_alive() and not replies, "a waiting chat is not refused"
    finally:
        for held in chats:
            seats.release(held.seat_id, db=db)
    turn.join(15)
    assert replies == ["finally"]
    assert _occupancy(home)["running"] == 0
