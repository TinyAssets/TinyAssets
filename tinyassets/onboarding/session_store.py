"""Sealed server-side store for the app's AuthKit refresh tokens.

The onboarding app hands the browser an opaque *handle* and keeps the WorkOS
refresh token here.

**Sealed at rest.** Every record is AES-GCM ciphertext under a process key. The
AAD binds both the handle and the record kind, so a record copied onto another
handle's filename does not decrypt and a record's kind cannot be flipped by an
editor of the JSON. Authenticated public owner metadata supports revocation.
No credential is outside the ciphertext: neither the token nor the successor
handle a rotation points at. A tombstone naming its successor in plaintext would have
handed a data-dir reader a *directly usable bearer handle*, which is the original
cross-user disclosure wearing a different hat.

**What this does and does not defend.** It defends against a reader of the *data
dir* that is not this process: a stray backup, a detached volume, a container
sidecar, a mounted-in tool, a restored snapshot. It is **not** a boundary against
code executing inside this process — that reader can call ``read()`` itself. That
is a separate, already-filed finding
(``docs/concerns/2026-08-28-user-code-runs-in-process.md``) whose fix is a process
split, and encryption must not be sold as a substitute for it. Likewise the
compose ``worker*`` fleet services mount ``/data`` and copy the environment; they
are being retired under ``openspec/changes/user-owned-automations`` and are
deliberately out of scope here.

**The key never reaches a child.** ``TINYASSETS_SESSION_SEAL_KEY`` is read and
popped from ``os.environ`` at **import time** (see ``arm()`` at the foot of this
module), not on first store use. Lazy loading left the key in the environment for
however long it took the first token request to arrive, and every provider child
spawned in that window inherits it: both
``tinyassets.providers.base.subprocess_env_without_api_keys`` and the no-universe
branch of the provider child env copy ``os.environ`` wholesale.
``tinyassets.universe_server.main()`` calls ``arm()`` as its first statement so
the scrub precedes the app build, the reconciler threads and uvicorn — the
onboarding route being dark (``TINYASSETS_ONBOARDING_APP`` off) must not leave the
key sitting in the environment.

**Handles rotate.** A refresh mints a NEW handle and replaces the old record with
a sealed tombstone naming the successor, expiring after a short grace. So a
captured handle stops working roughly one rotation after capture instead of
lasting the full 7-day TTL, and the retired token is not kept on disk at all.
The old handle follows exactly ONE hop during grace — long enough for a second
tab that lost the rotation race, short of walking a chain. A hop that lands on
another tombstone (two rotations inside the grace window) fails closed, so the
caller falls back to its cookie instead of presenting a token AuthKit retired.

The filename is a keyed digest of the handle, never the handle: the handle is a
bearer credential, so using it as a filename published every live session to
anything that could list the directory.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_LOG = logging.getLogger("tinyassets.onboarding")

#: Exactly 32 bytes, as 43-or-44-char base64url or 64-char hex. Absent ->
#: ephemeral per-process key + one warning. Set but malformed -> RuntimeError.
SEAL_KEY_ENV = "TINYASSETS_SESSION_SEAL_KEY"

#: Under ``.runtime/`` so it sits apart from user-visible universe state.
_STORE_DIRNAME = "app_refresh_sessions"
#: The pre-seal plaintext store. Deleted on sight, never read.
_LEGACY_DIRNAME = "app_refresh_sessions"

#: AuthKit's default maximum session length.
REFRESH_SESSION_TTL = 7 * 24 * 3600
#: How long a superseded handle keeps resolving. Covers a multi-tab rotation
#: race; short enough that a stolen handle dies about a rotation after capture.
GRACE_SECONDS = 120
#: Expired records (tombstones past grace, sessions past TTL) are swept on any
#: store use at most this often, so a rotated-away handle leaves the disk within
#: a minute of its grace ending -- not only at the next restart.
_SWEEP_INTERVAL = 60
#: ``secrets.token_urlsafe(32)`` is 43 url-safe characters.
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
#: Strictly base64url (32 bytes = 43 chars, optional single pad) or hex. NOT
#: standard base64: tolerating ``+``/``/`` also accepted junk like a value
#: containing ``$``, which then silently became a key nobody chose.
_B64_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{43}=?$")
_HEX_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")

_MAX_TOKEN = 4096
_RECORD_VERSION = 2
_KIND_SESSION = "session"
_KIND_TOMBSTONE = "tombstone"

_key: bytes | None = None
_durable_key = False
_initialised: set[str] = set()
_last_sweep: float = 0.0


class SessionStoreUnavailable(RuntimeError):
    """The store cannot be brought to a safe state, so no session may be served.

    Raised when the legacy PLAINTEXT store could not be deleted. Serving through
    a store whose plaintext predecessor is still on disk would quietly keep the
    disclosure this module exists to end, so the endpoint returns 503 and the
    deletion is retried on the next request.
    """


def _reset_for_tests() -> None:
    """Drop the key singleton and the per-directory init memo.

    Tests point ``TINYASSETS_DATA_DIR`` at a fresh tmp dir per test; without this
    the first test's key would seal records the next test cannot open, and the
    one-time legacy discard would be skipped for every directory after the first.
    """
    global _key, _last_sweep, _durable_key
    _key = None
    _durable_key = False
    _last_sweep = 0.0
    _initialised.clear()


# --- the seal key -------------------------------------------------------------


def _decode_key(raw: str) -> bytes | None:
    """32 bytes from base64url or hex, else None."""
    text = raw.strip()
    if _HEX_KEY_RE.match(text):
        return bytes.fromhex(text)
    if _B64_KEY_RE.match(text):
        try:
            decoded = base64.urlsafe_b64decode(text.rstrip("=") + "=")
        except (binascii.Error, ValueError):
            return None
        return decoded if len(decoded) == 32 else None
    return None


def _load_key() -> bytes:
    global _durable_key
    # Pop, do not get: this runs at import, and everything after it in the
    # process — including every provider subprocess — sees an environment
    # without the key. Popping BEFORE the parse means even a rejected value
    # does not survive in the environment.
    raw = os.environ.pop(SEAL_KEY_ENV, "")
    if raw.strip():
        key = _decode_key(raw)
        if key is None:
            # Loud, at startup, before a single session is sealed. The
            # alternative — falling back to an ephemeral key — would look
            # healthy while silently logging every user out on each restart,
            # which is exactly the "mock fallback that looks real" failure.
            raise RuntimeError(
                f"{SEAL_KEY_ENV} is set but is not a valid key: expected 32 "
                "bytes as 43-char base64url (optionally padded) or 64-char hex. "
                "Generate one with: python -c \"import secrets,base64; "
                'print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"'
            )
        _durable_key = True
        return key
    _LOG.warning(
        "%s is not configured: app refresh sessions are sealed with an ephemeral "
        "per-process key and will NOT survive a restart (every signed-in user "
        "re-logs-in). Set %s to 32 bytes, base64url or hex.",
        SEAL_KEY_ENV,
        SEAL_KEY_ENV,
    )
    return secrets.token_bytes(32)


def seal_key() -> bytes:
    """The process seal key. Loaded at import; reloaded only after a test reset."""
    global _key
    if _key is None:
        _key = _load_key()
    return _key


def durable_seal_key() -> bytes:
    """Persistent consumers must not silently accept the ephemeral fallback."""
    key = seal_key()
    if not _durable_key:
        raise SessionStoreUnavailable("a durable app-session seal key is required")
    return key


def arm() -> None:
    """Load the key and scrub it from ``os.environ``. Idempotent, cheap, safe.

    Call this as early as a daemon entrypoint can — the point is to run before
    anything spawns a subprocess. Importing this module already does it; the
    explicit call exists so a daemon that never touches the onboarding routes
    still scrubs the environment.
    """
    seal_key()


# --- paths --------------------------------------------------------------------


def _handle_path_key(handle: str) -> str:
    """The on-disk name for a handle -- a keyed digest of it, never the handle.

    Keyed with the seal key (always present -- ephemeral if unconfigured), so a
    directory listing is not one ``sha256()`` of a guess away from a live bearer
    credential.
    """
    return hmac.new(seal_key(), handle.encode("utf-8"), hashlib.sha256).hexdigest()


def store_dir() -> Path:
    """The sealed store directory, created 0700, legacy plaintext discarded.

    Raises ``SessionStoreUnavailable`` if the legacy plaintext store is present
    and cannot be removed.
    """
    from tinyassets.owner_runtime import refuse_router_store

    refuse_router_store("refresh_sessions")
    from tinyassets.storage import data_dir

    root = data_dir()
    directory = root / ".runtime" / _STORE_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    marker = str(directory)
    if marker not in _initialised:
        # Memoized only AFTER both steps succeed. Marking first would turn one
        # failed deletion into a permanent silent skip: the plaintext store would
        # stay on disk and every later call would sail past it.
        _discard_legacy_store(root)
        _sweep_expired(directory)
        _initialised.add(marker)
    else:
        global _last_sweep
        now = time.time()
        if now - _last_sweep >= _SWEEP_INTERVAL:
            _last_sweep = now
            _sweep_expired(directory)
    return directory


def ensure_available() -> None:
    """Bring the store to a safe state, or raise ``SessionStoreUnavailable``.

    Called once at the top of the token endpoint so the failure surfaces as one
    honest 503 rather than partway through a grant.
    """
    store_dir()


def _record_path(handle: str) -> Path:
    """The ONLY place a record path is built -- one digest call, no exceptions."""
    return store_dir() / f"{_handle_path_key(handle)}.json"


def _discard_legacy_store(root: Path) -> None:
    """Delete the plaintext store outright. Never migrate it.

    Those files hold raw refresh tokens at whatever mode the process umask gave
    them (0644 was observed in production). Reading them to re-seal would mean
    trusting material we already treat as disclosed; the cost of refusing is one
    re-login.
    """
    legacy = root / _LEGACY_DIRNAME
    if not legacy.is_dir():
        return
    import shutil

    try:
        shutil.rmtree(legacy)
    except FileNotFoundError:
        return  # a concurrent caller won the race; the goal is met either way
    except OSError as exc:
        _LOG.error(
            "legacy plaintext refresh sessions could not be removed (%s): %s",
            legacy,
            exc.__class__.__name__,
        )
        raise SessionStoreUnavailable(
            "legacy plaintext refresh session store could not be removed"
        ) from exc
    _LOG.warning("legacy plaintext refresh sessions discarded; users re-login once")


def _sweep_expired(directory: Path) -> None:
    """Drop records past their TTL on first use of the directory.

    ``exp`` is outside the ciphertext precisely so this works after a restart
    with an ephemeral key, when nothing in the directory can be opened and the
    filenames no longer match any live handle. A tombstone's ``exp`` IS its grace
    deadline, so this is also what finally removes rotated-away handles.
    """
    now = int(time.time())
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for path in entries:
        if path.suffix == ".tmp":
            _unlink(path)  # a write that died between write_text and os.replace
            continue
        if path.suffix != ".json":
            continue
        record = _read_json(path)
        if record is None:
            _unlink(path)
            continue
        outer = _int(record.get("exp"))
        if outer is None or outer < now:
            _unlink(path)


# --- record I/O ---------------------------------------------------------------


def _read_json(path: Path) -> dict | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _write_record(handle: str, record: dict) -> None:
    path = _record_path(handle)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    # Narrowed BEFORE the atomic swap, not after: the window in between is the
    # whole problem -- the file must never exist at the umask default.
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _unb64(text: object) -> bytes | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return base64.urlsafe_b64decode(text)
    except (binascii.Error, ValueError):
        return None


def _int(value: object) -> int | None:
    """A JSON integer, or None. ``bool`` is not an int here, nor is a float or a
    numeric string: metadata that is not exactly what we wrote is not ours, and a
    record whose metadata we cannot read fails CLOSED (deleted) instead of raising
    into the request and turning one bad file into a standing 500."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _aad(handle: str, kind: str, owner: str) -> bytes:
    """Authenticate the handle, record kind and revocation owner.

    The handle stops a record being moved to another handle's filename. The kind
    stops a tombstone being relabelled as a session. The owner prevents a record
    being relabelled to escape account revocation.
    """
    return f"{kind}\x00{handle}\x00{owner}".encode("utf-8")


def _seal(handle: str, kind: str, payload: dict, ttl: int, *, owner: str) -> dict:
    """Seal ``payload`` (a JSON object) with its deadline INSIDE the ciphertext.

    The authenticated deadline is ``until``, sealed with the payload; the outer
    ``exp`` is a copy kept in plaintext ONLY so the sweep can age records out
    after a restart that lost an ephemeral key, when nothing decrypts. Readers
    enforce both, so editing the plaintext ``exp`` cannot extend a session or a
    tombstone (Codex, round 3): the deadline that admits a record is the one the
    key signed.
    """
    until = int(time.time()) + ttl
    body = dict(payload)
    body["until"] = until
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(seal_key()).encrypt(
        nonce, json.dumps(body).encode("utf-8"), _aad(handle, kind, owner)
    )
    return {
        "v": _RECORD_VERSION,
        "kind": kind,
        "owner": owner,
        "nonce": _b64(nonce),
        "ct": _b64(ciphertext),
        "exp": until,
    }


def _open(handle: str, record: dict, now: int | None = None) -> dict | None:
    """The sealed payload, or None if this record is not this handle's or its
    authenticated deadline has passed (when ``now`` is given)."""
    if _int(record.get("v")) != _RECORD_VERSION:
        return None
    kind = record.get("kind")
    owner = record.get("owner")
    if not isinstance(owner, str) or not owner:
        return None
    if kind not in (_KIND_SESSION, _KIND_TOMBSTONE):
        return None
    nonce = _unb64(record.get("nonce"))
    ciphertext = _unb64(record.get("ct"))
    if nonce is None or ciphertext is None or len(nonce) != 12:
        return None
    try:
        plaintext = AESGCM(seal_key()).decrypt(nonce, ciphertext, _aad(handle, kind, owner))
    except (InvalidTag, ValueError):
        return None
    try:
        decoded = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    until = _int(decoded.get("until"))
    if until is None or (now is not None and until < now):
        return None
    return decoded


def _open_token(handle: str, record: dict, now: int) -> str:
    """The refresh token in a live session record, or ""."""
    decoded = _open(handle, record, now)
    if decoded is None:
        return ""
    token = decoded.get("rt")
    if not isinstance(token, str):
        return ""
    return token if 0 < len(token) <= _MAX_TOKEN else ""


def _open_successor(handle: str, record: dict, now: int | None = None) -> str:
    """The successor handle in a tombstone, or "". ``now=None`` skips the
    deadline: logout must find the successor even after grace."""
    decoded = _open(handle, record, now)
    if decoded is None:
        return ""
    successor = decoded.get("succ")
    if not isinstance(successor, str) or not valid_handle(successor):
        return ""
    return successor


# --- the store API ------------------------------------------------------------


def valid_handle(handle: str) -> bool:
    return bool(_HANDLE_RE.match(handle or ""))


def mint(refresh_token: str, *, owner: str) -> str:
    """A fresh handle sealing ``refresh_token``, or "" if there is nothing to seal."""
    if not refresh_token or len(refresh_token) > _MAX_TOKEN:
        return ""
    if not isinstance(owner, str) or not owner:
        raise ValueError("refresh session requires a verified owner")
    handle = secrets.token_urlsafe(32)
    _write_record(
        handle,
        _seal(handle, _KIND_SESSION, {"rt": refresh_token}, REFRESH_SESSION_TTL, owner=owner),
    )
    return handle


def read(handle: str) -> tuple[str, str]:
    """``(refresh_token, current_handle)`` for a live handle, else ``("", "")``.

    The returned handle is the one the token now lives under -- the same handle
    normally, the successor during a rotation grace. Callers rotate from THAT
    handle, so a grace read never resurrects a superseded chain.
    """
    if not valid_handle(handle):
        return ("", "")
    path = _record_path(handle)
    record = _read_json(path)
    if record is None:
        # Missing (a no-op unlink) or truncated/garbage: it can never become
        # valid, so it does not get to sit there being retried.
        _unlink(path)
        return ("", "")
    now = int(time.time())
    outer = _int(record.get("exp"))
    if outer is None or outer < now:
        # Malformed metadata is not ours; an expired outer deadline is the sweep
        # hint agreeing with the sealed one. Either way the record goes.
        _unlink(path)
        return ("", "")

    if record.get("kind") == _KIND_TOMBSTONE:
        successor = _open_successor(handle, record, now)
        if not successor:
            _unlink(path)
            return ("", "")
        return _read_successor(successor, now)

    token = _open_token(handle, record, now)
    if not token:
        _unlink(path)
        return ("", "")
    return (token, handle)


def _read_successor(successor: str, now: int) -> tuple[str, str]:
    """One hop, no rotation, no write."""
    path = _record_path(successor)
    record = _read_json(path)
    if record is None:
        return ("", "")
    outer = _int(record.get("exp"))
    if outer is None or outer < now:
        _unlink(path)
        return ("", "")
    if record.get("kind") != _KIND_SESSION:
        # Two rotations inside the grace window. Chaining further would hand back
        # a token AuthKit has already retired and the caller would 401 with no
        # way back; failing closed lets it fall through to the cookie instead.
        return ("", "")
    token = _open_token(successor, record, now)
    if not token:
        _unlink(path)
        return ("", "")
    return (token, successor)


def rotate(current_handle: str, new_refresh_token: str) -> str:
    """Seal ``new_refresh_token`` under a NEW handle; tombstone the old one.

    The old record is REPLACED, not annotated. Its retired token is dropped
    (AuthKit has already invalidated it) and the successor handle is sealed
    inside the new ciphertext under the OLD handle's AAD -- so the file names no
    live credential in plaintext, and the tombstone expires at the end of grace
    instead of lingering for the session's full TTL.
    """
    if not valid_handle(current_handle):
        return ""
    path = _record_path(current_handle)
    record = _read_json(path)
    if record is None or _open(current_handle, record, int(time.time())) is None:
        return ""
    owner = record["owner"]
    new_handle = mint(new_refresh_token, owner=owner)
    if not new_handle:
        return ""
    _write_record(
        current_handle,
        _seal(current_handle, _KIND_TOMBSTONE, {"succ": new_handle}, GRACE_SECONDS, owner=owner),
    )
    return new_handle


def revoke_owner(owner: str) -> None:
    """Remove every device and rotation-grace record for this verified owner."""
    for path in store_dir().glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except ValueError:
            continue  # An unreadable JSON record cannot renew.
        if isinstance(record, dict) and record.get("owner") == owner:
            # Revocation must fail loudly if a credential cannot be removed.
            path.unlink(missing_ok=True)


def drop(handle: str) -> None:
    """End the session. Logout intent covers the successor too, not just this hop."""
    if not valid_handle(handle):
        return
    path = _record_path(handle)
    record = _read_json(path)
    if record is not None and record.get("kind") == _KIND_TOMBSTONE:
        successor = _open_successor(handle, record)
        if successor:
            _unlink(_record_path(successor))
    _unlink(path)


# Import-time, not first-use: see the module docstring. Everything above must be
# defined before this runs.
arm()
