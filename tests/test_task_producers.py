"""Phase C.4 — producer wrappers + dispatcher + ON/OFF identity test.

Per taskproducer_phase_c.md (archived in git) §5 + §7. The load-bearing step:
wrapping existing logic as producers must not change observable
behavior. The identity test asserts byte-identical WorkTargets across
flag settings after timestamp normalization.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from domains.fantasy_daemon.producers import (
    FantasyAuthorialProducer,
    SeedProducer,
    UserRequestProducer,
)
from tinyassets.producers import (
    TaskProducer,
    producer_interface_enabled,
    registered_producers,
    reset_registry,
    run_producers,
)
from tinyassets.work_targets import (
    ROLE_NOTES,
    WorkTarget,
    choose_authorial_targets,
    ensure_seed_targets,
)


@pytest.fixture
def universe_dir(tmp_path):
    d = tmp_path / "test-universe"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts with a clean registry (domain init may have
    registered producers at import time).

    Teardown re-registers the fantasy producers so subsequent test
    files (e.g. test_submit_request_wiring) that rely on the default
    producer-on path see the domain's normal registry state. Phase
    C.5 made the authorial phase read the live registry, so leaking
    an empty registry regresses downstream suites.
    """
    reset_registry()
    yield
    reset_registry()
    _reload_fantasy_init()


def _reload_fantasy_init() -> None:
    """Re-import ``domains.fantasy_daemon`` to re-register producers."""
    from domains import fantasy_daemon
    fantasy_daemon._init_producers()


# ─── Producer conformance ──────────────────────────────────────────────


def test_seed_producer_matches_protocol():
    assert isinstance(SeedProducer(), TaskProducer)


def test_fantasy_authorial_producer_matches_protocol():
    assert isinstance(FantasyAuthorialProducer(), TaskProducer)


def test_user_request_producer_matches_protocol():
    assert isinstance(UserRequestProducer(), TaskProducer)


def test_producer_origins_are_spec_values():
    assert SeedProducer().origin == "seed"
    assert FantasyAuthorialProducer().origin == "fantasy_authorial"
    assert UserRequestProducer().origin == "user_request"


# ─── Fantasy registration order ────────────────────────────────────────


def test_fantasy_init_registers_in_spec_order():
    _reload_fantasy_init()
    names = [p.name for p in registered_producers()]
    assert names == ["seed", "fantasy_authorial", "user_request"]


# ─── Individual producer behaviour ─────────────────────────────────────


def test_seed_producer_emits_notes_target_for_empty_universe(universe_dir):
    produced = SeedProducer().produce(universe_dir)
    assert any(t.target_id == "universe-notes" for t in produced)


def test_seed_producer_reads_premise_from_program_md(universe_dir):
    (universe_dir / "PROGRAM.md").write_text(
        "A glass kingdom in decline.", encoding="utf-8",
    )
    produced = SeedProducer().produce(universe_dir)
    assert any(t.target_id == "book-1" for t in produced)


def test_fantasy_authorial_producer_emits_selectable_targets(universe_dir):
    # Seed the registry with a notes target.
    ensure_seed_targets(universe_dir, premise="")
    produced = FantasyAuthorialProducer().produce(universe_dir)
    assert any(t.role == ROLE_NOTES for t in produced)


def test_user_request_producer_materializes_pending(universe_dir):
    (universe_dir / "requests.json").write_text(
        json.dumps([{
            "id": "req_x", "type": "general", "text": "hi",
            "status": "pending",
        }]),
        encoding="utf-8",
    )
    produced = UserRequestProducer().produce(universe_dir)
    assert len(produced) == 1
    assert "user-request" in produced[0].tags


# ─── Dispatcher semantics ──────────────────────────────────────────────


def test_dispatcher_stamps_origin_from_producer(universe_dir):
    """Spec §7: even if a producer emits origin='wrong', the dispatcher
    overrides to producer.origin."""
    from tinyassets.producers import register

    class MisLabelledProducer:
        name = "mislabeled"
        origin = "correct_origin"

        def produce(self, universe_path, *, config=None):
            return [WorkTarget(
                target_id="x",
                title="x",
                origin="wrong",  # producer sets a bad value
            )]

    register(MisLabelledProducer())
    merged = run_producers(universe_dir)
    assert merged[0].origin == "correct_origin"


def test_dispatcher_logs_and_skips_failing_producer(
    universe_dir, caplog,
):
    """One bad producer doesn't break the cycle."""
    from tinyassets.producers import register

    class FailingProducer:
        name = "broken"
        origin = "broken"
        def produce(self, universe_path, *, config=None):
            raise RuntimeError("oops")

    class OKProducer:
        name = "ok"
        origin = "ok"
        def produce(self, universe_path, *, config=None):
            return [WorkTarget(target_id="survivor", title="t")]

    register(FailingProducer())
    register(OKProducer())
    import logging
    with caplog.at_level(logging.WARNING, logger="tinyassets.producers"):
        merged = run_producers(universe_dir)
    assert len(merged) == 1
    assert merged[0].target_id == "survivor"
    assert any("broken" in rec.message for rec in caplog.records)


def test_dispatcher_last_write_wins_on_target_id(universe_dir, caplog):
    """Two producers emitting same target_id → later wins, warn logged."""
    from tinyassets.producers import register

    class First:
        name = "first"
        origin = "first"
        def produce(self, universe_path, *, config=None):
            return [WorkTarget(
                target_id="shared", title="first version",
            )]

    class Second:
        name = "second"
        origin = "second"
        def produce(self, universe_path, *, config=None):
            return [WorkTarget(
                target_id="shared", title="second version",
            )]

    register(First())
    register(Second())
    import logging
    with caplog.at_level(logging.WARNING, logger="tinyassets.producers"):
        merged = run_producers(universe_dir)
    assert len(merged) == 1
    assert merged[0].title == "second version"
    assert merged[0].origin == "second"
    assert any("last-write-wins" in rec.message for rec in caplog.records)


# ─── candidate_override back-compat ────────────────────────────────────


def test_choose_authorial_targets_respects_candidate_override(universe_dir):
    """Passing an override list skips the built-in seed + list_selectable."""
    override = [
        WorkTarget(target_id="custom", title="Injected", role=ROLE_NOTES),
    ]
    result = choose_authorial_targets(
        universe_dir, candidate_override=override,
    )
    assert [t.target_id for t in result] == ["custom"]


def test_choose_authorial_targets_without_override_uses_file(universe_dir):
    """Backwards-compatible path — no override → reads from disk as
    before. Load-bearing: this is the flag-off path."""
    ensure_seed_targets(universe_dir, premise="")
    result = choose_authorial_targets(universe_dir)
    assert any(t.target_id == "universe-notes" for t in result)


# ─── Feature flag ──────────────────────────────────────────────────────


def test_producer_interface_enabled_default_on(monkeypatch):
    monkeypatch.delenv("TINYASSETS_PRODUCER_INTERFACE", raising=False)
    assert producer_interface_enabled() is True


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "no"])
def test_producer_interface_flag_off_values(monkeypatch, value):
    monkeypatch.setenv("TINYASSETS_PRODUCER_INTERFACE", value)
    assert producer_interface_enabled() is False


# ─── Load-bearing identity test ────────────────────────────────────────


def _normalize(targets: list[WorkTarget]) -> list[dict]:
    """Strip timestamp + origin fields for byte-identity comparison.

    ``updated_at`` / ``created_at`` drift across runs is legitimate
    wall-clock difference (spec §5 qualifier). ``origin`` is expected
    to differ: flag-off path yields default `"unknown"`; flag-on path
    stamps the producer origin. Both are correct under their respective
    contracts — the identity claim is about the *selection*, not the
    attribution stamp.
    """
    out = []
    for t in sorted(targets, key=lambda x: x.target_id):
        d = asdict(t)
        d.pop("updated_at", None)
        d.pop("created_at", None)
        d.pop("origin", None)
        out.append(d)
    return out


def test_fantasy_universe_cycle_produces_identical_targets_with_flag_on_vs_off(
    universe_dir,
):
    """Load-bearing: producer-on path and producer-off path yield the
    same merged WorkTarget set for the same inputs.

    This is the refactor safety net. Without it, C.4 could silently
    change behavior because the call-site split in C.5 depends on the
    two code paths being equivalent.
    """
    # Seed with a premise so both paths see the full seed.
    (universe_dir / "PROGRAM.md").write_text(
        "Glass kingdom.", encoding="utf-8",
    )
    # And a pending request so UserRequestProducer has work.
    (universe_dir / "requests.json").write_text(
        json.dumps([{
            "id": "req_identity_test",
            "type": "general",
            "text": "test the producer pipeline",
            "status": "pending",
        }]),
        encoding="utf-8",
    )

    # Flag-OFF path: direct call to choose_authorial_targets (same as
    # today's authorial_priority_review). No producers registered.
    reset_registry()
    off_targets = choose_authorial_targets(
        universe_dir,
        premise="Glass kingdom.",
    )
    # The current authorial_priority_review also calls materialize
    # after ensure_seed_targets. Mirror that so the two paths are
    # comparing the same "what's live" set.
    from tinyassets.work_targets import materialize_pending_requests
    materialize_pending_requests(universe_dir)
    off_targets = choose_authorial_targets(
        universe_dir,
        premise="Glass kingdom.",
    )

    # Reset universe for a clean flag-ON run.
    (universe_dir / "work_targets.json").unlink(missing_ok=True)
    (universe_dir / "requests.json").write_text(
        json.dumps([{
            "id": "req_identity_test",
            "type": "general",
            "text": "test the producer pipeline",
            "status": "pending",
        }]),
        encoding="utf-8",
    )

    # Flag-ON path: register fantasy producers, run them, feed merged
    # set through choose_authorial_targets via candidate_override.
    _reload_fantasy_init()
    merged = run_producers(universe_dir)
    on_targets = choose_authorial_targets(
        universe_dir,
        premise="Glass kingdom.",
        candidate_override=merged,
    )

    # Identity check — normalized (strip timestamps + origin).
    assert _normalize(off_targets) == _normalize(on_targets), (
        "flag-on vs flag-off yield different WorkTarget selections — "
        "producer refactor changed observable behavior"
    )


# ─── C.5 — call site wiring ────────────────────────────────────────────


def test_authorial_review_dispatches_through_producers_when_flag_on(
    universe_dir, monkeypatch,
):
    """Phase C.5: flag-on → authorial_priority_review runs the registered
    producer list and feeds merged output to choose_authorial_targets.

    Pins the branch by registering a single sentinel producer and
    verifying its target is the one selected.
    """
    from domains.fantasy_daemon.phases.authorial_priority_review import (
        authorial_priority_review,
    )
    from tinyassets.producers import register

    monkeypatch.setenv("TINYASSETS_PRODUCER_INTERFACE", "on")
    (universe_dir / "PROGRAM.md").write_text(
        "Ignored in producer path.", encoding="utf-8",
    )

    class SentinelProducer:
        name = "sentinel"
        origin = "seed"

        def produce(self, universe_path, *, config=None):
            return [WorkTarget(
                target_id="sentinel-only",
                title="Only target the phase should see",
                role=ROLE_NOTES,
            )]

    register(SentinelProducer())

    result = authorial_priority_review({
        "_universe_path": str(universe_dir),
        "workflow_instructions": {"premise": "Glass kingdom."},
    })

    assert result["selected_target_id"] == "sentinel-only"


def test_authorial_review_skips_producers_when_flag_off(
    universe_dir, monkeypatch,
):
    """Phase C.5: flag-off → producer list ignored; direct-call path
    still reads from work_targets.json via choose_authorial_targets."""
    from domains.fantasy_daemon.phases.authorial_priority_review import (
        authorial_priority_review,
    )
    from tinyassets.producers import register

    monkeypatch.setenv("TINYASSETS_PRODUCER_INTERFACE", "off")

    class SentinelProducer:
        name = "sentinel"
        origin = "seed"

        def produce(self, universe_path, *, config=None):
            return [WorkTarget(
                target_id="sentinel-only",
                title="Should be ignored in flag-off path",
                role=ROLE_NOTES,
            )]

    register(SentinelProducer())

    result = authorial_priority_review({
        "_universe_path": str(universe_dir),
        "workflow_instructions": {"premise": "Glass kingdom."},
    })

    # Flag-off path seeds universe-notes + book-1, ignoring the sentinel.
    assert result["selected_target_id"] != "sentinel-only"
    assert result["selected_target_id"] is not None
