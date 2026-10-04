"""No timer lives in a box or jail; every platform timer is classified (design D7).

The scan finds every loop that waits on a clock (a ``while``/``for`` whose body
calls ``sleep``/``wait``) and every call that schedules a callback for later in
``tinyassets/``, and
requires each to be classified in ``tests/control_plane_timer_inventory.py``.
A ``box`` classification is forbidden outright, and the jails that run a
command center's code cannot outlive the call that started them, so nothing
inside one can keep time on its own.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests import control_plane_timer_inventory as inventory

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tinyassets"
_WAITS = {"sleep", "wait"}


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _waits(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and _call_name(n) in _WAITS for n in ast.walk(node))


#: Calls that schedule a callback for later: a self-rescheduling timer needs no
#: loop at all.
_SCHEDULERS = {"Timer", "call_later", "call_at"}


def _scan() -> set[str]:
    """Every clock-driven site, one key each.

    A ``while``/``for``/``async for`` whose body sleeps or waits, and every call
    that schedules a callback later (``threading.Timer``, ``loop.call_later``/
    ``call_at``, ``sched.scheduler``). The n-th such site in one
    function is ``<qualname>#n``, so a second loop added beside a classified one
    is a new key.
    """
    found: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        found |= _sites_in(path.read_text(encoding="utf-8"), rel)
    return found


def _sites_in(source: str, rel: str) -> set[str]:
    found: set[str] = set()
    tree = ast.parse(source, filename=rel)
    imports_scheduler = any(
        isinstance(node, ast.ImportFrom) and node.module == "sched"
        and any(alias.name == "scheduler" and alias.asname is None for alias in node.names)
        for node in ast.walk(tree)
    )
    stack: list[str] = []
    seen: dict[str, int] = {}

    def record(suffix: str) -> None:
        base = f"{rel}::{'.'.join(stack) or '<module>'}{suffix}"
        seen[base] = seen.get(base, 0) + 1
        found.add(base if seen[base] == 1 else f"{base}#{seen[base]}")

    def visit(node: ast.AST) -> None:
        scoped = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        if scoped:
            stack.append(node.name)
        if isinstance(node, (ast.While, ast.For, ast.AsyncFor)) and _waits(node):
            record("")
        if isinstance(node, ast.Call) and _call_name(node) in _SCHEDULERS:
            record(f" [{_call_name(node)}]")
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute) and func.attr == "scheduler"
                and isinstance(func.value, ast.Name) and func.value.id == "sched"
            ) or (
                imports_scheduler and isinstance(func, ast.Name) and func.id == "scheduler"
            ):
                record(" [sched]")
        for child in ast.iter_child_nodes(node):
            visit(child)
        if scoped:
            stack.pop()

    visit(tree)
    return found


def test_the_scan_sees_loops_without_while_and_self_rescheduling_callbacks():
    source = """
import asyncio, itertools, threading, sched
from sched import scheduler
async def ticker():
    for _ in itertools.count():
        await asyncio.sleep(60)
def again(loop):
    loop.call_later(60, again, loop)
def scheduled():
    timer = sched.scheduler()
    def again():
        timer.enter(60, 1, again)
    again()
    timer.run()
def imported():
    return scheduler()
def two():
    while True:
        threading.Event().wait(1)
    while True:
        threading.Event().wait(1)
"""
    assert _sites_in(source, "m.py") == {
        "m.py::ticker", "m.py::again [call_later]", "m.py::two", "m.py::two#2",
        "m.py::scheduled [sched]", "m.py::imported [sched]",
    }


def test_every_periodic_loop_is_classified_and_none_is_stale():
    found = _scan()
    listed = set(inventory.SITES)
    assert sorted(found - listed) == [], (
        "a new clock-driven loop must be classified in "
        "tests/control_plane_timer_inventory.py (D7: boxes keep no timers)"
    )
    assert sorted(listed - found) == [], "inventory entries whose loop is gone"


def test_no_timer_is_classified_as_living_in_a_box():
    assert {cls for cls, _note in inventory.SITES.values()} <= inventory.CLASSES
    assert [site for site, (cls, _n) in inventory.SITES.items() if cls == inventory.BOX] == []


def test_jailed_processes_cannot_outlive_their_call(tmp_path, monkeypatch):
    """The four-tool jail (built on ``provider_jail.jail_argv``, which provider
    CLIs share) and the code-node jail (``_bwrap_argv``) die with the platform
    process that started the call, in their own pid namespace: nothing a
    command center runs can keep a timer."""
    from tinyassets import universe_tools
    from tinyassets.node_sandbox import _bwrap_argv
    from tinyassets.providers import provider_jail

    universe = tmp_path / "data" / "cc-jail"
    universe.mkdir(parents=True)
    monkeypatch.setattr(provider_jail, "BWRAP_RESOLVER", lambda: "/usr/bin/bwrap")
    tool = universe_tools.tool_jail_argv(universe, ["/bin/true"], agent_id="main")
    node = _bwrap_argv(bwrap_path="/usr/bin/bwrap")
    for argv in (tool, node):
        assert "--die-with-parent" in argv
        assert "--unshare-all" in argv


def test_the_scheduler_package_imports_nothing_from_the_jail_side():
    """The trigger table and tick are control-plane code: they must not reach
    for a jail or a command center's files to decide anything."""
    forbidden = {"universe_tools", "provider_jail", "node_sandbox", "universe_files"}
    for path in (PACKAGE / "control_plane").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            else:
                continue
            for module in modules:
                assert not forbidden & set(module.split(".")), f"{path.name} imports {module}"
