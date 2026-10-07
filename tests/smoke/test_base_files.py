"""Smoke: the base files are the ones AGENTS.md names, and retired ones stay gone.

AGENTS.md is the always-loaded map. If it disappears from main, every
orient-first agent starts with no context: a silent onboarding failure.

STATUS.md (retired 2026-08-25) and PLAN.md (retired 2026-10-06, ADR-005) are
asserted absent, not tolerated: re-adding either would quietly restore a large
second source of truth beside the typed homes AGENTS.md lists.
"""

from __future__ import annotations

from pathlib import Path

# Repo root = three levels up from this file (tests/smoke/test_*.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_agents_md_exists():
    assert (_REPO_ROOT / "AGENTS.md").is_file(), "AGENTS.md missing from repo root"


def test_architecture_map_exists():
    assert (_REPO_ROOT / "docs" / "architecture.md").is_file(), "docs/architecture.md missing"


def test_plan_md_stays_retired():
    assert not (_REPO_ROOT / "PLAN.md").exists(), (
        "PLAN.md is back. It was retired 2026-10-06 (ADR-005): direction lives in "
        "README.md § Direction, the map in docs/architecture.md, decisions in "
        "docs/decisions/, behaviour in openspec/specs/."
    )


def test_status_md_stays_retired():
    assert not (_REPO_ROOT / "STATUS.md").exists(), (
        "STATUS.md is back. It was retired 2026-08-25 (5.2x over its own declared "
        "ceiling, 46% of 90 days of commits). Live state belongs in the typed homes "
        "listed in AGENTS.md § Live state, not in one always-loaded file."
    )


def test_pyproject_has_tinyassets_name():
    pyproject = _REPO_ROOT / "pyproject.toml"
    assert pyproject.is_file(), "pyproject.toml missing from repo root"
    content = pyproject.read_text(encoding="utf-8")
    assert 'name = "tinyassets"' in content, "pyproject.toml package name drifted"
    assert 'name = "workflow"' not in content, "old package name leaked back into pyproject.toml"
