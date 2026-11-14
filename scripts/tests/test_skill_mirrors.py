"""Each skill's folder is mirrored whole: every file under `.agents/skills/<name>/` matches
its `.claude/skills/<name>/` copy, not just `SKILL.md`."""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
import check_cross_provider_drift as drift  # noqa: E402

_FILES = {"SKILL.md": "# s\n", "references/cloudflare.md": "# cf\n"}


def _tree(root: Path) -> None:
    for tree in (".agents/skills/ops", ".claude/skills/ops"):
        for rel, text in _FILES.items():
            (root / tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / tree / rel).write_text(text, encoding="utf-8")


def _codes(root: Path, paths: list[Path] | None = None) -> set[tuple[str, str]]:
    return {(i.code, i.path) for i in drift.check_skill_mirrors(root, paths)}


def test_a_reference_file_that_differs_is_drift(tmp_path: Path) -> None:
    _tree(tmp_path)
    assert _codes(tmp_path) == set()
    mirror = tmp_path / ".claude/skills/ops/references/cloudflare.md"
    mirror.write_text("# cf\nmore\n", encoding="utf-8")
    want = {("skill-mirror-drift", ".claude/skills/ops/references/cloudflare.md")}
    assert _codes(tmp_path) == want
    # A hook's staged list names one copy; the other is compared with it.
    assert _codes(tmp_path, [mirror]) == want
    assert _codes(tmp_path, [tmp_path / ".agents/skills/ops/references/cloudflare.md"]) == want


def test_a_file_in_one_copy_only_is_drift(tmp_path: Path) -> None:
    _tree(tmp_path)
    (tmp_path / ".claude/skills/ops/references/extra.md").write_text("x\n", encoding="utf-8")
    (tmp_path / ".agents/skills/ops/references/only.md").write_text("y\n", encoding="utf-8")
    assert _codes(tmp_path) == {
        ("skill-mirror-missing-source", ".agents/skills/ops/references/extra.md"),
        ("skill-mirror-missing", ".claude/skills/ops/references/only.md"),
    }


def test_line_endings_and_undecodable_bytes_are_drift(tmp_path: Path) -> None:
    _tree(tmp_path)
    mirror = tmp_path / ".claude/skills/ops/SKILL.md"
    mirror.write_bytes(b"# s\r\n")
    want = {("skill-mirror-drift", ".claude/skills/ops/SKILL.md")}
    assert _codes(tmp_path) == want
    for tree, data in ((".agents", b"\xff\x00"), (".claude", b"\xfe\x00")):
        (tmp_path / tree / "skills/ops/SKILL.md").write_bytes(data)
    assert _codes(tmp_path) == want


def test_the_repository_skills_are_mirrored_whole() -> None:
    assert drift.check_skill_mirrors(_SCRIPTS.parent, None) == []
