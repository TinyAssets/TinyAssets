"""The reconcile dashboard reads a PR's declared rows against the real registry."""

import subprocess
from datetime import datetime, timezone

import pytest

from scripts import check_shapes, shape_reconcile

pytestmark = pytest.mark.verifies("shape-convergence:C2")

REAL_ROWS = shape_reconcile.registry_status()


def test_current_rows_past_s28_are_recognized():
    declaration = shape_reconcile.declared_rows("Registry row(s): S36, S35, N12\n", REAL_ROWS)
    assert declaration.rows == ["N12", "S35", "S36"]
    assert declaration.unknown == []
    assert not declaration.none


def test_a_future_row_is_recognized_once_the_registry_defines_it():
    table = "| S99 | future | a | b | current |\n"
    future = {**REAL_ROWS, **check_shapes.registry_rows(table)}
    assert shape_reconcile.declared_rows("Registry row(s): S99", future).rows == ["S99"]


def test_a_row_the_registry_does_not_define_is_rejected():
    assert "S9999" not in REAL_ROWS
    declaration = shape_reconcile.declared_rows("Registry row(s): S8, S9999", REAL_ROWS)
    assert declaration.rows == ["S8"]
    assert declaration.unknown == ["S9999"]


def test_none_is_a_declaration():
    for line in ("Registry row(s): none", "Registry row(s): None (agent tooling)"):
        declaration = shape_reconcile.declared_rows(line, REAL_ROWS)
        assert declaration == shape_reconcile.Declaration([], [], True)
    assert not shape_reconcile.declared_rows("no line here", REAL_ROWS).none


def test_dashboard_flags_typos_and_accepts_none(monkeypatch):
    def pr(number, body):
        return {
            "number": number,
            "body": body,
            "html_url": f"https://example.invalid/{number}",
            "head": {"ref": f"fix/{number}", "repo": None},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "draft": False,
            "labels": [],
        }

    pulls = [
        pr(1, "Registry row(s): S36"),
        pr(2, "Registry row(s): none (docs only)"),
        pr(3, "Registry row(s): S9999"),
    ]
    monkeypatch.setattr(
        shape_reconcile, "pages", lambda path: pulls if "pulls?state=open" in path else []
    )
    monkeypatch.setattr(shape_reconcile.openspec_flow, "build_report", lambda root: {"changes": []})
    monkeypatch.setattr(
        shape_reconcile.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="[]", stderr=""),
    )
    body = shape_reconcile.issue_body("o/r", datetime(2026, 11, 5, tzinfo=timezone.utc))
    rows = {line.split(" | ")[0]: line for line in body.splitlines() if line.startswith("| [#")}
    assert "| S36 |" in rows["| [#1](https://example.invalid/1)"]
    assert "registry row missing" not in rows["| [#1](https://example.invalid/1)"]
    assert "| none |" in rows["| [#2](https://example.invalid/2)"]
    assert "registry row missing" not in rows["| [#2](https://example.invalid/2)"]
    assert "unknown registry row: S9999" in rows["| [#3](https://example.invalid/3)"]
    assert "registry row missing" in rows["| [#3](https://example.invalid/3)"]
