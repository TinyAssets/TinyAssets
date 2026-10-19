"""T4 guard for the shared layer's new volume mount roots.

Extend SHARED and ROOT_NAMES as later lanes adopt the primitive. This is a
source data-flow guard, complemented by the real-mount syscall instrumentation.
Old project-tree paths are lane a's explicit deletion inventory, not exceptions
to reads through the new volume primitive.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ("tinyassets/boxes", "tinyassets/broker", "deploy")
SHARED_FILES = ("tinyassets/owner_router.py", "tinyassets/router_credentials.py",
                "tinyassets/router_traffic.py", "tinyassets/router_account_deletion.py",
                "tinyassets/restore_recovery.py", "scripts/roll_owner_boxes.py",
                "scripts/open_router_traffic.py")
ROOT_NAMES = {"mount_root", "mountpoint", "volume_mount_root", "owner_mount_root"}
READS = {"open", "read_text", "read_bytes", "stat", "lstat", "exists", "is_file",
         "is_dir", "is_symlink", "walk", "scandir", "listdir", "iterdir", "glob",
         "rglob", "resolve"}
OPAQUE_COMMANDS = {"mount", "umount", "xfs_growfs"}


def violations(source):
    tree = ast.parse(source)
    tainted = set()
    aliases = {}
    returns = {"attach"}
    functions = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    scopes = {}

    def scope_nodes(node, scope="module"):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name
        scopes[id(node)] = scope
        for child in ast.iter_child_nodes(node):
            scope_nodes(child, scope)

    scope_nodes(tree)

    def name(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Attribute)):
            for target in node.targets:
                if isinstance(target, ast.Name) and name(node.value) in READS:
                    aliases[target.id] = name(node.value)

    def dirty(node):
        if node is None:
            return False
        if isinstance(node, (ast.Name, ast.Attribute)):
            if name(node) in ROOT_NAMES or (scopes[id(node)], name(node)) in tainted:
                return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return "/home/owner" in node.value or "/boxhostd/mounts" in node.value
        if isinstance(node, ast.Call) and name(node.func) in returns:
            return True
        return any(dirty(child) for child in ast.iter_child_nodes(node))

    # Fixed point propagates through assignments, helper args and returns.
    while True:
        before = tainted.copy(), returns.copy()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and dirty(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    items = target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]
                    tainted.update((scopes[id(n)], name(n)) for n in items)
            if isinstance(node, ast.Call) and name(node.func) in functions:
                function = functions[name(node.func)]
                parameters = function.args.args
                if parameters and parameters[0].arg in ("self", "cls"):
                    parameters = parameters[1:]
                for argument, parameter in zip(node.args, parameters):
                    if dirty(argument):
                        tainted.add((function.name, parameter.arg))
                for keyword in node.keywords:
                    if dirty(keyword.value):
                        tainted.add((function.name, keyword.arg))
        for function in functions.values():
            if any(isinstance(n, ast.Return) and dirty(n.value)
                   for n in ast.walk(function)):
                returns.add(function.name)
        if before == (tainted, returns):
            break
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        operation = name(node.func)
        inputs = list(node.args) + [k.value for k in node.keywords]
        if isinstance(node.func, ast.Attribute):
            inputs.append(node.func.value)
        if operation in READS and any(dirty(arg) for arg in inputs):
            found.append((node.lineno, operation))
        if operation in {"_run", "run", "Popen", "check_call", "check_output", "system"}:
            if any(dirty(arg) for arg in node.args):
                first = node.args[0] if node.args else None
                if isinstance(first, (ast.List, ast.Tuple)):
                    first = first.elts[0] if first.elts else None
                if not (isinstance(first, ast.Constant) and first.value in OPAQUE_COMMANDS):
                    found.append((node.lineno, "subprocess content access"))
    return sorted(set(found))


def test_shared_code_never_reads_volume_mounts():
    files = {path for directory in SHARED for path in (ROOT / directory).rglob("*.py")}
    files.update(ROOT / path for path in SHARED_FILES if (ROOT / path).is_file())
    failures = {str(path.relative_to(ROOT)): violations(path.read_text(encoding="utf-8"))
                for path in sorted(files)}
    assert not {path: found for path, found in failures.items() if found}


@pytest.mark.parametrize("body", [
    "os.walk(mount_root)", "os.scandir(mount_root / str(uid))",
    "os.listdir(mount_root)", "open(mount_root / 'private')",
    "(mount_root / 'private').stat()", "mount_root.rglob('*')",
    "p = mount_root / str(uid)\np.read_bytes()",
    "from os import stat as inspect\ninspect(mount_root)",
    "opener = os.open\nopener(mount_root / 'secret', 0)",
    "h = volumes.attach(uid)\n(h.mountpoint / 'secret').open()",
    "def read(p):\n    return p.read_text()\nread(mount_root / 'file')",
    "def root():\n    return mount_root\nroot().stat()",
    "subprocess.run(['find', str(mount_root)])",
    "subprocess.run(['cat', str(mount_root / 'secret')])",
])
def test_guard_detects_content_access(body):
    assert violations(body)


def test_guard_allows_opaque_lifecycle_only():
    assert not violations("""
image = store / str(uid)
image.stat()
image.open('rb')
subprocess.run(['umount', '--no-canonicalize', str(mount_root / str(uid))])
subprocess.run(['xfs_growfs', str(mount_root / str(uid))])
""")
