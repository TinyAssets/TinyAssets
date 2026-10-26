"""Production extraction, service startup and first wake under the pinned runsc."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def extract(image_id):
    source = Path("/support/deploy/deploy_fail_safe.sh").read_text()
    start = source.index("extract_box_harness() {")
    function = source[start:source.index("\n}", start) + 2]
    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir="/harness"))
    with Path("/support/image.tar").open("rb") as archive:
        subprocess.run(["bash", "-euo", "pipefail", "-c",
                        function + '\nextract_box_harness "$1" "$2"',
                        "extract", str(stage), image_id], stdin=archive, check=True)
    assert stage.stat().st_mode & 0o777 == 0o555
    stage.rename(Path("/harness") / ("harness-" + image_id.removeprefix("sha256:")))


def harness():
    roots = list(Path("/harness").glob("harness-*"))
    if len(roots) != 1:
        raise RuntimeError("production extraction is missing or ambiguous")
    return roots[0]


@contextmanager
def production_host(output, *, runsc=Path("/support/gvisor/runsc")):
    """Emulate systemd's placement only; the unmodified entry point delegates."""
    installed = Path("/opt/tinyassets/boxhostd")
    rootfs = installed / harness().name
    assert rootfs.is_dir(), "candidate extraction must be mounted at its installed location"
    (installed / "harness-current").symlink_to(rootfs.name)
    (installed / "current").mkdir()
    (installed / "current/runsc").symlink_to(runsc)
    cgroup = Path("/sys/fs/cgroup") / ("boxhostd@" + uuid.uuid4().hex + ".service")
    cgroup.mkdir()  # systemd creates the unit; no owner cgroup or controller writes here.
    # systemd applies the unit's limits, but leaves subtree delegation to ExecStart.
    for name, value in (("memory.max", str(7 * 1024**3)), ("pids.max", "5120"),
                        ("cpu.max", "300000 100000")):
        (cgroup / name).write_text(value)
    env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
           "HOME": "/root", "BACKUP_DEST": ":local:/snapshots/owner-snapshots",
           "UNIVERSE_SERVER_URL": "https://rehearsal.invalid/mcp"}
    code = """
import os, sys
from pathlib import Path
parent = Path(sys.argv[1])
(parent / 'cgroup.procs').write_text(str(os.getpid()))
assert (parent / 'cgroup.procs').read_text().split() == [str(os.getpid())]
assert not (parent / 'cgroup.subtree_control').read_text().strip()
os.execve(sys.executable, [sys.executable, '-B', '/support/deploy/boxhostd.py',
                          '--data-root', '/data'], os.environ)
"""
    process = None
    try:
        subprocess.run(["bash", "/support/deploy/box-store-check.sh"],
                       env=env, stdout=output, stderr=output, check=True)
        # Match ExecStart's host interpreter. Application site-packages must
        # not hide an import dependency unavailable to the production host.
        process = subprocess.Popen(["unshare", "--mount", "--propagation", "private",
                                    "/usr/bin/python3", "-B", "-c", code, str(cgroup)],
                                   env=env, stdout=output, stderr=output)
        socket = Path("/run/tinyassets-boxhostd/broker-registration.sock")
        deadline = time.monotonic() + 30
        while not socket.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                output.seek(0)
                raise RuntimeError("production boxhostd did not open broker registration: " +
                                   output.read().decode(errors="replace"))
            time.sleep(.05)
        assert not (cgroup / "cgroup.procs").read_text().strip()
        assert str(process.pid) in (cgroup / "supervisor/cgroup.procs").read_text().split()
        assert {"cpu", "memory", "pids"} <= set(
            (cgroup / "cgroup.subtree_control").read_text().split())
        yield cgroup, env
    finally:
        if process is not None:
            process.terminate()
            try:
                # C13: callers finish broker sleep/publication before host stop.
                # Never silently kill a host whose local shutdown has not completed.
                if process.wait(180) != 0:
                    raise RuntimeError("production host did not finish shutdown")
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(10)
                raise RuntimeError("production host shutdown timed out") from None
        try:
            # Include failed launches; none may leave a sandbox in the engine.
            # This is the unit's default KillMode=control-group teardown.
            if (cgroup / "cgroup.procs").exists():
                (cgroup / "cgroup.kill").write_text("1")
                deadline = time.monotonic() + 10
                while "populated 1" in (cgroup / "cgroup.events").read_text():
                    if time.monotonic() > deadline:
                        raise RuntimeError("production launch cgroup did not empty")
                    time.sleep(.05)
                for child in sorted(cgroup.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                    if child.is_dir():
                        child.rmdir()
                cgroup.rmdir()
        finally:
            (installed / "current/runsc").unlink()
            (installed / "current").rmdir()
            (installed / "harness-current").unlink()


def published_sleep(owner_name):
    """Prove completion, including a busy stop resumed by the existing waker."""
    import sqlite3
    from contextlib import closing

    from tinyassets.broker.lifecycle_control import request

    def snapshot():
        with closing(sqlite3.connect('/data/.broker/state/owner-identities.db')) as db:
            return db.execute('SELECT snapshot_id,work FROM owner_volumes WHERE principal=?',
                              (owner_name,)).fetchone()

    previous = snapshot()[0]
    try:
        request("sleep", owner=owner_name)
    except RuntimeError:
        # A busy executor leaves the broker's durable snapshot intent. Do not
        # retry the command or bypass drain; prove the waker finishes that work.
        reference, work = snapshot()
        if work is None:
            if reference != previous:
                return reference
            raise
        if json.loads(work)["kind"] != "snapshot":
            raise
    deadline = time.monotonic() + 180
    while True:
        reference, work = snapshot()
        if reference != previous and work is None:
            return reference
        if time.monotonic() >= deadline:
            raise RuntimeError("broker did not publish its pending sleep snapshot")
        time.sleep(.05)


def production_launch(owner_name):
    """UID1001 asks the broker to wake, using production volumes, relay and runsc."""
    import sqlite3
    from contextlib import closing

    def snapshot():
        with closing(sqlite3.connect('/data/.broker/state/owner-identities.db')) as db:
            return db.execute('SELECT snapshot_id,work FROM owner_volumes WHERE principal=?',
                              (owner_name,)).fetchone()

    previous_snapshot = snapshot()[0]
    with tempfile.TemporaryFile() as output:
        broker = None
        try:
            with production_host(output) as (cgroup, env):
                broker = subprocess.Popen([
                    sys.executable, "-B", "/support/rehearsal_serve.py", "registered-broker",
                    "/run/tinyassets-boxhostd/broker"], env=env, stdout=output, stderr=output)
                deadline = time.monotonic() + 30
                while not Path("/run/tinyassets-boxhostd/broker/broker.sock").exists():
                    if broker.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("launch broker did not start")
                    time.sleep(.05)
                code = """
import asyncio, json, sys
from pathlib import Path
from tinyassets.boxes.daemon_control import request
from tinyassets.broker.owner_identities import wake_owner
from tinyassets.broker.supervisor import BrokerSupervisor
from tinyassets.router_credentials import serving
supervisor = BrokerSupervisor.from_boxhost(
    Path('/data'), socket_path=Path('/run/tinyassets-boxhostd/broker/broker.sock'),
    proof='synthetic-rehearsal-proof' * 2)
supervisor.start()
owner = sys.argv[1]
async def launch():
    async with serving(Path('/data')):
        await asyncio.to_thread(wake_owner, Path('/data'), principal=owner)
        status = await asyncio.to_thread(request, 'status', owner)
        assert status['awake']
        print(json.dumps({'uid': status['uid']}))
asyncio.run(launch())
"""
                # The broker returns only after authenticated owner app readiness.
                result = subprocess.run([
                    "setpriv", "--reuid=1001", "--regid=1001", "--groups=1100,1101,1102",
                    "--bounding-set=-all", "--no-new-privs",
                    sys.executable, "-B", "-c", code, owner_name],
                    env={**env, "PYTHONPATH": "/app"},
                    capture_output=True, text=True, timeout=90, check=True)
                uid = json.loads(result.stdout)["uid"]
                owner = cgroup / f"owner-{uid}"
                assert owner.is_dir() and owner.parent == (cgroup / "supervisor").parent
                assert int((owner / "memory.max").read_text()) == 2 * 1024**3
                assert int((owner / "pids.max").read_text()) == 1024
                assert int((owner / "cpu.weight").read_text()) > 0
                subprocess.run([
                    "/support/gvisor/runsc", "--root", "/var/lib/boxhostd/state/runsc",
                    "exec", "--user", f"{uid}:{uid}", f"owner-{uid}",
                    "/opt/venv/bin/python", "-B", "-c",
                    "import os; os.chdir('/'); os.listdir('/'); os.chdir('/app'); "
                    "import tinyassets; assert os.getuid() >= 300001"],
                    check=True, stdout=output, stderr=output, timeout=30)
                # C13: the broker coordinates sleep and publication while the
                # executor is still available. Host shutdown only executes.
                published_sleep(owner_name)
            current_snapshot, pending = snapshot()
            assert current_snapshot != previous_snapshot and pending is None, (
                "production shutdown did not publish its completed owner snapshot")
        except BaseException as exc:
            output.seek(0)
            detail = getattr(exc, "stderr", "") or ""
            raise RuntimeError("production entry launch failed: " + detail +
                               output.read().decode(errors="replace")) from exc
        finally:
            if broker is not None:
                broker.terminate()
                broker.wait(15)


if __name__ == "__main__":
    {"extract": extract}[sys.argv[1]](*sys.argv[2:])
