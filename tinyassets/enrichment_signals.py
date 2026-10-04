"""Domain-neutral enrichment signal queue helpers."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ENRICHMENT_SIGNALS_FILENAME = "enrichment_signals.json"
LEGACY_WORLDBUILD_SIGNALS_FILENAME = "worldbuild_signals.json"
ENRICHMENT_STATE_KEY = "enrichment_signals"
LEGACY_WORLDBUILD_STATE_KEY = "worldbuild_signals"


def enrichment_signals_path(universe_path: str | Path) -> Path:
    return Path(universe_path) / ENRICHMENT_SIGNALS_FILENAME


def legacy_worldbuild_signals_path(universe_path: str | Path) -> Path:
    return Path(universe_path) / LEGACY_WORLDBUILD_SIGNALS_FILENAME


def state_enrichment_signals(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return in-memory enrichment signals, accepting the legacy state key."""
    for key in (ENRICHMENT_STATE_KEY, LEGACY_WORLDBUILD_STATE_KEY):
        signals = state.get(key, [])
        if isinstance(signals, list) and signals:
            return [signal for signal in signals if isinstance(signal, dict)]
    return []


def load_enrichment_signals(
    universe_path: str | Path,
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """Load queued signals from the neutral file, falling back to the legacy file.

    A genuinely missing file always yields ``[]`` (the missing-canonical
    deprecation fallback). The ``strict`` flag governs a PRESENT-but-corrupt
    file:

    - ``strict=False`` (default): log loudly and return ``[]``. Safe for the
      many read-only consumers (routing in ``select_task``, cross-universe
      scans, status counts) that must not crash the daemon over a regenerable
      scratch file. It is *loud, not silent* — the error is logged.
    - ``strict=True``: raise ``RuntimeError``. Required by read-modify-write
      callers (``append_enrichment_signals``, the API re-emit path) so they can
      never overwrite queued work with a list derived from an empty read
      (Hard Rule #8).
    """
    signals_path = enrichment_signals_path(universe_path)
    # lexists: a dangling link at the canonical name is read (and refused), not
    # skipped in favour of the legacy file.
    if os.path.lexists(signals_path):
        return _read_signal_file(signals_path, strict=strict)
    return _read_signal_file(
        legacy_worldbuild_signals_path(universe_path), strict=strict,
    )


def write_enrichment_signals(
    universe_path: str | Path,
    signals: list[dict[str, Any]],
) -> None:
    from tinyassets.universe_files import write_data_path

    write_data_path(enrichment_signals_path(universe_path), json.dumps(signals, indent=2) + "\n")


def append_enrichment_signals(
    universe_path: str | Path,
    signals: list[dict[str, Any]],
) -> None:
    # Read-modify-write: read strict so a PRESENT-but-corrupt existing file
    # fails loud BEFORE we overwrite it with ``existing + signals``. A graceful
    # ``[]`` read here would silently drop whatever the corrupt file held.
    existing = load_enrichment_signals(universe_path, strict=True)
    write_enrichment_signals(universe_path, existing + signals)


def _read_signal_file(
    path: Path, *, strict: bool = False,
) -> list[dict[str, Any]]:
    from tinyassets.universe_files import MAX_PLATFORM_FILE_BYTES, read_data_path

    try:
        # Link-free; a refused read is an OSError handled as corruption below.
        raw = read_data_path(path, max_bytes=MAX_PLATFORM_FILE_BYTES)
        if raw is None:
            # The only legitimate "empty" path: the file genuinely does not
            # exist. The missing-canonical deprecation fallback, NOT an error mask.
            return []
        parsed = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, OSError, TypeError) as exc:
        # Hard Rule #8: a PRESENT file that cannot be read/parsed is corruption,
        # not the missing-file fallback. strict callers (read-modify-write) must
        # fail loud rather than overwrite the file with a derived-empty list;
        # read-only callers degrade to [] but log loudly (never silently).
        if strict:
            raise RuntimeError(
                f"enrichment signal file {path} exists but is "
                f"unreadable/malformed; refusing to silently drop queued "
                f"signals ({exc})"
            ) from exc
        logger.error(
            "enrichment signal file %s exists but is unreadable/malformed; "
            "treating as empty for this read and NOT overwriting it (%s)",
            path, exc,
        )
        return []
    if not isinstance(parsed, list):
        if strict:
            raise RuntimeError(
                f"enrichment signal file {path} exists but is not a JSON list "
                f"(got {type(parsed).__name__}); refusing to silently drop "
                "queued signals"
            )
        logger.error(
            "enrichment signal file %s exists but is not a JSON list (got %s); "
            "treating as empty for this read and NOT overwriting it",
            path, type(parsed).__name__,
        )
        return []
    return [signal for signal in parsed if isinstance(signal, dict)]
