"""Tests for TINYASSETS_DEBUG_CONTEXT=1 instrumentation.

Task #3 diagnostic pass: when CoreMemory exceeds budget, a flag-gated
per-field breakdown logs the culprits at WARNING level. Off by default
so prod is silent unless operator flips the flag.
"""
from __future__ import annotations

import logging

import pytest

from tinyassets.exceptions import ContextBundleOverflowError
from tinyassets.memory.manager import (
    MAX_CONTEXT_TOKENS,
    MemoryManager,
    _debug_context_enabled,
)


class TestDebugContextFlag:
    def test_flag_off_by_default(self, monkeypatch):
        monkeypatch.delenv("TINYASSETS_DEBUG_CONTEXT", raising=False)
        assert _debug_context_enabled() is False

    @pytest.mark.parametrize("value", ["1", "on", "true", "yes", "TRUE", "Yes"])
    def test_flag_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("TINYASSETS_DEBUG_CONTEXT", value)
        assert _debug_context_enabled() is True

    @pytest.mark.parametrize("value", ["0", "off", "false", "no", ""])
    def test_flag_falsy_values(self, monkeypatch, value):
        monkeypatch.setenv("TINYASSETS_DEBUG_CONTEXT", value)
        assert _debug_context_enabled() is False


def _oversized_state() -> dict:
    """Build a state dict whose orient_result overflows CoreMemory.

    _assemble_orient calls core.clear() then loads from orient_result, so
    the oversized payload must ride in on the state, not be pre-seeded.
    """
    huge_blob = "x" * (MAX_CONTEXT_TOKENS * 4 * 2)
    return {
        "chapter_number": 1,
        "orient_result": {
            "characters": [{"id": "ryn", "bio": huge_blob}],
            "world_state": {"locations": huge_blob},
        },
    }


class TestBreakdownLogging:
    def test_breakdown_emitted_when_flag_on(self, canonical_source, monkeypatch, caplog):
        monkeypatch.setenv("TINYASSETS_DEBUG_CONTEXT", "1")
        mgr = MemoryManager(
            universe_id="debug-test",
            source=canonical_source("episodic", key=":memory:", namespaces=["debug-test"]),
        )
        state = _oversized_state()

        # Irreducible world_state payload → trim cannot reach budget →
        # BUG-024 fix raises ContextBundleOverflowError (loud, not silent).
        # The debug breakdown should still emit before the raise.
        with caplog.at_level(logging.WARNING, logger="tinyassets.memory.manager"):
            with pytest.raises(ContextBundleOverflowError):
                mgr.assemble_context("orient", state)

        mgr.close()

        messages = [rec.getMessage() for rec in caplog.records]
        assert any("exceeds budget" in m for m in messages)
        assert any("CONTEXT-DEBUG core breakdown" in m for m in messages)
        assert any("CONTEXT-DEBUG bundle breakdown" in m for m in messages)

    def test_breakdown_silent_when_flag_off(self, canonical_source, monkeypatch, caplog):
        monkeypatch.delenv("TINYASSETS_DEBUG_CONTEXT", raising=False)
        mgr = MemoryManager(
            universe_id="debug-test",
            source=canonical_source("episodic", key=":memory:", namespaces=["debug-test"]),
        )
        state = _oversized_state()

        with caplog.at_level(logging.WARNING, logger="tinyassets.memory.manager"):
            with pytest.raises(ContextBundleOverflowError):
                mgr.assemble_context("orient", state)

        mgr.close()

        messages = [rec.getMessage() for rec in caplog.records]
        assert any("exceeds budget" in m for m in messages)
        assert not any("CONTEXT-DEBUG" in m for m in messages)

    def test_breakdown_not_emitted_when_under_budget(self, canonical_source, monkeypatch, caplog):
        monkeypatch.setenv("TINYASSETS_DEBUG_CONTEXT", "1")
        mgr = MemoryManager(
            universe_id="debug-test",
            source=canonical_source("episodic", key=":memory:", namespaces=["debug-test"]),
        )

        state = {
            "chapter_number": 1,
            "orient_result": {
                "characters": [{"id": "ryn", "bio": "short bio"}],
                "world_state": {"time": "dawn"},
            },
        }

        with caplog.at_level(logging.WARNING, logger="tinyassets.memory.manager"):
            mgr.assemble_context("orient", state)

        mgr.close()

        messages = [rec.getMessage() for rec in caplog.records]
        # No overflow → no debug output, even with flag on.
        assert not any("CONTEXT-DEBUG" in m for m in messages)
