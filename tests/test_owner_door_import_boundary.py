"""The two doors are separated by imports, not by a list of exceptions.

ADR-011 (*The owner door is complete; only the model door bounds; the only
per-account input is account type*) and
``openspec/changes/archive/2026-09-30-owner-door-complete-reads/``.
On 2026-09-30 the founder's request rail vanished because the app read it
through the connector, whose 24 KB model ceiling cut his 34 KB queue. The fix
the founder asked for is one a future session cannot undo by accident: "not in
some rule but architecturally".

So these tests assert STRUCTURE:

1. the owner door imports nothing that bounds, projects, or is a model door;
2. the shared domain read it calls imports none of those either;
3. the bound and the projections are imported ONLY by model-door modules, so a
   new bound anywhere else fails here;
4. the account type is read from billing storage in exactly one place.

Each is a static walk of the whole AST (function-level imports included), plus
one runtime check that exercising the owner door's reads never loads the ceiling.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PKG = _ROOT / "tinyassets"

#: What bounds or projects a read for a model's context, and the model doors
#: themselves (reaching one would reach its projection).
_BOUNDING = frozenset({
    "tinyassets.engine_result_bounds",
    "tinyassets.engine_read_views",
    "tinyassets.universe_server",
    "tinyassets.engine_mcp_server",
})

#: The ONLY modules allowed to import the ceiling or the projections: the model
#: door. A new entry here is a design change, not a fix -- read ADR-011
#: first.
_MODEL_DOOR = frozenset({
    "tinyassets/universe_server.py",
    "tinyassets/engine_mcp_server.py",
    "tinyassets/engine_read_views.py",
})


def _package_of(path: Path) -> list[str]:
    parts = list(path.relative_to(_ROOT).with_suffix("").parts)
    return parts[:-1]  # a module's package; for __init__ that is the package itself


def _imports(path: Path, source: str | None = None) -> set[str]:
    """Every module this file names in an import, relative imports RESOLVED
    (``from ..engine_result_bounds import x`` names the absolute module)."""
    text = path.read_text(encoding="utf-8") if source is None else source
    tree = ast.parse(text, filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = _package_of(path)
                base = package[: len(package) - (node.level - 1)]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            if not module:
                continue
            found.add(module)
            # `from tinyassets import universe_server` names the module too.
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


def test_the_import_walk_resolves_relative_imports():
    """A relative import must not slip past the boundary (Codex, round 1)."""
    probe = _PKG / "owner_door" / "routes.py"
    names = _imports(probe, "from ..engine_result_bounds import bound_tool_text\n"
                            "from . import routes\n")
    assert "tinyassets.engine_result_bounds" in names
    assert "tinyassets.owner_door.routes" in names


def _rel(path: Path) -> str:
    return path.relative_to(_ROOT).as_posix()


def test_the_owner_door_cannot_import_a_bound_or_a_model_door():
    offenders = {
        _rel(path): sorted(_imports(path) & _BOUNDING)
        for path in (_PKG / "owner_door").rglob("*.py")
        if _imports(path) & _BOUNDING
    }
    assert offenders == {}, (
        "the owner door returns complete data and has nothing to bound; "
        f"move this to the model door instead: {offenders}"
    )


def test_the_shared_domain_reads_cannot_import_a_bound_or_a_model_door():
    for module in ("tinyassets/api/graph_reads.py", "tinyassets/api/status.py",
                   "tinyassets/api/pending_requests.py",
                   "tinyassets/storage/pending_requests.py",
                   "tinyassets/conversation_store.py"):
        leaked = sorted(_imports(_ROOT / module) & _BOUNDING)
        assert leaked == [], f"{module} is a shared domain read and imports {leaked}"


def test_only_the_model_door_imports_the_ceiling_or_the_projections():
    bound = {"tinyassets.engine_result_bounds", "tinyassets.engine_read_views"}
    importers = {
        _rel(path)
        for path in _PKG.rglob("*.py")
        if _imports(path) & bound
    }
    # The plugin mirror is a copy of the same tree; the canonical tree is the one
    # that is checked here.
    assert importers <= _MODEL_DOOR, sorted(importers - _MODEL_DOOR)


def test_the_account_type_is_read_from_billing_in_one_place():
    """`get_tier` reads the stored subscription. Only the resolver may call it;
    everything else asks `universe_owner` for an `AccountType`."""
    callers = set()
    for path in _PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else (
                    func.attr if isinstance(func, ast.Attribute) else "")
                if name == "get_tier":
                    callers.add(_rel(path))
    assert callers <= {"tinyassets/universe_owner.py",
                       "tinyassets/storage/subscription_state.py"}, sorted(callers)


def test_limits_take_an_account_type_and_nothing_else_about_the_account():
    import inspect

    from tinyassets.usage_policy import AccountType, limits_for

    parameters = list(inspect.signature(limits_for).parameters.values())
    assert [p.name for p in parameters] == ["account"]
    assert parameters[0].annotation in (AccountType, "AccountType")
    assert {member.value for member in AccountType} == {"free", "paid"}


_RUNTIME_PROBE = r"""
import json, os, sys, tempfile
base = tempfile.mkdtemp()
os.environ["TINYASSETS_DATA_DIR"] = base
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.daemon_server import grant_universe_ownership, set_founder_home
home = "u-probeprobeprobe0"
os.makedirs(os.path.join(base, home))
open(os.path.join(base, home, "soul.md"), "w").write("# probe")
grant_universe_ownership(base, universe_id=home, owner_id="probe_owner")
set_founder_home(base, founder_sub="probe_owner", universe_id=home, platform_generated=True)
from tinyassets.api.graph_reads import read_graph
from tinyassets.api.status import get_status
from tinyassets.owner_door import routes  # noqa: F401
with identity_context(Identity(user_id="probe_owner", username="probe_owner",
                               capabilities=["tinyassets.universe.write"])):
    for target, extra in (("pending_requests", {}), ("model_options", {}),
                          ("agent_bindings", {"limit": 5}), ("app_ui", {}),
                          ("command_center_files", {}), ("conversation", {})):
        read_graph(target=target, **extra)
    get_status(include_conversation=True)
print(json.dumps(sorted(m for m in sys.modules if m in %s)))
"""


def test_exercising_the_owner_reads_never_loads_the_ceiling():
    """The static walk covers what is imported; this covers what is LOADED while
    the owner door's reads actually run, through every lazy import they take."""
    probe = _RUNTIME_PROBE % json.dumps(sorted(
        {"tinyassets.engine_result_bounds", "tinyassets.engine_read_views"}))
    run = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         encoding="utf-8", cwd=_ROOT, timeout=240, check=False)
    assert run.returncode == 0, run.stderr[-4000:]
    assert json.loads(run.stdout.strip().splitlines()[-1]) == []
