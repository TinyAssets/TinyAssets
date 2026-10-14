"""Tier-aware DaemonController and BranchTask queue tests.

Covers phase_e_preflight.md (archived in git) §4.4:

- Queue plumbing (8 tests)
- Dispatcher scoring (6 tests)
- Submission integration (7 tests)
- DaemonController integration (6 tests, flag-matrix parameterized)
- Restart recovery + GC (4 tests)
- Invariants 1/2/3 (3 tests)
- MCP surface (4 tests)

Total: ~38 tests.

All tests are disk-backed via ``tmp_path`` — file-lock primitives are
first introduction in this codebase and require integration, not mock,
coverage.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import rfc8785

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets import branch_tasks as bt_mod
from tinyassets.auth.provider import Identity
from tinyassets.branch_tasks import (
    ARCHIVE_FILENAME,
    QUEUE_FILENAME,
    BranchTask,
    append_task,
    archive_path,
    claim_task,
    garbage_collect,
    mark_status,
    new_task_id,
    queue_path,
    read_queue,
    recover_claimed_tasks,
)
from tinyassets.branch_tasks_v2 import (
    Epoch2BranchTaskAdapter,
    WorkerClaimDescriptor,
)
from tinyassets.daemon_server import initialize_author_server
from tinyassets.dispatcher import (
    DispatcherConfig,
    dispatcher_enabled,
    load_dispatcher_config,
    score_task,
    select_next_task,
    soul_guided_dispatch_read,
)
from tinyassets.storage.request_admissions import RequestAdmissionStore

# ───────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────


pytestmark = pytest.mark.usefixtures("cloud_runtime")


def _become(user_id: str) -> None:
    """Sign in as ``user_id``.

    These tests used to set ``UNIVERSE_SERVER_USER``, which named the actor by
    environment variable -- authority from a string anybody can set. The
    autouse operator fixture rebinds between tests, so this does not leak.
    """
    from tinyassets.auth import middleware as _mw
    from tinyassets.auth.provider import Identity

    _mw._current_identity.set(
        Identity(
            user_id=user_id,
            username=user_id,
            display_name=user_id,
            capabilities=[
                "tinyassets.universe.read",
                "tinyassets.universe.write",
                "tinyassets.universe.admin",
                "tinyassets.extensions.read",
                "tinyassets.extensions.write",
            ],
        )
    )


@pytest.fixture
def universe_dir(tmp_path: Path) -> Path:
    """A fresh universe directory per test."""
    udir = tmp_path / "test-universe"
    udir.mkdir(parents=True, exist_ok=True)
    return udir


def _make_task(
    *,
    trigger_source: str = "user_request",
    priority_weight: float = 0.0,
    status: str = "pending",
    queued_at: str | None = None,
    branch_task_id: str | None = None,
    universe_id: str = "test-universe",
    **extra,
) -> BranchTask:
    return BranchTask(
        branch_task_id=branch_task_id or new_task_id(),
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        universe_id=universe_id,
        inputs=extra.get("inputs", {}),
        trigger_source=trigger_source,
        priority_weight=priority_weight,
        queued_at=queued_at or datetime.now(timezone.utc).isoformat(),
        status=status,
        claimed_by=extra.get("claimed_by", ""),
    )


def _commit_epoch2(
    base_path: Path,
    *,
    key_suffix: str,
    text: str,
    created_at: str,
    priority_weight: float = 5,
    universe_id: str = "universe-a",
    directed_daemon_instruction: str = "",
    directed_daemon_id: str = "",
    directed_soul_hash: str = "",
) -> dict:
    canonical_body = rfc8785.dumps({
        "branch_id": "",
        "directed_daemon_id": directed_daemon_id,
        "directed_daemon_instruction": directed_daemon_instruction,
        "pickup_incentive": "",
        "priority_weight": priority_weight,
        "request_type": "general",
        "schema_version": "request-admission-v2",
        "text": text,
        "universe_id": universe_id,
    })
    return RequestAdmissionStore(base_path).commit_admission(
        tenant_id="tenant-a",
        actor_id="actor-a",
        universe_id=universe_id,
        idempotency_key_hash=(
            "hmac-sha256:"
            + hashlib.sha256(key_suffix.encode()).hexdigest()
        ),
        body_digest=(
            "sha256:" + hashlib.sha256(canonical_body).hexdigest()
        ),
        body_digest_version="rfc8785-v1",
        request_type="general",
        text=text,
        branch_id="",
        branch_def_id="loop-branch",
        trigger_source="operator_request",
        accepted_priority_weight=priority_weight,
        policy_version="operator-priority-v1",
        grant_generation=1,
        receipt={
            "authority": "request-local",
            "grant_generation": 1,
            "priority_policy_version": "operator-priority-v1",
            "directed_assignment": (
                {
                    "daemon_id": directed_daemon_id,
                    "daemon_soul_hash": directed_soul_hash,
                    "authority_scope": "owner",
                }
                if directed_daemon_id
                else {}
            ),
        },
        directed_daemon_id=directed_daemon_id,
        directed_daemon_instruction=directed_daemon_instruction,
        created_at=created_at,
    )


# ───────────────────────────────────────────────────────────────────────
# Queue plumbing (8 tests)
# ───────────────────────────────────────────────────────────────────────


def test_branch_task_roundtrip_dict():
    t = _make_task()
    d = t.to_dict()
    t2 = BranchTask.from_dict(d)
    assert t2 == t


def test_read_queue_missing_file_returns_empty(universe_dir):
    assert read_queue(universe_dir) == []


def test_read_queue_corrupt_json_raises(universe_dir):
    qp = queue_path(universe_dir)
    qp.write_text("not json at all", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Corrupt"):
        read_queue(universe_dir)


def test_append_task_preserves_ordering(universe_dir):
    t1 = _make_task()
    time.sleep(0.002)
    t2 = _make_task()
    append_task(universe_dir, t1)
    append_task(universe_dir, t2)
    q = read_queue(universe_dir)
    assert [t.branch_task_id for t in q] == [t1.branch_task_id, t2.branch_task_id]


def test_claim_task_idempotent(universe_dir):
    t = _make_task()
    append_task(universe_dir, t)
    first = claim_task(universe_dir, t.branch_task_id, "daemon-1")
    assert first is not None
    assert first.status == "running"
    assert first.claimed_by == "daemon-1"
    second = claim_task(universe_dir, t.branch_task_id, "daemon-2")
    assert second is None


def test_mark_status_valid_transitions(universe_dir):
    t = _make_task()
    append_task(universe_dir, t)
    claim_task(universe_dir, t.branch_task_id, "d1")
    mark_status(universe_dir, t.branch_task_id, status="succeeded")
    q = read_queue(universe_dir)
    assert q[0].status == "succeeded"


def test_mark_status_invalid_transition_raises(universe_dir):
    t = _make_task()
    append_task(universe_dir, t)
    # pending -> succeeded is not in valid transitions
    with pytest.raises(ValueError, match="Invalid transition"):
        mark_status(universe_dir, t.branch_task_id, status="succeeded")


def test_file_lock_race(universe_dir):
    """5 threads × 20 mixed ops, no lost entries, no duplicate claims."""
    ids = [f"bt_race_{i}" for i in range(100)]
    for tid in ids:
        append_task(universe_dir, _make_task(branch_task_id=tid))

    claimed: list[str] = []
    claimed_lock = threading.Lock()
    errors: list[Exception] = []

    def worker(start: int):
        try:
            for i in range(20):
                tid = ids[(start * 20 + i) % 100]
                got = claim_task(universe_dir, tid, f"w-{start}")
                if got is not None:
                    with claimed_lock:
                        claimed.append(got.branch_task_id)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(claimed) == len(set(claimed))  # no duplicate claims
    q = read_queue(universe_dir)
    assert len(q) == 100  # no lost entries
    running = sum(1 for t in q if t.status == "running")
    assert running == len(claimed)


# ───────────────────────────────────────────────────────────────────────
# Dispatcher scoring (6 tests)
# ───────────────────────────────────────────────────────────────────────


def test_tier_weight_dominates_user_boost():
    cfg = DispatcherConfig()
    now = datetime.now(timezone.utc).isoformat()
    host = _make_task(trigger_source="host_request", priority_weight=0.0,
                      queued_at=now)
    user = _make_task(trigger_source="user_request", priority_weight=20.0,
                      queued_at=now)
    assert score_task(host, now_iso=now, config=cfg) > score_task(
        user, now_iso=now, config=cfg,
    )


def test_recency_decay_within_tier():
    cfg = DispatcherConfig()
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    fresh = _make_task(trigger_source="user_request", queued_at=now)
    stale_dt = now_dt - timedelta(days=5)
    stale = _make_task(trigger_source="user_request",
                       queued_at=stale_dt.isoformat())
    assert score_task(fresh, now_iso=now, config=cfg) > score_task(
        stale, now_iso=now, config=cfg,
    )


def test_user_boost_within_tier():
    cfg = DispatcherConfig()
    now = datetime.now(timezone.utc).isoformat()
    lo = _make_task(trigger_source="user_request", priority_weight=0.0,
                    queued_at=now)
    hi = _make_task(trigger_source="user_request", priority_weight=5.0,
                    queued_at=now)
    assert score_task(hi, now_iso=now, config=cfg) > score_task(
        lo, now_iso=now, config=cfg,
    )


def test_deferred_coefficients_zero_until_paid_market_enabled():
    cfg = DispatcherConfig()
    now = datetime.now(timezone.utc).isoformat()
    t_lo = _make_task(trigger_source="user_request", queued_at=now)
    t_hi = BranchTask(
        branch_task_id=new_task_id(),
        branch_def_id="x", universe_id="u",
        trigger_source="user_request", queued_at=now, bid=1000.0,
    )
    # Same tier + same queued_at + no user boost => same score.
    assert score_task(t_lo, now_iso=now, config=cfg) == score_task(
        t_hi, now_iso=now, config=cfg,
    )


def test_dispatcher_determinism(universe_dir):
    cfg = DispatcherConfig()
    append_task(universe_dir, _make_task(trigger_source="host_request"))
    append_task(universe_dir, _make_task(trigger_source="user_request"))
    append_task(universe_dir, _make_task(trigger_source="owner_queued"))
    fixed = datetime.now(timezone.utc).isoformat()
    a = select_next_task(universe_dir, config=cfg, now_iso=fixed)
    b = select_next_task(universe_dir, config=cfg, now_iso=fixed)
    assert a is not None and b is not None
    assert a.branch_task_id == b.branch_task_id


def test_select_next_empty_queue_returns_none(universe_dir):
    cfg = DispatcherConfig()
    assert select_next_task(universe_dir, config=cfg) is None


def test_legacy_queue_cannot_select_forged_operator_tier(universe_dir):
    with pytest.raises(ValueError, match="Invalid trigger_source"):
        append_task(
            universe_dir,
            _make_task(trigger_source="operator_request"),
        )

    assert select_next_task(
        universe_dir,
        config=DispatcherConfig(),
    ) is None


def test_v2_selector_merges_epochs_without_mutating_either(tmp_path):
    universe_dir = tmp_path / "universe-a"
    universe_dir.mkdir()
    initialize_author_server(tmp_path)
    v1 = _make_task(
        trigger_source="host_request",
        priority_weight=0,
        queued_at="2026-07-24T08:00:00+00:00",
        branch_task_id="v1-host",
    )
    append_task(universe_dir, v1)
    committed = _commit_epoch2(
        tmp_path,
        key_suffix="selector-merge",
        text="operator work",
        created_at="2026-07-24T08:00:01+00:00",
    )
    adapter = Epoch2BranchTaskAdapter(tmp_path)

    legacy_selected = select_next_task(
        universe_dir,
        config=DispatcherConfig(),
        now_iso="2026-07-24T08:00:02+00:00",
    )
    v2_selected = select_next_task(
        universe_dir,
        config=DispatcherConfig(),
        now_iso="2026-07-24T08:00:02+00:00",
        epoch2_adapter=adapter,
    )
    epoch2_only = select_next_task(
        universe_dir,
        config=DispatcherConfig(),
        now_iso="2026-07-24T08:00:02+00:00",
        epoch2_adapter=adapter,
        queue_epochs=frozenset({2}),
    )

    assert legacy_selected is not None
    assert legacy_selected.branch_task_id == "v1-host"
    assert v2_selected is not None
    assert v2_selected.branch_task_id == committed["branch_task_id"]
    assert getattr(v2_selected, "queue_epoch") == 2
    assert epoch2_only is not None
    assert epoch2_only.branch_task_id == committed["branch_task_id"]
    assert read_queue(universe_dir)[0].status == "pending"
    assert adapter.get(committed["branch_task_id"]).status == "pending"


def test_mixed_invalid_epoch2_rows_never_execute_or_block_valid_epochs(
    tmp_path: Path,
) -> None:
    universe_dir = tmp_path / "universe-a"
    universe_dir.mkdir()
    initialize_author_server(tmp_path)
    append_task(
        universe_dir,
        _make_task(
            trigger_source="host_request",
            queued_at="2026-07-24T08:00:00+00:00",
            branch_task_id="valid-v1",
            universe_id="universe-a",
        ),
    )
    valid_v2 = _commit_epoch2(
        tmp_path,
        key_suffix="mixed-valid",
        text="valid operator work",
        created_at="2026-07-24T08:00:01+00:00",
    )
    forged = _commit_epoch2(
        tmp_path,
        key_suffix="mixed-forged",
        text="forged operator work",
        created_at="2026-07-24T08:00:02+00:00",
    )
    missing_receipt = _commit_epoch2(
        tmp_path,
        key_suffix="mixed-missing-receipt",
        text="missing receipt work",
        created_at="2026-07-24T08:00:03+00:00",
    )
    unsupported = _commit_epoch2(
        tmp_path,
        key_suffix="mixed-unsupported",
        text="unsupported protocol work",
        created_at="2026-07-24T08:00:04+00:00",
    )
    store = RequestAdmissionStore(tmp_path)
    with store.connection() as conn:
        conn.execute(
            """
            UPDATE branch_tasks_v2 SET branch_def_id = 'forged-branch'
            WHERE branch_task_id = ?
            """,
            (forged["branch_task_id"],),
        )
        conn.execute(
            """
            UPDATE request_admissions SET receipt_json = '{}'
            WHERE branch_task_id = ?
            """,
            (missing_receipt["branch_task_id"],),
        )
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            """
            UPDATE branch_tasks_v2 SET protocol_version = 99
            WHERE branch_task_id = ?
            """,
            (unsupported["branch_task_id"],),
        )
    transaction_at = datetime.fromisoformat(
        "2026-07-24T08:00:30+00:00"
    )
    adapter = Epoch2BranchTaskAdapter(
        tmp_path,
        clock=lambda: transaction_at,
    )
    descriptor = WorkerClaimDescriptor(
        queue_protocol_version=2,
        capabilities=frozenset({"operator_request_v1"}),
        worker_id="worker-v2",
        runtime_instance_id="runtime-v2",
        boot_id="boot-v2",
        build_sha="a" * 40,
        config_hash="b" * 64,
        universe_id="universe-a",
        expires_at="2026-07-24T08:01:30+00:00",
    )
    invalid = (forged, missing_receipt, unsupported)

    selected_v2 = select_next_task(
        universe_dir,
        config=DispatcherConfig(),
        now_iso="2026-07-24T08:00:30+00:00",
        epoch2_adapter=adapter,
    )

    assert selected_v2 is not None
    assert selected_v2.branch_task_id == valid_v2["branch_task_id"]
    with store.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM branch_tasks_v2_quarantine"
        ).fetchone()[0] == 0
    for row in invalid:
        assert adapter.claim(
            row["branch_task_id"],
            descriptor=descriptor,
            descriptor_reader=lambda _conn, _worker_id: descriptor,
        ) is None
    claimed_v2 = adapter.claim(
        valid_v2["branch_task_id"],
        descriptor=descriptor,
        descriptor_reader=lambda _conn, _worker_id: descriptor,
    )
    assert claimed_v2 is not None
    assert claimed_v2.status == "running"

    selected_v1 = select_next_task(
        universe_dir,
        config=DispatcherConfig(),
        now_iso="2026-07-24T08:00:31+00:00",
        epoch2_adapter=adapter,
    )
    assert selected_v1 is not None
    assert selected_v1.branch_task_id == "valid-v1"
    claimed_v1 = claim_task(universe_dir, "valid-v1", "worker-v2")
    assert claimed_v1 is not None
    assert claimed_v1.status == "running"

    maintenance = adapter.maintain_quarantine()

    assert maintenance.health == "green"
    assert maintenance.quarantined == 3
    reasons = {
        receipt.branch_task_id: receipt.reason
        for receipt in maintenance.receipts
    }
    assert reasons == {
        forged["branch_task_id"]: "invalid_operator_admission",
        missing_receipt["branch_task_id"]: "invalid_operator_admission",
        unsupported["branch_task_id"]: "unsupported_protocol",
    }
    with store.connection() as conn:
        rows = {
            row["branch_task_id"]: (row["status"], row["disabled"])
            for row in conn.execute(
                """
                SELECT branch_task_id, status, disabled
                FROM branch_tasks_v2
                """
            )
        }
    assert rows[valid_v2["branch_task_id"]] == ("running", 0)
    assert all(
        rows[row["branch_task_id"]] == ("pending", 1)
        for row in invalid
    )


def test_select_next_skips_tasks_directed_to_other_daemon(tmp_path):
    from tinyassets import daemon_registry

    universe = tmp_path / "test-universe"
    universe.mkdir()
    mine = daemon_registry.create_daemon(
        tmp_path,
        display_name="My Patch Daemon",
        created_by="host",
        soul_text="Prefer TinyAssets patch requests.",
    )
    other = daemon_registry.create_daemon(
        tmp_path,
        display_name="Other Patch Daemon",
        created_by="host",
        soul_text="Prefer unrelated patch requests.",
    )
    now = datetime.now(timezone.utc).isoformat()
    directed_elsewhere = BranchTask(
        branch_task_id="bt-directed-other",
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        universe_id="test-universe",
        trigger_source="owner_queued",
        queued_at=now,
        directed_daemon_id=other["daemon_id"],
    )
    open_task = BranchTask(
        branch_task_id="bt-open",
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        universe_id="test-universe",
        trigger_source="user_request",
        queued_at=now,
    )
    append_task(universe, directed_elsewhere)
    append_task(universe, open_task)

    selected = select_next_task(
        universe,
        config=DispatcherConfig(active_daemon_id=mine["daemon_id"]),
        now_iso=now,
    )

    assert selected is not None
    assert selected.branch_task_id == "bt-open"


def test_soul_guided_dispatch_read_boosts_matching_open_brain_task(canonical_daemon, tmp_path):
    from tinyassets import daemon_registry
    from tinyassets.daemon_brain import capture_daemon_memory

    universe = tmp_path / "test-universe"
    universe.mkdir()
    daemon = daemon_registry.create_daemon(
        tmp_path,
        display_name="TinyAssets Dispatch Daemon",
        created_by="host",
        soul_text="Prefer TinyAssets dispatch and verified patch work.",
        domain_claims=["workflow-platform"],
    )
    memory = capture_daemon_memory(
        canonical_daemon(tmp_path),
        daemon_id=daemon["daemon_id"],
        memory_kind="preference",
        content="Prefer workflow-platform dispatch patches with narrow tests.",
        source_type="manual",
        source_id="pytest",
        reliability="host_observed",
        language_type="policy",
        importance=0.9,
        visibility="published",
        promotion_state="accepted",
    )
    matching = BranchTask(
        branch_task_id="bt-workflow",
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        universe_id="test-universe",
        inputs={"text": "Fix workflow-platform dispatch scoring."},
        trigger_source="user_request",
        queued_at="2026-05-17T00:00:00+00:00",
    )
    unrelated = BranchTask(
        branch_task_id="bt-other",
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        universe_id="test-universe",
        inputs={"text": "Write a recipe tracker note."},
        trigger_source="user_request",
        queued_at="2026-05-17T00:00:00+00:00",
    )
    append_task(universe, unrelated)
    append_task(universe, matching)

    guidance = soul_guided_dispatch_read(
        universe,
        task=matching,
        active_daemon_id=daemon["daemon_id"],
        coefficient=1.0,
    )
    selected = select_next_task(
        universe,
        config=DispatcherConfig(
            active_daemon_id=daemon["daemon_id"],
            soul_affinity_coefficient=1.0,
        ),
        now_iso="2026-05-17T00:00:00+00:00",
    )

    assert guidance["read_only"] is True
    assert guidance["domain_claim_hits"] == ["workflow-platform"]
    assert guidance["brain_entry_ids"] == [memory["entry_id"]]
    assert guidance["score_adjustment"] > 0
    assert selected is not None
    assert selected.branch_task_id == "bt-workflow"


# ───────────────────────────────────────────────────────────────────────
# Submission integration (7 tests)
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def server_base(tmp_path: Path, monkeypatch):
    """Point TINYASSETS_DATA_DIR at a fresh tmp dir with one universe."""
    base = tmp_path / "output"
    base.mkdir()
    uid = "test-uni"
    udir = base / uid
    udir.mkdir()
    (udir / "PROGRAM.md").write_text(
        "Legacy fixture with an explicit compatibility Loop.",
        encoding="utf-8",
    )
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)
    identity = Identity(
        user_id="authenticated-requester",
        username="authenticated-requester",
        capabilities=["write"],
    )
    monkeypatch.setattr(
        "tinyassets.auth.middleware.current_identity",
        lambda: identity,
    )
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(
        base,
        universe_id=uid,
        actor_id=identity.user_id,
        permission="write",
        granted_by=identity.user_id,
    )
    return base, uid


def _call_submit(**kwargs):
    from tinyassets.api.universe import _action_submit_request

    return json.loads(_action_submit_request(**kwargs))


def test_submit_host_without_grant_queues_user_request(
    server_base, monkeypatch,
):
    _, uid = server_base
    _become("host")
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", "host")
    resp = _call_submit(universe_id=uid, text="do a scene")
    assert "branch_task_id" in resp and resp["branch_task_id"]
    q = read_queue(Path(os.environ["TINYASSETS_DATA_DIR"]) / uid)
    assert len(q) == 1
    assert q[0].trigger_source == "user_request"


def test_submit_request_as_non_host_queues_user_request(
    server_base, monkeypatch,
):
    _, uid = server_base
    _become("alice")
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", "host")
    resp = _call_submit(universe_id=uid, text="please write")
    assert resp["branch_task_id"]
    q = read_queue(Path(os.environ["TINYASSETS_DATA_DIR"]) / uid)
    assert q[0].trigger_source == "user_request"


def test_submit_creates_both_request_and_branch_task(server_base, monkeypatch):
    _, uid = server_base
    _become("alice")
    _call_submit(universe_id=uid, text="do a scene")
    udir = Path(os.environ["TINYASSETS_DATA_DIR"]) / uid
    # requests.json populated
    from tinyassets.work_targets import REQUESTS_FILENAME
    reqs = json.loads((udir / REQUESTS_FILENAME).read_text(encoding="utf-8"))
    assert len(reqs) == 1
    # branch_tasks.json populated
    assert len(read_queue(udir)) == 1


def test_submit_8kib_cap_still_enforced(server_base, monkeypatch):
    _, uid = server_base
    _become("alice")
    huge = "x" * (8 * 1024 + 1)
    resp = _call_submit(universe_id=uid, text=huge)
    assert "error" in resp


def test_submit_host_without_grant_priority_weight_is_rejected(
    server_base, monkeypatch,
):
    _, uid = server_base
    _become("host")
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", "host")
    response = _call_submit(
        universe_id=uid,
        text="boosted",
        priority_weight=50.0,
    )
    q = read_queue(Path(os.environ["TINYASSETS_DATA_DIR"]) / uid)
    assert response["error"] == "priority_authorization_required"
    assert q == []


def test_submit_non_host_priority_weight_is_rejected(server_base, monkeypatch):
    _, uid = server_base
    _become("alice")
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", "host")
    resp = _call_submit(universe_id=uid, text="sneaky", priority_weight=50.0)
    assert resp["error"] == "priority_authorization_required"
    q = read_queue(Path(os.environ["TINYASSETS_DATA_DIR"]) / uid)
    assert q == []


def test_submit_negative_priority_weight_rejected(server_base, monkeypatch):
    _, uid = server_base
    _become("host")
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", "host")
    resp = _call_submit(universe_id=uid, text="bad", priority_weight=-10.0)
    assert "error" in resp
    q = read_queue(Path(os.environ["TINYASSETS_DATA_DIR"]) / uid)
    assert q == []


# ───────────────────────────────────────────────────────────────────────
# DaemonController integration (6 tests)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("unified_execution,dispatcher_enabled", [
    ("off", "off"), ("off", "on"), ("on", "off"), ("on", "on"),
])
def test_dispatcher_flag_matrix_observational_safe(
    universe_dir, monkeypatch, unified_execution, dispatcher_enabled,
):
    """Flag matrix: under ANY cell, _dispatcher_startup + _dispatcher_observe
    must not raise and must not alter the queue file.

    Observational means: no side effects on branch_tasks.json beyond the
    startup-recovery + GC already permitted by invariants 7 + 10.
    """
    from fantasy_daemon.__main__ import _dispatcher_observe, _dispatcher_startup

    monkeypatch.setenv(
        "TINYASSETS_UNIFIED_EXECUTION", "1" if unified_execution == "on" else "0",
    )
    monkeypatch.setenv(
        "TINYASSETS_DISPATCHER_ENABLED", "on" if dispatcher_enabled == "on" else "off",
    )
    # Seed the queue with a pending task; call the startup + observe
    # hooks. Queue should be unchanged (pending stays pending).
    append_task(universe_dir, _make_task())
    before = read_queue(universe_dir)
    _dispatcher_startup(universe_dir)
    _dispatcher_observe(universe_dir)
    after = read_queue(universe_dir)
    assert [t.branch_task_id for t in before] == [t.branch_task_id for t in after]
    assert all(t.status == "pending" for t in after)


def test_dispatcher_flag_off_returns_no_observation_logs(
    universe_dir, monkeypatch, caplog,
):
    """Dispatcher flag off means dispatcher_observe silently skips."""
    from fantasy_daemon.__main__ import _dispatcher_observe

    monkeypatch.setenv("TINYASSETS_DISPATCHER_ENABLED", "off")
    append_task(universe_dir, _make_task(trigger_source="host_request"))
    import logging as _logging
    caplog.set_level(_logging.INFO)
    _dispatcher_observe(universe_dir)
    # Nothing observational should have been logged.
    assert not any(
        "dispatcher_observational" in record.message
        for record in caplog.records
    )


def test_dispatcher_enabled_flag_defaults_on(monkeypatch):
    monkeypatch.delenv("TINYASSETS_DISPATCHER_ENABLED", raising=False)
    assert dispatcher_enabled() is True


def test_dispatcher_enabled_flag_off_respected(monkeypatch):
    for val in ("off", "0", "false", "no"):
        monkeypatch.setenv("TINYASSETS_DISPATCHER_ENABLED", val)
        assert dispatcher_enabled() is False


def test_empty_queue_fallback_dispatcher_returns_none(universe_dir):
    """R7 fallback: empty queue => dispatcher returns None, daemon
    falls back to existing graph behavior."""
    cfg = DispatcherConfig()
    assert select_next_task(universe_dir, config=cfg) is None


def test_dispatcher_observe_does_not_raise_on_corrupt_queue(
    universe_dir, monkeypatch, caplog,
):
    """Observational path must NOT crash the daemon even if the
    queue file is corrupt — it logs and moves on."""
    from fantasy_daemon.__main__ import _dispatcher_observe

    monkeypatch.setenv("TINYASSETS_DISPATCHER_ENABLED", "on")
    queue_path(universe_dir).write_text("{not json", encoding="utf-8")
    # Should not raise — observe swallows.
    _dispatcher_observe(universe_dir)


# ───────────────────────────────────────────────────────────────────────
# Restart recovery + GC (4 tests)
# ───────────────────────────────────────────────────────────────────────


def test_recover_claimed_tasks_resets_running_to_pending(universe_dir):
    pending = _make_task(status="pending")
    running = _make_task(status="running")
    running.claimed_by = "dead-daemon"
    append_task(universe_dir, pending)
    # Directly write running task (append_task requires pending)
    qp = queue_path(universe_dir)
    raw = json.loads(qp.read_text(encoding="utf-8"))
    raw.append(running.to_dict())
    qp.write_text(json.dumps(raw), encoding="utf-8")

    count = recover_claimed_tasks(universe_dir)
    assert count == 1
    q = read_queue(universe_dir)
    assert all(t.status == "pending" for t in q)
    running_recovered = next(
        t for t in q if t.branch_task_id == running.branch_task_id
    )
    assert running_recovered.claimed_by == ""


def test_branch_tasks_json_survives_controller_lifecycle(universe_dir):
    """Persistent across process boundaries — append, reread is the
    lifecycle proxy here (real DaemonController tested via integration)."""
    ids = []
    for _ in range(3):
        t = _make_task()
        append_task(universe_dir, t)
        ids.append(t.branch_task_id)
    # Second "process": fresh read.
    q = read_queue(universe_dir)
    assert [t.branch_task_id for t in q] == ids


def test_gc_archives_old_terminal_tasks(universe_dir):
    old = datetime.now(timezone.utc) - timedelta(days=60)
    new = datetime.now(timezone.utc)
    # 3 old-succeeded, 2 old-pending, 1 new-succeeded
    old_done = [
        _make_task(status="pending", queued_at=old.isoformat())
        for _ in range(3)
    ]
    old_pending = [
        _make_task(status="pending", queued_at=old.isoformat())
        for _ in range(2)
    ]
    new_done = _make_task(status="pending", queued_at=new.isoformat())

    for t in old_done + old_pending + [new_done]:
        append_task(universe_dir, t)
    # Flip the "old_done" ones to succeeded directly.
    qp = queue_path(universe_dir)
    raw = json.loads(qp.read_text(encoding="utf-8"))
    done_ids = {t.branch_task_id for t in old_done}
    new_ids = {new_done.branch_task_id}
    for row in raw:
        if row["branch_task_id"] in done_ids:
            row["status"] = "succeeded"
        if row["branch_task_id"] in new_ids:
            row["status"] = "succeeded"
    qp.write_text(json.dumps(raw), encoding="utf-8")

    result = garbage_collect(universe_dir)
    assert result["archived"] == 3
    q = read_queue(universe_dir)
    remaining_ids = {t.branch_task_id for t in q}
    # Old pending kept + new succeeded kept
    assert remaining_ids == (
        {t.branch_task_id for t in old_pending} | new_ids
    )
    # Archive file populated
    ap = archive_path(universe_dir)
    assert ap.exists()
    arch = json.loads(ap.read_text(encoding="utf-8"))
    assert len(arch) == 3


def test_gc_preserves_pending_and_running_regardless_of_age(universe_dir):
    old = datetime.now(timezone.utc) - timedelta(days=90)
    pending = _make_task(status="pending", queued_at=old.isoformat())
    append_task(universe_dir, pending)
    # Inject running status directly
    qp = queue_path(universe_dir)
    raw = json.loads(qp.read_text(encoding="utf-8"))
    running = _make_task(
        status="running", queued_at=old.isoformat(),
    ).to_dict()
    raw.append(running)
    qp.write_text(json.dumps(raw), encoding="utf-8")

    garbage_collect(universe_dir)
    q = read_queue(universe_dir)
    assert len(q) == 2  # both survive
    assert {t.status for t in q} == {"pending", "running"}


def test_gc_corrupt_archive_fails_without_replacing_archive_or_live_queue(
    universe_dir,
):
    old = datetime.now(timezone.utc) - timedelta(days=90)
    terminal = _make_task(status="pending", queued_at=old.isoformat())
    append_task(universe_dir, terminal)
    qp = queue_path(universe_dir)
    live_before = json.loads(qp.read_text(encoding="utf-8"))
    live_before[0]["status"] = "succeeded"
    qp.write_text(json.dumps(live_before), encoding="utf-8")
    live_bytes = qp.read_bytes()
    ap = archive_path(universe_dir)
    ap.write_text("{not-json", encoding="utf-8")
    archive_bytes = ap.read_bytes()

    with pytest.raises(RuntimeError):
        garbage_collect(universe_dir)

    assert ap.read_bytes() == archive_bytes
    assert qp.read_bytes() == live_bytes


@pytest.mark.parametrize("blank_content", ["", " \n\t"])
def test_gc_blank_live_queue_fails_without_mutation(universe_dir, blank_content):
    qp = queue_path(universe_dir)
    qp.write_text(blank_content, encoding="utf-8")
    before = qp.read_bytes()

    with pytest.raises(RuntimeError, match="Corrupt queue"):
        garbage_collect(universe_dir)

    assert qp.read_bytes() == before
    assert not archive_path(universe_dir).exists()


@pytest.mark.parametrize("blank_content", ["", " \n\t"])
def test_gc_blank_archive_fails_without_mutating_live_or_archive(
    universe_dir, blank_content,
):
    old = datetime.now(timezone.utc) - timedelta(days=90)
    terminal = _make_task(status="pending", queued_at=old.isoformat())
    append_task(universe_dir, terminal)
    qp = queue_path(universe_dir)
    live = json.loads(qp.read_text(encoding="utf-8"))
    live[0]["status"] = "succeeded"
    qp.write_text(json.dumps(live), encoding="utf-8")
    live_before = qp.read_bytes()
    ap = archive_path(universe_dir)
    ap.write_text(blank_content, encoding="utf-8")
    archive_before = ap.read_bytes()

    with pytest.raises(RuntimeError, match="Corrupt queue"):
        garbage_collect(universe_dir)

    assert qp.read_bytes() == live_before
    assert ap.read_bytes() == archive_before


def test_gc_interrupted_archive_first_move_converges_without_duplicate(
    universe_dir,
):
    old = datetime.now(timezone.utc) - timedelta(days=90)
    terminal = _make_task(status="pending", queued_at=old.isoformat())
    append_task(universe_dir, terminal)
    qp = queue_path(universe_dir)
    live = json.loads(qp.read_text(encoding="utf-8"))
    live[0]["status"] = "succeeded"
    qp.write_text(json.dumps(live), encoding="utf-8")
    ap = archive_path(universe_dir)
    ap.write_text(json.dumps(live), encoding="utf-8")

    result = garbage_collect(universe_dir)

    assert result == {"archived": 1, "kept": 0}
    assert read_queue(universe_dir) == []
    archived = json.loads(ap.read_text(encoding="utf-8"))
    assert [
        row["branch_task_id"]
        for row in archived
        if row["branch_task_id"] == terminal.branch_task_id
    ] == [terminal.branch_task_id]


# ───────────────────────────────────────────────────────────────────────
# Invariants (3 tests)
# ───────────────────────────────────────────────────────────────────────


def test_invariant_2_cancel_branch_task_preserves_work_target(
    server_base, monkeypatch,
):
    """WorkTarget (in requests.json) persists even when its BranchTask
    is cancelled. Invariant §4.3 #2."""
    from tinyassets.api.universe import _action_queue_cancel
    from tinyassets.work_targets import REQUESTS_FILENAME

    _, uid = server_base
    _become("alice")
    _call_submit(universe_id=uid, text="keep this")
    udir = Path(os.environ["TINYASSETS_DATA_DIR"]) / uid
    q_before = read_queue(udir)
    btid = q_before[0].branch_task_id
    resp = json.loads(
        _action_queue_cancel(universe_id=uid, branch_task_id=btid),
    )
    assert resp["status"] == "cancelled"
    # requests.json still has the entry (WorkTarget source-of-truth)
    reqs_path = udir / REQUESTS_FILENAME
    reqs = json.loads(reqs_path.read_text(encoding="utf-8"))
    assert len(reqs) == 1


def test_invariant_3_dispatcher_does_not_call_producers(
    universe_dir, monkeypatch,
):
    """R9 / Invariant §4.3 #3: producers run inside the graph's
    review gates, NEVER inside the dispatcher.

    Patch at the registry boundary — `tinyassets.producers.registered_producers()`.
    """
    from tinyassets import producers as prod_mod

    # Snapshot real registry, replace with a mock producer counter.
    calls: list[str] = []

    class _CountingProducer:
        name = "counting"
        origin = "seed"

        def produce(self, universe_path, *, config=None):
            calls.append("called")
            return []

    saved = list(prod_mod._REGISTRY)
    prod_mod._REGISTRY.clear()
    prod_mod._REGISTRY.append(_CountingProducer())
    try:
        append_task(universe_dir, _make_task())
        # Multiple dispatcher operations — none should trigger a
        # producer call.
        cfg = DispatcherConfig()
        for _ in range(5):
            select_next_task(universe_dir, config=cfg)
            read_queue(universe_dir)
        assert calls == []
    finally:
        prod_mod._REGISTRY.clear()
        prod_mod._REGISTRY.extend(saved)


def test_invariant_3_producer_boundary_each_called_exactly_once(
    universe_dir,
):
    """R9 / Invariant §4.3 #3 complementary axis: the producer-running
    path (``run_producers``) invokes every registered producer exactly
    once per cycle, with distinct ``id(self)`` per producer.

    Patch ``.produce`` at the registry boundary — iterate
    ``registered_producers()`` and wrap each instance's method. Same
    care as unified-execution invariant 4's ``id()``-based check: a
    registration reshuffle that replaces a producer with a different
    instance of the same name must show as a new id, and the counting
    wrapper must catch a double-call regardless.
    """
    from tinyassets import producers as prod_mod
    from tinyassets.producers import run_producers

    class _Producer:
        def __init__(self, name: str):
            self.name = name
            self.origin = "seed"

        def produce(self, universe_path, *, config=None):
            return []

    saved = list(prod_mod._REGISTRY)
    prod_mod._REGISTRY.clear()
    p1 = _Producer("alpha")
    p2 = _Producer("beta")
    p3 = _Producer("gamma")
    prod_mod._REGISTRY.extend([p1, p2, p3])
    try:
        # Patch each registered producer's `.produce` with a
        # counting wrapper that records `id(self)` on entry.
        call_ids: list[int] = []
        for producer in prod_mod.registered_producers():
            original = producer.produce

            def _wrap(orig, pid):
                def _counted(universe_path, *, config=None):
                    call_ids.append(pid)
                    return orig(universe_path, config=config)
                return _counted
            producer.produce = _wrap(original, id(producer))

        # One observational cycle.
        run_producers(universe_dir)

        registered_ids = {id(p) for p in prod_mod.registered_producers()}
        # Each registered producer ran exactly once, all distinct by id.
        assert len(call_ids) == len(registered_ids)
        assert set(call_ids) == registered_ids
        # No id appears twice (catches a double-invocation regression).
        assert len(call_ids) == len(set(call_ids))
    finally:
        prod_mod._REGISTRY.clear()
        prod_mod._REGISTRY.extend(saved)


def test_invariant_9_positive_priority_never_silently_demotes(
    server_base, monkeypatch,
):
    """Unauthorized positive priority fails without an ordinary queue row."""
    _, uid = server_base
    _become("alice")
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", "host")
    response = _call_submit(
        universe_id=uid,
        text="anyone",
        priority_weight=999.0,
    )
    q = read_queue(Path(os.environ["TINYASSETS_DATA_DIR"]) / uid)
    assert response["error"] == "priority_authorization_required"
    assert q == []


# ───────────────────────────────────────────────────────────────────────
# MCP surface (4 tests)
# ───────────────────────────────────────────────────────────────────────


def test_queue_list_returns_sorted_scored_queue(server_base, monkeypatch):
    from tinyassets.api.universe import _action_queue_list

    _, uid = server_base
    _become("alice")
    _call_submit(universe_id=uid, text="A", request_type="general")
    _become("host")
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", "host")
    _call_submit(universe_id=uid, text="B", request_type="general")

    resp = json.loads(_action_queue_list(universe_id=uid))
    assert "queue" in resp
    assert len(resp["queue"]) == 2
    # Host identity alone grants no tier elevation; both remain ordinary work.
    assert {
        row["trigger_source"] for row in resp["queue"]
    } == {"user_request"}
    # scores present
    assert "score" in resp["queue"][0]
    # tier_status map present
    assert "tier_status" in resp
    assert resp["tier_status"]["host_request"] == "live"
    assert "stubbed" in resp["tier_status"]["goal_pool"]


def test_queue_list_merges_epoch2_without_exposing_request_text(
    server_base,
    monkeypatch,
):
    import tinyassets.branch_tasks_v2 as branch_tasks_v2

    monkeypatch.setattr(
        branch_tasks_v2,
        "EPOCH2_QUEUE_CONSUMER_READY",
        True,
    )
    from tinyassets.api.universe import _action_queue_list

    base, uid = server_base
    initialize_author_server(base)
    committed = _commit_epoch2(
        base,
        key_suffix="queue-list-v2",
        text="private operator request text",
        created_at=datetime.now(timezone.utc).isoformat(),
        universe_id=uid,
        directed_daemon_instruction="PRIVATE-INSTRUCTION-SENTINEL",
    )
    (base / uid / "branch_tasks.json").write_text(
        "{corrupt-v1",
        encoding="utf-8",
    )

    response = json.loads(_action_queue_list(universe_id=uid))

    assert response["pending_count"] == 1
    assert response["counts_complete"] is False
    assert response["epoch_counts"]["1"]["available"] is False
    assert response["epoch_health"]["2"]["available"] is True
    assert response["epoch_counts"]["2"]["depth"] == 1
    assert response["operational_state_counts"][
        "awaiting_compatible_capacity"
    ] == 1
    assert len(response["queue"]) == 1
    assert response["queue"][0]["branch_task_id"] == (
        committed["branch_task_id"]
    )
    assert response["queue"][0]["queue_epoch"] == 2
    assert "inputs" not in response["queue"][0]
    assert "private operator request text" not in json.dumps(response)
    assert "PRIVATE-INSTRUCTION-SENTINEL" not in json.dumps(response)
    assert "request-local" not in json.dumps(response)

    from tinyassets.daemon_registry import (
        control_runtime_instance,
        create_daemon,
        ensure_daemon_runtime,
        set_worker_queue_descriptor,
    )

    daemon = create_daemon(
        base,
        display_name="Queue status worker",
        created_by="test",
        soul_text="Serve the queue status integration fixture.",
    )
    runtime = ensure_daemon_runtime(
        base,
        daemon_id=daemon["daemon_id"],
        universe_id=uid,
        provider_name="codex",
        model_name="gpt-5",
        created_by="test",
        worker_id="worker-a",
    )
    now = datetime.now(timezone.utc)
    heartbeat = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phase": "polling",
        "subprocess_alive": True,
        "worker_id": "worker-a",
        "runtime_instance_id": runtime["runtime_instance_id"],
        "queue_protocol_version": 2,
        "capabilities": ["operator_request_v1"],
        "boot_id": "boot-a",
        "build_sha": "a" * 40,
        "config_hash": "sha256:" + "b" * 64,
        "universe_id": uid,
        "expires_at": (
            now + timedelta(seconds=60)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    set_worker_queue_descriptor(
        base,
        runtime_instance_id=runtime["runtime_instance_id"],
        descriptor=heartbeat,
        expected_worker_id="worker-a",
    )
    (base / uid / ".worker_supervisor.worker-a.json").write_text(
        json.dumps(heartbeat),
        encoding="utf-8",
    )
    with_capacity = json.loads(_action_queue_list(universe_id=uid))
    assert with_capacity["compatible_worker_count"] == 1
    assert with_capacity["eligible_epoch2_pending_count"] == 1
    assert with_capacity["operational_state_counts"][
        "awaiting_compatible_capacity"
    ] == 0

    control_runtime_instance(
        base,
        runtime_instance_id=runtime["runtime_instance_id"],
        action="pause",
        actor_id="test",
    )
    paused = json.loads(_action_queue_list(universe_id=uid))
    assert paused["compatible_worker_count"] == 0
    assert paused["eligible_epoch2_pending_count"] == 0
    assert paused["operational_state_counts"][
        "awaiting_compatible_capacity"
    ] == 1


def test_queue_list_discloses_staged_epoch2_work_awaiting_capacity(server_base):
    """With the epoch-2 consumer live, staged work is counted but not eligible.

    This asserted the inverse until `64f27fe7` ("Wire cloud drain epoch-2
    Branch consumer", #2182) activated the consumer: `consumer_ready` was
    False, capacity evidence was unavailable, and the response carried an
    `epoch2_consumer_not_ready` error. Live production now reports
    `consumer_ready: true`, so that contract is gone.

    The distinction still worth pinning is the one that replaced it: an
    activated consumer with **no live worker** must count the staged row as
    valid, refuse to call it eligible, and say exactly why.
    """
    from tinyassets.api.universe import _action_queue_list

    base, uid = server_base
    initialize_author_server(base)
    _commit_epoch2(
        base,
        key_suffix="consumer-not-ready",
        text="staged work",
        created_at=datetime.now(timezone.utc).isoformat(),
        universe_id=uid,
    )

    response = json.loads(_action_queue_list(universe_id=uid))

    # The consumer is live, so evidence is available and complete.
    assert response["consumer_ready"] is True
    assert response["capacity_evidence_available"] is True
    assert response["capacity_evidence_error"] is None
    assert response["counts_complete"] is True

    # The staged row is valid, but with no compatible worker it is not
    # eligible — validity and eligibility must not collapse into one another.
    assert response["valid_epoch2_pending_count"] == 1
    assert response["compatible_worker_count"] == 0
    assert response["eligible_epoch2_pending_count"] == 0

    # And the reason is disclosed rather than left implicit.
    assert response["operational_state_counts"]["awaiting_compatible_capacity"] == 1
    assert response["operational_reason_counts"]["awaiting_compatible_capacity"] == {
        "no_live_compatible_worker": 1
    }


def test_queue_list_preserves_v1_when_epoch2_read_fails(
    server_base,
    monkeypatch,
):
    from tinyassets.api.universe import _action_queue_list
    from tinyassets.branch_tasks_v2 import Epoch2OperationalRead

    base, uid = server_base
    append_task(
        base / uid,
        _make_task(universe_id=uid),
    )
    monkeypatch.setattr(
        "tinyassets.api.universe._epoch2_operational_read",
        lambda *_args, **_kwargs: Epoch2OperationalRead(
            summary={
                "available": False,
                "error": "injected_epoch2_failure",
                "diagnostics": [],
                "diagnostics_truncated": False,
                "compatible_worker_count": None,
            },
            candidates=(),
        ),
    )

    response = json.loads(_action_queue_list(universe_id=uid))

    assert response["pending_count"] == 1
    assert response["counts_complete"] is False
    assert response["epoch_health"]["1"]["available"] is True
    assert response["epoch_health"]["2"] == {
        "available": False,
        "error": "injected_epoch2_failure",
    }
    assert [row["branch_task_id"] for row in response["queue"]] != []


def test_queue_list_marks_bounded_operational_counts_incomplete(
    server_base,
    monkeypatch,
):
    from tinyassets.api.universe import _action_queue_list
    from tinyassets.branch_tasks_v2 import Epoch2OperationalRead

    _, uid = server_base
    monkeypatch.setattr(
        "tinyassets.api.universe._epoch2_operational_read",
        lambda *_args, **_kwargs: Epoch2OperationalRead(
            summary={
                "available": True,
                "depth": 1001,
                "lifecycle_counts": {
                    "pending": 1001,
                    "running": 0,
                    "cancel_requested": 0,
                    "cancelled": 0,
                    "succeeded": 0,
                    "failed": 0,
                },
                "operational_state_counts": {
                    "awaiting_compatible_capacity": 1000,
                    "invalid_operator_admission": 0,
                    "quarantined": 0,
                    "policy_parked": 0,
                },
                "operational_oldest_age_s": {},
                "operational_reason_counts": {},
                "diagnostics": [],
                "diagnostics_truncated": True,
                "valid_pending_count": 1000,
                "eligible_pending_count": 0,
                "compatible_worker_count": 0,
                "capacity_evidence_available": True,
                "operational_counts_authoritative": False,
                "unclassified_active_count": 1,
                "active_scan_limit": 1000,
            },
            candidates=(),
        ),
    )

    response = json.loads(_action_queue_list(universe_id=uid))

    assert response["counts_complete"] is False
    assert response["operational_counts_authoritative"] is False
    assert response["unclassified_epoch2_active_count"] == 1
    assert response["epoch2_active_scan_limit"] == 1000


def test_queue_list_restricts_exact_unscoped_integrity_count_to_admin(
    server_base,
    monkeypatch,
):
    import sqlite3

    from tinyassets.api.universe import _action_queue_list
    from tinyassets.storage import db_path

    base, uid = server_base
    initialize_author_server(base)
    committed = _commit_epoch2(
        base,
        key_suffix="unscoped-integrity",
        text="private unscoped fixture",
        created_at=datetime.now(timezone.utc).isoformat(),
        universe_id=uid,
    )
    with sqlite3.connect(db_path(base)) as conn:
        conn.execute(
            "DELETE FROM request_admissions WHERE admission_id = ?",
            (committed["admission_id"],),
        )
        conn.execute(
            "DELETE FROM user_requests WHERE request_id = ?",
            (committed["request_id"],),
        )

    monkeypatch.setattr(
        "tinyassets.api.universe._may_view_unscoped_epoch2_integrity",
        lambda _udir: False,
    )
    public_response = json.loads(_action_queue_list(universe_id=uid))

    assert public_response["counts_complete"] is False
    assert public_response["integrity_scope_complete"] is False
    assert "unscoped_invalid_count" not in public_response

    monkeypatch.setattr(
        "tinyassets.api.universe._may_view_unscoped_epoch2_integrity",
        lambda _udir: True,
    )
    admin_response = json.loads(_action_queue_list(universe_id=uid))

    assert admin_response["counts_complete"] is False
    assert admin_response["unscoped_invalid_count"] == 1


def test_queue_list_capacity_is_specific_to_directed_daemon(
    server_base,
    monkeypatch,
):
    import tinyassets.branch_tasks_v2 as branch_tasks_v2

    monkeypatch.setattr(
        branch_tasks_v2,
        "EPOCH2_QUEUE_CONSUMER_READY",
        True,
    )
    from tinyassets.api.universe import _action_queue_list
    from tinyassets.daemon_registry import (
        create_daemon,
        ensure_daemon_runtime,
        set_worker_queue_descriptor,
    )

    base, uid = server_base
    initialize_author_server(base)
    worker_daemon = create_daemon(
        base,
        display_name="Available worker daemon",
        created_by="actor-a",
        soul_mode="soul",
        soul_text="Serve ordinary work.",
    )
    directed_daemon = create_daemon(
        base,
        display_name="Unavailable directed daemon",
        created_by="actor-a",
        soul_mode="soul",
        soul_text="Receive directed work.",
    )
    _commit_epoch2(
        base,
        key_suffix="ordinary-capacity",
        text="ordinary",
        created_at=datetime.now(timezone.utc).isoformat(),
        universe_id=uid,
    )
    directed = _commit_epoch2(
        base,
        key_suffix="directed-capacity",
        text="directed",
        created_at=datetime.now(timezone.utc).isoformat(),
        universe_id=uid,
        directed_daemon_id=directed_daemon["daemon_id"],
        directed_soul_hash=directed_daemon["soul_hash"],
    )
    runtime = ensure_daemon_runtime(
        base,
        daemon_id=worker_daemon["daemon_id"],
        universe_id=uid,
        provider_name="codex",
        model_name="gpt-5",
        created_by="actor-a",
        worker_id="worker-a",
    )
    now = datetime.now(timezone.utc)
    heartbeat = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phase": "polling",
        "subprocess_alive": True,
        "worker_id": "worker-a",
        "runtime_instance_id": runtime["runtime_instance_id"],
        "queue_protocol_version": 2,
        "capabilities": ["operator_request_v1"],
        "boot_id": "boot-a",
        "build_sha": "a" * 40,
        "config_hash": "sha256:" + "b" * 64,
        "universe_id": uid,
        "expires_at": (
            now + timedelta(seconds=60)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    set_worker_queue_descriptor(
        base,
        runtime_instance_id=runtime["runtime_instance_id"],
        descriptor=heartbeat,
        expected_worker_id="worker-a",
    )
    (base / uid / ".worker_supervisor.worker-a.json").write_text(
        json.dumps(heartbeat),
        encoding="utf-8",
    )

    response = json.loads(_action_queue_list(universe_id=uid))

    assert response["epoch_counts"]["2"]["lifecycle"]["pending"] == 2
    assert response["valid_epoch2_pending_count"] == 2
    assert response["eligible_epoch2_pending_count"] == 1
    assert response["operational_state_counts"][
        "awaiting_compatible_capacity"
    ] == 1
    awaiting_ids = {
        item["branch_task_id"]
        for item in response["operational_diagnostics"]
        if item["operational_state"] == "awaiting_compatible_capacity"
    }
    assert awaiting_ids == {directed["branch_task_id"]}


def test_queue_cancel_on_running_task_with_explicit_grant_requests_cancel(
    server_base, monkeypatch,
):
    """Task #21: an explicitly authorized actor gets cooperative cancel.

    Host identity alone is not authority. The other authorized path is the
    claiming daemon. See test_queue_cancel_on_running_task_unauthorized for
    the rejection path."""
    from tinyassets.api.universe import _action_queue_cancel
    from tinyassets.branch_tasks import is_task_cancel_requested

    _, uid = server_base
    _become("host")
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", "host")
    monkeypatch.setenv(
        "UNIVERSE_SERVER_CAPABILITIES",
        "cancel_branch_task",
    )
    _call_submit(universe_id=uid, text="A")
    udir = Path(os.environ["TINYASSETS_DATA_DIR"]) / uid
    q = read_queue(udir)
    btid = q[0].branch_task_id
    claim_task(udir, btid, "daemon-1")

    resp = json.loads(
        _action_queue_cancel(universe_id=uid, branch_task_id=btid),
    )
    assert resp["status"] == "cancel_requested"
    assert resp["branch_task_id"] == btid
    # Flag is set; status stays "running" — daemon flips it on next event.
    assert is_task_cancel_requested(udir, btid) is True
    final = next(t for t in read_queue(udir) if t.branch_task_id == btid)
    assert final.status == "running"
    assert final.cancel_requested is True


def test_queue_cancel_on_running_task_as_owner_requests_cancel(
    server_base, monkeypatch,
):
    """Task #21: the claiming daemon can self-cancel its running task."""
    from tinyassets.api.universe import _action_queue_cancel
    from tinyassets.branch_tasks import is_task_cancel_requested

    _, uid = server_base
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", "host")
    # Submitted by the fixture's granted requester...
    _call_submit(universe_id=uid, text="A")
    udir = Path(os.environ["TINYASSETS_DATA_DIR"]) / uid
    q = read_queue(udir)
    btid = q[0].branch_task_id
    claim_task(udir, btid, "daemon-1")

    # ...and cancelled by the daemon that claimed it. `server_base` STUBS
    # `current_identity`, so binding the contextvar is invisible here: the stub
    # is what has to say who is calling.
    monkeypatch.setattr(
        "tinyassets.auth.middleware.current_identity",
        lambda: Identity(
            user_id="daemon-1", username="daemon-1", capabilities=["write"],
        ),
    )

    resp = json.loads(
        _action_queue_cancel(universe_id=uid, branch_task_id=btid),
    )
    assert resp["status"] == "cancel_requested"
    assert is_task_cancel_requested(udir, btid) is True


def test_queue_cancel_on_running_task_unauthorized(
    server_base, monkeypatch,
):
    """Task #21: actors that are neither host nor owner get rejected.

    Carries forward the original test's intent — running tasks require
    authorization — but narrows it to the actually-unauthorized case
    instead of the now-always-host case."""
    from tinyassets.api.universe import _action_queue_cancel

    _, uid = server_base
    _become("random-guest")
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", "host")
    _call_submit(universe_id=uid, text="A")
    udir = Path(os.environ["TINYASSETS_DATA_DIR"]) / uid
    q = read_queue(udir)
    btid = q[0].branch_task_id
    claim_task(udir, btid, "daemon-1")

    resp = json.loads(
        _action_queue_cancel(universe_id=uid, branch_task_id=btid),
    )
    assert resp["status"] == "rejected"
    assert resp["error"] == "cancel_not_authorized"


def test_queue_cancel_missing_id_rejects():
    from tinyassets.api.universe import _action_queue_cancel

    resp = json.loads(_action_queue_cancel(universe_id="anything"))
    assert "error" in resp


def test_tier_status_map_reflects_stubbed_vs_live():
    cfg = DispatcherConfig()
    tm = cfg.tier_status_map()
    assert tm["host_request"] == "live"
    assert tm["operator_request"] == "live"
    assert cfg.tier_weights["operator_request"] == 100.0
    assert tm["owner_queued"] == "live"
    assert tm["user_request"] == "live"  # default accept_external=True
    assert "stubbed" in tm["goal_pool"]
    # Paid bids moved from "stubbed" to "disabled"
    # (until accept_paid_bids is True).
    assert tm["paid_bid"] == "disabled"
    assert "stubbed" in tm["opportunistic"]

    cfg2 = DispatcherConfig(accept_external_requests=False,
                            accept_goal_pool=True)
    tm2 = cfg2.tier_status_map()
    assert tm2["user_request"] == "disabled"
    assert tm2["goal_pool"] == "live"


# ───────────────────────────────────────────────────────────────────────
# Config loading (small bonus coverage)
# ───────────────────────────────────────────────────────────────────────


def test_load_dispatcher_config_missing_file_returns_defaults(universe_dir):
    cfg = load_dispatcher_config(universe_dir)
    assert cfg.accept_external_requests is True
    assert cfg.accept_goal_pool is False


def test_load_dispatcher_config_reads_yaml_overrides(universe_dir):
    cfg_path = universe_dir / "dispatcher_config.yaml"
    cfg_path.write_text(
        "accept_external_requests: false\n"
        "accept_goal_pool: true\n"
        "tier_weights:\n"
        "  host_request: 200\n",
        encoding="utf-8",
    )
    cfg = load_dispatcher_config(universe_dir)
    assert cfg.accept_external_requests is False
    assert cfg.accept_goal_pool is True
    assert cfg.tier_weights["host_request"] == 200.0
    assert cfg.tier_weights["operator_request"] == 100.0


def test_directed_owner_boost_is_additive_without_tier_relabel():
    cfg = DispatcherConfig()
    now = "2026-07-24T08:00:00+00:00"
    directed = _make_task(
        trigger_source="owner_queued",
        priority_weight=25,
        queued_at=now,
    )
    directed.directed_daemon_id = "daemon-a"
    operator = _make_task(
        trigger_source="operator_request",
        priority_weight=0,
        queued_at=now,
    )
    ordinary = _make_task(
        trigger_source="user_request",
        priority_weight=0,
        queued_at=now,
    )

    assert directed.trigger_source == "owner_queued"
    assert score_task(directed, now_iso=now, config=cfg) == 115.0
    assert score_task(operator, now_iso=now, config=cfg) == 110.0
    assert score_task(ordinary, now_iso=now, config=cfg) == 70.0


def test_archive_after_days_override(universe_dir):
    """ARCHIVE_AFTER_DAYS override (exposed for test override per §4.3 #10)."""
    old = datetime.now(timezone.utc) - timedelta(days=2)
    t = _make_task(status="pending", queued_at=old.isoformat())
    append_task(universe_dir, t)
    qp = queue_path(universe_dir)
    raw = json.loads(qp.read_text(encoding="utf-8"))
    raw[0]["status"] = "succeeded"
    qp.write_text(json.dumps(raw), encoding="utf-8")

    # With override of 1 day, 2-day-old succeeded task is archived.
    result = garbage_collect(universe_dir, archive_after_days=1)
    assert result["archived"] == 1


def test_default_archive_constant_matches_spec():
    # Invariant 10: default 30 days.
    assert bt_mod.ARCHIVE_AFTER_DAYS == 30


def test_queue_filename_constants_match_spec():
    assert QUEUE_FILENAME == "branch_tasks.json"
    assert ARCHIVE_FILENAME == "branch_tasks_archive.json"
