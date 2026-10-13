"""Public model ids for source kinds that have no list-models endpoint.

A source that CAN call its provider's own list endpoint needs nothing from here: its
ids arrive through discovery as ``executor_enumerated`` and are public by
construction. This module exists for the sources that cannot — a subscription CLI is
the case that prompted it (founder, 2026-09-26: "i cant seem to select fable as a user
for the llm"), where the only ids a universe could offer were the ones its owner had
typed by hand, so a newly released model stayed invisible until someone shipped a
patch.

The list lives in the repo, one file per source kind with ids grouped by source.
Users' agents propose additions by ordinary PR. Review is the moderation;
`scripts/check_model_lists.py`
refuses a malformed one in CI. That is the whole mechanism: no shared database, no
attestation, no confirmers, no pending state. A tracked file plus review is cheaper
than all of it, and a merged PR is a public claim by construction.

**A listed id is evidence an id EXISTS, never permission to use it.** A universe still
needs its owner's own accepted model access before one can be selected; until then it
shows under the picker's "needs access" group with the one-tap grant.

No vendor names in this module. The files it reads name real models, because they are
DATA; the code that reads them knows only the id shape
(``tinyassets/providers/model_class.py``).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

#: One file per source kind, beside the repo root. Kept out of the package so it reads
#: as reviewed data rather than shipped code.
_DIRECTORY_NAME = "models"

#: A source kind names a file, so it may not wander outside the directory.
_SOURCE_KIND = re.compile(r"\A[a-z][a-z0-9_]{0,31}\Z", re.ASCII)

#: The same identifier shape the personal store accepts: printable ASCII, no spaces,
#: bounded. Deliberately not a charset that tries to judge whether an id is "public" --
#: that judgment was tried twice and failed both ways; publication is now a reviewed
#: PR, which is a decision by people rather than a regex.
_MODEL_ID = re.compile(r"\A[\x21-\x7e]{1,200}\Z", re.ASCII)


class PublicModelListError(ValueError):
    """A list file that cannot be trusted. Never silently treated as empty."""


def lists_directory() -> Path:
    """The repo's ``models/`` directory, resolved from this file's own location.

    Not from the cwd and not from an env var: a data file the code depends on should
    be found the same way in a test, a container and a worktree.
    """
    return Path(__file__).resolve().parents[2] / _DIRECTORY_NAME


def read_list(source_kind: str, *, source_key: str | None = None,
              directory: Path | None = None) -> tuple[str, ...]:
    """Ids for one source, or all ids when inspecting the whole file.

    An absent file is a source kind nobody has listed for -- normal, and not an
    error. A file that EXISTS but is malformed raises: a corrupted or hand-broken
    list must not read as "no models", which would silently shrink every user's
    picker with no signal.
    """
    if not _SOURCE_KIND.match(str(source_kind)):
        raise PublicModelListError("invalid source kind")
    path = (directory or lists_directory()) / f"{source_kind}.json"
    if not path.is_file():
        return ()
    return _parse(path, source_kind, source_key)


def _parse(path: Path, source_kind: str, source_key: str | None) -> tuple[str, ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicModelListError(f"{path.name} is not readable JSON") from exc
    if not isinstance(document, dict) or set(document) != {"source_kind", "models"}:
        raise PublicModelListError(
            f"{path.name} must hold exactly source_kind and models")
    if document["source_kind"] != source_kind:
        raise PublicModelListError(f"{path.name} names a different source kind")
    models = document["models"]
    if not isinstance(models, dict) or not models:
        raise PublicModelListError(f"{path.name} models must be keyed by source")
    for key, entries in models.items():
        if not isinstance(key, str) or not _SOURCE_KIND.match(key.replace("-", "_")):
            raise PublicModelListError(f"{path.name} has an invalid source key")
        if not isinstance(entries, list) or not entries:
            raise PublicModelListError(f"{path.name} {key} must list models")
        if any(not isinstance(entry, str) or not _MODEL_ID.match(entry) for entry in entries):
            raise PublicModelListError(f"{path.name} holds an invalid model id")
        if len(entries) != len(set(entries)):
            raise PublicModelListError(f"{path.name} lists a model twice")
        if entries != sorted(entries):
            raise PublicModelListError(f"{path.name} must be sorted")
    if source_key is not None:
        return tuple(models.get(source_key, ()))
    return tuple(entry for entries in models.values() for entry in entries)


def newest_listed(source_kind: str, *, source_key: str | None = None,
                  directory: Path | None = None) -> tuple[str, ...]:
    """The newest listed id of each class for one source kind.

    Listing both ``some-model-4-6`` and ``4-7`` offers only ``4-7``, by the id's own
    shape -- so a file may keep its history without every old version crowding the
    picker, and nobody has to curate which is newest by hand.
    """
    from tinyassets.providers.model_class import newest_per_class

    listed = read_list(source_kind, source_key=source_key, directory=directory)

    class _Row:
        __slots__ = ("model_id", "first_verified_at")

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id
            # No verification time exists for a reviewed list, and none is invented.
            # newest_per_class breaks a version tie on this, so an empty string makes
            # the tiebreak fall through to the model id, which is deterministic.
            self.first_verified_at = ""

    return tuple(row.model_id for row in newest_per_class(_Row(entry) for entry in listed))


@lru_cache(maxsize=8)
def _cached(source_kind: str, source_key: str, fingerprint: tuple[int, int]) -> tuple[str, ...]:
    return newest_listed(source_kind, source_key=source_key)


def newest_listed_cached(source_kind: str, *, source_key: str) -> tuple[str, ...]:
    """``newest_listed`` keyed on the file's size AND nanosecond mtime.

    The read is on the model-options path, which a client polls, and the file changes
    only when a PR lands. Keyed on a fingerprint rather than time-boxed, so a dev edit
    is picked up at once and a container never serves a stale list.

    Size as well as mtime because mtime alone is not sufficient: Codex replaced a file's
    contents while preserving its mtime and got the cached answer (#4028). Nanosecond
    precision and the size together make a same-fingerprint content change require a
    byte-identical length at an identical timestamp, and a real change always arrives
    with a fresh container and an empty cache anyway.

    A MISSING file is unlisted, which is normal. Any other stat failure is not: it means
    the data exists and cannot be read, which must not masquerade as "no models".
    """
    path = lists_directory() / f"{source_kind}.json"
    try:
        stat = path.stat()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise PublicModelListError(
            f"{path.name} exists but cannot be read: {type(exc).__name__}") from exc
    return _cached(source_kind, source_key, (stat.st_size, stat.st_mtime_ns))
