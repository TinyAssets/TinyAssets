"""Architecture consent uses the protected request store and owner approval door."""
from __future__ import annotations

import hashlib
import json
import re

ACTION = "architecture_approval"
PURPOSE = "tinyassets.architecture-approval.v1"
TTL = 24 * 60 * 60


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def files_digest(files: list[str]) -> str:
    return hashlib.sha256(canonical(sorted(files))).hexdigest()


def validate_action(action: dict) -> dict:
    """Normalize once, before the platform freezes the displayed request."""
    repo, pr = action.get("repo"), action.get("pr")
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("repo must be owner/repository")
    if type(pr) is not int or not 1 <= pr <= 2**31 - 1:
        raise ValueError("pr must be a positive PR number")
    for name, size in (("head_sha", 40), ("diff_key", 64)):
        if not isinstance(action.get(name), str) or not re.fullmatch(
                "[0-9a-f]{" + str(size) + "}", action[name]):
            raise ValueError(f"{name} must be a full lowercase hex digest")
    files = action.get("release_critical_files")
    if (not isinstance(files, list) or not 9 <= len(files) < 3000
            or any(not isinstance(p, str) or not p or len(p) > 1000
                   or p.startswith("/") or "\\" in p or any(ord(c) < 32 for c in p)
                   or any(part in {"", ".", ".."} for part in p.split("/")) for p in files)
            or len(set(files)) != len(files)):
        raise ValueError("release_critical_files must list 9-2999 distinct relative paths")
    if type(action.get("release_critical_count")) is not int or (
            action["release_critical_count"] != len(files)):
        raise ValueError("release_critical_count must equal the file list length")
    briefing = action.get("briefing")
    names = {"changes", "direction", "risks", "rollback"}
    if (not isinstance(briefing, dict) or set(briefing) != names
            or any(not isinstance(v, str) or not v.strip() or len(v) > 2000
                   for v in briefing.values())):
        raise ValueError("briefing needs changes, direction, risks and rollback, 1-2000 chars each")
    result = {"type": ACTION, "repo": repo.lower(), "pr": pr,
              "head_sha": action["head_sha"], "diff_key": action["diff_key"],
              "release_critical_files": sorted(files), "release_critical_count": len(files),
              "briefing": {k: v.strip() for k, v in briefing.items()}}
    if len(canonical(result)) > 50000:
        raise ValueError("architecture request exceeds 50000 bytes; split the PR")
    return result


def tab_text(action: dict) -> tuple[str, str, str]:
    briefing = action["briefing"]
    body = (
        "Approve only after reading this briefing: I have been briefed and agree this PR "
        "builds toward the correct core architecture in README Direction.\n\n"
        f"Repository: {action['repo']}\nPR: #{action['pr']}\n"
        f"Head SHA: {action['head_sha']}\nDrain-Review diff key: {action['diff_key']}\n"
        f"Release-critical files: {action['release_critical_count']}\n"
        + "\n".join(action["release_critical_files"])
        + "\n\n" + "\n\n".join(f"{k.capitalize()}: {briefing[k]}" for k in
                                    ("changes", "direction", "risks", "rollback"))
        + "\n\nAgreement expires after 24 hours. Changed scope needs a new approval."
    )
    return "Architecture", f"Architecture agreement for {action['repo']} #{action['pr']}", body


def broker_call(document: dict) -> dict:
    from tinyassets.api.helpers import _base_path
    from tinyassets.broker.client import BrokerClient
    from tinyassets.broker.supervisor import get_supervisor

    supervisor = get_supervisor(_base_path())
    if supervisor is None:
        raise RuntimeError("architecture signing broker is unavailable")
    client = BrokerClient(supervisor.socket_path, principal="architecture",
                          command_center="architecture", fence=supervisor.fence,
                          verify_peer=supervisor.verify_broker, timeout=15)
    return client.architecture(document)


def approve(action: dict, *, request_id: str, owner_session: dict) -> dict:
    from tinyassets.api.permissions import current_actor_id

    owner = json.loads(owner_session["identity_json"])["user_id"]
    if not owner or owner != current_actor_id():
        raise PermissionError("protected owner identity mismatch")
    return broker_call({"operation": "issue", "action": validate_action(action),
                        "request_id": request_id, "owner": owner})


async def public_attestation(request):
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse

    headers = {"Cache-Control": "no-store"}
    try:
        pr = int(request.path_params["pr"])
        if not 1 <= pr <= 2**31 - 1:
            raise ValueError("invalid PR")
        result = await run_in_threadpool(broker_call, {"operation": "read", "pr": pr})
    except (ValueError, PermissionError, RuntimeError, OSError):
        return JSONResponse({"error": "attestation_unavailable"}, status_code=503, headers=headers)
    if not result:
        return JSONResponse({"error": "not_found"}, status_code=404, headers=headers)
    return JSONResponse(result, headers=headers)
