from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets.wiki.okf_export import render_okf_files


def render_fixture(wiki_root, bundle_dir):
    files, report = render_okf_files({
        p.relative_to(wiki_root).as_posix(): p.read_bytes()
        for p in wiki_root.rglob("*.md")
    })
    for path, data in files.items():
        target = bundle_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return report


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    assert text.startswith("---\n")
    raw_meta, body = text.split("\n---\n", 1)
    meta: dict[str, str] = {}
    for line in raw_meta.removeprefix("---\n").splitlines():
        if not line.strip() or line.startswith("  "):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"')
    return meta, body


@pytest.fixture
def fixture_wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    wiki_root = data_root / "alpha" / "wiki"
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(data_root))
    monkeypatch.delenv("TINYASSETS_WIKI_PATH", raising=False)

    (wiki_root / "pages" / "concepts").mkdir(parents=True)
    (wiki_root / "drafts" / "concepts").mkdir(parents=True)
    (wiki_root / "raw").mkdir(parents=True)
    (wiki_root / "daemon-wiki").mkdir(parents=True)

    (wiki_root / "pages" / "concepts" / "alpha.md").write_text(
        "\n".join([
            "---",
            "title: Alpha Concept",
            "kind: project-note",
            "description: First exported concept.",
            "updated: 2026-06-24T12:30:00Z",
            "id: alpha-1",
            "category: concepts",
            "sources:",
            "  - internal",
            "status: promoted",
            "tags: [alpha, demo]",
            "---",
            "# Alpha Heading",
            "",
            "See [[related]] and [[missing-target]].",
            "",
        ]),
        encoding="utf-8",
    )
    (wiki_root / "pages" / "concepts" / "related.md").write_text(
        "\n".join([
            "---",
            "updated: 2026-06-23",
            "---",
            "# Related Concept",
            "",
            "Back to [[alpha]].",
            "",
        ]),
        encoding="utf-8",
    )
    (wiki_root / "pages" / "soul.md").write_text(
        "---\ntype: secret\n---\nhost-private memory\n",
        encoding="utf-8",
    )
    (wiki_root / "soul.md").write_text("host-private root memory\n", encoding="utf-8")
    (wiki_root / "drafts" / "concepts" / "draft.md").write_text(
        "---\ntype: draft\n---\ndraft body\n",
        encoding="utf-8",
    )
    (wiki_root / "raw" / "raw.md").write_text("raw body\n", encoding="utf-8")
    (wiki_root / "daemon-wiki" / "daemon.md").write_text("daemon body\n", encoding="utf-8")
    return wiki_root, tmp_path / "bundle"


def test_export_universe_okf_bundle_exports_curated_pages_only(
    fixture_wiki: tuple[Path, Path],
) -> None:
    wiki_root, bundle_dir = fixture_wiki

    report = render_fixture(wiki_root, bundle_dir)

    json.dumps(report)
    assert report["conformant"] is True
    assert report["counts"]["concepts_exported"] == 2
    assert (bundle_dir / "concepts" / "alpha.md").is_file()
    assert (bundle_dir / "concepts" / "related.md").is_file()
    assert not (bundle_dir / "drafts").exists()
    assert not (bundle_dir / "raw").exists()
    assert not (bundle_dir / "daemon-wiki").exists()
    assert not list(bundle_dir.rglob("soul.md"))
    assert report["counts"]["excluded_by_privacy"] >= 1


def test_export_converts_wikilinks_to_absolute_okf_links_and_flags_unresolved(
    fixture_wiki: tuple[Path, Path],
) -> None:
    wiki_root, bundle_dir = fixture_wiki

    report = render_fixture(wiki_root, bundle_dir)
    body = (bundle_dir / "concepts" / "alpha.md").read_text(encoding="utf-8")

    assert "[related](/concepts/related.md)" in body
    assert "[[missing-target]]" not in body
    assert "](/missing-target.md)" not in body
    assert "missing-target" in body
    assert report["counts"]["unresolved_links"] == 1
    assert report["unresolved_links"] == [{
        "source": "concepts/alpha.md",
        "target": "missing-target",
    }]


def test_export_guarantees_type_title_timestamp_and_workflow_keys(
    fixture_wiki: tuple[Path, Path],
) -> None:
    wiki_root, bundle_dir = fixture_wiki

    render_fixture(wiki_root, bundle_dir)
    alpha_meta, _ = _split_frontmatter(
        (bundle_dir / "concepts" / "alpha.md").read_text(encoding="utf-8")
    )
    related_meta, _ = _split_frontmatter(
        (bundle_dir / "concepts" / "related.md").read_text(encoding="utf-8")
    )

    assert alpha_meta["type"] == "project-note"
    assert alpha_meta["title"] == "Alpha Concept"
    assert alpha_meta["timestamp"] == "2026-06-24T12:30:00Z"
    assert alpha_meta["workflow_original_path"] == "pages/concepts/alpha.md"
    assert alpha_meta["workflow_id"] == "alpha-1"
    assert alpha_meta["workflow_category"] == "concepts"
    assert alpha_meta["workflow_status"] == "promoted"
    assert "workflow_sources" in alpha_meta

    assert related_meta["type"] == "note"
    assert related_meta["title"] == "Related Concept"
    assert related_meta["timestamp"]


def test_reserved_files_follow_okf_structure(fixture_wiki: tuple[Path, Path]) -> None:
    wiki_root, bundle_dir = fixture_wiki

    report = render_fixture(wiki_root, bundle_dir)
    index_text = (bundle_dir / "index.md").read_text(encoding="utf-8")
    log_text = (bundle_dir / "log.md").read_text(encoding="utf-8")

    index_meta, index_body = _split_frontmatter(index_text)
    assert index_meta == {"okf_version": "0.1"}
    assert "- [Alpha Concept](concepts/alpha.md) - First exported concept." in index_body
    assert "- [Related Concept](concepts/related.md)" in index_body
    assert not log_text.startswith("---")
    assert "## 2026-06-24" in log_text
    assert "## 2026-06-23" in log_text
    assert report["reserved_files"] == {
        "index.md": {"okf_version": "0.1", "valid": True},
        "log.md": {"valid": True},
    }
