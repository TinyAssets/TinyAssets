"""Read-only credential shapes and custody references.

Secrets live outside owner boxes. Only the broker package persists the vault;
public state and run evidence use credential-free summaries and references.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VAULT_FILENAME = ".credential-vault.json"
VALID_CREDENTIAL_TYPES = frozenset(
    # "http" is the general outbound-connection credential (channel-agnostic
    # outbound): a single token/secret for a user-declared HTTP connection,
    # resolved by connection id via `outbound_connections._GeneralVaultCredentialResolver`.
    {"social", "vcs", "http"}
)

#: When the secret NOW STORED in an http record was stored. There is no expiry
#: warning yet -- a key that died on the provider side was first noticed by a
#: failed run -- and this is the field one needs.
HTTP_DEPOSITED_AT = "deposited_at"


def http_credential_record(*, destination: str, token: str) -> dict[str, Any]:
    """The ONE shape of an http credential record, stamped with its write time.

    Three paths put a secret in an http slot: the owner's deposit
    (``api.http_connection.connect_http``), a rotation (``rotate_http``), and an
    oauth2 refresh (``connection_oauth.tokens``). ``_merge_single_record``
    REPLACES the whole credential slot, so a field only one
    of them wrote would silently disappear on the next write by another. One
    builder, so ``deposited_at`` means the same thing whichever path stored the
    secret that is there now.

    The token is passed straight through and never inspected, logged or returned.
    """
    from datetime import datetime, timezone

    return {
        "credential_type": "http",
        "service": destination,
        "destination": destination,
        "token": token,
        HTTP_DEPOSITED_AT: datetime.now(timezone.utc).isoformat(),
    }


def credential_vault_path(universe_dir: str | Path) -> Path:
    """Return the vault file path for *universe_dir*."""
    return _credential_root(Path(universe_dir)) / VAULT_FILENAME


def _credential_root(universe: Path) -> Path:
    """Platform custody of a command center's credentials: outside its box."""
    from tinyassets.owner_runtime import current

    if current() is not None:
        raise PermissionError("raw credential custody is unavailable in owner mode")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", universe.name):
        raise ValueError("invalid credential command center")
    return universe.parent / ".platform" / "credential-vaults" / universe.name


def vault_exists(universe_dir: str | Path | None) -> bool:
    """Return True when a vault file exists for *universe_dir*."""
    return universe_dir is not None and credential_vault_path(universe_dir).is_file()


def _normalize_record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("credential entries must be JSON objects")
    record = dict(raw)
    credential_type = record.get("credential_type")
    if not isinstance(credential_type, str) or not credential_type.strip():
        raise ValueError("credential_type is required")
    normalized_type = credential_type.strip()
    if normalized_type not in VALID_CREDENTIAL_TYPES:
        # The rejected value is NOT echoed. It is attacker- or typo-supplied
        # vault content, and a reviewer put a live token in this field and read
        # it back out of the exception. Naming the allowed set is enough to fix
        # a real mistake.
        allowed = ", ".join(sorted(VALID_CREDENTIAL_TYPES))
        raise ValueError(f"unknown credential_type; expected one of: {allowed}")
    record["credential_type"] = normalized_type
    for key in ("service", "provider", "destination", "purpose"):
        if isinstance(record.get(key), str):
            record[key] = record[key].strip()
    return record


def http_deposit_refusal(
    universe_dir: str | Path, *, destination: str, owner_user_id: str,
) -> str:
    """Why an owned http write for ``destination`` would be refused, or ``""``.

    Read-only, and derived from the SAME rows :func:`write_credential_vault`
    compares, so a surface that previews a write cannot disagree with the write
    that follows. There is deliberately no second copy of the rule here — both
    conditions below are the ones the writer raises ``PermissionError`` for.

    It exists because a ROTATION is previewed before the owner is asked to paste
    anything: a record left with no ownership row (deposited before http
    ownership was tracked, or by an owner-less path) is refused by the write, and
    admitting that card would put an unfulfillable tab in front of the owner
    (Codex refute-review, P2 #4).
    """
    universe = Path(universe_dir).resolve(strict=False)
    service = (destination or "").strip().lower()
    owner = (owner_user_id or "").strip()
    if not service or not owner:
        return "incomplete_request"
    path = custody_metadata_path(universe.parent)
    rows = []
    if path.exists():
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT service, owner_user_id FROM llm_credential_deposit_owners "
                "WHERE universe_id = ?", (universe.name,),
            ).fetchall()
        finally:
            conn.close()
    if any(str(row[1]) != owner for row in rows):
        return "foreign_owner"
    owned = {str(row[0]) for row in rows}
    if f"http:{service}" in owned:
        return ""
    on_disk = {
        _service(record)
        for record in load_credential_vault(universe)
        if record.get("credential_type") == "http"
    }
    return "unowned_record" if service in on_disk else ""


def _records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        raw_records = payload
    elif isinstance(payload, dict):
        raw_records = payload.get("credentials", [])
    else:
        raise ValueError("credential vault must be a JSON object or list")
    if not isinstance(raw_records, list):
        raise ValueError("credential vault 'credentials' must be a list")
    return [_normalize_record(item) for item in raw_records]


#: Service names are short lowercase identifiers (slack, github, anthropic...).
#: Anything else is not a name we issued and must not reach a log surface.
_SERVICE_NAME = re.compile(r"\A[a-z][a-z0-9._-]{0,39}\Z")


def _safe_service_name(value: object) -> str:
    """A service name fit to print, or "" — an allow-list, never a scrub."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip().lower()
    return candidate if _SERVICE_NAME.match(candidate) else ""


def _service(record: dict[str, Any]) -> str:
    return str(record.get("service") or record.get("provider") or "").strip().lower()


def _credential_key(record: dict[str, Any]) -> tuple[Any, ...]:
    """Return the logical key used for single-record vault upserts."""
    credential_type = str(record["credential_type"])
    service = _service(record)
    if credential_type == "vcs":
        destination = str(record.get("destination") or "").strip()
        return credential_type, service, destination
    return credential_type, service


def _vcs_purposes(record: dict[str, Any]) -> frozenset[str]:
    purpose = record.get("purpose")
    if isinstance(purpose, str) and purpose.strip():
        return frozenset({purpose.strip()})
    purposes = record.get("purposes")
    if isinstance(purposes, list):
        return frozenset(
            str(item).strip()
            for item in purposes
            if str(item).strip()
        )
    return frozenset({"write"})


def _credentials_match(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> bool:
    if _credential_key(existing) != _credential_key(incoming):
        return False
    if incoming["credential_type"] != "vcs":
        return True
    return bool(_vcs_purposes(existing) & _vcs_purposes(incoming))


def _ownership_service_keys(records: list[dict[str, Any]]) -> set[str]:
    """Namespaced HTTP slots whose depositor owns subsequent writes."""
    return {
        f"http:{_service(record)}" for record in records
        if record.get("credential_type") == "http" and _service(record)
    }


def _merge_single_record(
    existing: list[dict[str, Any]],
    incoming: dict[str, Any],
) -> list[dict[str, Any]]:
    matching_indexes = [
        index
        for index, record in enumerate(existing)
        if _credentials_match(record, incoming)
    ]
    if not matching_indexes:
        return [*existing, incoming]

    replacement = incoming

    first_match = matching_indexes[0]
    matching_set = set(matching_indexes)
    return [
        replacement if index == first_match else record
        for index, record in enumerate(existing)
        if index == first_match or index not in matching_set
    ]


def load_credential_vault(universe_dir: str | Path) -> list[dict[str, Any]]:
    """Load and validate the per-universe vault.

    Missing vaults are treated as empty. Malformed vaults raise ValueError so a
    daemon cannot silently grant or lose authority due to a bad secret file.
    """
    path = credential_vault_path(universe_dir)
    if not path.is_file():
        return []
    payload = None
    malformed = ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        # `UnicodeDecodeError.object` is the whole file too — a second channel
        # with the same consequence as JSONDecodeError.doc, found only because
        # a reviewer tried invalid UTF-8 rather than invalid JSON.
        malformed = "an undecodable byte"
    except json.JSONDecodeError as exc:
        # `exc.doc` is the ENTIRE vault file — every token for every service.
        # Chaining it, or interpolating `exc`, hands the whole thing to any
        # traceback, log, or error collector. Keep only the position, and raise
        # after this handler exits so no chain survives (`from None` clears
        # __cause__ but leaves __context__).
        malformed = f"line {exc.lineno} column {exc.colno}"
    if malformed:
        raise ValueError(f"credential vault is not valid JSON at {malformed}")
    return _records_from_payload(payload)


def _prepare_credential_write(
    universe_dir: str | Path,
    credentials: list[dict[str, Any]] | dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate + merge incoming credentials and return ``(records, summary)``
    WITHOUT touching the vault file.

    Against an existing valid vault, a single record is read-modify-write
    upserted into its logical slot and all matching duplicates collapse at their
    first position. Subscription fields merge; other credential types replace
    the whole slot. Two-or-more records replace the stored list exactly, and an
    empty payload clears it. A malformed existing vault blocks a single upsert.

    The returned ``records`` are what the caller persists as the LAST mutation of
    a deposit (after the owner-row DB commit, via ``_persist_credential_vault_file``)
    so the credential file is never visible before its ownership row is committed.
    The only filesystem access here is a read of the existing vault to compute a
    single-record upsert; nothing is written. The summary is non-secret and
    suitable for logs/status surfaces.
    """
    universe = Path(universe_dir)
    records = _records_from_payload(credentials)
    path = credential_vault_path(universe)
    collapsed_credential_count = 0
    dropped_credential_slots: list[dict[str, Any]] = []
    if len(records) == 1 and path.is_file():
        incoming = records[0]
        existing = load_credential_vault(universe)
        matching = [
            record
            for record in existing
            if _credentials_match(record, incoming)
        ]
        collapsed_credential_count = max(0, len(matching) - 1)
        if incoming["credential_type"] == "vcs":
            dropped_purposes = (
                frozenset().union(*(_vcs_purposes(record) for record in matching))
                - _vcs_purposes(incoming)
            )
            if dropped_purposes:
                dropped_credential_slots.append({
                    "credential_type": "vcs",
                    "service": _service(incoming),
                    "destination": str(
                        incoming.get("destination") or ""
                    ).strip(),
                    "purposes": sorted(dropped_purposes),
                })
        records = _merge_single_record(existing, incoming)
    credential_types = sorted({str(r["credential_type"]) for r in records})
    # Sanitised, not echoed. This summary is explicitly "suitable for logs and
    # status surfaces", and `service` is arbitrary vault content — a review put
    # a live token in that field and read it back out of the summary the
    # deposit script prints under the words "nothing above contains a token".
    # Same class as the credential_type echo fixed one round earlier.
    services = sorted(
        {
            name
            for r in records
            if (name := _safe_service_name(r.get("service") or r.get("provider")))
        }
    )
    summary = {
        "path": str(path),
        "credential_count": len(records),
        "credential_types": credential_types,
        "services": services,
        "collapsed_credential_count": collapsed_credential_count,
        "dropped_credential_slots": dropped_credential_slots,
    }
    return records, summary


@dataclass(frozen=True, slots=True)
class LLMCredentialCustodyReference:
    """Secret-free credential identity issued by the vault custody owner."""

    reference_id: str
    owner_user_id: str
    universe_id: str
    service: str
    generation: int
    reference_digest: str
    _record_digest: str = field(repr=False)


def _canonical_digest(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ensure_credential_owner_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_credential_deposit_owners (
            universe_id TEXT NOT NULL,
            service TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            PRIMARY KEY (universe_id, service)
        )
        """
    )


def _custody_reference_digest(
    *,
    reference_id: str,
    owner_user_id: str,
    universe_id: str,
    service: str,
    generation: int,
    record_digest: str,
) -> str:
    return _canonical_digest({
        "generation": generation,
        "owner_user_id": owner_user_id,
        "record_digest": record_digest,
        "reference_id": reference_id,
        "schema_version": 1,
        "service": service,
        "universe_id": universe_id,
    })


def _connection_custody_service(connection_id: str) -> str:
    """Namespaced custody key for a broker connection credential."""
    return f"connection:{connection_id.strip()}"


def _connection_grant_record_digest(
    *,
    grant_id: str,
    connection_id: str,
    credential_ref: str,
    owner_user_id: str,
    universe_id: str,
) -> str:
    """Secret-free grant-identity record digest. Binds custody to the exact grant +
    connection + credential reference; NEVER digests the credential material."""
    return _canonical_digest({
        "connection_id": connection_id,
        "credential_ref": credential_ref,
        "grant_id": grant_id,
        "owner_user_id": owner_user_id,
        "schema_version": 1,
        "service": _connection_custody_service(connection_id),
        "universe_id": universe_id,
    })


def _ensure_custody_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_credential_custody (
            reference_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            universe_id TEXT NOT NULL,
            service TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK (generation >= 1),
            record_digest TEXT NOT NULL,
            reference_digest TEXT NOT NULL,
            UNIQUE (owner_user_id, universe_id, service)
        )
        """
    )


def adopt_connection_grant_custody(
    conn: sqlite3.Connection,
    *,
    owner_user_id: str,
    universe_id: str,
    grant_id: str,
    connection_id: str,
    credential_ref: str,
) -> LLMCredentialCustodyReference:
    """Adopt/rotate connection-grant custody under an active transaction.

    Mirrors :func:`adopt_connection_grant_custody` exactly (same table, same
    reference-digest computation, same generation-on-drift semantics), but the
    credential is a connection grant — no subscription material is read, and the
    record digest is the secret-free grant identity. The CALLER MUST have already
    validated the grant is owned by ``owner_user_id`` + bound to ``universe_id`` +
    not revoked; this function records custody, it is not the ownership gate.
    """
    if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
        raise ValueError(
            "connection-grant custody adoption requires an active SQLite transaction"
        )
    owner = owner_user_id.strip()
    uid = universe_id.strip()
    gid = grant_id.strip()
    cid = connection_id.strip()
    ref = credential_ref.strip()
    if not owner or not uid or not gid or not cid or not ref:
        raise ValueError("connection-grant custody root is invalid")
    service = _connection_custody_service(cid)
    record_digest = _connection_grant_record_digest(
        grant_id=gid,
        connection_id=cid,
        credential_ref=ref,
        owner_user_id=owner,
        universe_id=uid,
    )
    _ensure_custody_schema(conn)
    row = conn.execute(
        """
        SELECT reference_id, generation, record_digest, reference_digest
          FROM llm_credential_custody
         WHERE owner_user_id = ? AND universe_id = ? AND service = ?
        """,
        (owner, uid, service),
    ).fetchone()
    if row is None:
        reference_id = f"llm_credential_{secrets.token_hex(16)}"
        generation = 1
    else:
        reference_id = str(row[0])
        if str(row[2]) == record_digest:
            return LLMCredentialCustodyReference(
                reference_id=reference_id,
                owner_user_id=owner,
                universe_id=uid,
                service=service,
                generation=int(row[1]),
                reference_digest=str(row[3]),
                _record_digest=record_digest,
            )
        generation = int(row[1]) + 1
    reference_digest = _custody_reference_digest(
        reference_id=reference_id,
        owner_user_id=owner,
        universe_id=uid,
        service=service,
        generation=generation,
        record_digest=record_digest,
    )
    conn.execute(
        """
        INSERT INTO llm_credential_custody (
            reference_id, owner_user_id, universe_id, service, generation,
            record_digest, reference_digest
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_user_id, universe_id, service) DO UPDATE SET
            generation = excluded.generation,
            record_digest = excluded.record_digest,
            reference_digest = excluded.reference_digest
        """,
        (reference_id, owner, uid, service, generation, record_digest, reference_digest),
    )
    return LLMCredentialCustodyReference(
        reference_id=reference_id,
        owner_user_id=owner,
        universe_id=uid,
        service=service,
        generation=generation,
        reference_digest=reference_digest,
        _record_digest=record_digest,
    )


def current_connection_grant_custody(
    conn: sqlite3.Connection,
    *,
    owner_user_id: str,
    universe_id: str,
    connection_id: str,
) -> LLMCredentialCustodyReference | None:
    """Return the current connection-grant custody reference, or None. Sibling of
    :func:`current_connection_grant_custody` for the connection service key."""
    if not isinstance(conn, sqlite3.Connection):
        raise ValueError("custody read requires a SQLite connection")
    owner = owner_user_id.strip()
    uid = universe_id.strip()
    service = _connection_custody_service(connection_id)
    _ensure_custody_schema(conn)
    row = conn.execute(
        """
        SELECT reference_id, generation, record_digest, reference_digest
          FROM llm_credential_custody
         WHERE owner_user_id = ? AND universe_id = ? AND service = ?
        """,
        (owner, uid, service),
    ).fetchone()
    if row is None:
        return None
    return LLMCredentialCustodyReference(
        reference_id=str(row[0]),
        owner_user_id=owner,
        universe_id=uid,
        service=service,
        generation=int(row[1]),
        reference_digest=str(row[3]),
        _record_digest=str(row[2]),
    )


def _secret_value(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    b64 = record.get("token_b64") or record.get("secret_b64")
    if isinstance(b64, str) and b64.strip():
        decoded = None
        try:
            decoded = base64.b64decode(b64.strip()).decode("utf-8").strip()
        except Exception:  # noqa: BLE001
            # Not chained, and raised outside the handler. A UnicodeDecodeError
            # here carries `.object` — the DECODED credential bytes — so
            # chaining published the very token this was decoding.
            decoded = None
        if decoded is None:
            raise ValueError(f"credential {keys[0]} base64 decode failed")
        return decoded
    return ""


def resolve_slack_token(
    universe_dir: str | Path | None,
    connection_id: str,
) -> str:
    """Return a Slack bot token for one connection, or an empty string.

    The record must be a ``social`` credential for service ``slack`` whose
    ``destination`` is the exact connection id. Scoping to the connection --
    rather than to the universe -- keeps one universe's Slack workspaces
    separable, so a second connection cannot be served with the first one's
    token.

    The caller is responsible for the vault-first / never-fall-through-to-env
    rule; this returns only what the vault holds.
    """
    if universe_dir is None:
        return ""
    wanted = connection_id.strip()
    if not wanted:
        return ""
    for record in load_credential_vault(universe_dir):
        if record.get("credential_type") != "social":
            continue
        if _service(record) != "slack":
            continue
        if str(record.get("destination") or "").strip() != wanted:
            continue
        return _secret_value(record, "bot_token", "token", "access_token")
    return ""


def resolve_slack_app_token(
    universe_dir: str | Path | None,
    connection_id: str,
) -> str:
    """Return a Slack **app-level** token for one connection, or an empty string.

    A Slack connection needs two different credentials, and they are not
    interchangeable: the bot token (``xoxb-``) posts messages, while the
    app-level token (``xapp-``, scope ``connections:write``) is the only one
    that can open a Socket Mode connection. Both live on the same vault record
    because they come from the same app install.

    Deliberately does NOT fall back to ``bot_token``/``token``. Handing a bot
    token to `apps.connections.open` fails with an opaque Slack error, and a
    silent fallback would turn "the app token was never deposited" into a
    mystery instead of the plain answer it is.
    """
    if universe_dir is None:
        return ""
    wanted = connection_id.strip()
    if not wanted:
        return ""
    for record in load_credential_vault(universe_dir):
        if record.get("credential_type") != "social":
            continue
        if _service(record) != "slack":
            continue
        if str(record.get("destination") or "").strip() != wanted:
            continue
        return _secret_value(record, "app_token", "app_level_token")
    return ""


def resolve_twitter_credentials(
    universe_dir: str | Path | None,
    destination: str,
) -> dict[str, str]:
    """Return the four OAuth 1.0a values for one X/Twitter destination.

    Mirrors :func:`resolve_slack_token`: the record must be a ``social``
    credential for service ``twitter`` (or ``x``) whose ``destination`` is the
    exact authorized destination. Scoping to the destination — never to the host
    process env — is what closes the cross-universe env hole the legacy
    ``twitter_post`` effector left open (it read ambient ``TWITTER_*``).

    Returns a ``{api_key, api_secret, access_token, access_token_secret}`` mapping
    only when ALL four values are present; otherwise an empty dict. The caller is
    responsible for the vault-first / never-fall-through-to-env rule — this returns
    only what the vault holds, and never echoes a value into caller-visible
    evidence.
    """
    if universe_dir is None:
        return {}
    wanted = destination.strip()
    if not wanted:
        return {}
    for record in load_credential_vault(universe_dir):
        if record.get("credential_type") != "social":
            continue
        if _service(record) not in ("twitter", "x"):
            continue
        if str(record.get("destination") or "").strip() != wanted:
            continue
        values = {
            "api_key": _secret_value(record, "api_key", "consumer_key"),
            "api_secret": _secret_value(record, "api_secret", "consumer_secret"),
            "access_token": _secret_value(record, "access_token"),
            "access_token_secret": _secret_value(record, "access_token_secret"),
        }
        if all(values.values()):
            return values
        return {}
    return {}


def resolve_universe_from_env(env: dict[str, str] | None = None) -> Path | None:
    """Resolve the active universe path from env, if one is explicitly bound."""
    source = os.environ if env is None else env
    value = source.get("TINYASSETS_UNIVERSE", "").strip()
    return Path(value) if value else None


def custody_metadata_path(data_root: str | Path) -> Path:
    """Broker-owned ownership and deletion metadata, never the owner database."""
    return Path(data_root).resolve() / ".broker" / "credential-custody.db"
