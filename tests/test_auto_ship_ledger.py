"""Tests for ``tinyassets.auto_ship_ledger`` (PR #198 §8 — Slice A).

Spec source: ``auto-ship-canary-v0.md (archived in git)`` §8.

Coverage:
- ID generation shape + collision resistance
- ``ShipAttempt`` dataclass round-trip + forward-compat (unknown keys)
- ``record_attempt`` happy path + duplicate detection + status validation
- ``update_attempt`` mutable-only enforcement + auto updated_at + KeyError
- ``read_attempts`` filters (ship_status + limit) + missing file = []
- Concurrent appenders via threads — file lock contract
- Corrupt JSONL row raises ``RuntimeError`` (Hard Rule 8 — no silent skip)
- ``attempt_from_decision`` Phase 1 conversion (passed → skipped, blocked
  → blocked + violations encoded in error_message)
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tinyassets.auto_ship_ledger import (
    LEDGER_FILENAME,
    MUTABLE_FIELDS,
    VALID_SHIP_STATUSES,
    ShipAttempt,
    attempt_from_decision,
    find_attempt,
    ledger_path,
    new_attempt_id,
    read_attempts,
    record_attempt,
    summarize_rubric_warnings,
    summarize_trajectory_warnings,
    update_attempt,
)


@pytest.fixture(autouse=True)
def _ledger_data_root(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path.parent))

# ── rubric warn-mode observability ─────────────────────────────────────────


def test_attempt_from_decision_persists_rubric_warnings():
    decision = {
        "validation_result": "passed",
        "would_open_pr": True,
        "violations": [],
        "rollback_handle": "revert:x",
        "rubric_warnings": [
            {"rule_id": "release_evidence_bundle_incomplete", "field": "x"},
            {"rule_id": "child_run_not_completed_for_keep", "field": "y"},
        ],
    }
    row = attempt_from_decision(decision=decision, request_id="r1")
    parsed = json.loads(row.rubric_warnings_json)
    assert {w["rule_id"] for w in parsed} == {
        "release_evidence_bundle_incomplete",
        "child_run_not_completed_for_keep",
    }


def test_attempt_from_decision_empty_warnings_default():
    row = attempt_from_decision(
        decision={"validation_result": "passed", "would_open_pr": True},
        request_id="r2",
    )
    assert row.rubric_warnings_json == "[]"


def test_from_dict_defaults_rubric_warnings_for_old_rows():
    old = {
        "ship_attempt_id": "ship_20260101_aaaaaaaa",
        "created_at": "t",
        "updated_at": "t",
        "ship_status": "skipped",
    }
    assert ShipAttempt.from_dict(old).rubric_warnings_json == ""


def test_summarize_rubric_warnings_aggregates(tmp_path):
    warn_sets = [
        [{"rule_id": "release_evidence_bundle_incomplete"}],
        [
            {"rule_id": "release_evidence_bundle_incomplete"},
            {"rule_id": "child_run_not_completed_for_keep"},
        ],
        [],
    ]
    for i, warns in enumerate(warn_sets):
        dec = {
            "validation_result": "passed",
            "would_open_pr": True,
            "rubric_warnings": warns,
        }
        record_attempt(tmp_path, attempt_from_decision(decision=dec, request_id=f"r{i}"))
    summary = summarize_rubric_warnings(tmp_path)
    assert summary["total_attempts"] == 3
    assert summary["attempts_with_warnings"] == 2
    assert summary["rule_id_counts"]["release_evidence_bundle_incomplete"] == 2
    assert summary["rule_id_counts"]["child_run_not_completed_for_keep"] == 1


def test_summarize_rubric_warnings_robust_to_malformed(tmp_path):
    # empty ledger -> zeros, no side effects, no crash
    assert summarize_rubric_warnings(tmp_path) == {
        "total_attempts": 0,
        "attempts_with_warnings": 0,
        "rule_id_counts": {},
    }
    # a row whose rubric_warnings_json is valid JSON but NOT a list is skipped
    row = attempt_from_decision(
        decision={"validation_result": "passed", "would_open_pr": True},
        request_id="rx",
    )
    row.rubric_warnings_json = "1"  # valid JSON, not a list
    record_attempt(tmp_path, row)
    summary = summarize_rubric_warnings(tmp_path)
    assert summary["total_attempts"] == 1
    assert summary["attempts_with_warnings"] == 0


# ── trajectory warn-mode observability (separate channel from rubric) ──────


def test_attempt_from_decision_persists_trajectory_warnings():
    decision = {
        "validation_result": "passed",
        "would_open_pr": True,
        "violations": [],
        "rollback_handle": "revert:x",
        "trajectory_warnings": [
            {"event": "trajectory_warning", "check": "child_integrity", "score": 0.3},
            {"event": "trajectory_warning", "check": "terminal_health", "score": 0.1},
        ],
    }
    row = attempt_from_decision(decision=decision, request_id="t1")
    parsed = json.loads(row.trajectory_warnings_json)
    assert {w["check"] for w in parsed} == {"child_integrity", "terminal_health"}
    # separate channel — rubric stays empty
    assert row.rubric_warnings_json == "[]"


def test_attempt_from_decision_empty_trajectory_warnings_default():
    row = attempt_from_decision(
        decision={"validation_result": "passed", "would_open_pr": True},
        request_id="t2",
    )
    assert row.trajectory_warnings_json == "[]"


def test_from_dict_defaults_trajectory_warnings_for_old_rows():
    old = {
        "ship_attempt_id": "ship_20260101_bbbbbbbb",
        "created_at": "t",
        "updated_at": "t",
        "ship_status": "skipped",
    }
    assert ShipAttempt.from_dict(old).trajectory_warnings_json == ""


def test_summarize_trajectory_warnings_aggregates(tmp_path):
    warn_sets = [
        [{"check": "child_integrity"}],
        [{"check": "child_integrity"}, {"check": "terminal_health"}],
        [],
    ]
    for i, warns in enumerate(warn_sets):
        dec = {
            "validation_result": "passed",
            "would_open_pr": True,
            "trajectory_warnings": warns,
        }
        record_attempt(tmp_path, attempt_from_decision(decision=dec, request_id=f"t{i}"))
    summary = summarize_trajectory_warnings(tmp_path)
    assert summary["total_attempts"] == 3
    assert summary["attempts_with_warnings"] == 2
    assert summary["check_counts"]["child_integrity"] == 2
    assert summary["check_counts"]["terminal_health"] == 1


def test_summarize_trajectory_warnings_robust_to_malformed(tmp_path):
    assert summarize_trajectory_warnings(tmp_path) == {
        "total_attempts": 0,
        "attempts_with_warnings": 0,
        "check_counts": {},
    }
    row = attempt_from_decision(
        decision={"validation_result": "passed", "would_open_pr": True},
        request_id="tx",
    )
    row.trajectory_warnings_json = "1"  # valid JSON, not a list
    record_attempt(tmp_path, row)
    summary = summarize_trajectory_warnings(tmp_path)
    assert summary["total_attempts"] == 1
    assert summary["attempts_with_warnings"] == 0


# ── ID generation ─────────────────────────────────────────────────────────


def test_new_attempt_id_shape_matches_spec_example():
    fixed = datetime(2026, 5, 2, 0, 0, 0, tzinfo=timezone.utc)
    aid = new_attempt_id(now=fixed)
    assert aid.startswith("ship_20260502_"), aid
    # spec example shows 8-hex tail (token_hex(4))
    assert len(aid.split("_")[-1]) == 8


def test_new_attempt_id_unique_in_same_second():
    fixed = datetime(2026, 5, 2, 0, 0, 0, tzinfo=timezone.utc)
    ids = {new_attempt_id(now=fixed) for _ in range(200)}
    # 200 calls in the same second should not collide — token_hex(4) is
    # 32 bits of entropy, birthday bound is ~64K.
    assert len(ids) == 200


# ── ShipAttempt round-trip ────────────────────────────────────────────────


def test_shipattempt_to_from_dict_round_trip():
    a = ShipAttempt(
        ship_attempt_id="ship_x",
        created_at="2026-05-02T00:00:00+00:00",
        updated_at="2026-05-02T00:00:00+00:00",
        ship_status="skipped",
        request_id="REQ-1",
        ship_class="docs_canary",
        would_open_pr=True,
    )
    d = a.to_dict()
    a2 = ShipAttempt.from_dict(d)
    assert a == a2


def test_shipattempt_from_dict_ignores_unknown_keys():
    """Forward compat — older runtime should still read newer rows."""
    row = {
        "ship_attempt_id": "ship_x",
        "created_at": "2026-05-02T00:00:00+00:00",
        "updated_at": "2026-05-02T00:00:00+00:00",
        "ship_status": "skipped",
        "future_field_we_dont_know_yet": "ignored",
    }
    a = ShipAttempt.from_dict(row)
    assert a.ship_attempt_id == "ship_x"


# ── record_attempt ────────────────────────────────────────────────────────


def test_record_attempt_creates_file(tmp_path: Path):
    a = ShipAttempt(
        ship_attempt_id="ship_1",
        created_at="",  # will be auto-stamped
        updated_at="",
        ship_status="skipped",
    )
    record_attempt(tmp_path, a)
    lp = ledger_path(tmp_path)
    assert lp.exists()
    # JSONL — exactly one line
    lines = lp.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["ship_attempt_id"] == "ship_1"
    assert row["created_at"]  # auto-stamped
    assert row["updated_at"] == row["created_at"]


def test_record_attempt_appends_not_rewrites(tmp_path: Path):
    for i in range(3):
        record_attempt(tmp_path, ShipAttempt(
            ship_attempt_id=f"ship_{i}",
            created_at="",
            updated_at="",
            ship_status="skipped",
        ))
    lp = ledger_path(tmp_path)
    lines = lp.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


def test_record_attempt_rejects_duplicate_id(tmp_path: Path):
    a = ShipAttempt(
        ship_attempt_id="ship_dup",
        created_at="",
        updated_at="",
        ship_status="skipped",
    )
    record_attempt(tmp_path, a)
    with pytest.raises(ValueError, match="already"):
        record_attempt(tmp_path, a)


def test_record_attempt_rejects_unknown_ship_status(tmp_path: Path):
    a = ShipAttempt(
        ship_attempt_id="ship_x",
        created_at="",
        updated_at="",
        ship_status="garbage",
    )
    with pytest.raises(ValueError, match="Invalid ship_status"):
        record_attempt(tmp_path, a)


def test_record_attempt_rejects_empty_id(tmp_path: Path):
    a = ShipAttempt(
        ship_attempt_id="",
        created_at="",
        updated_at="",
        ship_status="skipped",
    )
    with pytest.raises(ValueError, match="ship_attempt_id is required"):
        record_attempt(tmp_path, a)


# ── read_attempts ─────────────────────────────────────────────────────────


def test_read_attempts_empty_when_missing(tmp_path: Path):
    assert read_attempts(tmp_path) == []


def test_read_attempts_returns_in_append_order(tmp_path: Path):
    for i in range(5):
        record_attempt(tmp_path, ShipAttempt(
            ship_attempt_id=f"ship_{i}",
            created_at="",
            updated_at="",
            ship_status="skipped",
            request_id=f"R{i}",
        ))
    rows = read_attempts(tmp_path)
    assert [r.ship_attempt_id for r in rows] == [f"ship_{i}" for i in range(5)]


def test_read_attempts_ship_status_filter(tmp_path: Path):
    for status, idx in [("skipped", 0), ("blocked", 1), ("skipped", 2)]:
        record_attempt(tmp_path, ShipAttempt(
            ship_attempt_id=f"ship_{idx}",
            created_at="",
            updated_at="",
            ship_status=status,
        ))
    skipped = read_attempts(tmp_path, ship_status="skipped")
    assert len(skipped) == 2
    assert all(r.ship_status == "skipped" for r in skipped)


def test_read_attempts_limit_returns_tail(tmp_path: Path):
    for i in range(10):
        record_attempt(tmp_path, ShipAttempt(
            ship_attempt_id=f"ship_{i:02d}",
            created_at="",
            updated_at="",
            ship_status="skipped",
        ))
    last3 = read_attempts(tmp_path, limit=3)
    assert [r.ship_attempt_id for r in last3] == ["ship_07", "ship_08", "ship_09"]


def test_read_attempts_limit_zero_returns_empty(tmp_path: Path):
    for i in range(3):
        record_attempt(tmp_path, ShipAttempt(
            ship_attempt_id=f"ship_{i:02d}",
            created_at="",
            updated_at="",
            ship_status="skipped",
        ))
    assert read_attempts(tmp_path, limit=0) == []


def test_read_attempts_filter_then_limit(tmp_path: Path):
    for i in range(10):
        status = "skipped" if i % 2 == 0 else "blocked"
        record_attempt(tmp_path, ShipAttempt(
            ship_attempt_id=f"ship_{i:02d}",
            created_at="",
            updated_at="",
            ship_status=status,
        ))
    # 5 skipped rows; tail 2 = ship_06 and ship_08
    last2_skipped = read_attempts(tmp_path, ship_status="skipped", limit=2)
    assert [r.ship_attempt_id for r in last2_skipped] == ["ship_06", "ship_08"]


def test_read_attempts_corrupt_row_raises(tmp_path: Path):
    """Hard Rule 8 — no silent fallback on corrupt data."""
    lp = ledger_path(tmp_path)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(
        '{"ship_attempt_id":"ship_a","created_at":"x","updated_at":"x",'
        '"ship_status":"skipped"}\n'
        'not-json\n',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Corrupt ledger row"):
        read_attempts(tmp_path)


def test_read_attempts_blank_lines_skipped(tmp_path: Path):
    """Blank lines between rows must not crash — git autocrlf, editor
    EOF newlines, etc. produce them."""
    lp = ledger_path(tmp_path)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(
        '\n'
        '{"ship_attempt_id":"ship_a","created_at":"x","updated_at":"x","ship_status":"skipped"}\n'
        '\n'
        '{"ship_attempt_id":"ship_b","created_at":"x","updated_at":"x","ship_status":"skipped"}\n'
        '\n',
        encoding="utf-8",
    )
    rows = read_attempts(tmp_path)
    assert [r.ship_attempt_id for r in rows] == ["ship_a", "ship_b"]


# ── find_attempt ─────────────────────────────────────────────────────────


def test_find_attempt_returns_none_when_missing(tmp_path: Path):
    assert find_attempt(tmp_path, "nope") is None


def test_find_attempt_returns_row(tmp_path: Path):
    record_attempt(tmp_path, ShipAttempt(
        ship_attempt_id="ship_x",
        created_at="",
        updated_at="",
        ship_status="skipped",
        request_id="REQ-X",
    ))
    found = find_attempt(tmp_path, "ship_x")
    assert found is not None
    assert found.request_id == "REQ-X"


# ── update_attempt ───────────────────────────────────────────────────────


def test_update_attempt_mutates_allowed_field(tmp_path: Path):
    record_attempt(tmp_path, ShipAttempt(
        ship_attempt_id="ship_a",
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:00:00+00:00",
        ship_status="skipped",
    ))
    upd = update_attempt(
        tmp_path, "ship_a",
        ship_status="opened", pr_url="https://github.com/x/y/pull/1",
    )
    assert upd.ship_status == "opened"
    assert upd.pr_url == "https://github.com/x/y/pull/1"
    # updated_at must bump; created_at must NOT
    assert upd.created_at == "2026-05-01T00:00:00+00:00"
    assert upd.updated_at != "2026-05-01T00:00:00+00:00"
    # Persistence
    rd = find_attempt(tmp_path, "ship_a")
    assert rd is not None and rd.ship_status == "opened"


def test_update_attempt_rejects_immutable_field(tmp_path: Path):
    record_attempt(tmp_path, ShipAttempt(
        ship_attempt_id="ship_a",
        created_at="",
        updated_at="",
        ship_status="skipped",
    ))
    with pytest.raises(ValueError, match="immutable"):
        update_attempt(tmp_path, "ship_a", request_id="changed")


def test_update_attempt_rejects_changing_created_at(tmp_path: Path):
    """created_at is in identity territory — once stamped at record
    time, no caller may change it. Same protection as ship_attempt_id
    (which Python's kwarg shadowing already protects)."""
    record_attempt(tmp_path, ShipAttempt(
        ship_attempt_id="ship_a",
        created_at="",
        updated_at="",
        ship_status="skipped",
    ))
    with pytest.raises(ValueError, match="immutable"):
        update_attempt(tmp_path, "ship_a", created_at="2026-01-01T00:00:00+00:00")


def test_update_attempt_rejects_invalid_ship_status(tmp_path: Path):
    record_attempt(tmp_path, ShipAttempt(
        ship_attempt_id="ship_a",
        created_at="",
        updated_at="",
        ship_status="skipped",
    ))
    with pytest.raises(ValueError, match="Invalid ship_status"):
        update_attempt(tmp_path, "ship_a", ship_status="garbage")


def test_update_attempt_raises_keyerror_on_missing(tmp_path: Path):
    with pytest.raises(KeyError, match="not found"):
        update_attempt(tmp_path, "no-such", ship_status="merged")


def test_update_attempt_caller_provided_updated_at_is_honored(tmp_path: Path):
    """Slice C / replay code may need to pin updated_at to a synthetic
    timestamp instead of now()."""
    record_attempt(tmp_path, ShipAttempt(
        ship_attempt_id="ship_a",
        created_at="",
        updated_at="",
        ship_status="skipped",
    ))
    pinned = "2026-05-02T12:00:00+00:00"
    upd = update_attempt(
        tmp_path, "ship_a",
        ship_status="opened", updated_at=pinned,
    )
    assert upd.updated_at == pinned


# ── concurrent append ───────────────────────────────────────────────────


def test_concurrent_appends_serialize_via_lock(tmp_path: Path):
    """Five threads each appending one row should produce exactly five
    rows in total — no lost writes from racing on the file."""
    errors: list[str] = []

    def worker(idx: int):
        try:
            record_attempt(tmp_path, ShipAttempt(
                ship_attempt_id=f"ship_{idx:02d}",
                created_at="",
                updated_at="",
                ship_status="skipped",
                request_id=f"R{idx}",
            ))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{idx}: {exc!r}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], errors
    rows = read_attempts(tmp_path)
    assert len({r.ship_attempt_id for r in rows}) == 5


# ── attempt_from_decision (Phase 1 convenience) ─────────────────────────


def test_attempt_from_decision_passed_becomes_skipped():
    fixed = datetime(2026, 5, 3, 5, 30, 0, tzinfo=timezone.utc)
    decision = {
        "ship_status": "skipped",
        "would_open_pr": True,
        "validation_result": "passed",
        "violations": [],
        "rollback_handle": "revert:outcome:abc123",
        "dry_run": True,
    }
    row = attempt_from_decision(
        decision=decision,
        request_id="REQ-1",
        parent_run_id="run_1",
        ship_class="docs_canary",
        changed_paths=["docs/autoship-canaries/x.md"],
        release_gate_result="APPROVE_AUTO_SHIP",
        stable_evidence_handle="outcome:abc123",
        now=fixed,
    )
    assert row.ship_status == "skipped"
    assert row.would_open_pr is True
    assert row.error_class == ""
    assert row.error_message == ""
    assert row.rollback_handle == "revert:outcome:abc123"
    assert json.loads(row.changed_paths_json) == ["docs/autoship-canaries/x.md"]


def test_attempt_from_decision_blocked_encodes_violations():
    decision = {
        "ship_status": "skipped",
        "would_open_pr": False,
        "validation_result": "blocked",
        "violations": [
            {"rule_id": "risk_level_not_low", "field": "risk_level"},
            {"rule_id": "ship_class_not_allowed", "field": "ship_class"},
        ],
        "rollback_handle": None,
        "dry_run": True,
    }
    row = attempt_from_decision(decision=decision)
    assert row.ship_status == "blocked"
    assert row.would_open_pr is False
    # error_class is sorted unique rule_ids comma-joined
    assert row.error_class == "risk_level_not_low,ship_class_not_allowed"
    # Full violations preserved as JSON for downstream debugging
    parsed = json.loads(row.error_message)
    assert len(parsed) == 2
    assert {v["rule_id"] for v in parsed} == {
        "risk_level_not_low", "ship_class_not_allowed",
    }


def test_attempt_from_decision_blocked_with_no_violations_uses_fallback():
    """Defensive: if validate_ship_request ever returns blocked but no
    violations (shouldn't happen, but if the schema drifts), error_class
    must still be non-empty so downstream filters work."""
    decision = {
        "ship_status": "skipped",
        "would_open_pr": False,
        "validation_result": "blocked",
        "violations": [],
        "rollback_handle": None,
        "dry_run": True,
    }
    row = attempt_from_decision(decision=decision)
    assert row.ship_status == "blocked"
    assert row.error_class == "blocked"


def test_attempt_from_decision_round_trip_through_record_and_read(tmp_path: Path):
    """End-to-end: convert a real decision, persist, read back — schema
    survives JSONL round-trip including the JSON-encoded fields."""
    decision = {
        "ship_status": "skipped",
        "would_open_pr": True,
        "validation_result": "passed",
        "violations": [],
        "rollback_handle": "revert:abc",
        "dry_run": True,
    }
    row = attempt_from_decision(
        decision=decision,
        request_id="REQ-9",
        ship_class="docs_canary",
        changed_paths=["docs/autoship-canaries/foo.md", "docs/autoship-canaries/bar.md"],
    )
    record_attempt(tmp_path, row)
    rd = find_attempt(tmp_path, row.ship_attempt_id)
    assert rd is not None
    assert rd.request_id == "REQ-9"
    assert json.loads(rd.changed_paths_json) == [
        "docs/autoship-canaries/foo.md", "docs/autoship-canaries/bar.md",
    ]


# ── public surface invariants ───────────────────────────────────────────


def test_valid_ship_statuses_includes_all_phase_states():
    for s in ("skipped", "blocked", "opened", "merged", "failed", "rolled_back"):
        assert s in VALID_SHIP_STATUSES, s


def test_mutable_fields_excludes_identity_fields():
    """ship_attempt_id, created_at, request_id, parent_run_id, etc. are
    immutable. updated_at IS mutable (and auto-managed)."""
    for f in ("ship_attempt_id", "created_at", "request_id", "parent_run_id"):
        assert f not in MUTABLE_FIELDS, f
    assert "updated_at" in MUTABLE_FIELDS


def test_ledger_filename_matches_spec_lowercase_snake():
    assert LEDGER_FILENAME == "auto_ship_attempts.jsonl"


# ── Slice C — rollback PR identity fields (PR #227, Codex review 2026-05-03) ──


def test_ship_attempt_has_rollback_pr_identity_fields():
    """Schema check: ShipAttempt has rollback_pr_number + rollback_pr_url
    fields with empty-string defaults (forward-compat with rows written
    before Slice C lands)."""
    row = ShipAttempt(
        ship_attempt_id="ship_20260504_aa11bb22",
        created_at="2026-05-04T01:00:00+00:00",
        updated_at="2026-05-04T01:00:00+00:00",
        ship_status="skipped",
    )
    assert row.rollback_pr_number == ""
    assert row.rollback_pr_url == ""
    # Also via to_dict (so chatbots/operators see the keys even when empty)
    d = row.to_dict()
    assert "rollback_pr_number" in d
    assert "rollback_pr_url" in d


def test_update_attempt_can_mutate_rollback_pr_identity_fields(tmp_path):
    """update_attempt accepts rollback_pr_number + rollback_pr_url because
    they're in MUTABLE_FIELDS. This is the contract record_rollback_decision
    relies on per auto-ship-rollback-v0.md (archived in git)."""
    row = ShipAttempt(
        ship_attempt_id="ship_20260504_cc33dd44",
        created_at="2026-05-04T01:01:00+00:00",
        updated_at="2026-05-04T01:01:00+00:00",
        ship_status="merged",
        ship_class="docs_canary",
    )
    record_attempt(tmp_path, row)
    update_attempt(
        tmp_path,
        row.ship_attempt_id,
        ship_status="rolled_back",
        rollback_pr_number="500",
        rollback_pr_url="https://github.com/TinyAssets/TinyAssets/pull/500",
    )
    rd = find_attempt(tmp_path, row.ship_attempt_id)
    assert rd is not None
    assert rd.ship_status == "rolled_back"
    assert rd.rollback_pr_number == "500"
    assert rd.rollback_pr_url == "https://github.com/TinyAssets/TinyAssets/pull/500"


def test_old_row_without_rollback_pr_fields_loads_cleanly():
    """Forward-compat: a JSONL row written before this PR (no rollback_pr_*)
    must still hydrate via from_dict, with empty defaults."""
    legacy_row = {
        "ship_attempt_id": "ship_20260501_legacy01",
        "created_at": "2026-05-01T00:00:00+00:00",
        "updated_at": "2026-05-01T00:00:00+00:00",
        "ship_status": "merged",
        "request_id": "REQ-LEGACY",
    }
    row = ShipAttempt.from_dict(legacy_row)
    assert row.ship_attempt_id == "ship_20260501_legacy01"
    assert row.rollback_pr_number == ""
    assert row.rollback_pr_url == ""


def test_mutable_fields_includes_rollback_pr_identity():
    """Explicit allowlist check so the schema and the mutability rules
    stay in sync."""
    assert "rollback_pr_number" in MUTABLE_FIELDS
    assert "rollback_pr_url" in MUTABLE_FIELDS
