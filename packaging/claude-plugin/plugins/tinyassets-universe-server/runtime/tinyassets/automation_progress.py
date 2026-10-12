"""Deterministic checkpoint validation for user-authored background workflows.

This is an internal-work ledger, not an external-effect exactly-once guarantee.
Call after producing and checking an artifact, before committing its checkpoint.
"""
from __future__ import annotations

import copy
import hashlib
import re


def commit_artifact(previous, *, work_id, artifact):
    """Append one immutable artifact; reject completed IDs and duplicate content."""
    if not isinstance(previous, dict):
        raise ValueError("progress_checkpoint_invalid")
    if not isinstance(work_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._/-]{0,159}", work_id
    ):
        raise ValueError("progress_work_id_invalid")
    if not isinstance(artifact, str) or not artifact.strip():
        raise ValueError("progress_artifact_empty")
    records = previous.get("completed", {})
    if not isinstance(records, dict):
        raise ValueError("progress_checkpoint_invalid")
    hashes = set()
    for key, record in records.items():
        if not isinstance(key, str) or not isinstance(record, dict):
            raise ValueError("progress_checkpoint_invalid")
        content = record.get("artifact")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("progress_checkpoint_invalid")
        digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
        if record.get("sha256") != digest:
            raise ValueError("progress_checkpoint_corrupt")
        hashes.add(digest)
    if work_id in records:
        raise ValueError("progress_work_already_completed")
    digest = hashlib.sha256(artifact.strip().encode("utf-8")).hexdigest()
    if digest in hashes:
        raise ValueError("progress_artifact_already_completed")
    result = copy.deepcopy(previous)
    result.setdefault("completed", {})[work_id] = {
        "artifact": artifact, "sha256": digest,
    }
    return result
