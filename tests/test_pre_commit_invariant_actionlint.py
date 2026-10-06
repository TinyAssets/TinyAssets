"""Tests for scripts/pre_commit_invariant_actionlint.py.

Covers:
  (a) no-op when no workflow files staged
  (b) no-op when actionlint binary missing (warn + exit 0)
  (c) no-op with explicit paths that don't exist
  (d) main() passes when actionlint exits 0
  (e) main() fails (exit 2) when actionlint emits findings
  (f) pre-commit hook source-of-truth contains the invariant section
  (g) CI workflow exists + has correct paths filter
  (h) AGENTS.md (or README) documents the install one-liner
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import pre_commit_invariant_actionlint as inv  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_HOOK_SOURCE = _REPO / "scripts" / "git-hooks" / "pre-commit"
_CI_WORKFLOW = _REPO / ".github" / "workflows" / "actionlint.yml"


# ---- no-op paths -----------------------------------------------------------


def test_no_workflow_staged_is_noop():
    with patch.object(inv, "_staged_workflow_files", return_value=[]):
        assert inv.main([]) == 0


def test_actionlint_missing_is_noop(capsys):
    with patch.object(inv, "_staged_workflow_files",
                      return_value=[".github/workflows/example.yml"]), \
         patch.object(inv, "_find_actionlint", return_value=None):
        rc = inv.main([])
    assert rc == 0
    err = capsys.readouterr().err
    assert "actionlint" in err.lower()
    assert "choco install actionlint" in err


def test_explicit_invalid_path_is_noop():
    assert inv.main(["/nonexistent/path/to/file.yml"]) == 0


# ---- actionlint pass / fail paths ------------------------------------------


def test_actionlint_clean_exits_zero(capsys, tmp_path):
    wf = tmp_path / "example.yml"
    wf.write_text("name: x\non: push\njobs: {}\n", encoding="utf-8")
    with patch.object(inv, "run_actionlint", return_value=(0, "")):
        rc = inv.main([str(wf)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "clean" in err.lower()


def test_actionlint_findings_exits_two(capsys, tmp_path):
    wf = tmp_path / "broken.yml"
    wf.write_text("name: x\n", encoding="utf-8")
    findings = "broken.yml:1:1: some problem [syntax-check]\n"
    with patch.object(inv, "run_actionlint", return_value=(1, findings)):
        rc = inv.main([str(wf)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "INVARIANT VIOLATED" in err
    assert "some problem" in err


def test_actionlint_runtime_error_is_noop(capsys, tmp_path):
    wf = tmp_path / "any.yml"
    wf.write_text("", encoding="utf-8")
    err_marker = "__actionlint_runtime_error__: timeout"
    with patch.object(inv, "run_actionlint", return_value=(0, err_marker)):
        rc = inv.main([str(wf)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "skipping" in err.lower()


# ---- hook source-of-truth wiring -------------------------------------------


def test_hook_source_invokes_actionlint_invariant():
    text = _HOOK_SOURCE.read_text(encoding="utf-8")
    assert "scripts/pre_commit_invariant_actionlint.py" in text, (
        "pre-commit source-of-truth must delegate to the actionlint invariant"
    )
    # Hook should only fire when a workflow file is actually staged.
    assert ".github/workflows/" in text




# ---- CI workflow -----------------------------------------------------------


def test_ci_workflow_exists():
    assert _CI_WORKFLOW.is_file(), (
        ".github/workflows/actionlint.yml must exist"
    )


def test_ci_workflow_is_path_scoped():
    text = _CI_WORKFLOW.read_text(encoding="utf-8")
    # Must scope to workflow files only — no repo-wide triggering.
    assert ".github/workflows/**.yml" in text or \
           ".github/workflows/**.yaml" in text
    # Fail-the-PR shape: must run on pull_request.
    assert "pull_request:" in text


def test_ci_workflow_installs_pinned_actionlint():
    text = _CI_WORKFLOW.read_text(encoding="utf-8")
    assert "ACTIONLINT_VERSION" in text, (
        "pin actionlint by version; don't track HEAD"
    )


def test_ci_workflow_verifies_tarball_sha256():
    """Supply-chain hardening: the download must be sha256-verified,
    not just version-pinned. Version-pin protects against HEAD drift,
    SHA256 protects against the release tarball being replaced on
    the CDN / an MITM injecting a different byte stream."""
    text = _CI_WORKFLOW.read_text(encoding="utf-8")
    assert "ACTIONLINT_SHA256" in text, (
        "declare ACTIONLINT_SHA256 alongside ACTIONLINT_VERSION"
    )
    assert "sha256sum -c" in text, (
        "verify the tarball against ACTIONLINT_SHA256 via `sha256sum -c`"
    )
    # The 1.7.7 linux_amd64 checksum is a hard invariant — if someone
    # bumps the version without updating the hash, this test fails.
    # Update both together; the release's
    # `actionlint_<version>_checksums.txt` is the authoritative source.
    if 'ACTIONLINT_VERSION="1.7.7"' in text:
        assert (
            'ACTIONLINT_SHA256="023070a287cd8cccd71515fedc843f1985bf96c'
            '436b7effaecce67290e7e0757"'
        ) in text, (
            "if pinned to 1.7.7, SHA256 must be the upstream-published "
            "checksum 023070a287cd8cccd71515fedc843f1985bf96c436b7effae"
            "cce67290e7e0757"
        )


def test_ci_workflow_uses_merge_base_for_pr_diff():
    """PRs should only lint files the PR actually touched — not the whole
    repo — so pre-existing issues don't block unrelated PRs."""
    text = _CI_WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request.base.sha" in text or "base_ref" in text.lower()


# ---- docs ------------------------------------------------------------------


def test_the_install_one_liner_is_printed_where_the_miss_happens():
    """The install hint must reach the agent that hit the missing binary.

    This asserted a mention in AGENTS.md until 2026-09-26, when the rulebook recut
    moved every rule to its point of use. A mention in an always-loaded file is
    read on every unrelated turn and still absent at the moment it is needed; the
    invariant that matters is that the checker itself prints the command, which is
    what an agent sees when actionlint is missing.
    """
    source = (_REPO / "scripts" / "pre_commit_invariant_actionlint.py").read_text(
        encoding="utf-8"
    )
    assert "choco install actionlint" in source
    assert "brew install actionlint" in source
    # ...and printed, not merely documented in the module docstring.
    assert source.count("choco install actionlint") >= 2, (
        "the hint must be emitted at runtime, not only described in the docstring"
    )
