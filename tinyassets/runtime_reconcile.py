"""On-demand, dry-run-first retirement reconciliation tools."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tinyassets import daemon_registry, daemon_server
from tinyassets.branch_tasks_v2 import (
    EPOCH2_QUEUE_CONSUMER_READY,
    Epoch2BranchTask,
    Epoch2BranchTaskAdapter,
)
from tinyassets.dispatcher import load_dispatcher_config, prefers_request_type
from tinyassets.platform_runtime_provenance import require_process_cloud_admission
from tinyassets.sqlite_connection import ClosingConnection
from tinyassets.storage import DB_FILENAME, data_dir
from tinyassets.storage.request_admissions import RequestAdmissionStore

STALE_TASK_REASON = (
    "stale_awaiting_compatible_capacity_retired_fleet"
)


class ReconcileGuardError(RuntimeError):
    """The apply request does not match its reviewed dry-run plan."""


@dataclass(frozen=True)
class StaleFleetPlan:
    cutoff: str
    plan_digest: str
    tasks: tuple[dict[str, Any], ...]
    runtimes: tuple[dict[str, Any], ...]

    @property
    def task_count(self) -> int:
        return len(self.tasks)

    @property
    def runtime_count(self) -> int:
        return len(self.runtimes)

    def as_output(self, *, mode: str) -> dict[str, Any]:
        return {
            "mode": mode,
            "cutoff": self.cutoff,
            "plan_digest": self.plan_digest,
            "task_count": self.task_count,
            "runtime_count": self.runtime_count,
            "tasks": list(self.tasks),
            "runtimes": list(self.runtimes),
        }


class _LegacyCapacityMatcher:
    """Freeze the current status-surface matcher for this retirement tool."""

    def __init__(self, base_path: Path, *, now: datetime) -> None:
        self.base_path = base_path
        self.now = now
        self._states: dict[str, tuple[Any, list[dict[str, str]]]] = {}
        self._runtime_rows = self._read_provisioned_runtimes()

    def _read_provisioned_runtimes(self) -> dict[str, list[dict[str, Any]]]:
        database = self.base_path / DB_FILENAME
        if not database.is_file():
            return {}
        uri = f"{database.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, factory=ClosingConnection) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute(
                "SELECT * FROM author_runtime_instances "
                "WHERE status = 'provisioned'"
            ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["universe_id"]), []).append(dict(row))
        return grouped

    def _state(self, universe_id: str) -> tuple[Any, list[dict[str, str]]]:
        cached = self._states.get(universe_id)
        if cached is not None:
            return cached
        universe_path = self.base_path / universe_id
        config = load_dispatcher_config(universe_path)
        workers: list[dict[str, str]] = []
        if EPOCH2_QUEUE_CONSUMER_READY is True:
            from tinyassets.api.universe import _classify_epoch2_workers

            runtime_rows = self._runtime_rows.get(universe_id, [])
            descriptors: dict[str, dict[str, Any] | None] = {}
            runtime_by_id: dict[str, dict[str, Any]] = {}
            for row in runtime_rows:
                try:
                    metadata = json.loads(str(row["metadata_json"] or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(metadata, dict):
                    continue
                runtime_id = str(row["instance_id"])
                descriptor = metadata.get("queue_protocol_descriptor")
                descriptors[runtime_id] = (
                    descriptor if isinstance(descriptor, dict) else None
                )
                runtime_by_id[runtime_id] = row
            workers, _evidence = _classify_epoch2_workers(
                universe_path,
                now=self.now,
                trusted_descriptors=descriptors,
            )
            for worker in workers:
                row = runtime_by_id.get(worker["runtime_instance_id"], {})
                worker.update({
                    "daemon_id": str(row.get("author_id") or "").replace(
                        "author::",
                        "daemon::",
                        1,
                    ),
                    "provider_name": str(row.get("provider_name") or ""),
                    "model_name": str(row.get("model_name") or ""),
                })
        state = (config, workers)
        self._states[universe_id] = state
        return state

    def policy_matches(self, task: Epoch2BranchTask) -> bool:
        config, _workers = self._state(task.universe_id)
        return bool(config.tier_enabled(task.trigger_source))

    def capacity_matches(self, task: Epoch2BranchTask) -> bool:
        config, workers = self._state(task.universe_id)
        if (
            task.required_llm_type
            and config.served_llm_type
            and task.required_llm_type != config.served_llm_type
        ):
            return False
        if not prefers_request_type(task.request_type):
            return False
        if task.directed_daemon_id:
            return any(
                worker["daemon_id"] == task.directed_daemon_id
                for worker in workers
            )
        return bool(workers)


def _cutoff(*, now: datetime, older_than_hours: float) -> datetime:
    if older_than_hours < 0:
        raise ValueError("older-than-hours must be non-negative")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stable_now = now.astimezone(timezone.utc).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    return stable_now - timedelta(hours=older_than_hours)


def build_stale_fleet_plan(
    base_path: Path,
    *,
    older_than_hours: float,
    now: datetime | None = None,
) -> StaleFleetPlan:
    observed_at = now or datetime.now(timezone.utc)
    cutoff = _cutoff(
        now=observed_at,
        older_than_hours=older_than_hours,
    )
    matcher = _LegacyCapacityMatcher(base_path, now=observed_at)
    task_entries = tuple(
        asdict(item)
        for item in Epoch2BranchTaskAdapter(
            base_path,
        ).plan_stale_capacity_cancellation(
            cutoff=cutoff,
            capacity_matcher=matcher.capacity_matches,
            policy_matcher=matcher.policy_matches,
        )
    )
    runtime_entries = tuple(
        asdict(item)
        for item in daemon_registry.plan_stale_cloud_worker_runtime_retirement(
            base_path,
            cutoff=cutoff,
        )
    )
    digest_payload = {
        "cutoff": cutoff.isoformat(),
        "task_ids": sorted(
            item["branch_task_id"] for item in task_entries
        ),
        "runtime_ids": sorted(
            item["instance_id"] for item in runtime_entries
        ),
    }
    digest = hashlib.sha256(json.dumps(
        digest_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")).hexdigest()
    return StaleFleetPlan(
        cutoff=cutoff.isoformat(),
        plan_digest=digest,
        tasks=task_entries,
        runtimes=runtime_entries,
    )


def _apply_plan(base_path: Path, plan: StaleFleetPlan) -> dict[str, int]:
    """Write boundary. Read-only planning and the dry run stay ungated.

    This is explicit operator retirement, not automatic recovery: a human has
    already reviewed a dry-run plan and re-supplied its digest and both exact
    counts, and what follows cancels exactly those approved stale tasks and
    retires exactly those stale runtimes. It assigns nothing and mints nothing,
    so it is never an admitted successor for pending work -- but it is a
    platform write, so the process must be admitted like any other write site.

    Gated here rather than in ``main`` so the boundary is the writes themselves:
    an in-process caller gets the same refusal as the CLI. The resolve happens
    before the store is constructed and before any transaction opens, per
    `design.md` § Enforcement sites (A).
    """
    require_process_cloud_admission(surface="stale fleet retirement")
    cutoff = datetime.fromisoformat(plan.cutoff)
    store = RequestAdmissionStore(base_path)
    applied_tasks = 0
    for item in plan.tasks:
        updated = store.cancel_pending_v2_task_if_stale(
            item["branch_task_id"],
            cutoff=cutoff,
            expected_queued_at=item["queued_at"],
            expected_grant_generation=item["grant_generation"],
            expected_body_digest=item["body_digest"],
            expected_row_digest=item["row_digest"],
            reason=STALE_TASK_REASON,
        )
        if updated is None:
            raise ReconcileGuardError(
                f"task CAS changed: {item['branch_task_id']}"
            )
        applied_tasks += 1
    applied_runtimes = 0
    for item in plan.runtimes:
        updated = daemon_server.retire_runtime_instance_if_stale(
            base_path,
            instance_id=item["instance_id"],
            cutoff=cutoff,
            expected_updated_at=item["updated_at"],
            expected_row_digest=item["row_digest"],
        )
        if updated is None:
            raise ReconcileGuardError(
                f"runtime CAS changed: {item['instance_id']}"
            )
        applied_runtimes += 1
    return {
        "applied_task_count": applied_tasks,
        "applied_runtime_count": applied_runtimes,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tinyassets.runtime_reconcile")
    commands = parser.add_subparsers(dest="command", required=True)
    stale = commands.add_parser("stale-fleet")
    stale.add_argument("--older-than-hours", type=float, default=336)
    mode = stale.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    stale.add_argument("--expected-plan-digest")
    stale.add_argument("--expect-task-count", type=int)
    stale.add_argument("--expect-runtime-count", type=int)
    stale.add_argument("--data-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    base_path = args.data_dir if args.data_dir is not None else data_dir()
    try:
        plan = build_stale_fleet_plan(
            base_path,
            older_than_hours=args.older_than_hours,
        )
        if not args.apply:
            print(json.dumps(
                plan.as_output(mode="dry-run"),
                sort_keys=True,
                indent=2,
            ))
            return 0
        missing = [
            name
            for name, value in (
                ("--expected-plan-digest", args.expected_plan_digest),
                ("--expect-task-count", args.expect_task_count),
                ("--expect-runtime-count", args.expect_runtime_count),
            )
            if value is None
        ]
        if missing:
            raise ReconcileGuardError(
                "apply requires " + ", ".join(missing)
            )
        mismatches = []
        if args.expected_plan_digest != plan.plan_digest:
            mismatches.append("plan digest")
        if args.expect_task_count != plan.task_count:
            mismatches.append("task count")
        if args.expect_runtime_count != plan.runtime_count:
            mismatches.append("runtime count")
        if mismatches:
            raise ReconcileGuardError(
                "reviewed plan mismatch: " + ", ".join(mismatches)
            )
        output = plan.as_output(mode="apply")
        output.update(_apply_plan(base_path, plan))
        print(json.dumps(output, sort_keys=True, indent=2))
        return 0
    # PermissionError is the admission refusal from `_apply_plan`. Its message
    # is the sanitized `platform_not_cloud` token plus a literal surface name --
    # it carries no id, path, address or hostname -- so it belongs in the same
    # JSON error channel as the guard errors rather than as a traceback.
    except (ReconcileGuardError, PermissionError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
