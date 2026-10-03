from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tinyassets.auth.provider import Identity
from tinyassets.daemon_server import grant_universe_access
from tinyassets.universe_soul import read_pinned_universe_soul


@pytest.fixture
def us(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    # BIND the identity rather than stubbing the function that reads it. The
    # stub reached `current_identity` but not the contextvar the permission
    # helpers read, so creation granted the universe to one principal and the
    # ACL check asked about another -- `universe_access_denied` on a universe
    # the caller had just made.
    identity = Identity(
        user_id="soul-test-user",
        username="soul-test-user",
        capabilities=[
            "tinyassets.universe.read",
            "tinyassets.universe.write",
            "tinyassets.universe.admin",
            "tinyassets.universe.create",
            "tinyassets.extensions.read",
            "tinyassets.extensions.write",
        ],
    )
    from tinyassets.auth import middleware as _mw

    _mw._current_identity.set(identity)
    import tinyassets.api.universe as module

    importlib.reload(module)
    yield module
    importlib.reload(module)


def _mkuniverse(base: Path, uid: str) -> Path:
    udir = base / uid
    udir.mkdir(parents=True, exist_ok=True)
    return udir


def test_set_premise_writes_versioned_soul_profile(us):
    base = Path(us._base_path())
    _mkuniverse(base, "u1")

    result = json.loads(us._action_set_premise(
        universe_id="u1",
        text="A wandering lab studies civic memory.",
    ))

    assert result["status"] == "updated"
    assert result["soul"]["path"] == "soul.md"
    assert result["soul"]["schema_version"] == 1
    assert result["soul"]["versions_dir"] == "soul_versions"
    soul_md = (base / "u1" / "soul.md").read_text(encoding="utf-8")
    assert "# Universe Soul" in soul_md
    assert "## Purpose" in soul_md
    assert "A wandering lab studies civic memory." in soul_md
    assert "## Edit Authority" in soul_md
    assert "soul.edit" in soul_md
    assert "- Loop branch: _None recorded._" in soul_md
    version = base / "u1" / "soul_versions" / "0001.md"
    assert version.exists()
    assert version.read_text(encoding="utf-8") == soul_md


def test_read_premise_falls_back_to_soul_purpose(us):
    base = Path(us._base_path())
    udir = _mkuniverse(base, "u1")
    us._action_set_premise(universe_id="u1", text="A solar archive wakes up.")
    (udir / "PROGRAM.md").unlink()

    result = json.loads(us._action_read_premise(universe_id="u1"))

    assert result["premise"] == "A solar archive wakes up."
    assert result["source"] == "soul.md"
    assert result["soul"]["schema_version"] == 1


def test_read_pinned_universe_soul_reports_version_hash(us):
    base = Path(us._base_path())
    _mkuniverse(base, "u1")
    us._action_set_premise(
        universe_id="u1",
        text="A civic-memory workflow checks every source.",
    )

    pinned = read_pinned_universe_soul(base / "u1")

    assert pinned is not None
    assert pinned.version_id == "soul_versions/0001.md"
    assert len(pinned.content_sha256) == 64
    context = pinned.context()
    assert context["purpose"] == "A civic-memory workflow checks every source."
    assert context["version_id"] == "soul_versions/0001.md"
    assert context["identity_boundary"] == (
        "Command center soul guides this context only; it does not change "
        "the actor identity or user memory scope."
    )


def test_create_universe_always_writes_soul_profile(us):
    base = Path(us._base_path())

    result = json.loads(us._action_create_universe(
        universe_id="fresh-uni",
        text="A seedling kingdom.",
    ))

    assert result["status"] == "created"
    assert result["has_soul"] is True
    assert result["soul"]["path"] == "soul.md"
    assert (base / "fresh-uni" / "soul.md").exists()
    assert (base / "fresh-uni" / "PROGRAM.md").read_text(
        encoding="utf-8",
    ) == "A seedling kingdom."


def test_create_universe_without_premise_still_has_thin_soul(us):
    base = Path(us._base_path())

    result = json.loads(us._action_create_universe(universe_id="thin-uni"))

    assert result["status"] == "created"
    assert result["has_premise"] is False
    assert result["has_soul"] is True
    assert result["loop_dispatch"] == {
        "source": "soul.md",
        "branch_def_id": "",
        "declared": False,
    }
    udir = base / "thin-uni"
    soul_md = (udir / "soul.md").read_text(encoding="utf-8")
    # universe-creation D4/D5: a blank universe gets the OKF soul bundle, not a
    # premise/loop-populated thin soul. soul.md is OKF-shaped (frontmatter
    # type) and, with no premise, carries no ## Purpose / loop line.
    assert soul_md.startswith("---\ntype: Universe Soul")
    assert "# Command Center Soul" in soul_md
    assert "## Purpose" not in soul_md
    assert "Loop branch:" not in soul_md
    # The linked OKF baseline exists; the removed junk files do not.
    assert (udir / "identity.md").is_file()
    assert (udir / "soul_versions" / "0001.md").is_file()
    assert not (udir / "notes.json").exists()
    assert not (udir / "activity.log").exists()
    assert not (udir / "PROGRAM.md").exists()


def test_creation_adds_unnamed_serial_index_row(us):
    # universe-creation task 5.3: a blank universe's index row is keyed by its
    # immutable serial and carries no invented self-name (display == the id).
    from tinyassets.daemon_server import get_universe

    base = Path(us._base_path())
    created = json.loads(us._action_create_universe())
    uid = created["universe_id"]
    row = get_universe(base, universe_id=uid)
    assert row["universe_id"] == uid
    assert row["display_name"] == uid


def test_learned_name_projects_onto_immutable_index_row(us):
    # universe-creation task 5.3: an accepted learned-name change in identity.md
    # updates only the display-name projection for the same immutable row.
    from tinyassets.daemon_server import get_universe

    base = Path(us._base_path())
    uid = json.loads(us._action_create_universe())["universe_id"]

    out = json.loads(us._action_soul_edit(
        universe_id=uid,
        inputs_json=json.dumps({
            "name": "Aurelia",
            "source": "founder",
            "context": "the universe settled on its own name",
        }),
    ))
    assert out.get("error") is None, out
    assert out["persona_name"] == "Aurelia"

    row = get_universe(base, universe_id=uid)
    assert row["universe_id"] == uid       # immutable key unchanged
    assert row["display_name"] == "Aurelia"  # display projection updated


def test_create_universe_ledger_uses_generated_id():
    # Regression: a server-generated universe_id must reach the ledger row.
    # The extractor reads kwargs (no universe_id for a generated create), so it
    # must fall back to the id in the handler result.
    from tinyassets.api.universe import _extract_create_universe

    target, summary, payload = _extract_create_universe(
        {"text": ""},
        {"universe_id": "u-01generatedserial00000000", "status": "created"},
    )
    assert target == "u-01generatedserial00000000"
    assert "u-01generatedserial00000000" in summary


def test_create_universe_records_declared_loop_branch(us):
    base = Path(us._base_path())

    result = json.loads(us._action_create_universe(
        universe_id="workflow-uni",
        text="A civic workflow lab.",
        branch_def_id="workflow:review_loop",
    ))

    assert result["status"] == "created"
    assert result["soul"]["loop_branch_def_id"] == "workflow:review_loop"
    assert result["loop_dispatch"] == {
        "source": "soul.md",
        "branch_def_id": "workflow:review_loop",
        "declared": True,
    }
    soul_md = (base / "workflow-uni" / "soul.md").read_text(encoding="utf-8")
    assert "- Loop branch: workflow:review_loop" in soul_md
    pinned = read_pinned_universe_soul(base / "workflow-uni")
    assert pinned is not None
    assert pinned.context()["loop_branch_def_id"] == "workflow:review_loop"


def test_submit_request_uses_soul_declared_loop_branch(us):
    base = Path(us._base_path())
    us._action_create_universe(
        universe_id="loop-uni",
        text="A civic workflow lab.",
        branch_def_id="workflow:review_loop",
    )

    result = json.loads(us._action_submit_request(
        universe_id="loop-uni",
        text="Review this packet.",
        request_type="general",
    ))

    assert "status" in result, result
    assert result["status"] == "pending", result
    assert result["loop_dispatch"] == {
        "source": "soul.md",
        "has_soul": True,
        "branch_def_id": "workflow:review_loop",
    }
    queue = json.loads((base / "loop-uni" / "branch_tasks.json").read_text(
        encoding="utf-8",
    ))
    assert queue[0]["branch_def_id"] == "workflow:review_loop"
    assert queue[0]["inputs"]["loop_dispatch"]["source"] == "soul.md"


def test_submit_request_rejects_souled_universe_without_declared_loop(us):
    base = Path(us._base_path())
    us._action_create_universe(universe_id="thin-uni")

    result = json.loads(us._action_submit_request(
        universe_id="thin-uni",
        text="Do work.",
        request_type="general",
    ))

    assert result["error"] == "universe_loop_not_declared"
    assert result["loop_dispatch"]["source"] == "soul.md"
    assert not (base / "thin-uni" / "requests.json").exists()
    assert not (base / "thin-uni" / "branch_tasks.json").exists()


def test_submit_request_rejects_soulless_universe_without_legacy_marker(us):
    base = Path(us._base_path())
    empty = _mkuniverse(base, "empty-uni")
    assert not (empty / "soul.md").exists()

    result = json.loads(us._action_submit_request(
        universe_id="empty-uni",
        text="Do undeclared work.",
        request_type="general",
    ))

    assert result["error"] == "universe_loop_not_declared"
    assert result["loop_dispatch"]["source"] == "no_soul_no_loop_declared"
    assert result["loop_dispatch"]["branch_def_id"] == ""
    assert not (empty / "requests.json").exists()
    assert not (empty / "branch_tasks.json").exists()


def test_submit_request_legacy_program_no_soul_keeps_named_compat_loop(us):
    base = Path(us._base_path())
    legacy = _mkuniverse(base, "legacy-uni")
    grant_universe_access(
        base,
        universe_id="legacy-uni",
        actor_id="soul-test-user",
        permission="write",
        granted_by="soul-test-user",
    )
    (legacy / "PROGRAM.md").write_text("A legacy fantasy premise.", encoding="utf-8")
    assert not (legacy / "soul.md").exists()

    result = json.loads(us._action_submit_request(
        universe_id="legacy-uni",
        text="Do legacy work.",
        request_type="general",
    ))

    assert result["status"] == "pending"
    assert result["loop_dispatch"]["source"] == "legacy_program_fantasy_compat"
    queue = json.loads((legacy / "branch_tasks.json").read_text(encoding="utf-8"))
    assert queue[0]["branch_def_id"] == "fantasy_author:universe_cycle_wrapper"
