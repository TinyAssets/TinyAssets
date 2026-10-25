"""The canary's session: an ordinary user's sign-in, kept the way the phone app keeps it.

The canary is an ordinary account. It signs in once through the same
system-browser flow the phone and desktop apps use (``/app/native-sign-in``),
then renews through ``/app/token`` with its rotating session handle, exactly
like a signed-in app. No sign-in path, credential or admission rule exists for
it alone.

CI keeps the handle sealed (AES-GCM, ``TINYASSETS_CANARY_SESSION_KEY``) in the
file named by ``TINYASSETS_CANARY_SESSION_STATE``, which the ``canary-session``
action restores from and saves to the Actions cache. Every renewal rotates the
handle, so the new one is written before the access token is returned. With no
file yet, the sealed seed in ``TINYASSETS_CANARY_SESSION_SEED`` starts the
chain. Each workflow keeps its own chain so two runs never renew one handle.

Start or restart a chain (one ordinary sign-in in the canary's browser)::

    python scripts/canary_session.py begin --pending flow.json   # open the printed link
    python scripts/canary_session.py finish --pending flow.json \
        --link "<Open TinyAssets link>" --set-secret TINYASSETS_CANARY_SESSION_SEED_DEPLOY

Values are never printed; errors are codes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ORIGIN = "https://tinyassets.io"
# Cloudflare refuses urllib's default Python-urllib agent before the daemon sees it.
USER_AGENT = "tinyassets-canary/1.0"
_AAD = b"tinyassets-canary-session-v1"


class CanarySessionError(RuntimeError):
    """A code, never a value."""


def _key() -> bytes:
    raw = os.environ.get("TINYASSETS_CANARY_SESSION_KEY", "")
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except ValueError:
        key = b""
    if len(key) != 32:
        raise CanarySessionError("canary_session_key_missing")
    return key


def seal(handle: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    sealed = nonce + AESGCM(_key()).encrypt(nonce, handle.encode(), _AAD)
    return base64.urlsafe_b64encode(sealed).decode()


def unseal(text: str) -> str:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        sealed = base64.urlsafe_b64decode(text.strip())
        return AESGCM(_key()).decrypt(sealed[:12], sealed[12:], _AAD).decode()
    except (ValueError, InvalidTag):
        raise CanarySessionError("canary_session_unreadable") from None


def configured() -> bool:
    """A key plus a chain to renew: a saved state file or a seed."""
    path = _state_file()
    chain = (path is not None and path.is_file()) or bool(
        os.environ.get("TINYASSETS_CANARY_SESSION_SEED")
    )
    return bool(os.environ.get("TINYASSETS_CANARY_SESSION_KEY")) and chain


def _state_file() -> Path | None:
    path = os.environ.get("TINYASSETS_CANARY_SESSION_STATE", "")
    return Path(path) if path else None


def _post(origin: str, path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        origin + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Origin": origin, "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except ValueError:
            return exc.code, {}
    except (OSError, ValueError):
        raise CanarySessionError("canary_session_unreachable") from None


def _store(handle: str) -> None:
    path = _state_file()
    if path is None:
        raise CanarySessionError("canary_session_state_unset")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(seal(handle), encoding="utf-8")
    os.replace(tmp, path)


def access_token(origin: str = ORIGIN) -> str:
    """Renew like a signed-in app: rotate the handle, store it, return the access token."""
    path = _state_file()
    if path is not None and path.is_file():
        handle = unseal(path.read_text(encoding="utf-8"))
    elif os.environ.get("TINYASSETS_CANARY_SESSION_SEED"):
        handle = unseal(os.environ["TINYASSETS_CANARY_SESSION_SEED"])
    else:
        raise CanarySessionError("canary_session_missing")
    status, body = _post(
        origin, "/app/token", {"grant_type": "refresh_token", "session_ref": handle}
    )
    token = str(body.get("access_token", "")) if status == 200 else ""
    if not token:
        code = str(body.get("error", ""))[:64] or f"http_{status}"
        # Nothing rotated, so the stored handle stays good for the next try.
        # Only a 401 means the session ended; it needs one ordinary sign-in
        # (module docstring), like any user whose session ended.
        raise CanarySessionError(
            ("canary_signed_out: " if status == 401 else "canary_renewal_failed: ") + code
        )
    _store(str(body.get("session_ref") or handle))
    return token


def begin(pending: Path, origin: str = ORIGIN) -> str:
    """Start one ordinary system-browser sign-in; returns the link to open."""
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    # The Android return is an intent:// link a desktop browser leaves on the
    # page, so nothing on this machine redeems the sign-in before we do.
    status, begun = _post(
        origin, "/app/native-sign-in", {"client": "android", "code_challenge": challenge}
    )
    if status != 200 or not begun.get("url"):
        raise CanarySessionError("native_sign_in_refused: " + str(begun.get("error", status))[:64])
    # The flow lives ten minutes; the verifier never leaves this machine.
    pending.write_text(json.dumps({"ref": begun["ref"], "verifier": verifier}), encoding="utf-8")
    return begun["url"]


def finish(pending: Path, link: str, origin: str = ORIGIN) -> str:
    """Redeem the page's "Open TinyAssets" link; returns the sealed seed."""
    # Redeeming spends the sign-in, so a missing key must refuse first.
    _key()
    flow = json.loads(pending.read_text(encoding="utf-8"))
    query = parse_qs(urlsplit(link.strip()).query)
    ref = (query.get("signin") or [""])[0]
    return_secret = (query.get("return_secret") or [""])[0]
    if ref != flow["ref"] or not return_secret:
        raise CanarySessionError("return_link_mismatch")
    status, body = _post(
        origin,
        "/app/token",
        {
            "grant_type": "authorization_code",
            "native_ref": ref,
            "code_verifier": flow["verifier"],
            "return_secret": return_secret,
        },
    )
    handle = str(body.get("session_ref", "")) if status == 200 else ""
    sealed = seal(handle) if handle else ""
    # The flow is spent either way; failing to tidy it must not lose the seed.
    try:
        pending.unlink()
    except OSError:
        pass
    if not sealed:
        raise CanarySessionError("sign_in_exchange_failed: " + str(body.get("error", status))[:64])
    return sealed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--origin", default=ORIGIN)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("begin", help="print the sign-in link to open in the canary's browser")
    start.add_argument("--pending", type=Path, required=True)
    end = sub.add_parser("finish", help="redeem the returned link and store the seed")
    end.add_argument("--pending", type=Path, required=True)
    end.add_argument("--link", required=True, help='the page\'s "Open TinyAssets" link')
    end.add_argument(
        "--set-secret", metavar="NAME", help="store the sealed seed as this production secret"
    )
    sub.add_parser("refresh", help="renew once and store the rotated handle")
    args = parser.parse_args(argv)
    origin = args.origin.rstrip("/")
    try:
        if args.command == "begin":
            print(begin(args.pending, origin))
        elif args.command == "finish":
            sealed = finish(args.pending, args.link, origin)
            if args.set_secret:
                # Production-environment scope: a branch run never sees it.
                subprocess.run(
                    ["gh", "secret", "set", args.set_secret, "--env", "production"],
                    input=sealed.encode(),
                    check=True,
                )
                print("seed stored as production secret " + args.set_secret)
            else:
                print(sealed)
        else:
            access_token(origin)
            print("canary_session=renewed")
    except CanarySessionError as exc:
        print("canary_session_error=" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
