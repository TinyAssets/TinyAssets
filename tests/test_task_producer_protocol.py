"""Phase C.3 — TaskProducer protocol + registry.

Per docs/specs/taskproducer_phase_c.md §1. Pure infra ship: no
producers register in C.3, so the registry is empty at import time.
Tests cover the protocol shape + registry semantics (idempotent
re-register, order-preserving iteration, reset hook).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets.producers import (
    TaskProducer,
    register,
    registered_producers,
    reset_registry,
    unregister,
)
from tinyassets.work_targets import WorkTarget


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts with an empty registry, and the process gets its
    registry BACK afterwards.

    Resetting to empty at teardown (as this used to) left every later test in
    the process with no producers at all. The authorial phase reads the live
    registry, so test_work_targets'
    test_authorial_review_returns_one_target_and_alternates then selected
    nothing -- on two unrelated PRs' affected-tests runs, 2026-10-02.
    """
    import tinyassets.producers as producers

    saved = list(producers._REGISTRY)
    reset_registry()
    yield
    reset_registry()
    producers._REGISTRY.extend(saved)


# ─── Protocol shape ────────────────────────────────────────────────────


class _ConformingProducer:
    """Minimal producer matching the protocol structurally."""

    name = "test_producer"
    origin = "test"

    def produce(
        self,
        universe_path: Path,
        *,
        config: dict | None = None,
    ) -> list[WorkTarget]:
        return []


class _MissingMethodProducer:
    """Has attrs but no produce()."""
    name = "incomplete"
    origin = "test"


def test_conforming_producer_passes_isinstance():
    p = _ConformingProducer()
    assert isinstance(p, TaskProducer)


def test_missing_produce_fails_isinstance():
    p = _MissingMethodProducer()
    assert not isinstance(p, TaskProducer)


def test_protocol_requires_name_origin_produce():
    """Sanity — structural contract. A class missing `name` isn't a
    TaskProducer even if it has `origin` + `produce`."""

    class _NoName:
        origin = "x"

        def produce(self, universe_path, *, config=None):
            return []

    assert not isinstance(_NoName(), TaskProducer)


# ─── Registry semantics ────────────────────────────────────────────────


def test_empty_registry_at_import_time():
    """Phase C.3 ships with zero producers — C.4 registers them."""
    assert registered_producers() == ()


def test_register_appends():
    p1 = _ConformingProducer()
    register(p1)
    assert registered_producers() == (p1,)


def _named(name: str) -> _ConformingProducer:
    p = _ConformingProducer()
    p.name = name
    return p


def test_registration_order_preserved():
    a, b, c = _named("a"), _named("b"), _named("c")
    register(a)
    register(b)
    register(c)
    names = [p.name for p in registered_producers()]
    assert names == ["a", "b", "c"]


def test_register_idempotent_on_name():
    """Re-registering same name replaces the earlier instance."""
    first, second = _named("shared"), _named("shared")
    register(first)
    register(second)
    producers = registered_producers()
    assert len(producers) == 1
    assert producers[0] is second


def test_re_register_moves_to_end():
    """Re-registering bumps the producer to the end — registration
    order IS dispatch order, and a fresh registration signals "new"."""
    a, b = _named("a"), _named("b")
    register(a)
    register(b)
    register(a)  # bump `a` to end
    assert [p.name for p in registered_producers()] == ["b", "a"]


def test_registered_producers_returns_tuple_not_list():
    """Immutable return type — callers can't mutate registry indirectly."""
    register(_ConformingProducer())
    result = registered_producers()
    assert isinstance(result, tuple)


def test_unregister_removes_by_name():
    register(_named("gone"))
    assert unregister("gone") is True
    assert registered_producers() == ()


def test_unregister_missing_name_returns_false():
    assert unregister("never_registered") is False


def test_reset_registry_clears_all():
    register(_ConformingProducer())
    register(_ConformingProducer())  # same name; replaces
    reset_registry()
    assert registered_producers() == ()
