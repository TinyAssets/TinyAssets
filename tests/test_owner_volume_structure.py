"""T4 guard for the shared layer's new volume mount roots.

Extend SHARED and ROOT_NAMES as later lanes adopt the primitive. This is a
source data-flow guard, complemented by the real-mount syscall instrumentation.
Old project-tree paths are lane a's explicit deletion inventory, not exceptions
to reads through the new volume primitive.
"""

import ast
import subprocess
from pathlib import Path

import pytest

from tinyassets.boxes.volumes import OwnerVolumes

ROOT = Path(__file__).resolve().parents[1]
SHARED = ("tinyassets/boxes", "tinyassets/broker", "deploy")
SHARED_FILES = ("tinyassets/owner_router.py", "tinyassets/router_credentials.py",
                "tinyassets/router_traffic.py", "tinyassets/router_account_deletion.py",
                "tinyassets/restore_recovery.py", "tinyassets/cutover_control.py",
                "scripts/roll_owner_boxes.py",
                "scripts/open_router_traffic.py")
ROOT_NAMES = {"mount_root", "mountpoint", "volume_mount_root", "owner_mount_root", "volume_mount"}
LIFECYCLE = {"owner_daemons.py", "gvisor_runtime.py", "host_service.py", "admission.py",
             "quota.py", "destroy.py", "volume_lifecycle.py"}
READS = {"open", "read_text", "read_bytes", "stat", "lstat", "exists", "is_file",
         "is_dir", "is_symlink", "walk", "scandir", "listdir", "iterdir", "glob",
         "rglob", "resolve"}
OPAQUE_COMMANDS = {"mount", "umount", "xfs_growfs"}


def violations(source, *, roots=ROOT_NAMES):
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
            if name(node) in roots or (scopes[id(node)], name(node)) in tainted:
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
    failures = {str(path.relative_to(ROOT)): violations(
        path.read_text(encoding="utf-8"), roots=ROOT_NAMES | (
            {"owner_store", "_owner_tree"} if path.name in LIFECYCLE else set()))
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
    "source = volume_mount['source']\nPath(source).stat()",
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


@pytest.mark.parametrize("root", ["owner_store", "_owner_tree"])
def test_lifecycle_guard_rejects_the_retired_shared_tree_roots(root):
    assert violations(f"(self.{root} / str(uid)).stat()",
                      roots=ROOT_NAMES | {"owner_store", "_owner_tree"})


@pytest.mark.verifies(
    'owner-box-snapshot-undo:Undo points use automatic sleep and explicit owner checkpoints')
def test_one_lifecycle_and_no_shared_turn_pin():
    """N6 adds callers to the one executor. Shared code has no turn pin."""
    lifecycle = (ROOT / "tinyassets/broker/volume_lifecycle.py").read_text(encoding="utf-8")
    volumes = (ROOT / "tinyassets/boxes/volumes.py").read_text(encoding="utf-8")
    broker = (ROOT / "tinyassets/broker/owner_volumes.py").read_text(encoding="utf-8")
    tree = ast.parse(lifecycle)
    classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    assert classes.count("VolumeLifecycle") == 1
    executor = ast.parse(volumes)
    snapshots = [node.name for node in ast.walk(executor)
                 if isinstance(node, ast.FunctionDef) and node.name == "snapshot"]
    assert snapshots == ["snapshot"]
    forbidden = ("turn_pin", "per_turn", "per_session", "session_checkpoint",
                 "turn_checkpoint")
    for name in forbidden:
        assert name not in lifecycle and name not in volumes and name not in broker


def test_detach_retries_until_a_lingering_loop_device_clears(tmp_path, monkeypatch):
    monkeypatch.setattr("tinyassets.boxes.volumes.time.sleep", lambda _seconds: None)
    volumes = OwnerVolumes.__new__(OwnerVolumes)
    volumes.mount_root = tmp_path / "mounts"
    volumes.mount_root.mkdir()
    calls = []
    probes = iter([["/dev/loop7"], ["/dev/loop7"], [], []])

    def loops(uid):
        assert uid == 300001
        return next(probes)

    def run(*args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    volumes._loops = loops
    volumes._run = run
    volumes._mounts = lambda: []
    volumes._detach(300001)
    assert calls == [
        ("losetup", "--detach", "/dev/loop7"),
        ("losetup", "--detach", "/dev/loop7"),
    ]

    calls.clear()
    probes = iter([["/dev/loop7"], ["/dev/loop7"], [], []])

    def loops_after_kernel_detach(uid):
        assert uid == 300001
        return next(probes)

    def run_after_kernel_detach(*args):
        calls.append(args)
        if len(calls) > 1:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, "", "")

    volumes._loops = loops_after_kernel_detach
    volumes._run = run_after_kernel_detach
    volumes._detach(300001)
    assert calls == [
        ("losetup", "--detach", "/dev/loop7"),
        ("losetup", "--detach", "/dev/loop7"),
    ]


pytestmark = pytest.mark.verifies('owner-daemon-in-box:T4')
