"""spawn-entry (S2, C8): every agent starts through one function.

``tinyassets/control_plane/session.py`` ``start(agent_ref, input, parent)`` is
the only place an agent reference becomes a running turn. The executors it
drives (the served writer turn, the workflow agent turn, the shared-self
preparation) and the one agent-file loader are called from nowhere else.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSION = "tinyassets/control_plane/session.py"

#: ``(regex, files that may contain it)``. A match anywhere else is a second starter.
STARTER_CALLS = (
    # The served writer turn: converse() in universe_intelligence.
    (r"\bintelligence\.converse\(|\buniverse_intelligence\.converse\(|\b_converse_impl\(",
     {SESSION}),
    (r"\bcall_(?:fore|back)ground_work_agent\(", {SESSION, "tinyassets/workflow_agent.py"}),
    (r"\bprepare_shared_self_turn\(", {SESSION, "tinyassets/shared_self.py"}),
    # The one agent-file loader: what an agent IS is read in one place, and
    # addressed_agents resolves ids with it.
    (r"\bload_agent\(", {SESSION, "tinyassets/agent_definition.py",
                         "tinyassets/addressed_agents.py"}),
)


def _python_files():
    for path in sorted((ROOT / "tinyassets").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "__pycache__" in rel:
            continue
        yield rel, path.read_text(encoding="utf-8", errors="replace")


def test_exactly_one_starter_is_defined():
    source = (ROOT / SESSION).read_text(encoding="utf-8")
    assert len(re.findall(r"^def start\(", source, re.M)) == 1
    for rel, text in _python_files():
        if rel == SESSION:
            continue
        assert not re.search(
            r"^def (?:session_start|start_agent|start)\((?:\s*agent_ref|\s*agent\b)", text, re.M,
        ), f"{rel} defines a second starter; start every agent through {SESSION}"


def test_no_executor_is_started_outside_the_starter():
    offenders = []
    for rel, text in _python_files():
        code = "\n".join(line for line in text.splitlines()
                         if not line.lstrip().startswith("#"))
        for pattern, allowed in STARTER_CALLS:
            if rel not in allowed and re.search(pattern, code):
                offenders.append(f"{rel}: {pattern}")
    assert not offenders, (
        "an agent is started outside tinyassets/control_plane/session.py start(agent_ref, "
        "input, parent):\n  " + "\n  ".join(offenders))


def test_every_parent_kind_resolves_the_agent_from_its_files(tmp_path, _owner_box_double):
    """``start`` loads the agent once, from ``agents/<name>/``, before any executor."""
    import pytest

    from tinyassets.control_plane import session

    if _owner_box_double is None:
        pytest.skip("the owner box double needs Linux")

    (tmp_path / "u").mkdir()
    folder = tmp_path / "u" / "agents" / "scout"
    folder.mkdir(parents=True)
    (folder / "AGENTS.md").write_text("Scout ahead.", encoding="utf-8")
    theirs = tmp_path / "u" / "agents" / "theirs"
    theirs.mkdir()
    (theirs / "settings.yaml").write_text("schema_version: 1\nagent: {owner: p}\n",
                                          encoding="utf-8")
    from tinyassets.boxes.provider import ExecLimits
    from tinyassets.control_plane.binding import SessionBinding

    def admitted(center):
        # A child carries its parent's admitted binding on the owner box.
        (tmp_path / center).mkdir(exist_ok=True)
        handle = _owner_box_double.bind_under(tmp_path, center, account_id="o",
                                              turn_id=f"parent-{center}")
        return SessionBinding(_owner_box_double, handle, ExecLimits(), tmp_path / center)

    parent = session.ChildOf(base_path=tmp_path, universe_id="u", owner="o", agent_id="main",
                             box_binding=admitted("u"))
    with pytest.raises(session.AgentUnavailable):
        session.start("nobody", "hi", parent)
    with pytest.raises(session.AgentUnavailable):
        session.start("scout", "hi", session.ChildOf(
            base_path=tmp_path, universe_id="elsewhere", owner="o", agent_id="main",
            box_binding=admitted("elsewhere")))
    # A co-admin's own agent on this shared command center is not "o"'s to start.
    with pytest.raises(session.AgentUnavailable):
        session.start("theirs", "hi", parent)
    # A child started from inside an activity is refused before anything is queued.
    from tinyassets.agent_activities import ActivityRefused

    with pytest.raises(ActivityRefused):
        session.start("scout", "hi", session.ChildOf(
            base_path=tmp_path, universe_id="u", owner="o", agent_id="main",
            inside_activity=True, box_binding=admitted("u")))
