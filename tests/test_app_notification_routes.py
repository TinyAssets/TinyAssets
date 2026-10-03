"""The app's notification routes, through the real Starlette app.

These exist because the delivery surface is only as safe as the routes that
reach it. Two things they pin:

* **every app route is identity-gated**, enumerated from the real route table
  rather than from a list in the test, so a route added later is covered
  automatically or this goes red;
* **the service worker is the one exception**, by exact equality, because a
  browser fetches it with no bearer -- and it carries no secret, which is what
  makes the exception safe rather than merely necessary.

Driven through `create_streamable_http_app`, so the auth middleware is the real
one. Nothing about the challenge decision is substituted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ALICE = "workos|alice-routes"
BOB = "workos|bob-routes"

#: The app routes that are PUBLIC on purpose, each for a reason stated at its
#: carve-out in `tinyassets/auth/middleware.py`. Anything else under /app must
#: challenge. A new entry here is a deliberate decision, not a default.
PUBLIC_APP_PATHS = {
    "/app",              # the SPA shell, loads before sign-in
    "/app/token",        # its same-origin PKCE exchange
    "/app/billing/webhook",  # Stripe POSTs with no bearer
    # The OAuth return lands in the browser with no bearer; only the shell is
    # open and the authenticated exchange after it is still challenged.
    "/app/model-callback/connect",
    # The authorization server fetches the OAuth client metadata document without a bearer.
    "/app/oauth/client-metadata.json",
    "/app/sw.js",        # the browser fetches a service worker with no bearer
    "/app/ui-frame",     # an <iframe src> carries no bearer; the frame holds no identity
    "/app/m/b0/main.js",  # the app's ES modules load before sign-in; static, no identity
}


def test_the_ui_frame_carve_out_is_exactly_one_path(app_env):
    """Live 2026-10-01: every custom UI rendered `authentication_required`
    because the iframe navigation carries no bearer. Exactly the bootstrap opens."""
    from tinyassets.auth.middleware import _auth_challenge_path

    assert not _auth_challenge_path("/app/ui-frame")
    for near in ("/app/ui-frame/x", "/app/ui-frames", "/app/ui-frame.js",
                 "/app/ui", "/app/api/ui-frame"):
        assert _auth_challenge_path(near), near


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    for var in ("TINYASSETS_WEBPUSH_VAPID_PRIVATE_KEY",
                "TINYASSETS_WEBPUSH_VAPID_SUBJECT",
                "TINYASSETS_FCM_SERVICE_ACCOUNT_JSON"):
        monkeypatch.delenv(var, raising=False)
    return root


def _app_route_paths() -> list[str]:
    """Every ``/app`` path the real route table serves, templates resolved."""
    from tinyassets.onboarding import onboarding_routes

    paths = []
    for route in onboarding_routes():
        path = getattr(route, "path", "")
        if not path.startswith("/app"):
            continue
        paths.append(
            path.replace("{operation}", "probe").replace("{flow}", "connect")
            .replace("{build}", "b0").replace("{name}", "main.js")
        )
    return paths


# --- the negative test ---------------------------------------------------------


def test_every_app_route_is_identity_gated_except_the_named_public_ones(app_env):
    """Enumerated from the ROUTE TABLE, so a route added later is covered here
    without anyone remembering to add it. `_is_app_path` is a segment-boundary
    rule rather than a list, so a new `/app/...` route is challenged by
    default -- this is what proves that, and what would catch a carve-out
    added by accident."""
    from tinyassets.auth.middleware import _auth_challenge_path

    served = _app_route_paths()
    assert "/app/devices" in served and "/app/notify" in served

    for path in served:
        challenged = _auth_challenge_path(path)
        if path in PUBLIC_APP_PATHS:
            assert not challenged, f"{path} should be public"
        else:
            assert challenged, f"{path} answers anonymously"


def test_the_service_worker_carve_out_is_exactly_one_path(app_env):
    """A prefix carve-out would have opened the whole subtree."""
    from tinyassets.auth.middleware import _auth_challenge_path

    assert not _auth_challenge_path("/app/sw.js")
    for near in ("/app/sw.js/x", "/app/sw.jsx", "/app/swx.js", "/app/sw",
                 "/app/sw.js.map", "/app/devices/sw.js"):
        assert _auth_challenge_path(near), near


def test_the_client_metadata_carve_out_is_exactly_one_path(app_env):
    from tinyassets.auth.middleware import _auth_challenge_path

    assert not _auth_challenge_path("/app/oauth/client-metadata.json")
    for near in ("/app/oauth/client-metadata.json/x", "/app/oauth/client-metadata.jsonx",
                 "/app/oauth"):
        assert _auth_challenge_path(near), near


def test_the_service_worker_carries_no_secret(app_env, monkeypatch):
    """What makes the carve-out safe rather than merely necessary.

    Asserted as a PROPERTY, not by scanning for words: with web push fully
    configured, serve the worker and check that neither the private key nor
    the public one appears in it, and that it holds no high-entropy run at
    all. Word-matching would have failed on the comment that says the worker
    carries no key, and passed on a key that happened not to contain them.
    """
    import re

    from tinyassets.onboarding.notifications import handle_service_worker

    values = _webpush_env()
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    _status, _payload, response = _call(handle_service_worker, "GET")
    served = bytes(response.body).decode("utf-8")

    private = values["TINYASSETS_WEBPUSH_VAPID_PRIVATE_KEY"]
    public = values["TINYASSETS_WEBPUSH_VAPID_PUBLIC_KEY"]
    assert public not in served
    for line in private.splitlines():
        if line and "-----" not in line:
            assert line not in served
    # No credential-shaped run of any kind: the same heuristic the request
    # surface uses to refuse a pasted secret.
    assert not re.search(r"[A-Za-z0-9_\-]{32,}", served)


# --- registration is owner-scoped ---------------------------------------------


class _Request:
    """The HTTP envelope, and nothing else.

    The fake stops at the transport: method, path and body. The IDENTITY is
    real -- the handler reads it through `current_identity_or_none`, driven by
    a real `identity_context` -- because that is the thing being tested and
    substituting it would prove nothing about the gate.

    Deliberately not the middleware's TestClient: the middleware resolves the
    identity from its OWN auth provider, which discards an outer
    `identity_context`, so a per-request owner switch through it is a no-op
    and every test would run as one subject. The challenge decisions above are
    tested against the real `_auth_challenge_path` instead.
    """

    def __init__(self, method: str, body: object = None) -> None:
        self.method = method
        self._body = b"" if body is None else json.dumps(body).encode("utf-8")

    async def body(self) -> bytes:
        return self._body


def _call(handler, method: str, body: object = None):
    """Run one handler and return ``(status, decoded json)``."""
    import anyio

    response = anyio.run(handler, _Request(method, body))
    payload = bytes(response.body)
    try:
        decoded = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        decoded = {"_text": payload.decode("utf-8", "replace")}
    return response.status_code, decoded, response


def _as(owner: str):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    return identity_context(Identity(
        user_id=owner, username=owner,
        capabilities=["tinyassets.universe.write"],
    ))


def _webpush_env() -> dict[str, str]:
    """A real throwaway VAPID keypair, from the script that ships it."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    try:
        from webpush_keys import generate
    finally:
        sys.path.pop(0)
    return generate("mailto:ops@example.com")


def _browser_subscription(label: str = "alice-browser") -> dict:
    """A subscription with a real P-256 key, as a browser mints one."""
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


def test_registering_without_an_identity_is_refused(app_env, nobody):
    from tinyassets.onboarding.notifications import handle_devices

    status, payload, _ = _call(handle_devices, "POST", {
        "platform": "android", "token": "t",
    })

    assert status == 401
    assert payload["error"] == "authentication_required"


def test_reading_devices_without_an_identity_is_refused(app_env, nobody):
    from tinyassets.onboarding.notifications import handle_devices

    status, payload, _ = _call(handle_devices, "GET")

    assert status == 401
    assert payload["error"] == "authentication_required"


def test_the_switch_without_an_identity_is_refused(app_env, nobody):
    from tinyassets.onboarding.notifications import handle_notify_settings

    status, payload, _ = _call(handle_notify_settings, "POST", {"enabled": False})

    assert status == 401
    assert payload["error"] == "authentication_required"


def test_a_device_registers_and_lists_without_its_token(app_env):
    from tinyassets.onboarding.notifications import handle_devices

    with _as(ALICE):
        created_status, created, _ = _call(handle_devices, "POST", {
            "platform": "android", "token": "alice-phone-token", "label": "Pixel",
        })
        _status, listed, _ = _call(handle_devices, "GET")

    assert created_status == 200, created
    assert created["device_id"].startswith("dev_")
    assert [d["label"] for d in listed["devices"]] == ["Pixel"]
    assert listed["notifications_enabled"] is True
    assert "alice-phone-token" not in json.dumps(listed)


def test_a_payload_cannot_name_another_subject(app_env):
    """The owner is the authenticated subject; a body field is ignored."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.onboarding.notifications import handle_devices
    from tinyassets.storage import owner_devices as devices

    with _as(ALICE):
        _call(handle_devices, "POST", {
            "platform": "android", "token": "alice-phone-token",
            "owner_user_id": BOB, "owner_sub": BOB, "device_id": "dev_forged",
        })
        base = _base_path()

    assert len(devices.list_devices(base, owner_user_id=ALICE)) == 1
    assert devices.list_devices(base, owner_user_id=BOB) == []


def test_one_owner_never_sees_or_retires_anothers_device(app_env):
    from tinyassets.onboarding.notifications import handle_devices

    with _as(BOB):
        _status, bobs, _ = _call(handle_devices, "POST", {
            "platform": "android", "token": "bob-phone-token",
        })
    with _as(ALICE):
        _s1, listed, _ = _call(handle_devices, "GET")
        _s2, retired, _ = _call(handle_devices, "POST", {
            "operation": "retire", "device_id": bobs["device_id"],
        })

    assert listed["devices"] == []
    # Not-found and not-yours are the same answer, so this cannot probe ids.
    assert retired == {"retired": False}
    with _as(BOB):
        _s3, bob_sees, _ = _call(handle_devices, "GET")
    assert bob_sees["devices"][0]["enabled"] is True


def test_an_owner_retires_their_own_device(app_env):
    from tinyassets.onboarding.notifications import handle_devices

    with _as(ALICE):
        _s, created, _ = _call(handle_devices, "POST", {
            "platform": "android", "token": "alice-phone-token",
        })
        _s2, retired, _ = _call(handle_devices, "POST", {
            "operation": "retire", "device_id": created["device_id"],
        })
        _s3, listed, _ = _call(handle_devices, "GET")

    assert retired == {"retired": True}
    assert listed["devices"][0]["enabled"] is False
    assert listed["devices"][0]["retired_reason"] == "owner"


@pytest.mark.parametrize("body", [
    {"platform": "ios", "token": "t"},
    {"platform": "android", "token": ""},
    {"platform": "android"},
    {"platform": "web", "token": "not-a-subscription"},
    {"platform": "web", "token": {"endpoint": "http://x/y",
                                  "keys": {"p256dh": "k", "auth": "a"}}},
    {"platform": "android", "token": {"not": "a string"}},
])
def test_a_bad_registration_is_refused_with_a_reason(app_env, body):
    from tinyassets.onboarding.notifications import handle_devices

    with _as(ALICE):
        status, payload, _ = _call(handle_devices, "POST", body)

    assert status == 400, payload
    assert payload["error"]


def test_an_oversized_body_is_refused(app_env):
    from tinyassets.onboarding.notifications import MAX_BODY_BYTES, handle_devices

    with _as(ALICE):
        status, payload, _ = _call(handle_devices, "POST", {
            "platform": "android", "token": "x" * (MAX_BODY_BYTES + 100),
        })

    assert status == 400
    assert payload["error"]


def test_the_owner_turns_notifications_off_and_on(app_env):
    from tinyassets.api.helpers import _base_path
    from tinyassets.onboarding.notifications import (
        handle_devices,
        handle_notify_settings,
    )
    from tinyassets.storage import owner_devices as devices

    with _as(ALICE):
        _call(handle_devices, "POST", {
            "platform": "android", "token": "alice-phone-token",
        })
        _s, off, _ = _call(handle_notify_settings, "POST", {"enabled": False})
        _s2, read, _ = _call(handle_notify_settings, "GET")
        base = _base_path()

    assert off == {"enabled": False}
    assert read["enabled"] is False
    # And the off switch actually stops delivery, not just the read.
    assert devices.delivery_targets(base, owner_user_id=ALICE) == []

    with _as(ALICE):
        _call(handle_notify_settings, "POST", {"enabled": True})
    assert len(devices.delivery_targets(base, owner_user_id=ALICE)) == 1


def test_the_switch_reads_on_until_the_owner_turns_it_off_and_then_stays_off(app_env):
    """The app's automatic prompt keys on this read: ON for an owner who never
    chose, OFF once they did -- and re-registering a device (which the app does
    on every sign-in) never flips it back."""
    from tinyassets.onboarding.notifications import (
        handle_devices,
        handle_notify_settings,
    )

    with _as(ALICE):
        _s, fresh, _ = _call(handle_notify_settings, "GET")
        _call(handle_notify_settings, "POST", {"enabled": False})
        _call(handle_devices, "POST", {"platform": "android", "token": "alice-phone"})
        _s2, after, _ = _call(handle_notify_settings, "GET")

    assert fresh["enabled"] is True
    assert after["enabled"] is False


def test_one_owners_switch_does_not_touch_another(app_env):
    from tinyassets.onboarding.notifications import handle_notify_settings

    with _as(ALICE):
        _call(handle_notify_settings, "POST", {"enabled": False})
    with _as(BOB):
        _s, read, _ = _call(handle_notify_settings, "GET")

    assert read["enabled"] is True


@pytest.mark.parametrize("body", [{"enabled": "yes"}, {"enabled": 1}, {}])
def test_the_switch_refuses_a_non_boolean(app_env, body):
    from tinyassets.onboarding.notifications import handle_notify_settings

    with _as(ALICE):
        status, _payload, _ = _call(handle_notify_settings, "POST", body)

    assert status == 400


# --- what the client needs to subscribe ---------------------------------------


def test_the_public_key_is_absent_when_web_push_is_unconfigured(app_env):
    """The client reads "" as "do not offer this", rather than offering a
    subscription that can never be delivered to."""
    from tinyassets.onboarding.notifications import handle_notify_settings

    with _as(ALICE):
        _s, read, _ = _call(handle_notify_settings, "GET")

    assert read["vapid_public_key"] == ""


def test_the_public_key_is_derived_from_the_configured_private_key(
    app_env, monkeypatch,
):
    """Derived, not read from a second variable, so the two cannot drift."""
    import base64

    from tinyassets.onboarding.notifications import handle_notify_settings

    values = _webpush_env()
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    with _as(ALICE):
        _s, read, _ = _call(handle_notify_settings, "GET")

    served = read["vapid_public_key"]
    assert served == values["TINYASSETS_WEBPUSH_VAPID_PUBLIC_KEY"]
    # It is a P-256 point, which is what `applicationServerKey` must be.
    assert len(base64.urlsafe_b64decode(served + "=" * (-len(served) % 4))) == 65


def test_an_unusable_private_key_offers_no_public_key(app_env, monkeypatch):
    from tinyassets.onboarding.notifications import handle_notify_settings

    monkeypatch.setenv("TINYASSETS_WEBPUSH_VAPID_PRIVATE_KEY", "not-a-key")

    with _as(ALICE):
        _s, read, _ = _call(handle_notify_settings, "GET")

    assert read["vapid_public_key"] == ""


def test_the_service_worker_is_served_with_the_scope_header(app_env):
    from tinyassets.onboarding.notifications import handle_service_worker

    _status, _payload, response = _call(handle_service_worker, "GET")

    assert response.status_code == 200
    assert response.headers["service-worker-allowed"] == "/app"
    assert "application/javascript" in response.headers["content-type"]
    text = bytes(response.body).decode("utf-8")
    assert "notificationclick" in text
    assert "request=" in text  # the click lands on the request


def test_a_registered_web_device_can_actually_be_delivered_to(app_env, monkeypatch):
    """The end of the chain this PR exists for: a subscription registered
    THROUGH the route is a destination the real transport can send to."""
    import urllib.request

    from tinyassets.api.helpers import _base_path
    from tinyassets.notify import Notification
    from tinyassets.notify.webpush import webpush_transport
    from tinyassets.onboarding.notifications import handle_devices
    from tinyassets.storage import owner_devices as devices

    values = _webpush_env()
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    with _as(ALICE):
        status, created, _ = _call(handle_devices, "POST", {
            "platform": "web", "token": _browser_subscription(),
        })
        base = _base_path()
    assert status == 200, created

    [target] = devices.delivery_targets(base, owner_user_id=ALICE)
    sent = []

    class _Sent:
        def read(self, _n: int = -1) -> bytes:
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> bool:
            return False

    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda request, timeout=None: (sent.append(request.full_url), _Sent())[1],
    )
    transport = webpush_transport({
        "TINYASSETS_WEBPUSH_VAPID_PRIVATE_KEY":
            values["TINYASSETS_WEBPUSH_VAPID_PRIVATE_KEY"],
        "TINYASSETS_WEBPUSH_VAPID_SUBJECT":
            values["TINYASSETS_WEBPUSH_VAPID_SUBJECT"],
    })

    assert transport(target, Notification(
        title="Alice's universe asks", body="TODO: Today",
        data={"kind": "request", "request_id": "req_abc"},
    )) == "sent"
    assert sent == ["https://push.example.com/alice-browser"]
