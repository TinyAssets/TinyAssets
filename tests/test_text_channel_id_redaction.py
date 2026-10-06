"""#58 — Raw run_id / branch_def_id / goal_id must never appear in the
text channel of MCP tool returns. Phone users read `text` verbatim
through Claude.ai; IDs belong in structuredContent for scripts.

Covers the audit surface documented in the task:
run_branch, get_run, list_runs, build_branch, patch_branch,
rollback_node, create_branch, get_branch, list_branches, goals.propose,
goals.bind, plus get_run_output, judge_run, and get_node_output.
"""

from __future__ import annotations

import importlib
import json

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401

#: Runs are attributed to this universe; registered + ACL-granted in `env`.
REDACTION_UNIVERSE = "redaction-universe"

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def env(tmp_path, monkeypatch, authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    monkeypatch.setenv("_FORCE_MOCK", "true")
    # Branch mutation requires a credential-derived subject, and the scope
    # check is per-family: this file drives `goals` as well as `extensions`.
    # Nothing here asserts a *scope* refusal, so granting the writes these
    # tests perform costs no assertion strength. `extensions.costly` and
    # `goals.costly` stay withheld.
    authenticate_request("tester", capabilities=[
        "tinyassets.extensions.read",
        "tinyassets.extensions.write",
        "tinyassets.extensions.admin",
        # `extensions.costly` IS needed here: this file exists to prove run
        # IDs are redacted from the text channel, so it has to actually
        # execute `run_branch`. Nothing in it asserts a scope refusal.
        "tinyassets.extensions.costly",
        "tinyassets.goals.read",
        "tinyassets.goals.write",
    ])

    # Runs are owned by a universe now: without `universe_id` run_branch
    # returns `branch_run_requires_universe`, and with an unregistered id it
    # returns `universe_access_denied` — the ACL grant is the half that is
    # easy to miss.
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        grant_universe_access,
    )

    udir = base / REDACTION_UNIVERSE
    udir.mkdir(parents=True, exist_ok=True)
    ensure_universe_registered(
        base, universe_id=REDACTION_UNIVERSE, universe_path=udir,
    )
    grant_universe_access(
        base,
        universe_id=REDACTION_UNIVERSE,
        actor_id="tester",
        permission="write",
        granted_by="env",
    )
    from tinyassets import universe_server as us
    provider_calls = importlib.import_module("tinyassets.providers.call")

    importlib.reload(us)
    monkeypatch.setattr(
        provider_calls,
        "call_provider",
        lambda prompt, _system="", **_kwargs: f"fixture:{prompt}",
    )
    yield us, base
    importlib.reload(us)


def _call(us, tool, action, **kwargs):
    fn = getattr(us, f"_{tool}_impl")
    return json.loads(fn(action=action, **kwargs))


# ─── build/patch/run/get text-channel invariants ──────────────────────────


def test_build_branch_text_hides_branch_def_id(env):
    us, _ = env
    spec = {
        "name": "phone-friendly",
        "entry_point": "n1",
        "state_schema": [{"name": "x", "type": "str", "default": ""}],
        "node_defs": [{
            "node_id": "n1",
            "display_name": "First node",
            "phase": "custom",
            "prompt_template": "hi",
            "input_keys": [],
            "output_keys": ["x"],
        }],
        "edges": [
            {"from_node": "START", "to_node": "n1"},
            {"from_node": "n1", "to_node": "END"},
        ],
    }
    result = _call(us, "extensions", "build_branch",
                   spec_json=json.dumps(spec))
    assert result["status"] == "built"
    bid = result["branch_def_id"]
    assert bid
    assert bid not in result["text"]
    assert "phone-friendly" in result["text"]


# ─── judgments + rollback ─────────────────────────────────────────────────


# ─── goals ────────────────────────────────────────────────────────────────


def test_goal_propose_text_hides_goal_id(env):
    us, _ = env
    result = _call(us, "goals", "propose",
                   name="Paper: long-horizon eval",
                   description="Phase 6 candidate Goal.")
    assert result["status"] == "proposed"
    gid = result["goal"]["goal_id"]
    assert gid
    assert gid not in result["text"]
    assert "Paper: long-horizon eval" in result["text"]
