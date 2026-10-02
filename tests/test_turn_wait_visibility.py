"""A running turn says which step and which model it is waiting on.

Live 2026-10-02, turn c6ae56f9 on the free-only account: round 4 waited about
ten minutes on one qwen request, and the app could only say "Your agent is
thinking...", which reads exactly like a hang. The coordinator now notes each
round it opens on the boot registry (display only), and /status carries it.
"""

from tests import test_interactive_http_agent as integration
from tinyassets.storage.agent_turn_boot import BOOT

rig = integration.rig
reader = integration.reader
served = integration.served
agent = integration.agent


def test_each_round_is_noted_while_it_waits_and_cleared_when_the_turn_ends(agent):
    agent.requested_rounds = 1
    seen = []
    uid = agent.served.context.universe_dir.name

    def during():
        turn = agent.latest()
        seen.append(BOOT.progress(uid, turn.turn_id))

    agent.before_reply = during
    assert integration.run(agent) == "finished exact answer"
    model = agent.served.context.model_selection.model_id
    assert [(p[0], p[1]) for p in seen] == [(1, model), (2, model)]
    assert BOOT.progress(uid, agent.latest().turn_id) is None


def test_the_status_row_names_the_step_while_the_model_is_asked(agent):
    from datetime import datetime, timezone

    from tinyassets.storage.agent_turn_journal import AgentTurnJournal

    rows = []
    base = agent.served.context.universe_dir.parent
    uid = agent.served.context.universe_dir.name

    def during():
        rows.append(AgentTurnJournal(base).universe_working_turn(
            uid, now=datetime.now(timezone.utc), max_age_s=3600,
        ))

    agent.before_reply = during
    integration.run(agent)
    assert rows[0]["state"] == "inference_started" and rows[0]["round"] == 1
    assert rows[0]["model"] == agent.served.context.model_selection.model_id
    assert rows[0]["round_age_s"] >= 0
