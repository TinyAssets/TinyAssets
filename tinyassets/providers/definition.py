"""Open, user-defined compute provider registry (compute-agnostic-provider-set).

A :class:`ProviderDefinition` is an immutable descriptor of a compute provider a
universe owner has registered. It is the ``provider definition -> candidate`` state
in the ownership model
(``openspec/changes/compute-agnostic-provider-set/design.md`` §1): registration
creates ONLY a candidate. It does NOT enroll, authorize, select, or make the
provider routable — downstream owners (``user-assigned-llm-policy``
selection, ``constrain-set-engine-provider-authority``'s
``allowed_providers`` ceiling) act on the candidate. **This module writes no
authority and no credential.**

Access methods (design §3) — an executor is chosen deterministically by this field,
with NO cross-method fallback (Hard Rule #3 evolution):

- ``subscription_cli`` — references an existing CLI provider identity (e.g.
  ``codex``); the vendor CLI adapter executes it. Subscription writers stay CLI
  subprocesses, never an API SDK.
- ``api_key_http`` — references a ``ConnectionLedger`` http connection (endpoint +
  credential + grant, already SSRF-validated by ``connect_http``); a protocol
  encoder executes it over the credential-blind outbound proxy — never a vendor SDK
  pointed at an arbitrary ``base_url``.

The descriptor carries a ``ref`` (an indirection), never a secret: for
``subscription_cli`` it is the provider name (``codex`` / ``claude-code``); for
``api_key_http`` it is the ``grant_id`` of the ``ConnectionLedger`` grant that binds
the connection to this universe (not secret — the grant is the authorization, and
the credential lives in the vault behind the connection the grant points to). A
``commons`` definition is a remixable SHAPE only: remixing never carries the original
owner's ``ref`` or credential — the remixer supplies their own.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyassets.api.helpers import _universe_dir
from tinyassets.providers.wire_dialects import known_names

ACCESS_METHODS = ("subscription_cli", "api_key_http")
VISIBILITIES = ("private", "commons")

# Protocol shapes, partitioned by the access method they are valid with. A CLI
# protocol may only pair with subscription_cli; an HTTP protocol only with
# api_key_http — so the descriptor cannot describe an incoherent executor.
_CLI_PROTOCOLS = ("cli:codex", "cli:claude-code")
# HTTP protocols are the bundled wire dialects (``providers/dialects/*.json``):
# their structural names (``chat_messages``, ``content_blocks``) and the aliases
# stored rows already carry (``openai_chat``, ``anthropic_messages``). A stored
# row keeps its exact string, because the definition id content-addresses it.
_HTTP_PROTOCOLS = known_names()
PROTOCOLS = _CLI_PROTOCOLS + _HTTP_PROTOCOLS

_STORE_FILENAME = "provider_definitions.json"

# ``ref`` grammar: bounded ASCII, no whitespace/control. Covers both a provider
# name (codex / claude-code) and a ConnectionLedger connection id (http_<hex>).
_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{1,190}$")
# ``model`` is provider-defined free text but bounded and single-line.
_MAX_MODEL_CHARS = 200
_MODEL_RE = re.compile(r"^[^\x00-\x1f]{1,200}$")


class ProviderDefinitionError(ValueError):
    """Raised when a definition is malformed or conflicts with a stored one."""


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    """Immutable compute-provider descriptor. Content-addressed by ``id``."""

    id: str
    universe_id: str
    owner_user_id: str
    access_method: str
    protocol: str
    model: str
    ref: str
    visibility: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public_view(self) -> dict[str, Any]:
        """The remixable SHAPE only — no owner, no ref (which is another user's
        connection/provider and useless-plus-leaky to expose in the commons)."""
        return {
            "id": self.id,
            "access_method": self.access_method,
            "protocol": self.protocol,
            "model": self.model,
            "visibility": self.visibility,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _definition_id(
    *, universe_id: str, owner_user_id: str, access_method: str, protocol: str,
    ref: str, model: str
) -> str:
    """Deterministic server-issued id (never a user label). Length-prefixed
    canonical serialization so no two distinct descriptors collide, and identical
    descriptors register idempotently to the same id.

    ``owner_user_id`` is part of the material (Codex review): otherwise the id is a
    predictable function of shared inputs and a second principal in the same universe
    could pre-register it and squat the slot. With the owner in the id, each owner has
    their own id space — two owners registering the same descriptor get two distinct
    definitions, never a shared slot to contend."""
    parts = (universe_id, owner_user_id, access_method, protocol, ref, model)
    material = "\0".join(f"{len(p)}:{p}" for p in parts).encode()
    return f"provdef_{hashlib.sha256(material).hexdigest()[:32]}"


def _validate(
    *, access_method: str, protocol: str, model: str, ref: str, visibility: str
) -> None:
    if access_method not in ACCESS_METHODS:
        raise ProviderDefinitionError(
            f"access_method must be one of {ACCESS_METHODS}"
        )
    if visibility not in VISIBILITIES:
        raise ProviderDefinitionError(f"visibility must be one of {VISIBILITIES}")
    if protocol not in PROTOCOLS:
        raise ProviderDefinitionError(f"protocol must be one of {PROTOCOLS}")
    # Access-method / protocol coherence — a descriptor cannot name an executor
    # it could not run.
    if access_method == "subscription_cli" and protocol not in _CLI_PROTOCOLS:
        raise ProviderDefinitionError(
            "subscription_cli requires a cli:* protocol"
        )
    if access_method == "api_key_http" and protocol not in _HTTP_PROTOCOLS:
        raise ProviderDefinitionError(
            "api_key_http requires a bundled wire dialect ("
            + "/".join(_HTTP_PROTOCOLS) + ")"
        )
    if not isinstance(model, str) or not _MODEL_RE.match(model):
        raise ProviderDefinitionError(
            f"model must be 1..{_MAX_MODEL_CHARS} printable single-line chars"
        )
    if not isinstance(ref, str) or not _REF_RE.match(ref):
        raise ProviderDefinitionError("ref grammar invalid")


def _store_path(universe_id: str) -> Path:
    return _universe_dir(universe_id) / _STORE_FILENAME


def _load(universe_id: str) -> list[dict[str, Any]]:
    """Load the per-universe registry store.

    An ABSENT store is an empty list (safe to create). A store that EXISTS but is
    unreadable, corrupt, or the wrong shape RAISES (Codex review): returning ``[]``
    there would let the next registration overwrite the file and silently discard
    every prior definition. Fail loud, never clobber (Hard Rule #8)."""
    path = _store_path(universe_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Do NOT interpolate exc — it can carry the store's filesystem path or parser
        # internals, and this message can surface on the served MCP surface (Codex
        # adapt #4). The chained cause preserves the detail for local logs.
        raise ProviderDefinitionError(
            "provider definitions store is present but unreadable or corrupt"
        ) from exc
    if not isinstance(data, list):
        raise ProviderDefinitionError(
            "provider definitions store is malformed (expected a JSON list)"
        )
    return data


def _write(universe_id: str, rows: list[dict[str, Any]]) -> None:
    path = _store_path(universe_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A UNIQUE temp file per write + atomic replace (Codex review): a fixed temp name
    # let two concurrent writers clobber each other mid-replace. NOTE (MVP-deferred,
    # matching the single-founder concurrency-edge law): the load->append->write cycle
    # is not yet locked, so two truly-concurrent registrations could still lose a row;
    # a per-universe lock is the follow-up when multi-writer concurrency is in scope.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".provdef-", suffix=".json.tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            from tinyassets.role_modes import BROKER_METADATA_FILE_MODE, BROKER_READ_GID

            # The broker (gid 1102 reader) resolves definitions; nobody else reads them.
            os.fchown(handle.fileno(), -1, BROKER_READ_GID)
            os.fchmod(handle.fileno(), BROKER_METADATA_FILE_MODE)
            handle.write(json.dumps(rows, indent=2, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def register_definition(
    *,
    universe_id: str,
    owner_user_id: str,
    access_method: str,
    protocol: str,
    model: str,
    ref: str,
    visibility: str = "private",
    now: str | None = None,
) -> ProviderDefinition:
    """Register a compute provider definition. Creates ONLY a candidate — no
    enrollment, no selection, no authority, no credential write.

    Idempotent: an identical descriptor returns the existing definition. A stored
    definition with the same id but a different owner raises (a second principal
    cannot claim another's definition slot), mirroring connect_http's identity rule.
    """
    if not universe_id or not owner_user_id:
        raise ProviderDefinitionError("universe_id and owner_user_id are required")
    _validate(
        access_method=access_method,
        protocol=protocol,
        model=model,
        ref=ref,
        visibility=visibility,
    )
    definition_id = _definition_id(
        universe_id=universe_id,
        owner_user_id=owner_user_id,
        access_method=access_method,
        protocol=protocol,
        ref=ref,
        model=model,
    )
    rows = _load(universe_id)
    for row in rows:
        if row.get("id") != definition_id:
            continue
        # Idempotent re-register: everything is derived from the id inputs except
        # owner + visibility. Owner mismatch is a conflict; visibility may be
        # re-declared by the owner (private<->commons) without a new id.
        if row.get("owner_user_id") != owner_user_id:
            raise ProviderDefinitionError("definition owned by another principal")
        # Integrity re-check on the idempotent path (Codex adapt #3): a stored row
        # sharing this id whose fields no longer content-address it (e.g. a swapped
        # ref) must fail closed, never be returned or rewritten under a trusted id.
        verified = _verified_definition(row)
        if verified.visibility != visibility:
            row["visibility"] = visibility
            _write(universe_id, rows)
            return _verified_definition(row)
        return verified

    definition = ProviderDefinition(
        id=definition_id,
        universe_id=universe_id,
        owner_user_id=owner_user_id,
        access_method=access_method,
        protocol=protocol,
        model=model,
        ref=ref,
        visibility=visibility,
        created_at=now or _now_iso(),
    )
    rows.append(definition.as_dict())
    _write(universe_id, rows)
    return definition


def _verified_definition(
    row: dict[str, Any], *, expect_universe: str | None = None
) -> ProviderDefinition:
    """Construct a ProviderDefinition and verify its id is the content-address of its
    own fields. The id is deterministic over (universe, owner, access_method, protocol,
    ref, model), so a stored row whose id does NOT recompute has been tampered with —
    e.g. an id kept while `ref` (the grant) was swapped, which would let a substituted
    grant serve under a trusted name (Codex reject #4). Fail closed on mismatch.

    When ``expect_universe`` is given, ALSO verify the row belongs to that universe
    bucket (Codex adapt, read-surface review): the content-address proves the fields
    are self-consistent, NOT that the row lives in the right per-universe store — a row
    copied from another universe's file would carry a valid id but the wrong
    ``universe_id``. Fail closed on a bucket mismatch."""
    definition = ProviderDefinition(**row)
    expected = _definition_id(
        universe_id=definition.universe_id,
        owner_user_id=definition.owner_user_id,
        access_method=definition.access_method,
        protocol=definition.protocol,
        ref=definition.ref,
        model=definition.model,
    )
    if definition.id != expected:
        raise ProviderDefinitionError(
            "provider definition id integrity check failed (tampered store row)"
        )
    if expect_universe is not None and definition.universe_id != expect_universe:
        raise ProviderDefinitionError(
            "provider definition command center mismatch (row in the wrong bucket)"
        )
    return definition


def get_definition(universe_id: str, definition_id: str) -> ProviderDefinition | None:
    for row in _load(universe_id):
        if row.get("id") == definition_id:
            return _verified_definition(row, expect_universe=universe_id)
    return None


def list_definitions(universe_id: str) -> list[ProviderDefinition]:
    return [
        _verified_definition(row, expect_universe=universe_id)
        for row in _load(universe_id)
    ]


def list_commons_definitions(base: str | Path) -> list[dict[str, Any]]:
    """Every ``commons``-visibility definition across universes, as remixable
    SHAPE-only public views (no owner, no ref). This is what a user connecting
    compute sees — the channels prior users built."""
    root = Path(base)
    views: list[dict[str, Any]] = []
    if not root.is_dir():
        return views
    for child in sorted(root.iterdir()):
        store = child / _STORE_FILENAME
        if not store.is_file():
            continue
        try:
            rows = json.loads(store.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("visibility") == "commons":
                views.append(ProviderDefinition(**row).public_view())
    return views


def remix_definition(
    *,
    source_public_view: dict[str, Any],
    into_universe_id: str,
    new_owner_user_id: str,
    new_ref: str,
    now: str | None = None,
) -> ProviderDefinition:
    """Remix a commons definition's SHAPE into a new universe. The remixer MUST
    supply their own ``new_ref`` (their own connection/provider) — the original
    owner's ref and credential are NEVER carried (design §7, Codex finding). The
    result is a fresh PRIVATE candidate owned by the remixer.
    """
    access_method = str(source_public_view.get("access_method", ""))
    protocol = str(source_public_view.get("protocol", ""))
    model = str(source_public_view.get("model", ""))
    # A public view carries no ref/owner by construction; guard against a caller
    # passing a full definition dict and silently importing someone else's ref.
    if "ref" in source_public_view or "owner_user_id" in source_public_view:
        raise ProviderDefinitionError(
            "remix source must be a public view (no ref/owner); a credential or "
            "connection is never carried across a remix"
        )
    return register_definition(
        universe_id=into_universe_id,
        owner_user_id=new_owner_user_id,
        access_method=access_method,
        protocol=protocol,
        model=model,
        ref=new_ref,
        visibility="private",
        now=now,
    )
