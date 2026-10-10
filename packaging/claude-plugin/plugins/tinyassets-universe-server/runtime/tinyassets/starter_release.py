"""One release manifest and authenticated consumer for the D10 transaction API."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from tinyassets.starter_manifest import SeedFile, SeedManifest, digest
from tinyassets.starter_seeds import seed_boundary, seed_snapshot, seed_store
from tinyassets.starter_skills import starter_agent_files

# Exact published DEFAULT_OPERATING_INSTRUCTIONS bytes at the cutover base,
# 274adfc1ba (with and without its writer's trailing newline). Never fuzzy-match.
LEGACY_AGENTS_HASHES = (
    "2f7823d898508dcf90eecfbcbe0e5a7cbec7fd53cc83e49486b13f021cd53563",
    "8caef7f1324de9e14612e2af681a832970519dcb277d8d9eb5d634e3b69ea000",
)


def starter_manifest() -> SeedManifest:
    return SeedManifest("starter-agent-v1", "2-wake-when", tuple(
        SeedFile(path, text.encode("utf-8"),
                 LEGACY_AGENTS_HASHES if path == "AGENTS.md" else (),
                 historically_seeded=path == "AGENTS.md")
        for path, text in starter_agent_files().items()
    ))


def prepare_starter(root: Path, *, owner_id: str, center_id: str, fresh=False) -> dict:
    """Called after ingress/provisioning authenticates the bound owner and root."""
    from tinyassets.universe_owner import owner_of

    if root.name != center_id or owner_of(root.parent, center_id) != owner_id:
        raise PermissionError("starter release requires the canonical owner/center binding")
    from tinyassets.owner_control import ControlUnavailable

    manifest = starter_manifest()
    if not fresh:
        # Every native agent jail holds the seed boundary SHARED for its whole
        # life, so the exclusive store below waits on any running agent. An
        # installed release needs no write: read it holding the boundary shared
        # too, which a live jail allows and a migration in flight (an owner's
        # Undo between file changes) excludes. Live 2026-10-09: an agent that
        # woke a workflow agent held it, and the woken turn failed after the
        # store's 5s exclusive wait, every time.
        try:
            with (seed_boundary(root),
                  seed_snapshot(root, owner_id=owner_id, center_id=center_id) as seeds):
                current = seeds.installed(manifest) if seeds is not None else None
        except sqlite3.Error:
            current = None  # unreadable read-only (e.g. no -shm yet): the store decides
        if current is not None:
            return current
    with seed_store(root, owner_id=owner_id, center_id=center_id) as seeds:
        receipt = seeds.install(manifest, fresh=fresh)
        try:
            deliver_notices(root, seeds)
        except ControlUnavailable:
            # Owner controls are held elsewhere right now (an owner action, or the
            # run that launched this turn). Nothing was queued; the notice stays
            # undelivered in the durable outbox and the next prepare replays it.
            # The files are installed, so the turn must not fail for this.
            pass
        return receipt


def prepare_center_starter(root: Path) -> dict | None:
    """Resolve the center's owner, independently of the current interlocutor."""
    from tinyassets.universe_owner import owner_of

    owner = owner_of(root.parent, root.name)
    if owner is None:
        return None  # Unprovisioned preview/inspection; no owner state to migrate.
    return prepare_starter(root, owner_id=owner, center_id=root.name)


def deliver_notices(root: Path, seeds) -> None:
    """Replay the durable outbox into the existing owner inbox by stable key."""
    from tinyassets.storage.pending_requests import create_request

    for notice in seeds.notices():
        if notice["delivered"]:
            continue
        payload = notice["payload"]
        lines = [*payload["diagnostics"], *[
            f"{p['path']}: {p['outcome']}" for p in payload["paths"]
        ], "Review candidates or Undo this file update in Account → Soul and memory. "
           "Your agent uses the current runtime regardless of this choice."]
        row = create_request(
            root, kind="Notification", title=f"Starter files {notice['version']}",
            body="\n".join(lines), fields=[], action={"type": "notify"},
            dedupe_key="starter-seed:" + notice["notification_key"], agent="main",
        )
        if not row or row.get("error"):
            raise OSError("starter notice could not be delivered")
        seeds.mark_delivered(notice["notification_key"])


def owner_seed_view(root: Path, *, owner_id: str) -> list[dict]:
    """Current, hash-bound offers for the authenticated owner UI only."""
    from tinyassets.universe_owner import owner_of

    if owner_of(root.parent, root.name) != owner_id:
        raise PermissionError("starter owner binding changed")
    with seed_snapshot(root, owner_id=owner_id, center_id=root.name) as seeds:
        if seeds is None:
            return []
        notices = seeds.notices()
        for notice in notices:
            for item in notice["payload"]["paths"]:
                kind, hash_, _ = seeds._observe(item["path"])
                item["current_hash"] = hash_
                candidate = seeds.candidate(item["candidate"]) if item["candidate"] else None
                item["adoptable"] = (kind in ("regular", "absent")
                                     and candidate is not None and hash_ != digest(candidate))
                item["candidate_text"] = candidate.decode("utf-8") if candidate else ""
                paths = seeds._rows("seed_paths", "bundle_id=? AND relative_path=?",
                                    (notice["bundle_id"], item["path"]))
                item["current_outcome"] = paths[0]["outcome"] if paths else item["outcome"]
        return notices


def abandon_new_provision(root: Path, *, owner_id: str) -> None:
    """Archive a failed creation's NEW sidecar before its root is rolled back.

    Only the creation handler calls this, and only when the sidecar did not
    exist before this attempt. Keep all journal bytes for diagnosis; never
    leave that receipt active for a later creation reusing the same center ID.
    """
    import uuid

    from tinyassets.providers.provider_jail import UNIVERSE_SIDECARS_DIR
    from tinyassets.starter_seeds import seed_boundary
    from tinyassets.universe_owner import owner_of

    sidecar = root.parent / UNIVERSE_SIDECARS_DIR / root.name
    if not sidecar.exists():
        return
    if owner_of(root.parent, root.name) != owner_id:
        raise PermissionError("failed creation no longer owns its seed sidecar")
    with seed_boundary(root, exclusive=True):
        # Same daemon-owned parent, unique destination, no recursive deletion.
        sidecar.rename(sidecar.with_name(".failed-provision-" + uuid.uuid4().hex))
