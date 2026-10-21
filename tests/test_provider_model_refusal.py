"""A source refusing ONE model is not an unreadable reply, and not a dead end.

Live 2026-09-28, free-only universe ``u-01ky3zh1arr8qth8jee7zx63pq``, turn
``b1675c7d6bc742ba9db964c666a5fad6``: round 1 on ``qwen/qwen3.8-27b:free`` was
rate limited (429), the capacity path moved on, and round 2 on
``thinkingmachines/inkling:free`` got HTTP 403. The owner read "the connected
model replied in a format this universe could not read" -- nothing had replied
-- with detail "compute provider returned HTTP 403": the body naming WHICH
refusal it was had been thrown away. The router had also cooled the whole
connection, and the turn stopped with more accepted free models in its order.

Two days earlier the same universe died on our own context measurement: a large
tool result overflowed a 262k-token model while a 1M-token free model sat in the
same order.

Every test here drives the real writer, router, ``ApiKeyHttpProvider``,
coordinator and journal; only the wire is synthetic.
"""

import json
from dataclasses import replace

from tests import test_interactive_http_agent as integration

rig = integration.rig
reader = integration.reader
served = integration.served
agent = integration.agent

#: The envelope OpenRouter used on the same turn's 429 (server log, 2026-09-28
#: 19:46Z): ``{"error": {"message", "code", "metadata"}, "user_id"}``. The 403's
#: own body was never recorded -- that is the bug -- so the words are synthetic
#: and the shape is recorded.
RECORDED_SHAPE_403 = json.dumps({
    "error": {
        "message": "Synthetic refusal: this model is not available to this key",
        "code": 403,
        "metadata": {"provider_name": "Synthetic Lab", "model_slug": "vendor/model:free"},
    },
    "user_id": "user_synthetic",
})


def _order(agent, monkeypatch, models, *, contexts=None):
    """The owner's explicit order: the selected model, then ``models``."""
    from tinyassets.providers import discovery_snapshot
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.discovery_protocols import discovery_protocol
    from tinyassets.providers.model_policy import (
        Catalog,
        ModelPolicy,
        ModelRef,
        SourceModelPolicy,
    )

    contexts = contexts or {}
    original = discovery_snapshot.read_http_discovery_document

    def added(**kwargs):
        value = original(**kwargs)
        if "models/user" in kwargs["url"]:
            for row in value["data"]:
                if row["id"] in contexts:
                    row["context_length"] = contexts[row["id"]]
            for name in models:
                row = integration.authority_tests.snapshot_tests._model(name)
                if name in contexts:
                    row["context_length"] = contexts[name]
                value["data"].append(row)
        return value

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", added)
    snapshot = integration.authority_tests.snapshot_tests._refresh(agent.served.rig)
    selected = agent.served.context.model_selection
    interaction = replace(
        discovery_protocol(snapshot.models.provider_scope).text_interaction, needs_tools=True,
    )
    agent.served.context = replace(agent.served.context, agent_model_plan=AgentModelPlan(
        Catalog("owner", agent.served.context.universe_dir.name, (snapshot.models,)),
        ModelPolicy(
            generation=7, mode="explicit", saved_default=selected,
            fallbacks=tuple(ModelRef(selected.connection_id, name) for name in models),
        ),
        interaction,
        policy_source="saved",
        # As production builds it (served_model_plan): one policy per source,
        # whose interaction REPLACES the plan's for that source's models.
        source_policies=(SourceModelPolicy(selected.connection_id, interaction),),
    ))


def _record(exc):
    from tinyassets.universe_server import _served_failure_notice, _served_failure_record

    record = _served_failure_record(exc)
    return record, _served_failure_notice(exc, record)


# --------------------------------------------------------------------------
# Classification: the provider's refusal, in the provider's words.
# --------------------------------------------------------------------------










# --------------------------------------------------------------------------
# Fallback: the next model the owner accepted answers the turn.
# --------------------------------------------------------------------------


















# --------------------------------------------------------------------------
# Context fit: our own measurement moves the turn to a model that fits.
# --------------------------------------------------------------------------
