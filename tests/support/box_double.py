"""The in-process owner box every non-oracle test reaches.

Production has no unconfined path: every owner-scoped file read, write and
execution goes through the session's admitted box handle, and the provider
behind it is installed once by the owner-box frontend (gVisor through
``boxhostd``). A test process has no host service and no owner uids, so
without a double every owner operation would refuse.

The double is the local box core (``tinyassets.boxes.boxd.BoxdCore``) over
the test's data root: the command center directory IS the box, commands run
unisolated in the test process's uid, and the handle, epoch, journal and file
API are the real ones. Admission is stood in for at one seam: a center with
no recorded owner admits whichever account asks, as the deleted cell doubles
did for a test that wrote into a center it never created; a center the
daemon tables attribute admits only that account.

Opt out with ``pytestmark = pytest.mark.role_split`` in a module that drives
the real host service or provider (the root oracle runs those).

Linux-only, like the local box core itself.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import threading
import weakref
from pathlib import Path

import pytest

SUPPORTED = sys.platform != "win32" and os.path.isdir("/proc/self/fd")


class _PermissiveBoxd:
    """One local box core per data root; an unowned center admits any account."""

    def __init__(self):
        self._cores = {}
        self._handles = weakref.WeakValueDictionary()
        self._owner_core = {}
        self._guard = threading.Lock()
        self._state = tempfile.mkdtemp(prefix="ta-box-double-")

    def _core(self, root: Path):
        from tinyassets.boxes.boxd import BoxdCore
        from tinyassets.universe_owner import owner_of

        key = str(root)

        def recorded_owner(cc):
            try:
                return owner_of(root, cc)
            except Exception:  # noqa: BLE001 - no daemon tables beside an ad hoc root
                return None

        with self._guard:
            core = self._cores.get(key)
            if core is None:
                digest = hashlib.sha256(key.encode()).hexdigest()[:16]
                core = BoxdCore(boxes_root=root, state_dir=Path(self._state) / digest,
                                owner_of=recorded_owner, allow_unisolated=True)

                def require_owner(cc, account_id, _core=core):
                    owner = _core._owner_of(cc)
                    if owner is not None and owner != account_id:
                        from tinyassets.boxes.provider import BoxAuthError

                        raise BoxAuthError(
                            f"account {account_id!r} does not own command center {cc!r}")

                core._require_owner = require_owner
                self._cores[key] = core
        return core

    def admitted_owner(self, parent, command_center_id):
        """The account the double admits on this center: its recorded owner."""
        return self._core(Path(parent))._owner_of(command_center_id) or "test-owner"

    def _root(self) -> Path:
        from tinyassets.storage import data_dir

        return Path(os.path.abspath(data_dir()))

    def bind(self, command_center_id, *, account_id, turn_id=None):
        # Production binds on the one data root. A test that admitted a center
        # under another root (an explicit ``delete_account(root, ...)``) means
        # that root; the configured data directory is the default.
        with self._guard:
            opened = list(self._cores.values())
        for core in opened:
            if (core._root / command_center_id).is_dir():
                return self.bind_under(core._root, command_center_id,
                                       account_id=account_id, turn_id=turn_id)
        return self.bind_under(self._root(), command_center_id, account_id=account_id,
                               turn_id=turn_id)

    def bind_under(self, parent, command_center_id, *, account_id, turn_id=None):
        core = self._core(Path(parent))
        handle = core.bind(command_center_id, account_id=account_id, turn_id=turn_id)
        with self._guard:
            self._handles[id(handle)] = handle
            self._owner_core[id(handle)] = core
        return handle

    bind_for_destroy = bind

    def end_session(self, handle):
        """An explicit terminal stop; the local core keeps no session state."""
        del handle

    def _for(self, handle):
        core = self._owner_core.get(id(handle))
        if core is None:
            raise LookupError("handle was not bound through the box double")
        return core

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(handle, *args, **kwargs):
            original = args[0] if name == "read_many" and args else kwargs.get("paths")
            args, kwargs = _project(name, list(args), dict(kwargs))
            if name == "start_exec":
                # Commands run unisolated in the test's uid, so a box path in
                # argv must name the center's host directory, as the box's own
                # mount would.
                args, kwargs = _host_argv(self._for(handle), handle, args, kwargs)
            result = getattr(self._for(handle), name)(handle, *args, **kwargs)
            if name == "read_many" and original is not None:
                # The production channel hands back the caller's own keys.
                projected = args[0] if args else kwargs["paths"]
                back = dict(zip(projected, original))
                from tinyassets.boxes.provider import Snapshot

                return Snapshot({back.get(path, path): value
                                 for path, value in result.files.items()},
                                tuple(back.get(path, path) for path in result.missing),
                                result.generation)
            return result

        return call

    def close(self):
        with self._guard:
            cores, self._cores = list(self._cores.values()), {}
        for core in cores:
            core.close()


def _box_path(value):
    """The production channel projects a center-relative path under /cc."""
    from tinyassets.boxes.provider import BOX_ROOT, box_relpath

    if not isinstance(value, str):
        return value
    if value == BOX_ROOT or value.startswith(BOX_ROOT + "/"):
        return value
    relative = value.strip("/")
    if relative in ("", "."):
        return BOX_ROOT
    box_relpath(BOX_ROOT + "/" + relative)
    return BOX_ROOT + "/" + relative


_PATH_ARG = {"read": 0, "list": 0, "stat": 0, "remove": 1, "download": 0, "write": 1}


def _project(name, args, kwargs):
    if name == "read_many":
        if args:
            args[0] = [_box_path(path) for path in args[0]]
        elif "paths" in kwargs:
            kwargs["paths"] = [_box_path(path) for path in kwargs["paths"]]
        return args, kwargs
    index = _PATH_ARG.get(name)
    if index is None:
        return args, kwargs
    if len(args) > index:
        args[index] = _box_path(args[index])
    elif "path" in kwargs:
        kwargs["path"] = _box_path(kwargs["path"])
    return args, kwargs


def _host_argv(core, handle, args, kwargs):
    from tinyassets.boxes.provider import BOX_ROOT

    host = str(core._root / handle.command_center_id)

    def mapped(value):
        if isinstance(value, str) and (value == BOX_ROOT or value.startswith(BOX_ROOT + "/")):
            return host + value[len(BOX_ROOT):]
        return value

    def mapped_argv(argv):
        argv = [mapped(value) for value in argv]
        # The box image's `python` has tinyassets installed; in a test that
        # interpreter is the one running the suite, not whatever PATH finds.
        if argv and argv[0] in ("python", "python3"):
            argv[0] = sys.executable
        return argv

    if len(args) > 1:
        args[1] = mapped_argv(args[1])
    elif "argv" in kwargs:
        kwargs["argv"] = mapped_argv(kwargs["argv"])
    # The box image names its browsers (gvisor_runtime) and installs this
    # package; here, the test host's browsers and the source tree under test.
    import tinyassets

    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(
        Path.home() / ".cache" / "ms-playwright")
    kwargs["env"] = {"PLAYWRIGHT_BROWSERS_PATH": browsers,
                     "PYTHONPATH": str(Path(tinyassets.__file__).resolve().parent.parent),
                     **(kwargs.get("env") or {})}
    return args, kwargs


def install(monkeypatch):
    """Install the owner box double for one test (also usable in spawned children)."""
    from tinyassets.agent_loop import served_chat
    from tinyassets.boxes.provider import ExecLimits
    from tinyassets.control_plane import binding as binding_module

    double = _PermissiveBoxd()
    monkeypatch.setattr(served_chat, "_box_provider", double)
    monkeypatch.setattr(served_chat, "_box_limits", ExecLimits())
    real_file_binding = binding_module.file_binding
    bindings = {}

    def file_binding(root):
        """A directory a test treats as a command center binds itself.

        The deleted cell doubles admitted any center a test wrote into; the box
        double does the same at the one seam production never has.
        """
        root = Path(os.path.abspath(root))
        current = binding_module.current_binding()
        if (current is not None or root.name.startswith(".")
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", root.name)):
            return real_file_binding(root)
        if not root.is_dir():
            # The admission a production center gets from the broker: here the
            # directory simply exists, as the deleted admission double did.
            if root.exists() or not root.parent.is_dir():
                return real_file_binding(root)
            root.mkdir()
        owner = double.admitted_owner(root.parent, root.name)
        # One binding per directory and owner for the test: its operation
        # sequence keeps advancing, as a session's does, so the box journal
        # never sees a replayed operation id with different content.
        binding = bindings.get((str(root), owner))
        if binding is None:
            handle = double.bind_under(root.parent, root.name, account_id=owner,
                                       turn_id=hashlib.sha256(
                                           f"test-double:{owner}:{root}".encode()).hexdigest())
            binding = binding_module.SessionBinding(double, handle, ExecLimits(), root)
            bindings[(str(root), owner)] = binding
        binding.validate(owner, root)
        return binding

    monkeypatch.setattr(binding_module, "file_binding", file_binding)
    from tinyassets.boxes import lifecycle

    def admit_center(data_root, *, principal, center):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", center):
            raise RuntimeError("invalid command center name")
        (Path(data_root) / center).mkdir(exist_ok=True)
        return 1

    monkeypatch.setattr(lifecycle, "admit_center", admit_center)
    return double


@pytest.fixture(autouse=True)
def _owner_box_double(request, monkeypatch):
    if request.node.get_closest_marker("role_split") is not None or not SUPPORTED:
        yield None
        return
    double = install(monkeypatch)
    try:
        yield double
    finally:
        double.close()
