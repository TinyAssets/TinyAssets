"""scripts/data_nlink_census.py: counts what the nlink reader gate would refuse."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptors")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import data_nlink_census as census  # noqa: E402


def _universe(root: Path, name: str) -> Path:
    tree = root / name
    tree.mkdir()
    (tree / "universe.json").write_text("{}")
    return tree


def _snapshot(root: Path):
    return sorted((str(p), p.lstat().st_mtime_ns, p.lstat().st_nlink)
                  for p in root.rglob("*"))


def test_clean_tree_reports_nothing_and_exits_zero(tmp_path, capsys):
    tree = _universe(tmp_path, "alice")
    (tree / "soul.md").write_text("x")
    assert census.main(["--root", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["counts"]["multi_link_inodes"] == 0
    assert report["counts"]["regular_names"] == 2


def test_multi_link_files_are_classified_redacted_and_untouched(tmp_path, capsys):
    alice = _universe(tmp_path, "alice")
    bob = _universe(tmp_path, "u-bob")
    ws = alice / ".agent-workspace"
    ws.mkdir()
    (ws / "identity.md").write_text("me")
    os.link(ws / "identity.md", alice / "identity.md")  # interrupted promotion
    (ws / "secret-plan.txt").write_text("x")
    os.link(ws / "secret-plan.txt", ws / "copy.txt")  # agent `ln` in its workspace
    live = bob / ".agent-turn-runners" / ".consumer_liveness"
    live.mkdir(parents=True)
    (live / "tok.lock").write_text("")
    os.link(live / "tok.lock", bob / "outside.lock")
    (bob / "state.db").write_text("")
    os.link(bob / "state.db", bob / "state.db.bak.db")
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "page.md").write_text("w")
    os.link(tmp_path / "wiki" / "page.md", tmp_path / "wiki" / "alias.md")
    (bob / "x.md").symlink_to(alice / "identity.md")  # never followed
    before = _snapshot(tmp_path)

    assert census.main(["--root", str(tmp_path)]) == 3
    report = json.loads(capsys.readouterr().out)
    assert _snapshot(tmp_path) == before
    assert report["counts"]["multi_link_inodes"] == 5
    assert report["counts"]["special_entries"] == 1
    classes = report["refused_names_by_class"]
    assert classes["brain-file"]["names"] == 1
    assert classes["agent-workspace"]["names"] == 3
    assert classes["liveness-proof"]["names"] == 1
    assert classes["sqlite"]["names"] == 2
    assert classes["platform"]["names"] == 2
    text = json.dumps(report)
    assert "alice" not in text and "secret-plan" not in text
    assert "<owner#" in text and "identity.md" in text


def test_paths_flag_prints_names_and_counts_unseen_aliases(tmp_path, capsys):
    (tmp_path / "data").mkdir()
    tree = _universe(tmp_path / "data", "alice")
    (tree / "notes.json").write_text("{}")
    os.link(tree / "notes.json", tmp_path / "outside-root.json")
    assert census.main(["--root", str(tmp_path / "data"), "--paths"]) == 3
    row = json.loads(capsys.readouterr().out)["inodes"][0]
    assert row["names"] == ["alice/notes.json"]
    assert row["unseen_names"] == 1
    assert row["classes"] == ["fixed-file"]


def test_relative_root_is_refused(capsys):
    assert census.main(["--root", "data"]) == 2
