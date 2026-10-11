"""Envelope encryption for the per-command-center credential vault.

The on-disk vault is not the credential document. Each write draws a random
data key, encrypts the document with AES-GCM under that data key, and wraps
the data key with AES-GCM under a master key (the key-encryption key). The
``cryptography`` library supplies AES-GCM. A missing or unknown master key
raises :class:`VaultKeyError` and nothing is written or returned as plaintext.

The default provider reads the daemon environment. A managed secret store
implements :class:`VaultKeyProvider` and is installed with
:func:`set_vault_key_provider`; the envelope format does not change.

TODO(owner): the production master key lives in the daemon environment, not
in a managed secret store. Keep that as the default until an owner chooses a
store. Do not add a second default, and do not generate an ephemeral key.

Environment (set on the server before this code serves a vault):

``TINYASSETS_VAULT_MASTER_KEY``
    Active master key. 32 bytes, base64url (padding optional) or 64-char hex.
    Generate 32 bytes and encode them with ``base64.urlsafe_b64encode``.
    The exact command is in ``docs/reference/environment-variables.md``.

``TINYASSETS_VAULT_MASTER_KEY_ID``
    Id recorded on new envelopes. Defaults to ``1`` when unset.

``TINYASSETS_VAULT_PREVIOUS_MASTER_KEYS``
    Retired keys, comma-separated ``key_id=material``, so existing envelopes
    still open. The next write, or :func:`tinyassets.credential_vault.rewrap_credential_vault`,
    seals under the active id. Drop a retired id only after every vault file
    names the active id.

The data-key wrap and the document are each bound to associated data. The
document is bound to the command-center directory name (the last path
component), so a file copied into a different command center does not open.
The full filesystem path is not part of that binding: a restore that keeps
the directory name still opens.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
from typing import Any, Mapping, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MASTER_KEY_ENV = "TINYASSETS_VAULT_MASTER_KEY"
MASTER_KEY_ID_ENV = "TINYASSETS_VAULT_MASTER_KEY_ID"
PREVIOUS_KEYS_ENV = "TINYASSETS_VAULT_PREVIOUS_MASTER_KEYS"
VAULT_KEY_ENV_NAMES = frozenset({
    MASTER_KEY_ENV,
    MASTER_KEY_ID_ENV,
    PREVIOUS_KEYS_ENV,
})

ENVELOPE_FORMAT = "tinyassets-credential-vault-envelope"
ENVELOPE_VERSION = 1
_KEY_BYTES = 32
_NONCE_BYTES = 12
_KEY_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_HEX_KEY = re.compile(r"\A[0-9a-fA-F]{64}\Z")
_DATA_AAD_PREFIX = b"tinyassets-credential-vault-v1\x00"
_DEK_AAD_PREFIX = b"tinyassets-credential-vault-dek-v1\x00"
_DEFAULT_KEY_ID = "1"


class VaultKeyError(ValueError):
    """The vault cannot be sealed or opened. Never carries key or secret bytes."""


class VaultKeyProvider(Protocol):
    """Wraps data keys. Production uses :class:`EnvMasterKeyProvider`."""

    def active_key(self) -> tuple[str, bytes]:
        """Return ``(key_id, 32-byte master key)`` for new envelopes."""

    def key_by_id(self, key_id: str) -> bytes:
        """Return the 32-byte master key that wrapped an existing data key."""


_provider: VaultKeyProvider | None = None


def set_vault_key_provider(provider: VaultKeyProvider | None) -> None:
    """Install *provider*, or restore the environment provider when ``None``."""

    global _provider
    _provider = provider


def get_vault_key_provider() -> VaultKeyProvider:
    if _provider is not None:
        return _provider
    return EnvMasterKeyProvider()


def require_active_key() -> None:
    """Raise :class:`VaultKeyError` when no master key is configured."""

    get_vault_key_provider().active_key()


class EnvMasterKeyProvider:
    """Master keys from the daemon environment. Reads on each call.

    A managed store replaces this object via :func:`set_vault_key_provider`.
    There is no ephemeral key and no plaintext fallback.
    """

    def active_key(self) -> tuple[str, bytes]:
        raw = os.environ.get(MASTER_KEY_ENV, "")
        if not raw.strip():
            raise VaultKeyError(
                f"{MASTER_KEY_ENV} is not configured; "
                "credential vault access is refused"
            )
        key = _decode_key(raw)
        if key is None:
            raise VaultKeyError(
                f"{MASTER_KEY_ENV} is not a valid key; expected 32 bytes "
                "as base64url or 64-char hex"
            )
        key_id = os.environ.get(MASTER_KEY_ID_ENV, "").strip() or _DEFAULT_KEY_ID
        if not _valid_key_id(key_id):
            raise VaultKeyError(f"{MASTER_KEY_ID_ENV} is not a valid key id")
        previous = _previous_keys()
        prior = previous.get(key_id)
        if prior is not None and not secrets.compare_digest(prior, key):
            raise VaultKeyError(
                f"{MASTER_KEY_ID_ENV} disagrees with {PREVIOUS_KEYS_ENV}"
            )
        return key_id, key

    def key_by_id(self, key_id: str) -> bytes:
        if not _valid_key_id(key_id):
            raise VaultKeyError("credential vault master key id is not available")
        active_id, active = self.active_key()
        if key_id == active_id:
            return active
        prior = _previous_keys().get(key_id)
        if prior is None:
            raise VaultKeyError(
                f"credential vault master key id {key_id!r} is not available"
            )
        return prior


def is_envelope(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("format") == ENVELOPE_FORMAT


def seal_document(document: Mapping[str, Any], *, universe_name: str) -> str:
    """Seal *document*. Raise when the result does not decrypt to the same bytes.

    The caller writes the returned text only after this returns.
    """

    plaintext = _canonical_plaintext(document)
    token = _seal_plaintext(plaintext, universe_name=universe_name)
    opened = decrypt_envelope(json.loads(token), universe_name=universe_name)
    if len(opened) != len(plaintext) or not secrets.compare_digest(opened, plaintext):
        raise VaultKeyError(
            "credential vault encryption verification failed; "
            "the file was not replaced"
        )
    return token


def open_document(payload: Mapping[str, Any], *, universe_name: str) -> Any:
    """Return the credential document. Authentication failure raises ValueError."""

    raw = decrypt_envelope(payload, universe_name=universe_name)
    text = None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None:
        raise ValueError("credential vault authentication failed")
    parsed = None
    invalid = False
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        invalid = True
    if invalid:
        raise ValueError("credential vault authentication failed")
    return parsed


def decrypt_envelope(payload: Mapping[str, Any], *, universe_name: str) -> bytes:
    """Decrypt an envelope to the canonical document bytes."""

    if payload.get("format") != ENVELOPE_FORMAT:
        raise ValueError("credential vault envelope is incomplete")
    version = payload.get("version")
    if version != ENVELOPE_VERSION:
        raise ValueError("credential vault envelope version is not supported")
    key_id = payload.get("key_id")
    if not isinstance(key_id, str) or not _valid_key_id(key_id):
        raise ValueError("credential vault envelope is incomplete")
    dek_nonce = _decode_nonce(payload.get("dek_nonce"))
    data_nonce = _decode_nonce(payload.get("data_nonce"))
    wrapped = _decode_blob(payload.get("wrapped_dek"))
    ciphertext = _decode_blob(payload.get("ciphertext"))
    if dek_nonce is None or data_nonce is None or wrapped is None or ciphertext is None:
        raise ValueError("credential vault envelope is incomplete")
    kek = get_vault_key_provider().key_by_id(key_id)
    dek = _aead_open(kek, dek_nonce, wrapped, _DEK_AAD_PREFIX + key_id.encode("utf-8"))
    if len(dek) != _KEY_BYTES:
        raise ValueError("credential vault authentication failed")
    return _aead_open(dek, data_nonce, ciphertext, _data_aad(universe_name))


def _seal_plaintext(plaintext: bytes, *, universe_name: str) -> str:
    key_id, kek = get_vault_key_provider().active_key()
    dek = secrets.token_bytes(_KEY_BYTES)
    dek_nonce = secrets.token_bytes(_NONCE_BYTES)
    data_nonce = secrets.token_bytes(_NONCE_BYTES)
    wrapped = AESGCM(kek).encrypt(
        dek_nonce, dek, _DEK_AAD_PREFIX + key_id.encode("utf-8"),
    )
    ciphertext = AESGCM(dek).encrypt(data_nonce, plaintext, _data_aad(universe_name))
    payload = {
        "ciphertext": _b64(ciphertext),
        "data_nonce": _b64(data_nonce),
        "dek_nonce": _b64(dek_nonce),
        "format": ENVELOPE_FORMAT,
        "key_id": key_id,
        "version": ENVELOPE_VERSION,
        "wrapped_dek": _b64(wrapped),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _canonical_plaintext(document: Mapping[str, Any]) -> bytes:
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _data_aad(universe_name: str) -> bytes:
    if (
        not isinstance(universe_name, str)
        or not universe_name
        or universe_name in {".", ".."}
        or "\x00" in universe_name
    ):
        raise ValueError("credential vault directory name is required")
    return _DATA_AAD_PREFIX + universe_name.encode("utf-8")


def _valid_key_id(key_id: object) -> bool:
    return isinstance(key_id, str) and _KEY_ID.fullmatch(key_id) is not None


def _previous_keys() -> dict[str, bytes]:
    raw = os.environ.get(PREVIOUS_KEYS_ENV, "").strip()
    if not raw:
        return {}
    ring: dict[str, bytes] = {}
    for part in raw.split(","):
        entry = part.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise VaultKeyError(
                f"{PREVIOUS_KEYS_ENV} has an entry without a key id"
            )
        key_id, material = entry.split("=", 1)
        key_id = key_id.strip()
        if not _valid_key_id(key_id):
            raise VaultKeyError(f"{PREVIOUS_KEYS_ENV} has an invalid key id")
        key = _decode_key(material.strip())
        if key is None:
            raise VaultKeyError(
                f"{PREVIOUS_KEYS_ENV} has a key that is not 32 bytes"
            )
        if key_id in ring:
            raise VaultKeyError(f"{PREVIOUS_KEYS_ENV} repeats a key id")
        ring[key_id] = key
    return ring


def _decode_key(text: str) -> bytes | None:
    raw = text.strip()
    if _HEX_KEY.fullmatch(raw):
        return bytes.fromhex(raw)
    padded = raw + ("=" * ((4 - len(raw) % 4) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        return None
    if len(decoded) != _KEY_BYTES:
        return None
    return decoded


def _decode_nonce(value: Any) -> bytes | None:
    blob = _decode_blob(value)
    if blob is None or len(blob) != _NONCE_BYTES:
        return None
    return blob


def _decode_blob(value: Any) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def _b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode("ascii")


def _aead_open(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    failed = False
    plaintext = b""
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
    except (InvalidTag, ValueError):
        failed = True
    if failed:
        raise ValueError("credential vault authentication failed")
    return plaintext
