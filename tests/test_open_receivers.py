"""An owner opens one of their steps to ANY authenticated user, and is told who sent.

Drives the real canonical handles with two independently authenticated principals
who share no ACL: ``receiver`` owns the exposed step, ``outsider`` is a stranger
never named in ``allowed_senders``. Only the external provider is a double.

Why this exists: on 2026-09-26 a founder asked their universe, as a naive user, for
"a node that any user's universe can send to directly". The primitive could not
express it -- ``allowed_senders`` had to enumerate principals and empty meant
nobody -- so the agent minted a public webhook and published the URL, which is
anonymous content attached to no universe. These tests pin the three things that
were missing: opening, finding, and attribution.
"""
# ruff: noqa: F811 -- pytest resolves imported fixtures by parameter name

from __future__ import annotations

import json

import pytest

from tests.test_delivery_runtime import provider_probe  # noqa: F401
from tests.test_receiver_links import env  # noqa: F401
from tinyassets import universe_server as server
from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
from tinyassets.daemon_server import save_branch_definition


#: The receiving branch DECLARES both reserved attribution fields and its node
#: renders them into the prompt.
#:
#: ``defaults=True`` (the default here) gives each field a ``default_value``, which
#: matters for mutation sensitivity: a missing injection then produces a prompt
#: carrying the placeholder rather than a required-input refusal, so the assertion
#: that catches it is the rendered prompt itself and not an incidental preflight.
#: ``defaults=False`` is the handbook's exact declaration, which is the shape a real
#: agent will write -- see the test that uses it.
def _attributed_schema(defaults=True):
    attribution = [{"name": name, "type": "str"} for name in
                   ("delivery_sender_id", "delivery_sender_universe_id")]
    if defaults:
        for field, placeholder in zip(attribution, ("nobody", "nowhere")):
            field["default_value"] = placeholder
    return [
        {"name": "topic", "type": "str", "description": "Incoming topic"},
        *attribution,
        {"name": "result", "type": "str"},
        {"name": "extra", "type": "str"},
    ]


def _seed_attributed(base, defaults=True):
    """Re-save b-receiver so its node actually READS who sent the deliverable."""
    branch = BranchDefinition(
        branch_def_id="b-receiver",
        name="Private graph",
        author="receiver",
        visibility="private",
        node_defs=[
            NodeDefinition(
                node_id="definition",
                display_name="private node name",
                prompt_template=(
                    "private prompt {topic} from {delivery_sender_id} "
                    "at {delivery_sender_universe_id}"
                ),
                input_keys=["topic", "delivery_sender_id", "delivery_sender_universe_id"],
                output_keys=["result", "extra"],
            )
        ],
        graph_nodes=[GraphNodeRef(id="entry", node_def_id="definition")],
        edges=[EdgeDefinition("START", "entry"), EdgeDefinition("entry", "END")],
        entry_point="entry",
        state_schema=_attributed_schema(defaults),
    )
    save_branch_definition(base, branch_def=branch.to_dict())


@pytest.fixture
def two_users(env):
    base, authenticate = env

    def auth(owner):
        authenticate(owner, capabilities=[
            "tinyassets.extensions.read", "tinyassets.extensions.write",
            "tinyassets.extensions.costly",
        ])

    _seed_attributed(base)
    return base, auth


def _create(operation="create", **payload):
    args = {
        "branch_def_id": "b-receiver", "node_id": "entry",
        "input_keys": ["topic"], "allowed_senders": [], "description": "Send a topic",
    }
    args.update(payload)
    return json.loads(server.write_graph(
        target="receiver", operation=operation, graph_id="u-receiver",
        payload_json=json.dumps(args),
    ))


def _connect(receiver, *, graph="u-outsider", branch="b-outsider"):
    return json.loads(server.write_graph(
        target="output_link", operation="connect", graph_id=graph,
        payload_json=json.dumps({
            "branch_def_id": branch, "node_id": "entry",
            "receiver_id": receiver["receiver_id"],
            "expected_generation": receiver["generation"],
            "mapping": {"result": "topic"},
        }),
    ))


def _send(link, value="from a stranger 🍉", *, occurrence="one", graph="u-outsider"):
    return json.loads(server.run_graph(
        operation="deliver_output", graph_id=graph,
        inputs_json=json.dumps({
            "link_id": link["link_id"], "occurrence_id": occurrence,
            "outputs": {"result": value},
        }),
    ))


def _discover(graph, query=""):
    return json.loads(server.read_graph(target="receivers", graph_id=graph, query=query))


# ---------------------------------------------------------------------------
# Opening it
# ---------------------------------------------------------------------------


















# ---------------------------------------------------------------------------
# Finding it
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# Attribution cannot be forged, and the sender learns nothing
# ---------------------------------------------------------------------------














# ---------------------------------------------------------------------------
# The abuse bound
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


