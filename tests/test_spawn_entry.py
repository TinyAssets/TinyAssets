"""spawn-entry (S2, C8): every agent starts through one function.

``tinyassets/control_plane/session.py`` ``start(agent_ref, input, parent)`` is
the only place an agent reference becomes a running turn. The executors it
drives (the served writer turn, the workflow agent turn, the shared-self
preparation) and the one agent-file loader are called from nowhere else.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SESSION = "tinyassets/control_plane/session.py"

#: ``(regex, files that may contain it)``. A match anywhere else is a second starter.
STARTER_CALLS = (
    # The served writer turn: converse() in universe_intelligence.
    (r"\bintelligence\.converse\(|\buniverse_intelligence\.converse\(|\b_converse_impl\(",
     {SESSION}),
    (r"\bcall_foreground_work_agent\(", {SESSION, "tinyassets/workflow_agent.py"}),
    (r"\bcall_background_work_agent\(|\b_call_work_agent\(", set()),
    (r"\b_call_writer\(", {"tinyassets/universe_intelligence.py"}),
    (r"\bWorkflowAgentTurn\(", {"tinyassets/workflow_agent.py"}),
    (r"\bmake_interactive_agent_turn\(", {"tinyassets/providers/call.py",
                                             "tinyassets/universe_intelligence.py"}),
    (r"\bcall_interactive_agent_turn\(", {"tinyassets/providers/call.py",
                                             "tinyassets/universe_intelligence.py"}),
    (r"\bprepare_shared_self_turn\(", {SESSION, "tinyassets/shared_self.py"}),
    # The one agent-file loader: what an agent IS is read in one place, and
    # addressed_agents resolves ids with it.
    (r"\bload_agent\(", {SESSION, "tinyassets/agent_definition.py",
                         "tinyassets/addressed_agents.py"}),
)

INGRESS_SITES = {
    "tinyassets/universe_server.py": 1,
    "tinyassets/control_plane/activity_session.py": 1,
    "tinyassets/foreground_run_provider.py": 1,
    "tinyassets/ta_capabilities.py": 1,
}


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
        offenders.extend(_executor_bypasses(rel, text))
        code = "\n".join(line for line in text.splitlines()
                         if not line.lstrip().startswith("#"))
        for pattern, allowed in STARTER_CALLS:
            if rel not in allowed and re.search(pattern, code):
                offenders.append(f"{rel}: {pattern}")
    assert not offenders, (
        "an agent is started outside tinyassets/control_plane/session.py start(agent_ref, "
        "input, parent):\n  " + "\n  ".join(offenders))


def _executor_bypasses(rel, source):
    """Resolve import aliases and inspect calls/bases, including new subclass names."""
    tree = ast.parse(source)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for item in node.names:
                aliases[item.asname or item.name] = f'{node.module}.{item.name}'
        elif isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name] = item.name

    def name(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f'{name(node.value)}.{node.attr}'
        return ''

    allowed = {
        'AgentTurnCoordinator': {'tinyassets/interactive_http_agent.py',
                                 'tinyassets/workflow_agent.py'},
        'InteractiveHttpAgentTurn': {'tinyassets/providers/call.py'},
        'WorkflowAgentTurn': {'tinyassets/workflow_agent.py'},
        'tinyassets.universe_intelligence.converse': {SESSION},
    }
    problems = []
    for node in ast.walk(tree):
        targets = node.bases if isinstance(node, ast.ClassDef) else (
            [node.func] if isinstance(node, ast.Call) else [])
        for target in targets:
            resolved = name(target)
            key = resolved if resolved in allowed else resolved.rsplit('.', 1)[-1]
            if key in allowed and rel not in allowed[key]:
                problems.append(f'{rel}: {resolved}')
            if (key == 'text_sync' and rel != 'tinyassets/universe_intelligence.py'
                    and any(isinstance(item, ast.keyword)
                            and item.arg == 'engine_mcp_enabled'
                            and not (isinstance(item.value, ast.Constant)
                                     and item.value.value is False)
                            for item in ast.walk(tree))):
                problems.append(f'{rel}: tools-enabled text_sync')
    return problems


def test_all_live_ingress_sites_call_session_start():
    for rel, expected in INGRESS_SITES.items():
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert len(re.findall(r"\bsession\.start(?:\(|,)", source)) == expected, rel
    source = (ROOT / SESSION).read_text(encoding="utf-8")
    assert "from tinyassets.universe_intelligence import converse" in source
    assert "from tinyassets.workflow_agent import call_foreground_work_agent" in source


@pytest.mark.parametrize("source", [
    "from tinyassets.interactive_http_agent import InteractiveHttpAgentTurn as Turn\nTurn()",
    "from tinyassets.agent_turn_coordinator import AgentTurnCoordinator as Base\n"
    "class NewLoop(Base): pass",
    "from tinyassets.providers.call import text_sync as call\n"
    "call('hi', config=ModelConfig(engine_mcp_enabled=True))",
    "from tinyassets.universe_intelligence import converse as chat\nchat('hi')",
])
def test_fresh_executor_bypasses_are_rejected(monkeypatch, source):
    monkeypatch.setattr(__import__(__name__, fromlist=['_python_files']),
                        '_python_files', lambda: iter([('tinyassets/new_path.py', source)]))
    with pytest.raises(AssertionError, match="outside"):
        test_no_executor_is_started_outside_the_starter()


def test_new_session_start_caller_is_allowed(monkeypatch):
    monkeypatch.setattr(__import__(__name__, fromlist=['_python_files']),
                        '_python_files', lambda: iter([
                            ('tinyassets/new_ingress.py', 'session.start(agent, input, parent)')]))
    test_no_executor_is_started_outside_the_starter()


def test_every_parent_kind_resolves_the_agent_from_its_files(tmp_path, _owner_box_double):
    """``start`` loads the agent once, from ``agents/<name>/``, before any executor."""
    import pytest

    from tinyassets.control_plane import session

    assert _owner_box_double is not None, "the owner box double needs Linux"

    (tmp_path / "u").mkdir()
    folder = tmp_path / "u" / "agents" / "scout"
    folder.mkdir(parents=True)
    (folder / "AGENTS.md").write_text("Scout ahead.", encoding="utf-8")
    theirs = tmp_path / "u" / "agents" / "theirs"
    theirs.mkdir()
    (theirs / "settings.yaml").write_text("schema_version: 1\nagent: {owner: p}\n",
                                          encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()

    parent = session.ChildOf(base_path=tmp_path, universe_id="u", owner="o", agent_id="main")
    with pytest.raises(session.AgentUnavailable):
        session.start("nobody", "hi", parent)
    with pytest.raises(session.AgentUnavailable):
        session.start("scout", "hi", session.ChildOf(
            base_path=tmp_path, universe_id="elsewhere", owner="o", agent_id="main"))
    # A co-admin's own agent on this shared command center is not "o"'s to start.
    with pytest.raises(session.AgentUnavailable):
        session.start("theirs", "hi", parent)
    # A child started from inside an activity is refused before anything is queued.
    from tinyassets.agent_activities import ActivityRefused

    with pytest.raises(ActivityRefused):
        session.start("scout", "hi", session.ChildOf(
            base_path=tmp_path, universe_id="u", owner="o", agent_id="main",
            inside_activity=True))


pytestmark = pytest.mark.verifies('shape-convergence:C8')
