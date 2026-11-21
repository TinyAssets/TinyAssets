"""The required scope guard asks a change to more than prose for an Untangle check,
whose left, owed or later items name their fix."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
import review_checks as gate  # noqa: E402

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}
CODE = ["platform/owner/src/x.ts", "docs/notes.md"]
CHECK = "## Untangle check\n\n"


def _owed(section: str) -> list[str]:
    return gate.untangle_check_problems(CHECK + section, CODE)


def test_a_change_to_more_than_prose_needs_the_check() -> None:
    missing = gate.untangle_check_problems("## What changed\n\nA fix.\n", CODE)
    assert len(missing) == 1 and "no Untangle check" in missing[0]
    assert "platform/owner/src/x.ts" in missing[0]
    for body in ("## Untangle check\n\nNothing found.\n", "### untangle check\n",
                 "**Untangle check:** nothing found.\n"):
        assert gate.untangle_check_problems(body, CODE) == [], body
    assert gate.untangle_check_problems("A typo.\n", ["docs/a.md", "README.md"]) == []
    # Agents' instructions and the gates' rules bind, so they are not prose.
    assert gate.untangle_check_problems("A typo.\n", ["AGENTS.md"]) != []


@pytest.mark.parametrize("section", [
    "**Left, with reasons:**\n\n- `x` is owed to lane O.\n",
    "Left, with reasons:\n\n- `x` is owed: it needs a pin first,\n  and lane O is in flight.\n",
    "- **Left:**\n  - `x` waits for a later PR.\n",
    "**Left, with reasons:** `x` is owed to lane O.\n",
    "#### Owed\n\n- `x` and `y`.\n",
    "**Later:**\n\n- `x`.\n",
])
def test_an_owed_item_with_no_fix_named_is_refused(section: str) -> None:
    problems = _owed(section)
    assert len(problems) == 1 and "names no PR" in problems[0] and "`x`" in problems[0]


@pytest.mark.parametrize("fix", ["(#5601)", "(`untangle/o-wipe`)", "on `fix/wipe`",
                                 "(`process/p7-headers`)", "from land/7",
                                 "(live: the dark deploy applies it)"])
def test_an_owed_item_that_names_its_fix_passes(fix: str) -> None:
    assert _owed(f"**Left, with reasons:**\n\n- `x` is owed to lane O {fix}.\n") == []


def test_wording_that_owes_nothing_passes() -> None:
    section = (
        "**Found:**\n\n"
        "- `y` was owed as B24 and is fixed here; the `owed` entry is moved, not added.\n"
        "- The icon's missing link left a hidden link.\n\n"
        "**Fixed:** all of the above.\n\n"
        "**Left, with reasons:**\n\n"
        "- `#adopt`'s swallowed failure stays. It is decided: an adoption never fails a start.\n"
        "- The gateway's `flags.exactly`: C5b owed no change to it.\n"
        "- **Left:** nothing.\n\n"
        "The diff adds no new problem of the four kinds.\n"
    )
    assert _owed(section) == []


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True, env=_ENV).stdout.strip()


def _commit(root: Path, files: dict[str, str]) -> str:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "c")
    return _git(root, "rev-parse", "HEAD")


def _decide(repo: Path, base: str, head: str, check: str) -> subprocess.CompletedProcess:
    root = repo.parent
    (root / "body.md").write_text(check, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / "review_checks.py"), "--untangle-check",
         "--head", head, "--base", base, "--body-file", str(root / "body.md")],
        cwd=repo, capture_output=True, text=True)


def test_the_required_decision_reads_the_body_s_check(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    base = _commit(repo, {"docs/a.md": "# a\n", "src/x.py": "x = 1\n"})
    _git(repo, "checkout", "-q", "-b", "pr")
    prose = _commit(repo, {"docs/a.md": "# b\n"})
    assert _decide(repo, base, prose, "").stdout.strip() == "allow"
    code = _commit(repo, {"src/x.py": "x = 2\n"})
    refused = _decide(repo, base, code, "")
    assert (refused.returncode, refused.stdout.strip()) == (2, "deny")
    assert "no Untangle check" in refused.stderr and "src/x.py" in refused.stderr
    owed = _decide(repo, base, code, "## Untangle check\n\n**Left:**\n\n- `y` is owed.\n")
    assert owed.stdout.strip() == "deny" and "names no PR" in owed.stderr
    assert _decide(repo, base, code, "## Untangle check\n\nNothing left.\n").stdout.strip() \
        == "allow"
    unread = _decide(repo, "not-a-commit", code, "## Untangle check\n")
    assert unread.stdout.strip() == "deny" and "changed paths" in unread.stderr


OWED = ("# x as built\n\n## Owed\n\n- [ ] `a.ts` `f` goes, with the next change to it\n"
        "  (`untangle/a-f`).\n- [ ] `b.ts` `g`'s retry is decided (live: the dark deploy).\n")


def test_an_owed_item_is_a_checkbox_line_with_its_wrapped_lines() -> None:
    assert gate.owed_items(OWED + "  - a note under it\n- [x] Done (#5700).\n- plain\n") == [
        (5, False, "`a.ts` `f` goes, with the next change to it (`untangle/a-f`)."),
        (7, False, "`b.ts` `g`'s retry is decided (live: the dark deploy)."),
        (9, True, "Done (#5700)."),
    ]


def test_a_change_keeps_every_open_owed_item_until_it_is_ticked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    log = "docs/decisions/log/x.md"
    base = _commit(repo, {log: OWED})
    _git(repo, "checkout", "-q", "-b", "pr")
    check = "## Untangle check\n\nNothing left.\n"

    def decided(files: dict[str, str]) -> subprocess.CompletedProcess:
        _git(repo, "checkout", "-q", "-B", "pr", base)
        return _decide(repo, base, _commit(repo, files), check)

    # Removed without being done: refused, though the change is only prose.
    dropped = decided({log: OWED.replace("- [ ] `b.ts`", "- `b.ts`")})
    assert dropped.stdout.strip() == "deny" and "gone without being ticked" in dropped.stderr
    assert "`b.ts` `g`" in dropped.stderr
    assert decided({log: OWED.replace("- [ ] `b.ts`", "- [x] `b.ts`")}).stdout.strip() \
        == "allow"
    moved = OWED.split("## Owed\n\n")[1]
    assert decided({log: "# x as built\n", "docs/decisions/log/y.md": moved}).stdout.strip() \
        == "allow"
    unnamed = decided({"docs/decisions/log/z.md": "- [ ] `c.ts` waits for lane O.\n"})
    assert unnamed.stdout.strip() == "deny" and "z.md:1: an owed item names no PR" in \
        unnamed.stderr


def test_the_owed_list_is_every_open_item_in_the_decision_files(tmp_path: Path) -> None:
    (tmp_path / "docs/decisions/log").mkdir(parents=True)
    (tmp_path / "docs/decisions/log/x.md").write_text(OWED + "- [x] Done (#5700).\n",
                                                      encoding="utf-8")
    listed = subprocess.run([sys.executable, str(_SCRIPTS / "review_checks.py"), "--owed"],
                            cwd=tmp_path, capture_output=True, text=True, check=True)
    assert listed.stdout.splitlines() == [
        "docs/decisions/log/x.md:5: `a.ts` `f` goes, with the next change to it "
        "(`untangle/a-f`).",
        "docs/decisions/log/x.md:7: `b.ts` `g`'s retry is decided (live: the dark deploy).",
    ]
    elsewhere = subprocess.run([sys.executable, str(_SCRIPTS / "review_checks.py"), "--owed"],
                               cwd=tmp_path / "docs", capture_output=True, text=True)
    assert elsewhere.returncode == 2 and "repository's root" in elsewhere.stderr
