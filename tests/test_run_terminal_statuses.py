"""tinyassets.runs defines its terminal run statuses exactly once.

They were bound twice at module level (string literals near the long-poll, and
again from the RUN_STATUS_* constants near the child-run poller). The values
agreed, but the later binding silently replaced the earlier one, so an edit to
either copy would change behaviour for code next to the OTHER copy.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tinyassets import runs

_SOURCE = Path(runs.__file__).read_text(encoding="utf-8")


def test_terminal_statuses_are_bound_once_at_module_level():
    bindings = [
        node.lineno
        for node in ast.parse(_SOURCE).body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "_TERMINAL_STATUSES"
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    ]
    assert len(bindings) == 1, f"_TERMINAL_STATUSES bound at lines {bindings}"


def test_terminal_statuses_are_the_four_end_states():
    assert runs._TERMINAL_STATUSES == {
        runs.RUN_STATUS_COMPLETED,
        runs.RUN_STATUS_FAILED,
        runs.RUN_STATUS_CANCELLED,
        runs.RUN_STATUS_INTERRUPTED,
    }
    assert runs.RUN_STATUS_RUNNING not in runs._TERMINAL_STATUSES
    assert runs.RUN_STATUS_QUEUED not in runs._TERMINAL_STATUSES
