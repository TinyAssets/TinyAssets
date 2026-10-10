"""Synthetic architecture keys and requests; never production signing material."""
import base64
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tinyassets.architecture_approval import PURPOSE, canonical, files_digest


def action(count=9, *, repo="tinyassets/tinyassets", pr=4574, head="a" * 40, diff="c" * 64):
    return {"type": "architecture_approval", "repo": repo.lower(), "pr": pr,
            "head_sha": head, "diff_key": diff, "release_critical_count": count,
            "release_critical_files": [f"deploy/critical-{i}.py" for i in range(count)],
            "briefing": {"changes": "Replace the shared worker path.",
                         "direction": "Per-owner isolation and vendor-neutral tools.",
                         "risks": "Approval service availability.",
                         "rollback": "Restore the previous image and backup."}}


def signed(ask=None, *, now=None):
    ask = ask or action()
    now = int(time.time()) if now is None else now
    key = Ed25519PrivateKey.generate()
    trust = {"repo": ask["repo"], "owner_id": "alice",
             "public_key": base64.b64encode(key.public_key().public_bytes_raw()).decode()}
    payload = {"purpose": PURPOSE, "repo": ask["repo"], "pr": ask["pr"],
               "head_sha": ask["head_sha"], "diff_key": ask["diff_key"],
               "release_critical_count": ask["release_critical_count"],
               "files_digest": files_digest(ask["release_critical_files"]),
               "approver_owner_id": "alice", "approved_at": now, "expiry": now + 86400}
    envelope = {"payload": payload,
                "signature": base64.b64encode(key.sign(canonical(payload))).decode()}
    return key, trust, envelope
