"""The reconciler edits its one issue instead of posting updates or duplicates."""

from datetime import datetime, timezone
from types import SimpleNamespace

from scripts import shape_reconcile


def test_row_line_accepts_a_utf8_bom():
    assert shape_reconcile.declared_rows("\ufeffRegistry row(s): S21, B1\n") == ["B1", "S21"]


def test_old_path_net_deletion_converges_and_addition_grows():
    path = "tinyassets/api/market.py"
    deleted = [{"filename": path, "status": "modified", "patch": "@@\n-old\n-old2\n+new"}]
    added = [{"filename": path, "status": "modified", "patch": "@@\n-old\n+new\n+new2"}]
    replaced = [{"filename": path, "status": "modified", "patch": "@@\n-old\n+new"}]
    assert "B1" in shape_reconcile.old_path_changes(deleted)[1]
    assert "B1" not in shape_reconcile.old_path_changes(deleted)[0]
    assert "B1" in shape_reconcile.old_path_changes(added)[0]
    assert "B1" in shape_reconcile.old_path_changes(replaced)[0]
    assert "B1" in shape_reconcile.old_path_changes(
        [{"filename": path, "status": "added"}]
    )[0]


def test_touched_rows_exclude_mirror_tests_docs_and_openspec():
    files = [
        {
            "filename": "packaging/claude-plugin/plugins/tinyassets-universe-server/"
            "runtime/tinyassets/universe_server.py"
        },
        {"filename": "tests/test_model_call.py"},
        {"filename": "openspec/changes/example/proposal.md"},
        {"filename": "docs/decisions/example.md"},
    ]
    assert shape_reconcile.shape_paths(files) == set()
    registry = [
        {
            "filename": "docs/architecture.md",
            "patch": "@@\n-| S20 | old\n+| S20 | new\n+| S1 | unrelated",
        }
    ]
    assert shape_reconcile.shape_paths(registry) == {"S20"}
    registry[0]["patch"] = "@@\n-| S21 | old\n+| S21 | new"
    assert shape_reconcile.shape_paths(registry) == {"S21"}


def test_lowered_ceiling_converges():
    files = [
        {
            "filename": "scripts/check_shapes.py",
            "patch": '@@ -1,3 +1,3 @@\n "B1": Shape(\n-    ceiling=10,\n+    ceiling=9,',
        }
    ]
    assert shape_reconcile.lowered_ceilings(files) == {"B1"}
    source = (shape_reconcile.ROOT / "scripts/check_shapes.py").read_text(encoding="utf-8")
    pin_line = next(i for i, line in enumerate(source.splitlines(), 1) if "ceiling=68" in line)
    without_row_context = [
        {
            "filename": "scripts/check_shapes.py",
            "patch": (
                f"@@ -{pin_line},2 +{pin_line},2 @@\n"
                "-        ceiling=68,\n+        ceiling=67,"
            ),
        }
    ]
    assert shape_reconcile.lowered_ceilings(without_row_context) == {"B1"}


def test_overlap_only_runtime_source_files():
    assert not shape_reconcile.overlap_file("docs/architecture.md")
    assert not shape_reconcile.overlap_file("tests/test_model_call.py")
    assert not shape_reconcile.overlap_file(
        "packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/tinyassets/model_call.py"
    )
    assert shape_reconcile.overlap_file("tinyassets/model_call.py")


def test_issue_body_graces_undeclared_row_and_reports_convergence(monkeypatch):
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    prs = [
        {
            "number": number,
            "body": body,
            "created_at": created,
            "updated_at": created,
            "draft": False,
            "labels": [],
            "html_url": f"https://example.test/pull/{number}",
            "head": {"ref": f"branch-{number}", "repo": {"full_name": "owner/repo"}},
        }
        for number, body, created in [
            (1, "", "2026-10-05T00:00:00Z"),
            (2, "", "2026-10-03T00:00:00Z"),
            (3, "Registry row(s): B1", "2026-10-03T00:00:00Z"),
            (4, "Registry row(s): B1", "2026-10-03T00:00:00Z"),
        ]
    ]
    files = {
        1: [{"filename": "tinyassets/api/market.py", "patch": "@@\n-old\n-old2\n+new"}],
        2: [{"filename": "docs/architecture.md", "patch": "@@\n context"}],
        3: [{"filename": "tinyassets/api/market.py", "patch": "@@\n+new"}],
        4: [{"filename": "docs/architecture.md", "patch": "@@\n context"}],
    }

    def fake_pages(path):
        if "/pulls?" in path:
            return prs
        if "/files?" in path:
            return files[int(path.split("/pulls/")[1].split("/")[0])]
        return []

    monkeypatch.setattr(shape_reconcile, "pages", fake_pages)
    monkeypatch.setattr(shape_reconcile, "registry_status", lambda: {"B1": "drifted"})
    monkeypatch.setattr(shape_reconcile.openspec_flow, "build_report", lambda _: {"changes": []})
    monkeypatch.setattr(
        shape_reconcile.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="[]")
    )
    body = shape_reconcile.issue_body("owner/repo", now)
    rows = {
        int(line.split("](")[0].split("#")[-1]): line
        for line in body.splitlines()
        if line.startswith("| [#")
    }
    assert "converges: B1" in rows[1]
    assert "old path without row" not in rows[1]
    assert "builds on deleting path" not in rows[1]
    assert "registry row missing" not in rows[1]
    assert "| — |" in rows[1]
    assert "registry row missing" in rows[2]
    assert "builds on deleting path: B1" in rows[3]
    assert "#1, #3" in body
    assert "#1, #2" not in body
    assert "#3, #4" in body
    assert "#1, #4" not in body


def test_existing_issue_is_edited_once(monkeypatch):
    calls = []
    monkeypatch.setattr(shape_reconcile, "issue_body", lambda *_: "new body\n")
    monkeypatch.setattr(
        shape_reconcile,
        "pages",
        lambda *_: [
            {
                "number": 42,
                "title": shape_reconcile.TITLE,
                "body": "old body",
                "state": "open",
                "labels": [],
                "html_url": "https://github.com/TinyAssets/TinyAssets/issues/42",
            }
        ],
    )
    monkeypatch.setattr(shape_reconcile, "gh", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(shape_reconcile.sys, "argv", ["shape_reconcile.py"])

    assert shape_reconcile.main() == 0
    assert len(calls) == 1
    assert calls[0][0][:3] == ("repos/TinyAssets/TinyAssets/issues/42", "--method", "PATCH")
    assert '"shape-registry"' in calls[0][1]["input_data"]


def test_missing_issue_is_created_once(monkeypatch):
    calls = []
    monkeypatch.setattr(shape_reconcile, "issue_body", lambda *_: "body\n")
    monkeypatch.setattr(shape_reconcile, "pages", lambda *_: [])

    def fake_gh(*args, **kwargs):
        calls.append((args, kwargs))
        return {"html_url": "https://github.com/TinyAssets/TinyAssets/issues/42"}

    monkeypatch.setattr(shape_reconcile, "gh", fake_gh)
    monkeypatch.setattr(shape_reconcile.sys, "argv", ["shape_reconcile.py"])

    assert shape_reconcile.main() == 0
    assert len(calls) == 1
    assert calls[0][0][:3] == ("repos/TinyAssets/TinyAssets/issues", "--method", "POST")
    assert '"shape-registry"' in calls[0][1]["input_data"]
