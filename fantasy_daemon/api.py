"""FastAPI HTTP layer for Fantasy Author.

Multi-universe file-based adapter: serves all universe directories
under a configurable base path.  The daemon and API share the filesystem.

Usage::

    python -m fantasy_author serve --base ~/Documents/Fantasy\\ Author/

Starts the daemon in a background thread and uvicorn on the main thread.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from tinyassets.enrichment_signals import (
    load_enrichment_signals,
    write_enrichment_signals,
)
from tinyassets.universe_soul import (
    ensure_universe_soul,
    has_soul,
    legacy_premise_path,
    premise_from_soul,
    read_legacy_premise,
    read_universe_soul,
    write_universe_soul,
)

logger = logging.getLogger("fantasy_daemon.api")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Fantasy Author",
    version="0.1.0",
    description="HTTP interface for the Fantasy Author autonomous fiction daemon.",
)

# ---------------------------------------------------------------------------
# Configuration (set by serve() before uvicorn.run)
# ---------------------------------------------------------------------------

_base_path: str = ""
_api_key: str = ""

# Optional DaemonController reference -- set when running via `serve`.
# None when the API runs standalone (file-adapter-only mode).
_daemon: Any = None
_daemon_thread: Any = None
_writer_barrier: Any = None


def configure(
    base_path: str,
    api_key: str = "",
    daemon: Any = None,
    daemon_thread: Any = None,
) -> None:
    """Set module-level config before starting uvicorn.

    Called by the ``serve`` CLI command.  Must be called before any
    request is handled.

    When a daemon is provided (normal ``--serve`` startup), its universe
    is persisted for auto-resume.  When no daemon is provided (standalone
    API), checks for a previously persisted universe and auto-starts.

    Parameters
    ----------
    base_path:
        Root directory containing universe subdirectories.
    api_key:
        Optional API key for authentication.
    daemon:
        Optional DaemonController reference.
    daemon_thread:
        Optional threading.Thread running the daemon.
    """
    global _base_path, _api_key, _daemon, _daemon_thread, _writer_barrier
    replacement_barrier = None
    if base_path:
        from tinyassets.scoped_reset import prepare_service_writer_barrier

        replacement_barrier = prepare_service_writer_barrier(Path(base_path))
    previous_barrier = _writer_barrier
    _writer_barrier = replacement_barrier
    _base_path = base_path
    _api_key = api_key
    _daemon = daemon
    _daemon_thread = daemon_thread
    if previous_barrier is not None:
        previous_barrier.release()

    if base_path:
        from tinyassets import daemon_server as author_server

        author_server.sync_universes_from_filesystem(base_path)

    if daemon is not None:
        # Persist the universe the daemon was started on
        uid = _daemon_universe_id()
        if uid:
            _persist_active_universe(uid)
    elif base_path:
        # No daemon provided — check for a persisted universe to auto-resume
        saved = _read_active_universe()
        if saved:
            logger.info("Auto-resuming daemon on persisted universe: %s", saved)
            try:
                _start_daemon_for(saved)
            except Exception:
                logger.warning(
                    "Failed to auto-resume on %s", saved, exc_info=True,
                )


def _base() -> Path:
    """Return the resolved base directory.

    Raises HTTPException if the API has not been configured yet.
    """
    if not _base_path:
        raise HTTPException(
            status_code=503,
            detail="API not configured (no base path set)",
        )
    return Path(_base_path)


def _udir(uid: str) -> Path:
    """Return the universe directory for the given ID.

    The universe directory is ``{base_path}/{uid}/``.

    Raises HTTPException(400) if *uid* contains path-traversal sequences
    (e.g. ``../``) that would resolve outside the base directory.
    """
    base = _base()
    result = (base / uid).resolve()
    if not result.is_relative_to(base.resolve()):
        raise HTTPException(
            status_code=400, detail="Invalid universe ID: path traversal not allowed",
        )
    return result


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_KEY_PATTERN = re.compile(r"^fa_([a-zA-Z0-9_]+)_sk_[a-zA-Z0-9]+$")


def _extract_username(key: str) -> str:
    """Extract the username segment from an API key.

    Key format: ``fa_{username}_sk_{random}``.
    Returns ``"anonymous"`` if the key doesn't match.
    """
    m = _KEY_PATTERN.match(key)
    return m.group(1) if m else "anonymous"


def _require_auth(request: Request) -> str:
    """FastAPI dependency: validate API key, return username.

    If ``FA_API_KEY`` is not set, auth is disabled (development mode)
    and ``"anonymous"`` is returned.
    """
    key = _api_key or os.environ.get("FA_API_KEY", "")
    if not key:
        # No key configured -- open access (dev mode).
        return "anonymous"

    provided = request.headers.get("Authorization", "")
    if provided.startswith("Bearer "):
        provided = provided[7:]

    if provided != key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return _extract_username(key)


def _require_bearer_token(request: Request) -> dict[str, Any]:
    """FastAPI dependency: validate Bearer token (session or master API key).

    Returns the actor dict with user/host info and capabilities.
    """
    from tinyassets import daemon_server as author_server

    auth = request.headers.get("Authorization", "").strip()
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Bearer token")

    token = auth[7:]
    base = _base()

    # Ensure the author server database is initialized
    author_server.initialize_author_server(base)

    # Try to resolve the token (either session or master API key)
    actor = author_server.resolve_bearer_token(
        base,
        token,
        master_api_key=_api_key or "",
        master_username="host",
    )

    if actor is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return actor


def _actor_type(actor: dict[str, Any]) -> str:
    return "operator" if actor.get("token_type") == "master_api_key" else "user"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PremiseBody(BaseModel):
    text: str = Field(..., min_length=1, description="Story premise text")



class NoteBody(BaseModel):
    text: str = Field(..., min_length=1, description="Note text")
    category: str = Field(
        "direction",
        pattern=r"^(protect|concern|direction|observation|error)$",
        description="Note category",
    )
    target: str | None = Field(None, description="File path or scene reference")
    clearly_wrong: bool = Field(False, description="For concerns: provable error?")
    quoted_passage: str = Field("", description="Evidence from prose")
    anchor: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional prose span anchor, e.g. "
            "{'start_line': 12, 'end_line': 14, 'start_column': 3, 'end_column': 20}"
        ),
    )
    tags: list[str] = Field(default_factory=list, description="Optional note tags")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional note metadata",
    )


class NoteStatusBody(BaseModel):
    status: str = Field(
        ...,
        pattern=r"^(unread|read|acted_on|dismissed)$",
        description="New status for the note",
    )


class OutputBody(BaseModel):
    content: str = Field(..., min_length=1, description="File content to write")


class CanonBody(BaseModel):
    filename: str = Field(..., min_length=1, description="Filename (e.g. characters.md)")
    content: str = Field(..., min_length=1, description="File content")


class WorkspaceBody(BaseModel):
    filename: str = Field(..., min_length=1, description="Filename (e.g. notes.md)")
    content: str = Field(..., min_length=1, description="File content")


class CreateUniverseBody(BaseModel):
    name: str | None = Field(
        None,
        description="Human-readable display name (auto-generated if omitted)",
    )
    branch_def_id: str | None = Field(
        None,
        description="Optional workflow loop branch declared in the new universe soul.",
    )


class UpdateUniverseBody(BaseModel):
    name: str = Field(..., min_length=1, description="New display name")


class DaemonControlBody(BaseModel):
    universe: str | None = Field(
        None,
        description="Universe ID to start writing in (switches daemon if different)",
    )


# Multiplayer Author Server Models
class CreateSessionBody(BaseModel):
    username: str = Field(..., min_length=1, description="Username for the session")


class CreateBranchBody(BaseModel):
    name: str = Field(..., min_length=1, description="Branch name")
    parent_branch_id: str | None = Field(None, description="Parent branch ID (optional)")


class CreateUserRequestBody(BaseModel):
    request_type: str = Field(..., description="Type of request (e.g., author_preference)")
    text: str = Field(..., min_length=1, description="Request text")
    branch_id: str | None = Field(None, description="Associated branch ID")
    preferred_author_id: str | None = Field(None, description="Preferred author ID")


class SpawnRuntimeBody(BaseModel):
    author_id: str = Field(..., description="Author ID to run")
    provider_name: str = Field(..., description="Provider name (e.g., codex, opus)")
    model_name: str = Field(..., description="Model name")
    branch_id: str | None = Field(None, description="Target branch ID")


class RegisterAuthorBody(BaseModel):
    display_name: str = Field(..., min_length=1, description="Author display name")
    soul_text: str = Field(..., min_length=1, description="Author soul file text")


class ProposeForkBody(BaseModel):
    universe_id: str = Field(..., description="Universe ID")
    display_name: str = Field(..., min_length=1, description="New author display name")
    soul_text: str = Field(..., min_length=1, description="New author soul text")
    vote_seconds: int = Field(300, ge=1, description="Vote window duration in seconds")
    reason: str = Field("", description="Reason for the fork")


class CastVoteBody(BaseModel):
    choice: str = Field(..., pattern="^(yes|no)$", description="Vote choice")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Convert a human-readable name into a URL-safe slug."""
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    # Truncate to avoid filesystem path length issues
    if len(slug) > 80:
        slug = slug[:80].rstrip("-")
    return slug or ("universe-" + secrets.token_hex(3))


def _validate_universe_id(uid: str) -> Path:
    """Validate universe ID and return its directory path.

    If the directory exists but has no ``universe.json``, one is
    auto-created (backward compat migration).

    Raises 404 if the directory does not exist.
    """
    udir = _udir(uid)
    if not udir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Universe '{uid}' not found",
        )
    # Auto-migrate: create universe.json if missing
    _ensure_universe_json(udir, uid)
    return udir


def _ensure_universe_json(udir: Path, uid: str) -> dict[str, Any]:
    """Read universe.json, creating it if it doesn't exist (migration).

    Returns the parsed metadata dict.
    """
    meta_path = udir / "universe.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    # Create from directory name
    meta = {
        "id": uid,
        "name": uid.replace("-", " ").replace("_", " ").title(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "auto_name": True,
    }
    try:
        udir.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.debug("Failed to write universe.json for %s", uid, exc_info=True)
    return meta


def _read_universe_info(udir: Path, uid: str) -> dict[str, Any]:
    """Build a universe info dict for list/detail responses."""
    meta = _ensure_universe_json(udir, uid)
    info: dict[str, Any] = {
        "id": meta.get("id", uid),
        "name": meta.get("name", uid),
        "created_at": meta.get("created_at"),
        "auto_name": meta.get("auto_name", True),
    }

    # Enrich with status data if available
    status_path = udir / "status.json"
    if status_path.exists():
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
            info["word_count"] = data.get("word_count", 0)
            info["daemon_state"] = data.get("daemon_state", "unknown")
        except (OSError, json.JSONDecodeError):
            info["word_count"] = 0
            info["daemon_state"] = "idle"
    else:
        info["word_count"] = 0
        info["daemon_state"] = "idle"

    info["has_premise"] = bool(
        read_legacy_premise(udir).strip() or premise_from_soul(udir).strip()
    )
    info["has_soul"] = has_soul(udir)
    return info


# ---------------------------------------------------------------------------
# Daemon lifecycle helpers
# ---------------------------------------------------------------------------


def _stop_current_daemon(timeout: float = 10.0) -> None:
    """Stop the current daemon and wait for its thread to finish."""
    global _daemon, _daemon_thread
    if _daemon is not None:
        try:
            _daemon._stop_event.set()
            _daemon._paused.clear()
        except Exception:
            logger.debug("Error signalling daemon stop", exc_info=True)
    if _daemon_thread is not None and _daemon_thread.is_alive():
        _daemon_thread.join(timeout=timeout)
    # Clear runtime singletons so the next daemon creates fresh SQLite
    # connections in its own thread (SQLite objects can't cross threads).
    from fantasy_daemon import runtime
    runtime.reset()
    _daemon = None
    _daemon_thread = None


def _start_daemon_for(universe_id: str) -> None:
    """Start a new daemon for the given universe.

    Imports DaemonController lazily to avoid circular imports.
    Persists the universe ID so the daemon auto-resumes after restart.
    """
    global _daemon, _daemon_thread
    from fantasy_daemon.__main__ import DaemonController

    universe_path = str(_base() / universe_id)
    controller = DaemonController(
        universe_path=universe_path,
        no_tray=True,
    )
    t = threading.Thread(
        target=controller.start, name="daemon", daemon=False,
    )
    t.start()
    _daemon = controller
    _daemon_thread = t

    # Persist active universe for auto-resume after restart
    _persist_active_universe(universe_id)


def _daemon_universe_id() -> str | None:
    """Return the universe ID the daemon is currently bound to, or None."""
    if _daemon is None:
        return None
    try:
        # Prefer the explicit _universe_id (set by DaemonController.__init__)
        uid = getattr(_daemon, "_universe_id", None)
        if isinstance(uid, str) and uid:
            return uid
        # Fallback: derive from _universe_path
        return Path(_daemon._universe_path).name
    except Exception:
        return None


def _persist_active_universe(universe_id: str) -> None:
    """Write the active universe ID to disk for auto-resume after restart."""
    if not _base_path:
        return
    try:
        marker = Path(_base_path) / ".active_universe"
        marker.write_text(universe_id, encoding="utf-8")
    except OSError:
        logger.debug("Failed to persist active universe", exc_info=True)


def _clear_active_universe() -> None:
    """Remove the persisted active universe marker."""
    if not _base_path:
        return
    try:
        marker = Path(_base_path) / ".active_universe"
        if marker.exists():
            marker.unlink()
    except OSError:
        logger.debug("Failed to clear active universe marker", exc_info=True)


def _read_active_universe() -> str | None:
    """Read the persisted active universe ID, or None if not set."""
    if not _base_path:
        return None
    try:
        marker = Path(_base_path) / ".active_universe"
        if marker.exists():
            uid = marker.read_text(encoding="utf-8").strip()
            if uid and (_base() / uid).is_dir():
                return uid
    except OSError:
        logger.debug("Failed to read active universe marker", exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/health")
def health() -> dict[str, Any]:
    """Health check."""
    daemon_state = "not_attached"
    if _daemon is not None:
        try:
            daemon_state = _daemon.daemon_state
        except Exception:
            daemon_state = "error"

    # Count universes
    universe_count = 0
    if _base_path:
        try:
            base = Path(_base_path)
            if base.is_dir():
                universe_count = sum(
                    1 for d in base.iterdir()
                    if d.is_dir() and not d.name.startswith(".")
                )
        except OSError:
            pass

    result: dict[str, Any] = {
        "status": "ok",
        "universes": universe_count,
        "daemon": daemon_state,
    }
    active = _daemon_universe_id()
    if active:
        result["active_universe"] = active
        # Report synthesis queue for active universe
        try:
            active_dir = _base() / active
            pending = _count_pending_synthesis(active_dir)
            if pending > 0:
                result["pending_synthesis"] = pending
        except Exception:
            pass

    # Provider health
    providers_info, unhealthy = _get_provider_health()
    result["providers"] = providers_info
    if unhealthy:
        result["unhealthy_roles"] = unhealthy

    return result


def _get_provider_health() -> tuple[dict[str, Any], list[str]]:
    """Build provider status info and identify unhealthy roles."""
    from fantasy_daemon.providers.router import FALLBACK_CHAINS

    # Known providers with setup hints
    _SETUP_HINTS: dict[str, str] = {
        "ollama-local": "Needs Ollama running locally",
        "claude-code": "Needs claude CLI subscription",
        "codex": "Needs codex CLI subscription",
    }
    _FAMILIES: dict[str, str] = {
        "claude-code": "anthropic",
        "codex": "openai",
        "ollama-local": "local",
    }

    router = _daemon._router if _daemon and hasattr(_daemon, "_router") else None
    registered = set(router.available_providers) if router else set()

    # Build per-provider status
    providers_info: dict[str, Any] = {}
    all_known = set(FALLBACK_CHAINS.get("writer", [])) | set(FALLBACK_CHAINS.get("judge", []))
    for name in sorted(all_known):
        roles = [
            role for role, chain in FALLBACK_CHAINS.items()
            if name in chain
        ]
        info: dict[str, Any] = {
            "family": _FAMILIES.get(name, "unknown"),
            "roles": roles,
        }

        if name not in registered:
            info["status"] = "not_registered"
            info["setup"] = _SETUP_HINTS.get(name, "")
        elif router is not None:
            cd = router._quota.cooldown_remaining(name)
            if cd > 0:
                info["status"] = "cooldown"
                info["cooldown_remaining"] = cd
            else:
                info["status"] = "healthy"
        else:
            info["status"] = "unknown"

        providers_info[name] = info

    # Identify roles with no working provider
    unhealthy: list[str] = []
    for role, chain in FALLBACK_CHAINS.items():
        has_working = any(
            name in registered
            and (router is None or router._quota.cooldown_remaining(name) == 0)
            for name in chain
        )
        if not has_working:
            unhealthy.append(role)

    return providers_info, unhealthy


# -- Universe discovery & management ----------------------------------------


@app.get("/v1/universes")
def list_universes(_user: str = Depends(_require_auth)) -> dict[str, Any]:
    """List all universes under the base directory.

    A universe is any subdirectory containing ``universe.json``
    (or any non-hidden subdirectory, which gets auto-migrated).
    """
    base = _base()
    if not base.is_dir():
        return {"universes": []}

    universes: list[dict[str, Any]] = []
    try:
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                info = _read_universe_info(entry, entry.name)
                universes.append(info)
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to list universes: {e}",
        )
    return {"universes": universes}


@app.post("/v1/universes", status_code=201)
def create_universe(
    body: CreateUniverseBody | None = None,
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Create a new universe directory with metadata.

    If ``name`` is provided, a slug is generated from it.
    If omitted, a random short ID is used and the name is auto-generated.
    """
    base = _base()
    name = body.name if body and body.name else None
    auto_name = name is None

    if name:
        slug = _slugify(name)
    else:
        slug = "universe-" + secrets.token_hex(3)
        name = slug.replace("-", " ").title()

    # Ensure uniqueness
    udir = base / slug
    if udir.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Universe '{slug}' already exists",
        )

    meta = {
        "id": slug,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "auto_name": auto_name,
    }

    try:
        udir.mkdir(parents=True, exist_ok=True)
        soul = ensure_universe_soul(
            udir,
            loop_branch_def_id=(
                body.branch_def_id if body and body.branch_def_id else ""
            ),
        )
        (udir / "canon").mkdir(exist_ok=True)
        (udir / "output").mkdir(exist_ok=True)
        (udir / "universe.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8",
        )
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to create universe: {e}",
        )

    return {"id": slug, "name": name, "has_soul": True, "soul": soul.summary()}


@app.patch("/v1/universes/{uid}")
def update_universe(
    uid: str,
    body: UpdateUniverseBody,
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Update universe metadata (currently: display name)."""
    udir = _validate_universe_id(uid)
    meta = _ensure_universe_json(udir, uid)

    meta["name"] = body.name
    meta["auto_name"] = False

    meta_path = udir / "universe.json"
    try:
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to update universe: {e}",
        )

    return {
        "id": uid,
        "name": meta["name"],
        "auto_name": meta["auto_name"],
    }


@app.delete("/v1/universes/{uid}")
def delete_universe(
    uid: str,
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Delete a universe and all its data.

    If the daemon is currently running on this universe it is stopped
    first.  The active-universe marker is cleared if it matches.
    """
    udir = _validate_universe_id(uid)

    # Stop daemon if it's running on this universe
    if _daemon_universe_id() == uid:
        _stop_current_daemon()
        _clear_active_universe()
    elif _read_active_universe() == uid:
        _clear_active_universe()

    try:
        shutil.rmtree(udir)
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to delete universe: {e}",
        )

    return {"id": uid, "deleted": True}


# -- Premise ---------------------------------------------------------------


@app.get("/v1/universes/{uid}/premise")
def get_premise(uid: str, _user: str = Depends(_require_auth)) -> dict[str, Any]:
    """Read the current premise, falling back to soul.md purpose."""
    udir = _validate_universe_id(uid)
    text = read_legacy_premise(udir)
    source = "PROGRAM.md"
    soul = read_universe_soul(udir)
    if not text.strip() and soul is not None:
        text = soul.purpose
        source = "soul.md"
    if not text.strip():
        raise HTTPException(
            status_code=404,
            detail="No premise set yet. Use the set premise endpoint to create one.",
        )
    return {
        "text": text,
        "source": source,
        "has_soul": soul is not None,
        "soul": soul.summary() if soul is not None else None,
    }


@app.post("/v1/universes/{uid}/premise", status_code=200)
def set_premise(
    uid: str, body: PremiseBody, _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Write or overwrite the premise as soul.md purpose plus legacy mirror."""
    udir = _validate_universe_id(uid)
    try:
        soul = write_universe_soul(
            udir, purpose=body.text, lineage="created-from-premise",
        )
        legacy_premise_path(udir).write_text(body.text, encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write premise: {e}")
    return {"status": "ok", "has_soul": True, "soul": soul.summary()}


# -- Notes -----------------------------------------------------------------


@app.post("/v1/universes/{uid}/notes", status_code=201)
def post_note(
    uid: str, body: NoteBody, user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Add a note to the universe."""
    from fantasy_daemon.notes import add_note

    udir = _validate_universe_id(uid)
    note = add_note(
        udir,
        source="user",
        text=body.text,
        category=body.category,
        target=body.target,
        clearly_wrong=body.clearly_wrong,
        quoted_passage=body.quoted_passage,
        anchor=body.anchor,
        tags=body.tags,
        metadata=body.metadata,
    )
    return {"status": "ok", "note": note.to_dict()}


@app.get("/v1/universes/{uid}/notes")
def get_notes(
    uid: str,
    source: str | None = None,
    category: str | None = None,
    status: str | None = None,
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """List notes with optional filters."""
    from fantasy_daemon.notes import list_notes

    udir = _validate_universe_id(uid)
    notes = list_notes(udir, source=source, category=category, status=status)
    return {"notes": [n.to_dict() for n in notes]}


@app.patch("/v1/universes/{uid}/notes/{note_id}")
def patch_note(
    uid: str, note_id: str, body: NoteStatusBody,
    _user: str = Depends(_require_auth),
) -> dict[str, str]:
    """Update a note's status."""
    from fantasy_daemon.notes import update_note_status

    udir = _validate_universe_id(uid)
    if not update_note_status(udir, note_id, body.status):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"status": "ok"}


@app.delete("/v1/universes/{uid}/notes/{note_id}")
def delete_note_endpoint(
    uid: str, note_id: str, _user: str = Depends(_require_auth),
) -> dict[str, str]:
    """Delete a note."""
    from fantasy_daemon.notes import delete_note

    udir = _validate_universe_id(uid)
    if not delete_note(udir, note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"status": "ok"}


# -- Status ----------------------------------------------------------------


@app.get("/v1/universes/{uid}/status")
def get_status(uid: str, _user: str = Depends(_require_auth)) -> dict[str, Any]:
    """Read status.json from the universe directory.

    Adds ``is_active`` to indicate whether the daemon is currently
    writing this universe.  When the daemon is on a different universe,
    ``daemon_state`` is overridden to ``"idle"`` so the GPT doesn't
    report stale status as live activity.
    """
    udir = _validate_universe_id(uid)
    status_path = udir / "status.json"
    if not status_path.exists():
        return {
            "daemon_state": "idle",
            "is_active": False,
            "current_phase": "idle",
            "word_count": 0,
            "chapters_complete": 0,
            "scenes_complete": 0,
        }
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to read status: {e}")

    # Mark whether the daemon is actively writing this universe
    active_uid = _daemon_universe_id()
    is_active = active_uid is not None and active_uid == uid
    data["is_active"] = is_active
    if not is_active and data.get("daemon_state") not in (None, "idle"):
        data["daemon_state"] = "idle"

    # Report pending synthesis count so GPT can inform user
    data["pending_synthesis"] = _count_pending_synthesis(udir)
    return data


def _count_pending_synthesis(udir: Path) -> int:
    """Count pending synthesize_source signals for a universe.

    Reads through the enrichment-signals helper so the canonical
    ``enrichment_signals.json`` is preferred and the legacy
    ``worldbuild_signals.json`` is still honored during the deprecation window.
    """
    signals = load_enrichment_signals(udir)
    return sum(
        1 for s in signals
        if isinstance(s, dict) and s.get("type") == "synthesize_source"
    )


MAX_SYNTHESIS_RETRIES = 3


def _reemit_synthesis_signals(
    udir: Path, canon_dir: Path, filenames: list[str],
) -> list[str]:
    """Re-emit synthesize_source signals for sources that failed synthesis.

    Called when the gap state is detected (synthesis_complete=false but
    no pending signals). Tracks retry count in the manifest and stops
    re-emitting after MAX_SYNTHESIS_RETRIES to prevent infinite loops
    for permanently unfixable sources (binary/corrupted files).

    Returns the list of filenames that were actually re-emitted.
    """
    from fantasy_daemon.ingestion.core import detect_file_type

    manifest_path = canon_dir / ".manifest.json"

    # Load manifest to track retry counts
    manifest: dict[str, Any] = {}
    try:
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    reemitted: list[str] = []

    # Strict read: this path WRITES the signal list back, so a PRESENT-but-corrupt
    # file must fail loud and be left untouched for recovery rather than silently
    # overwritten with a derived list (Hard Rule #8 — would drop queued signals).
    # Canonical ``enrichment_signals.json`` preferred; legacy
    # ``worldbuild_signals.json`` read as fallback during deprecation.
    try:
        existing: list[dict[str, Any]] = load_enrichment_signals(udir, strict=True)
    except RuntimeError:
        logger.error(
            "Refusing to re-emit synthesis signals: existing signal file for "
            "%s is present but corrupt; leaving it untouched for recovery",
            udir, exc_info=True,
        )
        return reemitted

    try:
        # Only add signals for files not already queued
        already_queued = {
            s.get("source_file")
            for s in existing
            if isinstance(s, dict) and s.get("type") == "synthesize_source"
        }

        for filename in filenames:
            if filename in already_queued:
                continue

            # Check retry count — skip permanently failed sources.
            # The worldbuild node increments synthesis_attempts when
            # it actually attempts (and fails) synthesis.
            entry = manifest.get(filename, {})
            attempts = entry.get("synthesis_attempts", 0)
            if attempts >= MAX_SYNTHESIS_RETRIES or entry.get("synthesis_failed"):
                continue

            source_path = canon_dir / "sources" / filename
            byte_count = source_path.stat().st_size if source_path.exists() else 0
            detected = detect_file_type(filename)
            existing.append({
                "type": "synthesize_source",
                "topic": Path(filename).stem.replace("-", "_").replace(" ", "_"),
                "detail": (
                    f"Re-queued source file: {filename}"
                    f" ({byte_count} bytes, {detected.file_type.value})"
                    f" (attempt {attempts + 1}/{MAX_SYNTHESIS_RETRIES})"
                ),
                "source_file": filename,
                "file_type": detected.file_type.value,
                "mime_type": detected.mime_type,
            })
            reemitted.append(filename)

        write_enrichment_signals(udir, existing)
        if reemitted:
            logger.info(
                "Re-emitted synthesis signals for %d unsynthesized sources",
                len(reemitted),
            )
    except (OSError, json.JSONDecodeError):
        logger.debug("Failed to re-emit synthesis signals", exc_info=True)
    return reemitted


# -- Overview (composite) --------------------------------------------------


@app.get("/v1/universes/{uid}/overview")
def get_overview(uid: str, _user: str = Depends(_require_auth)) -> dict[str, Any]:
    """Composite endpoint: status + progress + output list in one call.

    This is the primary endpoint for "tell me about my story" — the GPT
    calls this once instead of making 3+ separate requests.
    """
    udir = _validate_universe_id(uid)

    # Status
    status_path = udir / "status.json"
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            status = {"daemon_state": "idle", "word_count": 0}
    else:
        status = {"daemon_state": "idle", "word_count": 0}

    # Progress
    progress_path = udir / "progress.md"
    progress = ""
    if progress_path.exists():
        try:
            progress = progress_path.read_text(encoding="utf-8")
        except OSError:
            pass

    # Output files
    output_dir = udir / "output"
    output_files: list[dict[str, Any]] = []
    if output_dir.is_dir():
        try:
            for entry in sorted(output_dir.rglob("*")):
                if entry.is_file():
                    rel = entry.relative_to(output_dir)
                    output_files.append({
                        "path": str(rel).replace("\\", "/"),
                        "size": entry.stat().st_size,
                    })
        except OSError:
            pass

    # Recent activity (last 10 lines)
    log_path = udir / "activity.log"
    recent_activity: list[str] = []
    if log_path.exists():
        try:
            all_lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            recent_activity = all_lines[-10:]
        except OSError:
            pass

    # Readiness: quick summary for GPT to assess if the universe is ready
    canon_dir = udir / "canon"
    canon_count = 0
    if canon_dir.is_dir():
        try:
            canon_count = sum(
                1 for f in canon_dir.iterdir()
                if f.is_file() and f.suffix == ".md"
            )
        except OSError:
            pass

    premise_set = bool(
        read_legacy_premise(udir).strip() or premise_from_soul(udir).strip()
    )
    daemon_state = status.get("daemon_state", "idle")

    # Check if daemon is actually on this universe
    active_uid = _daemon_universe_id()
    is_active = active_uid is not None and active_uid == uid
    status["is_active"] = is_active
    status["pending_synthesis"] = _count_pending_synthesis(udir)
    if not is_active and daemon_state not in ("idle", None):
        daemon_state = "idle"
        status["daemon_state"] = "idle"

    providers_available: list[str] = []
    if _daemon is not None and hasattr(_daemon, "_router") and _daemon._router is not None:
        providers_available = list(_daemon._router.available_providers)

    return {
        "status": status,
        "progress": progress or "No progress yet.",
        "output_files": output_files,
        "recent_activity": recent_activity,
        "readiness": {
            "canon_files": canon_count,
            "premise_set": premise_set,
            "daemon_state": daemon_state,
            "is_active": is_active,
            "pending_synthesis": _count_pending_synthesis(udir),
            "providers_available": providers_available,
        },
    }


# -- Activity --------------------------------------------------------------


@app.get("/v1/universes/{uid}/activity")
def get_activity(
    uid: str,
    lines: int = Query(50, ge=1, le=500),
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Read the most recent lines from activity.log."""
    udir = _validate_universe_id(uid)
    log_path = udir / "activity.log"
    if not log_path.exists():
        return {"lines": []}
    try:
        content = log_path.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to read activity log: {e}")
    all_lines = content.strip().splitlines()
    tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
    return {"lines": tail}


# -- Progress --------------------------------------------------------------


@app.get("/v1/universes/{uid}/progress")
def get_progress(uid: str, _user: str = Depends(_require_auth)) -> dict[str, str]:
    """Read progress.md from the universe directory."""
    udir = _validate_universe_id(uid)
    progress_path = udir / "progress.md"
    if not progress_path.exists():
        return {"text": "No progress yet. The daemon may not have started writing."}
    try:
        text = progress_path.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to read progress: {e}")
    return {"text": text}


# -- Facts -----------------------------------------------------------------


def _find_db_path(udir: Path) -> str | None:
    """Locate the world state DB for a universe.

    Checks the running daemon first, then falls back to ``story.db``
    inside the universe directory.  Returns None if no DB is found.
    """
    # If daemon is running for this universe, use its DB path
    if _daemon is not None:
        try:
            daemon_uid = _daemon._universe_id
            if daemon_uid == udir.name:
                db = Path(_daemon._db_path)
                if db.exists():
                    return str(db)
        except Exception:
            pass

    # Fallback: story.db in the universe directory
    story_db = udir / "story.db"
    if story_db.exists():
        return str(story_db)

    return None


@app.get("/v1/universes/{uid}/facts")
def get_facts(
    uid: str,
    chapter: int | None = Query(None, ge=1, description="Filter by chapter number"),
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Query extracted facts from the world state database.

    Returns all facts, or facts for a specific chapter if ``chapter``
    is provided.  Each fact includes source_type, language_type,
    confidence, importance, and originating scene.
    """
    udir = _validate_universe_id(uid)
    db_path = _find_db_path(udir)
    if db_path is None:
        return {"facts": [], "count": 0}

    from fantasy_daemon.nodes.world_state_db import (
        connect,
        get_all_facts,
        get_facts_for_chapter,
        init_db,
    )

    try:
        init_db(db_path)
        with connect(db_path) as conn:
            if chapter is not None:
                facts = get_facts_for_chapter(conn, chapter)
            else:
                facts = get_all_facts(conn)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to query facts: {e}",
        )

    return {"facts": facts, "count": len(facts)}


# -- Characters ------------------------------------------------------------


@app.get("/v1/universes/{uid}/characters")
def get_characters(
    uid: str,
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Query tracked characters from the world state database.

    Returns all characters with their current location, emotional
    state, associated knowledge facts, and last updated scene.
    """
    udir = _validate_universe_id(uid)
    db_path = _find_db_path(udir)
    if db_path is None:
        return {"characters": [], "count": 0}

    from fantasy_daemon.nodes.world_state_db import (
        connect,
        get_all_characters,
        init_db,
    )

    try:
        init_db(db_path)
        with connect(db_path) as conn:
            characters = get_all_characters(conn)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to query characters: {e}",
        )

    return {"characters": characters, "count": len(characters)}


# -- Promises --------------------------------------------------------------


@app.get("/v1/universes/{uid}/promises")
def get_promises(
    uid: str,
    status: str | None = Query(None, description="Filter: active, resolved, or overdue"),
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Query narrative promises from both scene-level and series-level trackers.

    Returns promises grouped by status.  Use ``?status=active``,
    ``?status=resolved``, or ``?status=overdue`` to filter.
    """
    udir = _validate_universe_id(uid)
    db_path = _find_db_path(udir)

    scene_promises: list[dict[str, Any]] = []
    series_promises: list[dict[str, Any]] = []

    # Scene-level promises from world_state_db
    if db_path is not None:
        from fantasy_daemon.nodes.world_state_db import (
            connect,
            init_db,
        )

        try:
            init_db(db_path)
            with connect(db_path) as conn:
                all_rows = conn.execute(
                    "SELECT * FROM promises ORDER BY importance DESC"
                ).fetchall()
                scene_promises = [dict(r) for r in all_rows]
        except Exception as e:
            logger.warning("Failed to query scene promises: %s", e)

    # Series-level promises from SeriesPromiseTracker (via runtime)
    try:
        from fantasy_daemon import runtime

        tracker = runtime.promise_tracker
        if tracker is not None:
            for p in tracker.get_all_promises():
                series_promises.append({
                    "promise_id": p.promise_id,
                    "description": p.description,
                    "status": p.status,
                    "priority": p.priority,
                    "created_book": p.created_book,
                    "created_chapter": p.created_chapter,
                    "resolved_book": p.resolved_book,
                    "resolved_chapter": p.resolved_chapter,
                    "evidence": p.evidence,
                    "level": "series",
                })
    except Exception as e:
        logger.warning("Failed to query series promises: %s", e)

    # Tag scene-level promises
    for p in scene_promises:
        p["level"] = "scene"

    combined = scene_promises + series_promises

    # Filter by status
    if status == "active":
        combined = [p for p in combined if p.get("status") in ("active", "open")]
    elif status == "resolved":
        combined = [p for p in combined if p.get("status") == "resolved"]
    elif status == "overdue":
        # For scene-level: active promises older than 3 chapters (heuristic)
        combined = [
            p for p in combined
            if p.get("status") in ("active", "open")
        ]

    return {"promises": combined, "count": len(combined)}


# -- Output ----------------------------------------------------------------


@app.get("/v1/universes/{uid}/output")
def list_output(uid: str, _user: str = Depends(_require_auth)) -> dict[str, Any]:
    """List all files in the output/ directory recursively.

    Returns file paths relative to ``output/`` with size in bytes.
    """
    udir = _validate_universe_id(uid)
    output_dir = udir / "output"
    if not output_dir.exists():
        return {"files": []}
    files: list[dict[str, Any]] = []
    try:
        for entry in sorted(output_dir.rglob("*")):
            if entry.is_file():
                rel = entry.relative_to(output_dir)
                files.append({
                    "path": str(rel).replace("\\", "/"),
                    "size": entry.stat().st_size,
                })
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to list output files: {e}")
    return {"files": files}


@app.get("/v1/universes/{uid}/output/{path:path}")
def get_output(uid: str, path: str, _user: str = Depends(_require_auth)) -> PlainTextResponse:
    """Read a file or chapter directory from output/.

    When *path* resolves to a file, returns its contents directly.
    When *path* resolves to a directory (e.g. ``book-1/chapter-01``),
    concatenates all ``.md`` files inside in sorted order with scene
    separators, giving the user a clean chapter view.
    """
    udir = _validate_universe_id(uid)
    # Sanitize: resolve and ensure it stays inside output/
    output_dir = udir / "output"
    target = (output_dir / path).resolve()
    if not target.is_relative_to(output_dir.resolve()):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Output file not found: {path}")

    try:
        if target.is_dir():
            # Concatenate scene files in sorted order
            scene_files = sorted(
                f for f in target.iterdir()
                if f.is_file() and f.suffix == ".md"
            )
            if not scene_files:
                raise HTTPException(status_code=404, detail=f"No scene files in: {path}")
            parts = [f.read_text(encoding="utf-8") for f in scene_files]
            content = "\n\n---\n\n".join(parts)
        else:
            content = target.read_text(encoding="utf-8")
    except HTTPException:
        raise
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to read output: {e}")
    return PlainTextResponse(content)


@app.put("/v1/universes/{uid}/output/{path:path}")
def put_output(
    uid: str,
    path: str,
    body: OutputBody,
    _user: str = Depends(_require_auth),
) -> dict[str, str]:
    """Disabled — output files are daemon-written only.

    All story changes go through notes, not direct edits.
    This endpoint exists for backwards compatibility but rejects all writes.
    """
    raise HTTPException(
        status_code=403,
        detail="Output files are read-only. Use notes to guide the story.",
    )


# -- Canon -----------------------------------------------------------------


@app.get("/v1/universes/{uid}/canon")
def list_canon(uid: str, _user: str = Depends(_require_auth)) -> dict[str, Any]:
    """List all files in the canon/ directory.

    Returns filenames with size in bytes.
    """
    udir = _validate_universe_id(uid)
    canon_dir = udir / "canon"
    if not canon_dir.exists():
        return {"files": []}
    files: list[dict[str, Any]] = []
    try:
        for entry in sorted(canon_dir.iterdir()):
            if entry.is_file() and not entry.name.startswith("."):
                files.append({
                    "filename": entry.name,
                    "size": entry.stat().st_size,
                })
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to list canon files: {e}")
    return {"files": files}


@app.get("/v1/universes/{uid}/canon/sources")
def list_canon_sources(
    uid: str, _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """List source files, their synthesis status, and produced docs.

    Returns enough info for the GPT to tell the user whether uploaded
    files are still being processed or have been synthesized into
    structured worldbuilding documents.
    """
    udir = _validate_universe_id(uid)
    canon_dir = udir / "canon"
    sources_dir = canon_dir / "sources"

    # 1. List raw source files
    source_files: list[dict[str, Any]] = []
    if sources_dir.is_dir():
        try:
            for entry in sorted(sources_dir.iterdir()):
                if entry.is_file() and not entry.name.startswith("."):
                    stat = entry.stat()
                    source_files.append({
                        "filename": entry.name,
                        "size": stat.st_size,
                        "modified_at": datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc,
                        ).isoformat(),
                    })
        except OSError:
            pass

    # 2. Load manifest for synthesis mappings
    manifest_data: dict[str, Any] = {}
    manifest_path = canon_dir / ".manifest.json"
    if manifest_path.exists():
        try:
            manifest_data = json.loads(
                manifest_path.read_text(encoding="utf-8"),
            )
        except (OSError, json.JSONDecodeError):
            pass

    # Enrich source files with manifest metadata (synthesis status)
    for sf in source_files:
        entry = manifest_data.get(sf["filename"], {})
        synth_docs = entry.get("synthesized_docs", [])
        sf["file_type"] = entry.get("file_type", "unknown")
        sf["synthesized_docs"] = synth_docs
        sf["synthesis_complete"] = len(synth_docs) > 0
        sf["synthesis_failed"] = bool(entry.get("synthesis_failed"))

    # 3. Collect synthesized canon docs (routed_to == "sources" with
    #    synthesized_docs populated in manifest)
    synthesized_docs: list[dict[str, Any]] = []
    for _name, entry in manifest_data.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("routed_to") != "sources":
            continue
        for doc_name in entry.get("synthesized_docs", []):
            doc_path = canon_dir / doc_name
            if doc_path.exists() and doc_path.is_file():
                stat = doc_path.stat()
                synthesized_docs.append({
                    "filename": doc_name,
                    "size": stat.st_size,
                    "source_file": entry.get("filename", _name),
                })

    # 4. Pending synthesis count (and re-emit for gap state)
    pending = _count_pending_synthesis(udir)

    # Detect gap state: sources exist with synthesis_complete=false but
    # no pending signals. Re-emit synthesize_source signals so the
    # worldbuild node retries on its next cycle.
    unsynthesized = [
        sf["filename"] for sf in source_files
        if not sf["synthesis_complete"] and not sf.get("synthesis_failed")
    ]
    if unsynthesized and pending == 0:
        reemitted = _reemit_synthesis_signals(udir, canon_dir, unsynthesized)
        pending = len(reemitted)

    return {
        "source_files": source_files,
        "source_count": len(source_files),
        "pending_synthesis": pending,
        "synthesized_docs": synthesized_docs,
        "synthesized_count": len(synthesized_docs),
    }


@app.get("/v1/universes/{uid}/canon/sources/{filename}")
def get_canon_source_file(
    uid: str, filename: str, _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Read a specific uploaded source file with integrity checksums."""
    import hashlib

    udir = _validate_universe_id(uid)
    canon_dir = udir / "canon"
    sources_dir = canon_dir / "sources"

    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid filename. Use list_sources to discover available "
                "source files."
            ),
        )

    target = sources_dir / safe_name
    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Source file not found: {filename}. Use list_sources to "
                "inspect available source files."
            ),
        )
    if not target.is_file():
        raise HTTPException(
            status_code=400,
            detail="Path is not a file. Use list_sources to inspect available source files.",
        )

    manifest_data: dict[str, Any] = {}
    manifest_path = canon_dir / ".manifest.json"
    if manifest_path.exists():
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    try:
        raw = target.read_bytes()
        content = raw.decode("utf-8")
        stat = target.stat()
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail=(
                "Source file is not valid UTF-8 text. Use list_sources to "
                "inspect available source files."
            ),
        )
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to read source file: {e}")

    entry = manifest_data.get(safe_name, {})
    synth_docs = entry.get("synthesized_docs", [])
    return {
        "filename": safe_name,
        "content": content,
        "byte_count": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc,
        ).isoformat(),
        "file_type": entry.get("file_type", "unknown"),
        "synthesized_docs": synth_docs,
        "synthesis_complete": len(synth_docs) > 0,
        "synthesis_failed": bool(entry.get("synthesis_failed")),
    }


@app.get("/v1/universes/{uid}/canon/{filename}")
def get_canon_file(
    uid: str, filename: str, _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Read a specific canon file with integrity checksums."""
    import hashlib

    udir = _validate_universe_id(uid)
    canon_dir = udir / "canon"
    # Sanitize: only allow bare filenames (no path separators)
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    target = canon_dir / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Canon file not found: {filename}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    try:
        raw = target.read_bytes()
        content = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to read canon file: {e}")
    return {
        "filename": safe_name,
        "content": content,
        "byte_count": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


@app.post("/v1/universes/{uid}/canon", status_code=201)
def post_canon(
    uid: str, body: CanonBody, _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Add a reference file to the canon/ directory with integrity checksums."""
    import hashlib

    udir = _validate_universe_id(uid)
    # Sanitize filename
    safe_name = Path(body.filename).name
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    canon_dir = udir / "canon"
    try:
        canon_dir.mkdir(parents=True, exist_ok=True)
        raw = body.content.encode("utf-8")
        (canon_dir / safe_name).write_bytes(raw)
        # Stamp as user-directed edit — highest tier, daemon won't overwrite
        import json as _json
        import time as _time
        marker = canon_dir / f".{safe_name}.reviewed"
        marker.write_text(
            _json.dumps({"reviewed_at": _time.time(), "model": "user"}),
            encoding="utf-8",
        )
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write canon file: {e}")
    return {
        "status": "ok",
        "filename": safe_name,
        "byte_count": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


@app.post("/v1/universes/{uid}/canon/upload", status_code=201)
async def upload_canon_files(
    uid: str,
    body: dict[str, Any],
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Upload files to canon/ via OpenAI file references.

    The GPT populates ``openaiFileIdRefs`` with file metadata including
    a ``download_link``.  This endpoint downloads each file, runs it
    through the ingestion pipeline (type detection, routing, synthesis
    signals), and stamps it with user-tier provenance.
    """
    import hashlib

    import httpx

    from fantasy_daemon.ingestion.core import ingest_file

    udir = _validate_universe_id(uid)
    canon_dir = udir / "canon"
    canon_dir.mkdir(parents=True, exist_ok=True)

    file_refs = body.get("openaiFileIdRefs", [])
    if not file_refs:
        raise HTTPException(status_code=400, detail="No file references provided")

    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for ref in file_refs:
            url = ref.get("download_link", "")
            filename = Path(ref.get("name", "unnamed.md")).name
            if not filename or filename in (".", ".."):
                filename = "unnamed.md"

            if not url:
                results.append({
                    "filename": filename, "status": "error",
                    "detail": "no download_link",
                })
                continue

            try:
                resp = await client.get(url)
                resp.raise_for_status()
                raw = resp.content

                # Run through ingestion pipeline
                ingest_result = ingest_file(
                    canon_dir, filename, raw,
                    universe_path=udir,
                )

                # Stamp as user-directed upload — highest tier
                import json as _json
                import time as _time

                # Marker goes on the actual file location
                if ingest_result.routed_to == "sources":
                    target_dir = canon_dir / "sources"
                else:
                    target_dir = canon_dir
                marker = target_dir / f".{filename}.reviewed"
                marker.write_text(
                    _json.dumps({
                        "reviewed_at": _time.time(),
                        "model": "user",
                    }),
                    encoding="utf-8",
                )

                results.append({
                    "filename": filename,
                    "status": "ok",
                    "byte_count": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "file_type": ingest_result.file_type.value,
                    "routed_to": ingest_result.routed_to,
                    "signal_emitted": ingest_result.signal_emitted,
                })
            except httpx.HTTPError as e:
                logger.warning("Failed to download %s: %s", filename, e)
                results.append({
                    "filename": filename, "status": "error",
                    "detail": str(e),
                })
            except OSError as e:
                logger.warning("Failed to write %s: %s", filename, e)
                results.append({
                    "filename": filename, "status": "error",
                    "detail": str(e),
                })

    return {"files": results, "count": len(results)}


@app.post("/v1/universes/{uid}/canon/batch", status_code=201)
def batch_upload_canon(
    uid: str,
    body: dict[str, Any],
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Batch upload mixed-media files via base64 content.

    Accepts an array of files, each with ``filename`` and ``content``
    (base64-encoded bytes).  Runs each through the ingestion pipeline
    for type detection, routing, and synthesis signals.

    Request body::

        {
            "files": [
                {"filename": "map.png", "content": "<base64>"},
                {"filename": "notes.md", "content": "<base64>"}
            ]
        }
    """
    import base64
    import hashlib

    from fantasy_daemon.ingestion.core import ingest_file

    udir = _validate_universe_id(uid)
    canon_dir = udir / "canon"
    canon_dir.mkdir(parents=True, exist_ok=True)

    files = body.get("files", [])
    if not files:
        raise HTTPException(
            status_code=400, detail="No files provided",
        )

    results: list[dict[str, Any]] = []
    for entry in files:
        filename = Path(entry.get("filename", "unnamed.bin")).name
        if not filename or filename in (".", ".."):
            filename = "unnamed.bin"

        b64_content = entry.get("content", "")
        if not b64_content:
            results.append({
                "filename": filename, "status": "error",
                "detail": "no content provided",
            })
            continue

        try:
            raw = base64.b64decode(b64_content)
        except Exception:
            results.append({
                "filename": filename, "status": "error",
                "detail": "invalid base64 content",
            })
            continue

        try:
            ingest_result = ingest_file(
                canon_dir, filename, raw,
                universe_path=udir,
            )

            # Stamp as user upload
            import json as _json
            import time as _time

            if ingest_result.routed_to == "sources":
                target_dir = canon_dir / "sources"
            else:
                target_dir = canon_dir
            marker = target_dir / f".{filename}.reviewed"
            marker.write_text(
                _json.dumps({
                    "reviewed_at": _time.time(),
                    "model": "user",
                }),
                encoding="utf-8",
            )

            results.append({
                "filename": filename,
                "status": "ok",
                "byte_count": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "file_type": ingest_result.file_type.value,
                "routed_to": ingest_result.routed_to,
                "signal_emitted": ingest_result.signal_emitted,
            })
        except OSError as e:
            logger.warning("Failed to ingest %s: %s", filename, e)
            results.append({
                "filename": filename, "status": "error",
                "detail": str(e),
            })

    return {"files": results, "count": len(results)}


# -- Workspace -------------------------------------------------------------


@app.get("/v1/universes/{uid}/workspace")
def list_workspace(uid: str, _user: str = Depends(_require_auth)) -> dict[str, Any]:
    """List all files in the workspace/ directory."""
    udir = _validate_universe_id(uid)
    ws_dir = udir / "workspace"
    if not ws_dir.exists():
        return {"files": []}
    files: list[dict[str, Any]] = []
    try:
        for entry in sorted(ws_dir.iterdir()):
            if entry.is_file() and not entry.name.startswith("."):
                files.append({
                    "filename": entry.name,
                    "size": entry.stat().st_size,
                })
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to list workspace files: {e}")
    return {"files": files}


@app.get("/v1/universes/{uid}/workspace/{filename}")
def get_workspace_file(
    uid: str, filename: str, _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Read a specific workspace file with integrity checksums."""
    import hashlib

    udir = _validate_universe_id(uid)
    ws_dir = udir / "workspace"
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    target = ws_dir / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Workspace file not found: {filename}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    try:
        raw = target.read_bytes()
        content = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to read workspace file: {e}")
    return {
        "filename": safe_name,
        "content": content,
        "byte_count": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


@app.post("/v1/universes/{uid}/workspace", status_code=201)
def post_workspace(
    uid: str, body: WorkspaceBody, _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Create or overwrite a file in the workspace/ directory."""
    import hashlib

    udir = _validate_universe_id(uid)
    safe_name = Path(body.filename).name
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    ws_dir = udir / "workspace"
    try:
        ws_dir.mkdir(parents=True, exist_ok=True)
        raw = body.content.encode("utf-8")
        (ws_dir / safe_name).write_bytes(raw)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write workspace file: {e}")
    return {
        "status": "ok",
        "filename": safe_name,
        "byte_count": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


@app.delete("/v1/universes/{uid}/workspace/{filename}")
def delete_workspace_file(
    uid: str, filename: str, _user: str = Depends(_require_auth),
) -> dict[str, str]:
    """Delete a file from the workspace/ directory."""
    udir = _validate_universe_id(uid)
    ws_dir = udir / "workspace"
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    target = ws_dir / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Workspace file not found: {filename}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    try:
        target.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete workspace file: {e}")
    return {"status": "ok", "filename": safe_name}


# -- Multiplayer Author Server -----------------------------------------------


@app.post("/v1/sessions", status_code=201)
def create_session(
    body: CreateSessionBody,
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """Create a user session. Returns a Bearer token for subsequent requests."""
    from tinyassets import daemon_server as author_server

    try:
        base = _base()
        result = author_server.create_session(
            base,
            username=body.username,
        )
        return {
            "token": result["token"],
            "username": result["account"]["username"],
            "user_id": result["account"]["user_id"],
            "created_at": result["created_at"],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create session: {str(e)}")


@app.get("/v1/me")
def get_current_user(
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """Get info about the current authenticated user."""
    return {
        "user_id": actor.get("user_id"),
        "username": actor.get("username"),
        "display_name": actor.get("display_name"),
        "capabilities": actor.get("capabilities", []),
    }


@app.get("/v1/authors")
def list_authors(
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """List all registered authors."""
    from tinyassets import daemon_server as author_server

    try:
        base = _base()
        author_server.ensure_default_author(base)
        authors = author_server.list_authors(base)
        return {
            "authors": authors,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list authors: {str(e)}")


@app.get("/v1/authors/{author_id}")
def get_author(
    author_id: str,
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """Get a specific author by ID."""
    from tinyassets import daemon_server as author_server

    try:
        base = _base()
        author = author_server.get_author(base, author_id=author_id)
        if author is None:
            raise HTTPException(status_code=404, detail="Author not found")
        return {"author": author}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get author: {str(e)}")


@app.post("/v1/authors/{parent_author_id}/fork-proposals", status_code=201)
def propose_author_fork(
    parent_author_id: str,
    body: ProposeForkBody,
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """Propose a fork of an existing author."""
    from tinyassets import daemon_server as author_server

    try:
        base = _base()

        # Create the vote window for the fork
        vote_result = author_server.propose_author_fork(
            base,
            universe_id=body.universe_id,
            author_id=parent_author_id,
            display_name=body.display_name,
            soul_text=body.soul_text,
            proposed_by=actor["user_id"],
            duration_seconds=body.vote_seconds,
            reason=body.reason,
        )

        return {
            "vote": vote_result,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to propose fork: {str(e)}")


@app.post("/v1/votes/{vote_id}/ballots", status_code=201)
def cast_vote(
    vote_id: str,
    body: CastVoteBody,
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """Cast a vote in a vote window."""
    from tinyassets import daemon_server as author_server

    try:
        base = _base()
        result = author_server.cast_vote(
            base,
            vote_id=vote_id,
            user_id=actor["user_id"],
            choice=body.choice,
        )
        return {"vote": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to cast vote: {str(e)}")


@app.post("/v1/votes/{vote_id}/resolve", status_code=200)
def resolve_vote(
    vote_id: str,
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """Resolve a vote window (host-only)."""
    from tinyassets import daemon_server as author_server

    if not author_server.actor_has_capability(actor, author_server.CAP_RESOLVE_VOTE):
        raise HTTPException(
            status_code=403,
            detail="Missing capability: resolve_vote",
        )

    try:
        base = _base()
        result = author_server.resolve_vote_if_due(base, vote_id=vote_id, force=True)
        if result is None:
            raise HTTPException(status_code=404, detail="Vote not found or not ready")
        return {"vote": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to resolve vote: {str(e)}")


@app.post("/v1/universes/{universe_id}/branches", status_code=201)
def create_branch(
    universe_id: str,
    body: CreateBranchBody,
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """Create a new branch in a universe."""
    from tinyassets import daemon_server as author_server

    try:
        base = _base()
        branch = author_server.create_branch(
            base,
            universe_id=universe_id,
            name=body.name,
            created_by=actor["user_id"],
            parent_branch_id=body.parent_branch_id,
        )
        author_server.record_action(
            base,
            universe_id=universe_id,
            actor_type=_actor_type(actor),
            actor_id=actor["user_id"],
            action_type="create_branch",
            target_type="branch",
            target_id=branch["branch_id"],
            summary=f"Created branch {branch['name']}",
            payload={
                "branch_id": branch["branch_id"],
                "parent_branch_id": body.parent_branch_id,
            },
        )
        return {"branch": branch}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create branch: {str(e)}")


@app.get("/v1/universes/{universe_id}/branches")
def list_branches(
    universe_id: str,
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """List all branches in a universe."""
    from tinyassets import daemon_server as author_server

    try:
        base = _base()
        branches = author_server.list_universe_forks(
            base, universe_id=universe_id,
        )
        return {"branches": branches}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list branches: {str(e)}")


@app.post("/v1/universes/{universe_id}/requests", status_code=201)
def create_request(
    universe_id: str,
    body: CreateUserRequestBody,
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """Submit a user request (e.g., author preference, notes)."""
    from tinyassets import daemon_server as author_server

    try:
        base = _base()
        request_item = author_server.create_user_request(
            base,
            universe_id=universe_id,
            branch_id=body.branch_id,
            user_id=actor["user_id"],
            request_type=body.request_type,
            text=body.text,
            preferred_author_id=body.preferred_author_id,
        )
        author_server.record_action(
            base,
            universe_id=universe_id,
            actor_type=_actor_type(actor),
            actor_id=actor["user_id"],
            action_type="submit_request",
            target_type="user_request",
            target_id=request_item["request_id"],
            summary=f"Submitted {body.request_type} request",
            payload={
                "branch_id": body.branch_id,
                "preferred_author_id": body.preferred_author_id,
                "request_type": body.request_type,
            },
        )
        return {"request": request_item}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create request: {str(e)}")


@app.get("/v1/universes/{universe_id}/requests")
def list_requests(
    universe_id: str,
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """List all requests in a universe."""
    from tinyassets import daemon_server as author_server

    try:
        base = _base()
        requests = author_server.list_user_requests(base, universe_id=universe_id)
        return {"requests": requests}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list requests: {str(e)}")


@app.post("/v1/universes/{universe_id}/runtime", status_code=201)
def spawn_runtime(
    universe_id: str,
    body: SpawnRuntimeBody,
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """Spawn a runtime instance for an author in a universe."""
    from tinyassets import daemon_server as author_server

    if not author_server.actor_has_capability(
        actor,
        author_server.CAP_SPAWN_RUNTIME_CAPACITY,
    ):
        raise HTTPException(
            status_code=403,
            detail="Missing capability: spawn_runtime_capacity",
        )

    try:
        base = _base()
        runtime = author_server.spawn_runtime_instance(
            base,
            universe_id=universe_id,
            author_id=body.author_id,
            provider_name=body.provider_name,
            model_name=body.model_name,
            branch_id=body.branch_id,
            created_by=actor["user_id"],
        )
        author_server.record_action(
            base,
            universe_id=universe_id,
            actor_type=_actor_type(actor),
            actor_id=actor["user_id"],
            action_type="spawn_runtime_capacity",
            target_type="runtime_instance",
            target_id=runtime["instance_id"],
            summary=f"Spawned runtime {body.provider_name}:{body.model_name}",
            payload={
                "author_id": body.author_id,
                "branch_id": body.branch_id,
                "provider_name": body.provider_name,
                "model_name": body.model_name,
            },
        )
        return {"runtime": runtime}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to spawn runtime: {str(e)}")


@app.get("/v1/universes/{universe_id}/runtimes")
def list_runtimes(
    universe_id: str,
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """List runtime instances in a universe."""
    from tinyassets import daemon_server as author_server

    try:
        base = _base()
        runtimes = author_server.list_runtime_instances(base, universe_id=universe_id)
        return {"runtimes": runtimes}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list runtimes: {str(e)}")


@app.get("/v1/universes/{universe_id}/ledger")
def list_actions(
    universe_id: str,
    actor: dict[str, Any] = Depends(_require_bearer_token),
) -> dict[str, Any]:
    """List action records (ledger) for a universe."""
    from tinyassets import daemon_server as author_server

    try:
        base = _base()
        actions = author_server.list_actions(base, universe_id=universe_id)
        return {"actions": actions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list actions: {str(e)}")


# -- Daemon control --------------------------------------------------------


@app.post("/v1/daemon/{action}")
def daemon_control(
    action: str,
    body: DaemonControlBody | None = None,
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Control the daemon: start, stop, or pause.

    Optionally accepts ``{"universe": "some-id"}`` in the request body.
    When ``universe`` is provided with the ``start`` action and differs
    from the currently active universe, the daemon stops, switches to
    the new universe, and starts writing there.
    """
    if action not in ("start", "stop", "pause"):
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    requested_universe = body.universe if body else None
    current_universe = _daemon_universe_id()

    if action == "pause":
        if _daemon is not None:
            try:
                _daemon._paused.set()
            except Exception:
                pass
            try:
                pause_path = Path(_daemon._universe_path) / ".pause"
                pause_path.write_text(
                    datetime.now(timezone.utc).isoformat(), encoding="utf-8",
                )
            except Exception:
                pass
        result: dict[str, Any] = {"status": "paused"}
        if current_universe:
            result["universe"] = current_universe
        return result

    if action == "start":
        # Universe switching: stop current, start new
        if requested_universe and requested_universe != current_universe:
            # Validate the target universe exists
            _validate_universe_id(requested_universe)
            _stop_current_daemon()
            _start_daemon_for(requested_universe)
            return {"status": "running", "universe": requested_universe}

        # Resume current daemon
        if _daemon is not None:
            try:
                _daemon._paused.clear()
            except Exception:
                pass
            try:
                pause_path = Path(_daemon._universe_path) / ".pause"
                if pause_path.exists():
                    pause_path.unlink()
            except Exception:
                pass
        elif requested_universe:
            # No daemon running, start fresh
            _validate_universe_id(requested_universe)
            _start_daemon_for(requested_universe)
            return {"status": "running", "universe": requested_universe}

        if _daemon is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No daemon running and no universe specified."
                    " Provide a universe ID to start writing."
                ),
            )
        result = {"status": "running"}
        active = _daemon_universe_id()
        if active:
            result["universe"] = active
        return result

    # action == "stop"
    _stop_current_daemon()
    return {"status": "stopping"}


# ---------------------------------------------------------------------------
# Webhook endpoint — Conway readiness and external integrations
# ---------------------------------------------------------------------------

# Registered webhook callbacks: list of (url, events) tuples.
# In production this would be persisted; for now it lives in memory.
_webhook_registrations: list[dict[str, Any]] = []


class WebhookRegistration(BaseModel):
    """Register a URL to receive daemon events via POST."""

    url: str = Field(..., description="The URL to POST events to")
    events: list[str] = Field(
        default_factory=lambda: ["*"],
        description=(
            "Event types to subscribe to. Use '*' for all. "
            "Available: scene_completed, chapter_completed, "
            "review_gate_triggered, daemon_paused, daemon_resumed, "
            "direction_received, canon_added"
        ),
    )
    secret: str = Field(
        default="",
        description="Optional shared secret for HMAC signature verification",
    )


VALID_WEBHOOK_EVENTS = {
    "scene_completed",
    "chapter_completed",
    "review_gate_triggered",
    "daemon_paused",
    "daemon_resumed",
    "direction_received",
    "canon_added",
}


@app.post(
    "/webhook/register",
    summary="Register a webhook",
    tags=["webhooks"],
)
async def register_webhook(
    reg: WebhookRegistration,
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Register a URL to receive real-time daemon events.

    The server will POST a JSON payload to the registered URL whenever
    a matching event occurs. Payload includes event type, timestamp,
    universe ID, and event-specific data.

    This endpoint supports Conway integration and external dashboards.
    """
    # Validate events
    if reg.events != ["*"]:
        invalid = [e for e in reg.events if e not in VALID_WEBHOOK_EVENTS]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid event types: {invalid}. "
                f"Valid: {sorted(VALID_WEBHOOK_EVENTS)}",
            )

    webhook_id = f"wh_{secrets.token_hex(8)}"
    entry = {
        "id": webhook_id,
        "url": reg.url,
        "events": reg.events,
        "secret": reg.secret,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    _webhook_registrations.append(entry)

    return {
        "webhook_id": webhook_id,
        "status": "registered",
        "events": reg.events,
    }


@app.get(
    "/webhook/registrations",
    summary="List webhook registrations",
    tags=["webhooks"],
)
async def list_webhooks(
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """List all registered webhook URLs and their subscribed events."""
    # Hide secrets in response
    safe = [
        {k: v for k, v in w.items() if k != "secret"}
        for w in _webhook_registrations
    ]
    return {"webhooks": safe, "count": len(safe)}


@app.delete(
    "/webhook/{webhook_id}",
    summary="Remove a webhook registration",
    tags=["webhooks"],
)
async def remove_webhook(
    webhook_id: str,
    _user: str = Depends(_require_auth),
) -> dict[str, Any]:
    """Remove a previously registered webhook."""
    global _webhook_registrations
    before = len(_webhook_registrations)
    _webhook_registrations = [
        w for w in _webhook_registrations if w.get("id") != webhook_id
    ]
    if len(_webhook_registrations) == before:
        raise HTTPException(status_code=404, detail="Webhook not found.")
    return {"webhook_id": webhook_id, "status": "removed"}


@app.post(
    "/webhook/conway",
    summary="Conway event receiver",
    tags=["webhooks"],
)
async def conway_webhook(request: Request) -> dict[str, Any]:
    """Receive events from Conway or other external platforms.

    This endpoint accepts incoming event notifications and routes them
    to the appropriate daemon or universe handler. Format:

    ```json
    {
        "event": "trigger",
        "source": "conway",
        "data": { ... }
    }
    ```
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    event_type = body.get("event", "unknown")
    source = body.get("source", "unknown")
    _data = body.get("data", {})  # noqa: F841 — reserved for future webhook dispatch

    logger.info(
        "Webhook received: event=%s source=%s", event_type, source,
    )

    return {
        "status": "received",
        "event": event_type,
        "source": source,
        "note": "Event acknowledged. Processing is asynchronous.",
    }
