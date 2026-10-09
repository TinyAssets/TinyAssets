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


def _render(spec, wall_seconds):
    """The preview cell: the real browser child supervisor, run here."""
    from tinyassets import ui_preview

    out, _err, code, _breach = ui_preview._supervised(
        json.dumps(spec).encode("utf-8"), wall_seconds)
    return SimpleNamespace(returncode=code, stdout=out, cell={})


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


@pytest.fixture(autouse=True)
def owner_cell_double(request, monkeypatch):
    """Stand in for every owner cell, unless the test wants the real one."""
    if request.node.get_closest_marker("role_split"):
        yield None
        return
    from tinyassets import (
        role_decoder,
        role_git,
        role_modes,
        role_node,
        role_preview,
        role_snapshot,
        role_tools,
        role_video,
    )

    monkeypatch.setattr(role_decoder, "_bounded_client", _InstalledLauncher())
    monkeypatch.setattr(role_decoder, "decode", _decode)
    monkeypatch.setattr(role_node, "run", _node)
    monkeypatch.setattr(role_git, "run", _git)
    monkeypatch.setattr(role_preview, "render", _render)
    monkeypatch.setattr(role_preview, "write", _write_preview)
    monkeypatch.setattr(role_tools, "run", _tool_run)
    monkeypatch.setattr(role_tools, "prepare", _tool_prepare)
    monkeypatch.setattr(role_video, "frames", _video_frames)
    monkeypatch.setattr(role_snapshot, "owner_uid", _snapshot_owner_uid)
    monkeypatch.setattr(role_snapshot, "seal", _snapshot_seal)
    # Daemon-side code checks these against the process it runs in. A test
    # process is not uid 1001 and is in no work group.
    monkeypatch.setattr(role_modes, "DAEMON_UID", os.getuid() if hasattr(os, "getuid") else 0)
    monkeypatch.setattr(role_modes, "WORK_GID", os.getgid() if hasattr(os, "getgid") else 0)
    monkeypatch.setattr(role_modes, "BROKER_READ_GID",
                        os.getgid() if hasattr(os, "getgid") else 0)
    yield _InstalledLauncher()
