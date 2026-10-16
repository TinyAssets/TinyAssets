"""Root OKF soul bundle read model.

The old implementation created ``<universe>/self/`` as a second identity model.
The current universe baseline keeps one active identity/intention model: the
linked OKF files rooted at ``soul.md`` in the universe directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets.universe_self_model import SEED_QUESTIONS, ensure_self_model, read_self_model


@pytest.fixture(autouse=True)
def _box_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path.parent))


def _write_blank_root_bundle(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text(
        '---\nokf_version: "0.1"\n---\n\n# Universe Bundle Index\n',
        encoding="utf-8",
    )
    (root / "soul.md").write_text(
        "---\ntype: Universe Soul\nstatus: not-learned\n---\n\n# Universe Soul\n",
        encoding="utf-8",
    )
    for slug in ("identity", "founder", "orgchart", "body", "origin"):
        (root / f"{slug}.md").write_text(
            f"---\ntype: Universe {slug.title()}\nstatus: not-learned\n---\n\n"
            f"# {slug.title()}\n\nNot learned yet.\n",
            encoding="utf-8",
        )


def test_ensure_self_model_does_not_create_self_folder(tmp_path: Path) -> None:
    result = ensure_self_model(tmp_path)
    assert result == tmp_path
    assert not (tmp_path / "self").exists()


def test_seed_questions_match_root_soul_files() -> None:
    assert {q.slug for q in SEED_QUESTIONS} == {
        "identity",
        "founder",
        "orgchart",
        "body",
        "origin",
    }


def test_read_missing_root_soul_bundle_reports_absent(tmp_path: Path) -> None:
    view = read_self_model(tmp_path, owner="test-owner")
    assert view["bundle_exists"] is False
    assert view["known"] == []
    assert view["open_questions"] == []


def test_read_blank_root_soul_bundle_is_all_open(tmp_path: Path) -> None:
    _write_blank_root_bundle(tmp_path)
    view = read_self_model(tmp_path, owner="test-owner")
    assert view["bundle_exists"] is True
    assert view["okf_version"] == "0.1"
    assert view["known"] == []
    assert {q["slug"] for q in view["open_questions"]} == {
        "identity",
        "founder",
        "orgchart",
        "body",
        "origin",
    }


def test_read_root_soul_files_distinguishes_known_from_open(tmp_path: Path) -> None:
    _write_blank_root_bundle(tmp_path)
    (tmp_path / "identity.md").write_text(
        "---\n"
        "type: Universe Identity\n"
        "status: learned\n"
        "name: Tiny\n"
        "---\n\n"
        "# Identity\n\n"
        "My founder calls me Tiny.\n\n"
        "# Citations\n"
        "[1] founder.md\n",
        encoding="utf-8",
    )
    view = read_self_model(tmp_path, owner="test-owner")
    known_slugs = {c["slug"] for c in view["known"]}
    open_slugs = {q["slug"] for q in view["open_questions"]}
    assert known_slugs == {"identity"}
    assert open_slugs == {"founder", "orgchart", "body", "origin"}
    assert view["name"] == "Tiny"


def test_read_ignores_legacy_self_directory(tmp_path: Path) -> None:
    _write_blank_root_bundle(tmp_path)
    legacy = tmp_path / "self"
    legacy.mkdir()
    (legacy / "identity.md").write_text(
        "---\ntype: self/identity\nname: Legacy\n---\n\nOld model.\n",
        encoding="utf-8",
    )
    view = read_self_model(tmp_path, owner="test-owner")
    assert view["known"] == []
    assert view["name"] == ""


def test_read_reports_actual_okf_version_from_root_index(tmp_path: Path) -> None:
    _write_blank_root_bundle(tmp_path)
    (tmp_path / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n\n# Universe Bundle Index\n',
        encoding="utf-8",
    )
    assert read_self_model(tmp_path, owner="test-owner")["okf_version"] == "0.2"
