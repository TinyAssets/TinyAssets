"""A request reaches its OWNER's devices, and nobody else's.

Driven through the real handlers and real stores: a request raised by the real
``request_from_user``, devices in the real registry, and -- for the transport
tests -- the real FCM transport built from a real (throwaway) service-account
key, with only the HTTP socket replaced. The fake is the network, never the
authority check, the composition or the idempotency.

The properties these exist to hold:

* a notification is routed by the OWNER of the universe holding the request,
  and by nothing a caller, a payload or the request's own content says;
* a handset that changes accounts stops receiving the old account's
  notifications;
* the identity line is the platform's and the body is the agent's, so no ask
  can be composed to look like it came from the platform or another person;
* nothing the owner typed into a request comes back out in a payload;
* one notification per request per device, and an unconfigured deployment says
  so instead of claiming a delivery.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets.api import visibility as vis
from tinyassets.daemon_server import (
    claim_founder_home,
    ensure_universe_registered,
    grant_universe_access,
    set_universe_display_name,
)
from tinyassets.notify import (
    OUTCOME_SENT,
    Notification,
)
from tinyassets.storage import owner_devices as devices

ALICE = "workos|alice-notify"
BOB = "workos|bob-notify"
A_UID = "u-alice-notify"
B_UID = "u-bob-notify"
A_NAME = "Alice's universe"


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.delenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", raising=False)
    for var in (
        "TINYASSETS_FCM_SERVICE_ACCOUNT_JSON",
        "TINYASSETS_WEBPUSH_VAPID_PRIVATE_KEY",
        "TINYASSETS_WEBPUSH_VAPID_SUBJECT",
    ):
        monkeypatch.delenv(var, raising=False)
    return root


def _home(base: Path, uid: str, owner: str, name: str = "") -> Path:
    udir = base / uid
    udir.mkdir(parents=True, exist_ok=True)
    (udir / "soul.md").write_text(f"# {uid}\n", encoding="utf-8")
    ensure_universe_registered(base, universe_id=uid, universe_path=udir)
    grant_universe_access(
        base, universe_id=uid, actor_id=owner, permission="admin", granted_by=owner,
    )
    claim_founder_home(base, owner, uid)
    vis.set_universe_visibility(uid, "private", source="default")
    if name:
        set_universe_display_name(base, universe_id=uid, display_name=name)
    return udir


class _Recorder:
    """A transport that records what it was handed. Never a network."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[tuple[dict, Notification]] = []
        self._raises = raises

    def __call__(self, device: dict, notification: Notification) -> str:
        self.calls.append((device, notification))
        if self._raises is not None:
            raise self._raises
        return OUTCOME_SENT


def _fake(raises: Exception | None = None) -> tuple[_Recorder, dict]:
    recorder = _Recorder(raises)
    return recorder, {"android": recorder, "web": recorder}


def _vapid_keypair() -> tuple[str, str]:
    """A throwaway VAPID key, for tests that must cross the real transport."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return pem, "mailto:ops@example.com"


def _web_subscription(label: str) -> dict:
    """A subscription with a real P-256 public key, as a browser mints."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    client = ec.generate_private_key(ec.SECP256R1())
    public = client.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return {
        "endpoint": f"https://push.example.com/{label}",
        "keys": {"p256dh": b64(public), "auth": b64(b"0123456789abcdef")},
    }


def _register(base: Path, owner: str, token: str, platform: str = "android") -> str:
    """Register one device. A web device needs a real subscription shape, so a
    bare label is turned into one keyed on itself -- with a REAL P-256 key, so
    the row works for a test that crosses the real transport."""
    if platform == "web" and isinstance(token, str):
        token = _web_subscription(token)
    return devices.register_device(
        base, owner_user_id=owner, platform=platform, token=token,
    )["device_id"]


def _raise_request(base: Path, uid: str, owner: str, transports: dict, **overrides):
    """A request, then dispatch, as the seam does -- with the network replaced."""
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.owner_notifications import notify_request_raised

    document = {
        "kind": "TODO", "title": "Today", "body": "A few things.",
        "action": {"type": "answer"},
        "fields": [{"name": "note", "type": "text", "label": "Reply"}],
    }
    document.update(overrides)
    with identity_context(Identity(user_id=owner, username=owner)):
        from tinyassets.api.pending_requests import request_from_user

        row = request_from_user(universe_id=uid, payload=document)
        assert "error" not in row, row
        result = notify_request_raised(
            base, universe_id=uid, raised_by=owner, request=row,
            transports=transports,
        )
    return row, result


# --- the routing key ----------------------------------------------------------












# --- the handset that changed accounts ----------------------------------------










# --- what it is allowed to say ------------------------------------------------






























# --- cost, without a meter ----------------------------------------------------


















# --- failure ------------------------------------------------------------------














# --- answering here clears there ----------------------------------------------























# --- account deletion ---------------------------------------------------------








# --- registration arguments ---------------------------------------------------






_SUBSCRIPTION_ALIASES = [
    # Metadata the browser attaches and the transport never reads.
    {"endpoint": "https://push.example.com/abc",
     "keys": {"p256dh": "k", "auth": "a"}, "expirationTime": None},
    # A vendor extra.
    {"endpoint": "https://push.example.com/abc",
     "keys": {"p256dh": "k", "auth": "a"}, "vendorHint": "x"},
    # Whitespace in the surrounding JSON string.
    '{"endpoint": "https://push.example.com/abc",\n'
    ' "keys": {"p256dh": "k", "auth": "a"}}',
    # Rotated keys, same endpoint: the same browser re-subscribing.
    {"endpoint": "https://push.example.com/abc",
     "keys": {"p256dh": "k2", "auth": "a2"}},
]






_URL_ALIASES = [
    # A fragment never leaves the client: urllib sends host + selector only.
    "https://push.example.com/abc#bob",
    "https://push.example.com/abc#",
    # Host case is not significant.
    "https://PUSH.EXAMPLE.COM/abc",
    "https://Push.Example.Com/abc",
    # The default https port is the same port.
    "https://push.example.com:443/abc",
]














# --- the upgrade path is part of the floor ------------------------------------


def _write_legacy_device(base, owner: str, platform: str, token, digest: str) -> str:
    """A device row as a PREVIOUS version stored it: correct token, stale
    destination digest. Written directly, because the point is that the code
    which computes the digest has changed underneath it."""
    import sqlite3
    import time as _time
    import uuid as _uuid

    from tinyassets.storage.owner_devices import _SCHEMA, owner_devices_db_path

    device_id = "dev_" + _uuid.uuid4().hex[:24]
    stored = token if isinstance(token, str) else json.dumps(
        token, sort_keys=True, separators=(",", ":"),
    )
    conn = sqlite3.connect(owner_devices_db_path(base))
    try:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO owner_devices (device_id, owner_user_id, platform, "
            "token_sha256, token_json, label, enabled, created_at, last_seen_at) "
            "VALUES (?,?,?,?,?,'',1,?,?)",
            (device_id, owner, platform, digest, stored, _time.time(),
             _time.time()),
        )
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    finally:
        conn.close()
    return device_id


def _legacy_digest(token) -> str:
    """The digest the previous version computed: whole document, no platform."""
    import hashlib

    text = token if isinstance(token, str) else json.dumps(
        token, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()












# --- the destination is re-verified at claim time -----------------------------










# --- one outstanding alert per device -----------------------------------------






# --- the exception boundary ---------------------------------------------------


