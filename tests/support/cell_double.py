"""The bounded owner launcher double every non-oracle test reaches.

Production has no unconfined fallback: every owner-scoped child runs in that
owner's cell through ``role_decoder._bounded_client``, which only the PID1
bootstrap installs. A test process has no launcher, no mapper and no owner
uids, so without a double every call through a cell would refuse.

The double is installed at the NARROWEST seam -- the per-class ``role_*``
entry point -- and each replacement runs the same inner work the cell would
run, unconfined, in the test process. So the code under test is the real
module (the nested jail, the real git spawn, the real Pillow decode), and only
the owner hand-off is stood in for.

Opt out with ``pytestmark = pytest.mark.role_split`` in a module that drives
the real launcher, mapper or cells (the root oracle runs those).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


class _InstalledLauncher:
    """Stands in for ``OwnerLauncherClient`` where only its presence is read.

    Every method a cell would reach is replaced above this object, so reaching
    one of them means a seam was missed rather than exercised: say so loudly.
    """

    def __getattr__(self, name):
        raise AssertionError(
            f"the owner launcher double has no {name!r}: a cell seam is "
            "unpatched, so this test would have needed a real launcher"
        )

    def __repr__(self):
        return "<cell-double owner launcher>"


def _decode(data, mime, universe_dir):
    """The image decoder cell: one Pillow decode, framed as the cell frames it."""
    from tinyassets import tool_images

    try:
        meta, encoded = tool_images._shown(data, mime)
    except MemoryError:
        return SimpleNamespace(returncode=3, stdout=json.dumps(
            {"error": "needs more memory than the decoder may use"}).encode() + b"\n")
    except Exception as exc:  # noqa: BLE001 - any decoder failure is a refusal line
        return SimpleNamespace(returncode=3, stdout=json.dumps(
            {"error": f"could not be decoded as {mime}: {str(exc)[:200]}"}).encode() + b"\n")
    return SimpleNamespace(
        returncode=0, stdout=json.dumps(meta).encode() + b"\n" + encoded, cell={})


def _node(instance, *, invoke, workspace, **request):
    """The node cell: the real nested sandbox, run here."""
    return instance.run_nested(**request, workspace=workspace, invoke=invoke)


def _git(argv, *, cwd, options, timeout_s):
    """The git cell: the real git spawn, with the cell's own HOME and cwd.

    ``git_bridge`` rewrites absolute pathspecs to ``/workspace/...`` because
    the cell's cwd IS the bound workspace. Here the cwd is the real directory,
    so the same names are turned back into cwd-relative ones.

    A request the cell cannot carry out surfaces as ``OwnerLaunchRefused``,
    which is what ``role_git.run`` raises when a cell does not complete -- not
    as the cell's own internal error class.
    """
    from tinyassets.owner_launcher_client import OwnerLaunchRefused
    from tinyassets.workspace_git import WorkspaceGitError, run_git_in_cell

    arguments = list(argv)
    if "--" in arguments:
        for index in range(arguments.index("--") + 1, len(arguments)):
            if arguments[index].startswith("/workspace/"):
                arguments[index] = arguments[index][len("/workspace/"):]
    try:
        with tempfile.TemporaryDirectory(prefix="ta-cell-git-home-") as home:
            result = run_git_in_cell(
                arguments, cwd=cwd, home_dir=home,
                path=os.environ.get("PATH", "/usr/bin:/bin"), git_binary="git",
                options=tuple(options), timeout_s=float(timeout_s),
            )
    except WorkspaceGitError as refused:
        raise OwnerLaunchRefused(f"git cell did not complete: {refused}") from None
    return SimpleNamespace(returncode=result.returncode, stdout=result.stdout_tail,
                          stderr=result.stderr_scrubbed, cell={})


#: Local bare repositories that stand in for a remote in the workspace-remote
#: cell double, keyed by ``owner/name``. A test that wants a real clone or push
#: registers one; the double then rewrites the route URL (which points at
#: ``ta-git.invalid`` through a proxy no test process runs) to that path, and
#: leaves every other option exactly as the daemon built it.
REMOTE_REPOS: dict[str, str] = {}


def _local_options(options, repo):
    """The cell's git options with the unreachable route pointed at a local repo."""
    local = REMOTE_REPOS.get(repo)
    if local is None:
        return list(options)
    kept = []
    index = 0
    while index < len(options):
        if options[index] != "-c":
            kept.append(options[index])
            index += 1
            continue
        setting = options[index + 1]
        index += 2
        if setting.startswith("http.proxy="):
            continue  # no in-cell forwarder here: the remote is a path
        if setting.startswith("url.http://ta-git.invalid/") and ".insteadOf=" in setting:
            source = setting.split(".insteadOf=", 1)[1]
            kept.extend(("-c", f"url.{local}.insteadOf={source}"))
            continue
        kept.extend(("-c", setting))
    return kept


def _remote_git(request, *, universe_dir, principal, egress_socket):
    """The workspace-remote cell: the real operation, run here.

    ``role_remote_git.run``'s daemon-side work is the part a test process
    cannot have (an owner uid, a mapper, a labelled pool directory), so the
    pool parent is created plainly and the cell's own
    ``workspace_remote_cell.perform`` runs unconfined in this process -- the
    same git, the same lease layout, the same answer.
    """
    from tinyassets import workspace_fs
    from tinyassets.workspace_remote_cell import perform

    center = Path(universe_dir)
    if not principal:
        raise PermissionError("workspace git scope is not an admitted command center")
    parent = center
    for part in request.get("lease_parent") or ():
        parent = parent / part
        parent.mkdir(mode=0o770, exist_ok=True)
    document = dict(request)
    if document.get("options"):
        document["options"] = _local_options(document["options"], document.get("repo", ""))
    root_fd = workspace_fs.open_dir_nofollow(center)
    try:
        with tempfile.TemporaryDirectory(prefix="ta-cell-remote-") as scratch:
            return perform(document, root_fd=root_fd, root_path=center, scratch=scratch,
                           git_binary="git", path=os.environ.get("PATH", "/usr/bin:/bin"))
    finally:
        if isinstance(root_fd, int):
            os.close(root_fd)


def _render(spec, wall_seconds):
    """The preview cell: the real browser child supervisor, run here."""
    import base64

    from tinyassets.custom_agents import read_app_ui_asset
    from tinyassets.role_preview_cell import supervised

    assets = {}
    for path, sha in spec['hashes'].items():
        data = read_app_ui_asset(spec['base_path'], owner_user_id=spec['owner_user_id'], sha256=sha)
        if data is not None:
            assets[path] = base64.b64encode(data).decode('ascii')
    packet = dict(spec, base_path='/absent', asset_bytes=assets)
    out, err, code, breach = supervised(json.dumps(packet).encode('utf-8'), wall_seconds)
    if breach or code:
        out = json.dumps({'unavailable': breach or err.decode('utf-8', 'replace')[-300:]}).encode()
    return SimpleNamespace(returncode=0, stdout=out, cell={})


def _write_preview(universe_dir, ui_id, data):
    """The preview-write cell: the real universe writer, run here."""
    from tinyassets.ui_preview import write_preview_in_cell

    return write_preview_in_cell(universe_dir, ui_id, data)


def _video_frames(data, universe_dir):
    """The video cell. There is no ffmpeg to stand in for, so refuse by name.

    A test that needs frames substitutes ``role_video.frames`` itself; one that
    only reaches here by accident learns that it did.
    """
    raise RuntimeError(
        "the video cell has no double: substitute tinyassets.role_video.frames, "
        "or mark the test role_split and run it on the root oracle")


def _tool_run(universe_dir, inner, *, agent_id, stdin, limits, wall_seconds,
              output_bytes, on_wait, egress_socket, ta_socket, stop=None,
              extension_root=None):
    """The tool cell: the real bubblewrap jail and supervisor, run here.

    This is ``role_tools.run``'s daemon-side work (queueing, the seed
    boundary, the disk budget) followed by the cell's own work (the jail argv
    and ``_supervise``) in one process, which is what the jail looked like
    before the owner hand-off existed.
    """
    from dataclasses import replace

    from tinyassets import universe_tools as tools
    from tinyassets.starter_seeds import seed_boundary

    if not agent_id.strip():
        raise tools.UniverseToolError("agent_id is required")
    wall = float(wall_seconds if wall_seconds is not None else limits.wall_seconds)
    cap = int(output_bytes if output_bytes is not None else limits.output_bytes)
    cpu = min(int(limits.cpu_seconds), int(wall) + 1)
    limited = tools._limited(inner, limits, cpu_seconds=cpu)
    root = Path(universe_dir).resolve()
    process_cap = int(limits.processes) + 3
    queued: list[float] = []
    filter_fd = tools._seccomp_fd()
    try:
        mounts = {} if egress_socket is None else {"egress_socket": egress_socket}
        if ta_socket is not None:
            mounts["ta_socket"] = ta_socket
        if extension_root is not None:
            mounts["extension_root"] = extension_root
        argv = tools.TOOL_JAIL_ARGV(
            root, limited, agent_id=agent_id, seccomp_fd=filter_fd, **mounts)
        with seed_boundary(root), tools._slot(root, on_wait=on_wait, waited=queued):
            try:
                budget = tools.jail_disk.open_budget(
                    root, min_free_bytes=limits.min_free_disk_bytes,
                    min_free_inodes=limits.min_free_inodes,
                )
            except tools.jail_disk.DiskFloorRefused as below:
                raise tools.UniverseToolError(
                    f"{below}, so the tool jail will not start; nothing ran") from None
            try:
                with tools._root_cgroup(limits, process_cap) as cgroup:
                    if cgroup is not None:
                        argv = ["/bin/sh", "-c", 'echo $$ > "$0" && exec "$@"',
                                str(cgroup / "cgroup.procs"), *argv]
                    run = tools._supervise(
                        argv, root, filter_fd, stdin=stdin, limits=limits, wall=wall,
                        cap=cap, process_cap=process_cap, budget=budget, stop=stop,
                    )
            finally:
                budget.settle()
            return replace(run, waited=queued[0] if queued else 0.0,
                           notice=budget.notice, disk_bound=budget.bound)
    finally:
        os.close(filter_fd)


def _tool_prepare(universe_dir, *, agent_id="main"):
    """The maintenance cell: create the fixed directories and promote brains."""
    from tinyassets import universe_tools as tools

    root = Path(universe_dir)
    workspace = root / ".agent-workspace"
    for name in (".agent-workspace", *tools.AGENT_HARNESS_DIRS):
        (root / name).mkdir(mode=0o770, parents=True, exist_ok=True)
    before = {name for name in tools.AGENT_BRAIN_FILES if (root / name).exists()}
    tools._promote_brain_files(root, workspace, agent_id=agent_id)
    promoted = [name for name in tools.AGENT_BRAIN_FILES
                if name not in before and (root / name).exists()]
    return {"visited": 0, "promoted": promoted, "skipped": [], "truncated": False}


#: The owner uid/gid every double speaks for. A test host labels nothing in
#: 300001-399999 (that needs privilege), so one number stands for the admitted
#: owner across the doubles -- including whatever stands in for
#: ``broker.owner_identities.owner_identity``, which must agree with this.
CELL_OWNER_UID = 300001


def _owner_identity(data_root, *, principal, allocate=False):
    """The broker's identity answer: every admitted principal is the one owner."""
    from tinyassets.broker.owner_identities import OwnerIdentity, validate_principal

    validate_principal(principal)
    if type(allocate) is not bool:
        raise ValueError("allocate must be boolean")
    return OwnerIdentity(CELL_OWNER_UID, CELL_OWNER_UID)


def _snapshot_owner_uid(universe):
    """The center's dedicated owner gid."""
    return CELL_OWNER_UID


def _snapshot_seal(fd, uid, *, directory, traverse_only=False):
    """The mode a real ACL seal leaves behind; the ACL itself needs privilege.

    ``role_snapshot.seal`` publishes an access ACL naming exactly ``uid``, and
    the kernel reflects its MASK into the group bits: ``rwx--x---`` for a
    traverse-only directory, ``rwxr-x---`` for a readable one and ``r--r-----``
    for a file. Nothing is ever granted to other.
    """
    os.fchmod(fd, (0o710 if traverse_only else 0o750) if directory else 0o440)


def _storage_measure(center, scope):
    """Owner-only logical bytes on same-UID fixtures, without a launcher.

    The daemon still adds its separate platform count. Only this hand-off is
    doubled; the real raw walker preserves exclusions and inode deduplication.
    """
    import stat

    from tinyassets import storage_accounting as accounting
    from tinyassets.jail_disk import _NOT_JAIL_WRITABLE
    from tinyassets.role_content import _owner_entry
    from tinyassets.role_storage import SCOPES
    from tinyassets.workspace_owner_pool import SCRATCH_DIR

    if scope not in SCOPES:
        raise ValueError('invalid storage scope')
    center = Path(center)
    if scope == 'workspaces':
        return accounting._walk_bytes(center / 'workspaces',
                                      exclude_top=frozenset({SCRATCH_DIR}))
    excluded = accounting._NOT_USER_BYTES if scope == 'universe' else _NOT_JAIL_WRITABLE
    seen = set()
    # Legacy same-UID fixtures can alias platform and owner files. Attribute
    # those aliases to the daemon count once, preserving the public contract.
    accounting._walk_bytes(center, exclude_top=excluded, _exclude_owner=True, _seen=seen)
    total = 0
    try:
        entries = list(center.iterdir())
    except FileNotFoundError:
        return 0
    for entry in entries:
        if entry.name in excluded or not _owner_entry(entry.name):
            continue
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(info.st_mode):
            total += accounting._walk_bytes(entry, _seen=seen)
        elif stat.S_ISREG(info.st_mode):
            key = (info.st_dev, info.st_ino)
            if key not in seen:
                seen.add(key)
                total += info.st_size
    return total


@pytest.fixture(autouse=True)
def owner_cell_double(request, monkeypatch):
    """Stand in for every owner cell, unless the test wants the real one."""
    if request.node.get_closest_marker("role_split"):
        yield None
        return
    from tinyassets.broker import owner_identities

    monkeypatch.setattr(owner_identities, "owner_identity", _owner_identity)
    from tinyassets import (
        role_decoder,
        role_git,
        role_modes,
        role_node,
        role_preview,
        role_remote_git,
        role_snapshot,
        role_storage,
        role_tools,
        role_video,
    )

    monkeypatch.setattr(role_decoder, "_bounded_client", _InstalledLauncher())
    monkeypatch.setattr(role_decoder, "decode", _decode)
    monkeypatch.setattr(role_node, "run", _node)
    monkeypatch.setattr(role_git, "run", _git)
    monkeypatch.setattr(role_remote_git, "run", _remote_git)
    monkeypatch.setattr(role_preview, "render", _render)
    monkeypatch.setattr(role_preview, "write", _write_preview)
    monkeypatch.setattr(role_tools, "run", _tool_run)
    monkeypatch.setattr(role_tools, "prepare", _tool_prepare)
    monkeypatch.setattr(role_video, "frames", _video_frames)
    monkeypatch.setattr(role_storage, "measure", _storage_measure)
    monkeypatch.setattr(role_snapshot, "owner_uid", _snapshot_owner_uid)
    monkeypatch.setattr(role_snapshot, "seal", _snapshot_seal)
    # Daemon-side code checks these against the process it runs in. A test
    # process is not uid 1001 and is in no work group.
    monkeypatch.setattr(role_modes, "DAEMON_UID", os.getuid() if hasattr(os, "getuid") else 0)
    monkeypatch.setattr(role_modes, "WORK_GID", os.getgid() if hasattr(os, "getgid") else 0)
    monkeypatch.setattr(role_modes, "BROKER_READ_GID",
                        os.getgid() if hasattr(os, "getgid") else 0)
    REMOTE_REPOS.clear()
    try:
        yield _InstalledLauncher()
    finally:
        REMOTE_REPOS.clear()
