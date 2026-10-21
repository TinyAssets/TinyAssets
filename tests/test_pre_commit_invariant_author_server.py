"""Tests for the tinyassets.author_server import pre-commit invariant."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import pre_commit_invariant_author_server as gate
from scripts.pre_commit_invariant_author_server import check_diff

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_diff(added_lines: list[str], filename: str = "tinyassets/foo.py") -> str:
    """Build a minimal unified diff that adds the given lines to filename."""
    lines = [
        f"diff --git a/{filename} b/{filename}",
        f"--- a/{filename}",
        f"+++ b/{filename}",
        f"@@ -1,0 +1,{len(added_lines)} @@",
    ]
    for line in added_lines:
        lines.append(f"+{line}")
    return "\n".join(lines) + "\n"


# ── no-hit cases ─────────────────────────────────────────────────────────────


def test_empty_diff_returns_no_hits():
    assert check_diff("") == []


def test_clean_import_no_hit():
    diff = _make_diff(["from tinyassets.daemon_server import execute_branch"])
    assert check_diff(diff) == []


def test_existing_file_content_not_in_diff_ignored():
    # Context lines (no "+" prefix) are never flagged.
    raw = "\n".join([
        "diff --git a/tinyassets/foo.py b/tinyassets/foo.py",
        "--- a/tinyassets/foo.py",
        "+++ b/tinyassets/foo.py",
        "@@ -1,1 +1,2 @@",
        " from tinyassets.author_server import x",  # context line — NOT new
        "+good_line = 1",
    ]) + "\n"
    assert check_diff(raw) == []


def test_comment_line_not_flagged():
    diff = _make_diff(["# from tinyassets.author_server import old_thing"])
    assert check_diff(diff) == []


def test_non_python_file_ignored():
    diff = _make_diff(
        ["from tinyassets.author_server import x"],
        filename="docs/notes.md",
    )
    assert check_diff(diff) == []


def test_string_literal_not_flagged():
    diff = _make_diff(['msg = "from tinyassets.author_server import x"'])
    assert check_diff(diff) == []


# ── hit cases ────────────────────────────────────────────────────────────────


def test_from_import_flagged():
    diff = _make_diff(["from tinyassets.author_server import foo"])
    hits = check_diff(diff)
    assert len(hits) == 1
    filename, lineno, line = hits[0]
    assert "tinyassets/foo.py" in filename
    assert lineno == 1
    assert "tinyassets.author_server" in line


def test_bare_import_flagged():
    diff = _make_diff(["import tinyassets.author_server"])
    hits = check_diff(diff)
    assert len(hits) == 1
    assert "tinyassets.author_server" in hits[0][2]


def test_indented_deferred_import_flagged():
    diff = _make_diff(["    from tinyassets.author_server import bar"])
    hits = check_diff(diff)
    assert len(hits) == 1


def test_multiple_hits_in_one_diff():
    diff = _make_diff([
        "from tinyassets.author_server import a",
        "x = 1",
        "from tinyassets.author_server import b",
    ])
    hits = check_diff(diff)
    assert len(hits) == 2
    assert hits[0][1] == 1
    assert hits[1][1] == 3


def test_mixed_hits_and_clean_lines():
    diff = _make_diff([
        "from tinyassets.daemon_server import good",
        "from tinyassets.author_server import bad",
        "from tinyassets.daemon_server import also_good",
    ])
    hits = check_diff(diff)
    assert len(hits) == 1
    assert hits[0][1] == 2


def test_line_number_tracking_with_context():
    """Context lines in the diff advance the new-file line counter."""
    raw = "\n".join([
        "diff --git a/tinyassets/bar.py b/tinyassets/bar.py",
        "--- a/tinyassets/bar.py",
        "+++ b/tinyassets/bar.py",
        "@@ -5,1 +5,3 @@",
        " existing_line = 1",         # context line → new lineno advances to 5
        "+from tinyassets.author_server import x",  # new lineno 6
        "+good_line = 2",
    ]) + "\n"
    hits = check_diff(raw)
    assert len(hits) == 1
    assert hits[0][1] == 6  # line 5 (context) advances counter, then +line = 6


def test_returns_correct_shape():
    diff = _make_diff(["from tinyassets.author_server import thing"])
    hits = check_diff(diff)
    assert len(hits) == 1
    filename, lineno, line = hits[0]
    assert isinstance(filename, str)
    assert isinstance(lineno, int)
    assert isinstance(line, str)


# ── edge cases ───────────────────────────────────────────────────────────────


def test_submodule_not_flagged():
    # tinyassets.author_server_utils is NOT the shim — don't over-match.
    diff = _make_diff(["from tinyassets.author_server_utils import helper"])
    assert check_diff(diff) == []




@pytest.mark.parametrize("variant", [
    "from tinyassets.author_server import x",
    "import tinyassets.author_server",
    "    from tinyassets.author_server import (y,)",
])
def test_parametrized_forbidden_forms(variant):
    diff = _make_diff([variant])
    assert len(check_diff(diff)) == 1


@pytest.mark.parametrize("prefix", ["# 🧪\n".encode(), b"# legacy byte \x8d\n"])
def test_diff_capture_preserves_non_ascii_and_still_rejects_imports(monkeypatch, prefix):
    payload = prefix + _make_diff(["from tinyassets.author_server import x"]).encode()

    def git_diff(command, **kwargs):
        assert kwargs.get("capture_output") is True
        assert not kwargs.get("text")
        assert "encoding" not in kwargs
        return SimpleNamespace(returncode=0, stdout=payload)

    monkeypatch.setattr(gate.subprocess, "run", git_diff)
    assert gate._get_staged_diff().encode("utf-8", errors="surrogateescape") == payload
    assert gate.main() == 2


@pytest.mark.parametrize("failure", ["missing_git", "git_error", "missing_output"])
def test_inspection_failure_is_not_an_empty_success(monkeypatch, capsys, failure):
    def git_diff(*args, **kwargs):
        if failure == "missing_git":
            raise FileNotFoundError("git")
        return SimpleNamespace(
            returncode=1 if failure == "git_error" else 0,
            stdout=None if failure == "missing_output" else b"",
        )

    monkeypatch.setattr(gate.subprocess, "run", git_diff)
    assert gate.main() == 2
    assert "inspection failed" in capsys.readouterr().err


def test_successfully_empty_diff_remains_clean(monkeypatch):
    monkeypatch.setattr(
        gate.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=b""),
    )
    assert gate.main() == 0


def test_real_git_staged_unicode_is_inspected_in_non_utf8_process(tmp_path):
    """Exercise the pipe reader and main together, not just a mocked diff."""
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    source = tmp_path / "probe.py"
    source.write_text("# 🪐\nfrom tinyassets.author_server import x\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", source.name], cwd=tmp_path, check=True)
    environment = dict(os.environ, PYTHONUTF8="0", LC_ALL="C", PYTHONCOERCECLOCALE="0")
    result = subprocess.run(
        [sys.executable, str(Path(gate.__file__).resolve())],
        cwd=tmp_path, env=environment, capture_output=True,
    )
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 2, output
    assert "probe.py" in output and "tinyassets.author_server" in output
    assert "UnicodeDecodeError" not in output
