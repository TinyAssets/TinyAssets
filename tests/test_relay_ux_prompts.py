"""The engine reply turn is sandboxed (2026-07-03 live-test finding)."""
from __future__ import annotations

import tinyassets.universe_intelligence as ui


def test_engine_sandbox_denies_host_tools():
    # web-only for the reply turn; host tools + filesystem denied
    assert ui._ENGINE_ALLOWED_TOOLS == ("WebFetch",)
    for denied in ("Bash", "Read", "Write", "Edit", "WebSearch", "Task", "Glob", "Grep"):
        assert denied in ui._ENGINE_DISALLOWED_TOOLS
