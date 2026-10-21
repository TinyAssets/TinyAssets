"""Actual two-owner file delivery: sender bytes -> receiver-owned copy -> read.

The refusal/topology surface lives in ``test_delivery_file_bridge``. This module
proves the positive path the bridge exists for, end to end through the real
callers: sender custody bound to a running sender run, one delivery, receiver-
owned bytes the receiver reads through its OWN run binding. Nothing is stubbed --
the custody copy and the dispatch-time input rewrite both run for real.
"""
# ruff: noqa: F811 -- pytest resolves imported fixtures by their public names
import base64
import json
from contextlib import contextmanager

import pytest

from tests.test_delivery_node_rpc import _prepare, _source, node_env  # noqa: F401
from tests.test_delivery_public import linked  # noqa: F401
from tests.test_receiver_links import env  # noqa: F401
from tinyassets import runs
from tinyassets.api import deliveries as api
from tinyassets.authoring import service
from tinyassets.authoring.store import AuthoringStore
from tinyassets.branches import BranchDefinition
from tinyassets.daemon_server import get_branch_definition, save_branch_definition
from tinyassets.run_file_capture import capture_authoring_files
from tinyassets.run_file_reader import read_bound_file
from tinyassets.storage import deliveries
from tinyassets.storage import run_files as store

SENDER = ("sender", "u-sender")
RECEIVER = ("receiver", "u-receiver")


@pytest.fixture(autouse=True)
def custody_capacity(monkeypatch):
    """Cross-owner delivery needs the operational custody ceiling configured.

    Without it EVERY file delivery refuses ``file_custody_not_configured``: this
    env var is a real deployment prerequisite for the feature, not test scaffolding.
    """
    monkeypatch.setenv("TINYASSETS_RUN_FILE_CUSTODY_MAX_BYTES", str(64 * 1024 * 1024))


def receiver_file_link(base, auth, *, io_type="file", max_count=1, max_bytes=1 << 20):
    """Receiver declares a file input on its OWN branch, then exposes a link.

    The admitted snapshot governs, so the declaration must exist before the
    receiver and link are created.
    """
    from tinyassets import universe_server as server

    auth("receiver")
    branch = BranchDefinition.from_dict(get_branch_definition(base, branch_def_id="b-receiver"))
    definition = branch.to_dict()
    expected = "list" if io_type == "file_bundle" else "dict"
    definition["state_schema"] = [
        {**field, "type": expected} if field.get("name") == "topic" else field
        for field in branch.state_schema
    ]
    definition["io_manifest"] = {"inputs": [
        {"name": "topic", "io_type": io_type, "max_count": max_count, "max_bytes": max_bytes},
    ]}
    save_branch_definition(base, branch_def=definition)
    receiver = json.loads(server.write_graph(
        target="receiver", operation="create", graph_id="u-receiver",
        payload_json=json.dumps({"branch_def_id": "b-receiver", "node_id": "entry",
                                 "input_keys": ["topic"], "allowed_senders": ["sender"]}),
    ))
    assert "receiver_id" in receiver, receiver
    auth("sender")
    link = json.loads(server.write_graph(
        target="output_link", operation="connect", graph_id="u-sender",
        payload_json=json.dumps({"branch_def_id": "b-sender", "node_id": "entry",
                                 "receiver_id": receiver["receiver_id"],
                                 "expected_generation": receiver["generation"],
                                 "mapping": {"result": "topic"}}),
    ))
    assert "link_id" in link, link
    return BranchDefinition.from_dict(definition), link


def sender_custody(base, bodies, *, label="delivery source"):
    """Real sender-owned custody objects captured from real authoring handles."""
    authoring = AuthoringStore(base)
    authoring.initialize()
    session = service.start_session(
        actor_id="sender", artifact_kind="node", sketch="files", store=authoring,
    )["session_id"]
    sources = []
    for index, body in enumerate(bodies):
        handle = authoring.put_file_handle(
            session_id=session, owner_id="sender", input_name=f"file{index}",
            filename=f"brief-{index}.txt", media_type="text/plain",
            content=body, lifetime_seconds=3600,
        )
        sources.append({"session_id": session, "handle_id": handle["handle_id"]})
    refs = capture_authoring_files(
        base, owner_id=SENDER[0], universe_id=SENDER[1], label=label, sources=sources,
    )
    return authoring, refs


def running_sender_run(base, branch, refs, *, field="attachment"):
    """A running sender run with the captured files bound to it -- the trusted source."""
    run_id = _prepare(base, branch)
    with runs._connect(base) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store.bind_in_transaction(
            conn, run_id=run_id, owner_id=SENDER[0], universe_id=SENDER[1],
            field_name=field, file_ids=[ref["file_id"] for ref in refs],
        )
    runs.update_run_status(base, run_id, status="running")
    return run_id


def deliver(base, branch, link, outputs, run_id, *, occurrence="one", cancel=lambda: False):
    return api.deliver_node_output(
        base, source=_source(base, branch, run_id), link_id=link["link_id"],
        occurrence_id=occurrence, outputs=outputs, should_cancel=cancel,
    )


def transfer_rows(base, delivery_id):
    with deliveries.transaction(base) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM graph_delivery_files WHERE delivery_id=? ORDER BY field_name, ordinal",
            (delivery_id,),
        )]


def receiver_run_id(base, delivery_id):
    with deliveries.transaction(base) as conn:
        row = conn.execute(
            "SELECT run_id FROM graph_delivery_attempts WHERE delivery_id=? AND attempt=1",
            (delivery_id,),
        ).fetchone()
    assert row is not None, "acceptance must create the receiver's own run"
    return row["run_id"]


def read_all(base, run_id, file_id, *, owner=RECEIVER):
    """Read a bound file the ordinary way -- through the owner's own run binding."""
    body = b""
    offset = 0
    while True:
        chunk = read_bound_file(
            base, owner_id=owner[0], universe_id=owner[1], run_id=run_id,
            file_id=file_id, offset=offset, count=1 << 16,
        )
        body += base64.b64decode(chunk["bytes_base64"])
        offset = chunk["next_offset"]
        if chunk["eof"]:
            return body


def custody_rows(base, owner):
    with runs._connect(base) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM run_file_objects WHERE owner_id=? AND universe_id=?", owner,
        )]












def _hook_after_last_read(monkeypatch, action):
    """Run ``action`` after the final source read, before the publication fence.

    ``run_file_crossowner`` imports ``_capture_files`` by name, so BOTH bindings
    are patched: patching only the defining module would leave the cross-owner
    path untouched and quietly make this test vacuous. The hook records its own
    firing and every test asserts it fired.
    """
    import tinyassets.run_file_capture as capture
    import tinyassets.run_file_crossowner as crossowner

    original = capture._capture_files
    fired = []

    def patched(*args, **kwargs):
        inner_open = kwargs["open_source"]

        @contextmanager
        def opened(index, current, cancelled):
            with inner_open(index, current, cancelled) as chunks:
                yield chunks
            # The stream is closed and no platform writer is held: a concurrent
            # change commits HERE, strictly after the final source read and
            # strictly before the publication fence is taken.
            fired.append(index)
            action()

        kwargs["open_source"] = opened
        return original(*args, **kwargs)

    monkeypatch.setattr(capture, "_capture_files", patched)
    monkeypatch.setattr(crossowner, "_capture_files", patched)
    return fired








