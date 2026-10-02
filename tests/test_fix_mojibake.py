"""Tests for scripts/fix-mojibake.py detection + autofix + pre-commit integration."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "fix-mojibake.py"
HOOK_SOURCE = REPO_ROOT / "scripts" / "git-hooks" / "pre-commit"

EM_DASH = "\u2014"
SECTION = "\u00a7"
LESS_EQUAL = "\u2264"
ARROW = "\u2192"
BULLET = "\u2022"
LEFT_DQ = "\u201c"
RIGHT_DQ = "\u201d"


def _mojibake(text: str) -> str:
    return text.encode("utf-8").decode("cp1252", errors="ignore")


def _load_module(name: str, path: Path):
    # fix-mojibake.py has a hyphen in the name; import by file path.
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fix_mojibake():
    return _load_module("fix_mojibake_under_test", SCRIPT_PATH)


# -------------------------------------------------------------------
# MOJIBAKE_MAP structural invariants
# -------------------------------------------------------------------


def test_map_covers_the_most_common_mojibake(fix_mojibake):
    """The critical patterns observed in STATUS.md + past commits must
    all be present — regression guard against accidental trimming."""
    must_have = {
        _mojibake(EM_DASH): EM_DASH,  # em-dash (most common)
        _mojibake(SECTION): SECTION,  # section sign
        _mojibake(LESS_EQUAL): LESS_EQUAL,  # less-or-equal
        _mojibake(ARROW): ARROW,  # right arrow
        _mojibake(LEFT_DQ): LEFT_DQ,  # left quote
    }
    for key, fix in must_have.items():
        assert key in fix_mojibake.MOJIBAKE_MAP, (
            f"Mojibake map missing critical pattern {key!r}"
        )
        assert fix_mojibake.MOJIBAKE_MAP[key] == fix


def test_replacement_order_is_longest_first(fix_mojibake):
    """Longer-key patterns must be tried before shorter prefixes so
    `“` (left double quote) doesn't get partially matched by `”`
    (right double quote) eating the real fix."""
    pairs = fix_mojibake._replacement_order(fix_mojibake.MOJIBAKE_MAP)
    lens = [len(k) for k, _ in pairs]
    assert lens == sorted(lens, reverse=True), (
        "Replacement iteration order must be longest-key-first"
    )


# -------------------------------------------------------------------
# scan_file — detection
# -------------------------------------------------------------------


def test_scan_clean_file_returns_empty(fix_mojibake, tmp_path):
    p = tmp_path / "clean.md"
    p.write_text(
        f"em-dash is {EM_DASH} and bullet is {BULLET} and arrow is {ARROW}\n",
        encoding="utf-8",
    )

    findings = fix_mojibake.scan_file(p)

    assert findings == []


def test_scan_finds_em_dash_mojibake(fix_mojibake, tmp_path):
    p = tmp_path / "bad.md"
    p.write_text(f"this is {_mojibake(EM_DASH)} a bad dash\n", encoding="utf-8")

    findings = fix_mojibake.scan_file(p)

    assert len(findings) == 1
    f = findings[0]
    assert f.line == 1
    assert f.mojibake == _mojibake(EM_DASH)
    assert f.fix == EM_DASH
    # Column is 1-indexed and points at the start of the match.
    assert f.column == len("this is ") + 1


def test_scan_finds_multiple_occurrences(fix_mojibake, tmp_path):
    p = tmp_path / "many.md"
    p.write_text(
        f"dash {_mojibake(EM_DASH)} and section {_mojibake(SECTION)} "
        f"and arrow {_mojibake(ARROW)}\n"
        f"ge {_mojibake(LESS_EQUAL)} 150 chars\n",
        encoding="utf-8",
    )

    findings = fix_mojibake.scan_file(p)

    assert len(findings) == 4
    # Line numbers captured.
    lines = {f.line for f in findings}
    assert lines == {1, 2}


def test_scan_does_not_double_report_prefix_match(fix_mojibake, tmp_path):
    """A left-quote mojibake string contains a right-quote prefix.
    at the same column — longest-match wins."""
    p = tmp_path / "quote.md"
    p.write_text(
        f"this is {_mojibake(LEFT_DQ)}quoted{_mojibake(RIGHT_DQ)} text\n",
        encoding="utf-8",
    )

    findings = fix_mojibake.scan_file(p)

    # Exactly two findings: one left quote, one right quote (not 4).
    # If the shorter prefix were naively matched first we'd get 4 finds.
    assert len(findings) == 2
    fixes = sorted(f.fix for f in findings)
    # Left double quote (U+201C) + right double quote (U+201D).
    assert fixes == sorted([LEFT_DQ, RIGHT_DQ])


# -------------------------------------------------------------------
# fix_file — autofix
# -------------------------------------------------------------------


def test_fix_file_replaces_in_place(fix_mojibake, tmp_path):
    p = tmp_path / "repair.md"
    original = (
        f"dash {_mojibake(EM_DASH)} and section {_mojibake(SECTION)} "
        f"and arrow {_mojibake(ARROW)}\n"
    )
    p.write_text(original, encoding="utf-8")

    change_count = fix_mojibake.fix_file(p)

    assert change_count == 3
    content = p.read_text(encoding="utf-8")
    assert content == f"dash {EM_DASH} and section {SECTION} and arrow {ARROW}\n"


def test_fix_file_on_clean_is_noop(fix_mojibake, tmp_path):
    p = tmp_path / "already_clean.md"
    original = f"all good {EM_DASH} nothing to fix\n"
    p.write_text(original, encoding="utf-8")
    mtime_before = p.stat().st_mtime_ns

    change_count = fix_mojibake.fix_file(p)

    assert change_count == 0
    assert p.read_text(encoding="utf-8") == original
    # No write happened, so mtime unchanged.
    assert p.stat().st_mtime_ns == mtime_before


def test_fix_file_idempotent_after_repair(fix_mojibake, tmp_path):
    p = tmp_path / "double.md"
    p.write_text(
        f"em-dash style: {_mojibake(EM_DASH)} goes here\n",
        encoding="utf-8",
    )

    n1 = fix_mojibake.fix_file(p)
    n2 = fix_mojibake.fix_file(p)

    assert n1 > 0
    assert n2 == 0  # nothing left to fix


# -------------------------------------------------------------------
# CLI main(): detection exit code + autofix
# -------------------------------------------------------------------


def test_main_detection_returns_nonzero_on_findings(fix_mojibake, tmp_path, capsys):
    p = tmp_path / "x.md"
    p.write_text(f"bad {_mojibake(EM_DASH)} dash\n", encoding="utf-8")

    rc = fix_mojibake.main([str(p)])

    assert rc == 1
    captured = capsys.readouterr()
    assert "mojibake" in captured.err.lower()


def test_main_autofix_returns_zero_and_repairs(fix_mojibake, tmp_path, capsys):
    p = tmp_path / "x.md"
    p.write_text(f"bad {_mojibake(EM_DASH)} dash\n", encoding="utf-8")

    rc = fix_mojibake.main(["--autofix", str(p)])

    assert rc == 0
    assert p.read_text(encoding="utf-8") == f"bad {EM_DASH} dash\n"


def test_main_clean_input_returns_zero(fix_mojibake, tmp_path):
    p = tmp_path / "x.md"
    p.write_text(f"all clean {EM_DASH} good\n", encoding="utf-8")

    rc = fix_mojibake.main([str(p)])

    assert rc == 0


# -------------------------------------------------------------------
# Pre-commit hook integration
# -------------------------------------------------------------------


def _have_bash() -> bool:
    return shutil.which("bash") is not None


pytestmark_hook = pytest.mark.skipif(
    not _have_bash(),
    reason="pre-commit hook is a bash script; no bash on PATH",
)


def _init_repo(tmp_path: Path) -> Path:
    """Minimal git repo with the hook installed + tinyassets/ dirs stubbed."""
    import os

    repo = tmp_path / "repo"
    repo.mkdir()

    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    subprocess.run(["git", "init", "-q"], cwd=repo, env=env, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=repo, check=True,
    )

    (repo / "tinyassets").mkdir()
    mirror_path = (
        "packaging/claude-plugin/plugins/tinyassets-universe-server/"
        "runtime/tinyassets"
    )
    (repo / mirror_path).mkdir(parents=True)
    (repo / "scripts").mkdir(exist_ok=True)

    # Copy the mojibake script into the throwaway repo at the same path
    # the hook will look for.
    shutil.copy(SCRIPT_PATH, repo / "scripts" / "fix-mojibake.py")

    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(HOOK_SOURCE, hooks_dir / "pre-commit")
    if sys.platform != "win32":
        import os
        os.chmod(hooks_dir / "pre-commit", 0o755)

    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed", "--no-verify"],
        cwd=repo, check=True, env=env,
    )
    return repo


@pytestmark_hook
def test_hook_rejects_mojibake_in_staged_markdown(tmp_path):
    repo = _init_repo(tmp_path)
    doc = repo / "doc.md"
    doc.write_text(f"this has {_mojibake(EM_DASH)} a bad dash\n", encoding="utf-8")
    subprocess.run(["git", "add", "doc.md"], cwd=repo, check=True)

    result = subprocess.run(
        ["git", "commit", "-q", "-m", "bad"],
        cwd=repo, capture_output=True, text=True,
    )

    assert result.returncode != 0, (
        f"Hook must reject mojibake. stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "mojibake" in result.stderr.lower()


@pytestmark_hook
def test_hook_passes_clean_markdown(tmp_path):
    repo = _init_repo(tmp_path)
    doc = repo / "doc.md"
    doc.write_text(f"this has {EM_DASH} a real em-dash\n", encoding="utf-8")
    subprocess.run(["git", "add", "doc.md"], cwd=repo, check=True)

    result = subprocess.run(
        ["git", "commit", "-q", "-m", "clean"],
        cwd=repo, capture_output=True, text=True,
    )

    assert result.returncode == 0, (
        f"Hook must pass on clean markdown. stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )


@pytestmark_hook
def test_autofix_then_commit_passes(tmp_path):
    repo = _init_repo(tmp_path)
    doc = repo / "doc.md"
    doc.write_text(f"needs {_mojibake(EM_DASH)} repair\n", encoding="utf-8")

    # Run the autofix directly on the working tree.
    subprocess.run(
        [sys.executable, "scripts/fix-mojibake.py", "--autofix", "doc.md"],
        cwd=repo, check=True,
    )

    # File repaired on disk; now stage + commit.
    subprocess.run(["git", "add", "doc.md"], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "commit", "-q", "-m", "post-fix"],
        cwd=repo, capture_output=True, text=True,
    )

    assert result.returncode == 0, (
        f"After autofix, hook must pass. stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert doc.read_text(encoding="utf-8") == f"needs {EM_DASH} repair\n"


def test_scan_finds_mojibake_the_table_does_not_list(fix_mojibake, tmp_path):
    """Box drawing and arrows (``─``, ``═``, ``←``) reached main through the
    table's gaps (tinyassets/api/market.py, 2026-10-01)."""
    mangled = "\u2500\u2550\u2190\u2205".encode("utf-8").decode("cp1252", errors="replace")
    # cp1252 leaves 0x90 undefined; a real mangle carries it as U+0090.
    mangled = "".join(
        chr(b) if ch == "\ufffd" else ch
        for ch, b in zip(mangled, "\u2500\u2550\u2190\u2205".encode("utf-8"))
    )
    target = tmp_path / "banner.py"
    target.write_text(f"# {mangled} section\n", encoding="utf-8")
    fixes = [f.fix for f in fix_mojibake.scan_file(target)]
    assert fixes == ["\u2500", "\u2550", "\u2190", "\u2205"]
    assert fix_mojibake.fix_file(target) == 4
    assert target.read_text(encoding="utf-8") == "# \u2500\u2550\u2190\u2205 section\n"


def test_scan_leaves_real_latin1_text_alone(fix_mojibake, tmp_path):
    target = tmp_path / "words.md"
    target.write_text("café naïve résumé Ångström — fine\n", encoding="utf-8")
    assert fix_mojibake.scan_file(target) == []
