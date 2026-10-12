"""Phase C.1 — `origin` field on WorkTarget.

Per taskproducer_phase_c.md (archived in git) §3 + §7. Pure schema addition:
the dataclass gains `origin: str = "unknown"`; no consumer in C.1.
C.4 wires actual producers to stamp authoritative values.

Asserted contracts:
- Legacy `work_targets.json` rows (no `origin` key) load with
  `origin="unknown"`.
- New WorkTargets can stamp explicit origin; it round-trips through
  `to_dict` / `from_dict`.
- `origin` is a plain `str` — not an Enum type — so any string value
  is accepted and producers can declare new origins without a
  central registry.
"""

from __future__ import annotations

from tinyassets.work_targets import WorkTarget


def test_legacy_worktarget_loads_with_unknown_origin():
    """Pre-Phase-C `work_targets.json` rows lack `origin`; default fills in."""
    legacy_payload = {
        "target_id": "old-target-42",
        "title": "Pre-C.1 target",
        "role": "notes",
        "publish_stage": "none",
        "lifecycle": "active",
        "current_intent": "legacy",
        "tags": ["old"],
        "metadata": {"auto_created": True},
        "created_at": 1700000000.0,
        "updated_at": 1700000000.0,
    }
    target = WorkTarget.from_dict(legacy_payload)
    assert target.origin == "unknown"
    # Other fields must still round-trip normally.
    assert target.target_id == "old-target-42"
    assert target.lifecycle == "active"


def test_new_worktarget_serializes_origin():
    """Explicit origin survives to_dict → from_dict round-trip."""
    target = WorkTarget(
        target_id="fresh-1",
        title="Fresh target",
        origin="user_request",
    )
    payload = target.to_dict()
    assert payload["origin"] == "user_request"
    rehydrated = WorkTarget.from_dict(payload)
    assert rehydrated.origin == "user_request"


def test_origin_values_are_strings_not_enum():
    """Origin is plain `str` — producers can declare new origins without
    a central Enum registry (spec §3.Q1 deferred to a later phase)."""
    target = WorkTarget(
        target_id="x",
        title="t",
        origin="custom_producer_42",
    )
    assert isinstance(target.origin, str)
    assert target.origin == "custom_producer_42"


def test_worktarget_default_origin_is_unknown():
    """Freshly-constructed WorkTarget with no origin arg → 'unknown'."""
    target = WorkTarget(target_id="x", title="t")
    assert target.origin == "unknown"


def test_origin_nonstring_data_is_coerced():
    """from_dict wraps origin in str(...) — int/None payloads don't
    crash the loader. Paranoid guard for hand-edited JSON."""
    target = WorkTarget.from_dict({
        "target_id": "coerce",
        "title": "t",
        "origin": 123,
    })
    assert target.origin == "123"
