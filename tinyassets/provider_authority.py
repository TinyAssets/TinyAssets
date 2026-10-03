"""Provider authority lives in platform state, never in the agent-editable config.

``config.yaml`` mixes the owner's preferences with server-owned authority: the
engine assignment lifecycle (``engine_assignment_state`` /
``engine_assignment_generation``), the secret-free ``provider_authority_bindings``
projection, and ``allowed_providers`` -- the routing ceiling the router enforces
(``providers/router.py``). The founder-approved harness makes the agent's own
config editable, and a file the agent can write must not hold what the daemon
trusts (``command-center-cutover`` design E6, ``target-architecture`` D8a). So
those four fields live in a platform record instead:

* `record_path` is the ONE resolver for where the record is. Today it is a hidden
  directory in the home (``.provider-authority/assignment.json``), which the tool
  jail never binds and the provider jail masks; the cutover retargets this
  function to ``.platform/cc-<ulid>/assignment.json`` and nothing else changes.
* `authority_for` reads it. The first read of a home that has no record yet is
  the one-time migration: today's values are copied out of ``config.yaml``
  (defaults when it has none), using exclusive creation. A record that exists
  always wins, including one published during migration; authority left in
  ``config.yaml`` is ignored and logged. If migration cannot write, readable
  config values remain usable in memory until the record can be created.
* A record that cannot be read fails CLOSED: an empty ``allowed_providers``
  ceiling, so nothing routes, rather than the unrestricted ``None`` default.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The fields that are authority, not preference.
AUTHORITY_FIELDS: tuple[str, ...] = (
    "allowed_providers",
    "engine_assignment_state",
    "engine_assignment_generation",
    "provider_authority_bindings",
)

#: Authority for a home that was never assigned (the UniverseConfig defaults).
DEFAULTS: dict[str, Any] = {
    "allowed_providers": None,
    "engine_assignment_state": "unassigned",
    "engine_assignment_generation": 0,
    "provider_authority_bindings": {},
}

#: What an unreadable record means: nothing may route.
FAIL_CLOSED: dict[str, Any] = {**DEFAULTS, "allowed_providers": []}

_DIR = ".provider-authority"
_FILE = "assignment.json"


def record_path(universe_path: str | Path) -> Path:
    """The one place the record lives (the cutover retargets only this)."""
    return Path(universe_path) / _DIR / _FILE


def _read(path: Path) -> dict[str, Any] | None:
    if not os.path.lexists(path):
        return None
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError(f"{path} is a link")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or set(document) != set(AUTHORITY_FIELDS):
        raise ValueError(f"{path} is not a provider authority record")
    return document


def write_record(universe_path: str | Path, fields: dict[str, Any]) -> None:
    """Replace the record atomically with exactly the authority fields."""
    record = {name: fields.get(name, DEFAULTS[name]) for name in AUTHORITY_FIELDS}
    path = record_path(universe_path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".assignment.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def authority_for(universe_path: str | Path, config_data: Any) -> dict[str, Any]:
    """The authority fields for this home, from the platform record only.

    ``config_data`` is the parsed ``config.yaml`` mapping, ``None`` when there is
    none, or any other object when it exists but is unreadable. It is consulted
    exactly once per home -- when no record exists yet -- to migrate today's
    values; after that it is never read for authority.
    """
    if not Path(universe_path).is_dir():
        # No home, no record: never create one (a retired home must not reappear).
        return dict(DEFAULTS)
    path = record_path(universe_path)
    try:
        record = _read(path)
    except (OSError, ValueError) as exc:
        logger.error("provider authority record unreadable (%s); nothing may route", exc)
        return dict(FAIL_CLOSED)
    if record is None and config_data is not None and not isinstance(config_data, dict):
        # config.yaml exists but cannot be read: migrating now would record the
        # defaults over a real assignment. Wait for a readable file; meanwhile
        # nothing routes.
        logger.error("config.yaml in %s unreadable and no authority record yet; "
                     "nothing may route until it is readable", universe_path)
        return dict(FAIL_CLOSED)
    if not isinstance(config_data, dict):
        config_data = None
    present = sorted(name for name in AUTHORITY_FIELDS if name in (config_data or {}))
    if record is None:
        migrated = {name: (config_data or {}).get(name, DEFAULTS[name])
                    for name in AUTHORITY_FIELDS}
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".assignment.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(migrated, handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    # A concurrent migration or publisher won. Never replace it.
                    try:
                        return _read(path) or dict(FAIL_CLOSED)
                    except (OSError, ValueError) as exc:
                        logger.error("provider authority record unreadable (%s); nothing may route",
                                     exc)
                        return dict(FAIL_CLOSED)
            finally:
                Path(tmp).unlink(missing_ok=True)
        except OSError as exc:
            # Even a failed write must not hide authority published meanwhile.
            try:
                record = _read(path)
            except (OSError, ValueError) as read_exc:
                logger.error("provider authority record unreadable (%s); nothing may route",
                             read_exc)
                return dict(FAIL_CLOSED)
            if record is not None:
                return record
            logger.warning("provider authority record could not be written (%s); "
                           "using migrated values in memory", exc)
            return migrated
        if present:
            logger.info("provider authority migrated out of config.yaml for %s: %s",
                        universe_path, ", ".join(present))
        return migrated
    if present:
        logger.warning(
            "config.yaml in %s carries authority fields (%s); ignored -- authority is "
            "read only from the platform record", universe_path, ", ".join(present),
        )
    return record


def current(universe_dir: str | Path | None, fallback_config: Any) -> Any:
    """Refresh authority for an existing home, retaining captured preferences.

    Without a home to read, retain the caller's ceiling rather than widening it
    to the defaults for a missing home.
    """
    if universe_dir is None or not Path(universe_dir).is_dir():
        return fallback_config
    from dataclasses import replace

    from tinyassets.config import _load_preferences

    preferences, data = _load_preferences(universe_dir)
    authority = authority_for(universe_dir, data)
    return replace(fallback_config if fallback_config is not None else preferences, **authority)
