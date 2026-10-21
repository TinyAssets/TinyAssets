"""Talking to one of your agents directly (harness §4.18, the multi-agent invariant).

A command center holds the main agent and any custom agent the owner bound
into it. ``converse(agent_id=...)`` runs the turn AS that agent: its own
instructions, its own thread, the same shared brain; the main agent sees what
the others said; and an id that is not the owner's own agent there is refused
by name before anything runs.

Only the provider call is stubbed. Auth, the ACL, the binding store, persona
assembly, the conversation store and learning persistence are the real ones.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tinyassets.universe_intelligence as ui
import tinyassets.universe_server as us
from tinyassets.universe_bundle import seed_okf_bundle

OWNER = "founder-1"
WEAVER_RULE = "Help plan and critique a rigorous research paper."
CHECK_RULE = "Verify every claim against its cited source."


@pytest.fixture(autouse=True)
def _reset_auth():
    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import DevAuthProvider

    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def _become(base: Path, actor: str, uid: str) -> None:
    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import AuthProvider, Identity
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(base, universe_id=uid, actor_id=actor, permission="admin")
    identity = Identity(user_id=actor, username=actor, capabilities=[
        "tinyassets.universe.read", "tinyassets.universe.write", "tinyassets.universe.admin",
    ])

    class _Static(AuthProvider):
        def resolve_token(self, token):
            return identity if token == "ok" else None

        def is_auth_required(self):
            return True

        def register_client(self, metadata):
            return {"client_id": "test-client", **metadata}

        def create_authorization(self, *_a, **_kw):
            raise NotImplementedError

        def exchange_code(self, *_a, **_kw):
            raise NotImplementedError

    set_provider(_Static())
    auth_middleware("ok")


def _universe(base: Path, uid: str) -> Path:
    from tests.conftest import own_universe
    from tinyassets.daemon_server import ensure_universe_registered

    udir = base / uid
    udir.mkdir()
    own_universe(base, uid)
    seed_okf_bundle(udir, purpose="To help my founder bring their projects to life.")
    ensure_universe_registered(base, universe_id=uid, universe_path=udir)
    return udir


def _agent(base: Path, uid: str, owner: str, name: str, **settings) -> str:
    """One of the owner's agents, as its files: ``agents/<slug>/`` with its id kept."""
    from tinyassets.command_center_packages import slug

    agent_id = "agent_" + slug(name).replace("-", "_")
    folder = base / uid / "agents" / slug(name)
    folder.mkdir(parents=True)
    (folder / "AGENTS.md").write_text(
        "## identity (soul)\n\n" + WEAVER_RULE + "\n\n## fact_check (skill)\n\n"
        + CHECK_RULE + "\n", encoding="utf-8")
    (folder / "settings.yaml").write_text(json.dumps({
        "schema_version": 1,
        "agent": {"id": agent_id, "name": name, "owner": owner, **settings}}),
        encoding="utf-8")
    return agent_id


class _Provider:
    """The model: records every call, answers the reply and the extraction."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.lesson: dict = {}

    def __call__(self, prompt, system="", **_kw):
        self.calls.append((prompt, system))
        if "strict JSON" in system:
            return json.dumps(self.lesson)
        return "reply-" + str(len(self.writer_calls()))

    def writer_calls(self):
        return [call for call in self.calls if "strict JSON" not in call[1]]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    udir = _universe(tmp_path, "u-home")
    _universe(tmp_path, "u-other")
    _become(tmp_path, OWNER, "u-home")
    provider = _Provider()
    monkeypatch.setattr(ui, "text_sync", provider)
    weaver = _agent(tmp_path, "u-home", OWNER, "Evidence Weaver")
    return {"base": tmp_path, "udir": udir, "provider": provider, "weaver": weaver}


def _converse(**kwargs) -> dict:
    return json.loads(us.converse(graph_id="u-home", **kwargs))











































