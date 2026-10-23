"""Bounded inbound proof primitives shared by router and broker.

The caller selects a binding; its installed profile selects a proof method.
This module does not select an owner, open a credential store, or interpret an
event's message content.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping

MAX_BODY_BYTES = 1024 * 1024
_HEADER = re.compile(r"[a-z0-9][a-z0-9-]{0,127}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class InvalidProfile(ValueError):
    """A profile cannot be used as proof."""


def _header_name(value: object) -> str:
    if not isinstance(value, str) or not _HEADER.fullmatch(value.lower()):
        raise InvalidProfile("invalid proof header")
    return value.lower()


def _headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def validate_profile(profile: Mapping) -> None:
    if not isinstance(profile, Mapping):
        raise InvalidProfile("profile must be a mapping")
    method = profile.get("method")
    if method == "capability":
        if set(profile) != {"method", "digest"} or not isinstance(profile["digest"], str):
            raise InvalidProfile("invalid capability profile")
        if not _HEX.fullmatch(profile["digest"]):
            raise InvalidProfile("invalid capability digest")
    elif method == "token":
        if set(profile) != {"method", "header", "digest"}:
            raise InvalidProfile("invalid token profile")
        _header_name(profile["header"])
        if not isinstance(profile["digest"], str) or not _HEX.fullmatch(profile["digest"]):
            raise InvalidProfile("invalid token digest")
    elif method == "hmac-sha256":
        required = {"method", "signature_header", "encoding", "prefix"}
        allowed = required | {"timestamp_header", "version", "max_skew_seconds"}
        if not required <= set(profile) or not set(profile) <= allowed:
            raise InvalidProfile("invalid HMAC profile")
        _header_name(profile["signature_header"])
        if "timestamp_header" in profile:
            _header_name(profile["timestamp_header"])
        if ("max_skew_seconds" in profile) != ("timestamp_header" in profile):
            raise InvalidProfile("timestamp requires finite skew")
        if profile["encoding"] not in {"hex", "base64"}:
            raise InvalidProfile("invalid HMAC encoding")
        if "version" in profile:
            if (not isinstance(profile["version"], str)
                    or not re.fullmatch(r"[A-Za-z0-9]{1,16}", profile["version"])):
                raise InvalidProfile("invalid HMAC version")
        if (not isinstance(profile["prefix"], str) or len(profile["prefix"]) > 32
                or not profile["prefix"].isascii()):
            raise InvalidProfile("invalid signature prefix")
        if "max_skew_seconds" in profile:
            skew = profile["max_skew_seconds"]
            if isinstance(skew, bool) or not isinstance(skew, int) or not 0 < skew <= 300:
                raise InvalidProfile("invalid HMAC skew")
    else:
        raise InvalidProfile("unsupported verification method")


def verify(profile: Mapping, headers: Mapping[str, str], body: bytes, *,
           token: str | None = None, secret: bytes | None = None,
           now: float | None = None) -> bool:
    """Verify raw bytes. Secret resolution happens in broker custody before this call."""
    validate_profile(profile)
    if len(body) > MAX_BODY_BYTES:
        return False
    method = profile["method"]
    if method == "capability":
        value = token
        expected = profile["digest"]
    elif method == "token":
        value = _headers(headers).get(_header_name(profile["header"]))
        expected = profile["digest"]
    else:
        names = _headers(headers)
        signature = names.get(_header_name(profile["signature_header"]))
        if not signature:
            return False
        timestamp = None
        if "timestamp_header" in profile:
            timestamp = names.get(_header_name(profile["timestamp_header"]))
            if not timestamp or not re.fullmatch(r"[0-9]{1,12}", timestamp):
                return False
            age = abs((time.time() if now is None else now) - int(timestamp))
            if age > profile["max_skew_seconds"]:
                return False
        if secret is None:
            return False
        prefix = profile["prefix"]
        if not signature.startswith(prefix):
            return False
        encoded = signature[len(prefix):]
        try:
            supplied = (bytes.fromhex(encoded) if profile["encoding"] == "hex"
                        else base64.b64decode(encoded, validate=True))
        except (ValueError, binascii.Error):
            return False
        signed = b":".join([*([profile["version"].encode()] if "version" in profile else []),
                             *([timestamp.encode()] if timestamp is not None else []), body])
        return hmac.compare_digest(supplied, hmac.new(secret, signed, hashlib.sha256).digest())
    if value is None or not isinstance(value, str) or len(value) > 4096:
        return False
    return hmac.compare_digest(hashlib.sha256(value.encode()).hexdigest(), expected)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _depth(value, limit: int = 32) -> bool:
    if limit < 0:
        return False
    if isinstance(value, dict):
        return all(_depth(item, limit - 1) for item in value.values())
    if isinstance(value, list):
        return all(_depth(item, limit - 1) for item in value)
    return True


def _json_object(body: bytes, headers: Mapping[str, str]) -> dict | None:
    """Uncompressed UTF-8 JSON object, or None when the envelope cannot be used."""
    if len(body) > MAX_BODY_BYTES:
        return None
    names = _headers(headers)
    if names.get("content-type", "").lower() != "application/json":
        return None
    if names.get("content-encoding", "identity").lower() != "identity":
        return None
    try:
        envelope = json.loads(body.decode("utf-8", "strict"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return None
    if not isinstance(envelope, dict) or not _depth(envelope):
        return None
    return envelope


def setup_challenge(body: bytes, headers: Mapping[str, str]) -> str | None:
    """One-time shared-app URL challenge. Not an installation and not an event."""
    envelope = _json_object(body, headers)
    if envelope is None or envelope.get("type") != "url_verification":
        return None
    challenge = envelope.get("challenge")
    if (not isinstance(challenge, str) or not challenge
            or len(challenge.encode("utf-8")) > 1024):
        return None
    return challenge


def extract_single_installation(body: bytes, headers: Mapping[str, str]) -> tuple[str, str] | None:
    """Fixed shared-app envelope extraction after successful proof only.

    The caller must invoke this solely after HMAC succeeds. Unsupported shapes
    are authenticated no-ops, including shared channels and encoded commands.
    """
    envelope = _json_object(body, headers)
    if envelope is None:
        return None
    if (envelope.get("type") != "event_callback"
            or envelope.get("is_ext_shared_channel") is not False):
        return None
    authorizations = envelope.get("authorizations")
    if not isinstance(authorizations, list) or len(authorizations) != 1:
        return None
    authorization = authorizations[0]
    if not isinstance(authorization, dict):
        return None
    enterprise = authorization.get("is_enterprise_install")
    if type(enterprise) is not bool:
        return None
    field = "enterprise_id" if enterprise else "team_id"
    identity = authorization.get(field)
    if not isinstance(identity, str) or not identity or len(identity.encode("utf-8")) > 256:
        return None
    return ("enterprise" if enterprise else "workspace", identity)


def verify_shared(data_root, binding: str, headers: Mapping[str, str], body: bytes) -> dict:
    """Broker-only shared-app proof, with secret resolved by custody name.

    The router supplies only a binding ID and bytes. The profile, secret and
    installation mapping are all resolved independently inside the broker.
    """
    from pathlib import Path

    from tinyassets.broker.inbound_installations import InstallationAuthority
    from tinyassets.credential_vault import load_credential_vault

    root = Path(data_root)
    authority = InstallationAuthority(root / ".broker" / "inbound-installations.db")
    row = authority.profile(binding)
    if row is None:
        return {"proof": "unknown"}
    records = load_credential_vault(root / ".broker" / "platform")
    matches = [record for record in records
               if record.get("credential_type") == "http"
               and record.get("destination") == row["secret_ref"]]
    if len(matches) != 1 or not isinstance(matches[0].get("token"), str):
        raise RuntimeError("platform signing secret unavailable")
    if not verify(row["profile"], headers, body, secret=matches[0]["token"].encode()):
        return {"proof": "invalid"}
    proof_headers = [row["profile"][key]
                     for key in ("signature_header", "timestamp_header")
                     if key in row["profile"]]
    challenge = setup_challenge(body, headers)
    if challenge is not None:
        return {"proof": "setup", "challenge": challenge, "proof_headers": proof_headers}
    identity = extract_single_installation(body, headers)
    if identity is None:
        return {"proof": "no_op", "proof_headers": proof_headers}
    installation = authority.lookup(*identity, app=binding)
    if installation is None:
        return {"proof": "no_op"}
    return {"proof": "mapped", "owner": installation["owner"],
            "binding": installation["binding"], "revision": installation["revision"],
            "proof_headers": proof_headers}


def request_shared(root, binding: str, headers: Mapping[str, str], body: bytes) -> dict:
    """Router-only broker RPC; no profile or credential travels in this request."""
    import socket
    from pathlib import Path

    from tinyassets import rpc_frames as rf
    from tinyassets.boxes.layout import BROKER_UID
    from tinyassets.broker.server import peer_uid

    if len(body) > MAX_BODY_BYTES:
        raise ValueError("inbound body too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(10)
        channel.connect(str(Path(root) / "broker" / "broker.sock"))
        if peer_uid(channel) != BROKER_UID:
            raise PermissionError("inbound verifier peer is not the broker")
        channel.sendall(rf.control(rf.CONNECTION, {
            "op": "INBOUND_VERIFY", "binding": binding, "headers": dict(headers),
            "body": base64.b64encode(body).decode("ascii"),
        }))
        answer = rf.read_frame_blocking(channel)
    if answer is None or answer.kind != rf.CONTROL or answer.stream != rf.CONNECTION:
        raise RuntimeError("inbound verification unavailable")
    document = answer.control()
    if document.get("op") != "INBOUND_VERIFY_IS":
        raise RuntimeError("inbound verification unavailable")
    return document["result"]
