"""Real canonical handles connect two owners and process a structured occurrence."""
# ruff: noqa: F811 -- imported pytest fixtures
import json

import pytest

from tests.test_delivery_runtime import provider_probe  # noqa: F401
from tests.test_receiver_links import env  # noqa: F401
from tinyassets import universe_server as server


@pytest.fixture
def linked(env):
    base, authenticate = env
    def auth(owner):
        authenticate(owner, capabilities=[
            "tinyassets.extensions.read", "tinyassets.extensions.write",
            "tinyassets.extensions.costly",
        ])
    auth("receiver")
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
                                 "expected_generation": 1, "mapping": {"result": "topic"}}),
    ))
    assert "link_id" in link, link
    return base, auth, receiver, link


def _send(link, value="exact input 🍉", *, occurrence="one", graph="u-sender"):
    return json.loads(server.run_graph(
        operation="deliver_output", graph_id=graph,
        inputs_json=json.dumps({"link_id": link["link_id"], "occurrence_id": occurrence,
                               "outputs": {"result": value}}),
    ))












