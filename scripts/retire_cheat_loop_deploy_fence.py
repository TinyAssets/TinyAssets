#!/usr/bin/env python3
"""Transitional production writer fence for retire-cheat-loop task 2.1.

This helper exists only for the filing-only cutover. Task 2.5 owns the locked
receipt/queue migration and removal of this product-specific deployment guard.
It never mutates receipt or queue data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote

try:
    import fcntl
except ImportError:  # pragma: no cover - production helper runs on Linux.
    fcntl = None

# The stop-writer fence's EXACT known writer fleet. The fixed provider-shaped
# worker fleet (tinyassets-worker + -codex-2/-claude-1/-claude-2) was retired
# under the "no host writer — prune all fleets" direction (execution is
# credential-driven, not a standing fleet), so the daemon is the only writer
# container now. Listing the removed workers made the fence's unguarded
# `docker inspect tinyassets-worker` fail with "no such object" → every deploy
# aborted at the stop-writer preflight (outages from 2026-08-10 onward). This
# tuple stays paired 1:1 with RECOVERY_SERVICES (strict zips below).
EXPECTED_CONTAINERS = (
    "tinyassets-daemon",
)
CANONICAL_SIDECARS = (
    ("tinyassets-tunnel", "cloudflared"),
    ("tinyassets-logs", "logs"),
)
CANONICAL_SIDECAR_IMAGES = {
    "tinyassets-tunnel": (
        "cloudflare/cloudflared:2026.3.0@sha256:"
        "6b599ca3e974349ead3286d178da61d291961182ec3fe9c505e1dd02c8ac31b0"
    ),
    "tinyassets-logs": (
        "timberio/vector:0.40.0-alpine@sha256:"
        "7a81fdd62e056321055a9e4bdec4073d752ecf68f4c192e676b85001721523c2"
    ),
}
CANONICAL_SIDECAR_MOUNTS = {
    "tinyassets-tunnel": (),
    "tinyassets-logs": (
        ("/opt/tinyassets/deploy/vector.yaml", "/etc/vector/vector.yaml"),
        (
            "/opt/tinyassets/deploy/vector-betterstack.yaml",
            "/etc/vector/vector-betterstack.yaml",
        ),
        (
            "/opt/tinyassets/deploy/vector-entrypoint.sh",
            "/etc/vector/vector-entrypoint.sh",
        ),
        ("/var/run/docker.sock", "/var/run/docker.sock"),
    ),
}
CANONICAL_COMPOSE_PROJECT = "tinyassets"
AUDITED_FULL_COMPOSE_RECOVERY_RUN_IDS = (
    "30514843571-1",
    "30514946746-1",
    "30515026545-1",
    "30515117371-1",
    "30517431860-1",
    "30518735998-1",
)
# Compose service names paired 1:1 with EXPECTED_CONTAINERS (strict zips). The
# retired worker services are dropped here in lockstep — a mismatch in length
# would raise on `zip(..., strict=True)`.
RECOVERY_SERVICES = (
    "daemon",
)
RECOVERY_SIDECAR_SERVICES = tuple(
    service for _name, service in CANONICAL_SIDECARS
)
RESTART_RACER_UNITS = (
    "daemon-watchdog.timer",
    "daemon-watchdog.service",
    "tinyassets-watchdog.timer",
    "tinyassets-watchdog.service",
    "tinyassets-autoheal.timer",
    "tinyassets-autoheal.service",
)
DAEMON_SERVICE = "tinyassets-daemon.service"
VOLUME_NAME = "tinyassets-data"
DEFAULT_RECEIPT_PATH = "/data/wiki_trigger_attempts.db"
DEFAULT_STATE_PATH = Path(
    "/var/lib/tinyassets-deploy/retire-cheat-loop-task-2-1-fence.json"
)
DEFAULT_LOCK_PATH = Path("/run/lock/tinyassets-deploy-fence.lock")
HOST_COMMAND_TIMEOUT_SECONDS = 45
# Stopping the daemon writer unit runs `ExecStop=docker compose down --timeout 30`
# under a systemd `TimeoutStopSec=60s`, so a legitimate quiesce can take the full
# ~60s (SIGTERM grace across the fleet, then SIGKILL) — LONGER than the 45s default
# host budget. Live 2026-08-10 (deploy run 31430209381, sha 91729b9b): the stop
# tripped `host command timed out after 45s: systemctl stop`, the fence downed the
# fleet mid-quiesce, and the no-image-mutation rollback was skipped, leaving prod
# with zero daemon containers. The stop budget MUST exceed the unit's TimeoutStopSec
# with margin (and stays well under the workflow's 300s systemd-run RuntimeMaxSec).
WRITER_UNIT_STOP_TIMEOUT_SECONDS = 120
LOCK_TIMEOUT_SECONDS = 60
# 120s was shorter than the daemon service's real startup. Live 2026-08-05,
# twice (runs 31057866767 and 31058104613): finalize failed with
# `mismatches={'tinyassets-daemon.service': {'expected': {'active': 'active'},
# 'actual': {'active': 'activating'}}}` -- the unit was starting correctly and
# simply had not finished, and the failure RE-FENCED a fleet that had already
# come up healthy, leaving /mcp at 502.
#
# The daemon's own healthcheck allows a 60s start_period before it even begins
# reporting, and the service waits on that, so 120s could not reliably cover a
# cold start. This is the same shape as the recovery canary that probed 0.7s
# after container start.
UNIT_RESTORE_TIMEOUT_SECONDS = 420
QUIESCED_RESTORE_PROOF_TIMEOUT_SECONDS = 60
LOOPBACK_HEALTH_TIMEOUT_SECONDS = 90
AUTHORITATIVE_UNIT_ACTIVE_STATES = frozenset(
    {
        "active",
        "reloading",
        "inactive",
        "failed",
        "activating",
        "deactivating",
    }
)
AUTHORITATIVE_UNIT_ENABLED_STATES = frozenset(
    {
        "enabled",
        "enabled-runtime",
        "linked",
        "linked-runtime",
        "static",
        "disabled",
        "indirect",
        "generated",
        "transient",
        "masked",
        "masked-runtime",
    }
)
RECOVERY_LEASE_SECONDS = 600
RECOVERY_SIDECAR_START_ATTEMPTS = 2
RECOVERY_COMPOSE_PATH = Path("/opt/tinyassets/compose.yml")
RECOVERY_COMPOSE_OVERRIDE_PATH = Path(
    "/opt/tinyassets/deploy/recovery-restart-no.yml"
)
RECOVERY_SCRIPT_PATH = Path(
    "/opt/tinyassets/deploy/retire-cheat-loop-deploy-fence.py"
)
RECOVERY_RECONCILE_SERVICE = "tinyassets-recovery-reconcile.service"
TASK_OWNER = "retire-cheat-loop task 2.1"
V1_RISK_STATUSES = frozenset({"pending", "running"})
V2_RISK_STATUSES = frozenset({"pending", "running", "cancel_requested"})
CANONICAL_IMAGE_RE = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+"
    r"@sha256:[0-9a-f]{64}$"
)
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
RECOVERY_PROJECT_RE = re.compile(r"^tinyassets-recovery-[0-9a-f]{16}$")
WRITER_PROCESS_MARKERS = (
    "tinyassets.universe_server",
    "tinyassets.daemon_server",
    # DELIBERATELY outlives its module. `tinyassets/cloud_worker.py` was deleted
    # on 2026-08-29 (nothing runs outside a user's universe -- PLAN.md), so no
    # current image can start one. This is a DETECTOR, not a service list: a
    # container left running from a pre-prune image is precisely the stray
    # writer the prune creates, and dropping the marker would blind the fence
    # to it. Remove only once no pre-2026-08-29 image can still be running.
    "tinyassets.cloud_worker",
    "claude-plugin",
    "mcpb",
)
MAX_STRAY_WRITER_PROCESS_CANDIDATES = 100
_PROCESS_START_TIME_KEY = "_process_start_time_ticks"
RECEIPT_COLUMNS = (
    "trigger_attempt_id",
    "request_id",
    "request_kind",
    "request_page",
    "status",
    "attempted_at",
    "goal_id",
    "branch_def_id",
    "queued_at",
    "run_id",
    "dispatcher_request_id",
    "error_class",
    "error_message",
)


class FenceError(RuntimeError):
    """Fail-closed deployment fence error."""


@dataclass(frozen=True)
class ReceiptStore:
    container_path: str
    host_path: Path


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_only_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def receipt_snapshot(path: Path) -> dict[str, Any]:
    """Return a deterministic logical snapshot without creating the store."""

    empty_digest = hashlib.sha256(b"").hexdigest()
    if not path.is_file():
        return {
            "exists": False,
            "quick_check": ["absent"],
            "schema": [],
            "row_count": 0,
            "status_counts": {},
            "max_attempted_at": None,
            "logical_digest": empty_digest,
        }
    try:
        with closing(_read_only_connection(path)) as connection:
            quick_check = [
                str(row[0]) for row in connection.execute("PRAGMA quick_check")
            ]
            if quick_check != ["ok"]:
                raise FenceError(
                    f"receipt sqlite quick_check failed: {quick_check!r}"
                )
            table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='wiki_trigger_attempts'"
            ).fetchone()
            if table is None:
                raise FenceError("receipt sqlite lacks exact receipt schema")
            columns = tuple(
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(wiki_trigger_attempts)"
                )
            )
            if columns != RECEIPT_COLUMNS:
                raise FenceError("receipt sqlite lacks exact receipt schema")
            schema = [
                {
                    "type": str(row["type"]),
                    "name": str(row["name"]),
                    "table": str(row["tbl_name"]),
                    "sql": str(row["sql"] or ""),
                }
                for row in connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "ORDER BY type,name"
                )
            ]
            quoted_columns = ",".join(f'"{column}"' for column in columns)
            rows = [
                list(row)
                for row in connection.execute(
                    f"SELECT {quoted_columns} FROM wiki_trigger_attempts "
                    "ORDER BY trigger_attempt_id"
                )
            ]
            status_counts = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT status,COUNT(*) FROM wiki_trigger_attempts "
                    "GROUP BY status ORDER BY status"
                )
            }
            max_attempted_at = connection.execute(
                "SELECT MAX(attempted_at) FROM wiki_trigger_attempts"
            ).fetchone()[0]
    except FenceError:
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise FenceError(
            f"receipt sqlite unreadable: {type(exc).__name__}"
        ) from exc

    logical_rows = b"".join(_json_bytes(row) + b"\n" for row in rows)
    return {
        "exists": True,
        "quick_check": quick_check,
        "schema": schema,
        "row_count": len(rows),
        "status_counts": status_counts,
        "max_attempted_at": max_attempted_at,
        "logical_digest": hashlib.sha256(logical_rows).hexdigest(),
    }


def _v1_tasks(raw: Any) -> list[dict[str, Any]]:
    tasks = raw if isinstance(raw, list) else raw.get("tasks") if isinstance(raw, dict) else None
    if not isinstance(tasks, list) or any(not isinstance(task, dict) for task in tasks):
        raise FenceError("v1 queue unreadable: unexpected shape")
    return tasks


def inventory_queue_risk(volume_dir: Path) -> list[dict[str, str]]:
    """Inventory every executable retired v1/v2 queue row, read-only."""

    risks: list[dict[str, str]] = []
    for path in sorted(volume_dir.rglob("branch_tasks.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            tasks = _v1_tasks(raw)
        except FenceError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FenceError(
                f"v1 queue unreadable: {path.relative_to(volume_dir)}"
            ) from exc
        for task in tasks:
            status = str(task.get("status", "")).lower()
            if (
                task.get("request_type") == "bug_investigation"
                and status in V1_RISK_STATUSES
            ):
                risks.append(
                    {
                        "id": str(task.get("branch_task_id") or ""),
                        "status": status,
                        "store": path.relative_to(volume_dir).as_posix(),
                        "version": "v1",
                    }
                )

    for path in sorted(volume_dir.rglob(".tinyassets.db")):
        try:
            with closing(_read_only_connection(path)) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "branch_tasks_v2" not in tables:
                    continue
                if "user_requests" not in tables:
                    raise FenceError("v2 queue schema incomplete: user_requests")
                task_columns = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(branch_tasks_v2)"
                    )
                }
                request_columns = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(user_requests)"
                    )
                }
                if not {"branch_task_id", "request_id", "status"} <= task_columns:
                    raise FenceError("v2 queue schema incomplete: branch_tasks_v2")
                if not {"request_id", "request_type"} <= request_columns:
                    raise FenceError("v2 queue schema incomplete: user_requests")
                foreign_key_failures = list(
                    connection.execute("PRAGMA foreign_key_check")
                )
                if foreign_key_failures:
                    raise FenceError("v2 queue foreign key check failed")
                placeholders = ",".join("?" for _ in V2_RISK_STATUSES)
                rows = connection.execute(
                    "SELECT bt.branch_task_id,bt.status,ur.request_type "
                    "FROM branch_tasks_v2 AS bt "
                    "LEFT JOIN user_requests AS ur ON ur.request_id=bt.request_id "
                    f"WHERE bt.status IN ({placeholders}) "
                    "ORDER BY bt.branch_task_id",
                    tuple(sorted(V2_RISK_STATUSES)),
                )
                for row in rows:
                    request_type = row[2]
                    if not isinstance(request_type, str) or not request_type.strip():
                        raise FenceError(
                            "v2 live task missing authoritative request type"
                        )
                    if request_type == "bug_investigation":
                        risks.append(
                            {
                                "id": str(row[0]),
                                "status": str(row[1]),
                                "store": path.relative_to(volume_dir).as_posix(),
                                "version": "v2",
                            }
                        )
        except FenceError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise FenceError(
                f"v2 queue unreadable: {path.relative_to(volume_dir)}"
            ) from exc
    return sorted(
        risks,
        key=lambda row: (row["version"], row["store"], row["id"], row["status"]),
    )


def _env_map(info: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in info.get("Config", {}).get("Env", []) or []:
        key, separator, value = str(item).partition("=")
        if separator:
            result[key] = value
    return result


def resolve_receipt_store(
    inspections: Mapping[str, Mapping[str, Any]],
    volume_dir: Path,
) -> ReceiptStore:
    """Resolve one controlled /data store shared by the exact writer fleet."""

    resolved_volume = volume_dir.resolve()
    selected_paths: set[str] = set()
    for name in EXPECTED_CONTAINERS:
        info = inspections.get(name)
        if info is None:
            raise FenceError(f"missing expected container: {name}")
        mounts = [
            mount
            for mount in info.get("Mounts", []) or []
            if mount.get("Destination") == "/data"
        ]
        if len(mounts) != 1:
            raise FenceError(f"{name} does not have exactly one /data mount")
        mount = mounts[0]
        if (
            mount.get("Name") != VOLUME_NAME
            or Path(str(mount.get("Source", ""))).resolve() != resolved_volume
        ):
            raise FenceError(f"{name} does not share {VOLUME_NAME}")
        selected = _env_map(info).get(
            "TINYASSETS_TRIGGER_RECEIPTS_DB",
            DEFAULT_RECEIPT_PATH,
        )
        normalized = posixpath.normpath(selected)
        if not normalized.startswith("/data/"):
            raise FenceError(f"{name} receipt path is outside /data")
        selected_paths.add(normalized)
    if len(selected_paths) != 1:
        raise FenceError("receipt path differs across the writer fleet")
    container_path = next(iter(selected_paths))
    host_path = (
        resolved_volume / container_path.removeprefix("/data/")
    ).resolve()
    if resolved_volume not in host_path.parents:
        raise FenceError("receipt path is outside /data")
    return ReceiptStore(container_path=container_path, host_path=host_path)


def safe_fleet_matches(
    observation: Mapping[str, Any],
    image_ref: str,
    revision: str,
    old_ids: Mapping[str, str],
) -> bool:
    if not CANONICAL_IMAGE_RE.fullmatch(image_ref):
        return False
    if not REVISION_RE.fullmatch(revision):
        return False
    containers = observation.get("containers")
    if not isinstance(containers, dict) or set(containers) != set(EXPECTED_CONTAINERS):
        return False
    if set(observation.get("volume_container_names", [])) != set(
        EXPECTED_CONTAINERS
    ):
        return False
    if observation.get("stray_writer_processes") or observation.get("queue_risk"):
        return False
    for name in EXPECTED_CONTAINERS:
        item = containers.get(name, {})
        if (
            item.get("running") is not True
            or item.get("image_ref") != image_ref
            or item.get("revision") != revision
            or not item.get("id")
            or item.get("id") == old_ids.get(name)
        ):
            return False
    return not observation.get("old_container_ids_running")


class Host:
    """Small subprocess boundary, replaceable in focused tests."""

    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout_seconds: int = HOST_COMMAND_TIMEOUT_SECONDS,
    ) -> str:
        try:
            result = subprocess.run(
                args,
                input=input_text,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise FenceError(
                f"host command timed out after {timeout_seconds}s: "
                f"{args[0]} {args[1] if len(args) > 1 else ''}"
            ) from exc
        # Kept for callers that need the command's diagnostics as well as its
        # stdout. `run` returns stdout only, because most callers json.loads it;
        # stashing stderr here lets the guarded-mutation path surface both without
        # changing this signature (and therefore without breaking the test doubles
        # that implement it).
        self.last_stderr = result.stderr
        if check and result.returncode:
            detail = result.stderr.strip().splitlines()
            bounded = detail[-1][:240] if detail else f"exit {result.returncode}"
            raise FenceError(f"{args[0]} {args[1] if len(args) > 1 else ''}: {bounded}")
        return result.stdout.strip()

    def container_info(self, name: str) -> dict[str, Any]:
        try:
            payload = json.loads(self.run(["docker", "inspect", name]))
            info = payload[0]
        except (IndexError, TypeError, json.JSONDecodeError) as exc:
            raise FenceError(f"invalid docker inspection for {name}") from exc
        if not isinstance(info, dict):
            raise FenceError(f"invalid docker inspection for {name}")
        return info

    def image_identity(self, image: str, expected_repository: str) -> tuple[str, str]:
        try:
            payload = json.loads(self.run(["docker", "image", "inspect", image]))
            info = payload[0]
        except (IndexError, TypeError, json.JSONDecodeError) as exc:
            raise FenceError("invalid docker image inspection") from exc
        repo_digests = [
            value
            for value in info.get("RepoDigests", []) or []
            if isinstance(value, str) and value.startswith(f"{expected_repository}@")
        ]
        if len(repo_digests) != 1 or not CANONICAL_IMAGE_RE.fullmatch(repo_digests[0]):
            raise FenceError("image does not resolve to one exact repository digest")
        revision = str(
            (info.get("Config", {}).get("Labels") or {}).get(
                "org.opencontainers.image.revision", ""
            )
        )
        if not REVISION_RE.fullmatch(revision):
            raise FenceError("image has no canonical source revision")
        return repo_digests[0], revision

    def volume_dir(self) -> Path:
        raw = self.run(
            [
                "docker",
                "volume",
                "inspect",
                VOLUME_NAME,
                "--format",
                "{{ .Mountpoint }}",
            ]
        )
        path = Path(raw).resolve()
        if not path.is_dir():
            raise FenceError("shared volume mountpoint is unavailable")
        return path

    def volume_container_names(self) -> list[str]:
        raw = self.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"volume={VOLUME_NAME}",
                "--format",
                "{{.Names}}",
            ]
        )
        return sorted(filter(None, raw.splitlines()))

    def container_pids(self, names: Iterable[str]) -> set[int]:
        pids: set[int] = set()
        for name in names:
            try:
                raw = self.run(["docker", "top", name, "-eo", "pid"])
            except FenceError:
                continue
            lines = raw.splitlines()
            if not lines or lines[0].strip() != "PID":
                continue
            pid_rows = [line.strip() for line in lines[1:]]
            if any(not re.fullmatch(r"[1-9][0-9]*", row) for row in pid_rows):
                continue
            pids.update(int(row) for row in pid_rows)
        return pids

    def container_restart_policy(self, identity: str) -> str:
        policy = self.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.HostConfig.RestartPolicy.Name}}",
                identity,
            ]
        )
        if policy not in {"no", "always", "unless-stopped", "on-failure"}:
            raise FenceError(
                f"container restart policy is not authoritative: {identity}"
            )
        return policy

    def unit_state(self, unit: str) -> dict[str, str]:
        return {
            "active": self.run(["systemctl", "is-active", unit], check=False),
            "enabled": self.run(["systemctl", "is-enabled", unit], check=False),
        }

    def unit_present(self, unit: str) -> bool:
        state = self.unit_load_state(unit)
        return state != "not-found"

    def unit_load_state(self, unit: str) -> str:
        state = self.run(
            ["systemctl", "show", "--property", "LoadState", "--value", unit]
        )
        if state not in {"loaded", "masked", "not-found"}:
            raise FenceError(f"unit load state is not authoritative: {unit}={state!r}")
        return state

    def unit_active_state(self, unit: str) -> str:
        state = self.run(
            ["systemctl", "show", "--property", "ActiveState", "--value", unit]
        )
        if state not in {
            "active",
            "reloading",
            "inactive",
            "failed",
            "activating",
            "deactivating",
        }:
            raise FenceError(f"unit active state is not authoritative: {unit}={state!r}")
        return state


def _configured_image() -> str:
    try:
        for line in Path("/etc/tinyassets/env").read_text(
            encoding="utf-8"
        ).splitlines():
            if line.startswith("TINYASSETS_IMAGE="):
                return line.partition("=")[2]
    except OSError as exc:
        raise FenceError("cannot read configured production image") from exc
    raise FenceError("configured production image is missing")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        _fsync_parent(path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _fsync_parent(path: Path) -> None:
    if os.name != "posix":
        return
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FenceError("durable stop-writer fence state is unavailable") from exc
    if state.get("owner") != TASK_OWNER or state.get("schema_version") != 1:
        raise FenceError("durable stop-writer fence state is invalid")
    return state


def _require_run_id(run_id: str) -> None:
    if not RUN_ID_RE.fullmatch(run_id):
        raise FenceError("deploy fence run identity is invalid")


def _require_state_run(state: Mapping[str, Any], run_id: str) -> None:
    _require_run_id(run_id)
    if state.get("run_id") != run_id:
        raise FenceError("durable stop-writer fence belongs to another deploy run")


def _exact_inspections(host: Host) -> dict[str, dict[str, Any]]:
    return {name: host.container_info(name) for name in EXPECTED_CONTAINERS}


def _container_running_exact(host: Host, identity: str) -> bool:
    """Prove an old container ID is absent or has one authoritative state."""

    output = host.run(
        [
            "docker",
            "ps",
            "-a",
            "--no-trunc",
            "--filter",
            f"id={identity}",
            "--format",
            "{{.ID}}|{{.State}}",
        ]
    )
    if not output:
        return False
    rows = output.splitlines()
    if len(rows) != 1:
        raise FenceError(f"container state is not authoritative: {identity}")
    container_id, separator, state = rows[0].partition("|")
    if separator != "|" or container_id != identity:
        raise FenceError(f"container state is not authoritative: {identity}")
    if state in {"running", "restarting", "paused"}:
        return True
    if state in {"created", "exited", "dead"}:
        return False
    raise FenceError(f"container state is not authoritative: {identity}={state!r}")


def _container_absent_exact(host: Host, identity: str) -> bool:
    """Prove that one exact container ID no longer exists in any state."""

    output = host.run(
        [
            "docker",
            "ps",
            "-a",
            "--no-trunc",
            "--filter",
            f"id={identity}",
            "--format",
            "{{.ID}}",
        ]
    )
    if not output:
        return True
    rows = output.splitlines()
    if len(rows) != 1 or rows[0] != identity:
        raise FenceError(f"container absence is not authoritative: {identity}")
    return False


def _named_container_running(host: Host, name: str) -> bool:
    output = host.run(
        [
            "docker",
            "ps",
            "--filter",
            f"name=^/{name}$",
            "--format",
            "{{.Names}}",
        ]
    )
    if not output:
        return False
    if output != name:
        raise FenceError(f"named container state is not authoritative: {name}")
    return True


def _named_container_absent_exact(host: Host, name: str) -> bool:
    """Prove an exact canonical container name is absent in every state."""

    output = host.run(
        [
            "docker",
            "ps",
            "-a",
            "--no-trunc",
            "--filter",
            f"name=^/{name}$",
            "--format",
            "{{.ID}}",
        ]
    )
    if not output:
        return True
    rows = output.splitlines()
    if len(rows) != 1 or not rows[0]:
        raise FenceError(f"named container absence is not authoritative: {name}")
    return False


def _prove_expected_container_names_absent(host: Host) -> None:
    """Fail closed unless every canonical container name is globally absent."""

    for name in EXPECTED_CONTAINERS:
        if not _named_container_absent_exact(host, name):
            raise FenceError(f"canonical target name still exists: {name}")


def _wait_units_quiesced(
    host: Host,
    units: Sequence[str],
    *,
    attempts: int = 20,
    delay_seconds: float = 0.5,
) -> None:
    remaining: dict[str, str] = {}
    for attempt in range(attempts):
        remaining = {
            unit: state
            for unit in units
            if (state := host.unit_active_state(unit)) not in {"inactive", "failed"}
        }
        if not remaining:
            return
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    raise FenceError(f"restart racer remains active after checked stop: {remaining}")


def _wait_units_restored(
    host: Host,
    expected_states: Mapping[str, Mapping[str, str]],
    *,
    timeout_seconds: float = UNIT_RESTORE_TIMEOUT_SECONDS,
    delay_seconds: float = 1.0,
) -> dict[str, dict[str, str]]:
    """Wait through transient systemd states, then require exact saved state."""

    actual: dict[str, dict[str, str]] = {}
    attempts = max(1, int(timeout_seconds / delay_seconds) + 1)
    for attempt in range(attempts):
        actual = {
            unit: _validated_unit_state(host, unit)
            for unit in expected_states
        }
        mismatches = {
            unit: {
                "expected": dict(expected_states[unit]),
                "actual": actual[unit],
            }
            for unit in expected_states
            if actual[unit] != expected_states[unit]
        }
        if not mismatches:
            return actual
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    raise FenceError(
        f"unit restoration proof failed after {timeout_seconds:g}s timeout: "
        f"mismatches={mismatches}"
    )


def _wait_units_stable_snapshot(
    host: Host,
    units: Sequence[str],
    *,
    timeout_seconds: float = UNIT_RESTORE_TIMEOUT_SECONDS,
    delay_seconds: float = 1.0,
) -> dict[str, dict[str, str]]:
    """Capture only stable systemd states before writing restoration intent."""

    states: dict[str, dict[str, str]] = {}
    transient: dict[str, dict[str, str]] = {}
    attempts = max(1, int(timeout_seconds / delay_seconds) + 1)
    for attempt in range(attempts):
        states = {unit: _validated_unit_state(host, unit) for unit in units}
        transient = {
            unit: state
            for unit, state in states.items()
            if state["active"] not in {"active", "inactive", "failed"}
        }
        if not transient:
            return states
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    raise FenceError(
        f"unit snapshot did not settle after {timeout_seconds:g}s timeout: "
        f"transient={transient}"
    )


def _set_restart_no(
    host: Host,
    consumers: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    proved: dict[str, str] = {}
    for name, info in consumers.items():
        identity = str(info.get("Id", ""))
        if not identity:
            raise FenceError(f"container identity is unavailable: {name}")
        host.run(["docker", "update", "--restart=no", identity])
        policy = host.container_restart_policy(identity)
        if policy != "no":
            raise FenceError(
                f"container restart fence did not persist: {name}={policy}"
            )
        proved[name] = policy
    return proved


def _apply_boot_fence(host: Host, present_racers: Sequence[str]) -> None:
    for unit in present_racers:
        if unit.endswith(".timer"):
            host.run(["systemctl", "disable", "--now", unit])
    host.run(["systemctl", "disable", DAEMON_SERVICE])


def _stop_and_mask_writer_units(
    host: Host,
    *,
    boot_fence_applied: bool = False,
) -> tuple[str, ...]:
    present_racers = tuple(
        unit for unit in RESTART_RACER_UNITS if host.unit_present(unit)
    )
    if not host.unit_present(DAEMON_SERVICE):
        raise FenceError(f"required production unit is missing: {DAEMON_SERVICE}")
    if not boot_fence_applied:
        _apply_boot_fence(host, present_racers)
    for unit in (*present_racers, DAEMON_SERVICE):
        # ONLY the daemon's ExecStop (docker compose down, TimeoutStopSec=60s) is
        # slow enough to need more than the 45s default; the racer timers stop
        # fast. Giving racers the long budget would let a hung racer consume 120s
        # EACH and cumulatively blow the workflow's 300s preflight budget, killing
        # the fence mid-quiesce (Codex 2026-08-10). So the long budget is daemon-only.
        stop_budget = (
            WRITER_UNIT_STOP_TIMEOUT_SECONDS
            if unit == DAEMON_SERVICE
            else HOST_COMMAND_TIMEOUT_SECONDS
        )
        host.run(["systemctl", "stop", unit], timeout_seconds=stop_budget)
    if present_racers:
        host.run(["systemctl", "mask", "--runtime", *present_racers])
    host.run(["systemctl", "mask", "--runtime", DAEMON_SERVICE])
    _wait_units_quiesced(host, (*present_racers, DAEMON_SERVICE))
    return present_racers


def _looks_like_writer_command(cmdline: str) -> bool:
    return any(marker in cmdline.lower() for marker in WRITER_PROCESS_MARKERS)


def _process_start_time_ticks(proc: Path) -> int:
    stat = (proc / "stat").read_text(encoding="utf-8", errors="strict")
    _prefix, separator, fields_text = stat.rpartition(")")
    if not separator:
        raise ValueError("process stat has no command terminator")
    fields = fields_text.split()
    if len(fields) <= 19 or not fields[19].isdigit():
        raise ValueError("process stat has no valid start time")
    return int(fields[19])


def _public_process_risk(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in candidate.items()
        if key != _PROCESS_START_TIME_KEY
    }


def _stray_writer_processes(
    receipt_path: Path,
    excluded_pids: set[int],
    volume_dir: Path | None = None,
) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    receipt_related = {
        receipt_path.resolve(),
        Path(f"{receipt_path}-wal").resolve(),
        Path(f"{receipt_path}-shm").resolve(),
    }
    for proc in sorted(Path("/proc").glob("[0-9]*")):
        try:
            pid = int(proc.name)
            if pid == os.getpid() or pid in excluded_pids:
                continue
            exe = Path(os.readlink(proc / "exe")).name
            cmdline = (
                (proc / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", "replace")
                .lower()
            )
            receipt_fd = False
            for descriptor in (proc / "fd").iterdir():
                try:
                    target = Path(os.path.realpath(descriptor)).resolve()
                except PermissionError as exc:
                    raise FenceError(
                        f"process scan permission denied: pid={pid}"
                    ) from exc
                except OSError:
                    continue
                if target in receipt_related:
                    receipt_fd = True
                    break
            server_like = _looks_like_writer_command(cmdline)
            controlled_path_env_keys: list[str] = []
            controlled_mount_namespace = False
            same_host_mount_namespace = False
            mount_namespace = ""
            if volume_dir is not None:
                resolved_volume = volume_dir.resolve()
                environ = (proc / "environ").read_bytes().split(b"\0")
                for item in environ:
                    key_raw, separator, value_raw = item.partition(b"=")
                    if not separator:
                        continue
                    value = value_raw.decode("utf-8", "replace")
                    if not value.startswith("/"):
                        continue
                    try:
                        candidate = Path(value).resolve()
                    except OSError:
                        continue
                    if candidate == resolved_volume or resolved_volume in candidate.parents:
                        controlled_path_env_keys.append(
                            key_raw.decode("utf-8", "replace")
                        )
                mountinfo = (proc / "mountinfo").read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                try:
                    mount_namespace = os.readlink(proc / "ns" / "mnt")
                    own_mount_namespace = os.readlink("/proc/self/ns/mnt")
                    same_host_mount_namespace = (
                        mount_namespace == own_mount_namespace
                    )
                except OSError as exc:
                    raise FenceError(
                        f"process mount namespace unreadable: pid={pid}"
                    ) from exc
                controlled_mount_namespace = (
                    str(resolved_volume) in mountinfo
                    and not same_host_mount_namespace
                )
            if (
                receipt_fd
                or server_like
                or controlled_path_env_keys
                or controlled_mount_namespace
            ):
                try:
                    process_start_time = _process_start_time_ticks(proc)
                except (OSError, UnicodeError, ValueError):
                    process_start_time = None
                if len(risks) >= MAX_STRAY_WRITER_PROCESS_CANDIDATES:
                    raise FenceError("writer process candidate limit exceeded")
                risks.append(
                    {
                        "pid": pid,
                        "exe": exe,
                        "receipt_fd": receipt_fd,
                        "server_like": server_like,
                        "controlled_path_env_keys": sorted(
                            set(controlled_path_env_keys)
                        ),
                        "controlled_mount_namespace": controlled_mount_namespace,
                        "same_host_mount_namespace": same_host_mount_namespace,
                        "mount_namespace": mount_namespace,
                        _PROCESS_START_TIME_KEY: process_start_time,
                    }
                )
        except PermissionError as exc:
            raise FenceError(f"process scan permission denied: pid={pid}") from exc
        except (FileNotFoundError, ProcessLookupError):
            continue
    return risks


def _confirm_stray_writer_processes(
    host: Host,
    candidates: Sequence[Mapping[str, Any]],
    container_identities: Sequence[str],
    *,
    proc_root: Path = Path("/proc"),
) -> list[dict[str, Any]]:
    """Reconcile scan candidates against a fresh Docker process snapshot."""

    owned_pids = host.container_pids(container_identities)
    confirmed: list[dict[str, Any]] = []
    for candidate in candidates:
        pid = int(candidate.get("pid") or 0)
        if pid <= 0:
            continue
        proc = proc_root / str(pid)
        if not proc.is_dir():
            continue
        try:
            current_start_time = _process_start_time_ticks(proc)
        except FileNotFoundError:
            if not proc.is_dir():
                continue
            current_start_time = None
        except (OSError, UnicodeError, ValueError):
            current_start_time = None
        scanned_start_time = candidate.get(_PROCESS_START_TIME_KEY)
        if (
            pid in owned_pids
            and scanned_start_time is not None
            and current_start_time == scanned_start_time
        ):
            continue
        confirmed.append(_public_process_risk(candidate))
    return confirmed


def observe_fleet(
    host: Host,
    *,
    expected_image_ref: str | None = None,
) -> dict[str, Any]:
    inspections = _exact_inspections(host)
    volume_dir = host.volume_dir()
    receipt = resolve_receipt_store(inspections, volume_dir)
    configured = expected_image_ref or _configured_image()
    if not CANONICAL_IMAGE_RE.fullmatch(configured):
        raise FenceError("configured image is not an immutable digest")
    repository = configured.partition("@")[0]
    containers: dict[str, dict[str, Any]] = {}
    for name, info in inspections.items():
        image_ref, revision = host.image_identity(
            str(info.get("Image", "")),
            repository,
        )
        containers[name] = {
            "id": str(info.get("Id", "")),
            "running": bool(info.get("State", {}).get("Running")),
            "pid": int(info.get("State", {}).get("Pid") or 0),
            "image_ref": image_ref,
            "revision": revision,
        }
    container_identities = tuple(
        str(info.get("Id", "")) for info in inspections.values()
    )
    excluded_pids = host.container_pids(container_identities)
    stray_candidates = _stray_writer_processes(
        receipt.host_path,
        excluded_pids,
        volume_dir,
    )
    return {
        "containers": containers,
        "volume_container_names": host.volume_container_names(),
        "receipt_container_path": receipt.container_path,
        "receipt_host_path": str(receipt.host_path),
        "receipt_snapshot": receipt_snapshot(receipt.host_path),
        "queue_risk": inventory_queue_risk(volume_dir),
        "stray_writer_processes": _confirm_stray_writer_processes(
            host,
            stray_candidates,
            container_identities,
        ),
    }


def _old_identity(
    host: Host,
    inspections: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str]:
    configured = _configured_image()
    if not CANONICAL_IMAGE_RE.fullmatch(configured):
        raise FenceError("previous configured image is not an immutable digest")
    repository = configured.partition("@")[0]
    identities = {
        host.image_identity(str(info.get("Image", "")), repository)
        for info in inspections.values()
    }
    if len(identities) != 1:
        raise FenceError("old writer fleet does not share one exact image")
    image_ref, revision = next(iter(identities))
    if image_ref != configured:
        raise FenceError("old configured and running image digests disagree")
    return image_ref, revision


def _restored_recovery_handoff(
    state: Mapping[str, Any] | None,
    inspections: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Bind a restored recovery generation to the next preflight."""

    if not state:
        return None
    project = str(state.get("recovery_project_name", ""))
    recovery_run_id = str(state.get("recovery_run_id", ""))
    raw_recorded = state.get("recovery_container_ids")
    has_recovery_marker = any(
        key in state
        for key in (
            "recovery_project_name",
            "recovery_run_id",
            "recovery_container_ids",
        )
    )
    if not has_recovery_marker:
        return None
    if not isinstance(raw_recorded, Mapping):
        raise FenceError("restored recovery provenance is incomplete")
    recorded = {
        str(name): str(identity)
        for name, identity in raw_recorded.items()
    }
    if (
        not project
        or not recovery_run_id
        or project != _recovery_project_name(recovery_run_id)
        or set(recorded) != set(EXPECTED_CONTAINERS)
    ):
        raise FenceError("restored recovery provenance is incomplete")
    actual = {
        name: str(info.get("Id", ""))
        for name, info in inspections.items()
    }
    projects = {
        str(
            (info.get("Config", {}).get("Labels", {}) or {}).get(
                "com.docker.compose.project",
                "",
            )
        )
        for info in inspections.values()
    }
    if actual != recorded or projects != {project}:
        raise FenceError("restored recovery provenance disagrees with live fleet")
    return {
        "source_run_id": str(state.get("source_run_id", "")),
        "recovery_run_id": recovery_run_id,
        "project_name": project,
        "container_ids": recorded,
        "removal_phase": "pending",
    }


def _assert_sidecar_image(name: str, info: Mapping[str, Any]) -> None:
    config = info.get("Config", {}) or {}
    if config.get("Image") != CANONICAL_SIDECAR_IMAGES.get(name):
        raise FenceError(f"sidecar image is not canonical: {name}")


def _assert_sidecar_nonwriter(
    name: str,
    info: Mapping[str, Any],
    volume_dir: Path,
) -> None:
    _assert_sidecar_image(name, info)
    raw_mounts = info.get("Mounts", []) or []
    if not isinstance(raw_mounts, list) or any(
        not isinstance(mount, Mapping) for mount in raw_mounts
    ):
        raise FenceError(f"sidecar mount posture is not canonical: {name}")
    mounts = list(raw_mounts)
    volume_source = posixpath.normpath(str(volume_dir))
    if any(
        mount.get("Type") != "bind"
        or bool(mount.get("Name"))
        or mount.get("Destination") == "/data"
        or posixpath.normpath(str(mount.get("Source", ""))) == volume_source
        or mount.get("RW") is not False
        for mount in mounts
    ):
        raise FenceError(f"sidecar is not a non-writer: {name}")
    actual_mounts = [
        (
            str(mount.get("Source", "")),
            str(mount.get("Destination", "")),
        )
        for mount in mounts
    ]
    expected_mounts = [
        (source, destination)
        for source, destination in CANONICAL_SIDECAR_MOUNTS.get(name, ())
    ]
    if len(actual_mounts) != len(expected_mounts) or set(actual_mounts) != set(
        expected_mounts
    ):
        raise FenceError(f"sidecar mount posture is not canonical: {name}")


def _sidecar_project_class(
    project: str,
    state: Mapping[str, Any],
) -> str:
    """Reduce an observed Compose project to a fixed, non-secret class."""

    if not project:
        return "missing"
    if project == CANONICAL_COMPOSE_PROJECT:
        return "current-canonical"
    if project == "workflow":
        return "legacy-workflow"
    if project == "deploy":
        return "legacy-deploy"
    if project == str(state.get("recovery_project_name", "")):
        return "recorded-recovery"
    if project in _audited_full_compose_recovery_projects():
        return "audited-full-compose-recovery"
    if RECOVERY_PROJECT_RE.fullmatch(project):
        return "unrecorded-recovery"
    return "other"


def _audited_full_compose_recovery_projects() -> frozenset[str]:
    """Projects created by public recovery attempts before writer isolation."""

    return frozenset(
        "tinyassets-recovery-"
        + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
        for run_id in AUDITED_FULL_COMPOSE_RECOVERY_RUN_IDS
    )


def _restored_sidecar_inspections(
    host: Host,
    state: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], str]:
    """Bind present fixed-name sidecars to canonical or recorded recovery ownership."""

    recovery_ids = {
        str(name): str(identity)
        for name, identity in dict(
            state.get("recovery_sidecar_container_ids") or {}
        ).items()
    }
    if recovery_ids and set(recovery_ids) != {
        name for name, _service in CANONICAL_SIDECARS
    }:
        raise FenceError("restored recovery sidecar provenance is incomplete")
    expected_project = (
        str(state.get("recovery_project_name", ""))
        if recovery_ids
        else CANONICAL_COMPOSE_PROJECT
    )
    if not expected_project:
        raise FenceError("restored recovery sidecar project is missing")
    allowed_projects = {expected_project}
    if not recovery_ids:
        allowed_projects.update(_audited_full_compose_recovery_projects())

    inspections: dict[str, dict[str, Any]] = {}
    observed_sidecar_project = ""
    for name, service in CANONICAL_SIDECARS:
        if _named_container_absent_exact(host, name):
            if name in recovery_ids:
                raise FenceError(f"recorded recovery sidecar is absent: {name}")
            continue
        info = host.container_info(name)
        identity = str(info.get("Id", ""))
        labels = info.get("Config", {}).get("Labels", {}) or {}
        if not identity:
            raise FenceError(f"restored sidecar identity is missing: {name}")
        observed_project = str(
            labels.get("com.docker.compose.project", "")
        )
        if observed_project not in allowed_projects:
            project_class = _sidecar_project_class(observed_project, state)
            raise FenceError(
                f"restored sidecar project {project_class} is invalid: {name}"
            )
        if (
            observed_sidecar_project
            and observed_project != observed_sidecar_project
        ):
            raise FenceError("restored sidecar projects differ")
        observed_sidecar_project = observed_project
        if labels.get("com.docker.compose.service") != service:
            raise FenceError(f"restored sidecar service is invalid: {name}")
        if recovery_ids and recovery_ids.get(name) != identity:
            raise FenceError(
                f"restored sidecar recorded identity changed: {name}"
            )
        try:
            _assert_sidecar_nonwriter(name, info, host.volume_dir())
        except FenceError:
            raise FenceError(
                f"restored sidecar non-writer proof failed: {name}"
            ) from None
        inspections[name] = info
    if recovery_ids and set(inspections) != set(recovery_ids):
        raise FenceError("restored recovery sidecar inventory changed")
    return inspections, observed_sidecar_project or expected_project


def _sidecar_handoff(
    inspections: Mapping[str, Mapping[str, Any]],
    *,
    project_name: str,
) -> dict[str, Any]:
    return {
        "container_ids": {
            name: str(info.get("Id", ""))
            for name, info in inspections.items()
        },
        "project_name": project_name,
        "removal_phase": "pending",
    }


def preflight(
    host: Host,
    *,
    image_ref: str,
    target_revision: str,
    run_id: str,
    state_path: Path,
) -> dict[str, Any]:
    _require_run_id(run_id)
    restored_state: dict[str, Any] | None = None
    if state_path.is_file():
        restored_state = _load_state(state_path)
        if restored_state.get("phase") != "restored" or _masked_units(host):
            raise FenceError("an earlier stop-writer fence requires reconciliation")
    if not CANONICAL_IMAGE_RE.fullmatch(image_ref):
        raise FenceError("target image is not an immutable digest")
    if not REVISION_RE.fullmatch(target_revision):
        raise FenceError("target revision is not canonical")
    repository = image_ref.partition("@")[0]
    pulled_ref, pulled_revision = host.image_identity(image_ref, repository)
    if (pulled_ref, pulled_revision) != (image_ref, target_revision):
        raise FenceError("pulled target image identity disagrees")

    inspections = _exact_inspections(host)
    for name, info in inspections.items():
        if not info.get("State", {}).get("Running"):
            raise FenceError(f"old expected container is not running: {name}")
    volume_dir = host.volume_dir()
    receipt = resolve_receipt_store(inspections, volume_dir)
    volume_names = set(host.volume_container_names())
    if not set(EXPECTED_CONTAINERS) <= volume_names:
        raise FenceError("expected container is absent from the production volume")
    extra_names = tuple(sorted(volume_names - set(EXPECTED_CONTAINERS)))
    extra_inspections = {
        name: host.container_info(name) for name in extra_names
    }
    extra_consumers = {
        name: {
            "id": str(info.get("Id", "")),
            "running": bool(info.get("State", {}).get("Running")),
            "restart_policy": str(
                info.get("HostConfig", {}).get("RestartPolicy", {}).get("Name", "")
            ),
        }
        for name, info in extra_inspections.items()
    }
    old_image_ref, old_revision = _old_identity(host, inspections)
    old_ids = {name: str(info.get("Id", "")) for name, info in inspections.items()}
    recovery_handoff = _restored_recovery_handoff(
        restored_state,
        inspections,
    )
    sidecar_inspections: dict[str, dict[str, Any]] = {}
    sidecar_project = ""
    if recovery_handoff and restored_state is not None:
        sidecar_inspections, sidecar_project = _restored_sidecar_inspections(
            host,
            restored_state,
        )
    controlled_identity_values = (
        *(info.get("Id") for info in inspections.values()),
        *(info.get("Id") for info in extra_inspections.values()),
        *(info.get("Id") for info in sidecar_inspections.values()),
    )
    if any(
        not isinstance(identity, str) or not identity.strip()
        for identity in controlled_identity_values
    ):
        raise FenceError("controlled container identity is unavailable")
    controlled_identities = tuple(controlled_identity_values)
    controlled_pids = host.container_pids(controlled_identities)
    preliminary_risk = inventory_queue_risk(volume_dir)
    preliminary_processes = _stray_writer_processes(
        receipt.host_path,
        controlled_pids,
        volume_dir,
    )
    preliminary_processes = _confirm_stray_writer_processes(
        host,
        preliminary_processes,
        controlled_identities,
    )
    preliminary_snapshot = receipt_snapshot(receipt.host_path)
    if preliminary_risk:
        raise FenceError("pre-mutation bug_investigation queue risk is nonzero")
    if preliminary_processes:
        raise FenceError("pre-mutation stray writer process risk is nonzero")
    present_racers = tuple(
        unit for unit in RESTART_RACER_UNITS if host.unit_present(unit)
    )
    if not host.unit_present(DAEMON_SERVICE):
        raise FenceError(f"required production unit is missing: {DAEMON_SERVICE}")
    stable_unit_states = _wait_units_stable_snapshot(
        host,
        (*present_racers, DAEMON_SERVICE),
    )

    state: dict[str, Any] = {
        "schema_version": 1,
        "owner": TASK_OWNER,
        "run_id": run_id,
        "phase": "fencing_planned",
        "target_image_ref": image_ref,
        "target_revision": target_revision,
        "previous_image_ref": old_image_ref,
        "previous_revision": old_revision,
        "volume_mountpoint": str(volume_dir),
        "receipt_container_path": receipt.container_path,
        "receipt_host_path": str(receipt.host_path),
        "old_container_ids": old_ids,
        "restart_racer_state": {
            unit: stable_unit_states[unit] for unit in present_racers
        },
        "daemon_service_state": stable_unit_states[DAEMON_SERVICE],
        "old_restart_policies": {
            name: str(info.get("HostConfig", {}).get("RestartPolicy", {}).get("Name", ""))
            for name, info in inspections.items()
        },
        "sidecar_restart_policies": {
            name: str(info.get("HostConfig", {}).get("RestartPolicy", {}).get("Name", ""))
            for name, info in sidecar_inspections.items()
        },
        "preliminary_receipt_snapshot": preliminary_snapshot,
        "present_restart_racer_units": present_racers,
        "extra_volume_consumers": extra_consumers,
        "fence_progress": {
            "restart_policy_proved": False,
            "boot_activators_disabled": False,
        },
    }
    if recovery_handoff:
        state["recovery_handoff"] = recovery_handoff
        state["sidecar_handoff"] = _sidecar_handoff(
            sidecar_inspections,
            project_name=sidecar_project,
        )

    # Write-ahead invariant: canonical current-run state is durable before the
    # first mutation. Every later failure is therefore visible to guards and
    # cleanup. The host operation lock keeps cleanup behind this full command.
    _atomic_json(state_path, state)
    consumers = {**inspections, **extra_inspections, **sidecar_inspections}
    state["restart_policy_proof"] = _set_restart_no(host, consumers)
    state["fence_progress"]["restart_policy_proved"] = True
    _atomic_json(state_path, state)
    _apply_boot_fence(host, present_racers)
    state["fence_progress"]["boot_activators_disabled"] = True
    state["phase"] = "fencing"
    _atomic_json(state_path, state)

    stopped_racers = _stop_and_mask_writer_units(
        host,
        boot_fence_applied=True,
    )
    if stopped_racers != present_racers:
        raise FenceError("restart racer inventory changed during quiescence")
    host.run(
        [
            "docker",
            "stop",
            *old_ids.values(),
            *(str(info.get("Id", "")) for info in extra_inspections.values()),
            *(
                str(info.get("Id", ""))
                for info in sidecar_inspections.values()
            ),
        ],
        check=False,
    )

    state["phase"] = "quiesced"
    _atomic_json(state_path, state)
    old_still_running = []
    for name, old_id in old_ids.items():
        if _container_running_exact(host, old_id):
            old_still_running.append({"container": name, "id": old_id})
    final_risk = inventory_queue_risk(volume_dir)
    extra_still_running = [
        name
        for name, extra in extra_consumers.items()
        if _container_running_exact(host, str(extra.get("id", "")))
    ]
    sidecars_still_running = [
        name
        for name, info in sidecar_inspections.items()
        if _container_running_exact(host, str(info.get("Id", "")))
    ]
    final_processes = _stray_writer_processes(receipt.host_path, set(), volume_dir)
    final_snapshot = receipt_snapshot(receipt.host_path)
    if old_still_running:
        raise FenceError("old container still running after quiescence")
    if extra_still_running:
        raise FenceError("extra production-volume consumer survived quiescence")
    if sidecars_still_running:
        raise FenceError("restored sidecar survived quiescence")
    for name, info in sidecar_inspections.items():
        captured_id = str(info.get("Id", ""))
        if _named_container_absent_exact(host, name):
            raise FenceError(
                f"restored sidecar recorded identity changed: {name}"
            )
        current = host.container_info(name)
        if str(current.get("Id", "")) != captured_id:
            raise FenceError(
                f"restored sidecar recorded identity changed: {name}"
            )
    if final_risk:
        raise FenceError("post-quiesce bug_investigation queue risk is nonzero")
    if final_processes:
        raise FenceError("post-quiesce stray writer process risk is nonzero")
    if final_snapshot != preliminary_snapshot:
        raise FenceError("receipt snapshot changed during writer quiescence")
    state["receipt_snapshot"] = final_snapshot
    if extra_consumers:
        state["phase"] = "unsafe_fenced"
        _atomic_json(state_path, state)
        raise FenceError(
            "extra production-volume consumer was fenced; refusing deployment"
        )
    state["phase"] = "preflight_proved"
    _atomic_json(state_path, state)
    return {
        "schema_version": 1,
        "owner": TASK_OWNER,
        "phase": state["phase"],
        "safe": True,
        "target_image_ref": image_ref,
        "target_revision": target_revision,
        "previous_image_ref": old_image_ref,
        "previous_revision": old_revision,
        "old_container_ids": old_ids,
        "receipt_container_path": receipt.container_path,
        "receipt_host_path": str(receipt.host_path),
        "preliminary_queue_risk": preliminary_risk,
        "final_queue_risk": final_risk,
        "stray_writer_processes": final_processes,
        "receipt_snapshot": final_snapshot,
        "restart_racer_state": state["restart_racer_state"],
    }


def _remove_restored_recovery_handoff(
    host: Host,
    state: dict[str, Any],
    *,
    state_path: Path,
) -> dict[str, str]:
    """Remove only the exact stopped recovery fleet bound by this preflight."""

    if "recovery_handoff" not in state:
        return {}
    raw_handoff = state["recovery_handoff"]
    if not isinstance(raw_handoff, dict) or not raw_handoff:
        raise FenceError("recovery handoff provenance is invalid")
    project = str(raw_handoff.get("project_name", ""))
    recovery_run_id = str(raw_handoff.get("recovery_run_id", ""))
    raw_recorded = raw_handoff.get("container_ids")
    if not isinstance(raw_recorded, Mapping):
        raise FenceError("recovery handoff provenance is invalid")
    recorded = {
        str(name): str(identity)
        for name, identity in raw_recorded.items()
    }
    phase = str(raw_handoff.get("removal_phase", ""))
    if (
        not project
        or not recovery_run_id
        or project != _recovery_project_name(recovery_run_id)
        or set(recorded) != set(EXPECTED_CONTAINERS)
        or phase not in {"pending", "planned", "removed"}
    ):
        raise FenceError("recovery handoff provenance is invalid")

    names = set(host.volume_container_names())
    if not names:
        if phase not in {"planned", "removed"}:
            raise FenceError("recovery handoff fleet disappeared before removal intent")
        if phase == "planned":
            for name in EXPECTED_CONTAINERS:
                if not _container_absent_exact(host, recorded[name]):
                    raise FenceError(
                        f"removed recovery handoff writer still exists: {name}"
                    )
                if not _named_container_absent_exact(host, name):
                    raise FenceError(
                        f"recovery handoff writer name was substituted: {name}"
                    )
        if phase != "removed":
            raw_handoff["removal_phase"] = "removed"
            raw_handoff["removed_container_ids"] = dict(recorded)
            _atomic_json(state_path, state)
        return recorded
    expected_names = set(EXPECTED_CONTAINERS)
    if names - expected_names:
        raise FenceError("recovery handoff fleet has extra writers")
    if phase == "pending" and names != expected_names:
        raise FenceError("recovery handoff fleet changed before removal intent")
    if phase == "removed":
        raise FenceError("removed recovery handoff fleet unexpectedly reappeared")

    inspections = {
        name: host.container_info(name)
        for name in EXPECTED_CONTAINERS
        if name in names
    }
    actual = {
        name: str(info.get("Id", ""))
        for name, info in inspections.items()
    }
    if actual != {name: recorded[name] for name in names}:
        raise FenceError("recovery handoff container identities changed")
    for name in expected_names - names:
        if not _container_absent_exact(host, recorded[name]):
            raise FenceError(
                f"removed recovery handoff writer still exists: {name}"
            )
        if not _named_container_absent_exact(host, name):
            raise FenceError(
                f"recovery handoff writer name was substituted: {name}"
            )
    for name, info in inspections.items():
        labels = info.get("Config", {}).get("Labels", {}) or {}
        if labels.get("com.docker.compose.project") != project:
            raise FenceError(
                f"recovery handoff writer belongs to another project: {name}"
            )
        if info.get("State", {}).get("Running"):
            raise FenceError(f"recovery handoff writer is still running: {name}")
        if host.container_restart_policy(recorded[name]) != "no":
            raise FenceError("recovery handoff writer restart policy is not no")

    if phase == "pending":
        raw_handoff["removal_phase"] = "planned"
        _atomic_json(state_path, state)
    host.run(
        [
            "docker",
            "rm",
            *(recorded[name] for name in EXPECTED_CONTAINERS if name in names),
        ]
    )
    if host.volume_container_names():
        raise FenceError("recovery handoff fleet removal did not converge")
    raw_handoff["removal_phase"] = "removed"
    raw_handoff["removed_container_ids"] = dict(recorded)
    _atomic_json(state_path, state)
    return recorded


def _sidecar_handoff_survivors(
    host: Host,
    state: Mapping[str, Any],
) -> dict[str, str]:
    """Prove the exact recorded sidecar subset without mutating it."""

    if "sidecar_handoff" not in state:
        return {}
    raw_handoff = state["sidecar_handoff"]
    if not isinstance(raw_handoff, Mapping):
        raise FenceError("sidecar handoff is invalid")
    raw_recorded = raw_handoff.get("container_ids")
    project = str(raw_handoff.get("project_name", ""))
    phase = str(raw_handoff.get("removal_phase", ""))
    if (
        not isinstance(raw_recorded, Mapping)
        or not project
        or phase not in {"pending", "planned", "removed"}
    ):
        raise FenceError("sidecar handoff is invalid")
    recorded = {
        str(name): str(identity)
        for name, identity in raw_recorded.items()
    }
    expected = dict(CANONICAL_SIDECARS)
    if set(recorded) - set(expected) or any(
        not identity for identity in recorded.values()
    ):
        raise FenceError("sidecar handoff is invalid")

    survivors: dict[str, str] = {}
    for name, service in CANONICAL_SIDECARS:
        recorded_id = recorded.get(name)
        name_absent = _named_container_absent_exact(host, name)
        if recorded_id is None:
            if not name_absent:
                raise FenceError(f"unexpected sidecar appeared: {name}")
            continue
        exact_absent = _container_absent_exact(host, recorded_id)
        if exact_absent:
            if phase == "pending":
                raise FenceError(
                    f"sidecar disappeared before removal intent: {name}"
                )
            if not name_absent:
                raise FenceError(f"sidecar identity was substituted: {name}")
            continue
        if name_absent:
            raise FenceError(f"sidecar name changed: {name}")
        info = host.container_info(name)
        _assert_sidecar_nonwriter(name, info, host.volume_dir())
        labels = info.get("Config", {}).get("Labels", {}) or {}
        if (
            str(info.get("Id", "")) != recorded_id
            or labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != service
        ):
            raise FenceError(f"sidecar identity drifted: {name}")
        if info.get("State", {}).get("Running"):
            raise FenceError(f"sidecar is still running: {name}")
        if host.container_restart_policy(recorded_id) != "no":
            raise FenceError(f"sidecar restart fence drifted: {name}")
        survivors[name] = recorded_id

    if phase == "removed" and survivors:
        raise FenceError("removed sidecar unexpectedly reappeared")
    return survivors


def _remove_sidecar_handoff(
    host: Host,
    state: dict[str, Any],
    *,
    state_path: Path,
) -> dict[str, str]:
    """Remove only exact stopped sidecars after durable removal intent."""

    if "sidecar_handoff" not in state:
        return {}
    raw_handoff = state["sidecar_handoff"]
    if not isinstance(raw_handoff, dict):
        raise FenceError("sidecar handoff is invalid")
    raw_recorded = raw_handoff.get("container_ids")
    if not isinstance(raw_recorded, Mapping):
        raise FenceError("sidecar handoff is invalid")
    recorded = {
        str(name): str(identity)
        for name, identity in raw_recorded.items()
    }
    survivors = _sidecar_handoff_survivors(host, state)
    if raw_handoff.get("removal_phase") == "pending":
        raw_handoff["removal_phase"] = "planned"
        _atomic_json(state_path, state)
    if survivors:
        host.run(
            [
                "docker",
                "rm",
                *(
                    survivors[name]
                    for name, _service in CANONICAL_SIDECARS
                    if name in survivors
                ),
            ]
        )
    for name, _service in CANONICAL_SIDECARS:
        if not _named_container_absent_exact(host, name):
            raise FenceError(f"sidecar removal did not converge: {name}")
    for name, identity in recorded.items():
        if not _container_absent_exact(host, identity):
            raise FenceError(f"sidecar ID survived removal: {name}")
    raw_handoff["removal_phase"] = "removed"
    raw_handoff["removed_container_ids"] = dict(recorded)
    _atomic_json(state_path, state)
    return recorded


def prepare_deploy(
    host: Host,
    *,
    image_ref: str,
    run_id: str,
    state_path: Path,
) -> dict[str, Any]:
    state = _load_state(state_path)
    _require_state_run(state, run_id)
    if state.get("phase") != "preflight_proved":
        raise FenceError("stop-writer preflight is not proved")
    if image_ref != state.get("target_image_ref") or _configured_image() != image_ref:
        raise FenceError("atomically installed target image does not match fence")
    volume_dir = Path(str(state["volume_mountpoint"]))
    if inventory_queue_risk(volume_dir):
        raise FenceError("queue risk appeared before target start")
    receipt_path = Path(str(state["receipt_host_path"]))
    if receipt_snapshot(receipt_path) != state.get("receipt_snapshot"):
        raise FenceError("receipt snapshot changed before target start")
    for old_id in state["old_container_ids"].values():
        if _container_running_exact(host, old_id):
            raise FenceError("old container restarted before target start")
    _sidecar_handoff_survivors(host, state)
    removed_recovery_ids = _remove_restored_recovery_handoff(
        host,
        state,
        state_path=state_path,
    )
    removed_sidecar_ids = _remove_sidecar_handoff(
        host,
        state,
        state_path=state_path,
    )
    host.run(["systemctl", "unmask", "--runtime", DAEMON_SERVICE])
    state["phase"] = "target_installed"
    _atomic_json(state_path, state)
    return {
        "owner": TASK_OWNER,
        "phase": state["phase"],
        "safe": True,
        "removed_recovery_container_ids": removed_recovery_ids,
        "removed_sidecar_container_ids": removed_sidecar_ids,
    }


def prove(
    host: Host,
    *,
    image_ref: str,
    revision: str,
    run_id: str,
    state_path: Path,
) -> dict[str, Any]:
    state = _load_state(state_path)
    _require_state_run(state, run_id)
    if state.get("phase") not in {
        "target_installed",
        "safe_fleet",
        "post_canary_proved",
        "recovery_pending_canary",
        "canary_accepted",
        "finalizing",
        "restored",
    }:
        raise FenceError("target was not prepared under the stop-writer fence")
    admitted_identities = {
        (
            str(state.get("target_image_ref", "")),
            str(state.get("target_revision", "")),
        ),
        (
            str(state.get("previous_image_ref", "")),
            str(state.get("previous_revision", "")),
        ),
    }
    if (image_ref, revision) not in admitted_identities:
        raise FenceError("active image identity is not admitted by durable fence state")
    observation = observe_fleet(host, expected_image_ref=image_ref)
    old_ids = state.get("old_container_ids", {})
    old_running = []
    for name, old_id in old_ids.items():
        if _container_running_exact(host, old_id):
            old_running.append({"container": name, "id": old_id})
    observation["old_container_ids_running"] = old_running
    if not safe_fleet_matches(observation, image_ref, revision, old_ids):
        state["last_failed_observation"] = observation
        _atomic_json(state_path, state)
        raise FenceError(
            "the expected safe target containers were not independently proved"
        )
    if observation["receipt_snapshot"] != state.get("receipt_snapshot"):
        raise FenceError("post-deploy receipt snapshot mismatch")
    if state.get("phase") not in {
        "post_canary_proved",
        "restored",
        "recovery_pending_canary",
        "canary_accepted",
        "finalizing",
    }:
        state["phase"] = "safe_fleet"
    _atomic_json(state_path, state)
    return {
        "schema_version": 1,
        "owner": TASK_OWNER,
        "phase": state["phase"],
        "safe": True,
        "target_image_ref": image_ref,
        "target_revision": revision,
        "old_container_ids": old_ids,
        "observation": observation,
    }


def post_canary(
    host: Host,
    *,
    image_ref: str,
    revision: str,
    run_id: str,
    state_path: Path,
) -> dict[str, Any]:
    evidence: dict[str, Any] | None = None
    last_error = ""
    for attempt in range(10):
        try:
            evidence = prove(
                host,
                image_ref=image_ref,
                revision=revision,
                run_id=run_id,
                state_path=state_path,
            )
            break
        except FenceError as exc:
            last_error = str(exc)
            if attempt < 9:
                time.sleep(2)
    if evidence is None:
        state = _load_state(state_path)
        diagnostic = state.get("last_failed_observation", {})
        raise FenceError(
            "post-canary exact fleet proof did not converge: "
            f"{last_error}; diagnostic={json.dumps(diagnostic, sort_keys=True)}"
        )
    evidence["phase"] = "post_canary_proved"
    state = _load_state(state_path)
    state["phase"] = "post_canary_proved"
    _atomic_json(state_path, state)
    return evidence


def _masked_units(host: Host) -> list[str]:
    units = (*RESTART_RACER_UNITS, DAEMON_SERVICE)
    return [
        unit
        for unit in units
        if host.unit_present(unit)
        and (
            host.unit_state(unit)["enabled"] in {"masked", "masked-runtime"}
            or host.unit_load_state(unit) == "masked"
        )
    ]


def fence_status(
    host: Host,
    *,
    run_id: str,
    state_path: Path,
) -> dict[str, Any]:
    _require_run_id(run_id)
    state: dict[str, Any] | None = None
    state_error = ""
    if state_path.is_file():
        try:
            state = _load_state(state_path)
        except FenceError as exc:
            state_error = str(exc)
    current_run_matches = bool(state and state.get("run_id") == run_id)
    return {
        "owner": TASK_OWNER,
        "state_exists": state_path.is_file(),
        "state_phase": state.get("phase") if state else None,
        "state_run_id": state.get("run_id") if state else None,
        "current_run_matches": current_run_matches,
        "current_run_cutover_started": bool(
            state
            and state.get("run_id") == run_id
            and state.get("phase") not in {"restored"}
        ),
        "state_error": state_error,
        "masked_units": _masked_units(host),
        "current_run_previous_image_ref": (
            state.get("previous_image_ref", "") if current_run_matches else ""
        ),
        "current_run_previous_revision": (
            state.get("previous_revision", "") if current_run_matches else ""
        ),
    }


def _echo_guarded_output(stdout: str, stderr: str) -> None:
    """Put a guarded mutation's own output in the workflow log.

    `Host.run` captures the wrapped command, so the only trace a guarded mutation
    left was `output_present: true` in this script's JSON verdict. On 2026-09-26
    `Install host services` reported success in five seconds having printed not one
    `[host-uptime-install]` line: no "converged N timers", no "already current", and
    none of the installer's warnings. Every workflow that wraps a host mutation in
    `guard-host-mutation` was equally blind, so "the installer did X in production"
    was not a claim any of their logs could support.

    Written to STDERR on purpose. This script's stdout is one JSON line that callers
    parse; interleaving a command's output into it would make that unparseable the
    first time an installer printed a brace. Both streams land in the same workflow
    log, which is where the visibility was wanted.
    """
    for label, text in (("stdout", stdout), ("stderr", stderr)):
        body = (text or "").strip()
        if not body:
            continue
        print(f"[fence] guarded mutation {label}:", file=sys.stderr)
        for line in body.splitlines():
            print(f"[fence]   {line}", file=sys.stderr)


def guard_host_mutation(host: Host, *, state_path: Path) -> dict[str, Any]:
    """Reject host mutation when canonical state or fence residue is unsafe."""

    if state_path.exists():
        state = _load_state(state_path)
        if state.get("phase") != "restored":
            raise FenceError(
                f"production cutover fence is {state.get('phase')!r}"
            )
        return {
            "owner": TASK_OWNER,
            "guard": "restored_state",
            "safe": True,
        }

    residue: list[dict[str, str]] = []
    for unit in (*RESTART_RACER_UNITS, DAEMON_SERVICE):
        if not host.unit_present(unit):
            continue
        state = _validated_unit_state(host, unit)
        enabled = state["enabled"]
        if enabled in {"masked", "masked-runtime"} or (
            enabled == "disabled"
            and (unit == DAEMON_SERVICE or unit.endswith(".timer"))
        ):
            residue.append(
                {"kind": "unit", "name": unit, "value": enabled}
            )
    for name in host.volume_container_names():
        info = host.container_info(name)
        identity = str(info.get("Id", ""))
        policy = host.container_restart_policy(identity)
        if policy == "no":
            residue.append(
                {"kind": "container", "name": name, "value": "restart=no"}
            )
    if residue:
        raise FenceError(f"stop-writer fence residue is present: {residue}")
    return {"owner": TASK_OWNER, "guard": "clean_absence", "safe": True}


def _archive_corrupt_state(state_path: Path) -> Path | None:
    if not state_path.exists():
        return None
    archive = state_path.with_name(
        f"{state_path.name}.corrupt-{time.time_ns()}"
    )
    shutil.copyfile(state_path, archive)
    os.chmod(archive, 0o600)
    with archive.open("r+b") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_parent(state_path)
    return archive


def quiesce_unsafe(
    host: Host,
    *,
    run_id: str,
    state_path: Path,
) -> dict[str, Any]:
    """Idempotently stop every controlled writer while leaving the fence closed."""

    _require_run_id(run_id)
    state_error = ""
    state_valid = True
    try:
        state = _load_state(state_path)
    except FenceError as exc:
        state_error = str(exc)
        state_valid = False
        state = {
            "schema_version": 1,
            "owner": TASK_OWNER,
            "run_id": run_id,
            "phase": "emergency_fencing",
            "old_container_ids": {},
            "extra_volume_consumers": {},
        }

    sidecar_refence_error = ""
    try:
        recorded_sidecars = _recorded_recovery_sidecars(host, state)
    except FenceError as exc:
        sidecar_refence_error = str(exc)
        try:
            # The fixed-name occupant is not ours to mutate. A previously
            # captured exact ID may still exist under another name, however,
            # and remains safe to restart-fence and stop without removal.
            recorded_sidecars = _recorded_recovery_sidecars_by_id(host, state)
        except FenceError as id_exc:
            sidecar_refence_error = (
                f"{sidecar_refence_error}; exact-ID refence failed: {id_exc}"
            )
            recorded_sidecars = {}
    if recorded_sidecars:
        try:
            state["recovery_sidecar_refence_proof"] = _set_restart_no(
                host,
                recorded_sidecars,
            )
            _atomic_json(state_path, state)
            host.run(
                [
                    "docker",
                    "stop",
                    *(
                        str(info.get("Id", ""))
                        for info in recorded_sidecars.values()
                    ),
                ],
                check=False,
            )
            for name, info in recorded_sidecars.items():
                if _container_running_exact(host, str(info.get("Id", ""))):
                    raise FenceError(f"recovery sidecar did not stop: {name}")
        except (FenceError, OSError) as exc:
            detail = f"sidecar refence failed: {exc}"
            sidecar_refence_error = "; ".join(
                value for value in (sidecar_refence_error, detail) if value
            )

    volume_dir = host.volume_dir()
    current_volume_names = set(host.volume_container_names())
    current_inspections = {
        name: host.container_info(name) for name in sorted(current_volume_names)
    }
    receipt_resolution_error = ""
    receipt: ReceiptStore | None = None
    if state.get("receipt_host_path") and state.get("volume_mountpoint"):
        receipt = ReceiptStore(
            container_path=str(state.get("receipt_container_path", "")),
            host_path=Path(str(state["receipt_host_path"])),
        )
        volume_dir = Path(str(state["volume_mountpoint"]))
    else:
        try:
            expected_inspections = {
                name: current_inspections[name] for name in EXPECTED_CONTAINERS
            }
            receipt = resolve_receipt_store(expected_inspections, volume_dir)
            state["receipt_container_path"] = receipt.container_path
            state["receipt_host_path"] = str(receipt.host_path)
            state["volume_mountpoint"] = str(volume_dir)
        except (FenceError, KeyError) as exc:
            receipt_resolution_error = (
                str(exc)
                if isinstance(exc, FenceError)
                else "expected writer is unavailable for receipt resolution"
            )

    recorded_extras = dict(state.get("extra_volume_consumers", {}))
    extra_names = tuple(
        sorted(
            (current_volume_names - set(EXPECTED_CONTAINERS))
            | set(recorded_extras)
        )
    )
    for name in extra_names:
        info = current_inspections.get(name)
        if info is not None:
            recorded_extras[name] = {
                "id": str(info.get("Id", "")),
                "running": bool(info.get("State", {}).get("Running")),
                "restart_policy": str(
                    info.get("HostConfig", {})
                    .get("RestartPolicy", {})
                    .get("Name", "")
                ),
            }
        elif name not in recorded_extras:
            recorded_extras[name] = {"inspection_error": True}
    state["extra_volume_consumers"] = recorded_extras
    state["recovery_sidecar_refence_error"] = sidecar_refence_error
    state["source_run_id"] = state.get("source_run_id") or state.get("run_id")
    state["run_id"] = run_id
    state["recovery_run_id"] = run_id
    state["phase"] = "emergency_fencing_planned"
    state["emergency_fence_progress"] = {
        "restart_policy_proved": False,
        "boot_activators_disabled": False,
    }
    archived_state = None
    if not state_valid:
        archived_state = _archive_corrupt_state(state_path)
    state["archived_corrupt_state"] = (
        str(archived_state) if archived_state else ""
    )
    _atomic_json(state_path, state)

    restart_policy_proof = _set_restart_no(host, current_inspections)
    state["restart_policy_proof"] = restart_policy_proof
    state["emergency_fence_progress"]["restart_policy_proved"] = True
    _atomic_json(state_path, state)
    present_racers = tuple(
        unit for unit in RESTART_RACER_UNITS if host.unit_present(unit)
    )
    if not host.unit_present(DAEMON_SERVICE):
        raise FenceError(f"required production unit is missing: {DAEMON_SERVICE}")
    _apply_boot_fence(host, present_racers)
    state["emergency_fence_progress"]["boot_activators_disabled"] = True
    state["phase"] = "emergency_fencing"
    _atomic_json(state_path, state)

    stopped_racers = _stop_and_mask_writer_units(
        host,
        boot_fence_applied=True,
    )
    if stopped_racers != present_racers:
        raise FenceError("restart racer inventory changed during emergency fence")
    controlled_ids = {
        name: str(info.get("Id", ""))
        for name, info in current_inspections.items()
        if str(info.get("Id", ""))
    }
    for name, recorded in recorded_extras.items():
        identity = str(recorded.get("id", ""))
        if identity:
            controlled_ids.setdefault(name, identity)
    host.run(
        ["docker", "stop", *sorted(set(controlled_ids.values()))],
        check=False,
    )

    names_still_running = [
        name
        for name, identity in controlled_ids.items()
        if _container_running_exact(host, identity)
    ]
    post_volume_names = set(host.volume_container_names())
    identity_drift = []
    for name in sorted(post_volume_names):
        info = host.container_info(name)
        if str(info.get("Id", "")) != controlled_ids.get(name):
            identity_drift.append(name)
    old_running = [
        {"container": name, "id": old_id}
        for name, old_id in state.get("old_container_ids", {}).items()
        if _container_running_exact(host, old_id)
    ]
    process_risk: list[dict[str, Any]] = []
    process_error = ""
    if receipt is not None:
        try:
            process_risk = _stray_writer_processes(
                receipt.host_path,
                set(),
                volume_dir,
            )
        except FenceError as exc:
            process_error = str(exc)
    if (
        names_still_running
        or identity_drift
        or old_running
        or process_risk
        or receipt_resolution_error
        or process_error
    ):
        state["phase"] = "unsafe_fence_unproved"
        state["receipt_resolution_error"] = receipt_resolution_error
        state["process_scan_error"] = process_error
        state["emergency_identity_drift"] = identity_drift
        _atomic_json(state_path, state)
        raise FenceError(
            "unsafe writer cleanup could not prove all controlled writers stopped"
        )
    state["phase"] = "unsafe_fenced"
    _atomic_json(state_path, state)
    return {
        "schema_version": 1,
        "owner": TASK_OWNER,
        "phase": "unsafe_fenced",
        "safe": False,
        "writers_fenced": True,
        "present_restart_racer_units": present_racers,
        "named_containers_running": names_still_running,
        "old_container_ids_running": old_running,
        "stray_writer_processes": process_risk,
        "restart_policy_proof": restart_policy_proof,
        "recovery_sidecar_refence_error": sidecar_refence_error,
        "masked_units": _masked_units(host),
        "source_state_error": state_error,
        "archived_corrupt_state": str(archived_state) if archived_state else "",
        "durable_state_path": str(state_path),
    }


def _validated_unit_state(host: Host, unit: str) -> dict[str, str]:
    state = host.unit_state(unit)
    if state.get("active") not in AUTHORITATIVE_UNIT_ACTIVE_STATES:
        raise FenceError(f"unit active state is not authoritative: {unit}")
    if state.get("enabled") not in AUTHORITATIVE_UNIT_ENABLED_STATES:
        raise FenceError(f"unit enablement is not authoritative: {unit}")
    return state


def _daemon_restore_expectation(
    state: Mapping[str, str],
    *,
    establish_active: bool,
) -> dict[str, str]:
    """Derive daemon intent without changing failed-forward rollback posture."""

    active = state.get("active")
    enabled = state.get("enabled")
    if (
        active not in AUTHORITATIVE_UNIT_ACTIVE_STATES
        or enabled not in AUTHORITATIVE_UNIT_ENABLED_STATES
    ):
        raise FenceError("saved daemon unit state is invalid")
    expected = {"active": active, "enabled": enabled}
    if active == "activating" or (
        establish_active and active in {"active", "inactive", "failed"}
    ):
        expected["active"] = "active"
    return expected


def restore_if_safe(
    host: Host,
    *,
    image_ref: str,
    revision: str,
    run_id: str,
    state_path: Path,
) -> dict[str, Any]:
    _require_run_id(run_id)
    masked_before = _masked_units(host)
    if not state_path.is_file():
        if masked_before:
            raise FenceError(
                "fence state missing while restart units remain masked; "
                "leaving restart racers fenced"
            )
        return {
            "owner": TASK_OWNER,
            "phase": "not_applicable",
            "safe": True,
            "masked_units": [],
        }
    state = _load_state(state_path)
    _require_state_run(state, run_id)
    successful_normal_handoff = (
        state.get("phase") == "post_canary_proved"
        and image_ref == state.get("target_image_ref")
        and revision == state.get("target_revision")
    )
    daemon_state = _daemon_restore_expectation(
        state.get("daemon_service_state", {}),
        establish_active=successful_normal_handoff,
    )
    evidence = prove(
        host,
        image_ref=image_ref,
        revision=revision,
        run_id=run_id,
        state_path=state_path,
    )
    racer_state = state.get("restart_racer_state", {})
    saved_racers = tuple(
        state.get("present_restart_racer_units") or racer_state.keys()
    )
    saved_units = (*saved_racers, DAEMON_SERVICE)
    if any(not host.unit_present(unit) for unit in saved_units):
        raise FenceError("unit restoration proof failed: saved unit is missing")
    host.run(["systemctl", "unmask", "--runtime", *saved_units])
    for unit in saved_racers:
        prior = racer_state.get(unit, {})
        if prior.get("enabled") == "enabled":
            host.run(["systemctl", "enable", unit])
        if prior.get("active") == "active":
            host.run(["systemctl", "start", unit])
    if daemon_state.get("enabled") == "enabled":
        host.run(["systemctl", "enable", DAEMON_SERVICE])
    if daemon_state.get("active") == "active":
        host.run(["systemctl", "start", DAEMON_SERVICE])

    expected_states = {**racer_state, DAEMON_SERVICE: daemon_state}
    actual_states = _wait_units_restored(host, expected_states)
    masks_after = _masked_units(host)
    if masks_after:
        raise FenceError(
            "unit restoration proof failed: "
            f"masked={masks_after}"
        )
    state["daemon_service_state"] = daemon_state
    state["phase"] = "restored"
    _atomic_json(state_path, state)
    evidence.update(
        {
            "phase": "restored",
            "masked_units_before": masked_before,
            "masked_units_after": masks_after,
            "expected_restored_unit_states": expected_states,
            "restored_unit_states": actual_states,
            "normal_handoff_active_established": successful_normal_handoff,
        }
    )
    return evidence


def _quiesced_restore_not_applicable(reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "owner": TASK_OWNER,
        "phase": "not_applicable",
        "safe": False,
        "cleanup_restored": False,
        "mutation_started": False,
        "reason": reason,
    }


def _recorded_quiesced_restore_intent(
    state: Mapping[str, Any],
) -> tuple[tuple[str, ...], dict[str, dict[str, str]], dict[str, str]]:
    """Validate the complete pre-quiesce posture before recovery mutates it."""

    progress = state.get("fence_progress")
    if not isinstance(progress, Mapping) or not (
        progress.get("restart_policy_proved") is True
        and progress.get("boot_activators_disabled") is True
    ):
        raise FenceError("quiesced restore lacks a completed write-ahead fence")

    raw_racers = state.get("present_restart_racer_units")
    if not isinstance(raw_racers, (list, tuple)):
        raise FenceError("quiesced restore racer inventory is invalid")
    saved_racers = tuple(str(unit) for unit in raw_racers)
    if (
        len(set(saved_racers)) != len(saved_racers)
        or any(unit not in RESTART_RACER_UNITS for unit in saved_racers)
    ):
        raise FenceError("quiesced restore racer inventory is invalid")

    raw_racer_state = state.get("restart_racer_state")
    if not isinstance(raw_racer_state, Mapping) or set(raw_racer_state) != set(
        saved_racers
    ):
        raise FenceError("quiesced restore racer state is incomplete")
    racer_state: dict[str, dict[str, str]] = {}
    for unit in saved_racers:
        raw = raw_racer_state.get(unit)
        if not isinstance(raw, Mapping):
            raise FenceError(f"saved unit state is invalid: {unit}")
        saved = {"active": raw.get("active"), "enabled": raw.get("enabled")}
        if (
            saved["active"] not in {"active", "inactive", "failed"}
            or saved["enabled"] not in AUTHORITATIVE_UNIT_ENABLED_STATES
            or saved["enabled"] in {"masked", "masked-runtime"}
        ):
            raise FenceError(f"saved unit state is invalid: {unit}")
        racer_state[unit] = saved

    raw_daemon_state = state.get("daemon_service_state")
    if not isinstance(raw_daemon_state, Mapping):
        raise FenceError("saved daemon unit state is invalid")
    daemon_state = {
        "active": raw_daemon_state.get("active"),
        "enabled": raw_daemon_state.get("enabled"),
    }
    if (
        daemon_state["active"] not in {"active", "inactive", "failed"}
        or daemon_state["enabled"]
        not in {"enabled", "enabled-runtime", "disabled"}
    ):
        raise FenceError("saved daemon unit state is invalid")

    raw_policies = state.get("old_restart_policies")
    if not isinstance(raw_policies, Mapping) or set(raw_policies) != set(
        EXPECTED_CONTAINERS
    ):
        raise FenceError("quiesced restore restart policies are incomplete")
    policies = {str(name): str(policy) for name, policy in raw_policies.items()}
    raw_sidecar_policies = state.get("sidecar_restart_policies") or {}
    if not isinstance(raw_sidecar_policies, Mapping):
        raise FenceError("quiesced restore sidecar policies are invalid")
    sidecar_policies = {
        str(name): str(policy) for name, policy in raw_sidecar_policies.items()
    }
    if set(sidecar_policies) - {name for name, _service in CANONICAL_SIDECARS}:
        raise FenceError("quiesced restore sidecar policies are invalid")
    policies.update(sidecar_policies)
    if any(
        policy not in {"no", "always", "unless-stopped", "on-failure"}
        for policy in policies.values()
    ):
        raise FenceError("quiesced restore restart policy is invalid")
    if state.get("extra_volume_consumers"):
        raise FenceError("quiesced restore recorded an extra volume consumer")
    return saved_racers, racer_state, policies


def _restore_recorded_restart_policies(
    host: Host,
    policies: Mapping[str, str],
    *,
    require_all: bool,
) -> dict[str, str]:
    """Restore policies by canonical name; absent compose-down IDs are deferred."""

    proof: dict[str, str] = {}
    for name, policy in policies.items():
        if _named_container_absent_exact(host, name):
            if require_all:
                raise FenceError(f"restored container is missing: {name}")
            continue
        info = host.container_info(name)
        identity = str(info.get("Id", ""))
        if not identity:
            raise FenceError(f"container identity is unavailable: {name}")
        host.run(["docker", "update", f"--restart={policy}", identity])
        actual = host.container_restart_policy(identity)
        if actual != policy:
            raise FenceError(
                f"restart policy restoration did not persist: "
                f"{name}={actual}, expected={policy}"
            )
        proof[name] = actual
    return proof


def _prove_quiesced_restore_observation(
    observation: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    image_ref: str,
    revision: str,
) -> bool:
    containers = observation.get("containers")
    if not isinstance(containers, Mapping) or set(containers) != set(
        EXPECTED_CONTAINERS
    ):
        return False
    if set(observation.get("volume_container_names", [])) != set(
        EXPECTED_CONTAINERS
    ):
        return False
    if observation.get("queue_risk") or observation.get("stray_writer_processes"):
        return False
    if observation.get("receipt_snapshot") != state.get("receipt_snapshot"):
        return False
    identities = {
        (row.get("image_ref"), row.get("revision"))
        for row in containers.values()
        if isinstance(row, Mapping)
    }
    if identities != {(image_ref, revision)}:
        return False
    return all(
        isinstance(row, Mapping)
        and row.get("running") is True
        and bool(row.get("id"))
        for row in containers.values()
    )


def _quiesced_restore_compose_provenance(
    host: Host,
    observation: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    services = dict(zip(EXPECTED_CONTAINERS, RECOVERY_SERVICES, strict=True))
    containers = observation.get("containers", {})
    proof: dict[str, dict[str, str]] = {}
    for name, service in services.items():
        info = host.container_info(name)
        identity = str(info.get("Id", ""))
        labels = info.get("Config", {}).get("Labels", {}) or {}
        if (
            identity != containers.get(name, {}).get("id")
            or labels.get("com.docker.compose.project")
            != CANONICAL_COMPOSE_PROJECT
            or labels.get("com.docker.compose.service") != service
        ):
            raise FenceError(f"restored compose provenance is invalid: {name}")
        proof[name] = {
            "id": identity,
            "project": CANONICAL_COMPOSE_PROJECT,
            "service": service,
        }
    return proof


def _wait_quiesced_restore_observation(
    host: Host,
    state: Mapping[str, Any],
    *,
    image_ref: str,
    revision: str,
    timeout_seconds: float = QUIESCED_RESTORE_PROOF_TIMEOUT_SECONDS,
    delay_seconds: float = 2.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    last_observation: dict[str, Any] = {}
    while True:
        try:
            last_observation = observe_fleet(
                host,
                expected_image_ref=image_ref,
            )
            if _prove_quiesced_restore_observation(
                last_observation,
                state,
                image_ref=image_ref,
                revision=revision,
            ):
                return last_observation
            last_error = "fleet identity, safety, or receipt proof disagrees"
        except FenceError as exc:
            last_error = str(exc)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(delay_seconds, remaining))
    raise FenceError(
        "quiesced fleet recovery proof did not converge: "
        f"{last_error}; diagnostic={json.dumps(last_observation, sort_keys=True)}"
    )


def _wait_loopback_mcp_health(
    host: Host,
    *,
    timeout_seconds: float = LOOPBACK_HEALTH_TIMEOUT_SECONDS,
    delay_seconds: float = 3.0,
) -> dict[str, Any]:
    url = "http://127.0.0.1:8001/mcp"
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "quiesced-restore-canary",
                    "version": "1",
                },
            },
        },
        separators=(",", ":"),
    )
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_status = "000"
    while True:
        attempts += 1
        last_status = host.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--connect-timeout",
                "2",
                "--max-time",
                "5",
                "--request",
                "POST",
                "--header",
                "Content-Type: application/json",
                "--header",
                "Accept: application/json, text/event-stream",
                "--data",
                payload,
                url,
            ],
            check=False,
            timeout_seconds=10,
        )
        if last_status == "200":
            return {
                "url": url,
                "http_status": last_status,
                "attempts": attempts,
                "timeout_seconds": timeout_seconds,
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(delay_seconds, remaining))
    raise FenceError(
        "restored daemon never served MCP on loopback: "
        f"attempts={attempts}, last_http_status={last_status!r}"
    )


def _quiesced_restore_evidence(
    *,
    image_ref: str,
    revision: str,
    masked_before: Sequence[str],
    expected_states: Mapping[str, Mapping[str, str]],
    actual_states: Mapping[str, Mapping[str, str]],
    expected_policies: Mapping[str, str],
    policy_proof: Mapping[str, str],
    observation: Mapping[str, Any],
    compose_provenance: Mapping[str, Mapping[str, str]],
    health: Mapping[str, Any],
    idempotent: bool,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "owner": TASK_OWNER,
        "phase": "restored",
        "safe": True,
        "cleanup_restored": True,
        "mutation_started": not idempotent,
        "recovery_kind": "quiesced_before_image_commit",
        "previous_image_ref": image_ref,
        "previous_revision": revision,
        "masked_units_before": list(masked_before),
        "masked_units_after": [],
        "expected_restored_unit_states": {
            unit: dict(unit_state) for unit, unit_state in expected_states.items()
        },
        "restored_unit_states": {
            unit: dict(unit_state) for unit, unit_state in actual_states.items()
        },
        "expected_restart_policies": dict(expected_policies),
        "restart_policy_restore_proof": dict(policy_proof),
        "observation": dict(observation),
        "compose_provenance": {
            name: dict(proof) for name, proof in compose_provenance.items()
        },
        "loopback_health": dict(health),
        "idempotent_reproof": idempotent,
    }


def restore_quiesced(
    host: Host,
    *,
    image_ref: str,
    revision: str,
    run_id: str,
    state_path: Path,
) -> dict[str, Any]:
    """Restore only a proved current-run quiesce whose image never committed."""

    _require_run_id(run_id)
    if not state_path.is_file():
        return _quiesced_restore_not_applicable("durable fence state is absent")
    state = _load_state(state_path)
    if state.get("run_id") != run_id:
        return _quiesced_restore_not_applicable("durable fence belongs to another run")

    phase = str(state.get("phase", ""))
    restore_marker = state.get("quiesced_restore")
    idempotent_reproof = phase == "restored" and isinstance(
        restore_marker, Mapping
    ) and restore_marker.get("recovery_kind") == "quiesced_before_image_commit"
    if phase not in {"preflight_proved", "restoring_quiesced"} and not (
        idempotent_reproof
    ):
        return _quiesced_restore_not_applicable(
            "durable fence is not a proved pre-image-commit quiesce"
        )

    previous_image_ref = str(state.get("previous_image_ref", ""))
    previous_revision = str(state.get("previous_revision", ""))
    configured_image_ref = _configured_image()
    if (
        not CANONICAL_IMAGE_RE.fullmatch(image_ref)
        or not REVISION_RE.fullmatch(revision)
        or image_ref != previous_image_ref
        or revision != previous_revision
        or configured_image_ref != previous_image_ref
    ):
        return _quiesced_restore_not_applicable(
            "configured, recorded, and floor-proved previous identities disagree"
        )

    saved_racers, racer_state, policies = _recorded_quiesced_restore_intent(state)
    saved_units = (*saved_racers, DAEMON_SERVICE)
    if any(not host.unit_present(unit) for unit in saved_units):
        raise FenceError("quiesced restore saved unit is missing")
    expected_states = {
        **racer_state,
        DAEMON_SERVICE: _daemon_restore_expectation(
            state.get("daemon_service_state", {}),
            establish_active=True,
        ),
    }

    if idempotent_reproof:
        masks_after = _masked_units(host)
        if masks_after:
            raise FenceError(f"idempotent restored proof found masks: {masks_after}")
        actual_states = _wait_units_restored(
            host,
            expected_states,
            timeout_seconds=5,
        )
        policy_proof = {
            name: host.container_restart_policy(
                str(host.container_info(name).get("Id", ""))
            )
            for name in policies
        }
        if policy_proof != policies:
            raise FenceError("idempotent restored restart policies disagree")
        observation = _wait_quiesced_restore_observation(
            host,
            state,
            image_ref=image_ref,
            revision=revision,
            timeout_seconds=5,
            delay_seconds=1,
        )
        health = _wait_loopback_mcp_health(
            host,
            timeout_seconds=10,
            delay_seconds=1,
        )
        compose_provenance = _quiesced_restore_compose_provenance(
            host,
            observation,
        )
        return _quiesced_restore_evidence(
            image_ref=image_ref,
            revision=revision,
            masked_before=(),
            expected_states=expected_states,
            actual_states=actual_states,
            expected_policies=policies,
            policy_proof=policy_proof,
            observation=observation,
            compose_provenance=compose_provenance,
            health=health,
            idempotent=True,
        )

    masks_before = _masked_units(host)
    expected_mask_set = set(saved_units)
    if set(masks_before) - expected_mask_set:
        raise FenceError("quiesced restore found an unrecorded masked unit")
    if phase == "preflight_proved" and set(masks_before) != expected_mask_set:
        raise FenceError("proved quiesce does not retain its exact runtime masks")

    volume_dir = Path(str(state.get("volume_mountpoint", ""))).resolve()
    receipt_path = Path(str(state.get("receipt_host_path", ""))).resolve()
    if volume_dir not in receipt_path.parents or not volume_dir.is_dir():
        raise FenceError("quiesced restore volume provenance is invalid")
    volume_names = set(host.volume_container_names())
    if not volume_names <= set(EXPECTED_CONTAINERS):
        raise FenceError("quiesced restore found an extra production-volume consumer")
    old_ids = state.get("old_container_ids")
    if not isinstance(old_ids, Mapping) or set(old_ids) != set(EXPECTED_CONTAINERS):
        raise FenceError("quiesced restore old container identities are incomplete")
    for name in EXPECTED_CONTAINERS:
        if _named_container_absent_exact(host, name):
            continue
        info = host.container_info(name)
        identity = str(info.get("Id", ""))
        if phase == "preflight_proved" and identity != str(old_ids.get(name, "")):
            raise FenceError(f"quiesced container identity was substituted: {name}")
        if phase == "preflight_proved" and info.get("State", {}).get("Running"):
            raise FenceError(f"quiesced container unexpectedly runs: {name}")
        if (
            phase == "preflight_proved"
            and host.container_restart_policy(identity) != "no"
        ):
            raise FenceError(f"quiesced container restart fence drifted: {name}")
        labels = info.get("Config", {}).get("Labels", {}) or {}
        expected_service = dict(
            zip(EXPECTED_CONTAINERS, RECOVERY_SERVICES, strict=True)
        )[name]
        if (
            labels.get("com.docker.compose.project") != CANONICAL_COMPOSE_PROJECT
            or labels.get("com.docker.compose.service") != expected_service
        ):
            raise FenceError(f"quiesced compose provenance is invalid: {name}")
        if phase == "restoring_quiesced":
            actual_image_ref, actual_revision = host.image_identity(
                str(info.get("Image", "")),
                image_ref.partition("@")[0],
            )
            if (actual_image_ref, actual_revision) != (image_ref, revision):
                raise FenceError(f"partial restored image is invalid: {name}")

    sidecar_handoff = state.get("sidecar_handoff") or {}
    recorded_sidecar_ids = (
        sidecar_handoff.get("container_ids", {})
        if isinstance(sidecar_handoff, Mapping)
        else {}
    )
    sidecar_services = dict(CANONICAL_SIDECARS)
    for name in set(policies) & set(sidecar_services):
        if _named_container_absent_exact(host, name):
            continue
        info = host.container_info(name)
        identity = str(info.get("Id", ""))
        labels = info.get("Config", {}).get("Labels", {}) or {}
        if (
            labels.get("com.docker.compose.project") != CANONICAL_COMPOSE_PROJECT
            or labels.get("com.docker.compose.service") != sidecar_services[name]
        ):
            raise FenceError(f"quiesced sidecar provenance is invalid: {name}")
        if phase == "preflight_proved" and identity != str(
            recorded_sidecar_ids.get(name, "")
        ):
            raise FenceError(f"quiesced sidecar identity was substituted: {name}")
        _assert_sidecar_nonwriter(name, info, volume_dir)

    if inventory_queue_risk(volume_dir):
        raise FenceError("queue risk appeared before quiesced restore")
    if _stray_writer_processes(receipt_path, set(), volume_dir):
        raise FenceError("stray writer process appeared before quiesced restore")
    if receipt_snapshot(receipt_path) != state.get("receipt_snapshot"):
        raise FenceError("receipt snapshot changed before quiesced restore")

    if phase == "preflight_proved":
        state["phase"] = "restoring_quiesced"
        state["quiesced_restore"] = {
            "recovery_kind": "quiesced_before_image_commit",
            "previous_image_ref": image_ref,
            "previous_revision": revision,
            "started_epoch": time.time(),
        }
        _atomic_json(state_path, state)

    prestart_policy_proof = _restore_recorded_restart_policies(
        host,
        policies,
        require_all=False,
    )
    host.run(["systemctl", "unmask", "--runtime", *saved_units])
    for unit in (*saved_racers, DAEMON_SERVICE):
        prior = expected_states[unit]
        if prior.get("enabled") == "enabled":
            host.run(["systemctl", "enable", unit])
        elif prior.get("enabled") == "enabled-runtime":
            host.run(["systemctl", "enable", "--runtime", unit])

    host.run(
        ["systemctl", "start", DAEMON_SERVICE],
        timeout_seconds=UNIT_RESTORE_TIMEOUT_SECONDS,
    )
    observation = _wait_quiesced_restore_observation(
        host,
        state,
        image_ref=image_ref,
        revision=revision,
    )
    _quiesced_restore_compose_provenance(host, observation)
    policy_proof = _restore_recorded_restart_policies(
        host,
        policies,
        require_all=True,
    )
    for unit in saved_racers:
        if racer_state[unit].get("active") == "active":
            host.run(["systemctl", "start", unit])

    actual_states = _wait_units_restored(
        host,
        expected_states,
        timeout_seconds=QUIESCED_RESTORE_PROOF_TIMEOUT_SECONDS,
    )
    health = _wait_loopback_mcp_health(host)
    masks_after = _masked_units(host)
    if masks_after:
        raise FenceError(f"quiesced restore left runtime masks: {masks_after}")
    if policy_proof != policies:
        raise FenceError("quiesced restore restart policy proof is incomplete")
    observation = _wait_quiesced_restore_observation(
        host,
        state,
        image_ref=image_ref,
        revision=revision,
        timeout_seconds=5,
        delay_seconds=1,
    )
    compose_provenance = _quiesced_restore_compose_provenance(
        host,
        observation,
    )

    state["phase"] = "restored"
    state["daemon_service_state"] = expected_states[DAEMON_SERVICE]
    state["restart_policy_restore_proof"] = policy_proof
    state["quiesced_restore"].update(
        {
            "completed_epoch": time.time(),
            "prestart_restart_policy_restore_proof": prestart_policy_proof,
            "expected_restored_unit_states": expected_states,
            "restored_unit_states": actual_states,
            "masked_units_after": [],
            "loopback_health": health,
        }
    )
    _atomic_json(state_path, state)
    return _quiesced_restore_evidence(
        image_ref=image_ref,
        revision=revision,
        masked_before=masks_before,
        expected_states=expected_states,
        actual_states=actual_states,
        expected_policies=policies,
        policy_proof=policy_proof,
        observation=observation,
        compose_provenance=compose_provenance,
        health=health,
        idempotent=False,
    )


def _validate_unsafe_recovery_source(
    host: Host,
    *,
    source_run_id: str,
    image_ref: str,
    revision: str,
    state_path: Path,
    retire_extra_consumers: tuple[str, ...] = (),
) -> tuple[dict[str, Any], str, str]:
    """Validate an immutable unsafe-fence generation before any mutation."""

    _require_run_id(source_run_id)
    state = _load_state(state_path)
    if state.get("phase") != "unsafe_fenced":
        raise FenceError("canonical state is not an authoritatively unsafe fence")
    if (state.get("source_run_id") or state.get("run_id")) != source_run_id:
        raise FenceError("unsafe fence belongs to another source run")
    required = (
        "target_image_ref",
        "target_revision",
        "previous_image_ref",
        "previous_revision",
        "volume_mountpoint",
        "receipt_host_path",
        "receipt_snapshot",
        "old_container_ids",
        "restart_racer_state",
        "daemon_service_state",
        "present_restart_racer_units",
        "extra_volume_consumers",
    )
    if any(key not in state for key in required):
        raise FenceError("unsafe fence lacks complete inherited preflight state")
    extras = dict(state.get("extra_volume_consumers") or {})
    # NOT gated on `extras` being non-empty. A prior recovery attempt can
    # already have cleared `extra_volume_consumers` while the OTHER fleet
    # enumerations still name the retired container -- observed live on
    # recoveries 31049384995 and 31049698106, which both refused with
    # "stopped fleet removal intent is invalid" because this block was
    # skipped once extras was empty, leaving the stale removal plan intact.
    if retire_extra_consumers:
        # Narrow, operator-named retirement of a recorded extra consumer.
        #
        # Live 2026-08-05: a deploy added a container the exact-fleet fence did
        # not admit; cleanup fenced the whole fleet (daemon included, so /mcp
        # 502'd) AND recorded the newcomer here, which made recovery refuse
        # unconditionally. Reverting the container is not enough -- the RECORD
        # outlives it, so production stays down with no in-band way back.
        #
        # Retirement is deliberately not a bypass:
        #   * the operator must name the container EXACTLY -- no wildcards;
        #   * a name in EXPECTED_CONTAINERS can never be retired;
        #   * the recorded entry must already be stopped;
        #   * the container must be absent or still stopped on the host NOW,
        #     re-checked here rather than trusted from the record;
        #   * every retirement is returned as evidence.
        # Anything else still raises.
        retired: dict[str, Any] = {}
        for name in retire_extra_consumers:
            if name in EXPECTED_CONTAINERS:
                raise FenceError(
                    "refusing to retire an expected fleet container"
                )
            entry = extras.get(name)
            enumerated = any(
                name in dict(state.get(key) or {})
                for key in ("old_container_ids", "recovery_container_ids")
            ) or name in dict(
                (state.get("stopped_fleet_removal") or {}).get(
                    "container_ids"
                )
                or {}
            )
            if entry is None and not enumerated:
                raise FenceError(
                    "refusing to retire an unrecorded extra volume consumer"
                )
            # The gate is the LIVE state, not the recorded one.
            # (Runs whether or not an extras entry survives: a container that
            # is running again must never be retired from ANY enumeration.) The fence
            # records each consumer immediately BEFORE stopping it, so
            # `entry["running"]` is true for every member at fence time --
            # refusing on it made retirement permanently unsatisfiable
            # (observed live: recovery 31047718991 refused on exactly this).
            # `entry` is kept as evidence rather than used as a gate.
            #
            # Fail closed: retirement requires POSITIVE proof the container is
            # not running. An inspection that errors is not proof of absence,
            # so it refuses rather than assuming the container is gone.
            try:
                info = host.container_info(name)
            except Exception:  # noqa: BLE001
                # Inspect failing is ambiguous alone, so prove ABSENCE
                # positively rather than assuming it: a SUCCESSFUL `ps -a`
                # listing that does not contain the name means the container
                # no longer exists -- the safest possible state, since a
                # removed container cannot be writing to the volume. If that
                # probe also fails, host.run raises and we still refuse, so an
                # unreachable docker never unlocks retirement.
                #
                # Observed live: recovery 31047957677 refused here because
                # cleanup had REMOVED the container, not merely stopped it.
                listed = host.run(
                    [
                        "docker",
                        "ps",
                        "-a",
                        "--filter",
                        f"name=^/{name}$",
                        "--format",
                        "{{.Names}}",
                    ]
                )
                if listed.strip():
                    raise FenceError(
                        "cannot prove the extra volume consumer is stopped"
                    )
                info = None
            if info is not None and bool(info.get("State", {}).get("Running")):
                raise FenceError(
                    "refusing to retire a RUNNING extra volume consumer"
                )
            # Retiring the RECORD is not enough: a stopped leftover container
            # still mounts the volume, so `volume_container_names()` keeps
            # reporting it and the very next check refuses with "fenced volume
            # has partial or extra writer containers" (observed live, recovery
            # 31057720758). Remove the container too -- it is explicitly named,
            # already proven not running, and is not an expected fleet member.
            if info is not None:
                try:
                    host.run(["docker", "rm", name])
                except Exception:  # noqa: BLE001
                    raise FenceError(
                        "could not remove the retired extra volume consumer"
                    ) from None
            retired[name] = dict(entry or {"note": "extras already cleared"})
            extras.pop(name, None)
            # Retiring a container means retiring it from EVERY recorded
            # structure that enumerates the fleet, not just this one.
            #
            # `_validate_stopped_fleet` requires
            # `set(stopped_fleet_removal["container_ids"]) == EXPECTED_CONTAINERS`
            # and that it still equal its `recorded_source` map. Those were
            # captured while the retired container existed, so leaving it in
            # place keeps the fleet enumerations one name too long and the
            # next recovery fails with "stopped fleet removal intent is
            # invalid" -- observed live on recovery 31049384995, after the
            # extra-consumer record had already been cleared.
            plan = state.get("stopped_fleet_removal")
            if isinstance(plan, dict):
                planned = plan.get("container_ids")
                if isinstance(planned, dict):
                    planned.pop(name, None)
                source_key = str(plan.get("recorded_source", ""))
                source_map = state.get(source_key)
                if isinstance(source_map, dict):
                    source_map.pop(name, None)
            for enumeration in ("old_container_ids", "recovery_container_ids"):
                recorded_map = state.get(enumeration)
                if isinstance(recorded_map, dict):
                    recorded_map.pop(name, None)
        state["extra_volume_consumers"] = extras
        state["retired_extra_volume_consumers"] = retired
    if extras:
        raise FenceError("unsafe fence recorded extra production-volume consumers")
    if not CANONICAL_IMAGE_RE.fullmatch(image_ref):
        raise FenceError("runner-bound recovery image is not immutable")
    if not REVISION_RE.fullmatch(revision):
        raise FenceError("runner-bound recovery revision is not canonical")
    configured = _configured_image()
    if configured != image_ref:
        raise FenceError("runner-bound recovery image disagrees with configured image")
    recorded = {
        (
            str(state["target_image_ref"]),
            str(state["target_revision"]),
        ),
        (
            str(state["previous_image_ref"]),
            str(state["previous_revision"]),
        ),
    }
    if (image_ref, revision) not in recorded:
        raise FenceError("runner-bound recovery identity is not recorded")
    repository = image_ref.partition("@")[0]
    if host.image_identity(image_ref, repository) != (image_ref, revision):
        raise FenceError("local recovery image identity disagrees with runner binding")
    policies = state.get("old_restart_policies")
    if not isinstance(policies, dict) or set(policies) != set(EXPECTED_CONTAINERS):
        raise FenceError("saved restart policy keys are incomplete")
    if any(
        policy not in {"always", "unless-stopped", "on-failure", "no"}
        for policy in policies.values()
    ):
        raise FenceError("saved restart policy is invalid")
    names = set(host.volume_container_names())
    expected_names = set(EXPECTED_CONTAINERS)
    if names - expected_names:
        raise FenceError("fenced volume has partial or extra writer containers")
    if names and names < expected_names:
        raw_plan = state.get("stopped_fleet_removal")
        if isinstance(raw_plan, Mapping):
            planned_ids = {
                str(name): str(identity)
                for name, identity in dict(
                    raw_plan.get("container_ids") or {}
                ).items()
            }
            recorded_source = str(raw_plan.get("recorded_source", ""))
            if (
                raw_plan.get("removal_phase") != "planned"
                or recorded_source
                not in {"recovery_container_ids", "old_container_ids"}
                or set(planned_ids) != expected_names
                or dict(state.get(recorded_source) or {}) != planned_ids
            ):
                raise FenceError("stopped fleet removal intent is invalid")
            for name in names:
                if str(host.container_info(name).get("Id", "")) != planned_ids[name]:
                    raise FenceError("stopped fleet removal identities changed")
            for name in expected_names - names:
                if not _container_absent_exact(host, planned_ids[name]):
                    raise FenceError(f"removed stopped writer still exists: {name}")
        else:
            _partial_canonical_target_ids(host, state, names)
    if names:
        inspections = (
            _exact_inspections(host)
            if names == expected_names
            else {name: host.container_info(name) for name in names}
        )
        for name, info in inspections.items():
            if info.get("State", {}).get("Running"):
                raise FenceError(f"fenced writer is still running: {name}")
            identity = host.image_identity(str(info.get("Image", "")), repository)
            if names == expected_names and identity != (image_ref, revision):
                raise FenceError(
                    "fenced fleet identity disagrees with runner binding"
                )
            container_id = str(info.get("Id", ""))
            if host.container_restart_policy(container_id) != "no":
                raise FenceError("fenced writer restart policy is not no")
    volume_dir = Path(str(state["volume_mountpoint"]))
    if volume_dir.resolve() != host.volume_dir().resolve():
        raise FenceError("fenced volume mountpoint changed")
    if inventory_queue_risk(volume_dir):
        raise FenceError("bug_investigation queue risk appeared while fenced")
    receipt_path = Path(str(state["receipt_host_path"]))
    if receipt_snapshot(receipt_path) != state["receipt_snapshot"]:
        raise FenceError("receipt snapshot changed while fenced")
    for old_id in state["old_container_ids"].values():
        if _container_running_exact(host, str(old_id)):
            raise FenceError("old writer container restarted while fenced")
    if _stray_writer_processes(receipt_path, set(), volume_dir):
        raise FenceError("stray writer process exists while fenced")
    expected_units = (
        *tuple(state["present_restart_racer_units"]),
        DAEMON_SERVICE,
    )
    for unit in expected_units:
        if not host.unit_present(unit):
            raise FenceError(f"saved production unit is missing: {unit}")
        unit_state = _validated_unit_state(host, unit)
        load_state = host.unit_load_state(unit)
        permitted_enabled = {"masked", "masked-runtime", "disabled"}
        if unit.endswith(".service") and unit != DAEMON_SERVICE:
            permitted_enabled.add("static")
        if unit_state["enabled"] not in permitted_enabled:
            raise FenceError(f"fenced unit can still boot-start: {unit}")
        if load_state not in {"loaded", "masked"}:
            raise FenceError(f"fenced unit load state is not authoritative: {unit}")
        if unit_state["active"] not in {"inactive", "failed"}:
            raise FenceError(f"fenced unit is not inactive: {unit}")
    return state, image_ref, revision


def _recovery_unit_name(run_id: str) -> str:
    suffix = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    return f"tinyassets-recovery-expiry-{suffix}"


def _recovery_project_name(run_id: str) -> str:
    suffix = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    return f"tinyassets-recovery-{suffix}"


def _partial_canonical_target_ids(
    host: Host,
    state: Mapping[str, Any],
    names: set[str],
) -> dict[str, str]:
    """Prove a strict subset belongs to the recorded failed canonical target."""

    expected_names = set(EXPECTED_CONTAINERS)
    if not names or not names < expected_names:
        raise FenceError("partial canonical target names are invalid")
    target_image_ref = str(state.get("target_image_ref", ""))
    target_revision = str(state.get("target_revision", ""))
    if (
        not CANONICAL_IMAGE_RE.fullmatch(target_image_ref)
        or not REVISION_RE.fullmatch(target_revision)
    ):
        raise FenceError("partial canonical target identity is not recorded")
    repository = target_image_ref.partition("@")[0]
    inspections = {name: host.container_info(name) for name in names}
    actual: dict[str, str] = {}
    for name, info in inspections.items():
        if info.get("State", {}).get("Running"):
            raise FenceError(f"partial canonical target is running: {name}")
        identity = host.image_identity(str(info.get("Image", "")), repository)
        if identity != (target_image_ref, target_revision):
            raise FenceError(
                f"partial canonical target image changed: {name}"
            )
        labels = info.get("Config", {}).get("Labels", {}) or {}
        if labels.get("com.docker.compose.project") != "tinyassets":
            raise FenceError(
                f"partial canonical target project changed: {name}"
            )
        container_id = str(info.get("Id", ""))
        if not container_id:
            raise FenceError(
                f"partial canonical target identity is missing: {name}"
            )
        if host.container_restart_policy(container_id) != "no":
            raise FenceError(
                f"partial canonical target restart policy is not no: {name}"
            )
        actual[name] = container_id
    for name in expected_names - names:
        if not _named_container_absent_exact(host, name):
            raise FenceError(
                f"missing partial target name still exists off-volume: {name}"
            )
    return actual


def _remove_partial_canonical_target_for_recovery(
    host: Host,
    state: dict[str, Any],
    *,
    state_path: Path,
) -> dict[str, str]:
    """Write-ahead and remove only a proved failed canonical target subset."""

    raw_plan = state.get("partial_target_removal")
    if "partial_target_removal" in state and not isinstance(raw_plan, dict):
        raise FenceError("partial target removal intent is invalid")
    plan = raw_plan if isinstance(raw_plan, dict) else None
    names = set(host.volume_container_names())
    recorded: dict[str, str] = {}
    if plan:
        recorded = {
            str(name): str(identity)
            for name, identity in dict(plan.get("container_ids") or {}).items()
        }
        if (
            plan.get("image_ref") != state.get("target_image_ref")
            or plan.get("revision") != state.get("target_revision")
            or plan.get("project_name") != "tinyassets"
            or plan.get("removal_phase") not in {"planned", "removed"}
            or not recorded
            or not set(recorded) < set(EXPECTED_CONTAINERS)
        ):
            raise FenceError("partial target removal intent is invalid")
        if not names <= set(recorded):
            raise FenceError("partial target removal inventory changed")

    if not names:
        if not plan:
            return {}
        for name, identity in recorded.items():
            if not _container_absent_exact(host, identity):
                raise FenceError(
                    f"removed partial target still exists: {name}"
                )
        _prove_expected_container_names_absent(host)
        if plan["removal_phase"] != "removed":
            plan["removal_phase"] = "removed"
            _atomic_json(state_path, state)
        return recorded
    if not names < set(EXPECTED_CONTAINERS):
        return {}

    actual = _partial_canonical_target_ids(host, state, names)
    if not plan:
        plan = {
            "container_ids": dict(actual),
            "image_ref": str(state["target_image_ref"]),
            "project_name": "tinyassets",
            "removal_phase": "planned",
            "revision": str(state["target_revision"]),
        }
        state["partial_target_removal"] = plan
        _atomic_json(state_path, state)
        recorded = dict(actual)
    if (
        plan.get("image_ref") != state.get("target_image_ref")
        or plan.get("revision") != state.get("target_revision")
        or plan.get("project_name") != "tinyassets"
        or plan.get("removal_phase") != "planned"
        or not recorded
        or not set(recorded) < set(EXPECTED_CONTAINERS)
        or not names <= set(recorded)
        or actual != {name: recorded[name] for name in names}
    ):
        raise FenceError("partial target removal intent changed")
    for name in set(recorded) - names:
        if not _container_absent_exact(host, recorded[name]):
            raise FenceError(
                f"removed partial target still exists: {name}"
            )
    host.run(
        [
            "docker",
            "rm",
            *(recorded[name] for name in EXPECTED_CONTAINERS if name in names),
        ]
    )
    if host.volume_container_names():
        raise FenceError("partial target removal did not converge")
    _prove_expected_container_names_absent(host)
    plan["removal_phase"] = "removed"
    _atomic_json(state_path, state)
    return recorded


def _remove_recorded_stopped_fleet_for_recovery(
    host: Host,
    state: dict[str, Any],
    *,
    state_path: Path,
) -> dict[str, str]:
    """WAL-remove only the exact recorded, already-fenced generation."""

    names = set(host.volume_container_names())
    raw_plan = state.get("stopped_fleet_removal")
    if raw_plan is not None and not isinstance(raw_plan, dict):
        raise FenceError("stopped fleet removal intent is invalid")
    plan = raw_plan if isinstance(raw_plan, dict) else None
    # A COMPLETED removal plan is history, not a live invariant.
    #
    # A recovery that starts a fresh generation and is then re-fenced leaves
    # `stopped_fleet_removal["container_ids"]` describing the generation it
    # removed, while `recovery_container_ids` describes the one it started.
    # Same keys, different ids -- so the equality check below refuses forever
    # and the fleet can never be recovered again.
    #
    # Observed live 2026-08-05 after recovery 31048315265: phase=unsafe_fenced,
    # removal_phase=removed, all five expected containers present and Exited,
    # extras empty, every enumeration carrying exactly the right NAMES.
    #
    # When the plan says the removal already completed, drop it and let the
    # `if not plan:` branch re-derive from the CURRENT generation. That path is
    # strict -- it requires the live container ids to equal a recorded map
    # exactly -- so this loosens no identity guarantee, it just stops treating
    # finished work as a contradiction.
    if isinstance(plan, dict) and plan.get("removal_phase") == "removed":
        source_key = str(plan.get("recorded_source", ""))
        planned_now = {
            str(name): str(identity)
            for name, identity in dict(plan.get("container_ids") or {}).items()
        }
        if dict(state.get(source_key) or {}) != planned_now:
            plan = None
    recorded: dict[str, str] = {}
    recorded_source = ""
    if plan:
        recorded = {
            str(name): str(identity)
            for name, identity in dict(plan.get("container_ids") or {}).items()
        }
        recorded_source = str(plan.get("recorded_source", ""))
        if (
            plan.get("removal_phase") not in {"planned", "removed"}
            or recorded_source
            not in {"recovery_container_ids", "old_container_ids"}
            or set(recorded) != set(EXPECTED_CONTAINERS)
            or dict(state.get(recorded_source) or {}) != recorded
            or not names <= set(recorded)
        ):
            raise FenceError("stopped fleet removal intent is invalid")
    elif not names:
        return {}
    elif names != set(EXPECTED_CONTAINERS):
        raise FenceError("recovery cannot replace a partial or extra fleet")

    inspections = {name: host.container_info(name) for name in names}
    actual = {
        name: str(info.get("Id", ""))
        for name, info in inspections.items()
    }
    if not plan:
        for source in ("recovery_container_ids", "old_container_ids"):
            candidate = {
                str(name): str(identity)
                for name, identity in dict(state.get(source) or {}).items()
            }
            if set(candidate) == set(EXPECTED_CONTAINERS) and actual == candidate:
                recorded_source = source
                recorded = candidate
                break
        if not recorded_source:
            raise FenceError("stopped fleet is not the recorded fenced generation")
        plan = {
            "container_ids": dict(recorded),
            "recorded_source": recorded_source,
            "removal_phase": "planned",
        }
        state["stopped_fleet_removal"] = plan
        _atomic_json(state_path, state)
    elif actual != {name: recorded[name] for name in names}:
        raise FenceError("stopped fleet removal identities changed")

    for name, identity in recorded.items():
        if name not in names:
            if not _container_absent_exact(host, identity):
                raise FenceError(f"removed stopped writer still exists: {name}")
            if not _named_container_absent_exact(host, name):
                raise FenceError(
                    f"stopped writer name was substituted: {name}"
                )
    for name, info in inspections.items():
        if info.get("State", {}).get("Running"):
            raise FenceError(f"recovery writer is still running: {name}")
        identity = recorded[name]
        if host.container_restart_policy(identity) != "no":
            raise FenceError("recovery writer restart policy is not no")
    if names:
        host.run(
            [
                "docker",
                "rm",
                *(recorded[name] for name in EXPECTED_CONTAINERS if name in names),
            ]
        )
    if host.volume_container_names():
        raise FenceError("recorded stopped fleet removal did not converge")
    for name, identity in recorded.items():
        if not _container_absent_exact(host, identity):
            raise FenceError(f"removed stopped writer still exists: {name}")
    _prove_expected_container_names_absent(host)
    plan["removal_phase"] = "removed"
    _atomic_json(state_path, state)
    return recorded


def _remove_partial_owned_recovery_generation(
    host: Host,
    state: dict[str, Any],
    *,
    state_path: Path,
) -> bool:
    """Remove an expired partial compose start owned by the recorded recovery."""

    names = set(host.volume_container_names())
    if not names or names == set(EXPECTED_CONTAINERS):
        return False
    if (
        state.get("phase") != "recovery_starting"
        or not names < set(EXPECTED_CONTAINERS)
    ):
        raise FenceError("recovery volume inventory is not the exact owned writer fleet")
    project = str(state.get("recovery_project_name", ""))
    if not project:
        raise FenceError("partial recovery generation is not durable")
    inspections = {name: host.container_info(name) for name in names}
    actual: dict[str, str] = {}
    for name, info in inspections.items():
        labels = info.get("Config", {}).get("Labels", {}) or {}
        if labels.get("com.docker.compose.project") != project:
            raise FenceError(
                f"partial writer belongs to another recovery generation: {name}"
            )
        identity = str(info.get("Id", ""))
        if not identity or host.container_restart_policy(identity) != "no":
            raise FenceError("partial recovery writer restart policy is not no")
        actual[name] = identity
    recorded = dict(state.get("recovery_container_ids") or {})
    if recorded and any(recorded.get(name) != identity for name, identity in actual.items()):
        raise FenceError("partial recovery container identities changed")
    running = [
        actual[name]
        for name, info in inspections.items()
        if info.get("State", {}).get("Running")
    ]
    if running:
        host.run(["docker", "stop", *running])
    for name, identity in actual.items():
        info = host.container_info(name)
        if info.get("State", {}).get("Running"):
            raise FenceError(f"partial recovery writer did not stop: {name}")
        if host.container_restart_policy(identity) != "no":
            raise FenceError("partial recovery writer restart policy changed")
    host.run(["docker", "rm", *actual.values()])
    if host.volume_container_names():
        raise FenceError("partial recovery generation removal did not converge")
    state["recovery_removed_partial_container_ids"] = actual
    state["phase"] = "unsafe_fenced"
    _atomic_json(state_path, state)
    return True


def _assert_recovery_container_ownership(
    host: Host,
    state: Mapping[str, Any],
) -> dict[str, str]:
    """Reject mutation unless all current writers belong to this generation."""

    names = set(host.volume_container_names())
    project = str(state.get("recovery_project_name", ""))
    if (names or state.get("recovery_sidecar_container_ids")) and not project:
        raise FenceError("recovery container generation is not durable")
    actual_ids: dict[str, str] = {}
    if names:
        if names != set(EXPECTED_CONTAINERS):
            raise FenceError(
                "recovery volume inventory is not the exact owned writer fleet"
            )
        inspections = _exact_inspections(host)
        for name, info in inspections.items():
            labels = info.get("Config", {}).get("Labels", {}) or {}
            if labels.get("com.docker.compose.project") != project:
                raise FenceError(
                    f"writer belongs to another recovery generation: {name}"
                )
            actual_ids[name] = str(info.get("Id", ""))
        recorded = state.get("recovery_container_ids")
        if recorded and dict(recorded) != actual_ids:
            raise FenceError("recovery container identities changed")
    recorded_sidecars = {
        str(name): str(identity)
        for name, identity in dict(
            state.get("recovery_sidecar_container_ids") or {}
        ).items()
    }
    if recorded_sidecars:
        expected_sidecars = {
            name for name, _service in CANONICAL_SIDECARS
        }
        if not set(recorded_sidecars) <= expected_sidecars or (
            state.get("phase") != "recovery_sidecars_starting"
            and set(recorded_sidecars) != expected_sidecars
        ):
            raise FenceError("recovery sidecar identities are incomplete")
        sidecars = _recorded_recovery_sidecars(host, state)
        actual_sidecars = {
            name: str(info.get("Id", ""))
            for name, info in sidecars.items()
        }
        if actual_sidecars != recorded_sidecars:
            raise FenceError("recovery sidecar identities changed")
    return actual_ids


def _restore_prestart_fence_without_mutation(
    host: Host,
    state: dict[str, Any],
    *,
    state_path: Path,
) -> bool:
    """Return a failed-before-start attempt to unsafe state without touching Docker."""

    expected = dict(state.get("recovery_prestart_container_ids") or {})
    if state.get("recovery_container_ids") or not expected:
        return False
    if set(host.volume_container_names()) != set(EXPECTED_CONTAINERS):
        return False
    inspections = _exact_inspections(host)
    actual = {
        name: str(info.get("Id", ""))
        for name, info in inspections.items()
    }
    if actual != expected:
        return False
    for name, info in inspections.items():
        if info.get("State", {}).get("Running"):
            return False
        if host.container_restart_policy(str(info.get("Id", ""))) != "no":
            return False
    state["phase"] = "unsafe_fenced"
    state.pop("recovery_project_name", None)
    state.pop("recovery_expiry_unit", None)
    _atomic_json(state_path, state)
    return True


def _prove_recovery_entrypoint(expected_sha256: str) -> str:
    """Prove the expiry timer will invoke this exact installed script."""

    try:
        running_path = Path(__file__).resolve(strict=True)
        timer_path = RECOVERY_SCRIPT_PATH.resolve(strict=True)
    except OSError as exc:
        raise FenceError(
            f"recovery timer entrypoint is unavailable: {exc}"
        ) from exc
    if not running_path.samefile(timer_path):
        raise FenceError(
            "recovery timer entrypoint does not match the running script"
        )
    if not os.access(timer_path, os.X_OK):
        raise FenceError("recovery timer entrypoint is not executable")
    digest = hashlib.sha256(timer_path.read_bytes()).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise FenceError("expected recovery script digest is invalid")
    if digest != expected_sha256:
        raise FenceError("installed recovery script digest disagrees with checkout")
    return digest


def _arm_recovery_expiry(
    host: Host,
    *,
    source_run_id: str,
    run_id: str,
    state_path: Path,
) -> str:
    unit = _recovery_unit_name(run_id)
    host.run(
        [
            "systemd-run",
            "--quiet",
            "--collect",
            f"--unit={unit}",
            f"--on-active={RECOVERY_LEASE_SECONDS}s",
            "--timer-property=AccuracySec=1s",
            "/usr/bin/python3",
            str(RECOVERY_SCRIPT_PATH),
            "--state-path",
            str(state_path),
            "expire-recovery",
            "--source-run-id",
            source_run_id,
            "--run-id",
            run_id,
        ]
    )
    return unit


def _cancel_recovery_expiry(host: Host, unit: str) -> None:
    if unit:
        host.run(
            ["systemctl", "stop", f"{unit}.timer", f"{unit}.service"],
            check=False,
        )


def _prove_recovery_fences(
    host: Host,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Require every temporary recovery fence immediately before restore."""

    _assert_recovery_container_ownership(host, state)
    inspections = _exact_inspections(host)
    policies = {
        name: host.container_restart_policy(str(info.get("Id", "")))
        for name, info in inspections.items()
    }
    if state.get("recovery_sidecar_container_ids"):
        sidecars = _recovery_sidecar_inspections(host, state)
        policies.update(
            {
                name: host.container_restart_policy(str(info.get("Id", "")))
                for name, info in sidecars.items()
            }
        )
    if set(policies.values()) != {"no"}:
        raise FenceError(
            f"recovery restart fence drifted before finalization: {policies}"
        )
    saved_racers = set(state.get("present_restart_racer_units") or ())
    current_racers = {
        unit for unit in RESTART_RACER_UNITS if host.unit_present(unit)
    }
    if current_racers != saved_racers:
        raise FenceError(
            "writer boot activator inventory drifted before finalization: "
            f"saved={sorted(saved_racers)} current={sorted(current_racers)}"
        )
    unit_proof: dict[str, dict[str, str]] = {}
    for unit in (
        *tuple(sorted(saved_racers)),
        DAEMON_SERVICE,
    ):
        unit_state = _validated_unit_state(host, unit)
        if unit_state["active"] not in {"inactive", "failed"}:
            raise FenceError(f"recovery boot fence became active: {unit}")
        permitted = {"disabled", "masked", "masked-runtime"}
        if unit.endswith(".service") and unit != DAEMON_SERVICE:
            permitted.add("static")
        if unit_state["enabled"] not in permitted:
            raise FenceError(f"recovery boot fence became enabled: {unit}")
        unit_proof[unit] = unit_state
    expiry = str(state.get("recovery_expiry_unit", ""))
    if not expiry:
        raise FenceError("recovery expiry ownership is missing")
    timer = f"{expiry}.timer"
    timer_state = _validated_unit_state(host, timer)
    if (
        timer_state["active"] != "active"
        or timer_state["enabled"] not in {"transient", "enabled-runtime"}
    ):
        raise FenceError(
            f"recovery expiry timer is not armed: {timer}={timer_state}"
        )
    reconciler_state = _validated_unit_state(
        host,
        RECOVERY_RECONCILE_SERVICE,
    )
    if reconciler_state["enabled"] != "enabled":
        raise FenceError("boot recovery reconciler is not enabled")
    return {
        "restart_policies": policies,
        "boot_units": unit_proof,
        "expiry_timer": {timer: timer_state},
        "boot_reconciler": {
            RECOVERY_RECONCILE_SERVICE: reconciler_state,
        },
    }


def _require_recovery_owner(
    state: Mapping[str, Any],
    *,
    source_run_id: str,
    run_id: str,
) -> None:
    if not (
        state.get("source_run_id") == source_run_id
        and state.get("run_id") == run_id
        and state.get("recovery_run_id") == run_id
        and run_id in (state.get("recovery_attempts") or [])
    ):
        raise FenceError("canonical fence is not owned by this recovery attempt")


def _reconcile_orphaned_recovery(
    host: Host,
    *,
    source_run_id: str,
    run_id: str,
    state_path: Path,
) -> None:
    state = _load_state(state_path)
    if state.get("phase") == "unsafe_fenced":
        return
    if state.get("source_run_id") != source_run_id:
        raise FenceError("unsafe fence belongs to another source run")
    if state.get("phase") == "restored":
        raise FenceError("restored recovery cannot be replaced")
    deadline = float(state.get("recovery_deadline_epoch") or 0)
    if time.time() < deadline:
        raise FenceError("another recovery attempt still owns an active lease")
    state = _bind_starting_recovery_sidecars_for_refence(host, state_path)
    if _remove_partial_owned_recovery_generation(
        host,
        state,
        state_path=state_path,
    ):
        return
    _assert_recovery_container_ownership(host, state)
    quiesce_unsafe(host, run_id=run_id, state_path=state_path)


def _remove_fixed_sidecars_for_recovery(
    host: Host,
    state: dict[str, Any],
    *,
    state_path: Path,
) -> dict[str, str]:
    """Write-ahead, stop, and remove only owned fixed-name sidecars."""

    raw_plan = state.get("recovery_sidecar_removal")
    if raw_plan is not None and not isinstance(raw_plan, dict):
        raise FenceError("recovery sidecar removal intent is invalid")
    plan = raw_plan if isinstance(raw_plan, dict) else None
    expected_services = dict(CANONICAL_SIDECARS)
    present = {
        name
        for name, _service in CANONICAL_SIDECARS
        if not _named_container_absent_exact(host, name)
    }
    recorded: dict[str, str] = {}
    projects: dict[str, str] = {}
    if plan:
        recorded = {
            str(name): str(identity)
            for name, identity in dict(plan.get("container_ids") or {}).items()
        }
        projects = {
            str(name): str(project)
            for name, project in dict(plan.get("projects") or {}).items()
        }
        if (
            plan.get("removal_phase") not in {"planned", "removed"}
            or set(recorded) != set(projects)
            or set(recorded) - set(expected_services)
            or any(not value for value in (*recorded.values(), *projects.values()))
            or not present <= set(recorded)
        ):
            raise FenceError("recovery sidecar removal intent is invalid")
    if not present:
        if plan and plan.get("removal_phase") != "removed":
            for name, identity in recorded.items():
                if not _container_absent_exact(host, identity):
                    raise FenceError(
                        f"removed recovery sidecar still exists: {name}"
                    )
            plan["removal_phase"] = "removed"
            _atomic_json(state_path, state)
        return recorded

    inspections = {name: host.container_info(name) for name in present}
    actual: dict[str, str] = {}
    actual_projects: dict[str, str] = {}
    allowed_projects = {CANONICAL_COMPOSE_PROJECT}
    recovery_project = str(state.get("recovery_project_name", ""))
    if recovery_project:
        allowed_projects.add(recovery_project)
    for name, info in inspections.items():
        _assert_sidecar_nonwriter(name, info, host.volume_dir())
        labels = info.get("Config", {}).get("Labels", {}) or {}
        project = str(labels.get("com.docker.compose.project", ""))
        identity = str(info.get("Id", ""))
        if (
            not identity
            or project not in allowed_projects
            or labels.get("com.docker.compose.service")
            != expected_services[name]
        ):
            raise FenceError(f"recovery sidecar ownership is invalid: {name}")
        actual[name] = identity
        actual_projects[name] = project
    if not plan:
        state["recovery_sidecar_removal"] = {
            "container_ids": dict(actual),
            "projects": dict(actual_projects),
            "removal_phase": "planned",
        }
        if not state.get("sidecar_restart_policies"):
            state["sidecar_restart_policies"] = {
                name: str(
                    info.get("HostConfig", {})
                    .get("RestartPolicy", {})
                    .get("Name", "")
                )
                for name, info in inspections.items()
            }
        _atomic_json(state_path, state)
        plan = state["recovery_sidecar_removal"]
        recorded = dict(actual)
        projects = dict(actual_projects)
    if (
        plan.get("removal_phase") != "planned"
        or actual != {name: recorded[name] for name in present}
        or actual_projects != {name: projects[name] for name in present}
    ):
        raise FenceError("recovery sidecar removal intent changed")
    for name in set(recorded) - present:
        if not _container_absent_exact(host, recorded[name]):
            raise FenceError(f"removed recovery sidecar still exists: {name}")

    _set_restart_no(host, inspections)
    host.run(["docker", "stop", *(actual[name] for name in sorted(actual))])
    for name, identity in actual.items():
        info = host.container_info(name)
        if info.get("State", {}).get("Running"):
            raise FenceError(f"recovery sidecar did not stop: {name}")
        if host.container_restart_policy(identity) != "no":
            raise FenceError(f"recovery sidecar restart fence drifted: {name}")
    host.run(["docker", "rm", *(actual[name] for name in sorted(actual))])
    for name, _service in CANONICAL_SIDECARS:
        if not _named_container_absent_exact(host, name):
            raise FenceError(f"recovery sidecar removal did not converge: {name}")
    plan["removal_phase"] = "removed"
    _atomic_json(state_path, state)
    return recorded


def _recovery_sidecar_inspections(
    host: Host,
    state: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    project = str(state.get("recovery_project_name", ""))
    if not project:
        raise FenceError("recovery sidecar project is missing")
    inspections: dict[str, dict[str, Any]] = {}
    for name, service in CANONICAL_SIDECARS:
        if _named_container_absent_exact(host, name):
            raise FenceError(f"recovery sidecar is absent: {name}")
        info = host.container_info(name)
        _assert_sidecar_nonwriter(name, info, host.volume_dir())
        labels = info.get("Config", {}).get("Labels", {}) or {}
        if (
            not str(info.get("Id", ""))
            or labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != service
        ):
            raise FenceError(f"recovery sidecar ownership is invalid: {name}")
        inspections[name] = info
    return inspections


def _capture_recovery_sidecars(
    host: Host,
    state: dict[str, Any],
    *,
    state_path: Path,
    require_all: bool,
) -> dict[str, dict[str, Any]]:
    """Durably bind a full or partial fixed-name recovery-sidecar start."""

    project = str(state.get("recovery_project_name", ""))
    if not project:
        raise FenceError("recovery sidecar project is missing")
    inspections: dict[str, dict[str, Any]] = {}
    invalid_names: list[str] = []
    for name, service in CANONICAL_SIDECARS:
        if _named_container_absent_exact(host, name):
            continue
        info = host.container_info(name)
        labels = info.get("Config", {}).get("Labels", {}) or {}
        if (
            not str(info.get("Id", ""))
            or labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != service
        ):
            invalid_names.append(name)
            continue
        try:
            _assert_sidecar_image(name, info)
        except FenceError:
            invalid_names.append(name)
            continue
        inspections[name] = info
    actual = {
        name: str(info.get("Id", ""))
        for name, info in inspections.items()
    }
    recorded = {
        str(name): str(identity)
        for name, identity in dict(
            state.get("recovery_sidecar_container_ids") or {}
        ).items()
    }
    expected_names = {name for name, _service in CANONICAL_SIDECARS}
    if set(recorded) - expected_names:
        raise FenceError("recovery sidecar identities are invalid")
    for name, identity in recorded.items():
        if name in actual and actual[name] != identity:
            raise FenceError("recovery sidecar identities changed")
        if name not in actual and not _container_absent_exact(host, identity):
            raise FenceError("recovery sidecar identities changed")
    combined = {**recorded, **actual}
    if combined:
        state["recovery_sidecar_container_ids"] = combined
        _atomic_json(state_path, state)
    mount_invalid: list[str] = []
    for name, info in inspections.items():
        try:
            _assert_sidecar_nonwriter(name, info, host.volume_dir())
        except FenceError:
            mount_invalid.append(name)
    if invalid_names or mount_invalid:
        raise FenceError("recovery sidecar ownership or mounts are invalid")
    if require_all and set(inspections) != expected_names:
        raise FenceError("recovery sidecar inventory is incomplete")
    if not inspections and require_all:
        raise FenceError("recovery sidecar fleet is absent")
    return inspections


def _bind_starting_recovery_sidecars_for_refence(
    host: Host,
    state_path: Path,
) -> dict[str, Any]:
    """Bind the exact created subset after interruption before ID capture."""

    state = _load_state(state_path)
    if state.get("phase") != "recovery_sidecars_starting":
        return state
    try:
        _capture_recovery_sidecars(
            host,
            state,
            state_path=state_path,
            require_all=False,
        )
    except FenceError:
        pass
    return _load_state(state_path)


def _recorded_recovery_sidecars(
    host: Host,
    state: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    recorded = {
        str(name): str(identity)
        for name, identity in dict(
            state.get("recovery_sidecar_container_ids") or {}
        ).items()
    }
    if not recorded:
        return {}
    if set(recorded) - {name for name, _service in CANONICAL_SIDECARS}:
        raise FenceError("recovery sidecar identities are invalid")
    project = str(state.get("recovery_project_name", ""))
    if not project:
        raise FenceError("recovery sidecar project is missing")
    inspections: dict[str, dict[str, Any]] = {}
    allow_unrecorded = state.get("phase") == "recovery_sidecars_starting"
    for name, service in CANONICAL_SIDECARS:
        if name not in recorded:
            if not allow_unrecorded and not _named_container_absent_exact(host, name):
                raise FenceError(f"unexpected recovery sidecar appeared: {name}")
            continue
        if _named_container_absent_exact(host, name):
            if not _container_absent_exact(host, recorded[name]):
                raise FenceError(f"recovery sidecar name changed: {name}")
            continue
        info = host.container_info(name)
        labels = info.get("Config", {}).get("Labels", {}) or {}
        if (
            str(info.get("Id", "")) != recorded[name]
            or labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != service
        ):
            raise FenceError(f"recovery sidecar identity changed: {name}")
        inspections[name] = info
    return inspections


def _recorded_recovery_sidecars_by_id(
    host: Host,
    state: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Resolve previously proved recovery sidecars without trusting names."""

    recorded = {
        str(name): str(identity)
        for name, identity in dict(
            state.get("recovery_sidecar_container_ids") or {}
        ).items()
    }
    expected = dict(CANONICAL_SIDECARS)
    if (
        not recorded
        or set(recorded) - set(expected)
        or any(not identity for identity in recorded.values())
    ):
        raise FenceError("recovery sidecar identities are invalid")
    project = str(state.get("recovery_project_name", ""))
    if not project:
        raise FenceError("recovery sidecar project is missing")
    inspections: dict[str, dict[str, Any]] = {}
    for name, identity in recorded.items():
        if _container_absent_exact(host, identity):
            continue
        info = host.container_info(identity)
        labels = info.get("Config", {}).get("Labels", {}) or {}
        if (
            str(info.get("Id", "")) != identity
            or labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != expected[name]
        ):
            raise FenceError(f"recovery sidecar exact ID is invalid: {name}")
        inspections[name] = info
    return inspections


def _remove_recorded_recovery_sidecars(
    host: Host,
    state: dict[str, Any],
    *,
    state_path: Path,
) -> dict[str, str]:
    recorded = {
        str(name): str(identity)
        for name, identity in dict(
            state.get("recovery_sidecar_container_ids") or {}
        ).items()
    }
    if not recorded:
        return {}
    inspections = _recorded_recovery_sidecars(host, state)
    if inspections:
        for name, info in inspections.items():
            _assert_sidecar_nonwriter(name, info, host.volume_dir())
        _set_restart_no(host, inspections)
        host.run(
            [
                "docker",
                "stop",
                *(str(info.get("Id", "")) for info in inspections.values()),
            ]
        )
        for name, info in inspections.items():
            identity = str(info.get("Id", ""))
            if _container_running_exact(host, identity):
                raise FenceError(f"recovery sidecar did not stop: {name}")
            if host.container_restart_policy(identity) != "no":
                raise FenceError(f"recovery sidecar restart fence drifted: {name}")
        host.run(
            [
                "docker",
                "rm",
                *(str(info.get("Id", "")) for info in inspections.values()),
            ]
        )
    for name, identity in recorded.items():
        if not _container_absent_exact(host, identity):
            raise FenceError(f"recovery sidecar survived removal: {name}")
    state["recovery_removed_partial_sidecar_container_ids"] = recorded
    state.pop("recovery_sidecar_container_ids", None)
    _atomic_json(state_path, state)
    return recorded


def _start_recovery_sidecars(
    host: Host,
    state: dict[str, Any],
    *,
    state_path: Path,
) -> dict[str, dict[str, Any]]:
    """Start recovery-owned sidecars with one exact-ID partial-start retry."""

    for attempt in range(1, RECOVERY_SIDECAR_START_ATTEMPTS + 1):
        state["recovery_sidecar_start_attempt"] = attempt
        _atomic_json(state_path, state)
        try:
            host.run(
                [
                    "docker",
                    "compose",
                    "--project-name",
                    str(state["recovery_project_name"]),
                    "--env-file",
                    "/etc/tinyassets/env",
                    "-f",
                    str(RECOVERY_COMPOSE_PATH),
                    "-f",
                    str(RECOVERY_COMPOSE_OVERRIDE_PATH),
                    "up",
                    "-d",
                    "--no-deps",
                    *RECOVERY_SIDECAR_SERVICES,
                ]
            )
            return _capture_recovery_sidecars(
                host,
                state,
                state_path=state_path,
                require_all=True,
            )
        except (FenceError, OSError):
            partial_sidecars = _capture_recovery_sidecars(
                host,
                state,
                state_path=state_path,
                require_all=False,
            )
            if (
                not partial_sidecars
                or attempt == RECOVERY_SIDECAR_START_ATTEMPTS
            ):
                raise
            _remove_recorded_recovery_sidecars(
                host,
                state,
                state_path=state_path,
            )
            continue
    raise FenceError("recovery sidecar start attempts were exhausted")


def recover_unsafe(
    host: Host,
    *,
    source_run_id: str,
    run_id: str,
    image_ref: str,
    revision: str,
    state_path: Path,
    recovery_script_sha256: str = "",
    retire_extra_consumers: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Start one exact admitted fleet in a restart-fenced canary phase."""

    _require_run_id(run_id)
    _reconcile_orphaned_recovery(
        host,
        source_run_id=source_run_id,
        run_id=run_id,
        state_path=state_path,
    )
    state, image_ref, revision = _validate_unsafe_recovery_source(
        host,
        source_run_id=source_run_id,
        image_ref=image_ref,
        revision=revision,
        state_path=state_path,
        retire_extra_consumers=retire_extra_consumers,
    )
    attempts = list(state.get("recovery_attempts") or [])
    if run_id in attempts:
        raise FenceError("recovery attempt identity was already used")
    removed_partial_target: dict[str, str] = {}
    if state.get("stopped_fleet_removal"):
        removed_stopped = _remove_recorded_stopped_fleet_for_recovery(
            host,
            state,
            state_path=state_path,
        )
    else:
        removed_partial_target = _remove_partial_canonical_target_for_recovery(
            host,
            state,
            state_path=state_path,
        )
        if removed_partial_target:
            state["recovery_removed_partial_target_ids"] = removed_partial_target
            _atomic_json(state_path, state)
        removed_stopped = _remove_recorded_stopped_fleet_for_recovery(
            host,
            state,
            state_path=state_path,
        )
    if removed_stopped:
        state["recovery_removed_stopped_container_ids"] = removed_stopped
        _atomic_json(state_path, state)
    removed_recovery_sidecars = _remove_recorded_recovery_sidecars(
        host,
        state,
        state_path=state_path,
    )
    if removed_recovery_sidecars:
        state["recovery_removed_partial_sidecar_container_ids"] = (
            removed_recovery_sidecars
        )
        _atomic_json(state_path, state)
    # Restore the sidecars whenever cleanup fenced them, not only when it
    # recorded an explicit handoff.
    #
    # The recovery canary probes the PUBLIC url, which only resolves through
    # the `tinyassets-tunnel` sidecar. Gating restoration on `sidecar_handoff`
    # alone means a cleanup that fenced the sidecars WITHOUT writing that key
    # leaves recovery unable to satisfy its own success check: the fleet comes
    # up healthy, the daemon serves `POST /mcp -> 200` on loopback, the public
    # probe 502s because nothing is fronting it, and the failing probe
    # re-fences the fleet. Forever.
    #
    # Observed live 2026-08-05 (recovery 31050887125): all five containers
    # `Up (healthy)`, daemon logging 200s, NO tinyassets-tunnel in `docker ps
    # -a`, and state carrying `sidecar_restart_policies` but no
    # `sidecar_handoff`.
    #
    # `sidecar_restart_policies` is the record that cleanup fenced them, and
    # it is exactly what the restore path below consumes.
    recover_sidecars = "sidecar_handoff" in state or bool(
        state.get("sidecar_restart_policies")
    )
    if recover_sidecars:
        sidecar_policies = {
            str(name): str(policy)
            for name, policy in dict(
                state.get("sidecar_restart_policies") or {}
            ).items()
        }
        expected_sidecar_names = {
            name for name, _service in CANONICAL_SIDECARS
        }
        if set(sidecar_policies) - expected_sidecar_names or any(
            policy not in {"always", "unless-stopped", "on-failure", "no"}
            for policy in sidecar_policies.values()
        ):
            raise FenceError("saved sidecar restart policies are invalid")
        sidecar_policies = {
            name: "unless-stopped" for name in expected_sidecar_names
        }
        state["sidecar_restart_policies"] = sidecar_policies
        _atomic_json(state_path, state)
        removed_sidecars = _remove_fixed_sidecars_for_recovery(
            host,
            state,
            state_path=state_path,
        )
        state["recovery_removed_sidecar_container_ids"] = removed_sidecars
        _atomic_json(state_path, state)
    attempts.append(run_id)
    state["source_run_id"] = source_run_id
    state["run_id"] = run_id
    state["recovery_run_id"] = run_id
    state["recovery_attempts"] = attempts
    state["recovery_deadline_epoch"] = time.time() + RECOVERY_LEASE_SECONDS
    state["recovery_project_name"] = _recovery_project_name(run_id)
    names_before_start = set(host.volume_container_names())
    state["recovery_prestart_container_ids"] = (
        {
            name: str(info.get("Id", ""))
            for name, info in _exact_inspections(host).items()
        }
        if names_before_start
        else {}
    )
    if recovery_script_sha256:
        state["recovery_script_sha256"] = recovery_script_sha256
    state["phase"] = "recovery_planned"
    _atomic_json(state_path, state)
    try:
        expiry_unit = _arm_recovery_expiry(
            host,
            source_run_id=source_run_id,
            run_id=run_id,
            state_path=state_path,
        )
        state["recovery_expiry_unit"] = expiry_unit
        state["phase"] = "recovery_starting"
        _atomic_json(state_path, state)
        host.run(
            [
                "docker",
                "compose",
                "--project-name",
                str(state["recovery_project_name"]),
                "--env-file",
                "/etc/tinyassets/env",
                "-f",
                str(RECOVERY_COMPOSE_PATH),
                "-f",
                str(RECOVERY_COMPOSE_OVERRIDE_PATH),
                "up",
                "-d",
                "--no-deps",
                *RECOVERY_SERVICES,
            ]
        )
        inspections = _exact_inspections(host)
        state["recovery_container_ids"] = {
            name: str(info.get("Id", ""))
            for name, info in inspections.items()
        }
        _atomic_json(state_path, state)
        _assert_recovery_container_ownership(host, state)
        state["recovery_restart_policy_proof"] = _set_restart_no(
            host,
            inspections,
        )
        state["phase"] = "recovery_pending_canary"
        _atomic_json(state_path, state)
        evidence: dict[str, Any] | None = None
        last_error = ""
        for attempt in range(30):
            try:
                evidence = prove(
                    host,
                    image_ref=image_ref,
                    revision=revision,
                    run_id=run_id,
                    state_path=state_path,
                )
                break
            except FenceError as exc:
                last_error = str(exc)
                if attempt < 29:
                    time.sleep(2)
        if evidence is None:
            raise FenceError(f"exact fleet did not converge: {last_error}")
        if recover_sidecars:
            state = _load_state(state_path)
            state["phase"] = "recovery_sidecars_starting"
            _atomic_json(state_path, state)
            sidecars = _start_recovery_sidecars(
                host,
                state,
                state_path=state_path,
            )
            state["recovery_sidecar_restart_policy_proof"] = _set_restart_no(
                host,
                sidecars,
            )
            if any(
                not info.get("State", {}).get("Running")
                for info in sidecars.values()
            ):
                raise FenceError("recovery sidecar fleet is not running")
        evidence.update(
            {
                "phase": "recovery_pending_canary",
                "source_run_id": source_run_id,
                "recovery_run_id": run_id,
                "recovery_deadline_epoch": state["recovery_deadline_epoch"],
                "restart_policy_proof": state[
                    "recovery_restart_policy_proof"
                ],
                "recovery_sidecar_container_ids": dict(
                    state.get("recovery_sidecar_container_ids") or {}
                ),
            }
        )
        state["phase"] = "recovery_pending_canary"
        _atomic_json(state_path, state)
        return evidence
    except (FenceError, OSError) as recovery_error:
        try:
            failed_state = _load_state(state_path)
            if failed_state.get("phase") == "recovery_sidecars_starting":
                try:
                    _capture_recovery_sidecars(
                        host,
                        failed_state,
                        state_path=state_path,
                        require_all=False,
                    )
                except FenceError:
                    # A foreign fixed-name container is not ours to mutate.
                    # The writer-first quiesce below records sidecar drift
                    # and never mutates an unproved fixed-name occupant.
                    pass
                failed_state = _load_state(state_path)
            refenced = False
            try:
                _assert_recovery_container_ownership(host, failed_state)
            except FenceError:
                if failed_state.get("recovery_sidecar_container_ids"):
                    # Recovery-owned sidecars are durably ID-bound. If one
                    # unexpectedly became a volume consumer, the generic
                    # emergency fence must stop it with every other writer,
                    # but it remains unremoved for later inspection.
                    quiesce_unsafe(
                        host,
                        run_id=run_id,
                        state_path=state_path,
                    )
                    refenced = True
                elif _restore_prestart_fence_without_mutation(
                    host,
                    failed_state,
                    state_path=state_path,
                ):
                    raise FenceError(
                        f"recovery failed and was re-fenced without mutation: "
                        f"{recovery_error}"
                    ) from recovery_error
                else:
                    raise
            if not refenced:
                quiesce_unsafe(host, run_id=run_id, state_path=state_path)
        except (FenceError, OSError) as refence_error:
            if "was re-fenced without mutation" in str(refence_error):
                raise refence_error
            raise FenceError(
                f"recovery failed: {recovery_error}; "
                f"re-fence also failed: {refence_error}"
            ) from recovery_error
        raise FenceError(
            f"recovery failed and was re-fenced: {recovery_error}"
        ) from recovery_error


def _restore_restart_policies(
    host: Host,
    state: dict[str, Any],
    *,
    state_path: Path,
) -> dict[str, str]:
    proof = dict(state.get("restart_policy_restore_proof") or {})
    policies = {
        **dict(state["old_restart_policies"]),
        **dict(state.get("sidecar_restart_policies") or {}),
    }
    recorded_sidecars = {
        str(name): str(identity)
        for name, identity in dict(
            state.get("recovery_sidecar_container_ids") or {}
        ).items()
    }
    for name, policy in policies.items():
        info = host.container_info(name)
        identity = str(info.get("Id", ""))
        if not identity:
            raise FenceError(f"container identity is unavailable: {name}")
        if name in recorded_sidecars and recorded_sidecars[name] != identity:
            raise FenceError(f"recovery sidecar identity changed: {name}")
        host.run(["docker", "update", f"--restart={policy}", identity])
        actual = host.container_restart_policy(identity)
        if actual != policy:
            raise FenceError(
                f"restart policy restoration did not persist: "
                f"{name}={actual}, expected={policy}"
            )
        proof[name] = actual
        state["restart_policy_restore_proof"] = proof
        _atomic_json(state_path, state)
    return proof


def finalize_recovery(
    host: Host,
    *,
    source_run_id: str,
    run_id: str,
    image_ref: str,
    revision: str,
    state_path: Path,
) -> dict[str, Any]:
    """Commit a canary-accepted recovery and restore its saved boot posture."""

    state = _load_state(state_path)
    _require_recovery_owner(
        state,
        source_run_id=source_run_id,
        run_id=run_id,
    )
    if state.get("phase") == "restored":
        return prove(
            host,
            image_ref=image_ref,
            revision=revision,
            run_id=run_id,
            state_path=state_path,
        )
    if state.get("phase") not in {
        "recovery_pending_canary",
        "canary_accepted",
        "finalizing",
    }:
        raise FenceError("recovery is not ready for canary finalization")
    try:
        evidence = prove(
            host,
            image_ref=image_ref,
            revision=revision,
            run_id=run_id,
            state_path=state_path,
        )
        state = _load_state(state_path)
        fence_proof = _prove_recovery_fences(host, state)
        state["pre_finalize_fence_proof"] = fence_proof
        _atomic_json(state_path, state)
        state["canary_accepted_epoch"] = state.get(
            "canary_accepted_epoch"
        ) or time.time()
        state["phase"] = "canary_accepted"
        _atomic_json(state_path, state)
        state["phase"] = "finalizing"
        _atomic_json(state_path, state)
        restart_proof = _restore_restart_policies(
            host,
            state,
            state_path=state_path,
        )
        restored = restore_if_safe(
            host,
            image_ref=image_ref,
            revision=revision,
            run_id=run_id,
            state_path=state_path,
        )
        _cancel_recovery_expiry(
            host,
            str(state.get("recovery_expiry_unit", "")),
        )
        restored.update(
            {
                "source_run_id": source_run_id,
                "recovery_run_id": run_id,
                "canary_accepted_epoch": state["canary_accepted_epoch"],
                "restart_policy_restore_proof": restart_proof,
                "pre_finalize_evidence": evidence,
                "pre_finalize_fence_proof": fence_proof,
            }
        )
        return restored
    except (FenceError, OSError) as finalize_error:
        try:
            _assert_recovery_container_ownership(
                host,
                _load_state(state_path),
            )
            quiesce_unsafe(host, run_id=run_id, state_path=state_path)
        except (FenceError, OSError) as refence_error:
            raise FenceError(
                f"recovery finalization failed: {finalize_error}; "
                f"re-fence also failed: {refence_error}"
            ) from finalize_error
        raise FenceError(
            f"recovery finalization failed and was re-fenced: {finalize_error}"
        ) from finalize_error


def refence_recovery(
    host: Host,
    *,
    source_run_id: str,
    run_id: str,
    state_path: Path,
) -> dict[str, Any]:
    """Re-fence only a generation durably owned by this recovery attempt."""

    _require_run_id(source_run_id)
    _require_run_id(run_id)
    state = _load_state(state_path)
    _require_recovery_owner(
        state,
        source_run_id=source_run_id,
        run_id=run_id,
    )
    if state.get("phase") == "restored":
        raise FenceError("restored recovery cannot be re-fenced")
    state = _bind_starting_recovery_sidecars_for_refence(host, state_path)
    _assert_recovery_container_ownership(host, state)
    return quiesce_unsafe(host, run_id=run_id, state_path=state_path)


def reconcile_recovery_on_boot(
    host: Host,
    *,
    state_path: Path,
) -> dict[str, Any]:
    """Automatically close an interrupted recovery after a host reboot."""

    if not state_path.is_file():
        return {"phase": "not_applicable", "safe": True}
    state = _load_state(state_path)
    phase = str(state.get("phase", ""))
    if phase in {"restored", "unsafe_fenced"}:
        return {
            "phase": phase,
            "safe": phase == "restored",
            "writers_fenced": phase == "unsafe_fenced",
        }
    if phase == "restoring_quiesced":
        run_id = str(state.get("run_id", ""))
        _require_state_run(state, run_id)
        return quiesce_unsafe(
            host,
            run_id=run_id,
            state_path=state_path,
        )
    source_run_id = str(state.get("source_run_id", ""))
    run_id = str(state.get("recovery_run_id") or state.get("run_id") or "")
    _require_recovery_owner(
        state,
        source_run_id=source_run_id,
        run_id=run_id,
    )
    state = _bind_starting_recovery_sidecars_for_refence(host, state_path)
    _assert_recovery_container_ownership(host, state)
    return quiesce_unsafe(host, run_id=run_id, state_path=state_path)


def expire_recovery(
    host: Host,
    *,
    source_run_id: str,
    run_id: str,
    state_path: Path,
) -> dict[str, Any]:
    """Re-fence an unaccepted recovery after its durable lease expires."""

    state = _load_state(state_path)
    _require_recovery_owner(
        state,
        source_run_id=source_run_id,
        run_id=run_id,
    )
    if state.get("phase") == "restored":
        return {
            "owner": TASK_OWNER,
            "phase": state.get("phase"),
            "expired": False,
            "safe": True,
        }
    deadline = float(state.get("recovery_deadline_epoch") or 0)
    if time.time() < deadline:
        raise FenceError("recovery lease has not expired")
    evidence = refence_recovery(
        host,
        source_run_id=source_run_id,
        run_id=run_id,
        state_path=state_path,
    )
    evidence["expired"] = True
    return evidence


def _write_optional(path: str | None, payload: Mapping[str, Any]) -> None:
    if path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_json_bytes(payload) + b"\n")


@contextmanager
def _operation_lock(
    path: Path,
    *,
    timeout_seconds: float = LOCK_TIMEOUT_SECONDS,
) -> Iterable[None]:
    """Serialize every host-side fence observation and mutation."""

    if fcntl is None:
        raise FenceError("host operation lock primitive is unavailable")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FenceError(
                        f"host operation lock timed out after {timeout_seconds:g}s"
                    ) from exc
                time.sleep(min(0.1, remaining))
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in (
        "preflight",
        "prove",
        "post-canary",
        "restore-if-safe",
        "restore-quiesced",
    ):
        command = subparsers.add_parser(name)
        command.add_argument("--image-ref", required=True)
        command.add_argument("--revision", required=True)
        command.add_argument("--run-id", required=True)
    prepare = subparsers.add_parser("prepare-deploy")
    prepare.add_argument("--image-ref", required=True)
    prepare.add_argument("--run-id", required=True)
    observe = subparsers.add_parser("observe")
    observe.add_argument("--image-ref")
    status = subparsers.add_parser("status")
    status.add_argument("--run-id", required=True)
    quiesce = subparsers.add_parser("quiesce-unsafe")
    quiesce.add_argument("--run-id", required=True)
    recover = subparsers.add_parser("recover-unsafe")
    recover.add_argument("--source-run-id", required=True)
    recover.add_argument("--run-id", required=True)
    recover.add_argument("--image-ref", required=True)
    recover.add_argument("--revision", required=True)
    recover.add_argument("--expected-script-sha256", required=True)
    recover.add_argument(
        "--retire-extra-consumer",
        action="append",
        default=[],
        metavar="CONTAINER_NAME",
        help=(
            "Retire one EXACT recorded extra volume consumer that is already "
            "stopped. Repeatable. Never matches an expected fleet container, "
            "never a wildcard, and re-checks the live container state."
        ),
    )
    finalize = subparsers.add_parser("finalize-recovery")
    finalize.add_argument("--source-run-id", required=True)
    finalize.add_argument("--run-id", required=True)
    finalize.add_argument("--image-ref", required=True)
    finalize.add_argument("--revision", required=True)
    refence = subparsers.add_parser("refence-recovery")
    refence.add_argument("--source-run-id", required=True)
    refence.add_argument("--run-id", required=True)
    expire = subparsers.add_parser("expire-recovery")
    expire.add_argument("--source-run-id", required=True)
    expire.add_argument("--run-id", required=True)
    subparsers.add_parser("reconcile-recovery-on-boot")
    guard = subparsers.add_parser("guard-host-mutation")
    guard.add_argument(
        "--command-timeout",
        type=int,
        default=HOST_COMMAND_TIMEOUT_SECONDS,
    )
    guard.add_argument("mutation_argv", nargs=argparse.REMAINDER)
    return parser


def _execute(args: argparse.Namespace, host: Host) -> dict[str, Any]:
    if args.command == "preflight":
        return preflight(
            host,
            image_ref=args.image_ref,
            target_revision=args.revision,
            run_id=args.run_id,
            state_path=args.state_path,
        )
    if args.command == "prepare-deploy":
        return prepare_deploy(
            host,
            image_ref=args.image_ref,
            run_id=args.run_id,
            state_path=args.state_path,
        )
    if args.command == "prove":
        return prove(
            host,
            image_ref=args.image_ref,
            revision=args.revision,
            run_id=args.run_id,
            state_path=args.state_path,
        )
    if args.command == "post-canary":
        return post_canary(
            host,
            image_ref=args.image_ref,
            revision=args.revision,
            run_id=args.run_id,
            state_path=args.state_path,
        )
    if args.command == "restore-if-safe":
        return restore_if_safe(
            host,
            image_ref=args.image_ref,
            revision=args.revision,
            run_id=args.run_id,
            state_path=args.state_path,
        )
    if args.command == "restore-quiesced":
        return restore_quiesced(
            host,
            image_ref=args.image_ref,
            revision=args.revision,
            run_id=args.run_id,
            state_path=args.state_path,
        )
    if args.command == "observe":
        return observe_fleet(host, expected_image_ref=args.image_ref)
    if args.command == "quiesce-unsafe":
        return quiesce_unsafe(
            host,
            run_id=args.run_id,
            state_path=args.state_path,
        )
    if args.command == "recover-unsafe":
        recovery_script_sha256 = _prove_recovery_entrypoint(
            args.expected_script_sha256
        )
        return recover_unsafe(
            host,
            source_run_id=args.source_run_id,
            run_id=args.run_id,
            image_ref=args.image_ref,
            revision=args.revision,
            state_path=args.state_path,
            recovery_script_sha256=recovery_script_sha256,
            retire_extra_consumers=tuple(args.retire_extra_consumer),
        )
    if args.command == "finalize-recovery":
        return finalize_recovery(
            host,
            source_run_id=args.source_run_id,
            run_id=args.run_id,
            image_ref=args.image_ref,
            revision=args.revision,
            state_path=args.state_path,
        )
    if args.command == "refence-recovery":
        return refence_recovery(
            host,
            source_run_id=args.source_run_id,
            run_id=args.run_id,
            state_path=args.state_path,
        )
    if args.command == "expire-recovery":
        return expire_recovery(
            host,
            source_run_id=args.source_run_id,
            run_id=args.run_id,
            state_path=args.state_path,
        )
    if args.command == "reconcile-recovery-on-boot":
        return reconcile_recovery_on_boot(
            host,
            state_path=args.state_path,
        )
    if args.command == "guard-host-mutation":
        mutation_argv = list(args.mutation_argv)
        separator_present = mutation_argv[:1] == ["--"]
        if separator_present:
            mutation_argv.pop(0)
        if (
            (separator_present and not mutation_argv)
            or args.command_timeout < 1
            or args.command_timeout > 300
        ):
            raise FenceError("guarded host mutation command is invalid")
        guard_evidence = guard_host_mutation(host, state_path=args.state_path)
        if not mutation_argv:
            return {**guard_evidence, "mutation_completed": False}
        output = host.run(
            [
                "timeout",
                "--kill-after=2s",
                f"{args.command_timeout}s",
                *mutation_argv,
            ],
            timeout_seconds=args.command_timeout + 5,
        )
        _echo_guarded_output(output, getattr(host, "last_stderr", ""))
        return {
            **guard_evidence,
            "mutation_completed": True,
            "output_present": bool(output),
        }
    return fence_status(
        host,
        run_id=args.run_id,
        state_path=args.state_path,
    )


def main(argv: Sequence[str] | None = None, *, host: Host | None = None) -> int:
    args = _parser().parse_args(argv)
    host = host or Host()
    try:
        with _operation_lock(args.lock_path):
            result = _execute(args, host)
        _write_optional(args.evidence, result)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except FenceError as exc:
        failure = {
            "schema_version": 1,
            "owner": TASK_OWNER,
            "safe": False,
            "error": str(exc),
        }
        if args.state_path.is_file():
            try:
                state = _load_state(args.state_path)
                command_run_id = getattr(args, "run_id", "")
                if state.get("run_id") == command_run_id:
                    failure.update(
                        {
                            "phase": state.get("phase"),
                            "cutover_started": state.get("phase") != "restored",
                            "previous_image_ref": state.get(
                                "previous_image_ref", ""
                            ),
                            "previous_revision": state.get(
                                "previous_revision", ""
                            ),
                            "old_container_ids": state.get(
                                "old_container_ids", {}
                            ),
                            "last_failed_observation": state.get(
                                "last_failed_observation", {}
                            ),
                        }
                    )
                else:
                    failure["stale_state_ignored"] = True
            except FenceError:
                failure["state_error"] = "durable fence state unreadable"
        _write_optional(args.evidence, failure)
        print(json.dumps(failure, sort_keys=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
