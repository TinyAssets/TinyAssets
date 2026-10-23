"""S24 send-path: transports cannot grow another owner-message intake."""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.verifies("owner-daemon-in-box:One durable owner send intake (S24)")

ROOT = Path(__file__).resolve().parents[1]


def violations(root):
    problems = []
    homes = {
        "admit_owner_send": "tinyassets/owner_door/send.py",
        "deliver_owner_send": "tinyassets/universe_server.py",
        "_dispatch_owner_send": "tinyassets/universe_server.py",
    }
    for path in (root / "tinyassets").rglob("*.py"):
        name = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }

        def function_of(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node.name
            return ""

        aliases = {
            alias.asname or alias.name: alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = node.value
                referred = (value.attr if isinstance(value, ast.Attribute) else
                            value.id if isinstance(value, ast.Name) else '')
                if referred:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            aliases[target.id] = aliases.get(referred, referred)
        counts = dict.fromkeys(homes, 0)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                sql = node.value.lower()
                if re.search(r'\b(insert|replace|update)\b', sql) and 'conversation_turns' in sql:
                    function = function_of(node)
                    allowed = (name == 'tinyassets/conversation_store.py' and function in {
                        'record_turn', 'admit_owner_send', '_record_pair',
                        'bind_send_execution', 'backfill_once',
                    }) or (name == 'tinyassets/storage/conversation_run_admissions.py'
                           and function == '_write_pair')
                    if not allowed:
                        problems.append(f'{name}:{node.lineno}: unreviewed transcript SQL')
            if isinstance(node, ast.Call):
                called = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                called = aliases.get(called, called)
                function = function_of(node)
                store_file = name == "tinyassets/conversation_store.py"
                projection = name == "tinyassets/storage/conversation_run_admissions.py"
                keywords = {k.arg: k.value for k in node.keywords}
                if called == "record_turn":
                    speaker = keywords.get("speaker", node.args[2] if len(node.args) > 2 else None)
                    safe = isinstance(speaker, ast.Constant) and speaker.value in (
                        "platform",
                        "universe",
                    )
                    if not safe and not (store_file and function == "_sync_tail_impl"):
                        problems.append(f"{name}:{node.lineno}: owner row writer {called}")
                if called in {
                    "_record_pair",
                    "record_exchange",
                    "record_exchange_turns",
                    "record_failure",
                }:
                    admitted = keywords.get("founder_turn")
                    legacy_wrapper = store_file and function in {
                        'record_exchange', 'record_exchange_turns', 'record_failure'}
                    if not legacy_wrapper and (
                        admitted is None or isinstance(admitted, ast.Constant)
                    ):
                        problems.append(f"{name}:{node.lineno}: unadmitted pair {called}")
                if called == "_write_pair" and not (projection and function == 'project_terminal'):
                    problems.append(f"{name}:{node.lineno}: second projection writer")
                if called in {"execute", "executemany", "executescript"} and node.args:
                    sql = ast.unparse(node.args[0]).lower()
                    if (
                        re.search(r"\b(insert|replace|update)\b", sql)
                        and "conversation_turns" in sql
                    ):
                        allowed = (
                            store_file
                            and function
                            in {
                                "record_turn",
                                "admit_owner_send",
                                "_record_pair",
                                "bind_send_execution",
                                "backfill_once",
                            }
                        ) or (projection and function == "_write_pair")
                        if not allowed:
                            problems.append(f"{name}:{node.lineno}: unreviewed transcript writer")
                if called in homes:
                    counts[called] += 1
                if called in homes and name != homes[called]:
                    problems.append(f"{name}:{node.lineno}: second send intake {called}")
        for called, count in counts.items():
            if count > 1:
                problems.append(f"{name}: multiple calls to {called}")
        if "/app/turn/steer" in source or "class AppAcceptance" in source:
            problems.append(f"{name}: retired send transport")
    calls = []
    for path in (root / "tinyassets").rglob("*.js"):
        name = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\bMCP\s*\.\s*converse\s*\(", source):
            calls.append(name)
        wire_calls = re.findall(r'callTool\s*\(\s*[\'"]converse[\'"]', source)
        if wire_calls:
            if name != "tinyassets/onboarding/app/main.js" or len(wire_calls) != 1:
                problems.append(f"{name}: second converse transport")
        if re.search(r"/app/turn/steer|claimHeldLines|function\s+(?:queueTurn|sendBatch)", source):
            problems.append(f"{name}: retired send path")
    if calls != ["tinyassets/onboarding/app/send_journal.js"]:
        problems.append(f"expected one browser send call, got {calls}")
    return problems


def test_send_path():
    assert not (found := violations(ROOT)), "\n".join(found)


def test_send_path_rejects_second_transport(tmp_path):
    app = tmp_path / "tinyassets/onboarding/app"
    app.mkdir(parents=True)
    (app / "send_journal.js").write_text("MCP.converse(message);")
    assert not violations(tmp_path)
    (app / "extra.js").write_text("MCP.converse(message);")
    assert violations(tmp_path)
    (app / "extra.js").write_text('fetch("/app/turn/steer", {});')
    assert violations(tmp_path)
    (app / "extra.js").unlink()
    (app / "extra.py").write_text("store.admit_owner_send(home, session, id, message, opts)")
    assert violations(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        'record_turn(home, session, "founder", message)',
        "record_turn(home, session, speaker=role, text=message)",
        '_record_pair(home, session, message, reply, speaker="universe")',
        "_write_pair(scope, row)",
        "record_exchange_turns(home, session, message, reply)",
        'db.execute("INSERT INTO conversation_turns VALUES (?)", (message,))',
        'sql="INSERT INTO conversation_turns VALUES (?)"\ndb.execute(sql, (message,))',
        'writer=store.record_turn\nwriter(home, session, "founder", message)',
    ],
)
def test_guard_detects_owner_row_writers(tmp_path, source):
    app = tmp_path / "tinyassets/onboarding/app"
    app.mkdir(parents=True)
    (app / "send_journal.js").write_text("MCP.converse(message);")
    (app / "extra.py").write_text(source)
    assert violations(tmp_path)
    (app / "extra.py").write_text(
        "from tinyassets.owner_door.send import deliver_owner_send as again\nagain()"
    )
    assert violations(tmp_path)
