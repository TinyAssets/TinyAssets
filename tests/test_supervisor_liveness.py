"""Tests for ``tinyassets.api.status._compute_supervisor_liveness``.

Pairs with PR #212 (BUG-011 Phase A lease metadata fields). This test
file uses ``getattr`` defaults to verify the supervisor_liveness helper
works both pre- and post-PR-#212.

Spec source: PR #206 (daemon-liveness-watchdog.md (archived in git)).
"""

from __future__ import annotations

import json
from datetime import datetime

from tinyassets.api.status import (
    _compute_supervisor_liveness,
    _parse_iso_to_epoch,
)

# ── _parse_iso_to_epoch unit ───────────────────────────────────────────────


def test_parse_iso_to_epoch_handles_empty_string():
    assert _parse_iso_to_epoch("") is None


def test_parse_iso_to_epoch_handles_none_safely():
    # Defensive: getattr default is "" but a None could leak through.
    assert _parse_iso_to_epoch(None or "") is None


def test_parse_iso_to_epoch_handles_z_suffix():
    iso = "2026-05-02T22:30:00Z"
    epoch = _parse_iso_to_epoch(iso)
    assert epoch is not None
    assert epoch > 0


def test_parse_iso_to_epoch_handles_offset_suffix():
    iso = "2026-05-02T22:30:00+00:00"
    epoch = _parse_iso_to_epoch(iso)
    assert epoch is not None


def test_parse_iso_to_epoch_returns_none_on_garbage():
    assert _parse_iso_to_epoch("not-a-timestamp") is None


# ── _compute_supervisor_liveness — empty queue ─────────────────────────────






# ── queue counts ───────────────────────────────────────────────────────────










def test_missing_capacity_evidence_marks_operational_counts_incomplete(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "tinyassets.api.universe._epoch2_operational_snapshot",
        lambda _udir: {
            "available": True,
            "queue_epoch": 2,
            "depth": 1,
            "lifecycle_counts": {
                "pending": 1,
                "running": 0,
                "cancel_requested": 0,
                "cancelled": 0,
                "succeeded": 0,
                "failed": 0,
            },
            "lifecycle_oldest_age_s": {"pending": 10},
            "operational_state_counts": {
                "awaiting_compatible_capacity": 1,
                "invalid_operator_admission": 0,
                "quarantined": 0,
                "policy_parked": 0,
            },
            "operational_oldest_age_s": {
                "awaiting_compatible_capacity": 10,
            },
            "operational_reason_counts": {},
            "valid_pending_count": 1,
            "eligible_pending_count": 0,
            "operational_counts_authoritative": False,
            "unclassified_active_count": 0,
            "active_scan_limit": 1000,
            "diagnostics": [],
            "diagnostics_truncated": False,
            "compatible_worker_count": 0,
            "capacity_evidence_available": False,
            "capacity_evidence_error": "runtime registry unavailable",
        },
    )

    out = _compute_supervisor_liveness(tmp_path)

    assert out["counts_complete"] is False
    assert any(
        "epoch2_capacity_evidence_unavailable" in warning
        for warning in out["warnings"]
    )
    assert not any(
        "epoch2_operational_scan_overflow" in warning
        for warning in out["warnings"]
    )


def test_disabled_epoch2_consumer_is_visible_in_status(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "tinyassets.api.universe._epoch2_operational_snapshot",
        lambda _udir: {
            "available": True,
            "queue_epoch": 2,
            "depth": 1,
            "lifecycle_counts": {
                "pending": 1,
                "running": 0,
                "cancel_requested": 0,
                "cancelled": 0,
                "succeeded": 0,
                "failed": 0,
            },
            "lifecycle_oldest_age_s": {"pending": 10},
            "operational_state_counts": {
                "awaiting_compatible_capacity": 1,
                "invalid_operator_admission": 0,
                "quarantined": 0,
                "policy_parked": 0,
            },
            "operational_oldest_age_s": {
                "awaiting_compatible_capacity": 10,
            },
            "operational_reason_counts": {},
            "valid_pending_count": 1,
            "eligible_pending_count": 0,
            "operational_counts_authoritative": False,
            "unclassified_active_count": 0,
            "active_scan_limit": 1000,
            "diagnostics": [],
            "diagnostics_truncated": False,
            "compatible_worker_count": 0,
            "capacity_evidence_available": False,
            "capacity_evidence_error": "epoch2_consumer_not_ready",
            "consumer_ready": False,
        },
    )

    out = _compute_supervisor_liveness(tmp_path)

    assert out["counts_complete"] is False
    assert any(
        "epoch2_consumer_not_ready" in warning
        for warning in out["warnings"]
    )


def test_unknown_epoch2_lifecycle_status_is_visible_and_incomplete(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "tinyassets.api.universe._epoch2_operational_snapshot",
        lambda _udir: {
            "available": True,
            "queue_epoch": 2,
            "depth": 1,
            "lifecycle_counts": {
                "pending": 0,
                "running": 0,
                "cancel_requested": 0,
                "cancelled": 0,
                "succeeded": 0,
                "failed": 0,
                "unknown": 1,
            },
            "lifecycle_oldest_age_s": {"unknown": 10},
            "unknown_lifecycle_status_counts": {"unknown": 1},
            "operational_state_counts": {
                "awaiting_compatible_capacity": 0,
                "invalid_operator_admission": 1,
                "quarantined": 0,
                "policy_parked": 0,
            },
            "operational_oldest_age_s": {
                "invalid_operator_admission": 10,
            },
            "operational_reason_counts": {
                "invalid_operator_admission": {"incomplete": 1},
            },
            "valid_pending_count": 0,
            "eligible_pending_count": 0,
            "operational_counts_authoritative": False,
            "integrity_scope_complete": True,
            "unclassified_active_count": 0,
            "active_scan_limit": 1000,
            "diagnostics": [],
            "diagnostics_truncated": False,
            "compatible_worker_count": 0,
            "capacity_evidence_available": True,
        },
    )

    out = _compute_supervisor_liveness(tmp_path)

    assert out["queue_state"]["depth"] == 1
    assert out["queue_state"]["unknown"] == 1
    assert out["counts_complete"] is False
    assert any(
        "epoch2_unknown_lifecycle_status" in warning
        for warning in out["warnings"]
    )


# ── pending-age detection (BUG-009 incident pattern) ───────────────────────










# ── loop-stall signal (2026-06-25 wedge detector) ──────────────────────────








# ── lease metadata path (post-#212) ────────────────────────────────────────
















# ── pre-#212 fallback (no lease fields populated) ──────────────────────────




# ── integration with get_status ────────────────────────────────────────────


def test_get_status_response_includes_supervisor_liveness(tmp_path, monkeypatch):
    import json

    from tinyassets.api.status import get_status

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "test-universe")
    universe = tmp_path / "test-universe"
    universe.mkdir(parents=True, exist_ok=True)
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(tmp_path, "test-universe")

    response = json.loads(get_status("test-universe"))
    assert "supervisor_liveness" in response
    assert "queue_state" in response["supervisor_liveness"]
    assert "running_tasks_lease" in response["supervisor_liveness"]
    assert "stale_running_tasks" in response["supervisor_liveness"]




def test_operational_capacity_requires_live_complete_descriptor(tmp_path):
    from tinyassets.api.universe import _compatible_epoch2_worker_ids

    universe = tmp_path / "universe-a"
    universe.mkdir()
    now = datetime.fromisoformat("2026-07-24T08:00:00+00:00")
    beat = {
        "ts": "2026-07-24T08:00:00Z",
        "phase": "polling",
        "subprocess_alive": True,
        "worker_id": "worker-a",
        "runtime_instance_id": "runtime-a",
        "queue_protocol_version": 2,
        "capabilities": ["operator_request_v1"],
        "boot_id": "boot-a",
        "build_sha": "a" * 40,
        "config_hash": "sha256:" + "b" * 64,
        "universe_id": "universe-a",
        "expires_at": "2026-07-24T08:01:30Z",
    }
    heartbeat = universe / ".worker_supervisor.worker-a.json"
    heartbeat.write_text(json.dumps(beat), encoding="utf-8")
    descriptor_fields = (
        "queue_protocol_version",
        "capabilities",
        "worker_id",
        "runtime_instance_id",
        "boot_id",
        "build_sha",
        "config_hash",
        "universe_id",
        "expires_at",
    )
    trusted = {
        "runtime-a": {field: beat[field] for field in descriptor_fields},
    }

    assert _compatible_epoch2_worker_ids(universe, now=now) == []
    assert _compatible_epoch2_worker_ids(
        universe,
        now=now,
        trusted_descriptors=trusted,
    ) == ["worker-a"]

    beat["subprocess_alive"] = False
    heartbeat.write_text(json.dumps(beat), encoding="utf-8")
    assert _compatible_epoch2_worker_ids(
        universe,
        now=now,
        trusted_descriptors=trusted,
    ) == []
    beat["subprocess_alive"] = True

    beat["expires_at"] = "2026-07-24T08:00:00Z"
    heartbeat.write_text(json.dumps(beat), encoding="utf-8")
    trusted["runtime-a"] = {
        field: beat[field] for field in descriptor_fields
    }
    assert _compatible_epoch2_worker_ids(
        universe,
        now=now,
        trusted_descriptors=trusted,
    ) == []

    beat["expires_at"] = "2026-07-24T08:01:30Z"
    beat.pop("config_hash")
    heartbeat.write_text(json.dumps(beat), encoding="utf-8")
    trusted["runtime-a"] = {
        field: beat[field]
        for field in descriptor_fields
        if field in beat
    }
    assert _compatible_epoch2_worker_ids(
        universe,
        now=now,
        trusted_descriptors=trusted,
    ) == []


def _capacity_beat() -> dict:
    """The known-good beat shape used by the admission-gate tests."""
    return {
        "ts": "2026-07-24T08:00:00Z",
        "phase": "polling",
        "subprocess_alive": True,
        "worker_id": "worker-a",
        "runtime_instance_id": "runtime-a",
        "queue_protocol_version": 2,
        "capabilities": ["operator_request_v1"],
        "boot_id": "boot-a",
        "build_sha": "a" * 40,
        "config_hash": "sha256:" + "b" * 64,
        "universe_id": "universe-a",
        "expires_at": "2026-07-24T08:01:30Z",
    }


_CAPACITY_DESCRIPTOR_FIELDS = (
    "queue_protocol_version",
    "capabilities",
    "worker_id",
    "runtime_instance_id",
    "boot_id",
    "build_sha",
    "config_hash",
    "universe_id",
    "expires_at",
)


def test_capacity_rejections_name_the_gate_that_turned_each_worker_away(
    tmp_path,
):
    """A bare ``compatible_worker_count: 0`` is unactionable.

    Production sat blocked for 22.5h with ten live workers and zero
    compatible ones because no read-only surface named which admission
    gate rejected them. Every gate must now be reported by name.
    """
    from tinyassets.api.universe import (
        _classify_epoch2_workers,
        _compatible_epoch2_workers,
    )

    universe = tmp_path / "universe-a"
    universe.mkdir()
    now = datetime.fromisoformat("2026-07-24T08:00:00+00:00")
    heartbeat = universe / ".worker_supervisor.worker-a.json"

    def classify(beat, trusted):
        heartbeat.write_text(json.dumps(beat), encoding="utf-8")
        return _classify_epoch2_workers(
            universe,
            now=now,
            trusted_descriptors=trusted,
        )

    def trusted_from(beat):
        return {
            "runtime-a": {
                field: beat[field]
                for field in _CAPACITY_DESCRIPTOR_FIELDS
                if field in beat
            }
        }

    # ACCEPT DIRECTION — this must stay green, or every assertion below
    # passes vacuously against a gate that rejects everything.
    beat = _capacity_beat()
    workers, evidence = classify(beat, trusted_from(beat))
    assert [worker["worker_id"] for worker in workers] == ["worker-a"]
    assert evidence["rejected"] == {}
    assert evidence["observed_worker_beats"] == 1
    assert "descriptor_mismatch_fields" not in evidence

    # The refactor must not change the accept decision itself.
    assert _compatible_epoch2_workers(
        universe,
        now=now,
        trusted_descriptors=trusted_from(beat),
    ) == workers

    # Each gate, named.
    beat = _capacity_beat()
    beat["subprocess_alive"] = False
    workers, evidence = classify(beat, trusted_from(beat))
    assert workers == []
    assert evidence["rejected"] == {"subprocess_not_alive": 1}

    beat = _capacity_beat()
    beat["capabilities"] = "operator_request_v1"
    workers, evidence = classify(beat, trusted_from(beat))
    assert workers == []
    assert evidence["rejected"] == {"capabilities_not_a_collection": 1}

    beat = _capacity_beat()
    beat["universe_id"] = "universe-b"
    workers, evidence = classify(beat, trusted_from(beat))
    assert workers == []
    assert evidence["rejected"] == {"universe_id_mismatch": 1}

    beat = _capacity_beat()
    workers, evidence = classify(beat, {"runtime-a": None})
    assert workers == []
    assert evidence["rejected"] == {"descriptor_never_published": 1}

    # A lease that has already expired is distinct from an untrusted one.
    beat = _capacity_beat()
    beat["expires_at"] = "2026-07-24T08:00:00Z"
    workers, evidence = classify(beat, trusted_from(beat))
    assert workers == []
    assert evidence["rejected"] == {"descriptor_lease_not_live": 1}


def test_capacity_rejection_reports_the_mismatched_field_not_its_value(
    tmp_path,
):
    """The registry copy drifting from the beat is the hardest gate to see.

    Report which field diverged so the cause is one read away, but never
    the values — they carry build and config identity.
    """
    from tinyassets.api.universe import _classify_epoch2_workers

    universe = tmp_path / "universe-a"
    universe.mkdir()
    now = datetime.fromisoformat("2026-07-24T08:00:00+00:00")
    beat = _capacity_beat()
    (universe / ".worker_supervisor.worker-a.json").write_text(
        json.dumps(beat), encoding="utf-8"
    )

    stale = {field: beat[field] for field in _CAPACITY_DESCRIPTOR_FIELDS}
    stale["expires_at"] = "2026-07-24T07:59:00Z"
    stale["build_sha"] = "c" * 40

    workers, evidence = _classify_epoch2_workers(
        universe,
        now=now,
        trusted_descriptors={"runtime-a": stale},
    )
    assert workers == []
    assert evidence["rejected"] == {"descriptor_not_trusted": 1}
    assert evidence["descriptor_mismatch_fields"] == [
        "build_sha",
        "expires_at",
    ]

    serialized = json.dumps(evidence)
    assert "c" * 40 not in serialized
    assert "2026-07-24T07:59:00Z" not in serialized


def test_capacity_rejection_flags_a_beat_with_no_provisioned_runtime_row(
    tmp_path,
):
    """A live beat with no registry row is invisible without this gate."""
    from tinyassets.api.universe import _classify_epoch2_workers

    universe = tmp_path / "universe-a"
    universe.mkdir()
    now = datetime.fromisoformat("2026-07-24T08:00:00+00:00")
    (universe / ".worker_supervisor.worker-a.json").write_text(
        json.dumps(_capacity_beat()), encoding="utf-8"
    )

    workers, evidence = _classify_epoch2_workers(universe, now=now)
    assert workers == []
    assert evidence["rejected"] == {"no_provisioned_runtime_row": 1}
    assert evidence["provisioned_runtime_count"] == 0
    assert evidence["observed_worker_beats"] == 1
