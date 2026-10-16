"""Focused tests for the transitional retire-cheat-loop task 2.1 deploy fence."""

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

import pytest

import scripts.retire_cheat_loop_deploy_fence as fence
from scripts.retire_cheat_loop_deploy_fence import (
    DAEMON_SERVICE,
    EXPECTED_CONTAINERS,
    RESTART_RACER_UNITS,
    FenceError,
    _remove_recorded_stopped_fleet_for_recovery,
    _validate_unsafe_recovery_source,
    expire_recovery,
    fence_status,
    finalize_recovery,
    inventory_queue_risk,
    post_canary,
    preflight,
    prepare_deploy,
    prove,
    quiesce_unsafe,
    receipt_snapshot,
    recover_unsafe,
    refence_recovery,
    resolve_receipt_store,
    restore_if_safe,
    safe_fleet_matches,
)

RUN_ID = "test-run-1"


def _create_receipt_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE wiki_trigger_attempts (
                trigger_attempt_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                request_kind TEXT NOT NULL,
                request_page TEXT NOT NULL,
                status TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                goal_id TEXT,
                branch_def_id TEXT,
                queued_at TEXT,
                run_id TEXT,
                dispatcher_request_id TEXT,
                error_class TEXT,
                error_message TEXT
            );
            """
        )


def test_absent_receipt_snapshot_has_defined_read_only_shape(tmp_path: Path):
    assert receipt_snapshot(tmp_path / "missing.db") == {
        "exists": False,
        "quick_check": ["absent"],
        "schema": [],
        "row_count": 0,
        "status_counts": {},
        "max_attempted_at": None,
        "logical_digest": (
            "e3b0c44298fc1c149afbf4c8996fb924"
            "27ae41e4649b934ca495991b7852b855"
        ),
    }


def test_receipt_snapshot_records_schema_counts_max_and_logical_digest(tmp_path: Path):
    path = tmp_path / "wiki_trigger_attempts.db"
    _create_receipt_db(path)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            """
            INSERT INTO wiki_trigger_attempts (
                trigger_attempt_id, request_id, request_kind, request_page,
                status, attempted_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("attempt-2", "BUG-2", "bug", "pages/bugs/two.md", "queued", "2026-02-02"),
                ("attempt-1", "BUG-1", "bug", "pages/bugs/one.md", "pending", "2026-01-01"),
            ],
        )

    first = receipt_snapshot(path)
    second = receipt_snapshot(path)

    assert first == second
    assert first["quick_check"] == ["ok"]
    assert first["row_count"] == 2
    assert first["status_counts"] == {"pending": 1, "queued": 1}
    assert first["max_attempted_at"] == "2026-02-02"
    assert first["schema"]
    assert len(first["logical_digest"]) == 64


def test_receipt_snapshot_rejects_corrupt_or_unexpected_schema(tmp_path: Path):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not sqlite")
    with pytest.raises(FenceError, match="receipt sqlite unreadable"):
        receipt_snapshot(corrupt)

    wrong = tmp_path / "wrong.db"
    with sqlite3.connect(wrong) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(FenceError, match="exact receipt schema"):
        receipt_snapshot(wrong)


def test_queue_inventory_finds_v1_pending_and_running_only(tmp_path: Path):
    universe = tmp_path / "universes" / "one"
    universe.mkdir(parents=True)
    (universe / "branch_tasks.json").write_text(
        json.dumps(
            [
                {
                    "branch_task_id": "pending",
                    "request_type": "bug_investigation",
                    "status": "pending",
                },
                {
                    "branch_task_id": "running",
                    "request_type": "bug_investigation",
                    "status": "running",
                },
                {
                    "branch_task_id": "done",
                    "request_type": "bug_investigation",
                    "status": "succeeded",
                },
                {
                    "branch_task_id": "generic",
                    "request_type": "branch_run",
                    "status": "pending",
                },
            ]
        ),
        encoding="utf-8",
    )

    assert inventory_queue_risk(tmp_path) == [
        {
            "id": "pending",
            "status": "pending",
            "store": "universes/one/branch_tasks.json",
            "version": "v1",
        },
        {
            "id": "running",
            "status": "running",
            "store": "universes/one/branch_tasks.json",
            "version": "v1",
        },
    ]


def test_queue_inventory_v2_joins_authoritative_user_request_type(tmp_path: Path):
    universe = tmp_path / "universes" / "two"
    universe.mkdir(parents=True)
    db = universe / ".tinyassets.db"
    with sqlite3.connect(db) as connection:
        connection.executescript(
            """
            CREATE TABLE user_requests (
                request_id TEXT PRIMARY KEY,
                request_type TEXT NOT NULL
            );
            CREATE TABLE branch_tasks_v2 (
                branch_task_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO user_requests VALUES (?, ?)",
            [
                ("req-p", "bug_investigation"),
                ("req-r", "bug_investigation"),
                ("req-c", "bug_investigation"),
                ("req-g", "branch_run"),
            ],
        )
        connection.executemany(
            "INSERT INTO branch_tasks_v2 VALUES (?, ?, ?)",
            [
                ("task-p", "req-p", "pending"),
                ("task-r", "req-r", "running"),
                ("task-c", "req-c", "cancel_requested"),
                ("task-g", "req-g", "pending"),
            ],
        )

    risks = inventory_queue_risk(tmp_path)
    assert [(row["id"], row["status"]) for row in risks] == [
        ("task-c", "cancel_requested"),
        ("task-p", "pending"),
        ("task-r", "running"),
    ]
    assert all(row["version"] == "v2" for row in risks)


def test_queue_inventory_v2_fails_closed_on_live_orphan_request(tmp_path: Path):
    db = tmp_path / ".tinyassets.db"
    with sqlite3.connect(db) as connection:
        connection.executescript(
            """
            CREATE TABLE user_requests (
                request_id TEXT PRIMARY KEY,
                request_type TEXT NOT NULL
            );
            CREATE TABLE branch_tasks_v2 (
                branch_task_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            INSERT INTO branch_tasks_v2 VALUES ('orphan', 'missing', 'running');
            """
        )

    with pytest.raises(FenceError, match="missing authoritative request type"):
        inventory_queue_risk(tmp_path)


def test_queue_inventory_v2_runs_foreign_key_check(tmp_path: Path):
    db = tmp_path / ".tinyassets.db"
    with sqlite3.connect(db) as connection:
        connection.executescript(
            """
            CREATE TABLE user_requests (
                request_id TEXT PRIMARY KEY,
                request_type TEXT NOT NULL
            );
            CREATE TABLE branch_tasks_v2 (
                branch_task_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL
                    REFERENCES user_requests(request_id),
                status TEXT NOT NULL
            );
            INSERT INTO branch_tasks_v2 VALUES ('orphan', 'missing', 'running');
            """
        )

    with pytest.raises(FenceError, match="foreign key check failed"):
        inventory_queue_risk(tmp_path)


def test_queue_inventory_fails_closed_on_unreadable_or_partial_store(tmp_path: Path):
    (tmp_path / "branch_tasks.json").write_text("{", encoding="utf-8")
    with pytest.raises(FenceError, match="v1 queue unreadable"):
        inventory_queue_risk(tmp_path)

    (tmp_path / "branch_tasks.json").unlink()
    with sqlite3.connect(tmp_path / ".tinyassets.db") as connection:
        connection.execute("CREATE TABLE branch_tasks_v2 (branch_task_id TEXT)")
    with pytest.raises(FenceError, match="v2 queue schema incomplete"):
        inventory_queue_risk(tmp_path)


def test_receipt_store_requires_one_shared_volume_and_data_relative_override(tmp_path: Path):
    inspections = {
        name: {
            "Id": f"id-{index}",
            "State": {"Running": True, "Pid": 100 + index},
            "Config": {"Env": ["TINYASSETS_TRIGGER_RECEIPTS_DB=/data/receipts.db"]},
            "Mounts": [
                {
                    "Destination": "/data",
                    "Name": "tinyassets-data",
                    "Source": str(tmp_path),
                }
            ],
        }
        for index, name in enumerate(EXPECTED_CONTAINERS)
    }
    selected = resolve_receipt_store(inspections, tmp_path)
    assert selected.container_path == "/data/receipts.db"
    assert selected.host_path == tmp_path / "receipts.db"

    inspections[EXPECTED_CONTAINERS[0]]["Config"]["Env"] = [
        "TINYASSETS_TRIGGER_RECEIPTS_DB=/tmp/escape.db"
    ]
    with pytest.raises(FenceError, match="outside /data"):
        resolve_receipt_store(inspections, tmp_path)


def test_restart_racer_inventory_includes_all_three_watchdog_families():
    assert set(RESTART_RACER_UNITS) == {
        "daemon-watchdog.timer",
        "daemon-watchdog.service",
        "tinyassets-watchdog.timer",
        "tinyassets-watchdog.service",
        "tinyassets-autoheal.timer",
        "tinyassets-autoheal.service",
    }
    assert DAEMON_SERVICE == "tinyassets-daemon.service"


def test_volume_consumer_inventory_includes_stopped_containers():
    class CaptureHost(fence.Host):
        def __init__(self) -> None:
            self.args: list[str] = []

        def run(
            self,
            args: Any,
            *,
            check: bool = True,
            input_text: str | None = None,
            timeout_seconds: int = fence.HOST_COMMAND_TIMEOUT_SECONDS,
        ) -> str:
            del check, input_text
            self.args = list(args)
            return "tinyassets-daemon\nstopped-extra"

    host = CaptureHost()
    assert host.volume_container_names() == ["stopped-extra", "tinyassets-daemon"]
    assert "-a" in host.args


def test_safe_fleet_requires_exact_writer_set_digest_revision_and_no_old_ids():
    image_ref = "ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "a" * 64
    revision = "b" * 40
    observation = {
        "containers": {
            name: {
                "id": f"new-{index}",
                "running": True,
                "image_ref": image_ref,
                "revision": revision,
            }
            for index, name in enumerate(EXPECTED_CONTAINERS)
        },
        "volume_container_names": list(EXPECTED_CONTAINERS),
        "stray_writer_processes": [],
        "queue_risk": [],
    }
    old_ids = {name: f"old-{index}" for index, name in enumerate(EXPECTED_CONTAINERS)}
    assert safe_fleet_matches(observation, image_ref, revision, old_ids)

    observation["containers"][EXPECTED_CONTAINERS[0]]["revision"] = "c" * 40
    assert not safe_fleet_matches(observation, image_ref, revision, old_ids)


class LifecycleHost:
    def __init__(self, volume_dir: Path) -> None:
        self.volume = volume_dir
        self.old_image_ref = "ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "a" * 64
        self.old_revision = "a" * 40
        self.target_image_ref = (
            "ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "b" * 64
        )
        self.target_revision = "b" * 40
        self.image_identities = {
            "sha256:old": (self.old_image_ref, self.old_revision),
            self.old_image_ref: (self.old_image_ref, self.old_revision),
            self.target_image_ref: (self.target_image_ref, self.target_revision),
            "sha256:target": (self.target_image_ref, self.target_revision),
        }
        self.containers = self._containers("old", "sha256:old", running=True)
        self.units = {
            unit: {
                "active": "active" if unit.endswith(".timer") else "inactive",
                "enabled": "enabled" if unit.endswith(".timer") else "static",
                "load": "loaded",
            }
            for unit in RESTART_RACER_UNITS
        }
        self.units[DAEMON_SERVICE] = {
            "active": "active",
            "enabled": "enabled",
            "load": "loaded",
        }
        self.units[fence.RECOVERY_RECONCILE_SERVICE] = {
            "active": "inactive",
            "enabled": "enabled",
            "load": "loaded",
        }
        self.calls: list[tuple[str, ...]] = []
        self.stubborn_unit: str | None = None
        self.unmask_noop = False
        self.unmask_error = False
        self.container_state_error = False
        self.container_state_override: str | None = None
        self.restart_policy_override: str | None = None
        self.start_installs_target = False
        self.fail_sidecar_compose_after = 0
        self.sidecar_compose_failures_remaining = 0
        self.incomplete_sidecar_compose_successes_remaining = 0
        self.substitute_sidecar_after_inspections = 0
        self.rename_sidecar_before_substitution = False
        self.sidecar_stop_failures_remaining = 0
        self.sidecar_updates_before_failure = -1
        self.missing_container_info_identity: set[str] = set()
        self.foreign_sidecar_compose = False
        self.mixed_sidecar_compose_owned_index: int | None = None
        self.recovery_sidecar_data_mount = False
        self.substitute_sidecars_before_stop = False
        self.interrupt_restart_restore_after = 0
        self.restart_restore_updates = 0

    def _containers(
        self,
        prefix: str,
        image: str,
        *,
        running: bool,
    ) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "Id": f"{prefix}-{index}",
                "Image": image,
                "State": {"Running": running, "Pid": 1000 + index if running else 0},
                "HostConfig": {"RestartPolicy": {"Name": "always"}},
                "Config": {"Env": [], "Labels": {}},
                "Mounts": [
                    {
                        "Destination": "/data",
                        "Name": "tinyassets-data",
                        "Source": str(self.volume),
                    }
                ],
            }
            for index, name in enumerate(EXPECTED_CONTAINERS)
        }

    def install_target_fleet(self) -> None:
        self.containers = self._containers(
            "target",
            "sha256:target",
            running=True,
        )
        self.units[DAEMON_SERVICE]["active"] = "active"

    def install_sidecars(
        self,
        *,
        project: str = "tinyassets",
        running: bool = True,
        restart: str = "always",
        prefix: str = "sidecar",
    ) -> None:
        for index, (name, service) in enumerate(fence.CANONICAL_SIDECARS):
            mounts = [
                {
                    "Type": "bind",
                    "Source": source,
                    "Destination": destination,
                    "RW": False,
                }
                for source, destination in fence.CANONICAL_SIDECAR_MOUNTS[name]
            ]
            self.containers[name] = {
                "Id": f"{prefix}-{index}",
                "Image": f"sha256:{prefix}-{index}",
                "State": {
                    "Running": running,
                    "Pid": 2000 + index if running else 0,
                },
                "HostConfig": {"RestartPolicy": {"Name": restart}},
                "Config": {
                    "Image": fence.CANONICAL_SIDECAR_IMAGES[name],
                    "Env": [],
                    "Labels": {
                        "com.docker.compose.project": project,
                        "com.docker.compose.service": service,
                    },
                },
                "Mounts": mounts,
            }

    def container_info(self, name: str) -> dict[str, Any]:
        if (
            name == fence.CANONICAL_SIDECARS[0][0]
            and self.substitute_sidecar_after_inspections
        ):
            self.substitute_sidecar_after_inspections -= 1
            if self.substitute_sidecar_after_inspections == 0:
                original = json.loads(json.dumps(self.containers[name]))
                if self.rename_sidecar_before_substitution:
                    self.containers["renamed-recovery-sidecar"] = original
                replacement = json.loads(json.dumps(original))
                replacement["Id"] = "substituted-sidecar-id"
                replacement["State"] = {"Running": True, "Pid": 2999}
                replacement["HostConfig"]["RestartPolicy"]["Name"] = "always"
                replacement["Config"]["Labels"][
                    "com.docker.compose.project"
                ] = "foreign-project"
                self.containers[name] = replacement
        info = self.containers.get(name)
        if info is None:
            info = next(
                candidate
                for candidate in self.containers.values()
                if candidate["Id"] == name
            )
        result = json.loads(json.dumps(info))
        if name in self.missing_container_info_identity:
            result["Id"] = ""
        return result

    def image_identity(self, image: str, expected_repository: str) -> tuple[str, str]:
        del expected_repository
        return self.image_identities[image]

    def volume_dir(self) -> Path:
        return self.volume

    def volume_container_names(self) -> list[str]:
        return sorted(
            name
            for name, info in self.containers.items()
            if any(
                mount.get("Name") == "tinyassets-data"
                for mount in info.get("Mounts", [])
                if isinstance(mount, Mapping)
            )
        )

    def container_pids(self, names: Any) -> set[int]:
        pids: set[int] = set()
        for identity in names:
            match = next(
                (
                    info
                    for name, info in self.containers.items()
                    if identity in {name, info["Id"]}
                ),
                None,
            )
            if match and match["State"]["Running"]:
                pids.add(int(match["State"]["Pid"]))
        return pids

    def container_restart_policy(self, identity: str) -> str:
        if self.restart_policy_override is not None:
            return self.restart_policy_override
        for info in self.containers.values():
            if identity == info["Id"]:
                return str(info["HostConfig"]["RestartPolicy"]["Name"])
        raise FenceError("container restart policy unavailable")

    def unit_present(self, unit: str) -> bool:
        return unit in self.units

    def unit_load_state(self, unit: str) -> str:
        return self.units[unit]["load"] if unit in self.units else "not-found"

    def unit_state(self, unit: str) -> dict[str, str]:
        state = self.units[unit]
        return {"active": state["active"], "enabled": state["enabled"]}

    def unit_active_state(self, unit: str) -> str:
        return self.units[unit]["active"]

    def run(
        self,
        args: list[str] | tuple[str, ...],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout_seconds: int = fence.HOST_COMMAND_TIMEOUT_SECONDS,
    ) -> str:
        del check, input_text
        command = tuple(args)
        self.calls.append(command)
        if command[0] == "systemctl":
            action = command[1]
            units = [
                value
                for value in command[2:]
                if value not in {"--now", "--runtime"}
            ]
            if action == "disable":
                for unit in units:
                    self.units[unit]["enabled"] = "disabled"
                    if "--now" in command and unit != self.stubborn_unit:
                        self.units[unit]["active"] = "inactive"
            elif action == "stop":
                for unit in units:
                    if unit in self.units and unit != self.stubborn_unit:
                        self.units[unit]["active"] = "inactive"
            elif action == "mask":
                for unit in units:
                    self.units[unit]["enabled"] = "masked-runtime"
                    self.units[unit]["load"] = "masked"
            elif action == "unmask" and self.unmask_error:
                raise FenceError("systemctl unmask failed")
            elif action == "unmask" and not self.unmask_noop:
                for unit in units:
                    self.units[unit]["enabled"] = (
                        "static" if unit.endswith(".service") else "disabled"
                    )
                    self.units[unit]["load"] = "loaded"
            elif action == "enable":
                for unit in units:
                    self.units[unit]["enabled"] = "enabled"
            elif action == "start":
                for unit in units:
                    self.units[unit]["active"] = "active"
                if (
                    DAEMON_SERVICE in units
                    and self.start_installs_target
                    and not self.containers
                ):
                    configured = fence._configured_image()
                    image = (
                        "sha256:target"
                        if configured == self.target_image_ref
                        else "sha256:old"
                    )
                    self.containers = self._containers(
                        "recovered",
                        image,
                        running=True,
                    )
                    for info in self.containers.values():
                        info["HostConfig"]["RestartPolicy"]["Name"] = "no"
            return ""
        if command[0] == "systemd-run":
            unit = next(
                value.partition("=")[2]
                for value in command
                if value.startswith("--unit=")
            )
            self.units[f"{unit}.timer"] = {
                "active": "active",
                "enabled": "transient",
                "load": "loaded",
            }
            self.units[f"{unit}.service"] = {
                "active": "inactive",
                "enabled": "transient",
                "load": "loaded",
            }
            return ""
        if command[:2] == ("docker", "compose"):
            if self.start_installs_target:
                project = command[command.index("--project-name") + 1]
                services = command[command.index("--no-deps") + 1 :]
                if tuple(services) == fence.RECOVERY_SERVICES:
                    configured = fence._configured_image()
                    image = (
                        "sha256:target"
                        if configured == self.target_image_ref
                        else "sha256:old"
                    )
                    sidecars = {
                        name: info
                        for name, info in self.containers.items()
                        if name in dict(fence.CANONICAL_SIDECARS)
                    }
                    self.containers = {
                        **self._containers("recovered", image, running=True),
                        **sidecars,
                    }
                    for name in EXPECTED_CONTAINERS:
                        info = self.containers[name]
                        info["HostConfig"]["RestartPolicy"]["Name"] = "no"
                        info["Config"]["Labels"][
                            "com.docker.compose.project"
                        ] = project
                elif tuple(services) == fence.RECOVERY_SIDECAR_SERVICES:
                    if self.foreign_sidecar_compose:
                        self.install_sidecars(
                            project="foreign-project",
                            restart="always",
                            prefix="foreign-sidecar",
                        )
                        raise FenceError("foreign sidecar blocked compose")
                    if self.mixed_sidecar_compose_owned_index is not None:
                        self.install_sidecars(
                            project=project,
                            restart="no",
                            prefix="recovery-sidecar",
                        )
                        foreign_index = 1 - self.mixed_sidecar_compose_owned_index
                        foreign_name = fence.CANONICAL_SIDECARS[foreign_index][0]
                        foreign = self.containers[foreign_name]
                        foreign["Id"] = f"foreign-sidecar-{foreign_index}"
                        foreign["Config"]["Labels"][
                            "com.docker.compose.project"
                        ] = "foreign-project"
                        foreign["HostConfig"]["RestartPolicy"]["Name"] = "always"
                        raise FenceError("mixed sidecar blocked compose")
                    if (
                        self.fail_sidecar_compose_after
                        and self.sidecar_compose_failures_remaining
                    ):
                        self.sidecar_compose_failures_remaining -= 1
                        name, service = fence.CANONICAL_SIDECARS[0]
                        self.install_sidecars(
                            project=project,
                            restart="no",
                            prefix="recovery-sidecar",
                        )
                        for extra_name, _extra_service in fence.CANONICAL_SIDECARS[
                            self.fail_sidecar_compose_after:
                        ]:
                            self.containers.pop(extra_name, None)
                        assert name in self.containers and service
                        raise FenceError("partial sidecar compose failure")
                    if self.incomplete_sidecar_compose_successes_remaining:
                        self.incomplete_sidecar_compose_successes_remaining -= 1
                        self.install_sidecars(
                            project=project,
                            restart="no",
                            prefix="recovery-sidecar",
                        )
                        self.containers.pop(fence.CANONICAL_SIDECARS[1][0])
                        return ""
                    self.install_sidecars(
                        project=project,
                        restart="no",
                        prefix="recovery-sidecar",
                    )
                    if self.recovery_sidecar_data_mount:
                        name = fence.CANONICAL_SIDECARS[0][0]
                        self.containers[name]["Mounts"] = [
                            {"Name": "tinyassets-data", "Destination": "/data"}
                        ]
                else:
                    raise AssertionError(f"unexpected compose services: {services}")
            return ""
        if command[:2] == ("docker", "update"):
            policy = next(
                value.partition("=")[2]
                for value in command
                if value.startswith("--restart=")
            )
            identity = command[-1]
            if (
                identity.startswith("recovery-sidecar-")
                and self.sidecar_updates_before_failure >= 0
            ):
                if self.sidecar_updates_before_failure == 0:
                    raise FenceError("injected sidecar restart-fence failure")
                self.sidecar_updates_before_failure -= 1
            for name, info in self.containers.items():
                if identity in {name, info["Id"]}:
                    info["HostConfig"]["RestartPolicy"]["Name"] = policy
                    self.restart_restore_updates += 1
                    if (
                        self.interrupt_restart_restore_after
                        and self.restart_restore_updates
                        == self.interrupt_restart_restore_after
                    ):
                        raise KeyboardInterrupt("simulated host loss")
                    break
            else:
                raise FenceError("docker update failed")
            return ""
        if command[:2] == ("docker", "stop"):
            if self.substitute_sidecars_before_stop:
                self.substitute_sidecars_before_stop = False
                for name, _service in fence.CANONICAL_SIDECARS:
                    self.containers.pop(name, None)
                self.install_sidecars(
                    project="foreign-project",
                    restart="always",
                    prefix="substituted-sidecar",
                )
            for identity in command[2:]:
                if (
                    identity.startswith("recovery-sidecar-")
                    and self.sidecar_stop_failures_remaining
                ):
                    self.sidecar_stop_failures_remaining -= 1
                    continue
                for name, info in self.containers.items():
                    if identity in {info["Id"], name}:
                        info["State"]["Running"] = False
                        info["State"]["Pid"] = 0
            return ""
        if command[:2] == ("docker", "rm"):
            identities = set(command[2:])
            self.containers = {
                name: info
                for name, info in self.containers.items()
                if info["Id"] not in identities and name not in identities
            }
            return ""
        if command[:2] == ("docker", "ps") and "-a" in command:
            if self.container_state_error:
                raise FenceError("docker ps failed")
            if self.container_state_override is not None:
                return self.container_state_override
            name_filters = [
                value.removeprefix("name=^/").removesuffix("$")
                for value in command
                if value.startswith("name=^/")
            ]
            if name_filters:
                info = self.containers.get(name_filters[0])
                return str(info["Id"]) if info else ""
            identity = next(
                value.removeprefix("id=")
                for value in command
                if value.startswith("id=")
            )
            for name, info in self.containers.items():
                del name
                if identity == info["Id"]:
                    if "{{.ID}}" in command:
                        return identity
                    state = "running" if info["State"]["Running"] else "exited"
                    return f"{identity}|{state}"
            return ""
        if command[:2] == ("docker", "ps"):
            selected = next(
                value.removeprefix("name=^/").removesuffix("$")
                for value in command
                if value.startswith("name=^/")
            )
            info = self.containers.get(selected)
            return selected if info and info["State"]["Running"] else ""
        if command[:3] == ("docker", "inspect", "--format"):
            identity = command[-1]
            for name, info in self.containers.items():
                if identity in {name, info["Id"]}:
                    return str(info["State"]["Running"]).lower()
            return ""
        raise AssertionError(f"unexpected command: {command}")


def _patch_lifecycle_runtime(
    monkeypatch: pytest.MonkeyPatch,
    configured_ref: list[str],
) -> None:
    monkeypatch.setattr(fence, "_configured_image", lambda: configured_ref[0])
    monkeypatch.setattr(fence, "_stray_writer_processes", lambda *_args: [])
    monkeypatch.setattr(fence.time, "sleep", lambda _seconds: None)

    @contextmanager
    def test_lock(_path: Path):
        yield

    monkeypatch.setattr(fence, "_operation_lock", test_lock)


def _write_restored_recovery_state(
    host: LifecycleHost,
    state_path: Path,
    *,
    project: str | None = None,
) -> None:
    recovery_run_id = "recovery-run-1"
    project = project or fence._recovery_project_name(recovery_run_id)
    container_ids = {
        name: str(info["Id"]) for name, info in host.containers.items()
    }
    for info in host.containers.values():
        info["Config"]["Labels"]["com.docker.compose.project"] = project
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "run_id": recovery_run_id,
                "source_run_id": "source-run-1",
                "recovery_run_id": recovery_run_id,
                "phase": "restored",
                "recovery_project_name": project,
                "recovery_container_ids": container_ids,
            }
        ),
        encoding="utf-8",
    )


def test_full_lifecycle_executes_every_command_and_restores_exact_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"

    preflight_result = preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    assert preflight_result["phase"] == "preflight_proved"

    configured_ref[0] = host.target_image_ref
    assert prepare_deploy(
        host,
        image_ref=host.target_image_ref,
        run_id=RUN_ID,
        state_path=state_path,
    )["phase"] == "target_installed"
    host.install_target_fleet()
    assert prove(
        host,
        image_ref=host.target_image_ref,
        revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )["phase"] == "safe_fleet"
    assert post_canary(
        host,
        image_ref=host.target_image_ref,
        revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )["phase"] == "post_canary_proved"
    assert fence_status(host, run_id=RUN_ID, state_path=state_path)["state_phase"] == (
        "post_canary_proved"
    )
    assert prove(
        host,
        image_ref=host.target_image_ref,
        revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )["phase"] == "post_canary_proved"
    restored = restore_if_safe(
        host,
        image_ref=host.target_image_ref,
        revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    assert restored["phase"] == "restored"
    assert restored["masked_units_after"] == []
    for unit in (*RESTART_RACER_UNITS, DAEMON_SERVICE):
        assert host.unit_state(unit)["enabled"] != "masked-runtime"
    for unit in RESTART_RACER_UNITS:
        if unit.endswith(".timer"):
            assert host.unit_state(unit) == {
                "active": "active",
                "enabled": "enabled",
            }
    assert host.unit_state(DAEMON_SERVICE) == {
        "active": "active",
        "enabled": "enabled",
    }
    second_run = fence.main(
        [
            "--state-path",
            str(state_path),
            "preflight",
            "--image-ref",
            "not-an-immutable-digest",
            "--revision",
            host.target_revision,
            "--run-id",
            "test-run-2",
        ],
        host=host,
    )
    second_failure = json.loads(capsys.readouterr().out)
    assert second_run == 2
    assert second_failure["stale_state_ignored"] is True
    assert not second_failure.get("cutover_started", False)


@pytest.mark.parametrize(
    "observed",
    [
        "active",
        "inactive",
        "failed",
    ],
)
def test_preflight_preserves_stable_daemon_predecessor_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    observed: str,
):
    host = LifecycleHost(tmp_path)
    host.units[DAEMON_SERVICE]["active"] = observed
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "fence-state.json"

    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["daemon_service_state"] == {
        "active": observed,
        "enabled": "enabled",
    }
    assert state["restart_racer_state"]["daemon-watchdog.service"][
        "active"
    ] == "inactive"


def test_preflight_confirms_new_exact_container_child_before_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    candidate = {
        "pid": 999,
        "exe": "python",
        "_process_start_time_ticks": 77,
    }
    scans = iter(([candidate], []))
    monkeypatch.setattr(
        fence,
        "_stray_writer_processes",
        lambda *_args: next(scans),
    )
    captured_ids = tuple(
        str(host.containers[name]["Id"]) for name in EXPECTED_CONTAINERS
    )
    confirmations: list[tuple[dict[str, Any], ...]] = []

    def confirm(
        _host: Any,
        candidates: Any,
        identities: Any,
    ) -> list[dict[str, Any]]:
        assert tuple(identities) == captured_ids
        confirmations.append(tuple(candidates))
        return []

    monkeypatch.setattr(fence, "_confirm_stray_writer_processes", confirm)

    result = preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=tmp_path / "fence-state.json",
    )

    assert result["phase"] == "preflight_proved"
    assert confirmations == [(candidate,)]


def test_preflight_initial_snapshot_does_not_trust_same_name_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    replacement_pid = 999
    captured_ids = {
        str(host.containers[name]["Id"]) for name in EXPECTED_CONTAINERS
    }
    snapshot_inputs: list[tuple[str, ...]] = []

    def container_pids(identities: Any) -> set[int]:
        identities = tuple(identities)
        snapshot_inputs.append(identities)
        if any(identity in EXPECTED_CONTAINERS for identity in identities):
            return {replacement_pid}
        assert set(identities) == captured_ids
        return {
            int(info["State"]["Pid"])
            for info in host.containers.values()
        }

    host.container_pids = container_pids  # type: ignore[method-assign]

    def scan(_receipt: Path, excluded: set[int], _volume: Path) -> list[dict[str, Any]]:
        if replacement_pid in excluded:
            return []
        return [
            {
                "pid": replacement_pid,
                "exe": "python",
                "_process_start_time_ticks": 77,
            }
        ]

    monkeypatch.setattr(fence, "_stray_writer_processes", scan)
    monkeypatch.setattr(
        fence,
        "_confirm_stray_writer_processes",
        lambda _host, candidates, _identities: list(candidates),
    )

    with pytest.raises(FenceError, match="stray writer process risk"):
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=tmp_path / "fence-state.json",
        )

    assert snapshot_inputs
    assert set(snapshot_inputs[0]) == captured_ids


@pytest.mark.parametrize("missing_kind", ["expected", "extra"])
def test_preflight_refuses_missing_controlled_identity_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_kind: str,
):
    host = LifecycleHost(tmp_path)
    private_name = "tinyassets-daemon"
    private_identity = str(host.containers[private_name]["Id"])
    if missing_kind == "extra":
        private_name = "private-forgotten-writer"
        extra = host._containers("extra", "sha256:old", running=False)[
            "tinyassets-daemon"
        ]
        private_identity = "private-extra-identity"
        extra["Id"] = private_identity
        host.containers[private_name] = extra
    host.missing_container_info_identity.add(private_name)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "fence-state.json"

    with pytest.raises(
        FenceError,
        match="controlled container identity is unavailable",
    ) as failure:
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    assert private_name not in str(failure.value)
    assert private_identity not in str(failure.value)
    assert not state_path.exists()
    assert not any(
        (
            call[:2] in {("docker", "update"), ("docker", "stop"), ("docker", "rm")}
            or (
                call[:1] == ("systemctl",)
                and len(call) > 1
                and call[1]
                in {"disable", "stop", "mask", "unmask", "enable", "start"}
            )
        )
        for call in host.calls
    )


def test_finalized_recovery_generation_is_removed_before_canonical_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    recovered_ids = {
        name: str(info["Id"]) for name, info in host.containers.items()
    }

    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    configured_ref[0] = host.target_image_ref
    evidence = prepare_deploy(
        host,
        image_ref=host.target_image_ref,
        run_id=RUN_ID,
        state_path=state_path,
    )

    assert evidence["phase"] == "target_installed"
    assert host.containers == {}
    remove = next(call for call in host.calls if call[:2] == ("docker", "rm"))
    assert set(remove[2:]) == set(recovered_ids.values())
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["recovery_handoff"]["removal_phase"] == "removed"
    assert state["recovery_handoff"]["container_ids"] == recovered_ids


def test_recovery_handoff_removes_exact_sidecars_before_canonical_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    host.install_sidecars()
    sidecar_ids = {
        name: str(host.containers[name]["Id"])
        for name, _service in fence.CANONICAL_SIDECARS
    }

    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    configured_ref[0] = host.target_image_ref
    evidence = prepare_deploy(
        host,
        image_ref=host.target_image_ref,
        run_id=RUN_ID,
        state_path=state_path,
    )

    assert evidence["removed_sidecar_container_ids"] == sidecar_ids
    assert set(host.containers) == set()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["sidecar_handoff"]["container_ids"] == sidecar_ids
    assert state["sidecar_handoff"]["removal_phase"] == "removed"
    sidecar_remove = next(
        call
        for call in host.calls
        if call[:2] == ("docker", "rm")
        and set(call[2:]) == set(sidecar_ids.values())
    )
    assert "-v" not in sidecar_remove


@pytest.mark.parametrize(
    ("project", "expected_class"),
    [
        ("workflow", "legacy-workflow"),
        ("deploy", "legacy-deploy"),
        ("recorded-recovery", "recorded-recovery"),
        (
            "tinyassets-recovery-0123456789abcdef",
            "unrecorded-recovery",
        ),
        ("", "missing"),
        ("private-foreign-project", "other"),
    ],
)
def test_preflight_classifies_invalid_sidecar_project_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    project: str,
    expected_class: str,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    if project == "recorded-recovery":
        state = json.loads(state_path.read_text(encoding="utf-8"))
        project = str(state["recovery_project_name"])
    host.install_sidecars(project=project)

    with pytest.raises(
        FenceError,
        match=(
            f"restored sidecar project {expected_class} is invalid: "
            "tinyassets-tunnel"
        ),
    ) as failure:
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    if expected_class in {"other", "unrecorded-recovery"}:
        assert project not in str(failure.value)
    assert not any(
        call[:2] in {("docker", "update"), ("docker", "stop"), ("docker", "rm")}
        for call in host.calls
    )


def test_preflight_classifies_current_project_when_recovery_project_expected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    host.install_sidecars(project=fence.CANONICAL_COMPOSE_PROJECT)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["recovery_sidecar_container_ids"] = {
        name: str(host.containers[name]["Id"])
        for name, _service in fence.CANONICAL_SIDECARS
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(
        FenceError,
        match=(
            "restored sidecar project current-canonical is invalid: "
            "tinyassets-tunnel"
        ),
    ):
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    assert not any(
        call[:2] in {("docker", "update"), ("docker", "stop"), ("docker", "rm")}
        for call in host.calls
    )


@pytest.mark.parametrize(
    "recovery_run_id",
    [
        "30514843571-1",
        "30514946746-1",
        "30515026545-1",
        "30515117371-1",
        "30517431860-1",
        "30518735998-1",
    ],
)
def test_preflight_hands_off_audited_full_compose_recovery_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recovery_run_id: str,
):
    assert fence.AUDITED_FULL_COMPOSE_RECOVERY_RUN_IDS == (
        "30514843571-1",
        "30514946746-1",
        "30515026545-1",
        "30515117371-1",
        "30517431860-1",
        "30518735998-1",
    )
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    project = fence._recovery_project_name(recovery_run_id)
    host.install_sidecars(project=project)
    sidecar_ids = {
        name: str(host.containers[name]["Id"])
        for name, _service in fence.CANONICAL_SIDECARS
    }

    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["sidecar_handoff"] == {
        "container_ids": sidecar_ids,
        "project_name": project,
        "removal_phase": "pending",
    }
    configured_ref[0] = host.target_image_ref
    evidence = prepare_deploy(
        host,
        image_ref=host.target_image_ref,
        run_id=RUN_ID,
        state_path=state_path,
    )
    assert evidence["removed_sidecar_container_ids"] == sidecar_ids
    assert set(host.containers) == set()


def test_preflight_does_not_override_recorded_sidecar_project_with_audit_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    host.install_sidecars(
        project=fence._recovery_project_name("30515117371-1")
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["recovery_sidecar_container_ids"] = {
        name: str(host.containers[name]["Id"])
        for name, _service in fence.CANONICAL_SIDECARS
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(
        FenceError,
        match=(
            "restored sidecar project audited-full-compose-recovery "
            "is invalid: tinyassets-tunnel"
        ),
    ):
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    assert not any(
        call[:2] in {("docker", "update"), ("docker", "stop"), ("docker", "rm")}
        for call in host.calls
    )


def test_preflight_refuses_mixed_audited_recovery_sidecar_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    host.install_sidecars(
        project=fence._recovery_project_name("30515117371-1")
    )
    logs_name = fence.CANONICAL_SIDECARS[1][0]
    host.containers[logs_name]["Config"]["Labels"][
        "com.docker.compose.project"
    ] = fence._recovery_project_name("30518735998-1")

    with pytest.raises(
        FenceError,
        match="restored sidecar projects differ",
    ):
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    assert not any(
        call[:2] in {("docker", "update"), ("docker", "stop"), ("docker", "rm")}
        for call in host.calls
    )


@pytest.mark.parametrize(
    ("drift", "expected", "private_value"),
    [
        (
            "missing_identity",
            "restored sidecar identity is missing: tinyassets-tunnel",
            "",
        ),
        (
            "wrong_service",
            "restored sidecar service is invalid: tinyassets-tunnel",
            "private-service-label",
        ),
        (
            "data_mount",
            "restored sidecar non-writer proof failed: tinyassets-tunnel",
            "private-volume-name",
        ),
    ],
)
def test_preflight_sidecar_refusal_reports_only_fixed_predicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    expected: str,
    private_value: str,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    host.install_sidecars()
    tunnel = host.containers[fence.CANONICAL_SIDECARS[0][0]]
    if drift == "missing_identity":
        host.missing_container_info_identity.add(
            fence.CANONICAL_SIDECARS[0][0]
        )
    elif drift == "wrong_service":
        tunnel["Config"]["Labels"][
            "com.docker.compose.service"
        ] = private_value
    else:
        tunnel["Mounts"] = [
            {"Name": private_value, "Destination": "/data"}
        ]

    with pytest.raises(FenceError, match=expected) as failure:
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    if private_value:
        assert private_value not in str(failure.value)
    assert not any(
        call[:2] in {("docker", "update"), ("docker", "stop"), ("docker", "rm")}
        for call in host.calls
    )


@pytest.mark.parametrize(
    "drift",
    [
        "forged_image",
        "volume_bind_alias",
        "named_volume",
        "duplicate_mount",
        "non_mapping_mount",
    ],
)
def test_preflight_refuses_unproved_sidecar_config_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    host.install_sidecars()
    name = fence.CANONICAL_SIDECARS[-1][0]
    if drift == "forged_image":
        host.containers[name]["Config"]["Image"] = "attacker/image@sha256:" + "f" * 64
    elif drift == "volume_bind_alias":
        host.containers[name]["Mounts"].append(
            {
                "Type": "bind",
                "Source": str(host.volume),
                "Destination": "/alternate-data",
                "RW": False,
            }
        )
    elif drift == "named_volume":
        mount = host.containers[name]["Mounts"][0]
        mount["Type"] = "volume"
        mount["Name"] = "foreign-volume"
    elif drift == "duplicate_mount":
        host.containers[name]["Mounts"].append(
            json.loads(json.dumps(host.containers[name]["Mounts"][0]))
        )
    else:
        host.containers[name]["Mounts"].append("not-a-mount")

    with pytest.raises(FenceError, match="sidecar"):
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    assert not any(
        call[:2] in {("docker", "update"), ("docker", "stop"), ("docker", "rm")}
        for call in host.calls
    )


def test_preflight_recorded_sidecar_identity_refusal_hides_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    project = str(state["recovery_project_name"])
    host.install_sidecars(project=project)
    state["recovery_sidecar_container_ids"] = {
        name: str(host.containers[name]["Id"])
        for name, _service in fence.CANONICAL_SIDECARS
    }
    state["recovery_sidecar_container_ids"][
        fence.CANONICAL_SIDECARS[0][0]
    ] = "private-recorded-id"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(
        FenceError,
        match="restored sidecar recorded identity changed: tinyassets-tunnel",
    ) as failure:
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    assert "private-recorded-id" not in str(failure.value)
    assert "recovery-sidecar-0" not in str(failure.value)


def test_preflight_refuses_sidecar_substitution_after_stopping_bound_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    host.install_sidecars()
    bound_ids = {
        name: str(host.containers[name]["Id"])
        for name, _service in fence.CANONICAL_SIDECARS
    }
    host.substitute_sidecars_before_stop = True

    with pytest.raises(
        FenceError,
        match="restored sidecar recorded identity changed: tinyassets-tunnel",
    ):
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    stop_call = next(call for call in host.calls if call[:2] == ("docker", "stop"))
    assert set(bound_ids.values()) <= set(stop_call[2:])
    assert not set(bound_ids) & set(stop_call[2:])
    for name, _service in fence.CANONICAL_SIDECARS:
        assert host.containers[name]["Id"].startswith("substituted-sidecar-")
        assert host.containers[name]["State"]["Running"] is True


@pytest.mark.parametrize("drift", ["foreign_project", "running", "restart", "substitute"])
def test_prepare_refuses_sidecar_drift_before_any_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    host.install_sidecars()
    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    name = fence.CANONICAL_SIDECARS[0][0]
    if drift == "foreign_project":
        host.containers[name]["Config"]["Labels"][
            "com.docker.compose.project"
        ] = "foreign-project"
    elif drift == "running":
        host.containers[name]["State"] = {"Running": True, "Pid": 9999}
    elif drift == "restart":
        host.containers[name]["HostConfig"]["RestartPolicy"]["Name"] = "always"
    else:
        host.containers[name]["Id"] = "substituted-sidecar-id"
    configured_ref[0] = host.target_image_ref

    with pytest.raises(FenceError, match="sidecar"):
        prepare_deploy(
            host,
            image_ref=host.target_image_ref,
            run_id=RUN_ID,
            state_path=state_path,
        )

    assert not any(call[:2] == ("docker", "rm") for call in host.calls)


@pytest.mark.parametrize("removed_count", [1, 2])
def test_prepare_replays_sidecar_removal_after_durable_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    removed_count: int,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    host.install_sidecars()
    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"]["removal_phase"] = "planned"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    for name, _service in fence.CANONICAL_SIDECARS[:removed_count]:
        del host.containers[name]
    configured_ref[0] = host.target_image_ref

    evidence = prepare_deploy(
        host,
        image_ref=host.target_image_ref,
        run_id=RUN_ID,
        state_path=state_path,
    )

    assert evidence["phase"] == "target_installed"
    assert host.containers == {}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["sidecar_handoff"]["removal_phase"] == "removed"


def test_ordinary_canonical_predecessor_is_not_removed_by_recovery_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    for info in host.containers.values():
        info["Config"]["Labels"]["com.docker.compose.project"] = "tinyassets"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "run_id": "prior-normal-run",
                "phase": "restored",
            }
        ),
        encoding="utf-8",
    )

    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    configured_ref[0] = host.target_image_ref
    prepare_deploy(
        host,
        image_ref=host.target_image_ref,
        run_id=RUN_ID,
        state_path=state_path,
    )

    assert set(host.containers) == set(EXPECTED_CONTAINERS)
    assert not any(call[:2] == ("docker", "rm") for call in host.calls)


def test_preflight_refuses_mismatched_restored_recovery_provenance_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    host.containers[EXPECTED_CONTAINERS[0]]["Config"]["Labels"][
        "com.docker.compose.project"
    ] = "foreign-project"

    with pytest.raises(FenceError, match="recovery provenance"):
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    assert not any(
        call[:2]
        in {
            ("docker", "update"),
            ("docker", "stop"),
            ("docker", "rm"),
            ("systemctl", "mask"),
        }
        for call in host.calls
    )


@pytest.mark.parametrize(
    "drift",
    ["partial", "foreign_project", "running", "restart_policy"],
)
def test_prepare_refuses_recovery_handoff_drift_without_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if drift != "partial":
        state["recovery_handoff"]["removal_phase"] = "planned"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    if drift == "partial":
        del host.containers[EXPECTED_CONTAINERS[-1]]
    elif drift == "foreign_project":
        host.containers[EXPECTED_CONTAINERS[0]]["Config"]["Labels"][
            "com.docker.compose.project"
        ] = "foreign-project"
    elif drift == "running":
        host.containers[EXPECTED_CONTAINERS[0]]["State"] = {
            "Running": True,
            "Pid": 9999,
        }
    else:
        host.containers[EXPECTED_CONTAINERS[0]]["HostConfig"]["RestartPolicy"][
            "Name"
        ] = "always"
    configured_ref[0] = host.target_image_ref

    with pytest.raises(FenceError):
        prepare_deploy(
            host,
            image_ref=host.target_image_ref,
            run_id=RUN_ID,
            state_path=state_path,
        )

    assert not any(call[:2] == ("docker", "rm") for call in host.calls)


def test_prepare_replays_partial_recovery_removal_after_durable_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    configured_ref[0] = host.target_image_ref
    original_run = host.run
    injected = False

    def interrupt_after_partial_remove(
        args: list[str] | tuple[str, ...],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout_seconds: int = fence.HOST_COMMAND_TIMEOUT_SECONDS,
    ) -> str:
        nonlocal injected
        command = tuple(args)
        if command[:2] == ("docker", "rm") and not injected:
            injected = True
            original_run(
                ["docker", "rm", *command[2:4]],
                check=check,
                input_text=input_text,
            )
            raise FenceError("simulated interruption after partial docker rm")
        return original_run(args, check=check, input_text=input_text)

    host.run = interrupt_after_partial_remove  # type: ignore[method-assign]
    with pytest.raises(FenceError, match="simulated interruption"):
        prepare_deploy(
            host,
            image_ref=host.target_image_ref,
            run_id=RUN_ID,
            state_path=state_path,
        )

    interrupted_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert interrupted_state["recovery_handoff"]["removal_phase"] == "planned"
    assert host.containers == {}

    host.run = original_run  # type: ignore[method-assign]
    evidence = prepare_deploy(
        host,
        image_ref=host.target_image_ref,
        run_id=RUN_ID,
        state_path=state_path,
    )

    assert evidence["phase"] == "target_installed"
    assert host.containers == {}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["recovery_handoff"]["removal_phase"] == "removed"


def test_prepare_refuses_off_volume_name_substitution_before_replay_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    configured_ref[0] = host.target_image_ref
    original_run = host.run
    interrupted = False

    def interrupt_after_one_remove(
        args: list[str] | tuple[str, ...],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout_seconds: int = fence.HOST_COMMAND_TIMEOUT_SECONDS,
    ) -> str:
        nonlocal interrupted
        command = tuple(args)
        if command[:2] == ("docker", "rm") and not interrupted:
            interrupted = True
            original_run(
                ["docker", "rm", command[2]],
                check=check,
                input_text=input_text,
            )
            raise FenceError("simulated recovery handoff interruption")
        return original_run(args, check=check, input_text=input_text)

    host.run = interrupt_after_one_remove  # type: ignore[method-assign]
    with pytest.raises(FenceError, match="simulated recovery handoff"):
        prepare_deploy(
            host,
            image_ref=host.target_image_ref,
            run_id=RUN_ID,
            state_path=state_path,
        )

    missing_name = next(
        name for name in EXPECTED_CONTAINERS if name not in host.containers
    )
    replacement = host._containers(
        "foreign-off-volume", "sha256:old", running=True
    )[missing_name]
    replacement["Mounts"] = []
    host.containers[missing_name] = replacement
    host.run = original_run  # type: ignore[method-assign]
    host.calls.clear()

    with pytest.raises(FenceError, match="substituted"):
        prepare_deploy(
            host,
            image_ref=host.target_image_ref,
            run_id=RUN_ID,
            state_path=state_path,
        )

    assert not any(call[:2] == ("docker", "rm") for call in host.calls)
    assert host.containers[missing_name]["State"]["Running"] is True


def test_prepare_replays_durable_recovery_removal_intent_after_exact_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)
    state_path = tmp_path / "fence-state.json"
    _write_restored_recovery_state(host, state_path)
    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["recovery_handoff"]["removal_phase"] = "planned"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    configured_ref[0] = host.target_image_ref

    evidence = prepare_deploy(
        host,
        image_ref=host.target_image_ref,
        run_id=RUN_ID,
        state_path=state_path,
    )

    assert evidence["phase"] == "target_installed"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["recovery_handoff"]["removal_phase"] == "removed"
    assert not any(call[:2] == ("docker", "rm") for call in host.calls)


def test_new_run_preflight_failure_ignores_stale_restored_generation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "fence-state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "run_id": "prior-run-1",
                "phase": "restored",
            }
        ),
        encoding="utf-8",
    )

    return_code = fence.main(
        [
            "--state-path",
            str(state_path),
            "preflight",
            "--image-ref",
            "not-an-immutable-digest",
            "--revision",
            host.target_revision,
            "--run-id",
            "current-run-2",
        ],
        host=host,
    )

    failure = json.loads(capsys.readouterr().out)
    assert return_code == 2
    assert failure["stale_state_ignored"] is True
    assert not failure.get("cutover_started", False)
    status = fence_status(
        host,
        run_id="current-run-2",
        state_path=state_path,
    )
    assert status["current_run_matches"] is False
    assert status["current_run_cutover_started"] is False


def test_operation_lock_blocks_status_until_inflight_command_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    first_started = threading.Event()
    release_first = threading.Event()
    status_executed = threading.Event()

    class FakeFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 4

        def __init__(self) -> None:
            self.mutex = threading.Lock()

        def flock(self, _descriptor: int, operation: int) -> None:
            if operation & self.LOCK_EX:
                self.mutex.acquire()
            else:
                self.mutex.release()

    def execute(args: Any, _host: Any) -> dict[str, Any]:
        if args.command == "observe" and args.image_ref == "first":
            first_started.set()
            assert release_first.wait(timeout=2)
        else:
            status_executed.set()
        return {"command": args.command}

    monkeypatch.setattr(fence, "fcntl", FakeFcntl())
    monkeypatch.setattr(fence, "_execute", execute)
    lock_path = tmp_path / "host-operation.lock"
    first = threading.Thread(
        target=fence.main,
        args=(
            [
                "--lock-path",
                str(lock_path),
                "observe",
                "--image-ref",
                "first",
            ],
        ),
    )
    second = threading.Thread(
        target=fence.main,
        args=(
            [
                "--lock-path",
                str(lock_path),
                "status",
                "--run-id",
                RUN_ID,
            ],
        ),
    )

    first.start()
    assert first_started.wait(timeout=2)
    second.start()
    time.sleep(0.05)
    assert not status_executed.is_set()
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert status_executed.is_set()
    assert not first.is_alive()
    assert not second.is_alive()


def test_operation_lock_fails_closed_without_flock_primitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(fence, "fcntl", None)
    with pytest.raises(FenceError, match="lock primitive is unavailable"):
        with fence._operation_lock(tmp_path / "host-operation.lock"):
            pytest.fail("lock body must not execute")


def test_operation_lock_timeout_is_bounded_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    class BusyFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 4

        @staticmethod
        def flock(_descriptor: int, operation: int) -> None:
            if operation & BusyFcntl.LOCK_EX:
                raise BlockingIOError("busy")

    monkeypatch.setattr(fence, "fcntl", BusyFcntl())
    with pytest.raises(FenceError, match="lock timed out after 0s"):
        with fence._operation_lock(
            tmp_path / "host-operation.lock",
            timeout_seconds=0,
        ):
            pytest.fail("timed-out lock body must not execute")


def test_host_command_timeout_becomes_fence_error(
    monkeypatch: pytest.MonkeyPatch,
):
    observed: dict[str, Any] = {}

    def timeout_run(args: Any, **kwargs: Any) -> Any:
        observed.update(kwargs)
        raise fence.subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(fence.subprocess, "run", timeout_run)
    with pytest.raises(FenceError, match="host command timed out after 45s"):
        fence.Host().run(["docker", "ps"])
    assert observed["timeout"] == fence.HOST_COMMAND_TIMEOUT_SECONDS


def test_guarded_mutation_strips_separator_before_invoking_command(
    tmp_path: Path,
):
    class GuardHost:
        def __init__(self) -> None:
            self.command: list[str] | None = None
            self.timeout_seconds: int | None = None

        def unit_present(self, _unit: str) -> bool:
            return False

        def volume_container_names(self) -> list[str]:
            return []

        def run(
            self,
            args: Any,
            *,
            timeout_seconds: int,
        ) -> str:
            self.command = list(args)
            self.timeout_seconds = timeout_seconds
            return ""

    host = GuardHost()
    args = fence._parser().parse_args(
        [
            "--state-path",
            str(tmp_path / "missing-state.json"),
            "guard-host-mutation",
            "--command-timeout",
            "120",
            "--",
            "/bin/bash",
            "-lc",
            "systemctl restart tinyassets-daemon.service",
        ]
    )

    evidence = fence._execute(args, host)

    assert host.command == [
        "timeout",
        "--kill-after=2s",
        "120s",
        "/bin/bash",
        "-lc",
        "systemctl restart tinyassets-daemon.service",
    ]
    assert host.timeout_seconds == 125
    assert evidence["mutation_completed"] is True


def test_guard_command_can_check_without_running_a_mutation(tmp_path: Path):
    class GuardOnlyHost:
        def unit_present(self, _unit: str) -> bool:
            return False

        def volume_container_names(self) -> list[str]:
            return []

        def run(self, _args: Any, **_kwargs: Any) -> str:
            pytest.fail("guard-only command must not invoke a mutation")

    args = fence._parser().parse_args(
        [
            "--state-path",
            str(tmp_path / "missing-state.json"),
            "guard-host-mutation",
        ]
    )

    evidence = fence._execute(args, GuardOnlyHost())

    assert evidence["mutation_completed"] is False


def test_guarded_mutation_output_reaches_the_workflow_log(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """A guarded mutation's own output must be visible, not just `output_present`.

    `Host.run` captures the wrapped command, so on 2026-09-26 `Install host
    services` reported success in five seconds having printed not one
    `[host-uptime-install]` line -- no "converged N timers", no "already current",
    and none of the installer's warnings. Every workflow wrapping a host mutation in
    `guard-host-mutation` was equally blind, so no workflow log could support a
    claim about what an installer actually did.
    """

    class NoisyHost:
        def __init__(self) -> None:
            self.last_stderr = "WARNING: systemd-journald restart FAILED\n"

        def unit_present(self, _unit: str) -> bool:
            return False

        def volume_container_names(self) -> list[str]:
            return []

        def run(self, _args: Any, **_kwargs: Any) -> str:
            return (
                "[host-uptime-install] journald drop-in installed\n"
                "[host-uptime-install] converged 5 timers at abc123\n"
            )

    args = fence._parser().parse_args(
        [
            "--state-path",
            str(tmp_path / "missing-state.json"),
            "guard-host-mutation",
            "--",
            "/bin/bash",
            "-lc",
            "install",
        ]
    )

    evidence = fence._execute(args, NoisyHost())
    captured = capsys.readouterr()

    assert evidence["mutation_completed"] is True
    # Both streams, because a failed step's diagnosis is usually on stderr.
    assert "journald drop-in installed" in captured.err
    assert "converged 5 timers at abc123" in captured.err
    assert "systemd-journald restart FAILED" in captured.err

    # STDOUT stays a single machine-readable line: callers parse it, and a command
    # that printed a brace would otherwise make the verdict unparseable.
    assert "host-uptime-install" not in captured.out


def test_guarded_mutation_echo_keeps_stdout_parseable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """The verdict on stdout must survive a mutation that prints JSON of its own."""

    class JsonPrintingHost:
        last_stderr = ""

        def unit_present(self, _unit: str) -> bool:
            return False

        def volume_container_names(self) -> list[str]:
            return []

        def run(self, _args: Any, **_kwargs: Any) -> str:
            return '{"safe": false, "error": "not the fence verdict"}'

    args = fence._parser().parse_args(
        [
            "--state-path",
            str(tmp_path / "missing-state.json"),
            "guard-host-mutation",
            "--",
            "/bin/bash",
            "-lc",
            "emit-json",
        ]
    )

    evidence = fence._execute(args, JsonPrintingHost())
    captured = capsys.readouterr()

    # Exercised through `_execute` rather than `main`, because `main` takes the
    # host operation lock and `flock` does not exist in Git Bash -- a test that is
    # red on every Windows box is one each lane re-diagnoses. The property under
    # test belongs to the echo, not to the locking: the mutation's output must not
    # be on stdout, so whatever `main` prints there stays a single parseable line.
    assert "not the fence verdict" not in captured.out
    assert "not the fence verdict" in captured.err

    # And the verdict itself is unpolluted: `main` serialises exactly this.
    verdict = json.loads(json.dumps(evidence, sort_keys=True))
    assert verdict["mutation_completed"] is True
    assert verdict.get("error") is None
    assert verdict["safe"] is True, (
        "the mutation's own JSON leaked into the fence verdict"
    )


def test_guarded_mutation_with_no_output_says_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """A silent mutation must not gain a noise banner."""

    class QuietHost:
        last_stderr = ""

        def unit_present(self, _unit: str) -> bool:
            return False

        def volume_container_names(self) -> list[str]:
            return []

        def run(self, _args: Any, **_kwargs: Any) -> str:
            return ""

    args = fence._parser().parse_args(
        [
            "--state-path",
            str(tmp_path / "missing-state.json"),
            "guard-host-mutation",
            "--",
            "/bin/true",
        ]
    )

    evidence = fence._execute(args, QuietHost())
    captured = capsys.readouterr()

    assert evidence["output_present"] is False
    assert "[fence] guarded mutation" not in captured.err


def test_guard_command_rejects_empty_mutation_after_separator(tmp_path: Path):
    args = fence._parser().parse_args(
        [
            "--state-path",
            str(tmp_path / "missing-state.json"),
            "guard-host-mutation",
            "--",
        ]
    )

    with pytest.raises(FenceError, match="guarded host mutation command is invalid"):
        fence._execute(args, object())


@pytest.mark.parametrize(
    ("unit", "enabled"),
    (
        (DAEMON_SERVICE, "masked"),
        ("daemon-watchdog.timer", "disabled"),
    ),
)
def test_guarded_mutation_rejects_missing_state_with_unit_residue(
    tmp_path: Path,
    unit: str,
    enabled: str,
):
    host = LifecycleHost(tmp_path)
    host.units[unit]["enabled"] = enabled

    with pytest.raises(FenceError, match="stop-writer fence residue"):
        fence.guard_host_mutation(
            host,
            state_path=tmp_path / "missing-state.json",
        )


def test_guarded_mutation_rejects_missing_state_with_restart_no_residue(
    tmp_path: Path,
):
    host = LifecycleHost(tmp_path)
    next(iter(host.containers.values()))["HostConfig"]["RestartPolicy"][
        "Name"
    ] = "no"

    with pytest.raises(FenceError, match="restart=no"):
        fence.guard_host_mutation(
            host,
            state_path=tmp_path / "missing-state.json",
        )


def test_guarded_mutation_rejects_nonterminal_canonical_state(tmp_path: Path):
    state_path = tmp_path / "fence-state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "run_id": RUN_ID,
                "phase": "fencing_planned",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(FenceError, match="fencing_planned"):
        fence.guard_host_mutation(
            LifecycleHost(tmp_path),
            state_path=state_path,
        )


def test_preflight_fails_if_checked_stop_leaves_active_racer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    host.stubborn_unit = "tinyassets-autoheal.service"
    host.units[host.stubborn_unit]["active"] = "active"
    configured_ref = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured_ref)

    with pytest.raises(FenceError, match="restart racer remains active"):
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=tmp_path / "fence-state.json",
        )


def test_preflight_records_and_fences_stopped_extra_volume_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    extra = host._containers("extra", "sha256:old", running=False)[
        "tinyassets-daemon"
    ]
    extra["Id"] = "extra-volume-writer"
    extra["HostConfig"]["RestartPolicy"]["Name"] = "always"
    host.containers["forgotten-writer"] = extra
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "fence-state.json"

    with pytest.raises(FenceError, match="consumer was fenced"):
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "unsafe_fenced"
    assert state["extra_volume_consumers"]["forgotten-writer"] == {
        "id": "extra-volume-writer",
        "restart_policy": "always",
        "running": False,
    }
    assert (
        host.containers["forgotten-writer"]["HostConfig"]["RestartPolicy"]["Name"]
        == "no"
    )


def test_preflight_wal_is_canonical_before_first_host_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    real_atomic_json = fence._atomic_json
    publications: list[str] = []

    def assert_durable_before_publish(path: Path, payload: dict[str, Any]) -> None:
        first_publication = not publications
        publications.append(str(payload["phase"]))
        if first_publication:
            assert payload["phase"] == "fencing_planned"
            assert all(
                info["HostConfig"]["RestartPolicy"]["Name"] == "always"
                for info in host.containers.values()
            )
            assert host.units[DAEMON_SERVICE]["enabled"] == "enabled"
        real_atomic_json(path, payload)

    monkeypatch.setattr(fence, "_atomic_json", assert_durable_before_publish)
    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=tmp_path / "fence-state.json",
    )

    assert publications[:3] == [
        "fencing_planned",
        "fencing_planned",
        "fencing",
    ]


def test_preflight_refuses_unproved_restart_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    host.restart_policy_override = "always"
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "fence-state.json"

    with pytest.raises(FenceError, match="restart fence did not persist"):
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["run_id"] == RUN_ID
    assert state["phase"] == "fencing_planned"
    assert state["fence_progress"] == {
        "boot_activators_disabled": False,
        "restart_policy_proved": False,
    }


@pytest.mark.parametrize("failure", ["noop", "error"])
def test_restore_rejects_unmask_noop_or_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
):
    host = LifecycleHost(tmp_path)
    host.unmask_noop = failure == "noop"
    host.unmask_error = failure == "error"
    monkeypatch.setattr(fence.time, "sleep", lambda _seconds: None)
    state_path = tmp_path / "fence-state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "run_id": RUN_ID,
                "phase": "safe_fleet",
                "old_container_ids": {},
                "restart_racer_state": {
                    unit: host.unit_state(unit) for unit in RESTART_RACER_UNITS
                },
                "daemon_service_state": host.unit_state(DAEMON_SERVICE),
            }
        ),
        encoding="utf-8",
    )
    for unit in (*RESTART_RACER_UNITS, DAEMON_SERVICE):
        host.units[unit]["enabled"] = "masked-runtime"
    monkeypatch.setattr(
        fence,
        "prove",
        lambda *_args, **_kwargs: {"safe": True, "phase": "safe_fleet"},
    )

    with pytest.raises(FenceError, match="unit restoration proof failed|unmask failed"):
        restore_if_safe(
            host,
            image_ref=host.target_image_ref,
            revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )
    assert json.loads(state_path.read_text(encoding="utf-8"))["phase"] == (
        "safe_fleet"
    )


@pytest.mark.parametrize("failure", ["command", "malformed"])
def test_old_container_state_errors_fail_closed(
    tmp_path: Path,
    failure: str,
):
    host = LifecycleHost(tmp_path)
    if failure == "command":
        host.container_state_error = True
    else:
        host.container_state_override = "not-an-exact-container-state"
    with pytest.raises(FenceError, match="docker ps failed|not authoritative"):
        fence._container_running_exact(host, "old-0")


def test_prove_rejects_image_identity_not_recorded_in_fence_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "fence-state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "run_id": RUN_ID,
                "phase": "target_installed",
                "target_image_ref": host.target_image_ref,
                "target_revision": host.target_revision,
                "previous_image_ref": host.old_image_ref,
                "previous_revision": host.old_revision,
                "old_container_ids": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fence,
        "observe_fleet",
        lambda *_args, **_kwargs: pytest.fail("identity must fail before observation"),
    )
    arbitrary_image = "ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "c" * 64
    with pytest.raises(FenceError, match="not admitted by durable fence state"):
        prove(
            host,
            image_ref=arbitrary_image,
            revision="c" * 40,
            run_id=RUN_ID,
            state_path=state_path,
        )


def test_unsafe_cleanup_stops_all_writers_and_proves_old_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "fence-state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "phase": "quiesced",
                "receipt_host_path": str(tmp_path / "wiki_trigger_attempts.db"),
                "old_container_ids": {
                    name: f"pre-cutover-{index}"
                    for index, name in enumerate(EXPECTED_CONTAINERS)
                },
                "old_restart_policies": {
                    name: "always" for name in EXPECTED_CONTAINERS
                },
            }
        ),
        encoding="utf-8",
    )

    evidence = quiesce_unsafe(host, run_id=RUN_ID, state_path=state_path)

    assert evidence["phase"] == "unsafe_fenced"
    assert evidence["old_container_ids_running"] == []
    assert not any(
        info["State"]["Running"] for info in host.containers.values()
    )
    assert host.unit_state(DAEMON_SERVICE)["enabled"] == "masked-runtime"


def test_unsafe_cleanup_stops_exact_ids_and_rejects_name_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "fence-state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "phase": "quiesced",
                "receipt_host_path": str(tmp_path / "wiki_trigger_attempts.db"),
                "old_container_ids": {},
            }
        ),
        encoding="utf-8",
    )
    original_id = str(host.containers["tinyassets-daemon"]["Id"])
    original_run = host.run
    stop_call: tuple[str, ...] | None = None

    def substitute_before_stop(
        args: list[str] | tuple[str, ...],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout_seconds: int = fence.HOST_COMMAND_TIMEOUT_SECONDS,
    ) -> str:
        nonlocal stop_call
        command = tuple(args)
        if command[:2] == ("docker", "stop") and stop_call is None:
            stop_call = command
            replacement = host._containers(
                "foreign-replacement", "sha256:old", running=True
            )["tinyassets-daemon"]
            host.containers["tinyassets-daemon"] = replacement
        return original_run(args, check=check, input_text=input_text)

    host.run = substitute_before_stop  # type: ignore[method-assign]
    with pytest.raises(FenceError, match="could not prove"):
        quiesce_unsafe(host, run_id=RUN_ID, state_path=state_path)

    assert stop_call is not None
    assert original_id in stop_call
    assert "tinyassets-daemon" not in stop_call
    replacement = host.containers["tinyassets-daemon"]
    assert replacement["Id"].startswith("foreign-replacement-")
    assert replacement["State"]["Running"] is True
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "unsafe_fence_unproved"
    assert state["emergency_identity_drift"] == ["tinyassets-daemon"]


def test_unsafe_cleanup_fences_newly_attached_volume_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    extra = host._containers("late", "sha256:old", running=True)[
        "tinyassets-daemon"
    ]
    extra["Id"] = "late-volume-writer"
    host.containers["late-volume-writer"] = extra
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "fence-state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "phase": "quiesced",
                "old_container_ids": {},
            }
        ),
        encoding="utf-8",
    )

    evidence = quiesce_unsafe(host, run_id=RUN_ID, state_path=state_path)

    assert evidence["writers_fenced"] is True
    assert host.containers["late-volume-writer"]["State"]["Running"] is False
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "late-volume-writer" in state["extra_volume_consumers"]


def test_unsafe_cleanup_still_fences_when_durable_state_is_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "fence-state.json"
    state_path.write_text("{", encoding="utf-8")

    evidence = quiesce_unsafe(host, run_id=RUN_ID, state_path=state_path)

    assert evidence["writers_fenced"] is True
    assert evidence["source_state_error"]
    assert not any(
        info["State"]["Running"] for info in host.containers.values()
    )
    assert Path(evidence["durable_state_path"]) == state_path
    canonical = json.loads(state_path.read_text(encoding="utf-8"))
    assert canonical["phase"] == "unsafe_fenced"
    archived = Path(evidence["archived_corrupt_state"])
    assert archived.is_file()
    assert archived.read_text(encoding="utf-8") == "{"


def test_corrupt_state_replacement_failure_keeps_canonical_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "fence-state.json"
    state_path.write_text("{", encoding="utf-8")
    monkeypatch.setattr(
        fence,
        "_atomic_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected atomic replacement failure")
        ),
    )

    with pytest.raises(OSError, match="injected atomic replacement failure"):
        quiesce_unsafe(host, run_id=RUN_ID, state_path=state_path)

    assert state_path.read_text(encoding="utf-8") == "{"
    archives = list(tmp_path.glob("fence-state.json.corrupt-*"))
    assert len(archives) == 1
    assert archives[0].read_text(encoding="utf-8") == "{"
    assert all(
        info["HostConfig"]["RestartPolicy"]["Name"] == "always"
        for info in host.containers.values()
    )
    assert host.units[DAEMON_SERVICE]["enabled"] == "enabled"


def test_missing_state_wal_failure_precedes_every_emergency_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "missing-state.json"
    monkeypatch.setattr(
        fence,
        "_atomic_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected emergency WAL failure")
        ),
    )

    with pytest.raises(OSError, match="injected emergency WAL failure"):
        quiesce_unsafe(host, run_id=RUN_ID, state_path=state_path)

    assert not state_path.exists()
    assert all(
        info["HostConfig"]["RestartPolicy"]["Name"] == "always"
        for info in host.containers.values()
    )
    assert host.units[DAEMON_SERVICE]["enabled"] == "enabled"
    mutation_calls = [
        call
        for call in host.calls
        if call[:2]
        in {
            ("docker", "update"),
            ("docker", "stop"),
            ("systemctl", "disable"),
            ("systemctl", "stop"),
            ("systemctl", "mask"),
        }
    ]
    assert mutation_calls == []


def test_unsafe_cleanup_without_receipt_resolution_never_claims_fenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    del host.containers[EXPECTED_CONTAINERS[0]]
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "missing-state.json"

    with pytest.raises(FenceError, match="could not prove"):
        quiesce_unsafe(host, run_id=RUN_ID, state_path=state_path)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "unsafe_fence_unproved"
    assert state["receipt_resolution_error"]
    assert not any(
        info["State"]["Running"] for info in host.containers.values()
    )


def test_process_scan_permission_error_is_uncertainty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    proc_root = tmp_path / "proc"
    proc = proc_root / "123"
    (proc / "fd").mkdir(parents=True)
    (proc / "cmdline").write_bytes(b"python\0worker")
    (proc / "environ").write_bytes(b"")
    (proc / "mountinfo").write_text("", encoding="utf-8")
    monkeypatch.setattr(fence.os, "getpid", lambda: 999)
    monkeypatch.setattr(fence.os, "readlink", lambda _path: "/usr/bin/python")
    original_read_bytes = Path.read_bytes

    def deny_environ(path: Path) -> bytes:
        if path.name == "environ":
            raise PermissionError("denied")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", deny_environ)
    monkeypatch.setattr(
        fence.Path,
        "glob",
        lambda _self, _pattern: [proc],
    )

    with pytest.raises(FenceError, match="process scan permission denied"):
        fence._stray_writer_processes(
            tmp_path / "receipt.db",
            set(),
            tmp_path,
        )


@pytest.mark.parametrize(
    ("process_namespace", "expected_risk"),
    (("mnt:[1]", False), ("mnt:[2]", True)),
)
def test_mountinfo_only_flags_foreign_not_same_host_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_namespace: str,
    expected_risk: bool,
):
    proc = tmp_path / "proc" / "123"
    (proc / "fd").mkdir(parents=True)
    (proc / "ns").mkdir()
    (proc / "cmdline").write_bytes(b"python\0idle")
    (proc / "environ").write_bytes(b"")
    (proc / "mountinfo").write_text(str(tmp_path), encoding="utf-8")
    monkeypatch.setattr(fence.os, "getpid", lambda: 999)

    def readlink(path: Any) -> str:
        value = str(path)
        if value.endswith("/exe") or value.endswith("\\exe"):
            return "/usr/bin/python"
        if value == "/proc/self/ns/mnt":
            return "mnt:[1]"
        return process_namespace

    monkeypatch.setattr(fence.os, "readlink", readlink)
    monkeypatch.setattr(fence.Path, "glob", lambda _self, _pattern: [proc])

    risks = fence._stray_writer_processes(tmp_path / "receipt.db", set(), tmp_path)
    assert bool(risks) is expected_risk
    if risks:
        assert risks[0]["same_host_mount_namespace"] is False
        assert risks[0]["mount_namespace"] == "mnt:[2]"


def test_stray_confirmation_filters_new_owned_children_and_exited_processes(
    tmp_path: Path,
):
    class RefreshHost:
        def container_pids(self, names: Any) -> set[int]:
            assert tuple(names) == ("captured-a", "captured-b")
            return {123}

    _write_process_stat(tmp_path, 123, 100)
    _write_process_stat(tmp_path, 456, 200)
    candidates = [
        {
            "pid": 123,
            "exe": "codex",
            "_process_start_time_ticks": 100,
        },
        {
            "pid": 456,
            "exe": "python",
            "_process_start_time_ticks": 200,
        },
        {
            "pid": 789,
            "exe": "exited",
            "_process_start_time_ticks": 300,
        },
    ]

    assert fence._confirm_stray_writer_processes(
        RefreshHost(),
        candidates,
        ("captured-a", "captured-b"),
        proc_root=tmp_path,
    ) == [{"pid": 456, "exe": "python"}]


def _write_process_stat(proc_root: Path, pid: int, start_time_ticks: int) -> None:
    proc = proc_root / str(pid)
    proc.mkdir(parents=True, exist_ok=True)
    fields_after_comm = ["S", *(["0"] * 18), str(start_time_ticks)]
    (proc / "stat").write_text(
        f"{pid} (writer process) {' '.join(fields_after_comm)}\n",
        encoding="utf-8",
    )


def test_stray_confirmation_rejects_numeric_pid_reuse(
    tmp_path: Path,
):
    class RefreshHost:
        def container_pids(self, names: Any) -> set[int]:
            assert tuple(names) == ("captured-a",)
            return {123}

    _write_process_stat(tmp_path, 123, 101)
    candidate = {
        "pid": 123,
        "exe": "python",
        "_process_start_time_ticks": 100,
    }

    assert fence._confirm_stray_writer_processes(
        RefreshHost(),
        [candidate],
        ("captured-a",),
        proc_root=tmp_path,
    ) == [{"pid": 123, "exe": "python"}]


@pytest.mark.parametrize("stat_contents", [None, "123 malformed\n"])
def test_stray_confirmation_keeps_owned_pid_when_generation_is_unreadable(
    tmp_path: Path,
    stat_contents: str | None,
):
    class RefreshHost:
        def container_pids(self, names: Any) -> set[int]:
            assert tuple(names) == ("captured-a",)
            return {123}

    proc = tmp_path / "123"
    proc.mkdir()
    if stat_contents is not None:
        (proc / "stat").write_text(stat_contents, encoding="utf-8")
    candidate = {
        "pid": 123,
        "exe": "private-exe",
        "_process_start_time_ticks": 100,
    }

    assert fence._confirm_stray_writer_processes(
        RefreshHost(),
        [candidate],
        ("captured-a",),
        proc_root=tmp_path,
    ) == [{"pid": 123, "exe": "private-exe"}]


def test_stray_confirmation_mixed_100_candidate_load_uses_one_snapshot(
    tmp_path: Path,
):
    owned_pids = set(range(1000, 1050))
    exited_pids = set(range(1050, 1099))
    unowned_pid = 1099
    snapshot_calls: list[tuple[str, ...]] = []

    class RefreshHost:
        def container_pids(self, identities: Any) -> set[int]:
            identities = tuple(identities)
            snapshot_calls.append(identities)
            assert identities == ("captured-a", "captured-b")
            return owned_pids

    candidates = []
    for pid in sorted((*owned_pids, *exited_pids, unowned_pid)):
        start_time = 10_000 + pid
        candidates.append(
            {
                "pid": pid,
                "exe": "python",
                "_process_start_time_ticks": start_time,
            }
        )
        if pid not in exited_pids:
            _write_process_stat(tmp_path, pid, start_time)

    assert len(candidates) == fence.MAX_STRAY_WRITER_PROCESS_CANDIDATES
    assert fence._confirm_stray_writer_processes(
        RefreshHost(),
        candidates,
        ("captured-a", "captured-b"),
        proc_root=tmp_path,
    ) == [{"pid": unowned_pid, "exe": "python"}]
    assert snapshot_calls == [("captured-a", "captured-b")]


@pytest.mark.parametrize(
    "docker_output",
    [
        "WRONG\n123",
        "PID\n123\ntruncated",
        "PID\n123 extra",
    ],
)
def test_container_pid_ownership_rejects_malformed_or_partial_output(
    docker_output: str,
):
    class OutputHost(fence.Host):
        def run(
            self,
            args: Any,
            *,
            check: bool = True,
            input_text: str | None = None,
            timeout_seconds: int = fence.HOST_COMMAND_TIMEOUT_SECONDS,
        ) -> str:
            del args, check, input_text
            return docker_output

    assert OutputHost().container_pids(("captured-a",)) == set()


def test_container_pid_ownership_treats_lookup_failure_as_zero_trust():
    class FailingHost(fence.Host):
        def run(
            self,
            args: Any,
            *,
            check: bool = True,
            input_text: str | None = None,
            timeout_seconds: int = fence.HOST_COMMAND_TIMEOUT_SECONDS,
        ) -> str:
            del args, check, input_text
            raise FenceError("private docker failure detail")

    assert FailingHost().container_pids(("captured-a",)) == set()


def test_container_pid_ownership_accepts_complete_well_formed_output():
    class OutputHost(fence.Host):
        def run(
            self,
            args: Any,
            *,
            check: bool = True,
            input_text: str | None = None,
            timeout_seconds: int = fence.HOST_COMMAND_TIMEOUT_SECONDS,
        ) -> str:
            del args, check, input_text
            return "PID\n123\n456"

    assert OutputHost().container_pids(("captured-a",)) == {123, 456}


def test_process_risk_inventory_refuses_candidate_101(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    proc_root = tmp_path / "proc"
    processes = []
    for pid in range(100, 201):
        proc = proc_root / str(pid)
        (proc / "fd").mkdir(parents=True)
        (proc / "ns").mkdir()
        (proc / "cmdline").write_bytes(b"python\0tinyassets.daemon_server")
        (proc / "environ").write_bytes(b"")
        (proc / "mountinfo").write_text("", encoding="utf-8")
        _write_process_stat(proc_root, pid, 1000 + pid)
        processes.append(proc)

    monkeypatch.setattr(fence.os, "getpid", lambda: 999999)

    def readlink(path: Any) -> str:
        value = str(path).replace("\\", "/")
        if value.endswith("/exe"):
            return "/usr/bin/python"
        return "mnt:[1]"

    monkeypatch.setattr(fence.os, "readlink", readlink)
    monkeypatch.setattr(
        fence.Path,
        "glob",
        lambda _self, _pattern: processes[:100],
    )
    assert len(
        fence._stray_writer_processes(
            tmp_path / "receipt.db",
            set(),
            tmp_path,
        )
    ) == 100

    monkeypatch.setattr(
        fence.Path,
        "glob",
        lambda _self, _pattern: processes,
    )
    with pytest.raises(FenceError, match="candidate limit exceeded"):
        fence._stray_writer_processes(
            tmp_path / "receipt.db",
            set(),
            tmp_path,
        )


def test_stray_confirmation_does_not_trust_same_name_replacement(
    tmp_path: Path,
):
    class ReplacementHost:
        def container_pids(self, identities: Any) -> set[int]:
            if tuple(identities) == ("captured-a", "captured-b"):
                return set()
            if tuple(identities) == EXPECTED_CONTAINERS:
                return {123}
            raise AssertionError(f"unexpected identities: {identities}")

    (tmp_path / "123").mkdir()

    assert fence._confirm_stray_writer_processes(
        ReplacementHost(),
        [{"pid": 123, "exe": "foreign-replacement"}],
        ("captured-a", "captured-b"),
        proc_root=tmp_path,
    ) == [{"pid": 123, "exe": "foreign-replacement"}]


def test_post_canary_failure_includes_final_observation_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "run_id": RUN_ID,
                "phase": "safe_fleet",
                "last_failed_observation": {
                    "volume_container_names": ["tinyassets-daemon"]
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fence,
        "prove",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FenceError("exact-five transient")
        ),
    )
    monkeypatch.setattr(fence.time, "sleep", lambda _seconds: None)
    with pytest.raises(FenceError, match="volume_container_names"):
        post_canary(
            object(),
            image_ref="ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "b" * 64,
            revision="b" * 40,
            run_id=RUN_ID,
            state_path=state_path,
        )


def test_cli_failure_includes_last_failed_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "run_id": RUN_ID,
                "phase": "safe_fleet",
                "last_failed_observation": {
                    "stray_writer_processes": [{"pid": 123, "exe": "codex"}]
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fence,
        "_execute",
        lambda *_args: (_ for _ in ()).throw(FenceError("proved unsafe")),
    )

    exit_code = fence.main(
        ["--state-path", str(state_path), "status", "--run-id", RUN_ID],
        host=host,
    )

    failure = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert failure["last_failed_observation"] == {
        "stray_writer_processes": [{"pid": 123, "exe": "codex"}]
    }


def test_writer_command_classifier_covers_idle_cloud_worker():
    assert fence._looks_like_writer_command(
        "python -m tinyassets.cloud_worker --poll-seconds 5"
    )


def _unsafe_recovery_state(host: LifecycleHost, state_path: Path) -> None:
    _create_receipt_db(host.volume / "wiki_trigger_attempts.db")
    snapshot = receipt_snapshot(host.volume / "wiki_trigger_attempts.db")
    for info in host.containers.values():
        info["State"] = {"Running": False, "Pid": 0}
        info["HostConfig"]["RestartPolicy"]["Name"] = "no"
    for unit in (*RESTART_RACER_UNITS, DAEMON_SERVICE):
        host.units[unit]["enabled"] = "masked-runtime"
        host.units[unit]["active"] = "inactive"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "run_id": "source-run-1",
                "source_run_id": "source-run-1",
                "phase": "unsafe_fenced",
                "target_image_ref": host.target_image_ref,
                "target_revision": host.target_revision,
                "previous_image_ref": host.old_image_ref,
                "previous_revision": host.old_revision,
                "volume_mountpoint": str(host.volume),
                "receipt_container_path": "/data/wiki_trigger_attempts.db",
                "receipt_host_path": str(host.volume / "wiki_trigger_attempts.db"),
                "receipt_snapshot": snapshot,
                "preliminary_receipt_snapshot": snapshot,
                "old_container_ids": {
                    name: str(host.containers[name]["Id"])
                    for name in EXPECTED_CONTAINERS
                },
                "old_restart_policies": {
                    name: "always" for name in EXPECTED_CONTAINERS
                },
                "restart_racer_state": {
                    unit: {
                        "active": "active" if unit.endswith(".timer") else "inactive",
                        "enabled": "enabled" if unit.endswith(".timer") else "static",
                    }
                    for unit in RESTART_RACER_UNITS
                },
                "daemon_service_state": {"active": "active", "enabled": "enabled"},
                "present_restart_racer_units": list(RESTART_RACER_UNITS),
                "extra_volume_consumers": {},
            }
        ),
        encoding="utf-8",
    )


def test_recover_unsafe_refuses_wrong_source_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)

    with pytest.raises(FenceError, match="source run"):
        recover_unsafe(
            host,
            source_run_id="wrong-run",
            run_id="recovery-1",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )
    assert not any(call[:2] == ("systemctl", "unmask") for call in host.calls)


def test_recover_unsafe_starts_restart_fenced_then_finalizes_exact_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.start_installs_target = True

    evidence = recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-1",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    assert evidence["phase"] == "recovery_pending_canary"
    assert evidence["source_run_id"] == "source-run-1"
    assert ("systemctl", "start", DAEMON_SERVICE) not in host.calls
    compose = next(call for call in host.calls if call[:2] == ("docker", "compose"))
    remove = next(call for call in host.calls if call[:2] == ("docker", "rm"))
    assert set(remove[2:]) == {
        f"old-{index}" for index, _name in enumerate(EXPECTED_CONTAINERS)
    }
    assert host.calls.index(remove) < host.calls.index(compose)
    assert "--no-deps" in compose
    assert compose[compose.index("--no-deps") + 1 :] == fence.RECOVERY_SERVICES
    assert str(fence.RECOVERY_COMPOSE_OVERRIDE_PATH) in compose
    assert all(
        info["HostConfig"]["RestartPolicy"]["Name"] == "no"
        for info in host.containers.values()
    )
    assert all(
        host.units[unit]["enabled"] == "masked-runtime"
        and host.units[unit]["active"] == "inactive"
        for unit in (*RESTART_RACER_UNITS, DAEMON_SERVICE)
    )
    assert all(
        info["Id"].startswith("recovered-") for info in host.containers.values()
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["source_run_id"] == "source-run-1"
    assert state["recovery_attempts"] == ["recovery-1"]
    assert state["recovery_removed_stopped_container_ids"] == {
        name: f"old-{index}" for index, name in enumerate(EXPECTED_CONTAINERS)
    }
    assert state["phase"] == "recovery_pending_canary"

    finalized = finalize_recovery(
        host,
        source_run_id="source-run-1",
        run_id="recovery-1",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    assert finalized["phase"] == "restored"
    assert ("systemctl", "start", DAEMON_SERVICE) in host.calls
    assert all(
        info["HostConfig"]["RestartPolicy"]["Name"] == "always"
        for info in host.containers.values()
    )


def test_recovery_recreates_removed_sidecars_and_restores_their_restart_posture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {
            name: f"removed-{index}"
            for index, (name, _service) in enumerate(fence.CANONICAL_SIDECARS)
        },
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state["sidecar_restart_policies"] = {
        name: "always" for name, _service in fence.CANONICAL_SIDECARS
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.install_sidecars(project="tinyassets", prefix="failed-sidecar")
    failed_ids = {
        name: str(host.containers[name]["Id"])
        for name, _service in fence.CANONICAL_SIDECARS
    }
    host.start_installs_target = True

    evidence = recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-sidecars",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    assert evidence["phase"] == "recovery_pending_canary"
    assert set(evidence["recovery_sidecar_container_ids"]) == {
        name for name, _service in fence.CANONICAL_SIDECARS
    }
    compose_calls = [
        call for call in host.calls if call[:2] == ("docker", "compose")
    ]
    assert len(compose_calls) == 2
    assert (
        compose_calls[1][compose_calls[1].index("--no-deps") + 1 :]
        == fence.RECOVERY_SIDECAR_SERVICES
    )
    removal = next(
        call
        for call in host.calls
        if call[:2] == ("docker", "rm")
        and set(call[2:]) == set(failed_ids.values())
    )
    assert "-v" not in removal
    recovery_project = fence._recovery_project_name("recovery-sidecars")
    for name, service in fence.CANONICAL_SIDECARS:
        info = host.containers[name]
        assert info["Config"]["Labels"] == {
            "com.docker.compose.project": recovery_project,
            "com.docker.compose.service": service,
        }
        assert info["HostConfig"]["RestartPolicy"]["Name"] == "no"

    finalized = finalize_recovery(
        host,
        source_run_id="source-run-1",
        run_id="recovery-sidecars",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    assert finalized["phase"] == "restored"
    for name, _service in fence.CANONICAL_SIDECARS:
        assert (
            host.containers[name]["HostConfig"]["RestartPolicy"]["Name"]
            == "unless-stopped"
        )


def test_recovery_refuses_empty_project_sidecars_without_mutating_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.install_sidecars(project="", prefix="unowned-sidecar")
    sidecar_ids = {
        str(host.containers[name]["Id"])
        for name, _service in fence.CANONICAL_SIDECARS
    }

    with pytest.raises(FenceError, match="sidecar ownership"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-empty-project",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    assert not any(
        call[:2] in {("docker", "update"), ("docker", "stop"), ("docker", "rm")}
        and any(identity in call for identity in sidecar_ids)
        for call in host.calls
    )


def test_recovery_refuses_data_mount_sidecar_without_mutating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.install_sidecars(project="tinyassets", prefix="writer-sidecar")
    name = fence.CANONICAL_SIDECARS[0][0]
    sidecar_id = str(host.containers[name]["Id"])
    host.containers[name]["Mounts"] = [
        {"Name": "tinyassets-data", "Destination": "/data"}
    ]

    with pytest.raises(FenceError, match="partial or extra writer|non-writer"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-data-sidecar",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    assert not any(sidecar_id in call for call in host.calls)


def test_partial_recovery_sidecar_start_is_durably_refenced_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state["sidecar_restart_policies"] = {
        name: "always" for name, _service in fence.CANONICAL_SIDECARS
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    host.fail_sidecar_compose_after = 1
    host.sidecar_compose_failures_remaining = 1

    evidence = recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-partial-sidecar",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    assert evidence["phase"] == "recovery_pending_canary"
    assert set(evidence["recovery_sidecar_container_ids"]) == {
        name for name, _service in fence.CANONICAL_SIDECARS
    }
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["recovery_sidecar_start_attempt"] == 2
    assert host.sidecar_compose_failures_remaining == 0
    assert any(
        call[:2] == ("docker", "rm")
        and "recovery-sidecar-0" in call
        for call in host.calls
    )


def test_repeated_partial_recovery_sidecar_start_stays_refenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state["sidecar_restart_policies"] = {
        name: "always" for name, _service in fence.CANONICAL_SIDECARS
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    host.fail_sidecar_compose_after = 1
    host.sidecar_compose_failures_remaining = 2

    with pytest.raises(FenceError, match="re-fenced"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-repeated-partial-sidecar",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    partial_name = fence.CANONICAL_SIDECARS[0][0]
    assert state["phase"] == "unsafe_fenced"
    assert state["recovery_sidecar_start_attempt"] == 2
    assert set(state["recovery_sidecar_container_ids"]) == {partial_name}
    assert host.containers[partial_name]["State"]["Running"] is False
    assert (
        host.containers[partial_name]["HostConfig"]["RestartPolicy"]["Name"]
        == "no"
    )


def test_substituted_partial_sidecar_cannot_block_writer_refence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    host.fail_sidecar_compose_after = 1
    host.sidecar_compose_failures_remaining = 1
    host.substitute_sidecar_after_inspections = 2

    with pytest.raises(FenceError, match="re-fenced"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-substituted-partial-sidecar",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    partial_name = fence.CANONICAL_SIDECARS[0][0]
    substituted = host.containers[partial_name]
    assert state["phase"] == "unsafe_fenced"
    assert "identity changed" in state["recovery_sidecar_refence_error"]
    assert all(
        not host.containers[name]["State"]["Running"]
        for name in EXPECTED_CONTAINERS
    )
    assert substituted["Id"] == "substituted-sidecar-id"
    assert substituted["State"]["Running"] is True
    assert substituted["HostConfig"]["RestartPolicy"]["Name"] == "always"
    assert not any(
        "substituted-sidecar-id" in call
        and call[:2] in {
            ("docker", "rm"),
            ("docker", "stop"),
            ("docker", "update"),
        }
        for call in host.calls
    )


def test_renamed_partial_sidecar_is_refenced_without_touching_substitute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    host.fail_sidecar_compose_after = 1
    host.sidecar_compose_failures_remaining = 1
    host.substitute_sidecar_after_inspections = 2
    host.rename_sidecar_before_substitution = True

    with pytest.raises(FenceError, match="re-fenced"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-renamed-partial-sidecar",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    renamed = host.containers["renamed-recovery-sidecar"]
    substituted = host.containers[fence.CANONICAL_SIDECARS[0][0]]
    assert state["phase"] == "unsafe_fenced"
    assert renamed["Id"] == "recovery-sidecar-0"
    assert renamed["State"]["Running"] is False
    assert renamed["HostConfig"]["RestartPolicy"]["Name"] == "no"
    assert substituted["Id"] == "substituted-sidecar-id"
    assert substituted["State"]["Running"] is True
    assert substituted["HostConfig"]["RestartPolicy"]["Name"] == "always"
    assert not any(
        "substituted-sidecar-id" in call
        and call[:2] in {
            ("docker", "rm"),
            ("docker", "stop"),
            ("docker", "update"),
        }
        for call in host.calls
    )


def test_incomplete_successful_sidecar_compose_is_retried_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    host.incomplete_sidecar_compose_successes_remaining = 1

    evidence = recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-incomplete-success-sidecar",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert evidence["phase"] == "recovery_pending_canary"
    assert state["recovery_sidecar_start_attempt"] == 2
    assert set(evidence["recovery_sidecar_container_ids"]) == {
        name for name, _service in fence.CANONICAL_SIDECARS
    }


def test_stubborn_partial_sidecar_cannot_block_writer_refence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    host.fail_sidecar_compose_after = 1
    host.sidecar_compose_failures_remaining = 1
    host.sidecar_stop_failures_remaining = 3

    with pytest.raises(FenceError, match="re-fenced"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-stubborn-partial-sidecar",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    partial_name = fence.CANONICAL_SIDECARS[0][0]
    assert state["phase"] == "unsafe_fenced"
    assert "did not stop" in state["recovery_sidecar_refence_error"]
    assert all(
        not host.containers[name]["State"]["Running"]
        for name in EXPECTED_CONTAINERS
    )
    assert host.containers[partial_name]["State"]["Running"] is True


def test_partial_sidecar_restart_failure_cannot_block_writer_refence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    host.fail_sidecar_compose_after = 1
    host.sidecar_compose_failures_remaining = 2
    host.sidecar_updates_before_failure = 1

    with pytest.raises(FenceError, match="re-fenced"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-sidecar-update-failure",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "unsafe_fenced"
    assert "restart-fence failure" in state["recovery_sidecar_refence_error"]
    assert all(
        not host.containers[name]["State"]["Running"]
        for name in EXPECTED_CONTAINERS
    )


@pytest.mark.parametrize("entrypoint", ["expiry", "boot"])
@pytest.mark.parametrize("created_count", [0, 1, 2])
def test_interrupted_sidecar_start_binds_and_refences_exact_created_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    created_count: int,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="interrupted-sidecar-start",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    expected_names = {
        name for name, _service in fence.CANONICAL_SIDECARS[:created_count]
    }
    for name, _service in fence.CANONICAL_SIDECARS[created_count:]:
        host.containers.pop(name)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["phase"] = "recovery_sidecars_starting"
    state["recovery_deadline_epoch"] = 0
    state.pop("recovery_sidecar_container_ids", None)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    if entrypoint == "expiry":
        evidence = expire_recovery(
            host,
            source_run_id="source-run-1",
            run_id="interrupted-sidecar-start",
            state_path=state_path,
        )
        assert evidence["expired"] is True
    else:
        evidence = fence.reconcile_recovery_on_boot(host, state_path=state_path)

    assert evidence["phase"] == "unsafe_fenced"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(state.get("recovery_sidecar_container_ids", {})) == expected_names
    assert all(not info["State"]["Running"] for info in host.containers.values())


@pytest.mark.parametrize("entrypoint", ["failure", "expiry", "boot"])
@pytest.mark.parametrize("owned_index", [0, 1])
def test_mixed_owned_foreign_sidecars_bind_and_refence_only_owned_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    owned_index: int,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    run_id = f"mixed-sidecar-{entrypoint}-{owned_index}"

    if entrypoint == "failure":
        host.mixed_sidecar_compose_owned_index = owned_index
        with pytest.raises(FenceError, match="re-fenced"):
            recover_unsafe(
                host,
                source_run_id="source-run-1",
                run_id=run_id,
                image_ref=host.old_image_ref,
                revision=host.old_revision,
                state_path=state_path,
            )
    else:
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id=run_id,
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )
        foreign_index = 1 - owned_index
        foreign_name = fence.CANONICAL_SIDECARS[foreign_index][0]
        foreign = host.containers[foreign_name]
        foreign["Id"] = f"foreign-sidecar-{foreign_index}"
        foreign["Config"]["Labels"]["com.docker.compose.project"] = (
            "foreign-project"
        )
        foreign["HostConfig"]["RestartPolicy"]["Name"] = "always"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["phase"] = "recovery_sidecars_starting"
        state["recovery_deadline_epoch"] = 0
        state.pop("recovery_sidecar_container_ids", None)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        if entrypoint == "expiry":
            expire_recovery(
                host,
                source_run_id="source-run-1",
                run_id=run_id,
                state_path=state_path,
            )
        else:
            fence.reconcile_recovery_on_boot(host, state_path=state_path)

    owned_name = fence.CANONICAL_SIDECARS[owned_index][0]
    foreign_name = fence.CANONICAL_SIDECARS[1 - owned_index][0]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(state["recovery_sidecar_container_ids"]) == {owned_name}
    assert host.containers[owned_name]["State"]["Running"] is False
    assert host.containers[foreign_name]["State"]["Running"] is True
    assert (
        host.containers[foreign_name]["HostConfig"]["RestartPolicy"]["Name"]
        == "always"
    )
    assert all(
        not host.containers[name]["State"]["Running"]
        for name in EXPECTED_CONTAINERS
    )


def test_foreign_sidecar_start_failure_still_refences_owned_writers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    host.foreign_sidecar_compose = True

    with pytest.raises(FenceError, match="re-fenced"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-foreign-sidecar",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "unsafe_fenced"
    assert all(
        not host.containers[name]["State"]["Running"]
        for name in EXPECTED_CONTAINERS
    )
    for name, _service in fence.CANONICAL_SIDECARS:
        info = host.containers[name]
        assert info["Config"]["Labels"]["com.docker.compose.project"] == (
            "foreign-project"
        )
        assert info["State"]["Running"] is True
        assert info["HostConfig"]["RestartPolicy"]["Name"] == "always"


def test_invalid_recovery_sidecar_mount_is_bound_and_refenced_not_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    host.recovery_sidecar_data_mount = True

    with pytest.raises(FenceError, match="re-fenced"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-invalid-sidecar-mount",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "unsafe_fenced"
    assert set(state["recovery_sidecar_container_ids"]) == {
        name for name, _service in fence.CANONICAL_SIDECARS
    }
    assert all(not info["State"]["Running"] for info in host.containers.values())
    sidecar_ids = set(state["recovery_sidecar_container_ids"].values())
    assert not any(
        call[:2] == ("docker", "rm")
        and any(identity in call for identity in sidecar_ids)
        for call in host.calls
    )


def test_refence_stops_recovery_sidecars_with_the_writer_fleet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state["sidecar_restart_policies"] = {
        name: "always" for name, _service in fence.CANONICAL_SIDECARS
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-refence-sidecars",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    evidence = refence_recovery(
        host,
        source_run_id="source-run-1",
        run_id="recovery-refence-sidecars",
        state_path=state_path,
    )

    assert evidence["phase"] == "unsafe_fenced"
    assert all(
        not info["State"]["Running"]
        and info["HostConfig"]["RestartPolicy"]["Name"] == "no"
        for info in host.containers.values()
    )


def test_recovery_uses_canonical_restart_posture_for_previously_absent_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-absent-sidecars",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    finalize_recovery(
        host,
        source_run_id="source-run-1",
        run_id="recovery-absent-sidecars",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    for name, _service in fence.CANONICAL_SIDECARS:
        assert (
            host.containers[name]["HostConfig"]["RestartPolicy"]["Name"]
            == "unless-stopped"
        )


def test_recovery_project_sidecars_hand_off_to_the_next_normal_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state["sidecar_restart_policies"] = {
        name: "always" for name, _service in fence.CANONICAL_SIDECARS
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-next-handoff",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    finalize_recovery(
        host,
        source_run_id="source-run-1",
        run_id="recovery-next-handoff",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    recovered_sidecar_ids = {
        name: str(host.containers[name]["Id"])
        for name, _service in fence.CANONICAL_SIDECARS
    }

    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    configured[0] = host.target_image_ref
    evidence = prepare_deploy(
        host,
        image_ref=host.target_image_ref,
        run_id=RUN_ID,
        state_path=state_path,
    )

    assert evidence["removed_sidecar_container_ids"] == recovered_sidecar_ids
    assert host.containers == {}


def test_real_finalized_recovery_state_hands_off_to_next_normal_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    configured = [host.old_image_ref]
    _patch_lifecycle_runtime(monkeypatch, configured)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-handoff",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    finalize_recovery(
        host,
        source_run_id="source-run-1",
        run_id="recovery-handoff",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    recovered_ids = {
        name: str(info["Id"]) for name, info in host.containers.items()
    }

    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )
    configured[0] = host.target_image_ref
    prepare_deploy(
        host,
        image_ref=host.target_image_ref,
        run_id=RUN_ID,
        state_path=state_path,
    )

    assert host.containers == {}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["recovery_handoff"]["container_ids"] == recovered_ids
    assert state["recovery_handoff"]["removal_phase"] == "removed"


def test_recover_unsafe_accepts_compose_down_zero_container_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.containers = {}
    host.start_installs_target = True

    evidence = recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-zero",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    assert evidence["phase"] == "recovery_pending_canary"
    assert set(host.containers) == set(EXPECTED_CONTAINERS)
    assert all(
        info["HostConfig"]["RestartPolicy"]["Name"] == "no"
        for info in host.containers.values()
    )


def test_recover_unsafe_refuses_unrecorded_stopped_container_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["old_container_ids"][EXPECTED_CONTAINERS[0]] = "another-generation"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.start_installs_target = True

    with pytest.raises(FenceError, match="recorded fenced generation"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-wrong-generation",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    assert not any(call[:2] == ("docker", "rm") for call in host.calls)
    assert set(host.containers) == set(EXPECTED_CONTAINERS)


@pytest.mark.parametrize(
    "recorded_source",
    ["old_container_ids", "recovery_container_ids"],
)
def test_stopped_full_fleet_removal_replays_exact_remaining_subset(
    tmp_path: Path,
    recorded_source: str,
):
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    for info in host.containers.values():
        info["State"] = {"Running": False, "Pid": 0}
        info["HostConfig"]["RestartPolicy"]["Name"] = "no"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    recorded = {name: str(info["Id"]) for name, info in host.containers.items()}
    state[recorded_source] = recorded
    state_path.write_text(json.dumps(state), encoding="utf-8")
    original_run = host.run
    interrupted = False

    def interrupt_after_one_remove(
        args: list[str] | tuple[str, ...],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout_seconds: int = fence.HOST_COMMAND_TIMEOUT_SECONDS,
    ) -> str:
        nonlocal interrupted
        command = tuple(args)
        if command[:2] == ("docker", "rm") and not interrupted:
            interrupted = True
            original_run(
                ["docker", "rm", command[2]],
                check=check,
                input_text=input_text,
            )
            raise FenceError("simulated stopped fleet removal interruption")
        return original_run(args, check=check, input_text=input_text)

    host.run = interrupt_after_one_remove  # type: ignore[method-assign]
    with pytest.raises(FenceError, match="simulated stopped fleet"):
        fence._remove_recorded_stopped_fleet_for_recovery(
            host,
            state,
            state_path=state_path,
        )

    interrupted_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert interrupted_state["stopped_fleet_removal"] == {
        "container_ids": recorded,
        "recorded_source": recorded_source,
        "removal_phase": "planned",
    }
    assert len(host.containers) == len(EXPECTED_CONTAINERS) - 1

    host.run = original_run  # type: ignore[method-assign]
    removed = fence._remove_recorded_stopped_fleet_for_recovery(
        host,
        interrupted_state,
        state_path=state_path,
    )

    assert removed == recorded
    assert host.containers == {}
    final_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert final_state["stopped_fleet_removal"]["removal_phase"] == "removed"


def test_stopped_full_fleet_replay_refuses_off_volume_name_substitution_before_removal(
    tmp_path: Path,
):
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    for info in host.containers.values():
        info["State"] = {"Running": False, "Pid": 0}
        info["HostConfig"]["RestartPolicy"]["Name"] = "no"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    recorded = {name: str(info["Id"]) for name, info in host.containers.items()}
    state["old_container_ids"] = recorded
    state_path.write_text(json.dumps(state), encoding="utf-8")
    original_run = host.run
    interrupted = False

    def interrupt_after_one_remove(
        args: list[str] | tuple[str, ...],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout_seconds: int = fence.HOST_COMMAND_TIMEOUT_SECONDS,
    ) -> str:
        nonlocal interrupted
        command = tuple(args)
        if command[:2] == ("docker", "rm") and not interrupted:
            interrupted = True
            original_run(
                ["docker", "rm", command[2]],
                check=check,
                input_text=input_text,
            )
            raise FenceError("simulated stopped fleet interruption")
        return original_run(args, check=check, input_text=input_text)

    host.run = interrupt_after_one_remove  # type: ignore[method-assign]
    with pytest.raises(FenceError, match="simulated stopped fleet"):
        fence._remove_recorded_stopped_fleet_for_recovery(
            host,
            state,
            state_path=state_path,
        )

    missing_name = next(
        name for name in EXPECTED_CONTAINERS if name not in host.containers
    )
    replacement = host._containers(
        "foreign-off-volume", "sha256:old", running=True
    )[missing_name]
    replacement["Mounts"] = []
    host.containers[missing_name] = replacement
    host.run = original_run  # type: ignore[method-assign]
    host.calls.clear()
    interrupted_state = json.loads(state_path.read_text(encoding="utf-8"))

    with pytest.raises(FenceError, match="substituted"):
        fence._remove_recorded_stopped_fleet_for_recovery(
            host,
            interrupted_state,
            state_path=state_path,
        )

    assert not any(call[:2] == ("docker", "rm") for call in host.calls)
    assert host.containers[missing_name]["State"]["Running"] is True


def test_recover_unsafe_replays_interrupted_full_stopped_fleet_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.start_installs_target = True
    original_run = host.run
    interrupted = False

    def interrupt_after_one_remove(
        args: list[str] | tuple[str, ...],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout_seconds: int = fence.HOST_COMMAND_TIMEOUT_SECONDS,
    ) -> str:
        nonlocal interrupted
        command = tuple(args)
        if command[:2] == ("docker", "rm") and not interrupted:
            interrupted = True
            original_run(
                ["docker", "rm", command[2]],
                check=check,
                input_text=input_text,
            )
            raise FenceError("simulated full predecessor interruption")
        return original_run(args, check=check, input_text=input_text)

    host.run = interrupt_after_one_remove  # type: ignore[method-assign]
    with pytest.raises(FenceError, match="simulated full predecessor"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="interrupted-full-predecessor",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    host.run = original_run  # type: ignore[method-assign]
    evidence = recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="replay-full-predecessor",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    assert evidence["phase"] == "recovery_pending_canary"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["stopped_fleet_removal"]["removal_phase"] == "removed"
    assert set(host.containers) == set(EXPECTED_CONTAINERS)


def test_recover_unsafe_refuses_reused_attempt_before_removing_stopped_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["recovery_attempts"] = ["recovery-reused"]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.start_installs_target = True

    with pytest.raises(FenceError, match="identity was already used"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-reused",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    assert not any(call[:2] == ("docker", "rm") for call in host.calls)
    assert set(host.containers) == set(EXPECTED_CONTAINERS)


def test_recovery_arms_expiry_before_starting_any_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.containers = {}
    host.start_installs_target = True

    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-timer-order",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    timer_index = next(
        index
        for index, call in enumerate(host.calls)
        if call[:1] == ("systemd-run",)
    )
    compose_index = next(
        index
        for index, call in enumerate(host.calls)
        if call[:2] == ("docker", "compose")
    )
    assert timer_index < compose_index
    timer_call = host.calls[timer_index]
    assert str(fence.RECOVERY_SCRIPT_PATH) in timer_call


def test_recovery_entrypoint_proof_binds_timer_to_running_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    installed = tmp_path / "retire-cheat-loop-deploy-fence.py"
    installed.write_bytes(Path(fence.__file__).read_bytes())
    installed.chmod(0o755)
    monkeypatch.setattr(fence, "RECOVERY_SCRIPT_PATH", installed)
    monkeypatch.setattr(fence, "__file__", str(installed))

    digest = hashlib.sha256(installed.read_bytes()).hexdigest()
    assert fence._prove_recovery_entrypoint(digest) == digest


def test_recovery_entrypoint_proof_rejects_different_timer_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    running = tmp_path / "running.py"
    timer = tmp_path / "timer.py"
    running.write_text("print('running')\n", encoding="utf-8")
    timer.write_text("print('timer')\n", encoding="utf-8")
    timer.chmod(0o755)
    monkeypatch.setattr(fence, "RECOVERY_SCRIPT_PATH", timer)
    monkeypatch.setattr(fence, "__file__", str(running))

    with pytest.raises(
        FenceError,
        match="does not match the running script",
    ):
        fence._prove_recovery_entrypoint("a" * 64)


def test_finalize_refences_when_restart_policies_drift_after_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-policy-drift",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    for info in host.containers.values():
        info["HostConfig"]["RestartPolicy"]["Name"] = "always"

    with pytest.raises(FenceError, match="re-fenced"):
        finalize_recovery(
            host,
            source_run_id="source-run-1",
            run_id="recovery-policy-drift",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    assert json.loads(state_path.read_text(encoding="utf-8"))["phase"] == (
        "unsafe_fenced"
    )


def test_finalize_refences_when_boot_fence_drifts_after_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-unit-drift",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    host.units[DAEMON_SERVICE].update(active="active", enabled="enabled")

    with pytest.raises(FenceError, match="re-fenced"):
        finalize_recovery(
            host,
            source_run_id="source-run-1",
            run_id="recovery-unit-drift",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    assert json.loads(state_path.read_text(encoding="utf-8"))["phase"] == (
        "unsafe_fenced"
    )


def test_finalize_refences_when_expiry_timer_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-timer-drift",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    host.units[f"{state['recovery_expiry_unit']}.timer"]["active"] = "inactive"

    with pytest.raises(FenceError, match="re-fenced"):
        finalize_recovery(
            host,
            source_run_id="source-run-1",
            run_id="recovery-timer-drift",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    assert json.loads(state_path.read_text(encoding="utf-8"))["phase"] == (
        "unsafe_fenced"
    )


def test_refence_rejects_replaced_container_generation_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-owned-generation",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    for index, info in enumerate(host.containers.values()):
        info["Id"] = f"replacement-{index}"
    calls_before = len(host.calls)

    with pytest.raises(FenceError, match="identities changed"):
        refence_recovery(
            host,
            source_run_id="source-run-1",
            run_id="recovery-owned-generation",
            state_path=state_path,
        )

    assert len(host.calls) == calls_before
    assert all(info["State"]["Running"] for info in host.containers.values())


def test_recovery_failure_rejects_foreign_labels_without_cleanup_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.start_installs_target = True
    original_run = host.run

    def install_foreign_generation(
        command: list[str] | tuple[str, ...],
        **kwargs: Any,
    ) -> str:
        result = original_run(command, **kwargs)
        if tuple(command)[:2] == ("docker", "compose"):
            for info in host.containers.values():
                info["Config"]["Labels"][
                    "com.docker.compose.project"
                ] = "another-generation"
        return result

    monkeypatch.setattr(host, "run", install_foreign_generation)

    with pytest.raises(FenceError, match="re-fence also failed"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-foreign-label",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    compose_index = next(
        index
        for index, call in enumerate(host.calls)
        if call[:2] == ("docker", "compose")
    )
    assert not any(
        call[:2] in {("docker", "update"), ("docker", "stop")}
        for call in host.calls[compose_index + 1 :]
    )


def test_refence_rejects_extra_volume_consumer_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-extra-consumer",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    extra = json.loads(json.dumps(next(iter(host.containers.values()))))
    extra["Id"] = "foreign-extra"
    host.containers["foreign-extra"] = extra
    calls_before = len(host.calls)

    with pytest.raises(FenceError, match="exact owned writer fleet"):
        refence_recovery(
            host,
            source_run_id="source-run-1",
            run_id="recovery-extra-consumer",
            state_path=state_path,
        )

    assert len(host.calls) == calls_before
    assert host.containers["foreign-extra"]["State"]["Running"]


def test_finalize_refences_new_writer_activator_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    missing = "tinyassets-autoheal.timer"
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.units.pop(missing)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["present_restart_racer_units"].remove(missing)
    state["restart_racer_state"].pop(missing)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-new-activator",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    host.units[missing] = {
        "active": "active",
        "enabled": "enabled",
        "load": "loaded",
    }

    with pytest.raises(FenceError, match="re-fenced"):
        finalize_recovery(
            host,
            source_run_id="source-run-1",
            run_id="recovery-new-activator",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    assert json.loads(state_path.read_text(encoding="utf-8"))["phase"] == (
        "unsafe_fenced"
    )


def test_boot_reconciler_refences_interrupted_recovery_after_timer_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-reboot",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    host.units.pop(f"{state['recovery_expiry_unit']}.timer")
    host.units.pop(f"{state['recovery_expiry_unit']}.service")
    for info in host.containers.values():
        info["State"].update(Running=False, Pid=0)
    for unit in (*RESTART_RACER_UNITS, DAEMON_SERVICE):
        host.units[unit].update(
            active="inactive",
            enabled="disabled",
            load="loaded",
        )

    evidence = fence.reconcile_recovery_on_boot(
        host,
        state_path=state_path,
    )

    assert evidence["phase"] == "unsafe_fenced"
    assert json.loads(state_path.read_text(encoding="utf-8"))["phase"] == (
        "unsafe_fenced"
    )


def test_cli_recovery_persists_installed_script_digest_before_compose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.start_installs_target = True
    digest = "c" * 64
    monkeypatch.setattr(
        fence,
        "_prove_recovery_entrypoint",
        lambda expected: digest if expected == digest else "",
    )
    original_run = host.run

    def run_with_digest_check(
        command: list[str] | tuple[str, ...],
        **kwargs: Any,
    ) -> str:
        if tuple(command)[:2] == ("docker", "compose"):
            pre_compose = json.loads(state_path.read_text(encoding="utf-8"))
            assert pre_compose["recovery_script_sha256"] == digest
            assert pre_compose["phase"] == "recovery_starting"
        return original_run(command, **kwargs)

    monkeypatch.setattr(host, "run", run_with_digest_check)
    args = fence._parser().parse_args(
        [
            "--state-path",
            str(state_path),
            "recover-unsafe",
            "--source-run-id",
            "source-run-1",
            "--run-id",
            "recovery-cli",
            "--image-ref",
            host.old_image_ref,
            "--revision",
            host.old_revision,
            "--expected-script-sha256",
            digest,
        ]
    )

    fence._execute(args, host)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["recovery_script_sha256"] == digest
    compose_index = next(
        index
        for index, call in enumerate(host.calls)
        if call[:2] == ("docker", "compose")
    )
    assert state["phase"] == "recovery_pending_canary"
    assert compose_index > 0


def test_expired_recovery_refences_orphaned_runner_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.containers = {}
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-expired",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["recovery_deadline_epoch"] = 0
    state_path.write_text(json.dumps(state), encoding="utf-8")

    evidence = expire_recovery(
        host,
        source_run_id="source-run-1",
        run_id="recovery-expired",
        state_path=state_path,
    )

    assert evidence["expired"] is True
    assert json.loads(state_path.read_text(encoding="utf-8"))["phase"] == "unsafe_fenced"
    assert not any(info["State"]["Running"] for info in host.containers.values())


def test_unexpired_recovery_lease_cannot_be_stolen_or_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.containers = {}
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-live",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    with pytest.raises(FenceError, match="has not expired"):
        expire_recovery(
            host,
            source_run_id="source-run-1",
            run_id="recovery-live",
            state_path=state_path,
        )
    with pytest.raises(FenceError, match="active lease"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-steal",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )
    assert all(info["State"]["Running"] for info in host.containers.values())


def test_expired_pending_recovery_is_reconciled_before_new_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.containers = {}
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-old",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["recovery_deadline_epoch"] = 0
    state_path.write_text(json.dumps(state), encoding="utf-8")

    evidence = recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-new",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    assert evidence["phase"] == "recovery_pending_canary"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["recovery_attempts"] == ["recovery-old", "recovery-new"]
    assert state["recovery_run_id"] == "recovery-new"
    removed = [
        call for call in host.calls if call[:2] == ("docker", "rm")
    ]
    assert len(removed) == 1
    assert set(removed[0][2:]) == {
        f"recovered-{index}"
        for index, _name in enumerate(EXPECTED_CONTAINERS)
    }


def test_expired_partial_foreign_recovery_generation_is_not_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "phase": "recovery_starting",
            "recovery_deadline_epoch": 0,
            "recovery_project_name": "tinyassets-recovery-old",
        }
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {
        "tinyassets-daemon": host._containers(
            "foreign",
            "sha256:old",
            running=True,
        )["tinyassets-daemon"]
    }
    info = host.containers["tinyassets-daemon"]
    info["HostConfig"]["RestartPolicy"]["Name"] = "no"
    info["Config"]["Labels"][
        "com.docker.compose.project"
    ] = "another-project"

    with pytest.raises(FenceError, match="another recovery generation"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-new",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    assert not any(
        call[:2] in {("docker", "stop"), ("docker", "rm")}
        for call in host.calls
    )
    assert info["State"]["Running"]


def test_unexpired_stopped_recovery_lease_is_not_stolen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "phase": "recovery_starting",
            "recovery_deadline_epoch": time.time() + 600,
            "recovery_project_name": "tinyassets-recovery-live",
        }
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {
        name: info
        for name, info in host._containers(
            "leased",
            "sha256:old",
            running=False,
        ).items()
        if name in EXPECTED_CONTAINERS
    }
    for info in host.containers.values():
        info["HostConfig"]["RestartPolicy"]["Name"] = "no"
        info["Config"]["Labels"][
            "com.docker.compose.project"
        ] = "tinyassets-recovery-live"

    with pytest.raises(FenceError, match="active lease"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-new",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    assert not any(
        call[:2] in {
            ("docker", "stop"),
            ("docker", "rm"),
            ("docker", "compose"),
        }
        for call in host.calls
    )
    assert set(host.containers) == set(EXPECTED_CONTAINERS)


@pytest.mark.parametrize(
    "phase",
    [
        "recovery_planned",
        "recovery_starting",
        "recovery_pending_canary",
        "safe_fleet",
    ],
)
def test_expired_intermediate_recovery_phase_replays_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "phase": phase,
            "run_id": "interrupted-run",
            "recovery_run_id": "interrupted-run",
            "recovery_attempts": ["interrupted-run"],
            "recovery_deadline_epoch": 0,
        }
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True

    evidence = recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id=f"replay-{phase}",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    assert evidence["phase"] == "recovery_pending_canary"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["recovery_attempts"] == [
        "interrupted-run",
        f"replay-{phase}",
    ]
    assert all(
        info["HostConfig"]["RestartPolicy"]["Name"] == "no"
        for info in host.containers.values()
    )


def test_finalize_recovery_refences_silent_restart_policy_misapplication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.containers = {}
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-policy",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    host.restart_policy_override = "no"

    with pytest.raises(FenceError, match="re-fenced"):
        finalize_recovery(
            host,
            source_run_id="source-run-1",
            run_id="recovery-policy",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "unsafe_fenced"
    assert not any(info["State"]["Running"] for info in host.containers.values())


@pytest.mark.parametrize("saved_policy", ["no", "on-failure", "always"])
def test_finalize_normalizes_saved_sidecar_policy_to_canonical_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    saved_policy: str,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state["sidecar_restart_policies"] = {
        name: saved_policy for name, _service in fence.CANONICAL_SIDECARS
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-saved-no",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    finalize_recovery(
        host,
        source_run_id="source-run-1",
        run_id="recovery-saved-no",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    for name, _service in fence.CANONICAL_SIDECARS:
        assert (
            host.containers[name]["HostConfig"]["RestartPolicy"]["Name"]
            == "unless-stopped"
        )


def test_boot_reconciliation_refences_mid_policy_restoration_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sidecar_handoff"] = {
        "container_ids": {},
        "project_name": "tinyassets",
        "removal_phase": "removed",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    host.containers = {}
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-policy-interruption",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    host.restart_restore_updates = 0
    host.interrupt_restart_restore_after = 1

    with pytest.raises(KeyboardInterrupt, match="simulated host loss"):
        finalize_recovery(
            host,
            source_run_id="source-run-1",
            run_id="recovery-policy-interruption",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )

    for info in host.containers.values():
        restarts = info["HostConfig"]["RestartPolicy"]["Name"] != "no"
        info["State"]["Running"] = restarts
        info["State"]["Pid"] = 9999 if restarts else 0
    host.interrupt_restart_restore_after = 0
    evidence = fence.reconcile_recovery_on_boot(host, state_path=state_path)

    assert evidence["phase"] == "unsafe_fenced"
    assert all(not info["State"]["Running"] for info in host.containers.values())


@pytest.mark.parametrize("phase", ["canary_accepted", "finalizing"])
def test_expiry_refences_interrupted_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.containers = {}
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-finalize-loss",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["phase"] = phase
    state["recovery_deadline_epoch"] = 0
    state_path.write_text(json.dumps(state), encoding="utf-8")

    evidence = expire_recovery(
        host,
        source_run_id="source-run-1",
        run_id="recovery-finalize-loss",
        state_path=state_path,
    )

    assert evidence["expired"] is True
    assert json.loads(state_path.read_text(encoding="utf-8"))["phase"] == "unsafe_fenced"
    assert not any(info["State"]["Running"] for info in host.containers.values())


def test_recover_unsafe_requires_exact_runner_bound_identity_before_wal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    before = state_path.read_bytes()

    with pytest.raises(FenceError, match="runner-bound|recorded"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-wrong-image",
            image_ref=host.target_image_ref,
            revision=host.target_revision,
            state_path=state_path,
        )

    assert state_path.read_bytes() == before
    assert host.calls == []


def test_recover_unsafe_requires_complete_saved_restart_policies_before_wal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["old_restart_policies"].pop(EXPECTED_CONTAINERS[0])
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()

    with pytest.raises(FenceError, match="restart polic"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-policy-gap",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )
    assert state_path.read_bytes() == before
    assert host.calls == []


def test_recovery_refence_wrong_source_does_not_mutate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)

    with pytest.raises(FenceError, match="not owned"):
        refence_recovery(
            host,
            source_run_id="wrong-source",
            run_id="unrelated-run",
            state_path=state_path,
        )
    assert host.calls == []


def test_recovery_refence_canary_failure_stops_current_recovered_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    host.start_installs_target = True
    recover_unsafe(
        host,
        source_run_id="source-run-1",
        run_id="recovery-canary",
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        state_path=state_path,
    )

    evidence = refence_recovery(
        host,
        source_run_id="source-run-1",
        run_id="recovery-canary",
        state_path=state_path,
    )

    assert evidence["phase"] == "unsafe_fenced"
    assert not any(info["State"]["Running"] for info in host.containers.values())


def test_restore_handles_active_timer_requiring_static_service_to_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    state = {
        "schema_version": 1,
        "owner": "retire-cheat-loop task 2.1",
        "run_id": RUN_ID,
        "phase": "safe_fleet",
        "old_container_ids": {},
        "restart_racer_state": {
            unit: host.unit_state(unit) for unit in RESTART_RACER_UNITS
        },
        "daemon_service_state": host.unit_state(DAEMON_SERVICE),
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    for unit in (*RESTART_RACER_UNITS, DAEMON_SERVICE):
        host.units[unit]["enabled"] = "masked-runtime"
        host.units[unit]["load"] = "masked"
    original_run = host.run
    original_state = host.unit_state
    service_reads = {"count": 0}

    def timer_requires_service(args: Any, **kwargs: Any) -> str:
        result = original_run(args, **kwargs)
        if tuple(args) == ("systemctl", "start", "daemon-watchdog.timer"):
            host.units["daemon-watchdog.service"]["active"] = "activating"
        return result

    def settling_state(unit: str) -> dict[str, str]:
        value = original_state(unit)
        if (
            unit == "daemon-watchdog.service"
            and value["active"] == "activating"
        ):
            service_reads["count"] += 1
            if service_reads["count"] >= 2:
                host.units[unit]["active"] = "inactive"
                value["active"] = "inactive"
        return value

    monkeypatch.setattr(host, "run", timer_requires_service)
    monkeypatch.setattr(host, "unit_state", settling_state)
    monkeypatch.setattr(fence.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        fence,
        "prove",
        lambda *_args, **_kwargs: {"safe": True, "phase": "safe_fleet"},
    )

    evidence = restore_if_safe(
        host,
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )

    assert evidence["phase"] == "restored"
    assert service_reads["count"] >= 2


def test_preflight_records_only_settled_unit_states_before_fencing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    host.units["tinyassets-watchdog.service"]["active"] = "activating"
    host.units[DAEMON_SERVICE].update(
        {"active": "activating", "enabled": "disabled"}
    )
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    monkeypatch.setattr(fence.time, "sleep", lambda _seconds: None)
    state_path = tmp_path / "state.json"
    original_state = host.unit_state
    reads = {"tinyassets-watchdog.service": 0, DAEMON_SERVICE: 0}

    def settling_state(unit: str) -> dict[str, str]:
        value = original_state(unit)
        if unit in reads and value["active"] == "activating":
            reads[unit] += 1
            if reads[unit] >= 2:
                host.units[unit]["active"] = (
                    "active" if unit == DAEMON_SERVICE else "inactive"
                )
                value["active"] = host.units[unit]["active"]
        return value

    monkeypatch.setattr(host, "unit_state", settling_state)

    preflight(
        host,
        image_ref=host.target_image_ref,
        target_revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["restart_racer_state"]["tinyassets-watchdog.service"] == {
        "active": "inactive",
        "enabled": "static",
    }
    assert state["daemon_service_state"] == {
        "active": "active",
        "enabled": "disabled",
    }
    assert all(count >= 2 for count in reads.values())


@pytest.mark.parametrize("transient", ["activating", "deactivating", "reloading"])
def test_preflight_refuses_nonsettling_transient_unit_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transient: str
):
    host = LifecycleHost(tmp_path)
    host.units[DAEMON_SERVICE]["active"] = transient
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    monkeypatch.setattr(fence.time, "sleep", lambda _seconds: None)
    state_path = tmp_path / "state.json"

    with pytest.raises(FenceError, match="unit snapshot did not settle"):
        preflight(
            host,
            image_ref=host.target_image_ref,
            target_revision=host.target_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    assert not state_path.exists()
    assert not any(
        call[:2]
        in {
            ("systemctl", "disable"),
            ("systemctl", "stop"),
            ("systemctl", "mask"),
            ("docker", "update"),
        }
        for call in host.calls
    )


@pytest.mark.parametrize("saved_active", ["active", "activating", "inactive", "failed"])
def test_successful_normal_handoff_converges_daemon_to_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saved_active: str
):
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    state = {
        "schema_version": 1,
        "owner": "retire-cheat-loop task 2.1",
        "run_id": RUN_ID,
        "phase": "post_canary_proved",
        "target_image_ref": host.target_image_ref,
        "target_revision": host.target_revision,
        "previous_image_ref": host.old_image_ref,
        "previous_revision": host.old_revision,
        "old_container_ids": {},
        "restart_racer_state": {
            unit: host.unit_state(unit) for unit in RESTART_RACER_UNITS
        },
        "daemon_service_state": {
            "active": saved_active,
            "enabled": "disabled",
        },
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    for unit in (*RESTART_RACER_UNITS, DAEMON_SERVICE):
        host.units[unit]["enabled"] = "masked-runtime"
        host.units[unit]["load"] = "masked"
    original_run = host.run

    def preserve_disabled_daemon(args: Any, **kwargs: Any) -> str:
        result = original_run(args, **kwargs)
        if tuple(args)[:3] == ("systemctl", "unmask", "--runtime"):
            host.units[DAEMON_SERVICE]["enabled"] = "disabled"
        return result

    monkeypatch.setattr(host, "run", preserve_disabled_daemon)
    monkeypatch.setattr(
        fence,
        "prove",
        lambda *_args, **_kwargs: {"safe": True, "phase": "safe_fleet"},
    )

    evidence = restore_if_safe(
        host,
        image_ref=host.target_image_ref,
        revision=host.target_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )

    expected_daemon = {"active": "active", "enabled": "disabled"}
    assert evidence["expected_restored_unit_states"][DAEMON_SERVICE] == (
        expected_daemon
    )
    assert evidence["restored_unit_states"][DAEMON_SERVICE] == expected_daemon


@pytest.mark.parametrize("saved_active", ["inactive", "failed"])
def test_failed_forward_rollback_preserves_daemon_predecessor_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saved_active: str
):
    host = LifecycleHost(tmp_path)
    host.units[DAEMON_SERVICE].update(
        {"active": saved_active, "enabled": "disabled"}
    )
    state_path = tmp_path / "state.json"
    state = {
        "schema_version": 1,
        "owner": "retire-cheat-loop task 2.1",
        "run_id": RUN_ID,
        "phase": "safe_fleet",
        "target_image_ref": host.target_image_ref,
        "target_revision": host.target_revision,
        "previous_image_ref": host.old_image_ref,
        "previous_revision": host.old_revision,
        "old_container_ids": {},
        "restart_racer_state": {
            unit: host.unit_state(unit) for unit in RESTART_RACER_UNITS
        },
        "daemon_service_state": {
            "active": saved_active,
            "enabled": "disabled",
        },
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    for unit in (*RESTART_RACER_UNITS, DAEMON_SERVICE):
        host.units[unit]["enabled"] = "masked-runtime"
        host.units[unit]["load"] = "masked"
    original_run = host.run

    def preserve_disabled_daemon(args: Any, **kwargs: Any) -> str:
        result = original_run(args, **kwargs)
        if tuple(args)[:3] == ("systemctl", "unmask", "--runtime"):
            host.units[DAEMON_SERVICE]["enabled"] = "disabled"
        return result

    monkeypatch.setattr(host, "run", preserve_disabled_daemon)
    monkeypatch.setattr(
        fence,
        "prove",
        lambda *_args, **_kwargs: {"safe": True, "phase": "safe_fleet"},
    )

    evidence = restore_if_safe(
        host,
        image_ref=host.old_image_ref,
        revision=host.old_revision,
        run_id=RUN_ID,
        state_path=state_path,
    )

    expected_daemon = {"active": saved_active, "enabled": "disabled"}
    assert evidence["expected_restored_unit_states"][DAEMON_SERVICE] == (
        expected_daemon
    )
    assert evidence["restored_unit_states"][DAEMON_SERVICE] == expected_daemon
    assert ("systemctl", "start", DAEMON_SERVICE) not in host.calls


def test_restore_proof_failure_precedes_all_unit_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "run_id": RUN_ID,
                "phase": "safe_fleet",
                "old_container_ids": {},
                "restart_racer_state": {
                    unit: host.unit_state(unit) for unit in RESTART_RACER_UNITS
                },
                "daemon_service_state": {
                    "active": "failed",
                    "enabled": "disabled",
                },
            }
        ),
        encoding="utf-8",
    )
    for unit in (*RESTART_RACER_UNITS, DAEMON_SERVICE):
        host.units[unit]["enabled"] = "masked-runtime"
        host.units[unit]["load"] = "masked"
    monkeypatch.setattr(
        fence,
        "prove",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FenceError("injected normal-deploy proof failure")
        ),
    )

    with pytest.raises(FenceError, match="injected normal-deploy proof failure"):
        restore_if_safe(
            host,
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    unit_mutations = {"disable", "stop", "mask", "unmask", "enable", "start"}
    assert not any(
        call[0] == "systemctl" and call[1] in unit_mutations
        for call in host.calls
    )


@pytest.mark.parametrize(
    "daemon_state",
    [
        {"active": "unknown", "enabled": "disabled"},
        {"active": "failed", "enabled": "unknown"},
    ],
)
def test_restore_rejects_invalid_persisted_daemon_state_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    daemon_state: dict[str, str],
):
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "retire-cheat-loop task 2.1",
                "run_id": RUN_ID,
                "phase": "safe_fleet",
                "old_container_ids": {},
                "restart_racer_state": {
                    unit: host.unit_state(unit) for unit in RESTART_RACER_UNITS
                },
                "daemon_service_state": daemon_state,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fence,
        "prove",
        lambda *_args, **_kwargs: {"safe": True, "phase": "safe_fleet"},
    )
    monkeypatch.setattr(
        fence,
        "_wait_units_restored",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("restore wait reached after invalid saved state")
        ),
    )

    with pytest.raises(FenceError, match="saved daemon unit state is invalid"):
        restore_if_safe(
            host,
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            run_id=RUN_ID,
            state_path=state_path,
        )

    assert not any(call[:2] == ("systemctl", "unmask") for call in host.calls)


def test_recover_unsafe_refences_after_mutation_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    original_run = host.run

    def fail_start(args: Any, **kwargs: Any) -> str:
        if tuple(args)[:2] == ("docker", "compose"):
            raise OSError("injected start I/O failure")
        return original_run(args, **kwargs)

    monkeypatch.setattr(host, "run", fail_start)
    with pytest.raises(FenceError, match="re-fenced"):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-1",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )
    assert json.loads(state_path.read_text(encoding="utf-8"))["phase"] == "unsafe_fenced"


@pytest.mark.parametrize("fault", ["extra", "queue", "receipt"])
def test_recover_unsafe_refuses_unproved_fenced_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
):
    host = LifecycleHost(tmp_path)
    _patch_lifecycle_runtime(monkeypatch, [host.old_image_ref])
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    if fault == "extra":
        host.containers["extra"] = host._containers("extra", "sha256:old", running=False)[
            "tinyassets-daemon"
        ]
    elif fault == "queue":
        (tmp_path / "branch_tasks.json").write_text(
            json.dumps(
                [
                    {
                        "branch_task_id": "x",
                        "request_type": "bug_investigation",
                        "status": "pending",
                    }
                ]
            ),
            encoding="utf-8",
        )
    else:
        with sqlite3.connect(tmp_path / "wiki_trigger_attempts.db") as connection:
            connection.execute(
                "INSERT INTO wiki_trigger_attempts "
                "(trigger_attempt_id,request_id,request_kind,request_page,status,attempted_at) "
                "VALUES ('x','x','bug','x','queued','now')"
            )

    with pytest.raises(FenceError):
        recover_unsafe(
            host,
            source_run_id="source-run-1",
            run_id="recovery-1",
            image_ref=host.old_image_ref,
            revision=host.old_revision,
            state_path=state_path,
        )
    assert json.loads(state_path.read_text(encoding="utf-8"))["phase"] == "unsafe_fenced"


def test_restore_waits_for_activating_unit_then_exact_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    states = iter(["activating", "inactive"])
    host.units["daemon-watchdog.service"]["active"] = "activating"
    original = host.unit_state

    def settling(unit: str) -> dict[str, str]:
        value = original(unit)
        if unit == "daemon-watchdog.service":
            value["active"] = next(states, "inactive")
        return value

    monkeypatch.setattr(host, "unit_state", settling)
    monkeypatch.setattr(fence.time, "sleep", lambda _seconds: None)
    actual = fence._wait_units_restored(
        host,
        {"daemon-watchdog.service": {"active": "inactive", "enabled": "static"}},
        timeout_seconds=91,
        delay_seconds=1,
    )
    assert actual["daemon-watchdog.service"]["active"] == "inactive"


def test_restore_timeout_reports_last_transient_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = LifecycleHost(tmp_path)
    host.units["daemon-watchdog.service"]["active"] = "activating"
    monkeypatch.setattr(fence.time, "sleep", lambda _seconds: None)
    with pytest.raises(FenceError, match="activating"):
        fence._wait_units_restored(
            host,
            {"daemon-watchdog.service": {"active": "inactive", "enabled": "static"}},
            timeout_seconds=91,
            delay_seconds=100,
        )


def test_parent_directory_fsync_is_attempted_on_posix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[str, int | Path]] = []
    monkeypatch.setattr(fence.os, "name", "posix")
    monkeypatch.setattr(
        fence.os,
        "open",
        lambda path, _flags: calls.append(("open", path)) or 91,
    )
    monkeypatch.setattr(
        fence.os,
        "fsync",
        lambda descriptor: calls.append(("fsync", descriptor)),
    )
    monkeypatch.setattr(
        fence.os,
        "close",
        lambda descriptor: calls.append(("close", descriptor)),
    )

    fence._fsync_parent(tmp_path / "state.json")

    assert calls == [
        ("open", tmp_path),
        ("fsync", 91),
        ("close", 91),
    ]


def _record_extra_consumer(state_path, name, *, running=False, cid="c" * 64):
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["extra_volume_consumers"] = {name: {"id": cid, "running": running}}
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _validate(host, state_path, retire=()):
    return _validate_unsafe_recovery_source(
        host,
        source_run_id="source-run-1",
        image_ref=host.target_image_ref,
        revision=host.target_revision,
        state_path=state_path,
        retire_extra_consumers=tuple(retire),
    )


def test_recorded_extra_consumer_still_blocks_recovery_by_default(tmp_path):
    """The default must stay a hard refusal — retirement is opt-in only."""
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    _record_extra_consumer(state_path, "tinyassets-worker-founder")

    with pytest.raises(FenceError, match="extra production-volume consumers"):
        _validate(host, state_path)


def test_an_exactly_named_stopped_extra_consumer_clears_that_gate(tmp_path):
    """The in-band way back from a fence that outlived its cause.

    Live 2026-08-05: a deploy added a container the exact-fleet fence did not
    admit. Cleanup fenced the whole fleet — daemon included, so /mcp returned
    502 — AND recorded the newcomer, which made recovery refuse forever.
    Reverting the container is not enough; the RECORD outlives it.

    Asserted precisely: WITHOUT retirement the extra-consumer gate raises;
    WITH it, that gate no longer does. Later validation still applies and is
    not weakened here -- this test deliberately does not assert recovery
    succeeds, only that this one refusal is lifted.
    """
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    _record_extra_consumer(state_path, "tinyassets-worker-founder")

    with pytest.raises(FenceError, match="extra production-volume consumers"):
        _validate(host, state_path)

    try:
        _validate(host, state_path, retire=("tinyassets-worker-founder",))
    except FenceError as exc:
        assert "extra production-volume consumers" not in str(exc)


def test_retirement_refuses_a_container_that_is_running_right_now(tmp_path):
    """The gate is the LIVE container, not the recorded flag."""
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    _record_extra_consumer(state_path, "tinyassets-worker-founder")
    host.containers["tinyassets-worker-founder"] = {
        "Id": "c" * 64,
        "State": {"Running": True, "Pid": 42},
        "HostConfig": {"RestartPolicy": {"Name": "always"}},
    }

    with pytest.raises(FenceError, match="RUNNING extra volume consumer"):
        _validate(host, state_path, retire=("tinyassets-worker-founder",))


def test_retirement_refuses_when_it_cannot_prove_the_container_is_stopped(
    tmp_path,
    monkeypatch,
):
    """Fail closed: an inspection error is not proof of absence.

    A container that STILL EXISTS but cannot be inspected must refuse --
    otherwise a transient docker failure would retire the record for a
    container still writing to the production volume.
    """
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    _record_extra_consumer(state_path, "tinyassets-worker-founder")
    # Exists (so `ps -a` reports it) but inspection fails.
    host.containers["tinyassets-worker-founder"] = {"Id": "c" * 64}
    monkeypatch.setattr(
        type(host),
        "container_info",
        lambda self, name: (_ for _ in ()).throw(FenceError("boom")),
    )

    with pytest.raises(FenceError, match="cannot prove"):
        _validate(host, state_path, retire=("tinyassets-worker-founder",))


def test_retirement_can_never_target_an_expected_fleet_container(tmp_path):
    """The whole fleet must stay unretirable — otherwise this is a bypass."""
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    _record_extra_consumer(state_path, "tinyassets-worker-founder")

    for name in EXPECTED_CONTAINERS:
        with pytest.raises(FenceError, match="expected fleet container"):
            _validate(host, state_path, retire=(name,))


def test_retirement_refuses_a_name_that_was_never_recorded(tmp_path):
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    _record_extra_consumer(state_path, "tinyassets-worker-founder")

    with pytest.raises(FenceError, match="unrecorded extra volume consumer"):
        _validate(host, state_path, retire=("tinyassets-worker-ghost",))


def test_retirement_purges_every_fleet_enumeration_not_just_the_extras(
    tmp_path,
    monkeypatch,
):
    """A retired container must vanish from every recorded fleet list.

    `_validate_stopped_fleet` requires the removal plan's container_ids to
    equal EXPECTED_CONTAINERS and to match its recorded_source map. Those were
    captured while the retired container existed, so clearing only
    `extra_volume_consumers` leaves the enumerations one name too long and the
    NEXT recovery fails with "stopped fleet removal intent is invalid" --
    observed live on recovery 31049384995.
    """
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    _record_extra_consumer(state_path, "tinyassets-worker-founder")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["old_container_ids"]["tinyassets-worker-founder"] = "c" * 64
    state["stopped_fleet_removal"] = {
        "removal_phase": "planned",
        "recorded_source": "old_container_ids",
        "container_ids": dict(state["old_container_ids"]),
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.retire_cheat_loop_deploy_fence._configured_image",
        lambda: host.target_image_ref,
    )

    # Later, unrelated validation may still refuse in this fixture; what must
    # NOT survive the purge is the removal-intent refusal it exists for.
    try:
        _validate(host, state_path, retire=("tinyassets-worker-founder",))
    except FenceError as exc:
        assert "stopped fleet removal intent is invalid" not in str(exc)


def test_purge_runs_even_when_a_prior_attempt_cleared_the_extras(tmp_path):
    """The second recovery attempt must still purge the enumerations.

    Live 2026-08-05: recovery 31048315265 cleared `extra_volume_consumers`,
    so recoveries 31049384995 and 31049698106 found it empty, skipped the
    retirement block entirely, and refused with "stopped fleet removal intent
    is invalid" because the stale removal plan still named the container.
    """
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["extra_volume_consumers"] = {}          # a prior attempt cleared it
    state["old_container_ids"]["tinyassets-worker-founder"] = "c" * 64
    state["stopped_fleet_removal"] = {
        "removal_phase": "planned",
        "recorded_source": "old_container_ids",
        "container_ids": dict(state["old_container_ids"]),
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    try:
        _validate(host, state_path, retire=("tinyassets-worker-founder",))
    except FenceError as exc:
        assert "stopped fleet removal intent is invalid" not in str(exc)
        assert "unrecorded extra volume consumer" not in str(exc)


def test_a_completed_removal_plan_does_not_deadlock_recovery(tmp_path):
    """A finished removal must not lock the fleet out of recovery forever.

    Observed live 2026-08-05: recovery 31048315265 started a fresh generation
    and was then re-fenced, leaving stopped_fleet_removal describing the
    generation it REMOVED while recovery_container_ids described the one it
    STARTED — same keys, different ids. Every later recovery refused with
    "stopped fleet removal intent is invalid", with production down.
    """
    host = LifecycleHost(tmp_path)
    host.install_target_fleet()
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    live = {
        name: str(host.containers[name]["Id"]) for name in EXPECTED_CONTAINERS
    }
    state["recovery_container_ids"] = live
    state["stopped_fleet_removal"] = {
        "removal_phase": "removed",
        "recorded_source": "recovery_container_ids",
        # the generation that was removed — different ids, same names
        "container_ids": {name: "d" * 64 for name in EXPECTED_CONTAINERS},
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    try:
        _remove_recorded_stopped_fleet_for_recovery(
            host, state, state_path=state_path
        )
    except FenceError as exc:
        assert "stopped fleet removal intent is invalid" not in str(exc)


def test_a_planned_removal_plan_is_still_strictly_validated(tmp_path):
    """Accept-direction control: an in-flight plan keeps its full checks."""
    host = LifecycleHost(tmp_path)
    host.install_target_fleet()
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["stopped_fleet_removal"] = {
        "removal_phase": "planned",
        "recorded_source": "recovery_container_ids",
        "container_ids": {name: "d" * 64 for name in EXPECTED_CONTAINERS},
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(FenceError, match="stopped fleet removal intent is invalid"):
        _remove_recorded_stopped_fleet_for_recovery(
            host, state, state_path=state_path
        )


def test_retirement_removes_the_stopped_container_not_just_the_record(tmp_path):
    """A retired-but-present container still blocks the very next check.

    Live 2026-08-05: recovery 31057720758 cleared the record for a leftover
    `tinyassets-slack-proof` container and then refused with "fenced volume has
    partial or extra writer containers", because the stopped container still
    mounts the volume and `volume_container_names()` still reports it.
    """
    host = LifecycleHost(tmp_path)
    state_path = tmp_path / "state.json"
    _unsafe_recovery_state(host, state_path)
    _record_extra_consumer(state_path, "tinyassets-leftover")
    host.containers["tinyassets-leftover"] = {
        "Id": "e" * 64,
        "State": {"Running": False, "Pid": 0},
        "HostConfig": {"RestartPolicy": {"Name": "no"}},
    }

    try:
        _validate(host, state_path, retire=("tinyassets-leftover",))
    except FenceError as exc:
        assert "extra production-volume consumers" not in str(exc)

    # The container itself must be gone, not merely unrecorded.
    assert "tinyassets-leftover" not in host.containers


def test_restore_quiesced_mutation_order_and_no_docker_start():
    """Level 2 auto-rollback safe ordering (transition-order proof).

    restore_quiesced reverses a proved-but-uncommitted quiesce. Its host
    mutations must be strictly ordered so a crash at any point stays
    recoverable and it never declares `restored` before proving the fleet:
      validate recorded intent -> durably mark `restoring_quiesced`
      -> unmask -> systemctl start (compose-up, NEVER `docker start <old-id>`)
      -> prove loopback MCP health -> durably mark `restored`.
    """
    source = inspect.getsource(fence.restore_quiesced)

    pos_intent = source.index("_recorded_quiesced_restore_intent(state)")
    pos_restoring = source.index('state["phase"] = "restoring_quiesced"')
    pos_unmask = source.index('"unmask", "--runtime"')
    pos_start = source.index('"start", DAEMON_SERVICE')
    pos_health = source.index("_wait_loopback_mcp_health(host)")
    pos_restored = source.index('state["phase"] = "restored"')

    assert (
        pos_intent
        < pos_restoring
        < pos_unmask
        < pos_start
        < pos_health
        < pos_restored
    ), (pos_intent, pos_restoring, pos_unmask, pos_start, pos_health, pos_restored)

    # Recovery is compose-up on the UNCHANGED image via the daemon unit; the
    # removed old containers are never resurrected by id.
    assert 'docker", "start' not in source
    assert 'docker", "restart' not in source


def test_restore_quiesced_only_acts_when_image_never_committed():
    """Safe-case gate: the configured image must still equal the recorded
    previous image — i.e. the new image never committed. Any disagreement is a
    no-op, never a host mutation."""
    source = inspect.getsource(fence.restore_quiesced)
    assert "configured_image_ref = _configured_image()" in source
    assert "configured_image_ref != previous_image_ref" in source
    assert "_quiesced_restore_not_applicable" in source
    # The proved-quiesce phases it will reverse, and nothing else.
    assert 'phase not in {"preflight_proved", "restoring_quiesced"}' in source


def test_restore_quiesced_registered_in_cli_and_dispatch():
    parser_src = inspect.getsource(fence._parser)
    execute_src = inspect.getsource(fence._execute)
    assert '"restore-quiesced"' in parser_src
    assert 'args.command == "restore-quiesced"' in execute_src
    assert "return restore_quiesced(" in execute_src


def test_boot_reconciler_refences_interrupted_quiesced_restore():
    """A crash mid-restore leaves phase `restoring_quiesced`; on reboot the
    reconciler must re-fence (quiesce) rather than leave a half-restored fleet
    running with an unproven posture."""
    source = inspect.getsource(fence.reconcile_recovery_on_boot)
    assert 'phase == "restoring_quiesced"' in source
    assert "return quiesce_unsafe(" in source


def test_writer_unit_stop_uses_a_budget_above_the_unit_stop_timeout(monkeypatch):
    """The daemon's ExecStop (`docker compose down`, systemd TimeoutStopSec=60s) can
    take the full ~60s, which exceeds the 45s default host budget. If the fence stops
    it with the default, the stop trips `host command timed out after 45s: systemctl
    stop`, the fence downs the fleet mid-quiesce, and the no-image-mutation rollback
    is skipped — a production outage (live 2026-08-10, deploy run 31430209381).

    Mutation-check: drop `timeout_seconds=WRITER_UNIT_STOP_TIMEOUT_SECONDS` from the
    stop call and the daemon is stopped with the 45s default — this goes red.
    """
    calls: list[tuple[list[str], int]] = []

    class RecordingHost:
        def unit_present(self, _unit: str) -> bool:
            return True

        def run(
            self,
            args: Any,
            *,
            check: bool = True,
            input_text: str | None = None,
            timeout_seconds: int = fence.HOST_COMMAND_TIMEOUT_SECONDS,
        ) -> str:
            del check, input_text
            calls.append((list(args), timeout_seconds))
            return ""

    monkeypatch.setattr(fence, "_apply_boot_fence", lambda *a, **k: None)
    monkeypatch.setattr(fence, "_wait_units_quiesced", lambda *a, **k: None)

    fence._stop_and_mask_writer_units(RecordingHost())

    stop_calls = [(args, t) for args, t in calls if args[:2] == ["systemctl", "stop"]]
    daemon_budgets = [t for args, t in stop_calls if fence.DAEMON_SERVICE in args]
    racer_budgets = [t for args, t in stop_calls if fence.DAEMON_SERVICE not in args]

    # The daemon gets the long budget; the racers KEEP the fast default so a hung
    # racer can't consume 120s each and cumulatively blow the 300s preflight budget.
    assert daemon_budgets, "the daemon writer unit was never stopped"
    assert all(t == fence.WRITER_UNIT_STOP_TIMEOUT_SECONDS for t in daemon_budgets)
    assert racer_budgets, "the racer units were never stopped"
    assert all(t == fence.HOST_COMMAND_TIMEOUT_SECONDS for t in racer_budgets)

    # The budget must MEANINGFULLY exceed the daemon unit's TimeoutStopSec (60s) and
    # stay under the workflow's outer `systemd-run RuntimeMaxSec` (300s) — a value of
    # 46 would beat the 45s default yet still be too small (Codex 2026-08-10).
    assert 60 < fence.WRITER_UNIT_STOP_TIMEOUT_SECONDS < 300
