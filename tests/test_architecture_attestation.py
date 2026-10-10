"""Attestations prove exact, expiring founder agreement, not GitHub authorship."""
import base64
import json
from argparse import Namespace

import pytest

from scripts.drain_review_gate import _blocking_review, architecture_allows_merge
from tests.support.architecture import action, signed
from tinyassets.architecture_approval import canonical
from tinyassets.broker.architecture import ArchitectureSigner


def verify(envelope, trust, ask=None, **changes):
    ask = ask or action()
    args = dict(trust=trust, repo=ask["repo"], pr=ask["pr"], head=ask["head_sha"],
                diff_key=ask["diff_key"], files=ask["release_critical_files"], now=1000)
    return architecture_allows_merge(envelope, **(args | changes))


def test_valid_and_unchanged_rebase():
    _, trust, envelope = signed(now=1000)
    assert verify(envelope, trust)
    assert verify(envelope, trust, head="b" * 40)
    assert verify(envelope, trust, files=list(reversed(action()["release_critical_files"])))


@pytest.mark.parametrize("fault", ["wrong-head", "wrong-diff", "wrong-count", "wrong-files",
                                  "expired", "future", "bad-signature", "wrong-key", "missing",
                                  "wrong-owner", "wrong-repo", "wrong-pr", "wrong-purpose",
                                  "excess-lifetime", "bool-count", "bool-pr", "duplicate-files"])
def test_invalid_proof_refuses_even_when_signed(fault):
    key, trust, envelope = signed(now=1000)
    payload = envelope["payload"]
    args = {}
    if fault == "wrong-head":
        payload.update(diff_key="", head_sha="b" * 40)
    elif fault == "wrong-diff":
        args["diff_key"] = "d" * 64  # Same SHA never rescues a wrong diff binding.
    elif fault == "wrong-count":
        payload["release_critical_count"] = 10
    elif fault == "wrong-files":
        args["files"] = action()["release_critical_files"][:-1] + ["deploy/other.py"]
    elif fault == "expired":
        args["now"] = payload["expiry"]
    elif fault == "future":
        args["now"] = 999
    elif fault == "wrong-owner":
        payload["approver_owner_id"] = "bob"
    elif fault == "wrong-repo":
        args["repo"] = "elsewhere/repository"
    elif fault == "wrong-pr":
        args["pr"] = 4575
    elif fault == "wrong-purpose":
        payload["purpose"] = "ordinary-signature"
    elif fault == "excess-lifetime":
        payload["expiry"] += 1
    elif fault == "bool-count":
        payload["release_critical_count"] = True
    elif fault == "bool-pr":
        payload["pr"] = True
    elif fault == "duplicate-files":
        args["files"] = action()["release_critical_files"] + ["deploy/critical-0.py"]
    envelope["signature"] = base64.b64encode(key.sign(canonical(payload))).decode()
    if fault == "bad-signature":
        payload["expiry"] -= 1
    elif fault == "wrong-key":
        trust = signed(now=1000)[1]
    elif fault == "missing":
        envelope = None
    assert not verify(envelope, trust, **args)


@pytest.mark.parametrize("envelope", [{}, [], "", {"payload": None, "signature": "?"}])
def test_malformed_proof_refuses(envelope):
    assert not verify(envelope, signed(now=1000)[1])


def guard(tmp_path, ask, trust, envelope, *, count_receipt=True):
    url = f"https://github.com/{ask['repo']}/pull/{ask['pr']}#issuecomment-123"
    approval = "Drain-Review-Verdict: APPROVE\nDrain-Review-Diff: " + ask["diff_key"] + "\n"
    comment = approval + (f"Drain-Review-Release-Critical: {ask['release_critical_count']}\n"
                          if count_receipt else "")
    values = {"body_file": approval + f"Drain-Review-Artifact: {url}",
              "review_comments_file": json.dumps({"url": url, "association": "OWNER",
                                                  "body": comment}),
              "architecture_trust": json.dumps(trust),
              "architecture_attestation": json.dumps(envelope),
              "release_critical_files": json.dumps(ask["release_critical_files"])}
    args = dict(head=ask["head_sha"], diff_key=ask["diff_key"], review_repo=ask["repo"],
                review_pr=ask["pr"], release_critical_count=ask["release_critical_count"])
    for name, value in values.items():
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        args[name] = path
    return _blocking_review(Namespace(**args))


def test_guard_requires_both_proofs(tmp_path):
    _, trust, envelope = signed()
    assert guard(tmp_path, action(), trust, envelope) == 0
    assert guard(tmp_path, action(), trust, None) == 2
    assert guard(tmp_path, action(), trust, envelope, count_receipt=False) == 2


def configured_signer(tmp_path):
    key, trust, _ = signed()
    config = {"repo": trust["repo"], "owner_id": trust["owner_id"],
              "private_key": base64.b64encode(key.private_bytes_raw()).decode()}
    (tmp_path / "architecture-signing.json").write_text(json.dumps(config), encoding="utf-8")
    return ArchitectureSigner(tmp_path), trust


def test_signer_founder_binding_and_retries(tmp_path, monkeypatch):
    signer, trust = configured_signer(tmp_path)
    doc = {"operation": "issue", "action": action(), "owner": "alice", "request_id": "req-one"}
    monkeypatch.setattr("tinyassets.broker.architecture.time.time", lambda: 1000)
    first = signer.execute(doc)
    assert verify(first, trust)
    monkeypatch.setattr("tinyassets.broker.architecture.time.time", lambda: 2000)
    assert signer.execute(doc) == first
    assert signer.execute({"operation": "read", "pr": 4574}) == first
    with pytest.raises(PermissionError):
        signer.execute(doc | {"owner": "bob"})
    with pytest.raises(PermissionError):
        signer.execute(doc | {"action": action(diff="d" * 64)})
    with pytest.raises(PermissionError):
        signer.execute(doc | {"request_id": "other", "action": action(repo="other/repo")})


def test_missing_key_never_generates_fallback(tmp_path):
    with pytest.raises(FileNotFoundError):
        ArchitectureSigner(tmp_path).execute({"operation": "read", "pr": 4574})
    assert not list(tmp_path.iterdir())
