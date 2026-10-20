"""Per-deploy acceptance through the public router, owner's box and model broker."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._canary_common import (  # noqa: E402
    _INITIALIZED_NOTIF,
    _extract_structured_tool_payload,
    _extract_tool_text,
    _init_payload,
    _post,
    owner_canary_sign_in,
)


def wake(origin: str, *, timeout=180):
    """Reach the canary through the public router before deployment drains it."""
    home = os.environ.get("TINYASSETS_CANARY_UNIVERSE_ID", "")
    owner = os.environ.get("TINYASSETS_CANARY_USER_ID", "")
    if not home or not owner:
        raise RuntimeError("canary_identity_missing")
    token, me, headers = owner_canary_sign_in(origin, min(timeout, 30))
    if me.get("universe_id") != home or me.get("principal_id") != owner:
        raise RuntimeError("canary_identity_mismatch")
    uid = headers.get("x-tinyassets-box-uid", "")
    if not re.fullmatch(r"3[0-9]{5}", uid) or not 300001 <= int(uid) <= 399999:
        raise RuntimeError("canary_box_missing")
    return token, home, uid, headers


def probe(origin: str, expected_sha: str, *, timeout=180):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise RuntimeError("invalid_expected_sha")
    token, home, uid, headers = wake(origin, timeout=timeout)
    def provenance(headers):
        if (headers.get("x-tinyassets-box-uid") != uid
                or headers.get("x-tinyassets-box-sha") != expected_sha):
            raise RuntimeError("canary_box_revision_mismatch")

    provenance(headers)
    sid = None

    def post(payload, *, observe=True):
        nonlocal sid
        result, sid = _post(
            origin + "/mcp", sid, payload, timeout, step_code=1,
            error_factory=lambda *_: RuntimeError("canary_transport_failed"),
            user_agent="tinyassets-owner-box-canary/1.0", bearer_token=token,
            observe_headers=provenance if observe else None)
        if result and "error" in result:
            raise RuntimeError("canary_mcp_failed")
        return result

    initialized = post(_init_payload("owner-box-deploy"))
    if not initialized or not isinstance(initialized.get("result"), dict):
        raise RuntimeError("canary_initialize_failed")
    post(_INITIALIZED_NOTIF)
    nonce = secrets.token_hex(16)
    result = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "converse", "arguments": {"graph_id": home,
                                          "message": "Reply with exactly: " + nonce}}})
    tool = (result or {}).get("result", {})
    if tool.get("isError"):
        raise RuntimeError("canary_turn_failed")
    value = _extract_structured_tool_payload(tool)
    if value is None:
        try:
            value = json.loads(_extract_tool_text(tool))
        except ValueError:
            raise RuntimeError("canary_reply_missing") from None
    if not isinstance(value, dict) or value.get("reply", "").strip() != nonce:
        raise RuntimeError("canary_reply_mismatch")
    # No response text, owner identifiers or credentials enter the deploy receipt.
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://tinyassets.io")
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args(argv)
    try:
        probe(args.origin.rstrip("/"), args.expected_sha)
    except Exception:
        print("owner_box_canary=failed", file=sys.stderr)
        return 1
    print("owner_box_canary=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
