"""Fixed browser child supervisor, invoked only inside the admitted preview cell."""
from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from tinyassets.ui_preview import (
    MAX_CHILD_OUTPUT,
    TREE_MEMORY_BYTES,
    TREE_PROCESSES,
    PreviewUnavailable,
    _child,
)


def _proc_snapshot() -> dict[int, tuple[int, int, bool, int]]:
    """Host PID -> (parent PID, RSS bytes, zombie, starttime) from Linux /proc."""
    import os

    snapshot = {}
    page = os.sysconf("SC_PAGE_SIZE")
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[1].split()
        except OSError:
            continue
        snapshot[int(entry)] = (int(fields[1]), int(fields[21]) * page,
                                fields[0] == b"Z", int(fields[19]))
    return snapshot

def _descendants(root: int, snapshot: dict[int, tuple[int, int, bool, int]]) -> set[int]:
    # Traverse zombies too: their live children still belong to this tree.
    children: dict[int, list[int]] = {}
    for pid, (ppid, _, _, _) in snapshot.items():
        children.setdefault(ppid, []).append(pid)
    found = set()
    pending = list(children.get(root, []))
    while pending:
        pid = pending.pop()
        if pid not in found:
            found.add(pid)
            pending.extend(children.get(pid, []))
    return found

def supervised(stdin: bytes, wall_seconds: float) -> tuple[bytes, bytes, int, str]:
    """Contain even detached Chromium processes in a Linux PID namespace."""
    import os
    import signal

    if sys.platform != 'linux':
        raise PreviewUnavailable('ui_preview_unavailable: previews need Linux')
    if (len(stdin) > MAX_CHILD_OUTPUT or type(wall_seconds) not in (int, float)
            or not 0 < wall_seconds <= 60):
        raise ValueError('preview packet exceeds its bound')
    bwrap = shutil.which('bwrap')
    if not bwrap:
        raise PreviewUnavailable('ui_preview_unavailable: previews need bubblewrap')
    # The enclosing owner cell supplies the filesystem and network boundary.
    # This namespace contains the browser descendants until cleanup completes.
    command = [bwrap, '--unshare-pid', '--die-with-parent', '--bind', '/', '/',
               '--dev', '/dev', '--proc', '/proc', '--', sys.executable,
               '-m', 'tinyassets.role_preview_cell']
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parents[1]),
        start_new_session=True,
    )
    chunks: dict[str, list[bytes]] = {"out": [], "err": []}
    sizes = {"out": 0, "err": 0}

    def drain(name: str, stream: Any, cap: int) -> None:
        for chunk in iter(lambda: stream.read(65536), b""):
            if sizes[name] < cap:
                chunks[name].append(chunk[: cap - sizes[name]])
            sizes[name] += len(chunk)

    readers = [threading.Thread(target=drain, args=("out", process.stdout, MAX_CHILD_OUTPUT),
                                daemon=True),
               threading.Thread(target=drain, args=("err", process.stderr, 65536), daemon=True)]
    for reader in readers:
        reader.start()
    def feed():
        try:
            process.stdin.write(stdin)
        except OSError:
            pass
        finally:
            process.stdin.close()

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    deadline = time.monotonic() + wall_seconds
    breach = ""
    seen: dict[int, int] = {}
    try:
        while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            snapshot = _proc_snapshot()
            descendants = _descendants(process.pid, snapshot)
            seen.update({pid: snapshot[pid][3] for pid in descendants})
            members = [info for pid, info in snapshot.items()
                       if not info[2] and seen.get(pid) == info[3]]
            if sum(rss for _, rss, _, _ in members) > TREE_MEMORY_BYTES:
                breach = "memory: the render used more memory than a preview may"
            elif len(members) > TREE_PROCESSES:
                breach = "processes: the render started more processes than a preview may"
            if time.monotonic() > deadline:
                breach = f"timeout: the render did not finish in {wall_seconds:.0f} s"
            if sizes["out"] > MAX_CHILD_OUTPUT:
                breach = "failed: the render printed more than a preview may"
            if breach:
                break
            time.sleep(0.25)
    finally:
        # Keep bwrap unreaped: its PID cannot be reused while cleanup runs.
        # Its parent-death signal kills namespace init, and the kernel then
        # kills every namespace member. Never signal another numeric PID.
        settle = time.monotonic() + 10
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(process.pid, signal.SIGKILL)
        while True:
            snapshot = _proc_snapshot()
            # Once bwrap is dead its children re-parent away from it, so a
            # member still dying is found by identity (pid + starttime) too.
            alive = {pid for pid in _descendants(process.pid, snapshot)
                     if not snapshot[pid][2]}
            alive |= {pid for pid, start in seen.items()
                      if pid in snapshot and snapshot[pid][3] == start
                      and not snapshot[pid][2]}
            if not alive or time.monotonic() >= settle:
                break
            time.sleep(0.1)
        for reader in [writer, *readers]:
            reader.join(timeout=max(0, settle - time.monotonic()))
        contained = not alive and not any(reader.is_alive() for reader in [writer, *readers])
        # Reaping is the final cleanup operation, after all tree walks.
        try:
            process.wait(timeout=max(0, settle - time.monotonic()))
        except subprocess.TimeoutExpired:
            contained = False
        if not contained:
            raise PreviewUnavailable("preview cell child containment failed")

    process.stdout.close()
    process.stderr.close()
    if sizes['out'] > MAX_CHILD_OUTPUT:
        breach = 'failed: the render printed more than a preview may'
    return b"".join(chunks["out"]), b"".join(chunks["err"]), process.returncode, breach

def main():
    raw = sys.stdin.buffer.read(MAX_CHILD_OUTPUT + 1)
    if len(raw) > MAX_CHILD_OUTPUT:
        raise ValueError('preview packet exceeds its bound')
    spec = json.loads(raw)
    if spec.get('base_path') != '/absent' or not isinstance(spec.get('asset_bytes'), dict):
        raise ValueError('preview child requires admitted data only')
    sys.stdout.write(json.dumps(_child(spec)) + '\n')
    sys.stdout.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
