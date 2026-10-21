r"""No LIVE instruction may name a script or test that does not exist.

This class of defect appeared twice in one PR, both times invisible to a
name-based grep of the code, because nothing *imported* the deleted file:

* `scripts/codex_review.py` was consolidated away, taking eleven fail-closed
  dispatch tests with it, while a handoff still told operators to run it.
* `scripts/check_background_authority_inventory.py` was deleted as a "dead
  script" while its own audit called it "the executable closure guard" for
  `harden-background-branch-execution-authority`. The TEST was its CI wiring, so
  deleting both removed the guard entirely.

Scoped deliberately to surfaces a reader treats as CURRENT. Dated audits,
design notes, and archived OpenSpec changes are historical records: naming a
script that has since been deleted is correct there, and asserting otherwise
would force edits to the record of what happened.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Surfaces whose contents read as "do this now".
LIVE_ROOTS = (
    "openspec/specs",
    ".github/workflows",
    "docs/reference",
    "docs/runbooks",
    ".agents/skills",
)
LIVE_FILES = ("AGENTS.md", "CLAUDE.md")

REF = re.compile(r"(?<![\w/.-])((?:scripts|tests)/[A-Za-z0-9_./-]+\.(?:py|ps1|sh))")

# A reference is excused when the surrounding text says the thing is gone.
# Checked over a small WINDOW, not one line: a doc's "partly historical" banner
# usually sits a line or two away from the name it is excusing.
RETIRED_MARKERS = ("retired", "deleted", "removed", "no longer", "was consolidated")
MARKER_WINDOW = 2


def _live_files() -> list[Path]:
    found: list[Path] = []
    for rel in LIVE_FILES:
        p = REPO_ROOT / rel
        if p.is_file():
            found.append(p)
    for root in LIVE_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        found.extend(
            p
            for p in base.rglob("*")
            if p.is_file() and p.suffix in {".md", ".yml", ".yaml"}
        )
    return sorted(found)


def _resolves(ref: str) -> bool:
    """Does `ref` name a real file from SOME plausible working directory?

    A workflow step with `working-directory: mobile` writes
    `python3 scripts/add_app_scheme.py` and means `mobile/scripts/...`. Checking
    only the repo root reports three green workflows as broken.
    """
    if (REPO_ROOT / ref).exists():
        return True
    return any(
        (child / ref).exists()
        for child in REPO_ROOT.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )


def _offenders() -> list[str]:
    bad: list[str] = []
    for path in _live_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = text.splitlines()
        for index, line in enumerate(lines):
            window = " ".join(
                lines[max(0, index - MARKER_WINDOW) : index + MARKER_WINDOW + 1]
            ).lower()
            if any(marker in window for marker in RETIRED_MARKERS):
                continue
            for ref in REF.findall(line):
                if not _resolves(ref):
                    rel = path.relative_to(REPO_ROOT).as_posix()
                    bad.append(f"{rel}:{index + 1} names missing {ref}")
    return bad


def test_live_surfaces_exist_to_be_checked() -> None:
    # Guards the guard: an empty file list would make the check vacuous.
    files = _live_files()
    assert len(files) > 10, f"only {len(files)} live files found; the scan is too narrow"


def test_no_live_instruction_names_a_missing_script() -> None:
    offenders = _offenders()
    assert not offenders, (
        "live instructions point at files that do not exist:\n  "
        + "\n  ".join(offenders)
        + "\n\nEither restore the file, retarget the instruction, or say on that "
        "line that it is retired."
    )


@pytest.mark.parametrize("missing", ["scripts/definitely_not_here.py"])
def test_detector_catches_a_planted_reference(tmp_path: Path, missing: str) -> None:
    # Proves the regex actually matches the shape it is meant to catch, without
    # writing into the repo.
    line = f"Run `python {missing}` before shipping."
    assert REF.findall(line) == [missing]
    assert not _resolves(missing)


pytestmark = pytest.mark.verifies('shape-convergence:C1')
