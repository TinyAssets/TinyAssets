"""Accepted structured delivery executes its pinned graph as the receiver.

Real graph, SQLite, executor and OS attempt lock. Only the external provider is
a deterministic test double. This is internal execution proof, not public intake.
"""

# ruff: noqa: F811 -- pytest resolves the imported fixture by parameter name

import contextvars
import json

import pytest

from tests.test_delivery_attempts import reserved  # noqa: F401
from tests.test_delivery_reservations import delivery_env  # noqa: F401
from tests.test_receiver_links import env as management_env  # noqa: F401
from tinyassets.auth.middleware import current_identity_or_none


@pytest.fixture
def provider_probe(monkeypatch):
    calls = []
    sender_only = contextvars.ContextVar("sender_private_context", default=None)
    sender_only.set("must not cross to receiver")

    def bind(provider_call, universe_id, *, principal_id):
        assert current_identity_or_none().user_id == "receiver"
        assert sender_only.get() is None
        assert principal_id == "receiver"
        assert universe_id == "u-receiver"

        def provider(prompt, system="", **kwargs):
            # The provider owns explicit receiver binding: prompt timeout workers
            # intentionally have no ambient request ContextVars to borrow.
            assert principal_id == "receiver"
            assert universe_id == "u-receiver"
            assert sender_only.get() is None
            calls.append(prompt)
            return json.dumps({"result": "processed exactly 🧪", "extra": "receiver-private"})

        return provider

    monkeypatch.setattr("tinyassets.api.runs._bind_run_provider_call", bind)
    return calls
