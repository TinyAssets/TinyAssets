"""The daemon's side of a workspace git operation (``workspace_worker``).

The route and the cell are gone: an operation is projected onto the admitted
owner box (``tests/test_c6_owner_box_callers.py`` drives that projection). What
stays here is the door's own contract: a malformed request is refused before any
authority is read, and the reconciliation entry runs.py calls is exported.
"""
from __future__ import annotations

from tinyassets import workspace_worker as ww

SHA = "a" * 40


def test_a_non_mapping_request_is_refused() -> None:
    for bad in (None, [], "checkout", 7):
        answer = ww.execute_workspace_operation(bad)
        assert answer["ok"] is False
        assert answer["stderr_class"] == "bad_argument"


def test_an_unknown_op_is_refused_before_any_authority_read() -> None:
    answer = ww.execute_workspace_operation({"op": "format-disk", "universe_dir": "/x"})
    assert answer["ok"] is False
    assert answer["stderr_class"] == "bad_argument"


def test_execute_uses_the_injected_spawn() -> None:
    """``spawn`` stays injectable: the callers' own tests ride on it."""
    seen: list[dict] = []

    def spawn(request):
        seen.append(request)
        return {"ok": True, "resolved_sha": SHA}

    answer = ww.execute_workspace_operation({"op": "checkout"}, spawn=spawn)
    assert answer == {"ok": True, "resolved_sha": SHA}
    assert seen == [{"op": "checkout"}]


def test_reconcile_push_intents_is_exported_where_runs_py_calls_it() -> None:
    assert callable(ww.reconcile_push_intents)
    assert "reconcile_push_intents" in ww.__all__
