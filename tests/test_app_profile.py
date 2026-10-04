"""D4a: the profile GET reads only the authenticated owner's own agent."""

import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from tinyassets import onboarding
from tinyassets.api import helpers, status
from tinyassets.auth import middleware
from tinyassets.storage import pending_requests

_ACTIVE_TURN = status._universe_active_turn


@pytest.fixture
def profile(tmp_path, monkeypatch):
    universe = tmp_path / "u-home"
    universe.mkdir()
    (universe / "identity.md").write_text("---\nname: Lumen\n---\n", encoding="utf-8")
    (universe / "AGENTS.md").write_text("## Responsibility\n" + "Report. " * 100,
                                       encoding="utf-8")
    identity = SimpleNamespace(user_id="owner")
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(middleware, "current_identity_or_none", lambda: identity)
    monkeypatch.setattr(middleware, "current_identity", lambda: identity)
    monkeypatch.setattr(onboarding, "_read_home",
                        lambda who: "u-home" if who.user_id == "owner" else "")
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(status, "_universe_active_turn", lambda _u: None)
    return universe, identity


def _get(query=b""):
    request = Request({"type": "http", "method": "GET", "path": "/app/profile",
                       "query_string": query, "headers": []})
    response = asyncio.run(onboarding._handle_profile(request))
    return response, json.loads(response.body)


def test_owner_gets_the_profile_fields(profile):
    response, doc = _get()
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert doc == {"name": "Lumen", "responsibility": ("Report. " * 100)[:500],
                   "status": "idle", "model": "", "agent_id": "main"}


def test_another_user_cannot_name_the_owners_home(profile):
    _universe, identity = profile
    identity.user_id = "stranger"
    response, doc = _get(b"universe_id=u-home")
    assert response.status_code == 404 and doc == {"error": "no_home"}


def test_no_identity_is_refused(profile, monkeypatch):
    monkeypatch.setattr(middleware, "current_identity_or_none", lambda: None)
    assert _get()[0].status_code == 401


def _pending(universe):
    assert pending_requests.create_request(
        universe, kind="approval", title="Approve a report", body="Send?", fields=[],
        action={}, dedupe_key="report",
    )


def test_pending_request_waits_on_the_owner(profile):
    universe, _identity = profile
    _pending(universe)
    assert _get()[1]["status"] == "waiting_on_you"


@pytest.mark.parametrize("state", ["ready", "inference_started", "native_started", "tools_pending"])
def test_live_turn_takes_precedence_over_a_pending_request(profile, monkeypatch, state):
    universe, _identity = profile
    _pending(universe)
    def active(u):
        assert u == universe
        return {"state": state, "model": "Serving model"}

    monkeypatch.setattr(status, "_universe_active_turn", active)
    doc = _get()[1]
    assert doc["status"] == "working" and doc["model"] == "Serving model"


def test_stale_turn_is_not_working(profile, monkeypatch):
    monkeypatch.setattr(status, "_universe_active_turn",
                        lambda _u: {"state": "ready", "stale": True, "model": "Old model"})
    assert _get()[1]["status"] == "idle"
    assert _get()[1]["model"] == ""


def test_profile_reads_a_real_live_journal_turn(profile, monkeypatch):
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.storage.agent_turn_boot import BOOT
    from tinyassets.storage.agent_turn_journal import AgentTurnJournal

    universe, _identity = profile
    set_founder_home(universe.parent, founder_sub="owner", universe_id=universe.name,
                     platform_generated=True)
    journal = AgentTurnJournal(universe.parent)
    turn = journal.create("owner", universe.name, prompt="My private request", system="s")
    BOOT.note_round(universe.name, turn.turn_id, round=1, model="Serving model")
    monkeypatch.setattr(status, "_universe_active_turn", _ACTIVE_TURN)
    try:
        doc = _get()[1]
        assert doc["status"] == "working" and doc["model"] == "Serving model"
        assert "My private request" not in json.dumps(doc)
    finally:
        BOOT.release(universe.name, turn.turn_id)


def test_unreadable_turn_is_not_reported_as_idle(profile, monkeypatch):
    monkeypatch.setattr(status, "_universe_active_turn", lambda _u: {"state": "unreadable"})
    assert _get()[0].status_code == 503


@pytest.mark.parametrize("switch_account", [False, True])
def test_account_header_loads_text_and_discards_another_accounts_response(tmp_path, switch_account):
    from tests.test_onboarding_app import _js_function

    node = shutil.which("node")
    if not node:
        pytest.skip("owner=codex runs-in=cloud-prepush-oracle Node runs the page's own source")
    page, _csp = onboarding.render_app_html()
    assert "loadProfile();" in _js_function(page, "showAccount")
    script = tmp_path / "profile.js"
    script.write_text(
        "const MCP={_loginEpoch:1}; let queueOwner='owner',queueScope='u-home';\n"
        "const els={}; function $(id){return els[id]||(els[id]={textContent:''});}\n"
        "function authHeaders(){return {Authorization:'Bearer owner'};}\n"
        "async function fetch(url,init){\n"
        "if(url!=='/app/profile'||init.credentials!=='same-origin'||"
        "init.headers.Authorization!=='Bearer owner')throw Error('wrong request');\n"
        + ("MCP._loginEpoch++; queueOwner='other';\n" if switch_account else "")
        + "return {ok:true,json:async()=>({name:'<b>Lumen</b>',responsibility:'Reports',"
        "status:'waiting_on_you'})};}\n"
        + _js_function(page, "loadProfile")
        + "\n(async()=>{await loadProfile();console.log(JSON.stringify(els));})();\n",
        encoding="utf-8",
    )
    proc = subprocess.run([node, str(script)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["profile-name"]["textContent"] == ("" if switch_account else "<b>Lumen</b>")
    if not switch_account:
        assert doc["profile-responsibility"]["textContent"] == "Reports"
        assert doc["profile-status"]["textContent"] == "Waiting on you"
