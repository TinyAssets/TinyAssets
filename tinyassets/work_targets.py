"""Work target and review persistence helpers.

The daemon's universe-level scheduling should operate over durable targets
and review artifacts rather than a transient task queue. This module keeps
the v1 registry in inspectable JSON files inside the universe directory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tinyassets import daemon_server as author_server
from tinyassets.enrichment_signals import (
    load_enrichment_signals,
    write_enrichment_signals,
)
from tinyassets.ingestion.canon_io import read_canon_text, safe_canon_path
from tinyassets.notes import add_note

logger = logging.getLogger(__name__)

WORK_TARGETS_FILENAME = "work_targets.json"
HARD_PRIORITIES_FILENAME = "hard_priorities.json"
ARTIFACTS_DIRNAME = "artifacts"
REVIEWS_DIRNAME = "reviews"
EXECUTIONS_DIRNAME = "executions"
DISCARD_ARCHIVE_DIRNAME = "discarded_targets"

ROLE_NOTES = "notes"
ROLE_PUBLISHABLE = "publishable"
PUBLISH_STAGE_NONE = "none"
PUBLISH_STAGE_PROVISIONAL = "provisional"
PUBLISH_STAGE_COMMITTED = "committed"

LIFECYCLE_ACTIVE = "active"
LIFECYCLE_PAUSED = "paused"
LIFECYCLE_DORMANT = "dormant"
LIFECYCLE_COMPLETE = "complete"
LIFECYCLE_SUPERSEDED = "superseded"
LIFECYCLE_MARKED_FOR_DISCARD = "marked_for_discard"
LIFECYCLE_DISCARDED = "discarded"

HARD_PRIORITY_ACTIVE = "active"
HARD_PRIORITY_RESOLVED = "resolved"

DISCARD_REVIEW_DELAY = 20
DISCARD_RETENTION_DAYS = 30
SYNTHESIS_RETRY_LIMIT = 3
PATCH_REQUEST_PICKUP_SIGNAL_TAG = "pickup-incentive"
REQUESTER_DIRECTED_DAEMON_TAG = "requester-directed-daemon"
PATCH_REQUEST_PICKUP_SIGNAL_CAP = 5.0
EPOCH2_MATERIALIZATION_RESULT_LIMIT = 512

EXECUTION_KIND_NOTES = "notes"
# Phase C.2: BOOK/CHAPTER/SCENE moved to
# domains/fantasy_daemon/work_kinds.py. Only NOTES stays here because
# every domain has notes-class work; the fantasy-specific kinds are no
# longer engine concerns.

_SAFE_ID_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, fallback: str = "target") -> str:
    slug = _SAFE_ID_RE.sub("-", text.lower()).strip("-")
    return slug or fallback


def _now() -> float:
    return time.time()


def _bounded_pickup_signal(metadata: dict[str, Any]) -> float:
    """Return capped pickup-priority signal, never an acceptance signal."""
    weight = 0.0
    incentive = metadata.get("pickup_incentive")
    if isinstance(incentive, dict) and incentive.get("enabled"):
        try:
            weight = max(weight, float(incentive.get("pickup_signal_weight") or 0.0))
        except (TypeError, ValueError):
            pass
    directed = metadata.get("requester_directed_daemon")
    if isinstance(directed, dict) and directed.get("effect") == "applied":
        weight = max(weight, PATCH_REQUEST_PICKUP_SIGNAL_CAP)
    return min(max(0.0, weight), PATCH_REQUEST_PICKUP_SIGNAL_CAP)


def _read_json(path: Path, default: Any) -> Any:
    """Read a JSON file or return ``default``. Logs a warning on corruption.

    Silent fallback loses signal: a corrupted ``requests.json`` must not
    look identical to "no file" to callers. The warning surfaces in the
    TinyAssets Server log so host/oncall can spot drop-on-the-floor cases.
    Callers that need to hard-fail on corruption can distinguish by
    checking the file exists + has bytes themselves — keep this helper
    permissive so inspect/health reads don't crash on one bad file.
    """
    from tinyassets.universe_files import MAX_PLATFORM_FILE_BYTES, read_data_path

    # Link-free. A REFUSED read raises rather than returning ``default``:
    # these files are read-modify-written, and "absent" would be overwritten.
    raw = read_data_path(path, max_bytes=MAX_PLATFORM_FILE_BYTES)
    if raw is None:
        return default
    try:
        value = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        logger.warning(
            "Failed to read JSON at %s (%s: %s); returning default",
            path, type(exc).__name__, exc,
        )
        return default
    return value


def _write_json(path: Path, payload: Any) -> None:
    from tinyassets.universe_files import write_data_path

    write_data_path(path, json.dumps(payload, indent=2, sort_keys=False) + "\n")


def work_targets_path(universe_path: str | Path) -> Path:
    return Path(universe_path) / WORK_TARGETS_FILENAME


def hard_priorities_path(universe_path: str | Path) -> Path:
    return Path(universe_path) / HARD_PRIORITIES_FILENAME


def review_artifacts_dir(universe_path: str | Path) -> Path:
    return Path(universe_path) / ARTIFACTS_DIRNAME / REVIEWS_DIRNAME


def execution_artifacts_dir(universe_path: str | Path) -> Path:
    return Path(universe_path) / ARTIFACTS_DIRNAME / EXECUTIONS_DIRNAME


def discard_archive_dir(universe_path: str | Path) -> Path:
    return Path(universe_path) / ARTIFACTS_DIRNAME / DISCARD_ARCHIVE_DIRNAME


@dataclass
class WorkTarget:
    """Durable locus of intentional work in the universe."""

    target_id: str
    title: str
    home_target_id: str | None = None
    role: str = ROLE_NOTES
    publish_stage: str = PUBLISH_STAGE_NONE
    lifecycle: str = LIFECYCLE_ACTIVE
    current_intent: str = ""
    tags: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    note_refs: list[str] = field(default_factory=list)
    linked_target_ids: list[str] = field(default_factory=list)
    timeline_refs: list[str] = field(default_factory=list)
    lineage_refs: list[str] = field(default_factory=list)
    selection_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    # Phase C.1: which TaskProducer emitted this target in its current
    # form. Legacy rows load with "unknown"; C.4 wires producers to
    # stamp authoritative values (seed, user_request, fantasy_authorial,
    # etc.). Not immutable — last-writer wins with a log warning.
    origin: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkTarget":
        return cls(
            target_id=str(data.get("target_id", str(uuid.uuid4()))),
            title=str(data.get("title", "Untitled target")),
            home_target_id=data.get("home_target_id"),
            role=str(data.get("role", ROLE_NOTES)),
            publish_stage=str(
                data.get("publish_stage", PUBLISH_STAGE_NONE)
            ),
            lifecycle=str(data.get("lifecycle", LIFECYCLE_ACTIVE)),
            current_intent=str(data.get("current_intent", "")),
            tags=list(data.get("tags", []))
            if isinstance(data.get("tags"), list) else [],
            artifact_refs=list(data.get("artifact_refs", []))
            if isinstance(data.get("artifact_refs"), list) else [],
            note_refs=list(data.get("note_refs", []))
            if isinstance(data.get("note_refs"), list) else [],
            linked_target_ids=list(data.get("linked_target_ids", []))
            if isinstance(data.get("linked_target_ids"), list) else [],
            timeline_refs=list(data.get("timeline_refs", []))
            if isinstance(data.get("timeline_refs"), list) else [],
            lineage_refs=list(data.get("lineage_refs", []))
            if isinstance(data.get("lineage_refs"), list) else [],
            selection_reason=str(data.get("selection_reason", "")),
            metadata=dict(data.get("metadata", {}))
            if isinstance(data.get("metadata"), dict) else {},
            created_at=float(data.get("created_at", _now())),
            updated_at=float(data.get("updated_at", _now())),
            origin=str(data.get("origin", "unknown")),
        )


@dataclass
class HardPriorityItem:
    """Explicit hard-priority record used by foundation review."""

    priority_id: str
    kind: str
    target_id: str | None = None
    detail: str = ""
    source_ref: str = ""
    hard_block: bool = True
    status: str = HARD_PRIORITY_ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HardPriorityItem":
        return cls(
            priority_id=str(data.get("priority_id", str(uuid.uuid4()))),
            kind=str(data.get("kind", "unknown")),
            target_id=data.get("target_id"),
            detail=str(data.get("detail", "")),
            source_ref=str(data.get("source_ref", "")),
            hard_block=bool(data.get("hard_block", True)),
            status=str(data.get("status", HARD_PRIORITY_ACTIVE)),
            metadata=dict(data.get("metadata", {}))
            if isinstance(data.get("metadata"), dict) else {},
            created_at=float(data.get("created_at", _now())),
            updated_at=float(data.get("updated_at", _now())),
        )


def load_work_targets(universe_path: str | Path) -> list[WorkTarget]:
    try:
        raw = author_server.list_work_target_dicts(universe_path)
        return [WorkTarget.from_dict(item) for item in raw if isinstance(item, dict)]
    except Exception:
        raw = _read_json(work_targets_path(universe_path), [])
        if not isinstance(raw, list):
            return []
        return [WorkTarget.from_dict(item) for item in raw if isinstance(item, dict)]


def save_work_targets(
    universe_path: str | Path,
    targets: list[WorkTarget],
) -> None:
    payloads = [target.to_dict() for target in targets]
    try:
        author_server.replace_work_target_dicts(universe_path, payloads)
    except Exception:
        _write_json(work_targets_path(universe_path), payloads)


def load_hard_priorities(universe_path: str | Path) -> list[HardPriorityItem]:
    try:
        raw = author_server.list_hard_priority_dicts(universe_path)
        return [
            HardPriorityItem.from_dict(item)
            for item in raw
            if isinstance(item, dict)
        ]
    except Exception:
        raw = _read_json(hard_priorities_path(universe_path), [])
        if not isinstance(raw, list):
            return []
        return [
            HardPriorityItem.from_dict(item)
            for item in raw
            if isinstance(item, dict)
        ]


def save_hard_priorities(
    universe_path: str | Path,
    priorities: list[HardPriorityItem],
) -> None:
    payloads = [priority.to_dict() for priority in priorities]
    try:
        author_server.replace_hard_priority_dicts(universe_path, payloads)
    except Exception:
        _write_json(hard_priorities_path(universe_path), payloads)


def list_selectable_targets(
    universe_path: str | Path,
    *,
    role: str | None = None,
) -> list[WorkTarget]:
    targets = load_work_targets(universe_path)
    filtered: list[WorkTarget] = []
    for target in targets:
        if role is not None and target.role != role:
            continue
        if target.lifecycle != LIFECYCLE_ACTIVE:
            continue
        filtered.append(target)
    return _filter_live_epoch2_targets(universe_path, filtered)


def get_target(
    universe_path: str | Path,
    target_id: str,
) -> WorkTarget | None:
    for target in load_work_targets(universe_path):
        if target.target_id == target_id:
            return target
    return None


def upsert_work_target(
    universe_path: str | Path,
    target: WorkTarget,
) -> WorkTarget:
    target.updated_at = _now()
    try:
        author_server.upsert_work_target_dict(universe_path, target.to_dict())
        return target
    except Exception:
        targets = load_work_targets(universe_path)
        for index, existing in enumerate(targets):
            if existing.target_id == target.target_id:
                targets[index] = target
                save_work_targets(universe_path, targets)
                return target
        targets.append(target)
        save_work_targets(universe_path, targets)
    return target


def upsert_hard_priority(
    universe_path: str | Path,
    priority: HardPriorityItem,
) -> HardPriorityItem:
    priority.updated_at = _now()
    try:
        author_server.upsert_hard_priority_dict(universe_path, priority.to_dict())
        return priority
    except Exception:
        priorities = load_hard_priorities(universe_path)
        for index, existing in enumerate(priorities):
            if existing.priority_id == priority.priority_id:
                priorities[index] = priority
                save_hard_priorities(universe_path, priorities)
                return priority
        priorities.append(priority)
        save_hard_priorities(universe_path, priorities)
    return priority


def ensure_seed_targets(
    universe_path: str | Path,
    premise: str = "",
) -> list[WorkTarget]:
    targets = load_work_targets(universe_path)
    if targets:
        return targets

    created: list[WorkTarget] = []
    notes_target = WorkTarget(
        target_id="universe-notes",
        title="Command Center Notes",
        role=ROLE_NOTES,
        publish_stage=PUBLISH_STAGE_NONE,
        tags=["notes", "universe"],
        current_intent="maintain command center notes",
        metadata={"auto_created": True},
    )
    created.append(notes_target)

    if premise.strip():
        title = premise.strip().splitlines()[0][:80] or "Book 1"
        book_target = WorkTarget(
            target_id="book-1",
            title=title,
            role=ROLE_PUBLISHABLE,
            publish_stage=PUBLISH_STAGE_PROVISIONAL,
            tags=["publishable", "book", "seed"],
            current_intent="continue developing this book",
            metadata={
                "auto_created": True,
                "premise_seed": premise.strip(),
                # Phase C.2: fantasy-specific execution_kind literal
                # kept in the engine path here because
                # ensure_seed_targets is slated for replacement by
                # `SeedProducer` in Phase C.4 — the producer will carry
                # its own fantasy-domain imports. Temporary inversion.
                "execution_kind": "book",
                "book_number": 1,
            },
        )
        created.append(book_target)

    save_work_targets(universe_path, created)
    return created


REQUESTS_FILENAME = "requests.json"


def requests_path(universe_path: str | Path) -> Path:
    return Path(universe_path) / REQUESTS_FILENAME


def _list_live_epoch2_requests(
    universe_path: str | Path,
) -> list[Any]:
    """Read this worker's live v2 requests, bound to the canonical root."""
    from tinyassets import branch_tasks_v2
    from tinyassets.storage import data_dir

    if branch_tasks_v2.EPOCH2_QUEUE_CONSUMER_READY is not True:
        return []
    worker_id = os.environ.get("TINYASSETS_WORKER_ID", "").strip()
    resolved_universe_path = Path(universe_path).resolve(strict=False)
    canonical_root = data_dir().resolve(strict=False)
    universe_id = resolved_universe_path.name.strip()
    if (
        not worker_id
        or not universe_id
        or resolved_universe_path.parent != canonical_root
    ):
        return []
    try:
        records = branch_tasks_v2.Epoch2BranchTaskAdapter(
            canonical_root,
        ).list_live_claimed_requests(
            universe_id=universe_id,
            worker_id=worker_id,
            limit=EPOCH2_MATERIALIZATION_RESULT_LIMIT,
        )
        if len(records) == EPOCH2_MATERIALIZATION_RESULT_LIMIT:
            logger.warning(
                "epoch-2 live-claim read reached the %s-row result cap for %s",
                EPOCH2_MATERIALIZATION_RESULT_LIMIT,
                resolved_universe_path,
            )
        return records
    except Exception:  # noqa: BLE001
        logger.warning(
            "epoch-2 live-claim read failed for %s",
            resolved_universe_path,
            exc_info=True,
        )
        return []


def _filter_live_epoch2_targets(
    universe_path: str | Path,
    targets: list[WorkTarget],
) -> list[WorkTarget]:
    """Fail closed for v2 targets whose claim is not live for this worker."""
    if not any(target.metadata.get("queue_epoch") == 2 for target in targets):
        return targets
    live_claims = {
        (
            request.branch_task_id,
            request.claimed_by,
            request.claimed_at,
        )
        for request in _list_live_epoch2_requests(universe_path)
    }
    filtered: list[WorkTarget] = []
    for target in targets:
        if target.metadata.get("queue_epoch") != 2:
            filtered.append(target)
            continue
        claim_identity = (
            str(target.metadata.get("branch_task_id") or ""),
            str(target.metadata.get("claimed_by") or ""),
            str(target.metadata.get("claimed_at") or ""),
        )
        if claim_identity in live_claims:
            filtered.append(target)
    return filtered


def materialize_pending_requests(
    universe_path: str | Path,
) -> list[WorkTarget]:
    """Convert executable v1/v2 requests into WorkTargets.

    MCP ``submit_request`` writes user requests to ``requests.json``
    with ``status="pending"``. Before this helper landed, nothing in
    the daemon read that file — every request was silently discarded
    (STATUS.md #18 / explorer finding). This function:

    1. Reads each pending request.
    2. Creates (or upserts) a ROLE_NOTES WorkTarget tagged
       ``user-request`` with the request text as the intent so the
       authorial scheduler picks it up.
    3. Flips the request's ``status`` to ``seen`` and stamps
       ``seen_at`` so the same request doesn't materialize twice.

    Protocol-v2 requests never enter that file.  They are materialized only
    after this exact worker owns a live unexpired epoch-2 reservation, using
    the canonical transactional Request/task read model.  This helper never
    mutates the v2 aggregate.

    Returns the list of WorkTargets created/updated. Safe to call every cycle;
    v1 is idempotent on request status and v2 on task claim identity.
    """
    path = requests_path(universe_path)
    requests = _read_json(path, [])

    created: list[WorkTarget] = []
    dirty = False
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for req in requests if isinstance(requests, list) else []:
        if not isinstance(req, dict):
            continue
        if req.get("status") != "pending":
            continue
        req_id = str(req.get("id") or "").strip()
        if not req_id:
            continue
        text = str(req.get("text") or "").strip()
        req_type = str(req.get("type") or "general").strip()
        source = str(req.get("source") or "unattributed").strip()
        title_stub = text.splitlines()[0][:70] if text else req_type
        target_id = _slugify(f"request-{req_id}", fallback=f"request-{req_id}")
        metadata = {
            "request_id": req_id,
            "request_type": req_type,
            "request_source": source,
            "request_timestamp": req.get("timestamp"),
            "branch_id": req.get("branch_id"),
        }
        pickup_incentive = req.get("pickup_incentive")
        if isinstance(pickup_incentive, dict):
            metadata["pickup_incentive"] = dict(pickup_incentive)
        authority_boundary = req.get("authority_boundary")
        if isinstance(authority_boundary, dict):
            metadata["authority_boundary"] = dict(authority_boundary)
        request_classification = req.get("request_classification")
        if isinstance(request_classification, dict):
            metadata["request_classification"] = dict(request_classification)
        directed_daemon = req.get("requester_directed_daemon")
        if isinstance(directed_daemon, dict):
            metadata["requester_directed_daemon"] = dict(directed_daemon)
        tags = ["user-request", req_type]
        meaning = metadata.get("request_classification", {}).get("meaning")
        if isinstance(meaning, str) and meaning:
            tags.append(f"meaning:{meaning}")
        if _bounded_pickup_signal(metadata) > 0:
            if metadata.get("pickup_incentive"):
                tags.append(PATCH_REQUEST_PICKUP_SIGNAL_TAG)
            if metadata.get("requester_directed_daemon"):
                tags.append(REQUESTER_DIRECTED_DAEMON_TAG)
        target = WorkTarget(
            target_id=target_id,
            title=f"Request: {title_stub or req_type}",
            role=ROLE_NOTES,
            publish_stage=PUBLISH_STAGE_NONE,
            lifecycle=LIFECYCLE_ACTIVE,
            current_intent=text or f"address user {req_type} request",
            tags=tags,
            selection_reason=f"user_request:{source}",
            metadata=metadata,
        )
        upsert_work_target(universe_path, target)
        created.append(target)
        req["status"] = "seen"
        req["seen_at"] = now_iso
        req["work_target_id"] = target_id
        dirty = True

    if dirty:
        _write_json(path, requests)
    created.extend(_materialize_live_epoch2_requests(universe_path))
    return created


def _materialize_live_epoch2_requests(
    universe_path: str | Path,
) -> list[WorkTarget]:
    """Upsert this worker's live claimed v2 Request without queue mutation."""
    records = _list_live_epoch2_requests(universe_path)
    if not records:
        return []

    existing = {
        target.target_id: target
        for target in load_work_targets(universe_path)
    }
    created: list[WorkTarget] = []
    for request in records:
        target_id = _slugify(
            f"request-{request.request_id}",
            fallback=f"request-{request.request_id}",
        )
        prior = existing.get(target_id)
        claim_identity = (
            request.branch_task_id,
            request.claimed_by,
            request.claimed_at,
        )
        if prior is not None and (
            prior.metadata.get("branch_task_id"),
            prior.metadata.get("claimed_by"),
            prior.metadata.get("claimed_at"),
        ) == claim_identity:
            continue

        metadata: dict[str, Any] = {
            "request_id": request.request_id,
            "request_type": request.request_type,
            "request_source": request.actor_id,
            "request_timestamp": request.queued_at,
            "branch_id": request.branch_id,
            "queue_epoch": 2,
            "admission_id": request.admission_id,
            "branch_task_id": request.branch_task_id,
            "trigger_source": request.trigger_source,
            "accepted_priority_weight": (
                request.accepted_priority_weight
            ),
            "claimed_by": request.claimed_by,
            "claimed_at": request.claimed_at,
        }
        if request.pickup_incentive:
            metadata["pickup_incentive_text"] = request.pickup_incentive
        if request.directed_daemon_id:
            metadata["requester_directed_daemon"] = {
                "daemon_id": request.directed_daemon_id,
                "instruction": request.directed_daemon_instruction,
            }
            if request.accepted_priority_weight > 0:
                metadata["requester_directed_daemon"]["effect"] = "applied"

        tags = ["user-request", request.request_type]
        if _bounded_pickup_signal(metadata) > 0:
            tags.append(REQUESTER_DIRECTED_DAEMON_TAG)
        title_stub = (
            request.text.splitlines()[0][:70]
            if request.text
            else request.request_type
        )
        target = WorkTarget(
            target_id=target_id,
            title=f"Request: {title_stub or request.request_type}",
            role=ROLE_NOTES,
            publish_stage=PUBLISH_STAGE_NONE,
            lifecycle=LIFECYCLE_ACTIVE,
            current_intent=(
                request.text
                or f"address user {request.request_type} request"
            ),
            tags=tags,
            selection_reason=f"user_request:{request.actor_id}",
            metadata=metadata,
        )
        upsert_work_target(universe_path, target)
        existing[target_id] = target
        created.append(target)
    return created


def create_target(
    universe_path: str | Path,
    *,
    title: str,
    home_target_id: str | None = None,
    role: str = ROLE_NOTES,
    publish_stage: str = PUBLISH_STAGE_NONE,
    lifecycle: str = LIFECYCLE_ACTIVE,
    current_intent: str = "",
    tags: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    note_refs: list[str] | None = None,
    linked_target_ids: list[str] | None = None,
    timeline_refs: list[str] | None = None,
    lineage_refs: list[str] | None = None,
    selection_reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> WorkTarget:
    target_id = _slugify(title, fallback=f"target-{uuid.uuid4().hex[:8]}")
    target = WorkTarget(
        target_id=target_id,
        title=title,
        home_target_id=home_target_id,
        role=role,
        publish_stage=publish_stage,
        lifecycle=lifecycle,
        current_intent=current_intent,
        tags=list(tags or []),
        artifact_refs=list(artifact_refs or []),
        note_refs=list(note_refs or []),
        linked_target_ids=list(linked_target_ids or []),
        timeline_refs=list(timeline_refs or []),
        lineage_refs=list(lineage_refs or []),
        selection_reason=selection_reason,
        metadata=dict(metadata or {}),
    )
    return upsert_work_target(universe_path, target)


def create_provisional_target(
    universe_path: str | Path,
    *,
    title: str,
    home_target_id: str | None = None,
    current_intent: str = "",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkTarget:
    return create_target(
        universe_path,
        title=title,
        home_target_id=home_target_id,
        role=ROLE_PUBLISHABLE,
        publish_stage=PUBLISH_STAGE_PROVISIONAL,
        current_intent=current_intent,
        tags=list(tags or []) + ["provisional"],
        metadata=metadata,
    )


def reclassify_target_role(
    universe_path: str | Path,
    target_id: str,
    *,
    new_role: str,
    reason: str = "",
    create_reconciliation_note: bool = False,
) -> WorkTarget | None:
    target = get_target(universe_path, target_id)
    if target is None:
        return None

    old_role = target.role
    target.role = new_role
    if new_role == ROLE_PUBLISHABLE:
        target.publish_stage = PUBLISH_STAGE_PROVISIONAL
    else:
        target.publish_stage = PUBLISH_STAGE_NONE
    target.metadata["role_change_reason"] = reason
    target.metadata["previous_role"] = old_role
    target.metadata["role_changed_at"] = _now()
    upsert_work_target(universe_path, target)

    if (
        create_reconciliation_note
        and old_role != new_role
        and new_role == ROLE_NOTES
    ):
        add_note(
            universe_path,
            source="system",
            text=(
                f"Target '{target.title}' changed from publishable to "
                "notes and may need reconciliation."
            ),
            category="concern",
            target=target.target_id,
            tags=["role-change", "reconciliation"],
            metadata={
                "target_id": target.target_id,
                "old_role": old_role,
                "new_role": new_role,
                "reason": reason,
            },
        )
    return target


def commit_publishable_target(
    universe_path: str | Path,
    target_id: str,
) -> WorkTarget | None:
    target = get_target(universe_path, target_id)
    if target is None:
        return None
    if target.role != ROLE_PUBLISHABLE:
        return None
    target.publish_stage = PUBLISH_STAGE_COMMITTED
    return upsert_work_target(universe_path, target)


def mark_target_for_discard(
    universe_path: str | Path,
    target_id: str,
    *,
    review_cycle: int,
    reason: str = "",
) -> WorkTarget | None:
    target = get_target(universe_path, target_id)
    if target is None:
        return None
    target.lifecycle = LIFECYCLE_MARKED_FOR_DISCARD
    target.metadata["marked_for_discard_review_cycle"] = int(review_cycle)
    target.metadata["marked_for_discard_reason"] = reason
    return upsert_work_target(universe_path, target)


def discard_target(
    universe_path: str | Path,
    target_id: str,
    *,
    review_cycle: int,
) -> WorkTarget | None:
    target = get_target(universe_path, target_id)
    if target is None:
        return None

    marked_at = int(target.metadata.get("marked_for_discard_review_cycle", -1))
    if marked_at < 0 or review_cycle - marked_at < DISCARD_REVIEW_DELAY:
        return None

    target.lifecycle = LIFECYCLE_DISCARDED
    target.metadata["discarded_review_cycle"] = int(review_cycle)
    target.metadata["discard_recoverable_until"] = _now() + (
        DISCARD_RETENTION_DAYS * 24 * 60 * 60
    )
    archived_path = discard_archive_dir(universe_path) / f"{target.target_id}.json"
    _write_json(archived_path, target.to_dict())
    return upsert_work_target(universe_path, target)


def finalize_eligible_discards(
    universe_path: str | Path,
    *,
    review_cycle: int,
) -> list[WorkTarget]:
    """Finalize any marked-for-discard targets that passed the delay."""
    finalized: list[WorkTarget] = []
    for target in load_work_targets(universe_path):
        if target.lifecycle != LIFECYCLE_MARKED_FOR_DISCARD:
            continue
        discarded = discard_target(
            universe_path,
            target.target_id,
            review_cycle=review_cycle,
        )
        if discarded is not None:
            finalized.append(discarded)
    return finalized


def resolve_hard_priority(
    universe_path: str | Path,
    priority_id: str,
) -> HardPriorityItem | None:
    priorities = load_hard_priorities(universe_path)
    for priority in priorities:
        if priority.priority_id == priority_id:
            priority.status = HARD_PRIORITY_RESOLVED
            priority.updated_at = _now()
            save_hard_priorities(universe_path, priorities)
            return priority
    return None


def sync_source_synthesis_priorities(
    universe_path: str | Path,
) -> tuple[list[HardPriorityItem], list[dict[str, Any]]]:
    """Mirror synthesize_source signals into explicit hard priorities."""
    universe_dir = Path(universe_path)
    # Strict: the rehydration below can write the queue back, so an unreadable
    # or refused signal file must stop here, not be rebuilt from [].
    raw_signals = load_enrichment_signals(universe_dir, strict=True)
    raw_signals = _rehydrate_missing_synthesis_signals(
        universe_dir, raw_signals,
    )

    synth_signals = [
        signal for signal in raw_signals
        if isinstance(signal, dict)
        and signal.get("type") == "synthesize_source"
    ]
    priorities = load_hard_priorities(universe_path)
    active_by_source: dict[str, HardPriorityItem] = {}
    for priority in priorities:
        if priority.kind == "synthesize_source" and priority.status == HARD_PRIORITY_ACTIVE:
            source_file = str(priority.metadata.get("source_file", ""))
            if source_file:
                active_by_source[source_file] = priority

    current_sources = {
        str(signal.get("source_file", "")) for signal in synth_signals
        if signal.get("source_file")
    }

    for signal in synth_signals:
        source_file = str(signal.get("source_file", ""))
        if not source_file:
            continue
        target = create_target(
            universe_path,
            title=f"Synthesize {source_file}",
            role=ROLE_NOTES,
            publish_stage=PUBLISH_STAGE_NONE,
            current_intent="synthesize source upload",
            tags=["foundation", "source", "synthesize"],
            metadata={
                "source_file": source_file,
                "signal_type": "synthesize_source",
            },
        )
        existing = active_by_source.get(source_file)
        if existing is None:
            priority = HardPriorityItem(
                priority_id=f"synthesize-source::{_slugify(source_file, 'source')}",
                kind="synthesize_source",
                target_id=target.target_id,
                detail=str(signal.get("detail", source_file)),
                source_ref=source_file,
                hard_block=True,
                metadata={
                    "source_file": source_file,
                    "signal": signal,
                },
            )
            priorities.append(priority)
            active_by_source[source_file] = priority
        else:
            existing.target_id = target.target_id
            existing.detail = str(signal.get("detail", source_file))
            existing.metadata["signal"] = signal
            existing.updated_at = _now()

    for priority in priorities:
        if (
            priority.kind == "synthesize_source"
            and priority.status == HARD_PRIORITY_ACTIVE
            and str(priority.metadata.get("source_file", "")) not in current_sources
        ):
            priority.status = HARD_PRIORITY_RESOLVED
            priority.updated_at = _now()

    _settle_stale_synthesis_targets(universe_path, current_sources)
    save_hard_priorities(universe_path, priorities)
    return priorities, synth_signals


def _rehydrate_missing_synthesis_signals(
    universe_dir: Path,
    raw_signals: list[Any],
) -> list[Any]:
    """Recreate missing synthesize_source signals from the source manifest.

    The API source listing already repairs this gap for read callers. The
    daemon needs the same repair at foundation-review time so a consumed or
    truncated signal file cannot leave active synthesis targets permanently
    unexecutable.
    """
    # Route the manifest read through the canon-I/O chokepoint so a symlinked
    # ``.manifest.json`` pointing outside canon is rejected before any read.
    canon_dir = universe_dir / "canon"
    try:
        manifest = json.loads(read_canon_text(canon_dir, ".manifest.json"))
    except ValueError:
        # Containment escape: treat as no manifest rather than reading outside.
        return raw_signals
    except (OSError, json.JSONDecodeError):
        return raw_signals
    if not isinstance(manifest, dict):
        return raw_signals

    queued_sources = {
        str(signal.get("source_file", ""))
        for signal in raw_signals
        if isinstance(signal, dict)
        and signal.get("type") == "synthesize_source"
        and signal.get("source_file")
    }
    appended = False
    for name, entry in manifest.items():
        if not isinstance(entry, dict):
            continue
        source_file = str(entry.get("filename") or name)
        if not source_file or source_file in queued_sources:
            continue
        if not _manifest_entry_needs_synthesis(universe_dir, source_file, entry):
            continue
        raw_signals.append(_synthesis_signal_from_manifest(source_file, entry))
        queued_sources.add(source_file)
        appended = True

    if appended:
        write_enrichment_signals(universe_dir, raw_signals)
    return raw_signals


def _manifest_entry_needs_synthesis(
    universe_dir: Path,
    source_file: str,
    entry: dict[str, Any],
) -> bool:
    source_path = str(entry.get("source_path") or f"sources/{source_file}")
    routed_to_sources = (
        str(entry.get("routed_to", "")) == "sources"
        or source_path.replace("\\", "/").startswith("sources/")
    )
    if not routed_to_sources:
        return False
    if entry.get("synthesized_docs"):
        return False
    if entry.get("synthesis_failed"):
        return False
    try:
        attempts = int(entry.get("synthesis_attempts", 0) or 0)
    except (TypeError, ValueError):
        attempts = 0
    if attempts >= SYNTHESIS_RETRY_LIMIT:
        return False
    # ``source_path`` is a manifest-supplied (untrusted) value. Resolve + contain
    # it against the canon ROOT before the ``is_file`` stat so a crafted
    # ``../`` source_path or a symlinked source cannot probe outside canon.
    try:
        target = safe_canon_path(
            universe_dir / "canon", source_path, kind="source file"
        )
    except ValueError:
        return False
    return target.is_file()


def _synthesis_signal_from_manifest(
    source_file: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    file_type = str(entry.get("file_type", "unknown"))
    try:
        byte_count = int(entry.get("byte_count", 0) or 0)
    except (TypeError, ValueError):
        byte_count = 0
    return {
        "type": "synthesize_source",
        "topic": Path(source_file).stem.replace("-", "_").replace(" ", "_"),
        "detail": (
            f"New source file: {source_file} "
            f"({byte_count} bytes, {file_type})"
        ),
        "source_file": source_file,
        "file_type": file_type,
        "mime_type": str(entry.get("mime_type", "")),
    }


def _settle_stale_synthesis_targets(
    universe_path: str | Path,
    current_sources: set[str],
) -> None:
    """Remove active synthesis targets whose source no longer needs a signal."""
    targets = load_work_targets(universe_path)
    changed = False
    for target in targets:
        if target.lifecycle != LIFECYCLE_ACTIVE:
            continue
        source_file = str(target.metadata.get("source_file", ""))
        signal_type = str(target.metadata.get("signal_type", ""))
        if signal_type != "synthesize_source" or not source_file:
            continue
        if source_file in current_sources:
            continue
        target.lifecycle = LIFECYCLE_COMPLETE
        target.metadata["completed_reason"] = (
            "synthesis_signal_absent_or_source_resolved"
        )
        target.updated_at = _now()
        changed = True
    if changed:
        save_work_targets(universe_path, targets)


def collect_soft_conflicts(universe_path: str | Path) -> list[dict[str, Any]]:
    """Collect non-blocking conflicts from notes for foundation review."""
    notes_path = Path(universe_path) / "notes.json"
    raw_notes = _read_json(notes_path, [])
    conflicts: list[dict[str, Any]] = []
    if not isinstance(raw_notes, list):
        return conflicts

    for note in raw_notes:
        if not isinstance(note, dict):
            continue
        if note.get("status") == "dismissed":
            continue
        if note.get("category") not in {"concern", "error"}:
            continue
        metadata = note.get("metadata", {})
        target_id = None
        if isinstance(metadata, dict):
            target_id = metadata.get("target_id") or metadata.get("work_target_id")
        conflicts.append({
            "note_id": note.get("id"),
            "target_id": target_id or note.get("target"),
            "category": note.get("category"),
            "text": note.get("text", ""),
            "clearly_wrong": bool(note.get("clearly_wrong", False)),
        })
    return conflicts


def choose_authorial_targets(
    universe_path: str | Path,
    *,
    premise: str = "",
    candidate_override: list[WorkTarget] | None = None,
) -> list[WorkTarget]:
    """Score and rank authorial candidates for the next cycle.

    Phase C.4: when ``candidate_override`` is passed, skip the built-in
    seed + list_selectable_targets step and score the provided list.
    Callers wiring through the producer interface pass the merged
    producer output here. Legacy flag-off path keeps the old behavior
    by leaving ``candidate_override=None``.
    """
    if candidate_override is None:
        ensure_seed_targets(universe_path, premise=premise)
        candidates = list_selectable_targets(universe_path)
    else:
        # Producer path has already run seed + list_selectable_targets;
        # just score the merged set. Empty means the daemon should idle,
        # not resurrect completed or paused targets.
        candidates = _filter_live_epoch2_targets(
            universe_path,
            list(candidate_override),
        )

    def score(target: WorkTarget) -> tuple[int, float, int, float]:
        role_score = 2 if target.role == ROLE_PUBLISHABLE else 1
        active_score = 2 if target.lifecycle == LIFECYCLE_ACTIVE else 1
        stage_score = (
            2 if target.publish_stage == PUBLISH_STAGE_COMMITTED
            else 1 if target.publish_stage == PUBLISH_STAGE_PROVISIONAL
            else 0
        )
        return (
            active_score + role_score,
            _bounded_pickup_signal(target.metadata),
            stage_score,
            -target.updated_at,
        )

    ranked = sorted(candidates, key=score, reverse=True)
    return ranked


def write_review_artifact(
    universe_path: str | Path,
    stage: str,
    payload: dict[str, Any],
) -> str:
    artifact_id = f"{int(_now())}-{uuid.uuid4().hex[:8]}-{_slugify(stage, 'review')}"
    target = review_artifacts_dir(universe_path) / f"{artifact_id}.json"
    _write_json(target, payload)
    return str(target.relative_to(Path(universe_path))).replace("\\", "/")


def write_execution_artifact(
    universe_path: str | Path,
    execution_id: str,
    payload: dict[str, Any],
) -> str:
    target = execution_artifacts_dir(universe_path) / f"{execution_id}.json"
    _write_json(target, payload)
    return str(target.relative_to(Path(universe_path))).replace("\\", "/")
