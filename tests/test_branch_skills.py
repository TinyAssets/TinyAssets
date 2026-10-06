"""Branch-carried skill snapshots.

Users should be able to create, remix, or copy skills and attach them to
Branches through the chatbot-facing branch authoring surface.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tinyassets.branches import BranchDefinition, EdgeDefinition, NodeDefinition
from tinyassets.daemon_server import (
    get_branch_definition,
    initialize_author_server,
    save_branch_definition,
)


@pytest.fixture
def branch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "skill-tester")
    # `UNIVERSE_SERVER_USER` alone does not reach branch WRITE actions:
    # they resolve the caller via `_request_branch_actor()`, which is
    # credential-derived and documented "never an env actor". Without a
    # bound request subject they return "Authenticated branch subject
    # required." before parsing anything, and every later assertion dies on
    # a KeyError that hides the real cause.
    authenticate_request("skill-tester")
    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us, base
    importlib.reload(us)


def _call(us, action: str, **kwargs):
    return json.loads(us._extensions_impl(action=action, **kwargs))


def _minimal_branch_spec(**overrides):
    spec = {
        "name": "Skill branch",
        "description": "Uses copied skills as branch context.",
        "entry_point": "draft",
        "node_defs": [
            {
                "node_id": "draft",
                "display_name": "Draft",
                "prompt_template": "Draft with {topic}.",
                "input_keys": ["topic"],
            }
        ],
        "edges": [
            {"from": "START", "to": "draft"},
            {"from": "draft", "to": "END"},
        ],
        "state_schema": [{"name": "topic", "type": "str"}],
    }
    spec.update(overrides)
    return spec


def test_branch_definition_round_trips_skill_snapshots():
    branch = BranchDefinition(
        name="Skill carrier",
        entry_point="draft",
        node_defs=[
            NodeDefinition(
                node_id="draft",
                display_name="Draft",
                prompt_template="Draft with {topic}.",
                input_keys=["topic"],
            )
        ],
        edges=[
            EdgeDefinition(from_node="START", to_node="draft"),
            EdgeDefinition(from_node="draft", to_node="END"),
        ],
        state_schema=[{"name": "topic", "type": "str"}],
        skills=[
            {
                "skill_id": "senior-review",
                "name": "Senior Review",
                "body": "Review for correctness, simplicity, and fit.",
                "source_url": "https://example.com/skills/senior-review.md",
                "tags": ["review", "quality"],
            }
        ],
    )

    restored = BranchDefinition.from_dict(branch.to_dict())

    assert restored.skills == [
        {
            "skill_id": "senior-review",
            "name": "Senior Review",
            "body": "Review for correctness, simplicity, and fit.",
            "source_url": "https://example.com/skills/senior-review.md",
            "tags": ["review", "quality"],
        }
    ]


def test_save_and_get_preserves_branch_skills(tmp_path: Path):
    initialize_author_server(tmp_path)
    branch = BranchDefinition(
        name="Persistent skills",
        skills=[
            {
                "skill_id": "internet-rubric",
                "name": "Internet Rubric",
                "body": "A copied rubric snapshot.",
                "source_url": "https://example.com/rubric.md",
            }
        ],
    )

    saved = save_branch_definition(tmp_path, branch_def=branch.to_dict())
    retrieved = get_branch_definition(tmp_path, branch_def_id=saved["branch_def_id"])

    assert retrieved["skills"] == [
        {
            "skill_id": "internet-rubric",
            "name": "Internet Rubric",
            "body": "A copied rubric snapshot.",
            "source_url": "https://example.com/rubric.md",
        }
    ]


def test_build_branch_accepts_skill_snapshots(branch_env):
    us, _base = branch_env
    spec = _minimal_branch_spec(
        skills=[
            {
                "name": "Copied prompt style",
                "body": "Use terse acceptance criteria before coding.",
                "source_url": "https://example.com/cool-skill.md",
                "source_note": "User pasted this from the internet.",
            }
        ]
    )

    built = _call(us, "build_branch", spec_json=json.dumps(spec))
    got = _call(us, "get_branch", branch_def_id=built["branch_def_id"])

    assert built["status"] == "built"
    assert built["skill_count"] == 1
    assert got["skills"][0]["skill_id"] == "copied-prompt-style"
    assert got["skills"][0]["source_url"] == "https://example.com/cool-skill.md"
    assert got["skills"][0]["body"] == "Use terse acceptance criteria before coding."


def test_patch_branch_can_add_update_and_remove_skill_snapshots(branch_env):
    us, _base = branch_env
    built = _call(us, "build_branch", spec_json=json.dumps(_minimal_branch_spec()))
    branch_id = built["branch_def_id"]

    added = _call(
        us,
        "patch_branch",
        branch_def_id=branch_id,
        changes_json=json.dumps([
            {
                "op": "add_skill",
                "skill": {
                    "name": "Review checklist",
                    "body": "Check tests, code shape, and live proof.",
                },
            }
        ]),
    )
    updated = _call(
        us,
        "patch_branch",
        branch_def_id=branch_id,
        changes_json=json.dumps([
            {
                "op": "update_skill",
                "skill_id": "review-checklist",
                "body": "Check tests, code shape, live proof, and rollback.",
            }
        ]),
    )
    removed = _call(
        us,
        "patch_branch",
        branch_def_id=branch_id,
        changes_json=json.dumps([
            {"op": "remove_skill", "skill_id": "review-checklist"}
        ]),
    )
    got = _call(us, "get_branch", branch_def_id=branch_id)

    assert added["status"] == "patched"
    assert added["post_patch"]["skill_count"] == 1
    assert updated["status"] == "patched"
    assert "skills" in updated["patched_fields"]
    assert removed["status"] == "patched"
    assert removed["post_patch"]["skill_count"] == 0
    assert got["skills"] == []


def test_patch_branch_rejects_skill_without_body(branch_env):
    us, _base = branch_env
    built = _call(us, "build_branch", spec_json=json.dumps(_minimal_branch_spec()))

    result = _call(
        us,
        "patch_branch",
        branch_def_id=built["branch_def_id"],
        changes_json=json.dumps([
            {
                "op": "add_skill",
                "skill": {
                    "name": "Broken copied skill",
                    "source_url": "https://example.com/missing-body.md",
                },
            }
        ]),
    )

    assert result["status"] == "rejected"
    assert result["errors"][0]["error"] == "skill body is required"
