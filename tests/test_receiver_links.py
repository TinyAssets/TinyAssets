"""Real request identities, universe ACLs and SQLite receiver/link records.

These prove management only; no public route, provider or delivery is invoked.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from tinyassets.api import receiver_links as api
from tinyassets.auth.middleware import current_identity_or_none, identity_context
from tinyassets.auth.provider import Identity
from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
from tinyassets.daemon_server import (
    grant_universe_access,
    initialize_author_server,
    revoke_universe_access,
    save_branch_definition,
)
from tinyassets.storage import db_path
from tinyassets.storage import receiver_links as store


@contextmanager
def _receiver_delivery_workers(base, monkeypatch):
    """Own actual delivery Futures until this fixture's patches can be undone."""
    from tinyassets import delivery_runtime, runs
    from tinyassets.storage import deliveries

    dispatch = delivery_runtime.dispatch_accepted_delivery
    futures = set()
    lock = threading.Lock()

    def tracked(base_path, *, delivery_id, attempt=1):
        submitted = dispatch(base_path, delivery_id=delivery_id, attempt=attempt)
        if Path(base_path).resolve() == base.resolve():
            with deliveries.transaction(base) as conn:
                row = conn.execute(
                    "SELECT run_id FROM graph_delivery_attempts "
                    "WHERE delivery_id=? AND attempt=?", (delivery_id, attempt),
                ).fetchone()
            future = runs.get_future(row["run_id"]) if row is not None else None
            if future is not None:
                with lock:
                    futures.add(future)
        return submitted

    monkeypatch.setattr(delivery_runtime, "dispatch_accepted_delivery", tracked)
    try:
        yield
    finally:
        deadline = time.monotonic() + 10
        while True:
            with lock:
                pending = list(futures)
                futures.clear()
            if not pending:
                break
            for future in pending:
                future.result(timeout=max(0, deadline - time.monotonic()))


@pytest.fixture
def env(tmp_path, monkeypatch, authenticate_request):
    base = tmp_path / "data"
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    initialize_author_server(base)
    # Persist actual ACLs rather than patching the permission resolver.
    for owner in ("receiver", "sender", "outsider"):
        grant_universe_access(
            base,
            universe_id="u-" + owner,
            actor_id=owner,
            permission="admin",
        )
        _seed(base, owner)
    # Provider doubles and the data-root environment belong to the shared
    # monkeypatch fixture. Drain before that dependency restores either one.
    with _receiver_delivery_workers(base, monkeypatch):
        yield base, authenticate_request


def _seed(base, owner, *, schema=None, author=None):
    branch = BranchDefinition(
        branch_def_id="b-" + owner,
        name="Private graph",
        author=author or owner,
        visibility="private",
        node_defs=[
            NodeDefinition(
                node_id="definition",
                display_name="private node name",
                prompt_template="private prompt {topic}",
                input_keys=["topic"],
                output_keys=["result", "extra"],
            )
        ],
        graph_nodes=[GraphNodeRef(id="entry", node_def_id="definition")],
        edges=[EdgeDefinition("START", "entry"), EdgeDefinition("entry", "END")],
        entry_point="entry",
        state_schema=schema
        or [
            {"name": "topic", "type": "str", "description": "Incoming topic"},
            {"name": "result", "type": "str"},
            {"name": "extra", "type": "str"},
        ],
    )
    save_branch_definition(base, branch_def=branch.to_dict())
    return branch


def _save(**kw):
    args = dict(
        universe_id="u-receiver",
        branch_def_id="b-receiver",
        node_id="entry",
        input_keys=["topic"],
        allowed_senders=["sender"],
        description="Send a topic",
    )
    args.update(kw)
    return api.save_receiver(**args)


def _link(receiver, **kw):
    args = dict(
        universe_id="u-sender",
        branch_def_id="b-sender",
        node_id="entry",
        receiver_id=receiver["receiver_id"],
        expected_generation=receiver["generation"],
        mapping={"result": "topic"},
    )
    args.update(kw)
    return api.connect_output(**args)


def _resolve(base, link, owner="sender", universe="u-sender"):
    with store.transaction(base) as conn:
        return store.resolve_link_in_transaction(
            conn,
            link_id=link["link_id"],
            owner_id=owner,
            universe_id=universe,
        )


def test_two_owners_can_expose_inspect_connect_without_private_graph_leak(env):
    base, auth = env
    auth("receiver")
    receiver = _save()
    assert receiver["generation"] == 1
    auth("sender")
    advertised = api.inspect_receiver(receiver_id=receiver["receiver_id"])
    # Kept EXACT, and widened deliberately: the three exposure fields are the
    # sender's own terms of use, and this set is the no-leak guard. Note what is
    # still absent -- the owner's universe_id, branch_def_id, node_id, snapshot
    # digests and allowed_senders all stay private view only.
    assert set(advertised) == {
        "receiver_id",
        "owner_id",
        "generation",
        "description",
        "contract",
        "revoked",
        "open_to_all",
        "discoverable",
        "sender_rate_limit",
    }
    assert advertised["open_to_all"] is False
    assert advertised["discoverable"] is False
    assert advertised["sender_rate_limit"] == store.NO_SENDER_RATE_LIMIT
    assert advertised["contract"] == [
        {"name": "topic", "type": "str", "required": True, "description": "Incoming topic"},
    ]
    assert "private" not in json.dumps(advertised)
    link = _link(receiver)
    stored_link, stored_receiver = _resolve(base, link)
    assert stored_link["owner_id"] == "sender"
    assert stored_receiver["owner_id"] == "receiver"
    assert json.loads(stored_receiver["snapshot_json"])["entry_point"] == "entry"
    assert not db_path(base).samefile(base / ".runs.db")


@pytest.mark.parametrize("principal", [None, "outsider", "sender"])
def test_only_receiver_owner_can_change_receiver(env, principal):
    _, auth = env
    auth("receiver")
    receiver = _save()
    auth(principal)
    for operation in (
        lambda: _save(),
        lambda: _save(receiver_id=receiver["receiver_id"], expected_generation=1),
        lambda: api.revoke_receiver(
            receiver_id=receiver["receiver_id"], universe_id="u-receiver", expected_generation=1
        ),
        lambda: api.inspect_receiver(
            receiver_id=receiver["receiver_id"], owner_universe_id="u-receiver"
        ),
    ):
        with pytest.raises(store.ReceiverAccessDenied, match="receiver_or_link_not_found"):
            operation()


@pytest.mark.parametrize("permission", ["read", "write"])
def test_collaborator_cannot_manage_receivers_even_with_write_scope(env, permission):
    base, auth = env
    grant_universe_access(base, universe_id="u-receiver", actor_id="sender", permission=permission)
    auth("sender")
    with pytest.raises(store.ReceiverAccessDenied):
        _save()


@pytest.mark.parametrize("scopes", [[], ["tinyassets.extensions.read"]])
def test_scopes_are_required_in_addition_to_ownership(env, scopes):
    _, auth = env
    auth("receiver", scopes)
    with pytest.raises(store.ReceiverAccessDenied):
        _save()


def test_foreign_public_branch_is_not_receiver_or_source_authority(env):
    base, auth = env
    foreign = _seed(base, "outsider")
    foreign.visibility = "public"
    save_branch_definition(base, branch_def=foreign.to_dict())
    auth("receiver")
    with pytest.raises(store.ReceiverAccessDenied):
        _save(branch_def_id=foreign.branch_def_id)
    receiver = _save()
    auth("sender")
    with pytest.raises(store.ReceiverAccessDenied):
        _link(receiver, branch_def_id=foreign.branch_def_id)


def test_universe_authored_graph_is_owned_only_in_that_universe(env):
    base, auth = env
    _seed(base, "receiver", author="universe:u-receiver")
    auth("receiver")
    assert _save()["generation"] == 1
    grant_universe_access(base, universe_id="u-sender", actor_id="receiver", permission="admin")
    with pytest.raises(store.ReceiverAccessDenied):
        _save(universe_id="u-sender")


def test_default_deny_and_unrelated_contract_read_are_indistinguishable(env):
    _, auth = env
    auth("receiver")
    receiver = _save(allowed_senders=[])
    auth("sender")
    for rid in [receiver["receiver_id"], "absent"]:
        with pytest.raises(store.ReceiverAccessDenied, match="receiver_or_link_not_found"):
            api.inspect_receiver(receiver_id=rid)
    with pytest.raises(store.ReceiverAccessDenied):
        _link(receiver)


@pytest.mark.parametrize("senders", [None, "sender", ["*"], [""], [42], [" sender"]])
def test_sender_policy_requires_explicit_exact_principals(env, senders):
    _, auth = env
    auth("receiver")
    with pytest.raises(ValueError):
        _save(allowed_senders=senders)


@pytest.mark.parametrize(
    "mapping",
    [
        {},
        {"undeclared": "topic"},
        {"result": "unknown"},
        {"result": "topic", "extra": "topic"},
        {"result": ""},
        [],
    ],
)
def test_link_mapping_validates_source_and_receiver_contract(env, mapping):
    _, auth = env
    auth("receiver")
    receiver = _save()
    auth("sender")
    with pytest.raises(ValueError):
        _link(receiver, mapping=mapping)


@pytest.mark.parametrize(
    "field, required",
    [
        ({"default": "private default"}, False),
        ({"default_value": "private default"}, False),
        ({"default_value": None}, True),
        ({"default_value": None, "default": "ignored legacy"}, True),
    ],
)
def test_advertised_defaults_match_compiler_without_disclosing_values(env, field, required):
    base, auth = env
    _seed(
        base,
        "receiver",
        schema=[
            {"name": "topic", "type": "str", **field},
            {"name": "result", "type": "str"},
            {"name": "extra", "type": "str"},
        ],
    )
    auth("receiver")
    receiver = _save()
    assert receiver["contract"][0]["required"] is required
    auth("sender")
    advertised = api.inspect_receiver(receiver_id=receiver["receiver_id"])
    assert "private default" not in json.dumps(advertised)
    assert "ignored legacy" not in json.dumps(advertised)


@pytest.mark.parametrize("keys", ["topic", ["topic", "topic"], ["unknown"], [3]])
def test_contract_input_names_are_explicit_unique_declared_keys(env, keys):
    _, auth = env
    auth("receiver")
    with pytest.raises(ValueError):
        _save(input_keys=keys)


def test_update_invalidates_old_links_and_new_connection_uses_new_snapshot(env):
    base, auth = env
    auth("receiver")
    receiver = _save()
    auth("sender")
    old_link = _link(receiver)
    auth("receiver")
    branch = _seed(base, "receiver")
    branch.node_defs[0].prompt_template = "new private prompt {topic}"
    save_branch_definition(base, branch_def=branch.to_dict())
    revised = _save(receiver_id=receiver["receiver_id"], expected_generation=1)
    assert revised["generation"] == 2
    assert revised["snapshot_sha256"] != receiver["snapshot_sha256"]
    with pytest.raises(store.ReceiverConflict, match="receiver_generation_changed"):
        _resolve(base, old_link)
    auth("sender")
    with pytest.raises(store.ReceiverConflict):
        _link(receiver)
    assert _resolve(base, _link(revised))[1]["generation"] == 2


def test_revoke_and_disconnect_are_idempotent_and_deny_future_use(env):
    base, auth = env
    auth("receiver")
    receiver = _save()
    auth("sender")
    disconnected, active = _link(receiver), _link(receiver)
    for _ in range(2):
        assert api.disconnect_output(link_id=disconnected["link_id"], universe_id="u-sender")[
            "disconnected"
        ]
    with pytest.raises(store.ReceiverAccessDenied):
        _resolve(base, disconnected)
    auth("receiver")
    for _ in range(2):
        assert api.revoke_receiver(
            receiver_id=receiver["receiver_id"], universe_id="u-receiver", expected_generation=1
        )["revoked"]
    with pytest.raises(store.ReceiverConflict, match="receiver_revoked"):
        _save(receiver_id=receiver["receiver_id"], expected_generation=1)
    with pytest.raises(store.ReceiverAccessDenied):
        _resolve(base, active)
    auth("sender")
    with pytest.raises(store.ReceiverAccessDenied):
        _link(receiver)


def test_foreign_link_is_not_a_bearer_grant(env):
    base, auth = env
    auth("receiver")
    receiver = _save()
    auth("sender")
    link = _link(receiver)
    auth("outsider")
    with pytest.raises(store.ReceiverAccessDenied):
        api.disconnect_output(link_id=link["link_id"], universe_id="u-outsider")
    with pytest.raises(store.ReceiverAccessDenied):
        _resolve(base, link, "outsider", "u-outsider")
    with pytest.raises(store.ReceiverAccessDenied):
        _resolve(base, link, "sender", "u-other")


def test_current_admin_revocation_denies_next_mutation(env):
    base, auth = env
    auth("receiver")
    receiver = _save()
    revoke_universe_access(base, universe_id="u-receiver", actor_id="receiver")
    with pytest.raises(store.ReceiverAccessDenied):
        _save(receiver_id=receiver["receiver_id"], expected_generation=1)


def test_admin_authority_is_fenced_until_receiver_commit(env, monkeypatch):
    base, auth = env
    auth("receiver")
    original = store.save_receiver

    def competing_revoke(*args, **kw):
        # A second DB writer must not revoke between the permission read and
        # receiver commit. This is SQLite contention, not a mocked ACL result.
        conn = sqlite3.connect(db_path(base), timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("DELETE FROM universe_acl WHERE actor_id='receiver'")
        finally:
            conn.rollback()
            conn.close()
        return original(*args, **kw)

    monkeypatch.setattr(store, "save_receiver", competing_revoke)
    assert _save()["generation"] == 1


def test_concurrent_generation_updates_have_exactly_one_winner(env):
    _, auth = env
    auth("receiver")
    receiver = _save()
    identity = current_identity_or_none()

    def update(_):
        with identity_context(identity):
            try:
                return _save(receiver_id=receiver["receiver_id"], expected_generation=1)[
                    "generation"
                ]
            except store.ReceiverConflict as exc:
                return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, range(2)))
    assert outcomes.count(2) == 1
    assert outcomes.count("receiver_generation_changed") == 1


def _process_generation_update(base, receiver_id, ready, start, results):
    """A separate Python process, no shared connection or in-memory lock."""
    os.environ["TINYASSETS_DATA_DIR"] = str(base)
    initialize_author_server(base)
    ready.put(True)
    if not start.wait(20):
        raise RuntimeError("test did not start its competing processes")
    with identity_context(
        Identity(
            user_id="receiver",
            username="fixture",
            capabilities=["tinyassets.extensions.write"],
        )
    ):
        try:
            results.put(_save(receiver_id=receiver_id, expected_generation=1)["generation"])
        except store.ReceiverConflict as exc:
            results.put(str(exc))


def test_separate_process_generation_race_has_exactly_one_winner(env):
    base, auth = env
    auth("receiver")
    receiver = _save()
    context = multiprocessing.get_context("spawn")
    ready, results = context.Queue(), context.Queue()
    start = context.Event()
    children = [
        context.Process(
            target=_process_generation_update,
            args=(base, receiver["receiver_id"], ready, start, results),
        )
        for _ in range(2)
    ]
    try:
        for child in children:
            child.start()
        for _ in children:
            assert ready.get(timeout=20)
        start.set()
        outcomes = [results.get(timeout=20) for _ in children]
        for child in children:
            child.join(timeout=10)
            assert child.exitcode == 0
        assert outcomes.count(2) == 1
        assert outcomes.count("receiver_generation_changed") == 1
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
        ready.close()
        results.close()


@pytest.mark.parametrize("generation", [None, True, "1", 0, 2])
def test_stale_or_malformed_generation_does_not_mutate(env, generation):
    _, auth = env
    auth("receiver")
    receiver = _save()
    with pytest.raises(store.ReceiverConflict):
        _save(receiver_id=receiver["receiver_id"], expected_generation=generation)
    with pytest.raises(store.ReceiverConflict):
        api.revoke_receiver(
            receiver_id=receiver["receiver_id"],
            universe_id="u-receiver",
            expected_generation=generation,
        )
    assert not api.inspect_receiver(
        receiver_id=receiver["receiver_id"], owner_universe_id="u-receiver"
    )["revoked"]
    auth("sender")
    with pytest.raises(store.ReceiverConflict):
        _link(receiver, expected_generation=generation)


def test_receiver_can_remove_sender_without_disclosing_revised_contract(env):
    base, auth = env
    auth("receiver")
    receiver = _save()
    auth("sender")
    link = _link(receiver)
    auth("receiver")
    _save(receiver_id=receiver["receiver_id"], expected_generation=1, allowed_senders=[])
    auth("sender")
    with pytest.raises(store.ReceiverAccessDenied):
        api.inspect_receiver(receiver_id=receiver["receiver_id"])
    with pytest.raises(store.ReceiverAccessDenied):
        _resolve(base, link)


def test_contract_read_requires_read_scope(env):
    _, auth = env
    auth("receiver")
    receiver = _save()
    auth("sender", ["tinyassets.extensions.write"])
    with pytest.raises(store.ReceiverAccessDenied):
        api.inspect_receiver(receiver_id=receiver["receiver_id"])


def test_empty_mapping_is_valid_when_receiver_requires_no_delivered_inputs(env):
    base, auth = env
    _seed(
        base,
        "receiver",
        schema=[
            {"name": "topic", "type": "str", "default_value": "receiver private default"},
            {"name": "result", "type": "str"},
            {"name": "extra", "type": "str"},
        ],
    )
    auth("receiver")
    receiver = _save()
    auth("sender")
    link = _link(receiver, mapping={})
    assert json.loads(_resolve(base, link)[0]["mapping_json"]) == {}


@pytest.mark.parametrize("operation", ["connect", "revoke", "disconnect"])
def test_all_management_mutations_hold_authority_fence(env, monkeypatch, operation):
    base, auth = env
    auth("receiver")
    receiver = _save()
    auth("sender")
    link = _link(receiver)
    methods = {
        "connect": "connect_output",
        "revoke": "revoke_receiver",
        "disconnect": "disconnect_output",
    }
    original = getattr(store, methods[operation])

    def compete(*args, **kwargs):
        conn = sqlite3.connect(db_path(base), timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("DELETE FROM universe_acl WHERE actor_id='sender'")
        finally:
            conn.rollback()
            conn.close()
        return original(*args, **kwargs)

    monkeypatch.setattr(store, methods[operation], compete)
    if operation == "connect":
        assert _link(receiver)["link_id"]
    elif operation == "disconnect":
        assert api.disconnect_output(link_id=link["link_id"], universe_id="u-sender")[
            "disconnected"
        ]
    else:
        auth("receiver")
        assert api.revoke_receiver(
            receiver_id=receiver["receiver_id"], universe_id="u-receiver", expected_generation=1
        )["revoked"]
