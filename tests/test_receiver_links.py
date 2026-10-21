"""Real request identities, universe ACLs and SQLite receiver/link records.

These prove management only; no public route, provider or delivery is invoked.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from tinyassets.api import receiver_links as api
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
from tinyassets.daemon_server import (
    grant_universe_access,
    initialize_author_server,
    save_branch_definition,
)
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












