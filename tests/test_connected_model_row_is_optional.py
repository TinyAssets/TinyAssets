"""A connected universe has nothing waiting on the user.

Live 2026-09-25, free-only account ``u-01ky3zh1arr8qth8jee7zx63pq``: OpenRouter
sign-in completed, and the rail still showed "Connect another LLM" under
"Waiting on you". The derived entry carried ``status: "pending"`` exactly like a
real ask, so every surface reading the rail -- and the user reading the heading
-- was told an action was outstanding when none was.

Fixing the status was not enough. Live 2026-09-30 the same free account still
had a permanent "LLM - Connect another LLM" card sitting in the rail with no
Accept, Deny or Clear on it, because a derived entry has nothing to resolve:

    "There is NO dismiss, deny or clear control, so a naive user with a working
    universe has a 'Waiting on you' item they can neither act on nor remove. If
    it's a standing 'optional' item, it doesn't belong in 'Waiting on you' at
    all; that rail is for things blocking the user's work, and optional extras
    live in the model picker ('Change model')."   -- founder, 2026-09-30

So the entry still EXISTS -- every client reads it, and it is still the one
route to a second source -- and the app no longer renders it as a rail card.
"Change model" reaches it, and the card appears only while it is open.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests import test_model_bootstrap as _bootstrap
from tests.test_notification_is_the_setup import _RAIL_HARNESS
from tests.test_onboarding_app import _js_function
from tinyassets.onboarding import render_app_html

_NODE = shutil.which("node")
rig = _bootstrap.rig
finish = _bootstrap.finish


def _rail():
    from tinyassets.api.pending_requests import list_requests

    return list_requests(universe_id="u-owner")["pending"]


# --------------------------------------------------------------------------- #
# The server's own answer: "optional" is not "pending".
# --------------------------------------------------------------------------- #


def test_an_unpowered_universe_still_blocks_on_its_connect_request(rig):
    """The unpowered state is unchanged: this one really is waiting on you."""
    first = _rail()[0]
    assert first["request_id"] == "sys_connect_llm"
    assert first["status"] == "pending" and first["sticky"] is True


def test_a_connected_universe_reports_the_row_as_optional(rig, monkeypatch):
    monkeypatch.setattr("tinyassets.api.pending_requests._serving_llm_bound",
                        lambda *a, **k: True)
    row = _rail()[-1]
    assert row["request_id"] == "sys_connect_llm"
    assert row["title"] == "Connect another LLM" and row["sticky"] is False
    # Still offered, still answerable -- just not outstanding.
    assert row["status"] == "optional"
    assert row["action"]["type"] == "connect" and row["action"]["use"] == "model"
    assert row["resolved_at"] is None and row["answer"] is None


def test_an_optional_row_is_not_counted_as_an_outstanding_ask(rig, monkeypatch):
    from tinyassets.api.pending_requests import list_requests

    monkeypatch.setattr("tinyassets.api.pending_requests._serving_llm_bound",
                        lambda *a, **k: True)
    document = list_requests(universe_id="u-owner")
    outstanding = [row for row in document["pending"] if row["status"] == "pending"]
    assert outstanding == [], "a connected universe reported work waiting on its user"


@pytest.mark.parametrize("remaining", [9, 0, 10, 50, None])
def test_low_compute_is_derived_each_read(rig, monkeypatch, remaining):
    from tinyassets import request_budget as budgets
    from tinyassets.api.pending_requests import _connect_llm_request
    from tinyassets.storage.pending_requests import list_pending

    monkeypatch.setattr("tinyassets.api.pending_requests._serving_llm_bound",
                        lambda *a, **k: True)
    pool = None if remaining is None else budgets.PooledBudget(((
        "source", budgets.RequestBudget(50 - remaining, 50, "Source", "UTC"),
    ),))
    monkeypatch.setattr(budgets, "budget_for_rail", lambda *args: pool)
    original = _connect_llm_request(connected=True)
    for _ in range(2):
        cards = [row for row in _rail() if row["request_id"] == "sys_connect_llm"]
        assert len(cards) == 1
        card = cards[0]
        if remaining is not None and remaining < 10:
            assert card["status"] == "pending"
            assert f"({remaining} left)" in card["suggestion"]
            assert "local free-request estimate" in card["suggestion"]
            assert "Your account may have a higher allowance; work can continue" in (
                card["suggestion"]
            )
            assert "Connect another free AI source" in card["suggestion"]
        else:
            assert card["status"] == original["status"]
            assert card.get("suggestion") == original.get("suggestion")
        assert card["body"] == original["body"]
        assert card["action"] == original["action"]
        assert not any(row["request_id"] == "sys_connect_llm"
                       for row in list_pending(rig / "u-owner"))
    pool = budgets.PooledBudget((("source", budgets.RequestBudget(0, 50, "Source", "UTC")),))
    assert _rail()[-1]["status"] == "optional"


# --------------------------------------------------------------------------- #
# The page: the heading stops asking when only optional rows remain.
# --------------------------------------------------------------------------- #


_CONNECTED = {"request_id": "sys_connect_llm", "kind": "LLM", "sticky": False,
              "status": "optional", "title": "Connect another LLM",
              "body": "Add another model source.", "fields": [],
              "action": {"type": "connect", "use": "model",
                         "setup": {"shapes": ["api_key", "local"]}}}
_ASK = {"request_id": "req_b", "kind": "API", "status": "pending", "title": "Key please",
        "fields": [], "action": {"type": "answer"}}


def _run_head(rows, extra=""):
    html, _ = render_app_html()
    source = "\n".join(_js_function(html, name) for name in (
        "isSetupRequest", "isOptionalRequest", "forgetFinishedSetup", "foldedModelAccess",
        "renderRail", "connectBody"))
    shapes = html[html.index("  const ConnectShapes={"):
                  html.index("  // A declared model list needs")]
    script = (_RAIL_HARNESS.replace("__SOURCE__", source + "\n" + shapes)
              + "\nrenderRail(" + json.dumps(rows) + ");\n" + extra + r"""
const tabs=host.children.map(t=>({text:text(t),
  optional:(t.className||'').split(' ').includes('rtab--optional'),
  hasPanel:t.children.some(c=>c.children.includes($('connect-panel')))}));
console.log(JSON.stringify({tabs,head:$('rail-head').textContent,
  railHidden:$('request-rail').hidden,otherOpen:$('connect-other').open,
  panelHidden:$('connect-panel').hidden,railOpen}));
""")
    run = subprocess.run([_NODE, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_the_heading_stops_asking_when_only_optional_rows_remain():
    out = _run_head([_CONNECTED])
    assert out["head"] == "Nothing waiting on you", "a connected universe was still asked"
    assert out["railHidden"] is False, "the rail vanished, so 'Add a key yourself' went with it"
    assert out["tabs"] == [], (
        "an item with nothing to accept, deny or clear was left in 'Waiting on you'"
    )


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_one_real_ask_still_makes_the_heading_ask():
    """A real ask renders; the optional entry beside it still does not."""
    out = _run_head([_ASK, _CONNECTED])
    assert out["head"] == "Waiting on you"
    assert [tab["optional"] for tab in out["tabs"]] == [False]
    assert "Key please" in out["tabs"][0]["text"]


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_an_older_page_payload_without_a_status_is_still_treated_as_an_ask():
    """A row with no ``status`` is a real ask; never guess it optional."""
    out = _run_head([{k: v for k, v in _ASK.items() if k != "status"}])
    assert out["head"] == "Waiting on you"
    assert out["tabs"][0]["optional"] is False


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_the_optional_row_renders_when_the_model_picker_opens_it():
    """``openConnectRequest`` sets ``railOpen``; that is the picker's route in."""
    out = _run_head([_CONNECTED], "railOpen='sys_connect_llm';renderRail(railCache);")
    assert len(out["tabs"]) == 1, "the model picker's route had no card to show"
    assert out["tabs"][0]["hasPanel"] is True, "the optional row could not be opened"
    assert out["tabs"][0]["optional"] is True


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_closing_the_opened_optional_row_removes_it_from_the_rail():
    """Tapping its header IS the dismiss a derived entry cannot otherwise have."""
    out = _run_head(
        [_CONNECTED],
        "railOpen='sys_connect_llm';renderRail(railCache);"
        "railOpen=null;renderRail(railCache);",
    )
    assert out["tabs"] == [], "the card the user closed stayed on the rail"
    assert out["panelHidden"] is True, "the setup panel was left on screen"


_BLOCKING = {"request_id": "sys_connect_llm", "kind": "LLM", "sticky": True,
             "status": "pending", "title": "Connect the model your universe runs on",
             "body": "Your universe needs a model to think with.", "fields": [],
             "action": {"type": "connect", "use": "model", "setup": {
                 "primary": {"preset_id": "guided_models_v1",
                             "label": "Continue with Example", "name": "Example",
                             "manage_url": "https://provider.example/keys",
                             "manual_key": True},
                 "shapes": ["api_key", "local"]}}}


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_the_finished_setup_card_does_not_carry_its_expansion_across_the_connect():
    """The exact screen the founder landed on returning from OpenRouter sign-in.

    2026-09-25: the rail showed "Connect another LLM" FULLY EXPANDED, body and
    all, with "Other ways to connect" open over the Model URL / API key / Model
    id fields and a Connect button — for a universe that was already connected.
    The setup card is expanded by ID (``openConnectRequest`` sets ``railOpen``),
    and that id does not change when the connect succeeds.
    """
    # The in-progress setup, opened the way openConnectRequest opens it, with
    # the shapes disclosure expanded as ConnectShapes leaves it.
    out = _run_head(
        [_BLOCKING],
        "railOpen='sys_connect_llm';$('connect-other').open=true;renderRail(railCache);"
        # ...then the callback returns and the rail refreshes: now connected.
        "renderRail(" + json.dumps([_CONNECTED]) + ");",
    )
    assert out["tabs"] == [], "the finished setup card stayed on the rail"
    assert out["panelHidden"] is True, "the setup panel was still on screen"
    assert out["otherOpen"] is False, "'Other ways to connect' stayed open"
    assert out["railOpen"] is None
    assert out["head"] == "Nothing waiting on you"


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_a_still_unpowered_setup_keeps_its_expansion_across_a_refresh():
    """Only the transition resets it. A rail refresh mid-setup must not."""
    # The user's open is a TAP: the browser flips `open`, then fires `toggle`.
    # Setting the property alone stopped counting on 2026-09-26, when the renderer
    # began deriving the element's state from the remembered tap instead of reading
    # it back off a node that survives every refresh — a node whose stale `open` was
    # why an unpowered account met the manual form already unfolded under the
    # one-tap button. The assertion is unchanged: a refresh must not close it.
    out = _run_head(
        [_BLOCKING],
        "railOpen='sys_connect_llm';renderRail(railCache);"
        "$('connect-other').open=true;$('connect-other').ontoggle();"
        "renderRail(" + json.dumps([_BLOCKING]) + ");",
    )
    assert out["tabs"][0]["hasPanel"] is True, "the setup collapsed mid-connect"
    assert out["otherOpen"] is True, "the user's open shape picker closed itself"


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_a_powered_user_can_still_open_connect_another_after_the_transition():
    """The reset is one-shot: adding a SECOND source still opens on demand."""
    out = _run_head(
        [_BLOCKING],
        "railOpen='sys_connect_llm';renderRail(railCache);"
        "renderRail(" + json.dumps([_CONNECTED]) + ");"
        # The user now taps "Connect another LLM" themselves.
        "railOpen='sys_connect_llm';renderRail(railCache);",
    )
    assert out["tabs"][0]["hasPanel"] is True, "an optional row could no longer be opened"
