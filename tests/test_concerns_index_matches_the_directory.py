"""Every concern file carries its own index row, and nobody hand-keeps a table.

`AGENTS.md` names `docs/concerns/README.md` as the thing to skim when an area
has known-unresolved findings, and a concern's whole purpose is to be found by
the next session. On 2026-08-31 ten of twenty-eight files were missing from the
hand-kept table, including a **P0**. Filing and linking were two steps, so they
drifted.

The first fix made the table and the directory agree in both directions. That
worked, and it made every PR that filed or resolved a concern edit the same
table, so parallel PRs conflicted on it. Since 2026-09-27 the row lives in each
file's front-matter and `scripts/concerns_index.py` prints the table. Filing is
one step again, and it touches one file.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONCERNS = _ROOT / "docs" / "concerns"
README = CONCERNS / "README.md"

_spec = importlib.util.spec_from_file_location(
    "concerns_index", _ROOT / "scripts" / "concerns_index.py"
)
assert _spec and _spec.loader
index = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(index)


def _files() -> list[str]:
    return [p.name for p in index.concern_files(CONCERNS)]


@pytest.mark.parametrize("name", _files())
def test_each_concern_carries_its_index_row(name: str) -> None:
    """A concern without valid front-matter is missing from the index, i.e. unread."""
    meta = index.read_front_matter(CONCERNS / name)
    # `filed` is what makes "re-verify a premise before acting on it" possible.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta["filed"])


def test_the_index_lists_every_concern_exactly_once() -> None:
    table = index.render(CONCERNS)
    linked = re.findall(r"\]\(([^)]+\.md)\)", table)
    assert sorted(linked) == sorted(_files())


def test_the_readme_does_not_grow_a_hand_kept_table_again() -> None:
    """The table is what every parallel PR used to conflict on."""
    rows = [
        line
        for line in README.read_text(encoding="utf-8").splitlines()
        if line.startswith("|") and re.search(r"\]\([^)]+\.md\)", line)
    ]
    assert not rows, (
        "docs/concerns/README.md has concern table rows again. Put severity, "
        "title, filed and summary in the concern file's front-matter instead; "
        "`python scripts/concerns_index.py` prints the table:\n  " + "\n  ".join(rows[:5])
    )


def test_invalid_front_matter_is_refused(tmp_path: pathlib.Path) -> None:
    cases = {
        "none.md": "# no front-matter\n",
        "unclosed.md": "---\nseverity: P1\n",
        "bad-sev.md": "---\nseverity: P9\ntitle: t\nfiled: '2026-09-27'\n---\n",
        "no-title.md": "---\nseverity: P1\nfiled: '2026-09-27'\n---\n",
        "bad-date.md": "---\nseverity: P1\ntitle: t\nfiled: soon\n---\n",
    }
    for name, text in cases.items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            index.read_front_matter(path)


def test_render_orders_by_severity_then_newest_and_escapes_pipes(tmp_path: pathlib.Path) -> None:
    def write(name: str, severity: str, filed: str, summary: str = "s") -> None:
        (tmp_path / name).write_text(
            f"---\nseverity: {severity}\ntitle: {name}\nfiled: '{filed}'\n"
            f"summary: '{summary}'\n---\n\nbody\n",
            encoding="utf-8",
        )

    write("a.md", "P2", "2026-09-01")
    write("b.md", "P0", "2026-08-01")
    write("c.md", "P2", "2026-09-20", summary="x | y")
    write("d.md", "null", "2026-09-25")
    rows = index.render(tmp_path).splitlines()[2:]
    assert [re.search(r"\]\(([^)]+)\)", r).group(1) for r in rows] == [
        "b.md", "c.md", "a.md", "d.md",
    ]
    assert "x \\| y" in rows[1]


pytestmark = pytest.mark.verifies('shape-convergence:C1')
