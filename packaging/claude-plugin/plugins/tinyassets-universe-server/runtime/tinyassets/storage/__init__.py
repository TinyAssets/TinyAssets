"""Bounded-context storage layers for the multiplayer engine.

Second canonical Module Layout subpackage (after ``tinyassets/bid/``) per
``docs/architecture.md``. Replaces the 3,575-LOC ``tinyassets/daemon_server.py``
god-module with per-context submodules:

- ``accounts`` — user accounts + auth + sessions + capabilities
- ``universes_branches`` — universes + branches + snapshots + ACLs
- ``daemons`` — daemon (author) definitions + forks + runtime instances
- ``requests_votes`` — user requests + vote windows + ballots + action records
- ``notes_work_targets`` — universe notes + work-targets + hard priorities
- ``goals_gates`` — goals + gate-claims + leaderboard reads

This ``__init__.py`` hosts the shared primitives every context module
needs: path helpers, the ``_connect()`` factory, and the constants +
JSON + slug utilities that were previously at the top of
``daemon_server.py``.

R7 ship sequence (see
``docs/exec-plans/active/2026-04-19-storage-package-split.md``):

- Commit 1 (this commit): shared helpers + constants. ``daemon_server.py``
  imports the helpers back from here rather than duplicating them.
- Commits 2-6: per-bounded-context split.

Per the foundation-end-state rule (README § Direction: foundational
patches are brought forward): each commit is itself end-state-shaped —
the helpers move to their final path in commit 1, not to a temporary
intermediate file.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyassets.ttl_memo import TTLMemo as _TTLMemo
from tinyassets.ttl_memo import read_ttl as _read_ttl

# -------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------

DB_FILENAME = ".tinyassets.db"
# Superseded on-disk names, OLDEST generation first. `.author_server.db` was
# renamed to `.workflow.db` by 20047d1d (2026-05-01), which the TinyAssets hard
# rename (89edf995, 2026-06-26) then superseded with DB_FILENAME. A universe
# last booted inside that window still carries `.workflow.db` on disk, so every
# generation has to stay in the chain until the fleet is known-migrated.
_LEGACY_DB_FILENAMES = (".author_server.db", ".workflow.db")
_SQLITE_SIBLING_SUFFIXES = ("-wal", "-shm")
DEFAULT_BRANCH_MODE = "no_fixed_mainline"
DEFAULT_QUICK_VOTE_SECONDS = 300
SESSION_PREFIX = "fa_session_"

CAP_READ_PUBLIC_UNIVERSE = "read_public_universe"
CAP_SUBMIT_REQUEST = "submit_request"
CAP_FORK_BRANCH = "fork_branch"
CAP_PROPOSE_AUTHOR_FORK = "propose_author_fork"
CAP_SPAWN_RUNTIME_CAPACITY = "spawn_runtime_capacity"
CAP_ASSIGN_RUNTIME_PROVIDER = "assign_runtime_provider"
CAP_PAUSE_RESUME_SERVER = "pause_resume_server"
CAP_ROLLBACK_BRANCH = "rollback_branch"
CAP_PROMOTE_BRANCH = "promote_branch"
CAP_SUPERSEDE_BRANCH = "supersede_branch"
CAP_EDIT_UNIVERSE_RULES = "edit_universe_rules"
CAP_GRANT_CAPABILITIES = "grant_capabilities"
CAP_RESOLVE_VOTE = "resolve_vote"
CAP_SET_GOAL_SELECTOR = "set_goal_selector"
CAP_SET_CANONICAL_BRANCH = "set_canonical_branch"
CAP_DEFINE_GATE_LADDER = "define_gate_ladder"
CAP_RETRACT_GATE_CLAIM = "retract_gate_claim"

ALL_CAPABILITIES: tuple[str, ...] = (
    CAP_READ_PUBLIC_UNIVERSE,
    CAP_SUBMIT_REQUEST,
    CAP_FORK_BRANCH,
    CAP_PROPOSE_AUTHOR_FORK,
    CAP_SPAWN_RUNTIME_CAPACITY,
    CAP_ASSIGN_RUNTIME_PROVIDER,
    CAP_PAUSE_RESUME_SERVER,
    CAP_ROLLBACK_BRANCH,
    CAP_PROMOTE_BRANCH,
    CAP_SUPERSEDE_BRANCH,
    CAP_EDIT_UNIVERSE_RULES,
    CAP_GRANT_CAPABILITIES,
    CAP_RESOLVE_VOTE,
    CAP_SET_GOAL_SELECTOR,
    CAP_SET_CANONICAL_BRANCH,
    CAP_DEFINE_GATE_LADDER,
    CAP_RETRACT_GATE_CLAIM,
)

DEFAULT_USER_CAPABILITIES: tuple[str, ...] = (
    CAP_READ_PUBLIC_UNIVERSE,
    CAP_SUBMIT_REQUEST,
    CAP_FORK_BRANCH,
    CAP_PROPOSE_AUTHOR_FORK,
)


# -------------------------------------------------------------------
# JSON + slug helpers
# -------------------------------------------------------------------


def _now() -> float:
    return time.time()


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _json_loads(payload: str | None, default: Any) -> Any:
    if not payload:
        return default
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return default


def _slugify(text: str, fallback: str = "item") -> str:
    cleaned = [
        ch.lower() if ch.isalnum() else "-"
        for ch in text.strip()
    ]
    slug = "".join(cleaned).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or fallback


# -------------------------------------------------------------------
# Path resolution
# -------------------------------------------------------------------


def _looks_like_windows_path(raw: str) -> bool:
    """Return True if ``raw`` looks like a Windows drive-letter path.

    Matches ``C:\\...``, ``c:/...``, ``D:\\...`` etc. Used to detect
    cross-OS env-var leakage: a host machine setting
    ``TINYASSETS_WIKI_PATH=C:\\Users\\Jonathan\\...`` that then reaches a
    Linux container joins against CWD on POSIX (``Path("C:\\Users\\...")``
    is NOT absolute on POSIX) and yields nonsense like
    ``/app/C:\\Users\\Jonathan\\Projects\\Wiki``.
    """
    if len(raw) < 3:
        return False
    if not raw[0].isalpha() or raw[1] != ":":
        return False
    return raw[2] in ("\\", "/")


def _reject_windows_path_on_posix(raw: str, var_name: str) -> None:
    """Raise with a specific error if ``raw`` is a Windows path on POSIX.

    Per AGENTS.md hard rule #8 (fail loudly, never silently): a silent
    fallback would hide the deploy misconfig that originally leaked a
    Windows path into the container. The error names the env var and
    the current runtime so the fix is obvious from the traceback.
    """
    import os
    if os.name == "nt":
        return
    if _looks_like_windows_path(raw):
        raise ValueError(
            f"{var_name}={raw!r} looks like a Windows drive-letter path "
            f"but the runtime is POSIX ({os.name!r}). Refusing: joining "
            f"this against the current working directory would produce "
            f"nonsense like '/app/{raw}'. Unset the variable to use the "
            f"platform default, or set it to a POSIX absolute path "
            f"(e.g. '/data/wiki')."
        )


def data_dir() -> Path:
    """Return the on-disk root for all TinyAssets daemon state.

    Canonical env var: ``TINYASSETS_DATA_DIR``.

    Resolution order (first match wins):
      1. ``$TINYASSETS_DATA_DIR`` if set and non-empty.
      2. Platform default:
         - Windows: ``%APPDATA%\\TinyAssets`` if ``APPDATA`` is set, else
           ``Path.home() / 'AppData' / 'Roaming' / 'TinyAssets'``.
         - macOS / Linux / container: ``~/.tinyassets``.

    Always returns an absolute, resolved Path. Callers should NOT re-resolve
    or re-expand; this function is the single source of truth for the
    daemon's on-disk root so that a containerized deploy setting
    ``TINYASSETS_DATA_DIR=/data`` gets all writes inside the bind-mount.

    The previous shape defaulted to CWD-relative ``"output"`` and produced
    the 2026-04-19 container CWD-drift bug: running the daemon from ``/app``
    wrote to ``/app/output`` instead of ``/data``. This function eliminates
    that class by refusing to return CWD-relative paths.

    Notes
    -----
    - This is the *root* for all on-disk state, not the universe dir.
      Per-universe directories sit under this root. The previous
      root setting conflated the two; the contract is that
      ``TINYASSETS_DATA_DIR`` is the root (e.g., ``/data``) and universes are
      subdirectories (e.g., ``/data/my-universe``).
    - The directory is not created here. Callers that write into it
      are responsible for ``mkdir(parents=True, exist_ok=True)``.
    """
    import os
    explicit = os.environ.get("TINYASSETS_DATA_DIR", "").strip()
    if explicit:
        _reject_windows_path_on_posix(explicit, "TINYASSETS_DATA_DIR")
        return Path(explicit).expanduser().resolve()

    # Platform default.
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata and os.name == "nt":
        return (Path(appdata) / "TinyAssets").resolve()
    if os.name == "nt":
        # Windows without APPDATA (unusual) — fall back to the standard
        # user path rather than ~/.tinyassets.
        return (Path.home() / "AppData" / "Roaming" / "TinyAssets").resolve()
    return (Path.home() / ".tinyassets").resolve()


def active_universe_id(base: Path | None = None) -> str:
    """Return the dynamic active universe marker when it points at a real universe.

    ``UNIVERSE_SERVER_DEFAULT_UNIVERSE`` is a boot/default setting. The
    runtime ``switch_universe`` MCP action writes ``.active_universe`` under
    the data root, so read that marker before falling back to static defaults.
    Invalid marker contents are ignored instead of becoming path traversal.
    """
    root = base or data_dir()
    marker = root / ".active_universe"
    try:
        uid = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not uid or "/" in uid or "\\" in uid or uid.startswith("."):
        return ""
    if not (root / uid).is_dir():
        return ""
    return uid


def wiki_path() -> Path:
    """Return the on-disk root for the knowledge wiki.

    Canonical env var: ``TINYASSETS_WIKI_PATH``.

    Resolution order (first match wins):
      1. ``$TINYASSETS_WIKI_PATH`` if set and non-empty.
      2. Platform default: ``data_dir() / "wiki"`` — inherits the
         canonical data root's platform handling (Windows
         ``%APPDATA%\\TinyAssets\\wiki``; Linux/macOS ``~/.tinyassets/wiki``).

    Pre-2026-04-20 the wiki fallback was hardcoded
    ``r"C:\\Users\\Jonathan\\Projects\\Wiki"`` in
    ``tinyassets/universe_server.py`` — broke every non-host deploy +
    leaked the developer's username into docs. Using this resolver
    closes that class the same way ``data_dir`` did for universe state.

    If a Windows-style path leaks into a POSIX runtime, this resolver raises
    ``ValueError`` rather than silently returning a nonsense path.

    Returns an absolute, resolved Path. Does not create the directory;
    callers mkdir on first write.
    """
    import os
    explicit = os.environ.get("TINYASSETS_WIKI_PATH", "").strip()
    if explicit:
        _reject_windows_path_on_posix(explicit, "TINYASSETS_WIKI_PATH")
        return Path(explicit).expanduser().resolve()

    # Platform default — inherit data_dir's platform handling.
    return (data_dir() / "wiki").resolve()


def _sqlite_db_siblings(db_file: Path) -> tuple[Path, ...]:
    return tuple(
        db_file.with_name(f"{db_file.name}{suffix}")
        for suffix in _SQLITE_SIBLING_SUFFIXES
    )


def _replace_if_exists(source: Path, target: Path) -> bool:
    if not source.exists():
        return False
    os.replace(source, target)
    return True


def _legacy_backup_path(legacy_db_path: Path) -> Path:
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    base = legacy_db_path.with_name(f"{legacy_db_path.name}.legacy-{timestamp}")
    candidate = base
    counter = 1
    while candidate.exists() or any(
        sibling.exists() for sibling in _sqlite_db_siblings(candidate)
    ):
        candidate = legacy_db_path.with_name(
            f"{base.name}-{counter}"
        )
        counter += 1
    return candidate


def _move_db_with_sqlite_siblings(
    source: Path,
    target: Path,
    *,
    primary_last: bool,
) -> list[str]:
    """Move a SQLite DB and its ``-wal``/``-shm`` sidecars to ``target``.

    ``primary_last`` selects which half of the set survives an interruption,
    and the two call sites need opposite answers:

    ``primary_last=True`` (promotion onto ``DB_FILENAME``). The canonical
    primary's existence is the marker that suppresses re-migration, so it must
    arrive last. The reverse order silently loses data: it can leave a
    canonical primary beside a stranded legacy WAL, and the next boot sees a
    canonical file, skips migration, and opens a database whose
    committed-but-uncheckpointed pages live in a WAL it will never read.

    ``primary_last=False`` (backup of a superseded generation). The backup
    destination is timestamped, so a resumed backup can never rejoin a
    half-moved destination. Moving sidecars first makes the collision counter
    in `_legacy_backup_path` treat the interrupted attempt's own sidecars as a
    conflict and bump the primary to ``<name>-1``, permanently divorcing it
    from the ``-wal`` holding its committed pages while still logging a
    successful backup. Moving the primary first instead leaves any unmoved
    sidecar at its original legacy name, where
    `_preserve_orphaned_legacy_sidecars` finds it and preserves it loudly.
    """
    moved = []
    siblings = list(zip(
        _sqlite_db_siblings(source), _sqlite_db_siblings(target),
        strict=True,
    ))

    def move_primary() -> None:
        if _replace_if_exists(source, target):
            moved.append(source.name)

    def move_siblings() -> None:
        for source_sibling, target_sibling in siblings:
            if _replace_if_exists(source_sibling, target_sibling):
                moved.append(source_sibling.name)

    if primary_last:
        move_siblings()
        move_primary()
    else:
        move_primary()
        move_siblings()
    return moved


def _incomplete_backup_primary(
    base: Path,
    legacy_name: str,
    suffix: str,
) -> Path | None:
    """Return the newest backup of ``legacy_name`` still missing ``suffix``.

    Identifies an interrupted backup by name alone, so no durable migration
    state is needed. Returns None when every backup already has that sidecar
    (nothing to reunite) or when none exist (the orphan is unexplained, and
    the caller must fail closed rather than guess).
    """
    candidates = sorted(
        (
            p for p in base.iterdir()
            if p.name.startswith(f"{legacy_name}.legacy-")
            and not p.name.endswith(_SQLITE_SIBLING_SUFFIXES)
            and not (base / f"{p.name}{suffix}").exists()
        ),
        key=lambda p: p.name,
    )
    return candidates[-1] if candidates else None


def _reject_orphaned_legacy_sidecars(base: Path) -> None:
    """Fail closed when a legacy ``-wal``/``-shm`` outlives its primary DB.

    Only reachable for universes interrupted by the pre-fix migrator, which
    renamed the primary before its sidecars. A WAL holds committed
    transactions that are not yet in the database file, and SQLite validates a
    WAL against the exact database that wrote it, so the orphan can neither be
    safely re-attached nor assumed redundant.

    Per AGENTS.md hard rule #8 the only honest response is to refuse: opening
    the canonical database here would silently serve state that is missing
    committed transactions. The orphan is deliberately left untouched so the
    refusal is sticky -- archiving it first would make the next boot succeed
    silently, which is the exact failure this guards against.
    """
    for name in _LEGACY_DB_FILENAMES:
        legacy_db = base / name
        if legacy_db.exists():
            continue
        for sidecar in _sqlite_db_siblings(legacy_db):
            if not sidecar.exists():
                continue

            # An interrupted *backup* of this generation leaves the sidecar
            # here while its primary already sits in a timestamped backup.
            # Canonical is unaffected in that case, so reunite the set rather
            # than refusing: the backup primary is this sidecar's own primary.
            suffix = sidecar.name[len(name):]
            adopter = _incomplete_backup_primary(base, name, suffix)
            if adopter is not None:
                os.replace(sidecar, adopter.with_name(adopter.name + suffix))
                _logger.warning(
                    "Reunited orphaned SQLite sidecar %s in %s with its "
                    "interrupted backup %s; a previous migration was cut "
                    "short mid-backup.",
                    sidecar.name,
                    base,
                    adopter.name,
                )
                continue

            raise RuntimeError(
                f"Refusing to open {base / DB_FILENAME}: found orphaned "
                f"legacy SQLite sidecar {sidecar} with no {name} primary. A "
                f"previous filename migration was interrupted, and that WAL "
                f"may hold committed transactions absent from "
                f"{DB_FILENAME}. Continuing would silently serve incomplete "
                f"state. Recover by restoring the matching {name} primary "
                f"beside this sidecar and restarting so the migration can "
                f"complete, or, once you have confirmed the sidecar is "
                f"redundant, move it out of {base} to clear this error."
            )


def _migrate_legacy_db_filename(base_path: str | Path) -> None:
    """Promote the newest surviving on-disk generation onto ``DB_FILENAME``.

    Option A per ``docs/design-notes/2026-04-27-author-server-db-filename-migration.md``:
    a one-shot forward rename, never a dual-read fallback. Superseded
    generations are backed up rather than deleted, so a wrong guess about
    which file is authoritative stays recoverable by the host.
    """
    base = Path(base_path)
    canonical_db = base / DB_FILENAME

    # Newest generation first: it is the best candidate to promote, and any
    # older name beside it is superseded history rather than live data.
    present = [
        base / name
        for name in reversed(_LEGACY_DB_FILENAMES)
        if (base / name).exists()
    ]
    if present:
        if canonical_db.exists():
            promote, superseded = None, present
        else:
            promote, superseded = present[0], present[1:]

        if promote is not None:
            moved = _move_db_with_sqlite_siblings(
                promote, canonical_db, primary_last=True,
            )
            if moved:
                _logger.info(
                    "Migrated legacy SQLite filename in %s from %s to %s (%s)",
                    base,
                    promote.name,
                    canonical_db.name,
                    ", ".join(moved),
                )

        for legacy_db in superseded:
            backup = _legacy_backup_path(legacy_db)
            moved = _move_db_with_sqlite_siblings(
                legacy_db, backup, primary_last=False,
            )
            _logger.warning(
                "Superseded SQLite generation %s existed in %s alongside %s; "
                "using %s and backed up legacy SQLite files to %s (%s)",
                legacy_db.name,
                base,
                canonical_db.name,
                canonical_db.name,
                backup.name,
                ", ".join(moved) if moved else "no files moved",
            )

    # Runs on every path: a legacy sidecar left without its primary is an
    # orphan from an interrupted pre-fix migration, including the mixed case
    # where one generation still has a primary and another does not.
    _reject_orphaned_legacy_sidecars(base)


def db_path(base_path: str | Path) -> Path:
    _migrate_legacy_db_filename(base_path)
    return Path(base_path) / DB_FILENAME


# -------------------------------------------------------------------
# Bootstrap env-readability probe (closes 2026-04-22 Concern)
# -------------------------------------------------------------------

_TINYASSETS_ENV_PATH = Path("/etc/tinyassets/env")

_logger = __import__("logging").getLogger(__name__)


def probe_env_readability(
    env_path: Path = _TINYASSETS_ENV_PATH,
) -> bool:
    """Check that the operator env file is readable by the current process.

    Returns True when the file is readable (or absent — absent is fine,
    the env file is only provisioned in cloud/container deploys). Returns
    False when the file exists but cannot be read, and emits a WARNING
    log with the observed mode bits and the fix command so the operator
    can recover without hunting through docs.

    This is a non-crashing probe — degraded operation with a visible
    warning is preferable to a dead daemon. Callers should invoke this
    once at startup so the warning appears in the initial log burst where
    operators are most likely to see it.
    """
    if not env_path.exists():
        return True

    try:
        env_path.open("r").close()
        return True
    except PermissionError:
        try:
            import stat as _stat
            mode = env_path.stat().st_mode
            mode_str = _stat.filemode(mode)
        except OSError:
            mode_str = "(unknown)"
        _logger.warning(
            "Bootstrap env file %s exists but is NOT readable by the current "
            "process (mode=%s). Daemon will start in degraded mode — secrets "
            "from env file are unavailable. Fix: chmod 644 %s",
            env_path,
            mode_str,
            env_path,
        )
        return False
    except OSError as exc:
        _logger.warning(
            "Bootstrap env file %s could not be opened: %s. "
            "Daemon will start in degraded mode.",
            env_path,
            exc,
        )
        return False


# -------------------------------------------------------------------
# Storage utilization observability (BUG-023 Phase 1)
# -------------------------------------------------------------------


# Per-subsystem paths, relative to data_dir(). Each path may be either
# a file (size from stat) or a directory (recursive walk). Missing paths
# resolve to 0 bytes rather than error — observability must never break
# the probe surface it rides on.
_SUBSYSTEM_PATHS: tuple[tuple[str, str, bool], ...] = (
    # (name, relative path, is_directory)
    # NOTE: `run_transcripts` is NOT in this table — run state/records are
    # per-universe SQLite stores (<universe>/.langgraph_runs.db + .runs.db),
    # not a `runs/` dir, so it is measured separately below. See
    # inspect_storage_utilization().
    ("knowledge_db",   "knowledge.db", False),
    ("story_db",       "story.db", False),
    ("lance_indexes",  "lance", True),
    ("checkpoint_db",  "checkpoints.db", False),
    ("wiki",           "wiki", True),
    ("activity_log",   "activity.log", False),
    ("universe_outputs", "output", True),
)

_PRESSURE_WARN_THRESHOLD = 0.80
_PRESSURE_CRITICAL_THRESHOLD = 0.95


def path_size_bytes(path: Path) -> int:
    """Return the on-disk size of ``path`` in bytes.

    - Missing paths → 0 (not an error; a subsystem may be uninitialized).
    - Files → ``stat().st_size``.
    - Directories → recursive sum of regular-file sizes; OSError on a
      single child does not abort the walk.
    """
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    if not path.is_dir():
        return 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _pressure_level_from_percent(percent: float) -> str:
    """Classify ``percent`` (0.0-1.0) into an alert tier."""
    if percent >= _PRESSURE_CRITICAL_THRESHOLD:
        return "critical"
    if percent >= _PRESSURE_WARN_THRESHOLD:
        return "warn"
    return "ok"


#: How long a storage snapshot may be reused. Measured 2026-08-28 on the live box:
#: this walk costs ~24-49 ms and issues ~3,300 `stat` syscalls per call, because it
#: recursively sums every subsystem directory. That was 19% of a `read_graph
#: target=status` request -- tolerable at two users, and the wrong SHAPE at a
#: thousand, because the cost is O(files on disk) while the rest of the request is
#: flat. A disk does not fill in a minute, so a minute-old number is the same
#: answer for every consumer that exists (the uptime canary pages on
#: `pressure_level`, which moves on the scale of hours).
_STORAGE_SNAPSHOT_TTL_VAR = "TINYASSETS_STORAGE_SNAPSHOT_TTL_S"
_DEFAULT_STORAGE_SNAPSHOT_TTL_S = 60.0

_storage_snapshot_memo = _TTLMemo()


def reset_storage_snapshot_cache() -> None:
    """Drop the memo, and disown any walk already in flight so it cannot republish."""
    _storage_snapshot_memo.invalidate()


def inspect_storage_utilization() -> dict[str, Any]:
    """Cached front for :func:`_inspect_storage_utilization_uncached`.

    Same contract and shape; a snapshot up to `TINYASSETS_STORAGE_SNAPSHOT_TTL_S`
    seconds old may be returned, and `0` restores the uncached behaviour. Keyed by
    data dir so a test tmpdir never inherits production's figures.

    The walk is expensive (~3,300 `stat` syscalls) and its cost is O(files on disk),
    so it must not run per request. :class:`TTLMemo` supplies single-flight, so 25
    concurrent cold callers run ONE walk rather than 25 — which matters because the
    box saturates at about that concurrency, and a cache that stampedes there makes
    the worst moment worse (Codex ADAPT 2026-08-28).
    """
    ttl = max(0.0, _read_ttl(_STORAGE_SNAPSHOT_TTL_VAR, _DEFAULT_STORAGE_SNAPSHOT_TTL_S))
    snapshot = _storage_snapshot_memo.get(
        str(data_dir()),
        _inspect_storage_utilization_uncached,
        ttl=ttl,
    )
    # The memo deep-copies its value: strip process-local timing before disclosure.
    started = snapshot.pop("_observed_monotonic")
    snapshot["observation_age_seconds"] = round(max(0.0, time.monotonic() - started), 3)
    snapshot["cache_ttl_seconds"] = ttl
    return snapshot


def _inspect_storage_utilization_uncached() -> dict[str, Any]:
    """Return a snapshot of daemon storage state.

    Phase-1 surface for BUG-023: gives an MCP-reachable operator a way
    to see per-subsystem byte counts + root-volume pressure before the
    wall is hit. Pairs with ``get_status.storage_utilization`` so the
    uptime canary can page on ``pressure_level`` in {warn, critical}.

    Shape (stable contract — consumed by get_status + tests):
        {
          volume_percent: float,  # 0.0-1.0, root volume usage
          volume_bytes_total: int,
          volume_bytes_free: int,
          per_subsystem: {
            <name>: {bytes: int, path: str},
            ...
          },
          growth_estimate: {
            bytes_per_day_recent: int,
            days_until_full_at_recent_rate: float | null
          } | null,
          pressure_level: 'ok' | 'warn' | 'critical'
        }

    Invariants:
      - Read-only; no writes.
      - Missing subsystem paths yield ``bytes=0``, never raise.
      - Windows-path-on-POSIX guard inherited from ``data_dir()``.
    """
    import shutil as _shutil

    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    observed_monotonic = time.monotonic()
    root = data_dir()

    volume_availability = "available"
    try:
        usage = _shutil.disk_usage(str(root if root.exists() else root.parent))
        volume_bytes_total = int(usage.total)
        volume_bytes_free = int(usage.free)
        volume_percent = (
            0.0 if volume_bytes_total == 0
            else 1.0 - (volume_bytes_free / volume_bytes_total)
        )
        if volume_bytes_total == 0:
            volume_availability = "unavailable"
    except OSError:
        volume_availability = "unavailable"
        volume_bytes_total = 0
        volume_bytes_free = 0
        volume_percent = 0.0

    per_subsystem: dict[str, dict[str, Any]] = {}
    for name, rel_path, _is_dir in _SUBSYSTEM_PATHS:
        abs_path = root / rel_path
        per_subsystem[name] = {
            "bytes": path_size_bytes(abs_path),
            "path": str(abs_path),
        }

    # run_transcripts: run state + records live in per-universe SQLite stores
    # (<universe>/.langgraph_runs.db = run state/messages, + .runs.db = metadata),
    # NOT a `runs/` directory. The old `("run_transcripts", "runs", True)` entry
    # therefore always reported 0, which once read as "run transcripts aren't
    # persisting" (2026-06-25 false alarm; ~3 GB of run data was persisting fine).
    # Measure the real stores: root-level (single-/active-universe layout) + one
    # level of per-universe dirs, INCLUDING the SQLite WAL/SHM sidecars — live
    # WAL-mode DBs hold uncheckpointed bytes in <db>-wal / <db>-shm.
    #
    # Caps note: caps.py maps run_transcripts -> `run_artifacts`
    # (TINYASSETS_CAP_RUN_ARTIFACTS_BYTES). That cap is OFF by default and only logs
    # (never deletes). It historically tracked the rotatable `runs/` dir
    # (rotation.py); this metric now reflects the non-rotated SQLite stores, so if
    # that cap is ever enabled it bounds the DBs, not a rotatable dir — size it
    # accordingly (or leave unset).
    run_store_bytes = 0
    for _db in (".langgraph_runs.db", ".runs.db"):
        for _suffix in ("", "-wal", "-shm"):
            run_store_bytes += path_size_bytes(root / f"{_db}{_suffix}")
            for _child in root.glob(f"*/{_db}{_suffix}"):
                run_store_bytes += path_size_bytes(_child)
    per_subsystem["run_transcripts"] = {
        "bytes": run_store_bytes,
        "path": f"{root} (.langgraph_runs.db + .runs.db incl. WAL, root + per-universe)",
    }

    # Phase-3 subsystem cap snapshot — consumers (uptime canary, alert
    # rules) can see which caps are configured + where each subsystem
    # sits relative to its soft/hard thresholds. Inspect-level subsystem
    # names map to cap-level ones (caps owns its own vocabulary:
    # checkpoints / logs / run_artifacts; inspect uses file-path names).
    try:
        from tinyassets.storage.caps import subsystem_cap_snapshot
        cap_input = {
            "checkpoints": per_subsystem.get("checkpoint_db", {}).get("bytes", 0),
            "logs": per_subsystem.get("activity_log", {}).get("bytes", 0),
            "run_artifacts": per_subsystem.get("run_transcripts", {}).get("bytes", 0),
        }
        subsystem_caps = subsystem_cap_snapshot(cap_input)
    except Exception:  # noqa: BLE001 — observability must not break probe
        subsystem_caps = {}

    return {
        "observed_at": observed_at,
        "_observed_monotonic": observed_monotonic,
        "volume_availability": volume_availability,
        "volume_scope": "filesystem_containing_data_root",
        "volume_percent_formula": "1 - volume_bytes_free / volume_bytes_total",
        "subsystem_scope": "partial_enumerated_daemon_paths",
        "accounting_caveats": [
            "The largest listed subsystem is not necessarily the largest filesystem consumer.",
            "Docker images and unlisted paths are excluded from subsystem accounting.",
            "Subsystem bytes are not complete owner-attributed or billable storage.",
            "This is not an atomic snapshot; status may overlay newer universe-scoped "
            "subsystem values on root-scoped observations.",
            "Cache TTL is reuse time after scan completion, not a maximum observation age.",
            "Filesystem percentage uses total capacity and available bytes, including "
            "reserved-space effects; it can differ from df's used/(used+available).",
            "When volume_availability is unavailable, legacy zero values and pressure "
            "labels are not evidence of healthy storage.",
        ],
        "volume_percent": round(volume_percent, 4),
        "volume_bytes_total": volume_bytes_total,
        "volume_bytes_free": volume_bytes_free,
        "per_subsystem": per_subsystem,
        "subsystem_caps": subsystem_caps,
        # No historical timeseries store yet — growth_estimate lands in a
        # later phase when run-transcript rotation emits size-at-time
        # samples. Null is the spec-mandated shape for the no-data case.
        "growth_estimate": None,
        "pressure_level": _pressure_level_from_percent(volume_percent),
    }


def base_path_from_universe(universe_path: str | Path) -> Path:
    return Path(universe_path).resolve().parent


def universe_id_from_path(universe_path: str | Path) -> str:
    return Path(universe_path).resolve().name


# -------------------------------------------------------------------
# SQLite connection factory
# -------------------------------------------------------------------


@contextlib.contextmanager
def _connect(base_path: str | Path):
    path = db_path(base_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        with conn:
            yield conn
    finally:
        conn.close()


__all__ = [
    # Constants
    "DB_FILENAME",
    "DEFAULT_BRANCH_MODE",
    "DEFAULT_QUICK_VOTE_SECONDS",
    "SESSION_PREFIX",
    "CAP_READ_PUBLIC_UNIVERSE",
    "CAP_SUBMIT_REQUEST",
    "CAP_FORK_BRANCH",
    "CAP_PROPOSE_AUTHOR_FORK",
    "CAP_SPAWN_RUNTIME_CAPACITY",
    "CAP_ASSIGN_RUNTIME_PROVIDER",
    "CAP_PAUSE_RESUME_SERVER",
    "CAP_ROLLBACK_BRANCH",
    "CAP_PROMOTE_BRANCH",
    "CAP_SUPERSEDE_BRANCH",
    "CAP_EDIT_UNIVERSE_RULES",
    "CAP_GRANT_CAPABILITIES",
    "CAP_RESOLVE_VOTE",
    "CAP_SET_GOAL_SELECTOR",
    "CAP_SET_CANONICAL_BRANCH",
    "CAP_DEFINE_GATE_LADDER",
    "CAP_RETRACT_GATE_CLAIM",
    "ALL_CAPABILITIES",
    "DEFAULT_USER_CAPABILITIES",
    # Helpers
    "_now",
    "_json_dumps",
    "_json_loads",
    "_slugify",
    "base_path_from_universe",
    "data_dir",
    "db_path",
    "inspect_storage_utilization",
    "universe_id_from_path",
    "wiki_path",
    "_connect",
    # Accounts bounded context
    "_account_id_for_username",
    "actor_has_capability",
    "create_or_update_account",
    "create_session",
    "ensure_host_account",
    "get_account",
    "grant_capabilities",
    "list_accounts",
    "list_capabilities",
    "resolve_bearer_token",
]


# -------------------------------------------------------------------
# Bounded-context re-exports — lazy via PEP-562 ``__getattr__``
# -------------------------------------------------------------------
#
# Rationale (docs/design-notes/2026-04-19-storage-init-stale-bytecode-
# mitigation.md Option A): eager re-exports at module-body tail created
# a circular-import window (``accounts.py`` top-imports constants from
# ``tinyassets.storage``; this file end-imports functions from
# ``accounts``). Worked by accident of ordering. The 2026-04-19 P0
# exposed the fragility when R7a symbol additions raced process restart.
#
# The lazy shape below means a fresh ``from tinyassets.storage.accounts
# import ...`` runs only at first attribute access, AFTER the package
# body has fully bound its constants. Same public API (``from
# tinyassets.storage import ensure_host_account`` etc. still works per
# Python's ``from`` import resolution protocol).
#
# ``__all__`` still enumerates every re-export so that ``import *``,
# static analyzers, and the import-graph smoke test can discover them.


_LAZY_IMPORTS = {
    # name -> (submodule, attr). Submodule path is relative to
    # ``tinyassets.storage``; attr is the name to look up on the submodule.
    "_account_id_for_username": ("accounts", "_account_id_for_username"),
    "actor_has_capability":     ("accounts", "actor_has_capability"),
    "create_or_update_account": ("accounts", "create_or_update_account"),
    "create_session":           ("accounts", "create_session"),
    "ensure_host_account":      ("accounts", "ensure_host_account"),
    "get_account":              ("accounts", "get_account"),
    "grant_capabilities":       ("accounts", "grant_capabilities"),
    "list_accounts":            ("accounts", "list_accounts"),
    "list_capabilities":        ("accounts", "list_capabilities"),
    "resolve_bearer_token":     ("accounts", "resolve_bearer_token"),
}


def __getattr__(name: str) -> Any:  # PEP-562
    """Resolve re-exported names against the current submodule state.

    Cache the resolved value on the package so subsequent accesses are
    O(1) and participate in ``dir()`` discovery. Missing names raise
    ``AttributeError`` (standard module-attribute contract).
    """
    if name in _LAZY_IMPORTS:
        import importlib
        submodule, attr = _LAZY_IMPORTS[name]
        mod = importlib.import_module(f"tinyassets.storage.{submodule}")
        try:
            value = getattr(mod, attr)
        except AttributeError as exc:
            raise AttributeError(
                f"module 'tinyassets.storage' lazy-import target "
                f"'tinyassets.storage.{submodule}' has no attribute {attr!r}"
            ) from exc
        globals()[name] = value  # cache for subsequent accesses
        return value
    raise AttributeError(f"module 'tinyassets.storage' has no attribute {name!r}")


def __dir__() -> list[str]:  # PEP-562 pair — supports ``dir(tinyassets.storage)``
    return sorted(set(globals()) | set(_LAZY_IMPORTS))
