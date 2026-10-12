"""Account KEKs and command-center DEKs. Only the broker process constructs this.

Unwrapped KEKs live in this object's memory. Disk holds wrapped keys and
sealed records. A missing KMS configuration fails closed. There is no
plaintext fallback and no locally generated production root.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tinyassets.broker.root_key import (
    KeyServiceUnavailable,
    MemoryRootKey,
    ReconnectRequired,
    RootKeyError,
    RootKeyProvider,
    encryption_context,
)
from tinyassets.vault_envelope import (
    KEY_BYTES,
    EnvelopeError,
    canonical_record,
    open_envelope,
    seal,
)

_SERVICES: dict[str, "KeyService"] = {}
_SERVICES_LOCK = threading.Lock()
_ALARM_THRESHOLD = 3


def account_id(owner: str) -> str:
    """Opaque digest. CloudTrail sees this, never an email or a name."""
    if not isinstance(owner, str) or not owner.strip():
        raise RootKeyError("account is required to seal a credential")
    return hashlib.sha256(b"tinyassets-account-v1\x00" + owner.strip().encode("utf-8")).hexdigest()


def record_id_for(record: dict[str, Any]) -> str:
    credential_type = str(record.get("credential_type") or "")
    slot = str(record.get("destination") or record.get("service") or record.get("provider") or "")
    purpose = str(record.get("purpose") or "")
    return hashlib.sha256(
        json.dumps([credential_type, slot, purpose], separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]


class KeyService:
    def __init__(self, data_root: Path, provider: RootKeyProvider) -> None:
        self.data_root = Path(data_root)
        self.provider = provider
        self._cache: dict[str, tuple[str, bytes]] = {}
        self._lock = threading.RLock()
        broker = self.data_root / ".broker"
        broker.mkdir(parents=True, exist_ok=True)
        self._path = broker / "vault-keys.db"
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS account_keks (
                    account TEXT PRIMARY KEY,
                    kek_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    key_ref TEXT NOT NULL,
                    wrapped BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS center_deks (
                    account TEXT NOT NULL,
                    command_center TEXT NOT NULL,
                    dek_id TEXT NOT NULL,
                    kek_id TEXT NOT NULL,
                    wrapped BLOB NOT NULL,
                    active INTEGER NOT NULL,
                    PRIMARY KEY (account, command_center, dek_id)
                );
                CREATE TABLE IF NOT EXISTS sealed_records (
                    account TEXT NOT NULL,
                    command_center TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    dek_id TEXT NOT NULL,
                    credential_type TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    envelope TEXT NOT NULL,
                    PRIMARY KEY (account, command_center, record_id)
                );
                CREATE TABLE IF NOT EXISTS tombstones (
                    account TEXT PRIMARY KEY,
                    erased_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reconnect_asks (
                    account TEXT NOT NULL,
                    command_center TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    PRIMARY KEY (account, command_center, record_id)
                );
                CREATE TABLE IF NOT EXISTS center_owners (
                    command_center TEXT PRIMARY KEY,
                    owner TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS health (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    consecutive_failures INTEGER NOT NULL,
                    alarm INTEGER NOT NULL,
                    detail TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO health (id, consecutive_failures, alarm, detail) VALUES "
                "(1, 0, 0, '')"
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(sealed_records)").fetchall()
            }
            if "position" not in columns:
                conn.execute(
                    "ALTER TABLE sealed_records ADD COLUMN position INTEGER NOT NULL DEFAULT 0"
                )

    def _tombstoned(self, conn: sqlite3.Connection, account: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM tombstones WHERE account = ?",
                (account,),
            ).fetchone()
            is not None
        )

    def _kek(self, account: str) -> tuple[str, bytes]:
        with self._lock:
            cached = self._cache.get(account)
            if cached is not None:
                return cached
            context = encryption_context(account)
            with self._connect() as conn:
                if self._tombstoned(conn, account):
                    raise ReconnectRequired("erased account key is not re-imported")
                row = conn.execute(
                    "SELECT kek_id, wrapped, provider_id, key_ref FROM account_keks WHERE "
                    "account = ?",
                    (account,),
                ).fetchone()
            try:
                if row is None:
                    plaintext, wrapped = self.provider.generate(context)
                    kek_id = hashlib.sha256(wrapped).hexdigest()[:16]
                    with self._connect() as conn:
                        if self._tombstoned(conn, account):
                            raise ReconnectRequired("erased account key is not re-imported")
                        conn.execute(
                            "INSERT INTO account_keks (account, kek_id, provider_id, key_ref, "
                            "wrapped) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                account,
                                kek_id,
                                self.provider.provider_id,
                                self.provider.key_ref,
                                wrapped,
                            ),
                        )
                else:
                    if row["provider_id"] not in {self.provider.provider_id, "memory", "aws-kms"}:
                        raise ReconnectRequired("account key provider is not available")
                    plaintext = self.provider.unwrap(row["wrapped"], context)
                    kek_id = row["kek_id"]
            except ReconnectRequired:
                raise
            except KeyServiceUnavailable:
                raise
            except RootKeyError as exc:
                raise KeyServiceUnavailable("key service unavailable") from exc
            if len(plaintext) != KEY_BYTES:
                raise ReconnectRequired("account key did not authenticate")
            self._cache[account] = (kek_id, plaintext)
            return kek_id, plaintext

    def _active_dek(self, account: str, center: str) -> tuple[str, bytes, str]:
        kek_id, kek = self._kek(account)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT dek_id, wrapped, kek_id FROM center_deks "
                "WHERE account = ? AND command_center = ? AND active = 1",
                (account, center),
            ).fetchone()
        if row is None:
            dek = AESGCM.generate_key(bit_length=256)
            dek_id = hashlib.sha256(os.urandom(16)).hexdigest()[:16]
            wrapped = _wrap_dek(
                kek, dek, account=account, center=center, dek_id=dek_id, kek_id=kek_id
            )
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO center_deks (account, command_center, dek_id, kek_id, wrapped, "
                    "active) "
                    "VALUES (?, ?, ?, ?, ?, 1)",
                    (account, center, dek_id, kek_id, wrapped),
                )
            return dek_id, dek, kek_id
        try:
            dek = _open_dek(
                kek,
                row["wrapped"],
                account=account,
                center=center,
                dek_id=row["dek_id"],
                kek_id=row["kek_id"],
            )
        except EnvelopeError as exc:
            raise ReconnectRequired("command center key did not authenticate") from exc
        return row["dek_id"], dek, row["kek_id"]

    def _dek_by_id(self, account: str, center: str, dek_id: str) -> bytes:
        _kek_id, kek = self._kek(account)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT wrapped, kek_id FROM center_deks "
                "WHERE account = ? AND command_center = ? AND dek_id = ?",
                (account, center, dek_id),
            ).fetchone()
        if row is None:
            raise ReconnectRequired("command center key is not available")
        try:
            return _open_dek(
                kek,
                row["wrapped"],
                account=account,
                center=center,
                dek_id=dek_id,
                kek_id=row["kek_id"],
            )
        except EnvelopeError as exc:
            raise ReconnectRequired("command center key did not authenticate") from exc

    def has_center(self, center: str) -> bool:
        with self._connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM sealed_records WHERE command_center = ? LIMIT 1",
                    (center,),
                ).fetchone()
                is not None
            )

    def accounts_for_center(self, center: str) -> list[str]:
        with self._connect() as conn:
            return [
                row["account"]
                for row in conn.execute(
                    "SELECT DISTINCT account FROM sealed_records WHERE command_center = ?",
                    (center,),
                )
            ]

    def load_center(self, center: str) -> list[dict[str, Any]]:
        """Open every record that authenticates. One failure does not drop the rest."""
        opened: list[dict[str, Any]] = []
        unavailable = False
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT account, command_center, record_id, envelope, slot FROM sealed_records "
                "WHERE command_center = ? ORDER BY position, record_id",
                (center,),
            ).fetchall()
        for row in rows:
            try:
                opened.append(self._open_row(row))
            except ReconnectRequired:
                self._ask(row["account"], center, row["record_id"], row["slot"])
            except KeyServiceUnavailable:
                unavailable = True
        if unavailable and not opened and rows:
            raise KeyServiceUnavailable("key service unavailable")
        return opened

    def open_record(self, *, account: str, center: str, record_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT account, command_center, record_id, envelope, slot FROM sealed_records "
                "WHERE account = ? AND command_center = ? AND record_id = ?",
                (account, center, record_id),
            ).fetchone()
        if row is None:
            raise ReconnectRequired("credential record is not available")
        try:
            return self._open_row(row)
        except ReconnectRequired:
            self._ask(account, center, record_id, row["slot"])
            raise

    def _open_row(self, row: sqlite3.Row) -> dict[str, Any]:
        envelope = json.loads(row["envelope"])
        if (
            envelope.get("account") != row["account"]
            or envelope.get("command_center") != row["command_center"]
            or envelope.get("record_id") != row["record_id"]
        ):
            raise ReconnectRequired("credential vault authentication failed")
        try:
            dek = self._dek_by_id(row["account"], envelope["command_center"], envelope["key_id"])
            raw = open_envelope(envelope, key=dek)
        except (EnvelopeError, KeyError, json.JSONDecodeError) as exc:
            raise ReconnectRequired("credential vault authentication failed") from exc
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReconnectRequired("credential vault authentication failed") from exc
        if not isinstance(record, dict):
            raise ReconnectRequired("credential vault authentication failed")
        return record

    def _ask(self, account: str, center: str, record_id: str, slot: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO reconnect_asks (account, command_center, record_id, reason) "
                "VALUES (?, ?, ?, 'reconnect_required') "
                "ON CONFLICT (account, command_center, record_id) DO UPDATE SET reason = "
                "'reconnect_required'",
                (account, center, record_id),
            )
        try:
            from tinyassets.credential_vault import record_refresh_rejected

            record_refresh_rejected(self.data_root, universe_id=center, service=slot or record_id)
        except Exception:
            # The ask row is the record. A status-store failure must not hide it
            # or turn one record into a vault-wide outage.
            return

    def pending_reconnects(self, center: str) -> list[dict[str, str]]:
        with self._connect() as conn:
            return [
                {"record_id": row["record_id"], "reason": row["reason"]}
                for row in conn.execute(
                    "SELECT record_id, reason FROM reconnect_asks WHERE command_center = ?",
                    (center,),
                )
            ]

    def list_center(self, center: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [
                {
                    "record_id": row["record_id"],
                    "credential_type": row["credential_type"],
                    "slot": row["slot"],
                    "generation": row["generation"],
                }
                for row in conn.execute(
                    "SELECT record_id, credential_type, slot, generation FROM sealed_records "
                    "WHERE command_center = ? ORDER BY record_id",
                    (center,),
                )
            ]

    def replace_center(self, *, owner: str, center: str, records: list[dict[str, Any]]) -> None:
        account = account_id(owner)
        prepared: list[tuple[str, int, str, str, str, str, int]] = []
        for position, record in enumerate(records):
            public = {key: value for key, value in record.items() if not str(key).startswith("_")}
            record_id = record_id_for(public)
            generation = self._next_generation(account, center, record_id)
            dek_id, dek, kek_id = self._active_dek(account, center)
            plaintext = canonical_record(public)
            envelope = seal(
                plaintext,
                key=dek,
                key_id=dek_id,
                provider_id=self.provider.provider_id,
                key_ref=self.provider.key_ref,
                kek_id=kek_id,
                account=account,
                command_center=center,
                record_id=record_id,
                generation=generation,
            )
            try:
                opened = open_envelope(envelope, key=dek)
            except EnvelopeError as exc:
                raise RootKeyError(
                    "credential vault encryption verification failed; the vault was not replaced"
                ) from exc
            if opened != plaintext:
                raise RootKeyError(
                    "credential vault encryption verification failed; the vault was not replaced"
                )
            slot = str(
                public.get("destination") or public.get("service") or public.get("provider") or ""
            )
            prepared.append(
                (
                    record_id,
                    generation,
                    dek_id,
                    str(public.get("credential_type") or ""),
                    slot,
                    json.dumps(envelope, sort_keys=True),
                    position,
                )
            )
        new_ids = {item[0] for item in prepared}
        with self._lock:
            with self._connect() as conn:
                if self._tombstoned(conn, account):
                    raise ReconnectRequired("erased account key is not re-imported")
                conn.execute("BEGIN IMMEDIATE")
                try:
                    existing = conn.execute(
                        "SELECT record_id, envelope, dek_id FROM sealed_records "
                        "WHERE account = ? AND command_center = ?",
                        (account, center),
                    ).fetchall()
                    for row in existing:
                        if row["record_id"] in new_ids:
                            continue
                        try:
                            dek = self._dek_by_id(account, center, row["dek_id"])
                            open_envelope(json.loads(row["envelope"]), key=dek)
                        except (ReconnectRequired, EnvelopeError, json.JSONDecodeError):
                            continue
                        conn.execute(
                            "DELETE FROM sealed_records WHERE account = ? AND command_center = "
                            "? AND record_id = ?",
                            (account, center, row["record_id"]),
                        )
                        conn.execute(
                            "DELETE FROM reconnect_asks WHERE account = ? AND command_center = "
                            "? AND record_id = ?",
                            (account, center, row["record_id"]),
                        )
                    for (
                        record_id,
                        generation,
                        dek_id,
                        credential_type,
                        slot,
                        envelope,
                        position,
                    ) in prepared:
                        conn.execute(
                            "INSERT INTO sealed_records (account, command_center, record_id, "
                            "generation, dek_id, credential_type, slot, position, envelope) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT (account, command_center, record_id) DO UPDATE SET "
                            "generation = excluded.generation, dek_id = excluded.dek_id, "
                            "credential_type = excluded.credential_type, slot = excluded.slot, "
                            "position = excluded.position, envelope = excluded.envelope",
                            (
                                account,
                                center,
                                record_id,
                                generation,
                                dek_id,
                                credential_type,
                                slot,
                                position,
                                envelope,
                            ),
                        )
                        conn.execute(
                            "DELETE FROM reconnect_asks WHERE account = ? AND command_center = "
                            "? AND record_id = ?",
                            (account, center, record_id),
                        )
                    conn.execute(
                        "INSERT INTO center_owners (command_center, owner) VALUES (?, ?) "
                        "ON CONFLICT (command_center) DO UPDATE SET owner = excluded.owner",
                        (center, owner),
                    )
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise

    def owner_for_center(self, center: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner FROM center_owners WHERE command_center = ?",
                (center,),
            ).fetchone()
        if row is None or not row["owner"]:
            raise RootKeyError("account is required to seal a credential")
        return str(row["owner"])

    def _next_generation(self, account: str, center: str, record_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT generation FROM sealed_records "
                "WHERE account = ? AND command_center = ? AND record_id = ?",
                (account, center, record_id),
            ).fetchone()
        return 1 if row is None else int(row["generation"]) + 1

    def delete_record(self, *, owner: str, center: str, record_id: str) -> None:
        account = account_id(owner)
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM sealed_records WHERE account = ? AND command_center = ? AND "
                "record_id = ?",
                (account, center, record_id),
            )

    def rewrap_account_keks(self) -> int:
        """Rewrap every account KEK under the current root. Records stay sealed."""
        with self._connect() as conn:
            rows = list(conn.execute("SELECT account, kek_id, wrapped FROM account_keks"))
        count = 0
        for row in rows:
            context = encryption_context(row["account"])
            plaintext = self.provider.unwrap(row["wrapped"], context)
            wrapped = self.provider.wrap(plaintext, context)
            kek_id = hashlib.sha256(wrapped).hexdigest()[:16]
            with self._connect() as conn:
                conn.execute(
                    "UPDATE account_keks SET wrapped = ?, kek_id = ?, provider_id = ?, key_ref = ? "
                    "WHERE account = ?",
                    (
                        wrapped,
                        kek_id,
                        self.provider.provider_id,
                        self.provider.key_ref,
                        row["account"],
                    ),
                )
            self._cache.pop(row["account"], None)
            count += 1
        self._destroy_unreferenced_root()
        return count

    def _destroy_unreferenced_root(self) -> None:
        provider = self.provider
        if not isinstance(provider, MemoryRootKey) or not provider._retired:
            return
        with self._connect() as conn:
            rows = list(conn.execute("SELECT account, wrapped FROM account_keks"))
        for row in rows:
            provider.unwrap(row["wrapped"], encryption_context(row["account"]), active_only=True)
        provider.drop_retired()

    def rotate_center_dek(self, *, owner: str, center: str) -> str:
        account = account_id(owner)
        kek_id, kek = self._kek(account)
        dek = AESGCM.generate_key(bit_length=256)
        dek_id = hashlib.sha256(os.urandom(16)).hexdigest()[:16]
        wrapped = _wrap_dek(kek, dek, account=account, center=center, dek_id=dek_id, kek_id=kek_id)
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute(
                        "UPDATE center_deks SET active = 0 WHERE account = ? AND command_center "
                        "= ?",
                        (account, center),
                    )
                    conn.execute(
                        "INSERT INTO center_deks (account, command_center, dek_id, kek_id, "
                        "wrapped, active) "
                        "VALUES (?, ?, ?, ?, ?, 1)",
                        (account, center, dek_id, kek_id, wrapped),
                    )
                    rows = conn.execute(
                        "SELECT record_id, generation, envelope FROM sealed_records "
                        "WHERE account = ? AND command_center = ?",
                        (account, center),
                    ).fetchall()
                    for row in rows:
                        old = json.loads(row["envelope"])
                        old_key = self._dek_by_id(account, center, old["key_id"])
                        plaintext = open_envelope(old, key=old_key)
                        generation = int(row["generation"]) + 1
                        envelope = seal(
                            plaintext,
                            key=dek,
                            key_id=dek_id,
                            provider_id=self.provider.provider_id,
                            key_ref=self.provider.key_ref,
                            kek_id=kek_id,
                            account=account,
                            command_center=center,
                            record_id=row["record_id"],
                            generation=generation,
                        )
                        if open_envelope(envelope, key=dek) != plaintext:
                            raise RootKeyError("dek rotation verification failed")
                        conn.execute(
                            "UPDATE sealed_records SET generation = ?, dek_id = ?, envelope = ? "
                            "WHERE account = ? AND command_center = ? AND record_id = ?",
                            (
                                generation,
                                dek_id,
                                json.dumps(envelope, sort_keys=True),
                                account,
                                center,
                                row["record_id"],
                            ),
                        )
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
        self._drop_unreferenced_deks(account, center)
        return dek_id

    def _drop_unreferenced_deks(self, account: str, center: str) -> None:
        with self._connect() as conn:
            named = {
                row["dek_id"]
                for row in conn.execute(
                    "SELECT DISTINCT dek_id FROM sealed_records WHERE account = ? AND "
                    "command_center = ?",
                    (account, center),
                )
            }
            rows = conn.execute(
                "SELECT dek_id, active FROM center_deks WHERE account = ? AND command_center = ?",
                (account, center),
            ).fetchall()
            for row in rows:
                if row["dek_id"] not in named and not row["active"]:
                    conn.execute(
                        "DELETE FROM center_deks WHERE account = ? AND command_center = ? AND "
                        "dek_id = ?",
                        (account, center, row["dek_id"]),
                    )

    def erase_account(self, owner: str, *, survivors: list[tuple[str, str]] | None = None) -> None:
        """D12 order: rewrap survivors, then destroy this account's DEKs and KEK.

        ``survivors`` is a list of ``(center, new_owner)`` whose DEKs are rewrapped
        before the account KEK is destroyed. A restore will not import the KEK.
        """
        account = account_id(owner)
        for center, new_owner in survivors or []:
            self._rewrap_survivor(account, center, new_owner)
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM sealed_records WHERE account = ?", (account,))
                conn.execute("DELETE FROM center_deks WHERE account = ?", (account,))
                conn.execute("DELETE FROM account_keks WHERE account = ?", (account,))
                conn.execute("DELETE FROM reconnect_asks WHERE account = ?", (account,))
                conn.execute(
                    "INSERT INTO tombstones (account, erased_at) VALUES (?, datetime('now')) "
                    "ON CONFLICT (account) DO UPDATE SET erased_at = excluded.erased_at",
                    (account,),
                )
                conn.execute("COMMIT")
            self._cache.pop(account, None)

    def _rewrap_survivor(self, account: str, center: str, new_owner: str) -> None:
        new_account = account_id(new_owner)
        _kek_id, kek = self._kek(account)
        new_kek_id, new_kek = self._kek(new_account)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT dek_id, wrapped, kek_id FROM center_deks WHERE account = ? AND "
                "command_center = ?",
                (account, center),
            ).fetchall()
            for row in rows:
                dek = _open_dek(
                    kek,
                    row["wrapped"],
                    account=account,
                    center=center,
                    dek_id=row["dek_id"],
                    kek_id=row["kek_id"],
                )
                wrapped = _wrap_dek(
                    new_kek,
                    dek,
                    account=new_account,
                    center=center,
                    dek_id=row["dek_id"],
                    kek_id=new_kek_id,
                )
                active = conn.execute(
                    "SELECT active FROM center_deks WHERE account = ? AND command_center = ? "
                    "AND dek_id = ?",
                    (account, center, row["dek_id"]),
                ).fetchone()
                conn.execute(
                    "INSERT OR REPLACE INTO center_deks "
                    "(account, command_center, dek_id, kek_id, wrapped, active) VALUES (?, ?, "
                    "?, ?, ?, ?)",
                    (
                        new_account,
                        center,
                        row["dek_id"],
                        new_kek_id,
                        wrapped,
                        active["active"] if active else 0,
                    ),
                )
            records = conn.execute(
                "SELECT record_id, generation, dek_id, credential_type, slot, position, envelope "
                "FROM sealed_records WHERE account = ? AND command_center = ?",
                (account, center),
            ).fetchall()
            for row in records:
                envelope = json.loads(row["envelope"])
                envelope["account"] = new_account
                # The ciphertext is bound to the old account. Reseal under the survivor.
                dek = self._dek_by_id(account, center, row["dek_id"])
                plaintext = open_envelope(json.loads(row["envelope"]), key=dek)
                resealed = seal(
                    plaintext,
                    key=dek,
                    key_id=row["dek_id"],
                    provider_id=self.provider.provider_id,
                    key_ref=self.provider.key_ref,
                    kek_id=new_kek_id,
                    account=new_account,
                    command_center=center,
                    record_id=row["record_id"],
                    generation=row["generation"],
                )
                conn.execute(
                    "INSERT OR REPLACE INTO sealed_records (account, command_center, record_id, "
                    "generation, dek_id, credential_type, slot, position, envelope) VALUES (?, "
                    "?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_account,
                        center,
                        row["record_id"],
                        row["generation"],
                        row["dek_id"],
                        row["credential_type"],
                        row["slot"],
                        row["position"],
                        json.dumps(resealed, sort_keys=True),
                    ),
                )

    def import_account_kek(
        self, *, owner: str, wrapped: bytes, kek_id: str, provider_id: str, key_ref: str
    ) -> None:
        account = account_id(owner)
        with self._connect() as conn:
            if self._tombstoned(conn, account):
                raise ReconnectRequired("erased account key is not re-imported")
            conn.execute(
                "INSERT INTO account_keks (account, kek_id, provider_id, key_ref, wrapped) "
                "VALUES (?, ?, ?, ?, ?)",
                (account, kek_id, provider_id, key_ref, wrapped),
            )

    def probe(self) -> dict[str, Any]:
        context = encryption_context("health-probe")
        try:
            sample = b"\x00" * 32
            wrapped = self.provider.wrap(sample, context)
            opened = self.provider.unwrap(wrapped, context)
            if opened != sample:
                raise KeyServiceUnavailable("kms health probe did not round-trip")
        except ReconnectRequired as exc:
            self._note_failure("key service")
            raise KeyServiceUnavailable("key service") from exc
        except KeyServiceUnavailable as exc:
            self._note_failure("key service")
            raise KeyServiceUnavailable("key service") from exc
        except RootKeyError as exc:
            self._note_failure("key service")
            raise KeyServiceUnavailable("key service") from exc
        with self._connect() as conn:
            conn.execute(
                "UPDATE health SET consecutive_failures = 0, alarm = 0, detail = '' WHERE id = 1"
            )
        return {"status": "ok", "alarm": False, "reason": ""}

    def _note_failure(self, detail: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT consecutive_failures FROM health WHERE id = 1").fetchone()
            count = int(row["consecutive_failures"] if row else 0) + 1
            alarm = 1 if count >= _ALARM_THRESHOLD else 0
            conn.execute(
                "UPDATE health SET consecutive_failures = ?, alarm = ?, detail = ? WHERE id = 1",
                (count, alarm, detail),
            )

    def health_snapshot(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT consecutive_failures, alarm, detail FROM health WHERE id = 1"
            ).fetchone()
        if row is None:
            return {"status": "unknown", "alarm": False, "reason": "key service"}
        alarm = bool(row["alarm"])
        status = "unavailable" if row["consecutive_failures"] else "ok"
        return {
            "status": "alarm" if alarm else status,
            "alarm": alarm,
            "reason": row["detail"] or "",
        }


def _dek_aad(*, account: str, center: str, dek_id: str, kek_id: str) -> bytes:
    return json.dumps(
        ["ta-dek-v1", account, center, dek_id, kek_id],
        separators=(",", ":"),
    ).encode("utf-8")


def _wrap_dek(kek: bytes, dek: bytes, **binding: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM

    nonce = os.urandom(12)
    return nonce + _AESGCM(kek).encrypt(nonce, dek, _dek_aad(**binding))


def _open_dek(kek: bytes, wrapped: bytes, **binding: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM

    if len(wrapped) <= 12:
        raise EnvelopeError("command center key is incomplete")
    try:
        dek = _AESGCM(kek).decrypt(wrapped[:12], wrapped[12:], _dek_aad(**binding))
    except (InvalidTag, ValueError) as exc:
        raise EnvelopeError("command center key did not authenticate") from exc
    if len(dek) != KEY_BYTES:
        raise EnvelopeError("command center key did not authenticate")
    return dek


def broker_process() -> bool:
    uid = getattr(os, "getuid", lambda: -1)()
    return uid == 1002 or os.environ.get("TINYASSETS_BROKER_PROCESS") == "1"


def broker_service(data_root: Path) -> KeyService:
    """The broker's own key service. The daemon reaches it only over the socket."""
    from tinyassets.broker.vault_kms_file import provider_for_process

    key = str(Path(data_root).resolve())
    with _SERVICES_LOCK:
        service = _SERVICES.get(key)
        if service is None:
            service = KeyService(Path(key), provider_for_process())
            _SERVICES[key] = service
        return service


def for_data_root(data_root: Path) -> KeyService:
    if not broker_process():
        raise KeyServiceUnavailable("vault key service is held by the broker")
    return broker_service(data_root)


def seal_blob(
    service: KeyService,
    *,
    owner: str,
    center: str,
    record_id: str,
    generation: int,
    plaintext: bytes,
) -> bytes:
    account = account_id(owner)
    dek_id, dek, kek_id = service._active_dek(account, center)
    envelope = seal(
        plaintext,
        key=dek,
        key_id=dek_id,
        provider_id=service.provider.provider_id,
        key_ref=service.provider.key_ref,
        kek_id=kek_id,
        account=account,
        command_center=center,
        record_id=record_id,
        generation=generation,
    )
    return json.dumps(envelope, sort_keys=True).encode("utf-8")


def open_blob(
    service: KeyService,
    *,
    owner: str,
    center: str,
    blob: bytes,
    generation: int,
    record_id: str,
) -> bytes:
    try:
        envelope = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReconnectRequired("credential vault authentication failed") from exc
    account = account_id(owner)
    if (
        envelope.get("account") != account
        or envelope.get("command_center") != center
        or envelope.get("generation") != generation
        or envelope.get("record_id") != record_id
    ):
        raise ReconnectRequired("credential vault authentication failed")
    try:
        dek = service._dek_by_id(account, center, envelope["key_id"])
        return open_envelope(envelope, key=dek)
    except (EnvelopeError, KeyError, ReconnectRequired) as exc:
        raise ReconnectRequired("credential vault authentication failed") from exc


def reset_for_tests() -> None:
    with _SERVICES_LOCK:
        _SERVICES.clear()
