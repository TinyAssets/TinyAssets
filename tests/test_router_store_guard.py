"""Every router-state opener refuses before touching an owner's private tree."""

import ast
import os
from contextlib import ExitStack, closing
from pathlib import Path

import pytest

from tinyassets import owner_runtime
from tinyassets.api_keys import ApiKeyStore
from tinyassets.auth.provider import OAuthProvider
from tinyassets.broker.owner_reads import Plans
from tinyassets.connection_oauth.pkce import flows_db
from tinyassets.erasure_ledger import ErasureLedger
from tinyassets.onboarding import (
    approval_handoff,
    canary_sign_in,
    inline_model_connect,
    native_sign_in,
    owner_sessions,
    session_store,
)
from tinyassets.router_credentials import HookOwners


def oauth_connection(root):
    provider = object.__new__(OAuthProvider)
    provider._db_path = root / ".auth.db"
    return closing(provider._connect())


OPENERS = [
    ("plans", Plans),
    ("owner_sessions", lambda root: owner_sessions.store()),
    ("owner_sessions", lambda root: inline_model_connect.flows()),
    ("owner_sessions", lambda root: approval_handoff.flows()),
    ("refresh_sessions", lambda root: session_store.store_dir()),
    ("native_sign_in", lambda root: native_sign_in.flows()),
    ("canary_sign_in", lambda root: canary_sign_in._limits()),
    ("oauth_pkce", flows_db),
    ("api_keys", lambda root: ApiKeyStore(root).db()),
    ("hook_owners", HookOwners),
    ("erasure_ledger", ErasureLedger),
    ("mcp_oauth", lambda root: OAuthProvider(root / ".auth.db")),
    ("mcp_oauth", oauth_connection),
]


@pytest.mark.parametrize("name,opener", OPENERS, ids=[
    "plans", "owner_sessions", "inline_flows", "handoffs", "refresh_sessions", "native_sign_in",
    "canary_sign_in", "oauth_pkce", "api_keys", "hook_owners", "erasure_ledger", "mcp_oauth",
    "mcp_oauth_connect",
])
@pytest.mark.parametrize("inside", [True, False], ids=["box", "router"])
def test_router_store_openers(tmp_path, monkeypatch, name, opener, inside):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    runtime = owner_runtime.OwnerRuntime("alice", 1001, tmp_path, os.getpid(), None)
    monkeypatch.setattr(owner_runtime, "_runtime", runtime if inside else None)

    def open_store():
        with ExitStack() as stack:
            opened = opener(tmp_path)
            if not isinstance(opened, Path) and hasattr(opened, "__enter__"):
                stack.enter_context(opened)

    if inside:
        message = f"router store {name} is never opened inside an owner box"
        with pytest.raises(RuntimeError, match=message):
            open_store()
        assert not list(tmp_path.iterdir())
    else:
        open_store()


def test_policy_session_reader_refuses_in_box(tmp_path, monkeypatch):
    from tinyassets.broker.policy import PolicyStore

    monkeypatch.setattr(owner_runtime, "_runtime", owner_runtime.OwnerRuntime(
        "alice", 1001, tmp_path, os.getpid(), None))
    with pytest.raises(RuntimeError, match="router store owner_sessions"):
        PolicyStore(tmp_path, None)._session("alice", {"session_hash": "a" * 64})


# The router can resolve identity, route lifecycle calls and clean up shared
# credentials. These are shared control-plane stores, never owner data stores.
_ROUTER_IMPORTS = {
    "tinyassets.auth.middleware",
    "tinyassets.auth.provider",
    "tinyassets.billing.stripe_adapter",  # Shared subscription cancellation.
    "tinyassets.broker.account_erasure",  # Shared credential custody erasure.
    "tinyassets.boxes.daemon_control",
    "tinyassets.broker.owner_identities",
    "tinyassets.onboarding.owner_sessions",
    "tinyassets.router_credentials",
    "tinyassets.erasure_ledger",
    "tinyassets.router_traffic",  # Router-owned shared admission marker.
    "tinyassets.router_account_deletion",  # Shared identity erasure.
    "tinyassets.storage",  # data_dir is passed only to the shared store APIs.
}


def _router_owner_io(source):
    violations = []
    tree = ast.parse(source)
    dataclass_replace = any(
        isinstance(node, ast.ImportFrom) and node.module == "dataclasses"
        and any(alias.name == "replace" and alias.asname is None for alias in node.names)
        for node in ast.walk(tree)
    )
    for node in ast.walk(tree):
        imports = ([node.module] if isinstance(node, ast.ImportFrom)
                   else [item.name for item in node.names] if isinstance(node, ast.Import) else [])
        for module in imports:
            if module and module.startswith("tinyassets") and module not in _ROUTER_IMPORTS:
                violations.append((node.lineno, "owner import", module))
        if isinstance(node, ast.Call):
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else node.func.id if isinstance(node.func, ast.Name) else "")
            # dataclasses.replace changes identity metadata, never the filesystem.
            if dataclass_replace and isinstance(node.func, ast.Name) and node.func.id == "replace":
                continue
            if name in {"open", "connect", "read_text", "read_bytes", "write_text", "write_bytes",
                        "mkdir", "iterdir", "glob", "rglob", "scandir", "listdir",
                        "unlink", "rmdir", "rename", "replace", "stat", "lstat"}:
                violations.append((node.lineno, "direct filesystem access", name))
    return violations


def test_router_imports_only_shared_control_plane_and_never_opens_files():
    source = (Path(__file__).resolve().parents[1] / "tinyassets" / "owner_router.py").read_text(
        encoding="utf-8",
    )
    assert _router_owner_io(source) == []


@pytest.mark.parametrize("source", [
    "from tinyassets.daemon_server import _connect",
    "from tinyassets.account_deletion import delete_account",
    "import tinyassets.universe_files as files",
    "data_dir().joinpath('owner', 'soul.md').read_text()",
    "open(data_dir() / 'owner' / 'soul.md')",
    "sqlite3.connect(data_dir() / 'owner' / 'history.db')",
])
def test_router_guard_detects_owner_store_imports_and_file_access(source):
    assert _router_owner_io(source)


# D7: the reviewed process boundaries, by module and qualified function.
# Owner-runtime's own lifecycle is intrinsic to that module, not a second path.
_ROLE_DISPATCH = {
    ('operation_epoch.py', 'new_identity'): (
        'Box operations bind to their durable journal epoch; router identities have no box journal.'
    ),
    ('operation_epoch.py', 'check'): (
        'Box replay uses the platform restore generation; router IDs have no owner receipts.'
    ),
    ('api_keys.py', 'current_store'): (
        'Router opens its key store; the box uses credential RPC.'
    ),
    ('broker/client.py', 'BrokerClient._owner_mode'): (
        'Shared management methods choose router or bound box transport.'
    ),
    ('broker/client.py', 'BrokerClient._connect'): (
        'Router management uses the host socket; boxes use the relay.'
    ),
    ('broker/client.py', 'BrokerClient.ledger_query'): (
        'Router connection completion and box grant reads share this client.'
    ),
    ('broker/client.py', 'BrokerClient.connection_catalog'): (
        'Router connections page and box catalog both query the broker.'
    ),
    ('broker/client.py', 'BrokerClient.disconnect'): (
        'Both the router door and box can revoke a connection.'
    ),
    ('broker/client.py', 'BrokerClient.http_connect'): (
        'Router deposits secrets; boxes connect without secrets.'
    ),
    ('broker/owner_identities.py', 'admitted_owner'): (
        'Router selects an admitted center; boxes use bound identity RPC.'
    ),
    ('broker/supervisor.py', 'get_supervisor'): (
        'Router owns the process supervisor; boxes receive a channel adapter.'
    ),
    ('commons_catalog.py', 'publish_definition'): (
        'Box publishes over RPC; broker writes its published store.'
    ),
    ('commons_catalog.py', 'get_definition'): (
        'Router reads published data; box reads it over the bound channel.'
    ),
    ('credential_vault.py', '_credential_root'): (
        'Broker owns raw custody; direct box access must refuse.'
    ),
    ('onboarding/owner_sessions.py', 'require'): (
        'Router resolves cookies; boxes validate forwarded protected proof.'
    ),
    ('platform_runtime_provenance.py', '_owner_observation'): (
        'Box provenance comes from relay admission; router observes its process.'
    ),
    ('universe_server.py', 'create_streamable_http_app'): (
        'One factory places routes, lifespans and authentication by process role.'
    ),
    ('api_keys.py', 'ApiKeyStore.db'): (
        'Router opens its key store; direct box access must refuse.'
    ),
    ('auth/provider.py', 'OAuthProvider.__init__'): (
        'Router constructs OAuth storage; direct box access must refuse.'
    ),
    ('auth/provider.py', 'OAuthProvider._connect'): (
        'Router opens OAuth storage; direct box access must refuse.'
    ),
    ('billing/plan_store.py', 'BillingPlans.__init__'): (
        'Router billing uses broker plans; a box cannot construct this writer.'
    ),
    ('broker/owner_reads.py', 'Plans.__init__'): (
        'Broker opens plans; boxes must use the bound channel.'
    ),
    ('broker/owner_reads.py', 'publish'): (
        'Broker writes published data; boxes must use the bound channel.'
    ),
    ('broker/policy.py', 'PolicyStore._session'): (
        'Broker verifies router session evidence; box cannot open that store.'
    ),
    ('connection_oauth/pkce.py', 'flows_db'): (
        'Router owns OAuth flows; direct box access must refuse.'
    ),
    ('onboarding/canary_sign_in.py', '_limits'): (
        'Router owns sign-in limits; direct box access must refuse.'
    ),
    ('onboarding/native_sign_in.py', 'flows'): (
        'Router owns native sign-in flows; direct box access must refuse.'
    ),
    ('onboarding/owner_sessions.py', 'store'): (
        'Router owns sessions; box uses forwarded proof.'
    ),
    ('onboarding/session_store.py', 'store_dir'): (
        'Router owns refresh sessions; direct box access must refuse.'
    ),
    ('erasure_ledger.py', 'ErasureLedger.__init__'): (
        'Router owns deletion tombstones; direct box access must refuse.'
    ),
    ('router_credentials.py', 'HookOwners.__init__'): (
        'Router owns hook digests; boxes reach it through credential RPC.'
    ),
}


def _mode_sites(path, root=None):
    """Find runtime mode predicates, including imported aliases and captured modes."""
    source = path.read_text(encoding="utf-8")
    if "owner_runtime" not in source and "_owner_mode" not in source:
        return set()
    tree = ast.parse(source)
    nodes = list(ast.walk(tree))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    def scope(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return node
        return tree

    symbols = {}
    relative = path.relative_to(root).with_suffix("") if root else Path(path.stem)
    module = "tinyassets." + ".".join(relative.parts)
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                symbols[scope(node), alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            home = node.module or ""
            if node.level:
                home = module.rsplit(".", node.level)[0] + "." + home
            for alias in node.names:
                symbols[scope(node), alias.asname or alias.name] = home + "." + alias.name

    def name(node):
        if isinstance(node, ast.Name):
            current = scope(node)
            while (current, node.id) not in symbols:
                if current is tree:
                    return node.id
                current = scope(current)
            return symbols[current, node.id]
        if isinstance(node, ast.Attribute):
            return name(node.value) + "." + node.attr
        return ""

    query = "tinyassets.owner_runtime.current"
    refusal = "tinyassets.owner_runtime.refuse_router_store"
    # Modes can be captured by a local/nested function (the app factory does so).
    # Track assignments in lexical scopes; unrelated functions must not taint one another.
    tainted = set()

    def bound(node):
        current = scope(node)
        while True:
            if (current, node.id) in tainted:
                return True
            if current is tree:
                return False
            current = scope(current)

    def mode(node):
        if isinstance(node, ast.Name):
            return bound(node)
        if isinstance(node, ast.Call):
            return (name(node.func) == query
                    or name(node.func).endswith("._owner_mode")
                    or name(node.func) == "bool" and any(mode(a) for a in node.args))
        # Reading runtime.principal/root/tools is not testing whether a runtime exists.
        if isinstance(node, ast.Attribute):
            return False
        return any(mode(child) for child in ast.iter_child_nodes(node))

    # Resolve aliases to the query and predicates before inspecting conditionals.
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                continue
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name) or value is None:
                    continue
                if ((name(value) in {query, refusal, "tinyassets.owner_runtime"}
                     or name(value).endswith("._owner_mode"))
                        and symbols.get((scope(target), target.id)) != name(value)):
                    symbols[scope(target), target.id] = name(value)
                    changed = True
                key = (scope(target), target.id)
                if mode(value) and key not in tainted:
                    tainted.add(key)
                    changed = True

    def function(node):
        names = []
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
        # A nested function belongs to its enclosing reviewed function.
        names.reverse()
        return ".".join(names[:2] if names and isinstance(
            next((n for n in tree.body if getattr(n, "name", None) == names[0]), None),
            ast.ClassDef) else names[:1])

    sites = set()
    for node in nodes:
        predicate = (
            isinstance(node, (ast.If, ast.IfExp, ast.While, ast.Assert)) and mode(node.test)
            or isinstance(node, (ast.Compare, ast.BoolOp, ast.UnaryOp)) and mode(node)
            or isinstance(node, ast.Call) and (
                name(node.func) == refusal or name(node.func).endswith("._owner_mode")
                or name(node.func) == "bool" and mode(node))
            or isinstance(node, ast.Return) and node.value is not None
            and isinstance(node.value, ast.Name) and mode(node.value)
        )
        if predicate:
            sites.add((function(node), node.lineno))
    return sites


def _unreviewed_modes(root):
    return [
        (path.relative_to(root).as_posix(), function, line)
        for path in sorted(root.rglob("*.py"))
        if path.relative_to(root).as_posix() != "owner_runtime.py"
        for function, line in sorted(_mode_sites(path, root))
        if (path.relative_to(root).as_posix(), function) not in _ROLE_DISPATCH
    ]


def test_owner_runtime_mode_forks_are_only_reviewed_role_dispatch():
    root = Path(__file__).resolve().parents[1] / "tinyassets"
    assert _unreviewed_modes(root) == []
    observed = {(path.relative_to(root).as_posix(), function)
                for path in root.rglob("*.py") if path.name != "owner_runtime.py"
                for function, _line in _mode_sites(path, root)}
    assert set(_ROLE_DISPATCH) == observed, "remove stale role-dispatch exceptions"
    assert all(_ROLE_DISPATCH.values())


@pytest.mark.parametrize("fork", [
    "from tinyassets.owner_runtime import current\n    if current() is None: return 'shared'",
    "from tinyassets.owner_runtime import current as live\n    if live(): return 'box'",
    "import tinyassets.owner_runtime as rt\n    runtime = rt.current()\n"
    "    if runtime is not None: return 'box'",
    "from tinyassets import owner_runtime as rt\n    mode = rt.current() is None\n"
    "    return 'shared' if mode else 'box'",
    "from tinyassets.owner_runtime import current\n    live = current\n"
    "    mode = bool(live())\n    if not mode: return 'shared'",
    "from .owner_runtime import current as live\n    if live(): return 'box'",
    "import tinyassets.owner_runtime as rt\n    alias = rt\n"
    "    return bool(alias.current())",
    "check = self._owner_mode\n    return check()",
    "if self._owner_mode(): return 'box'",
    "from tinyassets.owner_runtime import refuse_router_store as guard\n    guard('keys')",
])
def test_d7_catches_a_new_fork_in_a_module_copy(tmp_path, fork):
    source = Path(__file__).resolve().parents[1] / "tinyassets" / "universe_owner.py"
    copied = tmp_path / source.name
    copied.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    assert _unreviewed_modes(tmp_path) == []
    with copied.open("a", encoding="utf-8") as output:
        output.write("\n\ndef fake_fork(self):\n    " + fork + "\n")
    findings = _unreviewed_modes(tmp_path)
    assert findings
    assert {item[:2] for item in findings} == {("universe_owner.py", "fake_fork")}


def test_d7_allows_branchless_reads_and_other_current_functions(tmp_path):
    copied = tmp_path / "example.py"
    copied.write_text(
        "from tinyassets import owner_runtime as rt, turn_interrupt\n"
        "def owner():\n    runtime = rt.current()\n    return runtime.principal\n"
        "def other():\n    if turn_interrupt.current() is None: return None\n"
        "def unrelated():\n    from tinyassets.turn_interrupt import current\n"
        "    if current() is None: return None\n",
        encoding="utf-8",
    )
    assert _unreviewed_modes(tmp_path) == []


# D7: retired backend modules may neither return nor gain an import site.
_RETIRED_BACKEND = frozenset({
    "tinyassets.boxes.frontend",
    "tinyassets.boxes.production_files",
    "tinyassets.boxes.file_worker",
    "tinyassets.boxes.exec_worker",
    "tinyassets.boxes.executions",
    "tinyassets.boxes.gvisor_files",
    "tinyassets.boxes.owner_files",
    "tinyassets.boxes.handles",
    "tinyassets.boxes.file_channel",
    "tinyassets.boxes.file_wire",
    "tinyassets.boxes.git_bridge",
    "tinyassets.agent_loop.box_tools",
    "tinyassets.box_process",
    "tinyassets.agent_loop.box_ta_worker",
})


def _retired_backend_imports(root):
    findings = []
    for path in sorted(root.rglob("*.py")):
        if any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        dynamic = {alias.asname or alias.name for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom) and node.module == "importlib"
                   for alias in node.names if alias.name == "import_module"}
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                home = node.module or ""
                if node.level:
                    parts = path.relative_to(root).with_suffix("").parts
                    if root.name in {"tinyassets", "deploy", "scripts"}:
                        parts = (root.name, *parts)
                    package = ".".join(parts[:-node.level])
                    home = package + ("." + home if home else "")
                names = [home, *(home + "." + alias.name for alias in node.names)]
            elif isinstance(node, ast.Call) and node.args:
                function = ast.unparse(node.func)
                if (function.endswith("import_module") or function == "__import__"
                        or function in dynamic):
                    if isinstance(node.args[0], ast.Constant):
                        imported = node.args[0].value
                        if isinstance(imported, str) and imported.startswith("."):
                            from importlib.util import resolve_name
                            package = (node.args[1] if len(node.args) > 1 else
                                       next((kw.value for kw in node.keywords
                                             if kw.arg == "package"), None))
                            if isinstance(package, ast.Constant) and isinstance(package.value, str):
                                imported = resolve_name(imported, package.value)
                        names = [imported]
            for name in names:
                if isinstance(name, str) and any(
                        name == retired or name.startswith(retired + ".")
                        for retired in _RETIRED_BACKEND):
                    findings.append((path.relative_to(root).as_posix(), node.lineno, name))
                    break
    return findings


def test_retired_per_call_backend_cannot_be_imported():
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    for name in _RETIRED_BACKEND:
        assert importlib.util.find_spec(name) is None, name
    for directory in ("tinyassets", "deploy", "scripts"):
        assert _retired_backend_imports(root / directory) == []


@pytest.mark.parametrize("statement", [
    "import tinyassets.boxes.frontend as old",
    "from tinyassets.boxes import production_files as old",
    "from tinyassets.boxes.exec_worker import main",
    "import importlib; importlib.import_module('tinyassets.boxes.gvisor_files')",
    "__import__('tinyassets.boxes.executions')",
    "from importlib import import_module as fetch; fetch('tinyassets.boxes.frontend')",
    "import importlib; importlib.import_module('.frontend', 'tinyassets.boxes')",
])
def test_d7_rejects_retired_backend_imports_in_a_module_copy(tmp_path, statement):
    (tmp_path / "caller.py").write_text(statement, encoding="utf-8")
    assert len(_retired_backend_imports(tmp_path)) == 1


@pytest.mark.parametrize("statement", [
    "from . import frontend as old",
    "from .production_files import ProductionFileProvider",
    "from ..agent_loop import box_tools",
])
def test_d7_rejects_relative_backend_imports(tmp_path, statement):
    path = tmp_path / "tinyassets/boxes/caller.py"
    path.parent.mkdir(parents=True)
    path.write_text(statement, encoding="utf-8")
    assert len(_retired_backend_imports(tmp_path)) == 1
