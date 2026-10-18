"""Production extraction/argv proof; chroot locally, real runsc in Linux CI."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


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


def extracted_owner(config_file):
    """Process proof only: production rootfs/UID/argv, no runsc isolation claim."""
    import fcntl
    import socket
    import struct

    config = json.loads(Path(config_file).read_text())
    root = Path(config["root"]["path"])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as interface:
        flags = bytearray(fcntl.ioctl(interface.fileno(), 0x8913, struct.pack("16sH", b"lo", 0)))
        struct.pack_into("H", flags, 16, struct.unpack_from("H", flags, 16)[0] | 1)
        fcntl.ioctl(interface.fileno(), 0x8914, flags)
    # Mountpoint creation stays in ephemeral /run, never in the RO extraction.
    subprocess.run(["mount", "-t", "tmpfs", "tmpfs", str(root / "run")], check=True)
    subprocess.run(["mount", "--rbind", "/dev", str(root / "dev")], check=True)
    subprocess.run(["mount", "-t", "proc", "proc", str(root / "proc")], check=True)
    for mount in config["mounts"]:
        destination = root / mount["destination"].lstrip("/")
        if mount["type"] == "bind":
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                if Path(mount["source"]).is_dir():
                    destination.mkdir()
                else:
                    destination.touch()
            subprocess.run(["mount", "--bind", mount["source"], str(destination)], check=True)
        elif mount["destination"] == "/tmp":
            subprocess.run(["mount", "-t", "tmpfs", "-o", ",".join(mount["options"]),
                            "tmpfs", str(destination)], check=True)
    process = config["process"]
    os.chroot(root)
    os.chdir(process["cwd"])
    os.setgroups([])
    os.setgid(process["user"]["gid"])
    os.setuid(process["user"]["uid"])
    env = dict(value.split("=", 1) for value in process["env"])
    subprocess.run([process["args"][0], "-B", "-c",
                    "import os; os.chdir('/'); assert os.getuid() >= 300001; "
                    "os.listdir('/'); os.chdir('/app'); import tinyassets"],
                   env=env, check=True)
    os.execve(process["args"][0], process["args"], env)


def launch_extracted(manager, run, owner_name):
    """Query the unmodified production app before the instrumented user pass."""
    from tinyassets.boxes.gvisor_runtime import GvisorRuntime

    old = manager.runtime
    cgroup = Path("/sys/fs/cgroup") / run.name
    real_runsc = os.environ.get("TA_REHEARSAL_RUNSC") == "1"
    runtime = GvisorRuntime(
        runsc=Path("/support/gvisor/runsc") if real_runsc else Path("/bin/true"),
        rootfs=harness(), owner_store=Path("/box-store"),
        state_root=run / "runsc", bundle_root=run / "bundles",
        cgroup_parent=cgroup, restore_epoch_file=Path("/data/.restore-epoch"))
    processes = {}
    log = tempfile.TemporaryFile()
    command = runtime._command

    def recorded(*args, **kwargs):
        try:
            return command(*args, **kwargs)
        except subprocess.CalledProcessError as exc:
            log.write((exc.stderr or "").encode())
            raise

    runtime._command = recorded

    def start(uid, gid, **kw):
        config = runtime._config({"process": {}, "mounts": []}, uid=uid, gid=gid,
                                 tree=runtime._owner_tree(uid, gid), **kw)
        path = run / "launch.json"
        path.write_text(json.dumps(config))
        processes[uid] = subprocess.Popen([
            "unshare", "--mount", "--net", "--propagation", "private", sys.executable,
            __file__, "owner", str(path)], stdout=log, stderr=log)

    def stop(uid):
        if uid in processes:
            process = processes.pop(uid)
            process.terminate()
            process.wait(30)

    if real_runsc:
        cgroup.mkdir()
        (cgroup / "cgroup.subtree_control").write_text("+cpu +memory +pids")
        manager.runtime = runtime
    else:
        manager.runtime = SimpleNamespace(start=start, stop=stop,
            alive=lambda uid: uid in processes and processes[uid].poll() is None)
    uid = None
    try:
        uid = manager.ensure_awake(owner_name)
        assert not manager._rpc(manager.boxes[uid])["draining"]
        if real_runsc:
            # Use the image interpreter under the OCI owner UID, never host Python.
            runtime._command("exec", "--user", f"{uid}:{uid}", runtime.box_id(uid),
                             "/opt/venv/bin/python", "-B", "-c",
                             "import os; os.chdir('/'); os.listdir('/'); "
                             "os.chdir('/app'); import tinyassets; "
                             "assert os.getuid() >= 300001")
        manager.runtime.stop(uid)
        manager.boxes.clear()
    except BaseException as exc:
        log.seek(0)
        raise RuntimeError("production extraction launch failed: " +
                           log.read().decode(errors="replace")) from exc
    finally:
        for child_uid in list(processes):
            stop(child_uid)
        if real_runsc:
            for box in list(manager.boxes.values()):
                runtime.stop(box.uid)
            netns = runtime.state_root / "null-netns"
            if netns.exists():
                subprocess.run(["umount", str(netns)], check=True)
        manager.runtime = old
        log.close()
        if real_runsc:
            for child in cgroup.iterdir():
                if child.is_dir():
                    child.rmdir()
            cgroup.rmdir()


if __name__ == "__main__":
    {"extract": extract, "owner": extracted_owner}[sys.argv[1]](*sys.argv[2:])
