"""Root key seam. Production is AWS KMS. Tests use an in-memory provider.

The root never leaves the provider. There is no application-held master key
and no offline copy. The in-memory provider is refused when
``TINYASSETS_ENV=production``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib import error as urlerror
from urllib import request as urlrequest

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PURPOSE = "account-kek"


class RootKeyError(RuntimeError):
    """The root key cannot be used. The message never contains key material."""


class KeyServiceUnavailable(RootKeyError):
    """KMS is unreachable or refused the credential. Retryable. Not a reconnect."""

    reason = "key_service_unavailable"


class ReconnectRequired(RootKeyError):
    """This one record cannot be opened. Other records stay."""

    reason = "reconnect_required"


class RootKeyProvider(Protocol):
    provider_id: str
    key_ref: str

    def generate(self, context: Mapping[str, str]) -> tuple[bytes, bytes]:
        """Return ``(plaintext account KEK, wrapped blob)``."""

    def unwrap(self, blob: bytes, context: Mapping[str, str]) -> bytes:
        """Return the plaintext KEK. Wrong context or blob fails closed."""

    def wrap(self, kek: bytes, context: Mapping[str, str]) -> bytes:
        """Rewrap an existing KEK under the current root material."""


def encryption_context(account: str) -> dict[str, str]:
    if not account or not isinstance(account, str):
        raise RootKeyError("account digest is required")
    return {"ta_purpose": PURPOSE, "ta_account": account}


def _require_context(context: Mapping[str, str]) -> dict[str, str]:
    if (
        not isinstance(context, Mapping)
        or set(context) != {"ta_purpose", "ta_account"}
        or context.get("ta_purpose") != PURPOSE
        or not isinstance(context.get("ta_account"), str)
        or not context["ta_account"]
    ):
        raise RootKeyError("vault encryption context is incomplete")
    return {"ta_purpose": PURPOSE, "ta_account": context["ta_account"]}


class MemoryRootKey:
    """Process-local root. Refused in production. Never written to disk."""

    provider_id = "memory"
    key_ref = "memory-test"

    def __init__(self) -> None:
        if os.environ.get("TINYASSETS_ENV") == "production":
            raise RootKeyError("in-memory vault provider is refused in production")
        self._root = AESGCM.generate_key(bit_length=256)
        self._retired: list[bytes] = []
        self.fail = False

    def generate(self, context: Mapping[str, str]) -> tuple[bytes, bytes]:
        kek = AESGCM.generate_key(bit_length=256)
        return kek, self.wrap(kek, context)

    def wrap(self, kek: bytes, context: Mapping[str, str]) -> bytes:
        self._touch(context)
        if len(kek) != 32:
            raise RootKeyError("account key is not 32 bytes")
        nonce = os.urandom(12)
        return nonce + AESGCM(self._root).encrypt(nonce, kek, _context_aad(context))

    def unwrap(
        self, blob: bytes, context: Mapping[str, str], *, active_only: bool = False
    ) -> bytes:
        self._touch(context)
        roots = (self._root,) if active_only else (self._root, *reversed(self._retired))
        aad = _context_aad(context)
        for root in roots:
            if len(blob) <= 12:
                break
            try:
                opened = AESGCM(root).decrypt(blob[:12], blob[12:], aad)
            except (InvalidTag, ValueError):
                continue
            if len(opened) == 32:
                return opened
        raise ReconnectRequired("wrapped account key did not authenticate")

    def rotate(self) -> None:
        self._retired.append(self._root)
        self._root = AESGCM.generate_key(bit_length=256)

    def drop_retired(self) -> None:
        self._retired.clear()

    def _touch(self, context: Mapping[str, str]) -> None:
        _require_context(context)
        if self.fail:
            raise KeyServiceUnavailable("kms unreachable")


def _context_aad(context: Mapping[str, str]) -> bytes:
    bound = _require_context(context)
    return json.dumps(
        ["ta-root-v1", bound["ta_purpose"], bound["ta_account"]],
        separators=(",", ":"),
    ).encode("utf-8")


class AwsKmsRootKey:
    """AWS KMS adapter. No other module names AWS."""

    provider_id = "aws-kms"

    def __init__(
        self,
        *,
        key_arn: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "us-west-2",
        transport: Callable[[str, dict[str, str], bytes], dict[str, Any]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not key_arn.startswith("arn:aws:kms:"):
            raise RootKeyError("vault KMS key ARN is not configured")
        if not access_key_id or not secret_access_key:
            raise RootKeyError("vault KMS credential is not configured")
        self.key_ref = key_arn
        self._access_key_id = access_key_id
        self._secret = secret_access_key
        self._region = region
        self._transport = transport or _urllib_post
        self._now = now or (lambda: datetime.now(timezone.utc))

    def generate(self, context: Mapping[str, str]) -> tuple[bytes, bytes]:
        body = self._call(
            "GenerateDataKey",
            {
                "KeyId": self.key_ref,
                "KeySpec": "AES_256",
                "EncryptionContext": _require_context(context),
            },
        )
        plaintext = _b64_field(body, "Plaintext")
        blob = _b64_field(body, "CiphertextBlob")
        if plaintext is None or blob is None or len(plaintext) != 32:
            raise KeyServiceUnavailable("kms data key response was refused")
        return plaintext, blob

    def unwrap(self, blob: bytes, context: Mapping[str, str]) -> bytes:
        body = self._call(
            "Decrypt",
            {
                "CiphertextBlob": base64.b64encode(blob).decode("ascii"),
                "EncryptionContext": _require_context(context),
            },
        )
        plaintext = _b64_field(body, "Plaintext")
        if plaintext is None or len(plaintext) != 32:
            raise ReconnectRequired("wrapped account key did not authenticate")
        return plaintext

    def wrap(self, kek: bytes, context: Mapping[str, str]) -> bytes:
        if len(kek) != 32:
            raise RootKeyError("account key is not 32 bytes")
        body = self._call(
            "Encrypt",
            {
                "KeyId": self.key_ref,
                "Plaintext": base64.b64encode(kek).decode("ascii"),
                "EncryptionContext": _require_context(context),
            },
        )
        blob = _b64_field(body, "CiphertextBlob")
        if blob is None:
            raise KeyServiceUnavailable("kms encrypt response was refused")
        return blob

    def _call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        host = f"kms.{self._region}.amazonaws.com"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "content-type": "application/x-amz-json-1.1",
            "host": host,
            "x-amz-date": amzdate,
            "x-amz-target": f"TrentService.{action}",
        }
        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
        payload_hash = hashlib.sha256(body).hexdigest()
        canonical = "\n".join(["POST", "/", "", canonical_headers, signed_headers, payload_hash])
        scope = f"{datestamp}/{self._region}/kms/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amzdate,
                scope,
                hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            ]
        )
        signature = hmac.new(
            _signing_key(self._secret, datestamp, self._region),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self._access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        try:
            return self._transport(f"https://{host}/", headers, body)
        except KeyServiceUnavailable:
            raise
        except ReconnectRequired:
            raise
        except (OSError, TimeoutError, urlerror.URLError) as exc:
            raise KeyServiceUnavailable("kms unreachable") from exc


def _signing_key(secret: str, datestamp: str, region: str) -> bytes:
    def _hmac(key: bytes, value: str) -> bytes:
        return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()

    dated = _hmac(("AWS4" + secret).encode("utf-8"), datestamp)
    regional = _hmac(dated, region)
    service = _hmac(regional, "kms")
    return _hmac(service, "aws4_request")


def _b64_field(body: Mapping[str, Any], name: str) -> bytes | None:
    raw = body.get(name)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return base64.b64decode(raw)
    except (ValueError, TypeError):
        return None


def _urllib_post(url: str, headers: dict[str, str], body: bytes) -> dict[str, Any]:
    req = urlrequest.Request(url, data=body, headers=headers, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if "InvalidCiphertext" in detail or "IncorrectKey" in detail:
            raise ReconnectRequired("wrapped account key did not authenticate") from None
        raise KeyServiceUnavailable("kms refused the vault credential") from None
    if not isinstance(payload, dict):
        raise KeyServiceUnavailable("kms response was refused")
    return payload

