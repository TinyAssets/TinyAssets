"""Sealed-record envelope for the broker credential store.

The fields ``format``, ``version``, ``key_id``, ``nonce`` and ``ciphertext``
are the envelope from the closed vault work, extended with the provider, the
account, the command center, the record id and the generation. Associated
data binds those four so a ciphertext copied to another account, center or
slot does not open.

This module does not hold a key and does not read the environment. The broker
key service supplies the data key.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENVELOPE_FORMAT = "tinyassets-credential-vault-envelope"
ENVELOPE_VERSION = 2
NONCE_BYTES = 12
KEY_BYTES = 32


class EnvelopeError(ValueError):
    """The envelope is incomplete or did not authenticate. No secret bytes."""


def canonical_record(record: Mapping[str, Any]) -> bytes:
    return json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def associated_data(
    *,
    account: str,
    command_center: str,
    record_id: str,
    generation: int,
) -> bytes:
    if (
        not account
        or not command_center
        or not record_id
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
    ):
        raise EnvelopeError("sealed record binding is incomplete")
    return json.dumps(
        ["ta-vault-v2", account, command_center, record_id, generation],
        separators=(",", ":"),
    ).encode("utf-8")


def seal(
    plaintext: bytes,
    *,
    key: bytes,
    key_id: str,
    provider_id: str,
    key_ref: str,
    kek_id: str,
    account: str,
    command_center: str,
    record_id: str,
    generation: int,
    nonce: bytes | None = None,
) -> dict[str, Any]:
    import secrets

    if len(key) != KEY_BYTES:
        raise EnvelopeError("data key is not 32 bytes")
    aad = associated_data(
        account=account,
        command_center=command_center,
        record_id=record_id,
        generation=generation,
    )
    nonce = nonce if nonce is not None else secrets.token_bytes(NONCE_BYTES)
    if len(nonce) != NONCE_BYTES:
        raise EnvelopeError("nonce is not 96 bits")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return {
        "account": account,
        "ciphertext": _b64(ciphertext),
        "command_center": command_center,
        "format": ENVELOPE_FORMAT,
        "generation": generation,
        "kek_id": kek_id,
        "key_id": key_id,
        "key_ref": key_ref,
        "nonce": _b64(nonce),
        "provider_id": provider_id,
        "record_id": record_id,
        "version": ENVELOPE_VERSION,
    }


def open_envelope(payload: Mapping[str, Any], *, key: bytes) -> bytes:
    if payload.get("format") != ENVELOPE_FORMAT or payload.get("version") != ENVELOPE_VERSION:
        raise EnvelopeError("credential vault envelope is not supported")
    nonce = _decode(payload.get("nonce"), NONCE_BYTES)
    ciphertext = _decode(payload.get("ciphertext"), None)
    account = payload.get("account")
    center = payload.get("command_center")
    record_id = payload.get("record_id")
    generation = payload.get("generation")
    if (
        nonce is None
        or ciphertext is None
        or not isinstance(account, str)
        or not isinstance(center, str)
        or not isinstance(record_id, str)
        or isinstance(generation, bool)
        or not isinstance(generation, int)
    ):
        raise EnvelopeError("credential vault envelope is incomplete")
    aad = associated_data(
        account=account,
        command_center=center,
        record_id=record_id,
        generation=generation,
    )
    if len(key) != KEY_BYTES:
        raise EnvelopeError("credential vault authentication failed")
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except (InvalidTag, ValueError):
        raise EnvelopeError("credential vault authentication failed") from None


def _b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode("ascii")


def _decode(value: Any, exact: int | None) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        blob = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if exact is not None and len(blob) != exact:
        return None
    return blob
