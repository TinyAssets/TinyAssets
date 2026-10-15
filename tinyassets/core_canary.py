"""Core deploy-canary pieces: HMAC door, recording intake, echo branch.

The live probe (``scripts/core_capability_live.py``) signs in as one dedicated
owner and checks three things that must already exist:

* ``POST /api/core-canary/hmac`` returns the lowercase hex HMAC-SHA256 of the
  submitted ``nonce`` under a key the model never receives.
* Patch requests from this owner land on a receiver that stores the delivery
  and does not file an issue.
* A private ``source_code`` branch echoes ``nonce`` back as ``echo``.

The founder pastes one GitHub secret, JSON with exactly ``email`` and
``password``. The deploy pipes that document to this module on stdin inside
the daemon container. The HMAC key is derived from that password, so the
probe recomputes it and the key is never printed or written back to GitHub.
The key and the connection bearer live only in
``{data}/.runtime/core-canary.json`` (mode 0600), outside every universe.
The bearer is a different secret in the ordinary credential vault. The
password is never stored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SERVICE_PATH = "/api/core-canary/hmac"
SERVICE_HOST = "tinyassets.io"
DESTINATION = "core-canary-hmac"
ECHO_BRANCH_ID = "core-canary-echo"
RECORD_BRANCH_ID = "core-canary-record"
RECORDING_LABEL = "Core canary"
INTAKE_RECEIVER_ID = "core-canary-intake"
_RECORD_NAME = "core-canary.json"
_KEY_LABEL = b"tinyassets-core-canary-hmac-v1\0"
_MAX_RECORD_BYTES = 8192
_MAX_BODY_BYTES = 4096
_MAX_NONCE_CHARS = 256
_DUMMY_BEARER = "0" * 64

ECHO_SOURCE = 'def run(state):\n    return {"echo": state["nonce"]}\n'
RECORD_SOURCE = 'def run(state):\n    return {"recorded": state["report"]}\n'

#: The GitHub secret's fields. Tests pin this to the deploy verifier.
CONFIG_FIELDS = (
    "email",
    "password",
)

_RECORD_FIELDS = (
    "email",
    "owner_id",
    "universe_id",
    "connection_id",
    "service_host",
    "service_path",
    "intake_receiver_id",
    "branch_id",
    "hmac_key",
    "bearer",
)


class CoreCanaryRecordError(ValueError):
    """The on-disk record cannot be trusted. The message names no secret."""


class CoreCanaryProvisionError(RuntimeError):
    """Install could not finish. The message names no secret."""


def _record_path(base: Path) -> Path:
    return Path(base) / ".runtime" / _RECORD_NAME


def _hex64(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def load_record(base: str | Path) -> dict[str, Any] | None:
    """The canary record, or ``None`` when the file is absent.

    A present file that is unreadable, swapped for a symlink, or incomplete
    raises :class:`CoreCanaryRecordError`. The message is exactly
    ``core canary record is unreadable`` — JSON errors are not chained, because
    their text can contain the file.
    """
    path = _record_path(base)
    if path.is_symlink():
        raise CoreCanaryRecordError("core canary record is unreadable")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError:
        raise CoreCanaryRecordError("core canary record is unreadable") from None
    try:
        info = os.fstat(fd)
        if not info.st_size or info.st_size > _MAX_RECORD_BYTES:
            raise CoreCanaryRecordError("core canary record is unreadable")
        raw = os.read(fd, info.st_size + 1)
    finally:
        os.close(fd)
    if len(raw) > _MAX_RECORD_BYTES:
        raise CoreCanaryRecordError("core canary record is unreadable")
    try:
        document = json.loads(raw)
    except ValueError:
        raise CoreCanaryRecordError("core canary record is unreadable") from None
    if not isinstance(document, dict) or type(document.get("version")) is not int:
        raise CoreCanaryRecordError("core canary record is unreadable")
    if document.get("version") != 1:
        raise CoreCanaryRecordError("core canary record is unreadable")
    for key in _RECORD_FIELDS:
        value = document.get(key)
        if not isinstance(value, str) or not value:
            raise CoreCanaryRecordError("core canary record is unreadable")
    if not _hex64(document["hmac_key"]) or not _hex64(document["bearer"]):
        raise CoreCanaryRecordError("core canary record is unreadable")
    if document["hmac_key"] == document["bearer"]:
        raise CoreCanaryRecordError("core canary record is unreadable")
    return document


def _write_record(base: Path, record: dict[str, Any]) -> None:
    directory = Path(base) / ".runtime"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise CoreCanaryProvisionError("core canary record could not be stored")
    # Shared with owner sessions. Do not tighten an existing directory: the
    # broker group is granted traverse on it. The record file itself is 0600.
    directory.mkdir(mode=0o700, exist_ok=True)
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".core-canary.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fchmod(fd, 0o600)
        os.close(fd)
        fd = -1
        os.replace(tmp, _record_path(base))
        os.chmod(_record_path(base), 0o600)
    except OSError:
        raise CoreCanaryProvisionError("core canary record could not be stored") from None
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp.exists():
            tmp.unlink()


def offered_intake(universe_id: str) -> dict[str, str] | None:
    """The recording intake for this universe, or ``None`` when it is not the canary.

    A missing file is "no override". An unreadable file raises, so a send fails
    closed instead of falling through to the production intake. A receiver id
    that is not an address is the same failure.
    """
    from tinyassets.storage import data_dir

    record = load_record(data_dir())
    if record is None or record["universe_id"] != universe_id:
        return None
    from tinyassets.patch_intake import _RECEIVER_ID_RE

    if _RECEIVER_ID_RE.match(record["intake_receiver_id"]) is None:
        raise CoreCanaryRecordError("core canary record is unreadable")
    return {"receiver_id": record["intake_receiver_id"], "label": RECORDING_LABEL}


def canonical_email(email: str) -> str:
    return email.strip()


def service_hmac_key(email: str, password: str) -> str:
    """The HMAC key for this signup. Same password and email, same key.

    The probe recomputes this from the GitHub secret. The value is stored on
    the host and is not the password.
    """
    message = _KEY_LABEL + canonical_email(email).casefold().encode("utf-8")
    return hmac.new(password.encode("utf-8"), message, hashlib.sha256).hexdigest()


def parse_founder_config(raw: str) -> tuple[dict[str, str] | None, str]:
    """The pasted secret, or ``(None, reason)``. The reason names no secret."""
    if not raw.strip():
        return None, "TINYASSETS_CORE_CANARY_CONFIG is empty"
    try:
        document = json.loads(raw)
    except ValueError:
        return None, "core canary config is not JSON"
    if not isinstance(document, dict):
        return None, "core canary config is not a JSON object"
    extra = sorted(key for key in document if key not in CONFIG_FIELDS)
    if extra:
        return None, "unexpected: " + ", ".join(extra)
    missing = [
        key for key in CONFIG_FIELDS if not isinstance(document.get(key), str) or not document[key]
    ]
    if missing:
        return None, "missing or empty: " + ", ".join(missing)
    return {key: document[key] for key in CONFIG_FIELDS}, ""


def probe_config(email: str, password: str, universe_id: str) -> dict[str, str]:
    """The fields the live probe uses, derived from the signup and the home.

    ``connection_id`` is the same deterministic id ``connect_http`` stores.
    """
    from tinyassets.api.http_connection import _ids

    connection_id, _grant = _ids(universe_id=universe_id, destination=DESTINATION)
    return {
        "email": canonical_email(email),
        "password": password,
        "universe_id": universe_id,
        "connection_id": connection_id,
        "service_path": SERVICE_PATH,
        "service_hmac_key": service_hmac_key(email, password),
        "intake_receiver_id": INTAKE_RECEIVER_ID,
        "branch_id": ECHO_BRANCH_ID,
    }


def _identity(owner_id: str):
    from tinyassets.auth.provider import Identity, action_scope_for

    caps = [
        "tinyassets.extensions.read",
        "tinyassets.extensions.write",
        "tinyassets.extensions.admin",
        "read",
        "write",
        "costly",
        "admin",
    ]
    scope = action_scope_for("universe", "create_universe")
    if scope is not None:
        caps.append(scope.oauth_scope)
        caps.append(scope.effect)
    return Identity(user_id=owner_id, username=owner_id, capabilities=list(dict.fromkeys(caps)))


def _require_token(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CoreCanaryProvisionError(f"core canary {label} is required")
    if not value.isprintable() or any(char.isspace() for char in value):
        raise CoreCanaryProvisionError(f"core canary {label} is required")
    return value


def _branch(
    branch_def_id: str,
    name: str,
    author: str,
    source: str,
    input_key: str,
    output_key: str,
    display: str,
):
    from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition

    branch = BranchDefinition(
        branch_def_id=branch_def_id,
        name=name,
        author=author,
        visibility="private",
        entry_point="entry",
        node_defs=[
            NodeDefinition(
                node_id="entry",
                display_name=display,
                input_keys=[input_key],
                output_keys=[output_key],
                source_code=source,
            )
        ],
        graph_nodes=[GraphNodeRef(id="entry", node_def_id="entry")],
        edges=[EdgeDefinition("START", "entry"), EdgeDefinition("entry", "END")],
        state_schema=[
            {"name": input_key, "type": "str"},
            {"name": output_key, "type": "str"},
        ],
    )
    errors = branch.validate()
    if errors:
        raise CoreCanaryProvisionError("core canary branch is invalid: " + "; ".join(errors))
    return branch


def _require_admin(base: Path, owner_id: str, universe_id: str) -> None:
    from tinyassets.daemon_server import (
        get_founder_home,
        grant_universe_access,
        universe_access_permission,
    )

    permission = universe_access_permission(
        base,
        universe_id=universe_id,
        actor_id=owner_id,
    )
    if permission != "admin" and get_founder_home(base, owner_id) == universe_id:
        grant_universe_access(
            base,
            universe_id=universe_id,
            actor_id=owner_id,
            permission="admin",
            granted_by=owner_id,
        )
        permission = universe_access_permission(
            base,
            universe_id=universe_id,
            actor_id=owner_id,
        )
    if permission != "admin":
        raise CoreCanaryProvisionError("core canary owner is not an admin of this home")


def install(
    base: str | Path,
    *,
    owner_id: str,
    universe_id: str,
    email: str,
    password: str,
) -> dict[str, Any]:
    """Install or refresh the canary pieces. Idempotent for the same account.

    The HMAC key is derived from ``password``, so a repeat with the same
    password stores the same key and a changed password replaces only that
    key. The connection bearer is reused. A record for a different account
    is refused and left untouched. Returns the record, including both
    secrets — callers must not log it. The password is not stored.
    """
    from tinyassets.api.http_connection import connect_http
    from tinyassets.api.receiver_links import save_receiver
    from tinyassets.auth.middleware import identity_context
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        get_branch_definition,
        save_branch_definition,
    )
    from tinyassets.storage import receiver_links as store
    from tinyassets.storage.effector_consents import grant_consent

    root = Path(base)
    owner_id = _require_token(owner_id, "owner")
    universe_id = _require_token(universe_id, "universe")
    email = canonical_email(_require_token(email, "email"))
    if not isinstance(password, str) or not password:
        raise CoreCanaryProvisionError("core canary password is required")
    if "@" not in email.strip("@") or email.count("@") != 1:
        raise CoreCanaryProvisionError("core canary email is required")
    universe_dir = root / universe_id
    if not universe_dir.is_dir():
        raise CoreCanaryProvisionError("core canary home directory is missing")

    existing = load_record(root)
    if existing is not None and (
        existing["owner_id"] != owner_id
        or existing["email"].casefold() != email.casefold()
        or existing["universe_id"] != universe_id
    ):
        raise CoreCanaryRecordError("core canary record belongs to a different account")
    hmac_key = service_hmac_key(email, password)
    if existing is None:
        bearer = secrets.token_hex(32)
        while bearer == hmac_key:
            bearer = secrets.token_hex(32)
        stored_email = email
    else:
        bearer = existing["bearer"]
        if bearer == hmac_key:
            bearer = secrets.token_hex(32)
            while bearer == hmac_key:
                bearer = secrets.token_hex(32)
        stored_email = existing["email"]

    _require_admin(root, owner_id, universe_id)
    ensure_universe_registered(
        root,
        universe_id=universe_id,
        universe_path=universe_dir,
    )
    echo = _branch(
        ECHO_BRANCH_ID,
        "Core canary echo",
        owner_id,
        ECHO_SOURCE,
        "nonce",
        "echo",
        "Echo nonce",
    )
    record_branch = _branch(
        RECORD_BRANCH_ID,
        "Core canary record",
        owner_id,
        RECORD_SOURCE,
        "report",
        "recorded",
        "Record report",
    )
    save_branch_definition(root, branch_def=echo.to_dict())
    save_branch_definition(root, branch_def=record_branch.to_dict())
    saved_echo = get_branch_definition(root, branch_def_id=ECHO_BRANCH_ID)
    if saved_echo.get("visibility") != "private":
        raise CoreCanaryProvisionError("core canary echo branch is not private")

    with identity_context(_identity(owner_id)):
        expected = None
        try:
            current = store.inspect_receiver(
                root,
                receiver_id=INTAKE_RECEIVER_ID,
                principal_id=owner_id,
                owner_universe_id=universe_id,
            )
        except (store.ReceiverAccessDenied, ValueError):
            if existing is not None:
                raise CoreCanaryProvisionError("core canary intake is missing") from None
        else:
            expected = current["generation"]
        try:
            receiver = save_receiver(
                universe_id=universe_id,
                branch_def_id=RECORD_BRANCH_ID,
                node_id="entry",
                input_keys=["report"],
                allowed_senders=[owner_id],
                description="Records canary patch requests.",
                receiver_id=INTAKE_RECEIVER_ID,
                expected_generation=expected,
                open_to_all=False,
                discoverable=False,
            )
        except store.ReceiverConflict as exc:
            raise CoreCanaryProvisionError(f"core canary intake was not saved ({exc})") from None
        connection = connect_http(
            universe_id=universe_id,
            payload={
                "destination": DESTINATION,
                "auth_scheme": "bearer",
                "secret": bearer,
                "allowed_endpoints": [
                    {
                        "host": SERVICE_HOST,
                        "path_template": SERVICE_PATH,
                        "methods": ["POST"],
                    }
                ],
            },
        )
        if connection.get("status") != "provisioned":
            code = connection.get("error")
            if not isinstance(code, str) or not code.isidentifier():
                code = "connection_not_provisioned"
            raise CoreCanaryProvisionError(f"core canary connection was not provisioned ({code})")
        connection_id = connection.get("connection_id")
        if not isinstance(connection_id, str) or not connection_id:
            raise CoreCanaryProvisionError(
                "core canary connection was not provisioned (connection_not_provisioned)"
            )
        grant_consent(
            universe_dir,
            sink="authenticated_external_call",
            destination=DESTINATION,
            granted_by=owner_id,
        )

    record = {
        "version": 1,
        "email": stored_email,
        "owner_id": owner_id,
        "universe_id": universe_id,
        "connection_id": connection_id,
        "service_host": SERVICE_HOST,
        "service_path": SERVICE_PATH,
        "intake_receiver_id": receiver["receiver_id"],
        "branch_id": ECHO_BRANCH_ID,
        "hmac_key": hmac_key,
        "bearer": bearer,
    }
    _write_record(root, record)
    return record


def _presented_bearer(request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _bearer_matches(presented: str, stored: str | None) -> bool:
    expected = stored if isinstance(stored, str) and stored else _DUMMY_BEARER
    try:
        match = hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
    except (TypeError, ValueError):
        return False
    return bool(stored) and match


async def _read_body(request) -> bytes | None:
    """The body, or ``None`` when it is over the cap."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > _MAX_BODY_BYTES:
                return None
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def _decide(request) -> tuple[int, dict[str, str]]:
    from tinyassets.storage import data_dir

    try:
        record = load_record(data_dir())
    except CoreCanaryRecordError:
        logger.error("core canary hmac: record unreadable")
        return 500, {"error": "unavailable"}
    stored = record["bearer"] if record else None
    if not _bearer_matches(_presented_bearer(request), stored):
        return 401, {"error": "unauthorized"}
    key = record["hmac_key"] if record else ""
    if not isinstance(key, str) or not key:
        logger.error("core canary hmac: key missing")
        return 500, {"error": "unavailable"}
    body = await _read_body(request)
    if body is None:
        return 413, {"error": "too_large"}
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return 400, {"error": "invalid_json"}
    if not isinstance(document, dict):
        return 400, {"error": "invalid_json"}
    nonce = document.get("nonce")
    if not isinstance(nonce, str) or not nonce or len(nonce) > _MAX_NONCE_CHARS:
        return 400, {"error": "nonce_required"}
    digest = hmac.new(key.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()
    return 200, {"hmac": digest}


async def hmac_endpoint(request):
    """POST ``{"nonce": "..."}`` → ``{"hmac": "<lowercase hex>"}``.

    Authenticated by the connection bearer. A missing record and a wrong
    bearer are the same 401. The key is not part of the response.
    """
    from starlette.responses import JSONResponse

    try:
        status, payload = await _decide(request)
    except Exception as exc:
        logger.error("core canary hmac failed: %s", type(exc).__name__)
        status, payload = 500, {"error": "unavailable"}
    logger.info("core canary hmac status=%s", status)
    return JSONResponse(
        payload,
        status_code=status,
        headers={"cache-control": "no-store"},
    )


def lookup_workos_user(email: str, *, opener=urllib.request.urlopen) -> str:
    """The WorkOS user id for ``email``. Does not create an account.

    Failures name a status or a next step, never the API key or the body.
    """
    from tinyassets.account_deletion import _WORKOS_BASE_URL, _WORKOS_USER_ID

    api_key = os.environ.get("WORKOS_API_KEY", "").strip()
    if not api_key:
        raise CoreCanaryProvisionError(
            "WORKOS_API_KEY is not set; the host looks the signed-up owner up"
        )
    query = urllib.parse.urlencode({"email": email})
    req = urllib.request.Request(
        f"{_WORKOS_BASE_URL}/user_management/users?{query}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "tinyassets-core-canary/1.0",
        },
        method="GET",
    )
    try:
        with opener(req, timeout=20) as response:
            raw = response.read()
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        raise CoreCanaryProvisionError(f"WorkOS user lookup failed (HTTP {exc.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CoreCanaryProvisionError("WorkOS user lookup request failed") from None
    if status != 200:
        raise CoreCanaryProvisionError(f"WorkOS user lookup returned HTTP {status}")
    try:
        document = json.loads(raw)
    except ValueError:
        raise CoreCanaryProvisionError("WorkOS user lookup returned unexpected data") from None
    users = document.get("data") if isinstance(document, dict) else None
    if not isinstance(users, list):
        raise CoreCanaryProvisionError("WorkOS user lookup returned unexpected data")
    if len(users) == 0:
        raise CoreCanaryProvisionError(
            "no WorkOS user for that email; sign up at https://tinyassets.io first"
        )
    if len(users) > 1:
        raise CoreCanaryProvisionError("WorkOS user lookup matched more than one user")
    user = users[0] if isinstance(users[0], dict) else {}
    user_id = str(user.get("id") or "")
    if _WORKOS_USER_ID.fullmatch(user_id) is None:
        raise CoreCanaryProvisionError("WorkOS user lookup returned unexpected data")
    return user_id


def _read_founder_secret() -> str:
    """The deploy's stdin document. An empty env value does not fall through.

    Arguments are refused without being echoed: a ``--password`` would show
    up in process listings and in this process's error text.
    """
    if "TINYASSETS_CORE_CANARY_CONFIG" in os.environ:
        return os.environ["TINYASSETS_CORE_CANARY_CONFIG"]
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("core canary reads the secret from stdin", file=sys.stderr)
        return 1
    parsed, reason = parse_founder_config(_read_founder_secret())
    if parsed is None:
        print(reason, file=sys.stderr)
        return 1
    email = canonical_email(parsed["email"])
    password = parsed["password"]
    if "@" not in email.strip("@") or email.count("@") != 1:
        print("core canary email is required", file=sys.stderr)
        return 1
    try:
        owner_id = lookup_workos_user(email)
    except CoreCanaryProvisionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    from tinyassets.api.first_contact import ensure_founder_home, founder_home_failure
    from tinyassets.auth.middleware import identity_context
    from tinyassets.storage import data_dir

    root = data_dir()
    with identity_context(_identity(owner_id)):
        universe_id = ensure_founder_home(root, owner_id)
        if not universe_id:
            reason = founder_home_failure() or "unknown"
            print(f"founder home was not created ({reason})", file=sys.stderr)
            return 1
        try:
            record = install(
                root,
                owner_id=owner_id,
                universe_id=universe_id,
                email=email,
                password=password,
            )
        except (CoreCanaryRecordError, CoreCanaryProvisionError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    derived = probe_config(email, password, universe_id)
    if (
        record["hmac_key"] != derived["service_hmac_key"]
        or record["connection_id"] != derived["connection_id"]
        or record["intake_receiver_id"] != INTAKE_RECEIVER_ID
        or record["branch_id"] != ECHO_BRANCH_ID
        or record["service_path"] != SERVICE_PATH
    ):
        print("core canary pieces were not stored", file=sys.stderr)
        return 1
    print("core_canary_provisioned=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
