"""Protected owner control of REST API keys; no bearer can mint or revoke one.

POST /app/api-keys (the owner's box) with one of:
  {"operation": "list"} or {"operation": "revoke", "key_id": str}, and the
  credential-request card's ``request_*`` operations.
POST /app/api-keys/mint (the router, P0-3) with one of:
  {"name": str, "scopes": [{"universe", "agent", "capability"}]}: no scopes is a
  ``full`` key (decision A1);
  {"request_id": str, "digest": str}: an agent's held key request, as shown.
The router mints a key and writes its grant in the broker together, from the
owner's protected session; a box never writes a grant.
"""
import json
import sqlite3

from starlette.responses import JSONResponse

from tinyassets.onboarding.owner_sessions import HEADERS, require
from tinyassets.owner_control import ControlUnavailable


async def handle(request):
    from tinyassets.api_keys import ApiKeyRefused
    from tinyassets.api_keys import current_store as key_store
    from tinyassets.onboarding import _read_small_json

    try:
        session = require(request)
        owner = json.loads(session["identity_json"])["user_id"]
        data = await _read_small_json(request, limit=65536)
        if not isinstance(data, dict):
            raise ValueError("invalid API key request")
        keys = key_store()
        operation = data.get("operation")
        if operation in {"request_preview", "request_create", "request_revoke", "request_finish",
                         "request_decline",
                         "owner_list", "owner_revoke"}:
            from tinyassets.api.credential_requests import owner_operation
            from tinyassets.auth.middleware import current_identity

            identity = current_identity()
            if identity is None or identity.user_id != owner:
                raise PermissionError("Owner session and app account must match")
            if operation == "owner_list" and set(data) == {"operation"}:
                result = keys.list(owner)
            elif operation == "owner_revoke" and set(data) == {"operation", "key_id"}:
                result = keys.revoke(owner, str(data["key_id"]))
            else:
                result = owner_operation(owner, data, keys)
        elif operation == "list" and set(data) == {"operation"}:
            result = keys.list(owner)
        elif operation == "revoke" and set(data) == {"operation", "key_id"}:
            result = keys.revoke(owner, str(data["key_id"]))
        else:
            raise ValueError("invalid API key operation")
        return JSONResponse(result, headers=HEADERS)
    except ControlUnavailable:
        return JSONResponse({"error": "api_key_busy"}, status_code=503, headers=HEADERS)
    except (ApiKeyRefused, PermissionError, ValueError, KeyError, TypeError, sqlite3.Error):
        return JSONResponse({"error": "api_key_refused"}, status_code=403, headers=HEADERS)


async def mint(request):
    """Router-served: mint one key and write its broker grant from the owner's session."""
    from starlette.concurrency import run_in_threadpool

    from tinyassets.onboarding import _read_small_json
    from tinyassets.owner_runtime import current

    try:
        if current() is not None:
            raise PermissionError("keys are minted on the trusted side")
        session = require(request)
        owner = json.loads(session["identity_json"])["user_id"]
        data = await _read_small_json(request, limit=65536)
        result = await run_in_threadpool(
            _mint, owner, {"session_hash": session["session_hash"]}, data)
        return JSONResponse(result, headers=HEADERS)
    except RuntimeError:
        return JSONResponse({"error": "api_key_busy"}, status_code=503, headers=HEADERS)
    except (PermissionError, ValueError, KeyError, TypeError, sqlite3.Error):
        return JSONResponse({"error": "api_key_refused"}, status_code=403, headers=HEADERS)


def _mint(owner: str, proof: dict, data: object) -> dict:
    from tinyassets.api_keys import CAPABILITIES, ApiKeyStore, _client
    from tinyassets.broker.policy_submit import submit
    from tinyassets.router_credentials import mint_key
    from tinyassets.storage import data_dir

    root = data_dir()
    proposal = None
    if isinstance(data, dict) and set(data) == {"request_id", "digest"}:
        held = submit(root, owner, "held", request_id=data["request_id"])
        request = held["change"].get("api_key")
        if held["digest"] != data["digest"] or not isinstance(request, dict):
            raise PermissionError("Review the current request before creating")
        name, scopes, ref, proposal = (request["name"], request["scopes"],
                                       request["request_ref"], data)
    elif isinstance(data, dict) and set(data) == {"name", "scopes"}:
        name, scopes, ref = data["name"], data["scopes"], ""
    else:
        raise ValueError("invalid API key mint")
    if not isinstance(scopes, list) or any(
            not isinstance(s, dict) or s.get("capability") not in CAPABILITIES for s in scopes):
        raise ValueError("unknown API capability")
    minted = mint_key(root, owner, name=name, request_ref=ref)
    if minted.get("already_created"):
        return minted
    client = _client(minted["key_id"])
    try:
        submit(root, owner, "outside_set", session_proof=proof, client=client,
               source="api_key", mode="scoped" if scopes else "full", scopes=scopes,
               expected_generation=0, name=minted["name"],
               **({"proposal": proposal} if proposal else {}))
    except BaseException as failure:
        # A lost answer may follow a committed grant (and an applied request): keep the
        # key then. Discard it only when the broker shows no grant; when it cannot say,
        # keep it, refused at admission until a grant exists, and visible to revoke.
        try:
            granted = any(row["client"] == client for row in
                          submit(root, owner, "outside_list")["clients"])
        except Exception:  # noqa: BLE001 - unknown: raise the original, keep the key
            raise failure from None
        if granted:
            return {**minted, "scopes": scopes}
        ApiKeyStore(root).discard(owner, minted["key_id"])  # never a key without its grant
        raise
    return {**minted, "scopes": scopes}
