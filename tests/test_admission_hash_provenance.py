"""An admission's idempotency hash is typed by its provenance.

Live 2026-08-05: every cloud-automation slice quarantined as
`invalid_operator_admission` and no work ever ran. The writer minted a
server-derived `sha256:` digest and the epoch-2 reader accepted only
`hmac-sha256:`. Both sides had tests; neither test ran the *other* side's
real function, and the automation fixtures spelled the hash `hmac-sha256:`
by hand -- a shape production never produces. These tests close that gap by
checking each writer's actual output against the reader's actual contract.
"""

import pytest

from tinyassets.daemon_server import initialize_author_server
from tinyassets.storage.request_admissions import (
    RequestAdmissionStore,
    canonical_content_digest,
    expected_idempotency_hash_re,
    mint_idempotency_key_hash,
)


def _accepts(value: str, *, server_derived: bool) -> bool:
    pattern = expected_idempotency_hash_re(server_derived=server_derived)
    return pattern.fullmatch(value) is not None




def test_each_provenance_rejects_the_other_algorithm():
    """The contract is typed, not merely widened."""

    server = canonical_content_digest({"seed": "a"})
    user = mint_idempotency_key_hash("user-supplied-key-0001")

    assert not _accepts(server, server_derived=False)
    assert not _accepts(user, server_derived=True)


def test_server_derived_hash_is_stable_across_hmac_key_rotation(monkeypatch):
    """Replay is looked up by hash alone while the ids stay deterministic.

    Key the automation hash and a retry after rotation misses replay and
    then collides on the unchanged primary keys.
    """

    seed = {"domain": "cloud-continuation-admission-v1", "continuation": "c1"}
    monkeypatch.setenv("TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY", "k" * 64)
    before = canonical_content_digest(seed)
    monkeypatch.setenv("TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY", "z" * 64)
    assert canonical_content_digest(seed) == before


def test_commit_rejects_a_user_admission_carrying_a_server_derived_hash(
    tmp_path,
):
    """The write boundary fails loudly instead of quarantining downstream."""

    store = RequestAdmissionStore(tmp_path)
    with pytest.raises(ValueError, match="provenance"):
        store.commit_admission(
            tenant_id="tenant-a",
            actor_id="alice",
            universe_id="universe-a",
            idempotency_key_hash=canonical_content_digest({"seed": "a"}),
            body_digest="sha256:" + "b" * 64,
            body_digest_version="rfc8785-v1",
            request_type="general",
            text="repair the queue",
            branch_id="",
            branch_def_id="loop-branch",
            trigger_source="operator_request",
            accepted_priority_weight=50.0,
            policy_version="operator-priority-v1",
            grant_generation=3,
            receipt={"authority": "exact-universe-grant"},
            directed_daemon_id="",
            created_at="2026-07-24T08:00:00Z",
        )


def test_commit_rejects_an_unminted_hash(tmp_path):
    store = RequestAdmissionStore(tmp_path)
    with pytest.raises(ValueError, match="provenance"):
        store.commit_admission(
            tenant_id="tenant-a",
            actor_id="alice",
            universe_id="universe-a",
            idempotency_key_hash="hmac:scope-key-a",
            body_digest="sha256:" + "b" * 64,
            body_digest_version="rfc8785-v1",
            request_type="general",
            text="repair the queue",
            branch_id="",
            branch_def_id="loop-branch",
            trigger_source="operator_request",
            accepted_priority_weight=50.0,
            policy_version="operator-priority-v1",
            grant_generation=3,
            receipt={"authority": "exact-universe-grant"},
            directed_daemon_id="",
            created_at="2026-07-24T08:00:00Z",
        )


def test_a_correctly_minted_user_admission_still_commits(tmp_path):
    """The gate must not be a blanket reject -- prove the ACCEPT direction."""

    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    committed = store.commit_admission(
        tenant_id="tenant-a",
        actor_id="alice",
        universe_id="universe-a",
        idempotency_key_hash=mint_idempotency_key_hash("user-key-000000001"),
        body_digest="sha256:" + "b" * 64,
        body_digest_version="rfc8785-v1",
        request_type="general",
        text="repair the queue",
        branch_id="",
        branch_def_id="loop-branch",
        trigger_source="operator_request",
        accepted_priority_weight=50.0,
        policy_version="operator-priority-v1",
        grant_generation=3,
        receipt={"authority": "exact-universe-grant"},
        directed_daemon_id="",
        created_at="2026-07-24T08:00:00Z",
    )
    assert committed["admission_id"]
