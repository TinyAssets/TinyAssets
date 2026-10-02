"""Project-wide daemon identity facade.

This module presents daemon/soul language while the existing SQLite
substrate still stores rows in the transitional author_* tables. The mapping
keeps the migration additive: public callers use daemon_id, while storage can
move later without changing the caller contract.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyassets import daemon_server
from tinyassets.platform_runtime_provenance import (
    PLATFORM_NOT_CLOUD_REASON,
    cached_process_is_cloud_admitted,
    resolve_process_cloud_admission,
)
from tinyassets.principals import has_named_principal, named_principal
from tinyassets.sqlite_connection import ClosingConnection
from tinyassets.storage import DB_FILENAME
from tinyassets.storage.request_admissions import (
    OPERATOR_CAPABILITY,
    QUEUE_PROTOCOL_VERSION,
)

SOULLESS_SOUL_TEXT = "Default soulless daemon. Uses the platform dispatcher policy."
VALID_SOUL_MODES = {"soul", "soulless"}
PROJECT_LOOP_FLAG = "project_loop_default"
RUNTIME_CONTROL_STATUSES = {
    "pause": "paused",
    "resume": "provisioned",
    "restart": "restart_requested",
}


@dataclass(frozen=True)
class StaleCloudWorkerRuntimeRetirementPlan:
    """CAS evidence for one reviewed stale cloud-worker runtime."""

    instance_id: str
    universe_id: str
    worker_id: str
    updated_at: float
    row_digest: str


def _runtime_heartbeat_is_stale(
    base_path: str | Path,
    row: Mapping[str, Any],
    *,
    cutoff: datetime,
    now: datetime,
) -> bool:
    from tinyassets.api.universe import _worker_liveness

    metadata = json.loads(str(row["metadata_json"] or "{}"))
    worker_id = str(metadata.get("worker_id") or "")
    liveness = _worker_liveness(
        Path(base_path) / str(row["universe_id"]),
        now=now,
    )
    matching = [
        worker
        for worker in liveness.get("workers", [])
        if (
            str(worker.get("runtime_instance_id") or "")
            == str(row["instance_id"])
            or str(worker.get("worker_id") or "") == worker_id
        )
    ]
    if not matching:
        return not any(
            worker.get("parse_error")
            and not worker.get("worker_id")
            and not worker.get("runtime_instance_id")
            for worker in liveness.get("workers", [])
        )
    threshold_seconds = max(0.0, (now - cutoff).total_seconds())
    return all(
        not worker.get("parse_error")
        and float(worker.get("beat_age_s", -1)) >= threshold_seconds
        for worker in matching
    )


def plan_stale_cloud_worker_runtime_retirement(
    base_path: str | Path,
    *,
    cutoff: datetime,
) -> list[StaleCloudWorkerRuntimeRetirementPlan]:
    """Read a mutation-free plan for dead retired-fleet runtimes."""
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    cutoff = cutoff.astimezone(timezone.utc)
    database = Path(base_path) / DB_FILENAME
    if not database.is_file():
        return []
    uri = f"{database.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True, factory=ClosingConnection) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        has_task_store = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'branch_tasks_v2'"
        ).fetchone()
        if has_task_store is None:
            return []
        rows = conn.execute(
            "SELECT * FROM author_runtime_instances "
            "WHERE status = 'provisioned' ORDER BY instance_id"
        ).fetchall()
        now = datetime.now(timezone.utc)
        planned: list[StaleCloudWorkerRuntimeRetirementPlan] = []
        for row in rows:
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(metadata, dict):
                continue
            worker_id = str(metadata.get("worker_id") or "").strip()
            if (
                metadata.get("runtime_registration") != "cloud_worker"
                or not worker_id
                or float(row["updated_at"]) > cutoff.timestamp()
                or not _runtime_heartbeat_is_stale(
                    base_path,
                    row,
                    cutoff=cutoff,
                    now=now,
                )
            ):
                continue
            active_ownership = conn.execute(
                """
                SELECT 1 FROM branch_tasks_v2
                WHERE claimed_by = ?
                  AND status IN ('running', 'cancel_requested')
                LIMIT 1
                """,
                (worker_id,),
            ).fetchone()
            if active_ownership is not None:
                continue
            planned.append(StaleCloudWorkerRuntimeRetirementPlan(
                instance_id=str(row["instance_id"]),
                universe_id=str(row["universe_id"]),
                worker_id=worker_id,
                updated_at=float(row["updated_at"]),
                row_digest=daemon_server._runtime_reconcile_digest(row),
            ))
    return planned


def _daemon_id_from_author_id(author_id: str) -> str:
    if author_id.startswith("author::"):
        return "daemon::" + author_id[len("author::"):]
    return author_id


def _author_id_from_daemon_id(daemon_id: str) -> str:
    if daemon_id.startswith("daemon::"):
        return "author::" + daemon_id[len("daemon::"):]
    return daemon_id


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("metadata")
    return dict(meta) if isinstance(meta, dict) else {}


def _soul_mode(row: dict[str, Any]) -> str:
    meta = _metadata(row)
    raw = str(meta.get("daemon_soul_mode") or "").strip()
    if raw in VALID_SOUL_MODES:
        return raw
    if meta.get("auto_created"):
        return "soulless"
    soul_text = str(row.get("soul_text") or "")
    return "soulless" if soul_text == SOULLESS_SOUL_TEXT else "soul"


def _domain_claims(row: dict[str, Any]) -> list[str]:
    raw = _metadata(row).get("domain_claims", [])
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _daemon_model_binding(daemon: dict[str, Any]) -> list[str]:
    metadata = daemon.get("metadata")
    if not isinstance(metadata, dict):
        return []
    bound: list[str] = []
    for key in ("current_llm", "fixed_llm", "pinned_llm", "active_llm"):
        value = str(metadata.get(key) or "").strip()
        if value:
            bound.append(value)
    allowed = metadata.get("allowed_llms")
    if isinstance(allowed, list):
        bound.extend(str(item).strip() for item in allowed if str(item).strip())
    seen: set[str] = set()
    return [item for item in bound if not (item in seen or seen.add(item))]


def _daemon_from_author(row: dict[str, Any], *, include_soul: bool = False) -> dict[str, Any]:
    mode = _soul_mode(row)
    metadata = _metadata(row)
    out = {
        "daemon_id": _daemon_id_from_author_id(str(row["author_id"])),
        "legacy_author_id": str(row["author_id"]),
        "display_name": str(row["display_name"]),
        "soul_hash": str(row["soul_hash"]),
        "soul_mode": mode,
        "has_soul": mode == "soul",
        "domain_claims": _domain_claims(row),
        "owner_user_id": str(metadata.get("owner_user_id") or metadata.get("created_by") or "host"),
        "tenant_id": str(metadata.get("tenant_id") or metadata.get("owner_user_id") or "host"),
        "lineage_parent_id": (
            _daemon_id_from_author_id(str(row["lineage_parent_id"]))
            if row.get("lineage_parent_id")
            else None
        ),
        "reputation_score": float(row.get("reputation_score") or 0.0),
        "created_at": row.get("created_at"),
        "metadata": metadata,
    }
    if include_soul:
        out["soul_text"] = str(row.get("soul_text") or "")
    return out


def _runtime_from_author_runtime(
    row: dict[str, Any],
    *,
    daemon: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    daemon_id = (
        daemon["daemon_id"]
        if daemon is not None
        else _daemon_id_from_author_id(str(row["author_id"]))
    )
    return {
        "runtime_instance_id": str(row["instance_id"]),
        "daemon_id": daemon_id,
        "legacy_author_id": str(row["author_id"]),
        "universe_id": str(row["universe_id"]),
        "provider_name": str(row["provider_name"]),
        "model_name": str(row["model_name"]),
        "branch_id": row.get("branch_id"),
        "status": str(row["status"]),
        "created_by": str(row["created_by"]),
        "owner_user_id": str(metadata.get("owner_user_id") or row["created_by"]),
        "tenant_id": str(
            metadata.get("tenant_id")
            or metadata.get("owner_user_id")
            or row["created_by"],
        ),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "metadata": metadata,
    }


def create_daemon(
    base_path: str | Path,
    *,
    display_name: str,
    created_by: str,
    soul_mode: str | None = None,
    soul_text: str = "",
    domain_claims: list[str] | None = None,
    lineage_parent_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or return a named daemon identity.

    ``soul_mode="soulless"`` creates the default platform-dispatcher daemon.
    ``soul_mode="soul"`` requires non-empty ``soul_text`` and records optional
    domain claims for future node/gate eligibility checks.
    """
    name = display_name.strip()
    if not name:
        raise ValueError("display_name is required")
    daemon_server.initialize_author_server(base_path)

    mode = (soul_mode or ("soul" if soul_text.strip() else "soulless")).strip()
    if mode not in VALID_SOUL_MODES:
        raise ValueError(f"soul_mode must be one of {sorted(VALID_SOUL_MODES)}")
    if mode == "soul" and not soul_text.strip():
        raise ValueError("soul_text is required when soul_mode='soul'")
    clean_soul_text = soul_text.strip()
    parent_author_id = (
        _author_id_from_daemon_id(lineage_parent_id.strip())
        if lineage_parent_id and lineage_parent_id.strip()
        else None
    )

    clean_claims = [
        str(item).strip()
        for item in (domain_claims or [])
        if str(item).strip()
    ]
    if mode == "soul":
        soul_hash = hashlib.sha256(clean_soul_text.encode("utf-8")).hexdigest()
        for existing in daemon_server.list_authors(base_path):
            if str(existing.get("soul_hash") or "") != soul_hash:
                continue
            if str(existing.get("display_name") or "").lower() == name.lower():
                continue
            if parent_author_id and str(existing.get("author_id") or "") == parent_author_id:
                continue
            raise ValueError(
                "duplicate soul_hash requires lineage_parent_id; "
                "a copied soul must be recorded as a fork or renamed descendant",
            )
    merged_metadata = dict(metadata or {})
    merged_metadata.update({
        "daemon_registry": True,
        "daemon_soul_mode": mode,
        "domain_claims": clean_claims,
        # SERVER-DERIVED, never caller-supplied. These were `setdefault`, which
        # let a caller's metadata WIN over the authenticated `created_by` — and
        # `daemon_create` accepts caller metadata straight from the public
        # surface. An attacker could therefore mint a daemon carrying
        # owner_user_id="victim" and satisfy any owner-scoped check built on it,
        # including `_is_project_loop_daemon`, whose flag the served consumer uses to
        # select a daemon and register runtime authority.
        #
        # Found by cross-family review 2026-08-05 and reproduced: ownership must
        # come from the authenticated actor, not from the request body. The
        # fields below now follow the same force-override rule the rest of this
        # block already used for server-owned values.
        "owner_user_id": created_by,
        "tenant_id": created_by,
        "created_by": created_by,
    })
    if mode == "soul":
        wiki_metadata = (
            dict(merged_metadata.get("daemon_wiki"))
            if isinstance(merged_metadata.get("daemon_wiki"), dict)
            else {}
        )
        wiki_metadata["host_local"] = True
        wiki_metadata["schema_version"] = 1
        merged_metadata["daemon_wiki"] = wiki_metadata

    author = daemon_server.register_author(
        base_path,
        display_name=name,
        soul_text=clean_soul_text if mode == "soul" else SOULLESS_SOUL_TEXT,
        created_by=created_by,
        lineage_parent_id=parent_author_id,
        metadata=merged_metadata,
    )
    daemon = _daemon_from_author(author, include_soul=False)
    if mode == "soul":
        from tinyassets.daemon_wiki import scaffold_daemon_wiki

        scaffold_daemon_wiki(base_path, daemon=daemon, soul_text=soul_text)
    return daemon


def list_daemons(base_path: str | Path) -> list[dict[str, Any]]:
    daemon_server.initialize_author_server(base_path)
    return [
        _daemon_from_author(row, include_soul=False)
        for row in daemon_server.list_authors(base_path)
    ]


def _is_project_loop_daemon(
    daemon: dict[str, Any],
    *,
    universe_id: str = "",
    owner_user_id: str = "",
) -> bool:
    metadata = daemon.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if not daemon.get("has_soul"):
        return False
    if universe_id and str(metadata.get("universe_id") or "") != universe_id:
        return False
    if owner_user_id and str(daemon.get("owner_user_id") or "") != owner_user_id:
        return False
    return bool(
        metadata.get(PROJECT_LOOP_FLAG)
        or (
            metadata.get("project_default")
            and metadata.get("loop_primary")
        )
    )


def select_project_loop_daemon(
    base_path: str | Path,
    *,
    include_soul: bool = False,
    universe_id: str = "",
    owner_user_id: str = "",
) -> dict[str, Any] | None:
    """Return the latest soul-bearing daemon marked as the project loop default.

    The autonomous loop still has a deterministic soulless fallback. This
    selector only opts into a soul when the host explicitly marked that daemon
    as the project loop default.
    """
    daemon_server.initialize_author_server(base_path)
    for daemon in reversed(list_daemons(base_path)):
        if _is_project_loop_daemon(
            daemon,
            universe_id=universe_id.strip(),
            owner_user_id=owner_user_id.strip(),
        ):
            if include_soul:
                return get_daemon(
                    base_path,
                    daemon_id=daemon["daemon_id"],
                    include_soul=True,
                )
            return daemon
    return None


def get_daemon(
    base_path: str | Path,
    *,
    daemon_id: str,
    include_soul: bool = False,
) -> dict[str, Any]:
    daemon_server.initialize_author_server(base_path)
    author_id = _author_id_from_daemon_id(daemon_id)
    row = daemon_server.get_author(base_path, author_id=author_id)
    return _daemon_from_author(row, include_soul=include_soul)


def summon_daemon(
    base_path: str | Path,
    *,
    daemon_id: str,
    universe_id: str,
    provider_name: str,
    model_name: str,
    created_by: str,
    branch_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    daemon_server.initialize_author_server(base_path)
    daemon = get_daemon(base_path, daemon_id=daemon_id)
    allowed_models = _daemon_model_binding(daemon)
    if allowed_models and model_name not in allowed_models:
        raise ValueError(
            "daemon model identity mismatch: this daemon is bound to "
            f"{', '.join(allowed_models)}; use a borrowed-role executor, "
            "renamed fork, or update the active soul version before changing models",
        )
    merged_metadata = dict(metadata or {})
    merged_metadata.update({
        # Same rule as create_daemon: ownership is derived, never caller-supplied.
        # These were `setdefault`, and `daemon_summon` forwards caller metadata
        # from the public surface, so an attacker could mint a RUNTIME carrying
        # owner_user_id/tenant_id naming a victim. Ownership belongs to the
        # daemon row, which is server state (cross-family review 2026-08-05).
        "owner_user_id": daemon["owner_user_id"],
        "tenant_id": daemon["tenant_id"],
        "daemon_id": daemon["daemon_id"],
        "daemon_soul_hash": daemon["soul_hash"],
        "daemon_soul_mode": daemon["soul_mode"],
        "domain_claims": daemon["domain_claims"],
    })
    runtime = daemon_server.spawn_runtime_instance(
        base_path,
        universe_id=universe_id,
        author_id=daemon["legacy_author_id"],
        provider_name=provider_name,
        model_name=model_name,
        branch_id=branch_id,
        created_by=created_by,
        metadata=merged_metadata,
    )
    return _runtime_from_author_runtime(runtime, daemon=daemon)


def ensure_daemon_runtime(
    base_path: str | Path,
    *,
    daemon_id: str,
    universe_id: str,
    provider_name: str,
    model_name: str,
    created_by: str,
    worker_id: str,
    branch_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or refresh the runtime slot for a stable worker process."""
    # Platform admission, before any read and before any row is written. Every
    # row this function writes is stamped `runtime_registration: "cloud_worker"`
    # (below), so an unadmitted process must not reach the write at all. The
    # verdict is the process-owned observation — not a hostname, not an env var,
    # not `boot_id` (incarnation/liveness only, design.md § Enforcement sites
    # (B)) — and it is re-resolved here rather than inherited from any existing
    # row: a registration row is a record, never permission.
    provenance = resolve_process_cloud_admission()
    if not provenance.is_cloud:
        # Sanitized tokens only: verdict + reason, no instance id, no expected
        # id, no address, no hostname.
        raise PermissionError(
            f"{PLATFORM_NOT_CLOUD_REASON}: cloud-worker runtime registration "
            f"requires an admitted cloud runtime "
            f"(verdict={provenance.verdict}, reason={provenance.reason})"
        )
    clean_worker_id = worker_id.strip()
    if not clean_worker_id:
        raise ValueError("worker_id is required")
    daemon = get_daemon(base_path, daemon_id=daemon_id)
    allowed_models = _daemon_model_binding(daemon)
    if allowed_models and model_name not in allowed_models:
        raise ValueError(
            "daemon model identity mismatch: this daemon is bound to "
            f"{', '.join(allowed_models)}; use a borrowed-role executor, "
            "renamed fork, or update the active soul version before changing models",
        )
    merged_metadata = dict(metadata or {})
    merged_metadata.setdefault("owner_user_id", daemon["owner_user_id"])
    merged_metadata.setdefault("tenant_id", daemon["tenant_id"])
    merged_metadata.update({
        "worker_id": clean_worker_id,
        "runtime_registration": "cloud_worker",
        "daemon_id": daemon["daemon_id"],
        "daemon_soul_hash": daemon["soul_hash"],
        "daemon_soul_mode": daemon["soul_mode"],
        "domain_claims": daemon["domain_claims"],
    })
    row = daemon_server.ensure_worker_runtime_instance(
        base_path,
        universe_id=universe_id,
        author_id=daemon["legacy_author_id"],
        provider_name=provider_name,
        model_name=model_name,
        branch_id=branch_id,
        created_by=created_by,
        worker_id=clean_worker_id,
        metadata=merged_metadata,
    )
    return _runtime_from_author_runtime(row, daemon=daemon)


def set_worker_queue_descriptor(
    base_path: str | Path,
    *,
    runtime_instance_id: str,
    descriptor: Mapping[str, Any] | None,
    expected_worker_id: str = "",
) -> dict[str, Any]:
    """Persist vetted worker protocol evidence on its exact runtime slot."""
    # Platform admission on PUBLICATION and REFRESH only, before any read and
    # before the write. Minting the cloud-stamped runtime row is already gated
    # in `ensure_daemon_runtime`, but the liveness half of the same descriptor
    # (`expires_at`, `boot_id`, `build_sha`) is written here -- so without this
    # gate an unadmitted process holding the data dir could renew an existing
    # cloud descriptor indefinitely and keep the claim lane's validity window
    # open. An existing cloud-stamped slot is a record, never permission.
    #
    # Clearing (`descriptor is None`) is deliberately NOT gated: it is
    # revocation. It only removes a claim, so it cannot widen authority, and
    # refusing it would strand a live-looking descriptor for the rest of its
    # validity window on exactly the process that has lost admission -- the
    # opposite of the invariant, and it would break shutdown cleanup.
    #
    # Resolve here rather than peek: this function holds no transaction, so the
    # bounded metadata read cannot stall a write lock. Placed before the
    # unchanged-descriptor fast return below, because that return is itself an
    # authority-bearing answer about a runtime slot.
    if descriptor is not None:
        provenance = resolve_process_cloud_admission()
        if not provenance.is_cloud:
            raise PermissionError(
                f"{PLATFORM_NOT_CLOUD_REASON}: worker queue descriptor "
                f"publication requires an admitted cloud runtime "
                f"(verdict={provenance.verdict}, reason={provenance.reason})"
            )
    raw = daemon_server.get_runtime_instance(
        base_path,
        instance_id=runtime_instance_id,
    )
    runtime = _runtime_from_author_runtime(raw)
    runtime_worker = str(
        runtime.get("metadata", {}).get("worker_id") or ""
    ).strip()
    if expected_worker_id and runtime_worker != expected_worker_id:
        raise ValueError("queue_worker_id_mismatch")
    normalized: dict[str, Any] | None = None
    if descriptor is not None:
        capabilities = sorted({
            str(value).strip()
            for value in descriptor.get("capabilities", [])
            if str(value).strip()
        })
        normalized = {
            "queue_protocol_version": int(
                descriptor.get("queue_protocol_version", 0)
            ),
            "capabilities": capabilities,
            "worker_id": str(descriptor.get("worker_id") or "").strip(),
            "runtime_instance_id": str(
                descriptor.get("runtime_instance_id") or ""
            ).strip(),
            "boot_id": str(descriptor.get("boot_id") or "").strip(),
            "build_sha": str(descriptor.get("build_sha") or "").strip(),
            "config_hash": str(
                descriptor.get("config_hash") or ""
            ).strip(),
            "universe_id": str(
                descriptor.get("universe_id") or ""
            ).strip(),
            "expires_at": str(
                descriptor.get("expires_at") or ""
            ).strip(),
        }
        if normalized["queue_protocol_version"] != QUEUE_PROTOCOL_VERSION:
            raise ValueError("queue_protocol_version_mismatch")
        if OPERATOR_CAPABILITY not in capabilities:
            raise ValueError("queue_capability_missing")
        if normalized["worker_id"] != runtime_worker:
            raise ValueError("queue_worker_id_mismatch")
        if normalized["runtime_instance_id"] != runtime_instance_id:
            raise ValueError("queue_runtime_instance_id_mismatch")
        if normalized["universe_id"] != runtime["universe_id"]:
            raise ValueError("queue_universe_id_mismatch")
        if not normalized["boot_id"]:
            raise ValueError("queue_boot_id_missing")
        if re.fullmatch(r"[0-9a-f]{40}", normalized["build_sha"]) is None:
            raise ValueError("queue_build_sha_invalid")
        if (
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                normalized["config_hash"],
            )
            is None
        ):
            raise ValueError("queue_config_hash_invalid")
        try:
            expires = datetime.fromisoformat(
                normalized["expires_at"].replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError("queue_expires_at_invalid") from exc
        if expires.tzinfo is None:
            raise ValueError("queue_expires_at_invalid")
        if runtime["status"] == "retired":
            raise ValueError("queue_runtime_retired")

    if runtime.get("metadata", {}).get("queue_protocol_descriptor") == normalized:
        return runtime

    updated = daemon_server.update_runtime_instance_metadata(
        base_path,
        instance_id=runtime_instance_id,
        metadata_patch={"queue_protocol_descriptor": normalized},
        forbidden_statuses=("retired",) if normalized is not None else (),
    )
    return _runtime_from_author_runtime(updated)


def _authority_scope(
    *,
    daemon: dict[str, Any],
    runtime: dict[str, Any] | None,
    actor_id: str,
) -> str:
    actor_id = named_principal(actor_id)
    if not actor_id:
        return "none"
    delegated = daemon.get("metadata", {}).get("delegated_hosts", [])
    runtime_delegated = []
    if runtime is not None:
        runtime_delegated = runtime.get("metadata", {}).get("delegated_hosts", [])
    if actor_id in {daemon.get("owner_user_id"), runtime.get("created_by") if runtime else None}:
        return "owner"
    if actor_id in delegated or actor_id in runtime_delegated:
        return "delegated_host"
    if (
        actor_id == "host"
        and runtime is not None
        and (
            runtime.get("created_by") == "host"
            or not has_named_principal(runtime.get("created_by"))
        )
    ):
        return "local_host"
    return "none"


def build_requester_directed_daemon_assignment(
    base_path: str | Path,
    *,
    daemon_id: str,
    requester_id: str,
    patch_request_id: str,
    instruction: str = "",
) -> dict[str, Any]:
    """Validate requester authority and return proposal-only daemon routing metadata."""
    try:
        daemon = get_daemon(base_path, daemon_id=daemon_id)
    except KeyError:
        return {
            "daemon_id": daemon_id,
            "requester_id": requester_id,
            "patch_request_id": patch_request_id,
            "authority_scope": "none",
            "effect": "refused",
            "scope": "proposal_only",
            "affects_acceptance": False,
            "affects_release": False,
            "affects_merge": False,
            "note": "Daemon not found.",
        }
    scope = _authority_scope(daemon=daemon, runtime=None, actor_id=requester_id)
    if scope == "none":
        return {
            "daemon_id": daemon_id,
            "requester_id": requester_id,
            "patch_request_id": patch_request_id,
            "authority_scope": scope,
            "effect": "refused",
            "scope": "proposal_only",
            "affects_acceptance": False,
            "affects_release": False,
            "affects_merge": False,
            "note": "Requester is not authorized to direct this daemon.",
        }
    return {
        "daemon_id": daemon["daemon_id"],
        "daemon_soul_hash": daemon["soul_hash"],
        "requester_id": requester_id,
        "patch_request_id": patch_request_id,
        "authority_scope": scope,
        "effect": "applied",
        "scope": "proposal_only",
        "instruction": instruction.strip(),
        "affects_acceptance": False,
        "affects_release": False,
        "affects_merge": False,
    }


def _control_result(
    *,
    daemon_id: str,
    runtime_instance_id: str | None,
    authority_scope: str,
    effect: str,
    action: str,
    runtime: dict[str, Any] | None = None,
    daemon: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    return {
        "action_id": f"daemon-control::{uuid.uuid4().hex}",
        "daemon_id": daemon_id,
        "runtime_instance_id": runtime_instance_id,
        "authority_scope": authority_scope,
        "effect": effect,
        "control_state": effect,
        "action": action,
        "runtime": runtime,
        "daemon": daemon,
        "note": note,
    }


def control_runtime_instance(
    base_path: str | Path,
    *,
    runtime_instance_id: str,
    actor_id: str,
    action: str,
) -> dict[str, Any]:
    """Apply an ownership-scoped runtime control command."""
    normalized = action.strip().lower()
    if normalized not in {*RUNTIME_CONTROL_STATUSES, "banish"}:
        raise ValueError("action must be pause, resume, restart, or banish")
    daemon_server.initialize_author_server(base_path)
    row = daemon_server.get_runtime_instance(
        base_path,
        instance_id=runtime_instance_id,
    )
    daemon = get_daemon(
        base_path,
        daemon_id=_daemon_id_from_author_id(str(row["author_id"])),
    )
    runtime = _runtime_from_author_runtime(row, daemon=daemon)
    scope = _authority_scope(daemon=daemon, runtime=runtime, actor_id=actor_id)
    if scope == "none":
        return _control_result(
            daemon_id=daemon["daemon_id"],
            runtime_instance_id=runtime_instance_id,
            authority_scope=scope,
            effect="refused",
            action=normalized,
            runtime=runtime,
            daemon=daemon,
            note="Actor is not authorized to control this daemon runtime.",
        )

    metadata_patch = {
        "last_control_action": normalized,
        "last_control_actor": actor_id,
    }
    if normalized == "banish":
        updated = banish_daemon(base_path, runtime_instance_id=runtime_instance_id)
        return _control_result(
            daemon_id=daemon["daemon_id"],
            runtime_instance_id=runtime_instance_id,
            authority_scope=scope,
            effect="applied",
            action=normalized,
            runtime=updated,
            daemon=daemon,
        )

    updated_row = daemon_server.update_runtime_instance_status(
        base_path,
        instance_id=runtime_instance_id,
        status=RUNTIME_CONTROL_STATUSES[normalized],
        metadata_patch=metadata_patch,
    )
    updated = _runtime_from_author_runtime(updated_row, daemon=daemon)
    effect = "queued" if normalized == "restart" else "applied"
    return _control_result(
        daemon_id=daemon["daemon_id"],
        runtime_instance_id=runtime_instance_id,
        authority_scope=scope,
        effect=effect,
        action=normalized,
        runtime=updated,
        daemon=daemon,
    )


def update_daemon_behavior(
    base_path: str | Path,
    *,
    daemon_id: str,
    actor_id: str,
    behavior_update: dict[str, Any],
    apply_now: bool = False,
) -> dict[str, Any]:
    """Record an ownership-scoped daemon behavior proposal or update."""
    daemon = get_daemon(base_path, daemon_id=daemon_id)
    scope = _authority_scope(daemon=daemon, runtime=None, actor_id=actor_id)
    if scope == "none":
        return _control_result(
            daemon_id=daemon_id,
            runtime_instance_id=None,
            authority_scope=scope,
            effect="refused",
            action="update_behavior",
            daemon=daemon,
            note="Actor is not authorized to update this daemon.",
        )

    metadata = dict(daemon.get("metadata") or {})
    version = int(metadata.get("behavior_version") or 0) + 1
    proposal = {
        "proposal_id": f"daemon-behavior::{uuid.uuid4().hex}",
        "version": version,
        "proposed_by": actor_id,
        "status": "applied" if apply_now else "proposed",
        "behavior_update": dict(behavior_update),
    }
    proposals = list(metadata.get("behavior_updates", []))
    proposals.append(proposal)
    patch: dict[str, Any] = {
        "behavior_version": version,
        "behavior_updates": proposals[-25:],
    }
    if apply_now:
        patch["behavior_policy"] = dict(behavior_update)
    updated_row = daemon_server.update_author_metadata(
        base_path,
        author_id=_author_id_from_daemon_id(daemon_id),
        metadata_patch=patch,
    )
    updated = _daemon_from_author(updated_row, include_soul=False)
    return _control_result(
        daemon_id=daemon_id,
        runtime_instance_id=None,
        authority_scope=scope,
        effect="applied" if apply_now else "queued",
        action="update_behavior",
        daemon=updated,
        note="Behavior update applied." if apply_now else "Behavior update recorded as a proposal.",
    )


def daemon_control_status(
    base_path: str | Path,
    *,
    actor_id: str,
    daemon_id: str | None = None,
    runtime_instance_id: str | None = None,
    universe_id: str | None = None,
) -> dict[str, Any]:
    """Return ownership-scoped daemon control status for chat/web surfaces."""
    daemons = list_daemons(base_path)
    runtimes = list_runtime_instances(base_path, universe_id=universe_id)
    if daemon_id:
        daemons = [d for d in daemons if d["daemon_id"] == daemon_id]
        runtimes = [r for r in runtimes if r["daemon_id"] == daemon_id]
    if runtime_instance_id:
        runtimes = [r for r in runtimes if r["runtime_instance_id"] == runtime_instance_id]
        daemon_ids = {r["daemon_id"] for r in runtimes}
        daemons = [d for d in daemons if d["daemon_id"] in daemon_ids]

    daemon_by_id = {d["daemon_id"]: d for d in daemons}
    authorized_daemons = [
        d for d in daemons
        if _authority_scope(daemon=d, runtime=None, actor_id=actor_id) != "none"
    ]
    authorized_ids = {d["daemon_id"] for d in authorized_daemons}
    authorized_runtimes = []
    for runtime in runtimes:
        daemon = daemon_by_id.get(runtime["daemon_id"])
        if daemon is None:
            try:
                daemon = get_daemon(base_path, daemon_id=runtime["daemon_id"])
            except KeyError:
                continue
        if _authority_scope(daemon=daemon, runtime=runtime, actor_id=actor_id) != "none":
            authorized_runtimes.append(runtime)
            authorized_ids.add(runtime["daemon_id"])

    return {
        "action_id": f"daemon-control::{uuid.uuid4().hex}",
        "authority_scope": "owner" if authorized_ids else "none",
        "effect": "applied",
        "control_state": "applied",
        "daemons": [d for d in daemons if d["daemon_id"] in authorized_ids],
        "runtimes": authorized_runtimes,
        "daemon_count": len(authorized_ids),
        "runtime_count": len(authorized_runtimes),
    }


def banish_daemon(
    base_path: str | Path,
    *,
    runtime_instance_id: str,
) -> dict[str, Any]:
    daemon_server.initialize_author_server(base_path)
    runtime = daemon_server.retire_runtime_instance(
        base_path,
        instance_id=runtime_instance_id,
    )
    return _runtime_from_author_runtime(runtime)


def list_runtime_instances(
    base_path: str | Path,
    *,
    universe_id: str | None = None,
) -> list[dict[str, Any]]:
    daemon_server.initialize_author_server(base_path)
    return [
        _runtime_from_author_runtime(row)
        for row in daemon_server.list_runtime_instances(
            base_path, universe_id=universe_id,
        )
    ]


def runtime_matches_worker_provider(
    base_path: str | Path,
    *,
    universe_id: str,
    runtime_instance_id: str,
    daemon_id: str,
    worker_id: str,
    provider_name: str,
) -> bool:
    """Return whether one live runtime is the exact provider-bound worker."""
    # Existing-row eligibility, not permission (design.md § Enforcement sites
    # (B): "an existing registration row is not permission, so authority is
    # re-resolved on read rather than inherited from the row"). A row written
    # by an admitted process earlier — or copied, or restored from a volume —
    # must grant nothing to a process that is not itself admitted now.
    #
    # Cached-only on purpose. Callers pass this predicate's result straight into
    # authority decisions and at least one of them (`_ExactAudienceResolver` in
    # `cloud_automation_runtime.py`) is handed to an activation service that
    # calls it from inside its own transaction, so a resolve here could put a
    # bounded socket read under a write lock. Entry points that must succeed on
    # cloud resolve first, outside any transaction.
    #
    # `False` is the existing "not the exact worker" answer every caller already
    # handles (return None / PermissionError / recorded refusal reason), so an
    # unadmitted process is refused through the path callers already understand
    # rather than a new exception type they would not catch.
    if not cached_process_is_cloud_admitted():
        return False
    runtime = next(
        (
            value
            for value in list_runtime_instances(base_path, universe_id=universe_id)
            if value["runtime_instance_id"] == runtime_instance_id
        ),
        None,
    )
    if runtime is None:
        return False
    return all(
        (
            runtime["daemon_id"] == daemon_id,
            runtime["provider_name"] == provider_name,
            runtime["status"] == "provisioned",
            str(runtime.get("metadata", {}).get("worker_id") or "") == worker_id,
        )
    )


def provider_capacity_warning(
    provider_name: str,
    *,
    running_count: int,
) -> dict[str, Any] | None:
    """Return advisory same-provider capacity guidance.

    This is deliberately warning-only. TinyAssets does not cap host fleet size.
    """
    if running_count <= 0:
        return None
    next_count = running_count + 1
    return {
        "provider_name": provider_name,
        "current_count": running_count,
        "next_count": next_count,
        "severity": "warning",
        "can_override": True,
        "message": (
            f"Launching daemon #{next_count} on {provider_name} may require "
            "additional subscription or rate-limit headroom. TinyAssets will not "
            "block this; confirm your provider plan can support it."
        ),
    }
