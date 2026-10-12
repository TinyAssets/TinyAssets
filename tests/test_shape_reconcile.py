"""The reconciler edits its one issue instead of posting updates or duplicates."""

from scripts import shape_reconcile


def test_row_line_accepts_a_utf8_bom():
    assert shape_reconcile.declared_rows("\ufeffRegistry row(s): S21, B1\n") == ["B1", "S21"]


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
