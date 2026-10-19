"""Cold account destruction on real XFS, with the pinned runsc and zero launches."""
import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path


def test_sleeping_and_suspended_deletion_without_box_starts():
    """Run through linux_oracle --as-root --docker-engine; never skip a missing venue."""
    assert hasattr(os, "geteuid") and os.geteuid() == 0, "root Linux oracle required"
    name = "ta-cold-delete-" + uuid.uuid4().hex[:12]
    root = Path(__file__).resolve().parents[1]
    image = os.environ["TA_REHEARSAL_BASE_IMAGE"]
    runsc = Path(os.environ["TA_REHEARSAL_RUNSC_DIR"]) / "runsc"

    def docker(*args):
        return subprocess.run(["docker", *args], check=True, capture_output=True, text=True)

    try:
        docker("create", "--name", name, "--privileged", "--network=none",
               "-e", "PYTHONPATH=/work", "--entrypoint", "python", image, "/probe.py")
        docker("cp", str(root / "tinyassets"), name + ":/work/tinyassets")
        docker("cp", str(root / "deploy"), name + ":/work/deploy")
        docker("cp", str(runsc), name + ":/runsc")
        docker("cp", str(Path(__file__)), name + ":/probe.py")
        result = subprocess.run(["docker", "start", "--attach", name],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PASS sleeping" in result.stdout and "PASS suspended" in result.stdout
    finally:
        docker("rm", "--force", name)


async def probe():
    import socket
    import sqlite3
    from types import SimpleNamespace

    from tinyassets.boxes.gvisor_runtime import GvisorRuntime
    from tinyassets.boxes.owner_daemons import OwnerDaemons
    from tinyassets.boxes.relay import OwnerRelays
    from tinyassets.boxes.volumes import OwnerVolumes
    from tinyassets.broker import box_admission, owner_volumes
    from tinyassets.broker.box_admission import owner_record
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.broker.owner_reads import Plans
    from tinyassets.broker.server import BOX_HOST, _Connection
    from tinyassets.erasure_ledger import ErasureLedger, principal_digest, require_active

    root, store = Path("/venue"), Path("/venue/owners")
    store.mkdir(parents=True)
    disk = root / "store.img"
    with disk.open("wb") as stream:
        stream.truncate(512 * 1024**2)
    subprocess.run(["mkfs.xfs", "-f", "-m", "reflink=1", str(disk)],
                   check=True, capture_output=True)
    subprocess.run(["mount", "-o", "loop,prjquota", str(disk), str(store)], check=True)
    try:
        filesystem = subprocess.check_output(
            ["findmnt", "--mountpoint", str(store), "-no", "UUID"], text=True).strip()
        proof = root / ".owners-identity"
        proof.write_text("xfs " + filesystem + "\n")
        proof.chmod(0o600)
        shared, sockets = root / "shared", root / "sockets"
        shared.mkdir()
        sockets.mkdir()
        ledger = ErasureLedger(shared)
        ledger.initialize()
        broker = shared / ".broker/state"
        broker.mkdir(parents=True, mode=0o700)
        identities = OwnerIdentities(broker / "identities.db", initialize=True,
                                     plans=Plans(shared))
        with sqlite3.connect(identities.path) as db:
            db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INT, "
                       "project_id INT, bhard_bytes INT)")
        channel = _Connection(SimpleNamespace(_data_root=shared, _owner_identities=identities),
                              None, BOX_HOST)

        class Admission:
            def volume(self, owner, action="binding", **arguments):
                return owner_volumes.dispatch(identities, {
                    "op": "BOX_VOLUME", "principal": owner,
                    "action": action, "arguments": arguments})

            def owner(self, owner, *, rotate=False):
                require_active(shared, owner)
                return owner_record(identities, owner, rotate=rotate)

            def retire(self, owner, uid):
                assert channel._retire_box_admission({
                    "op": "BOX_RETIRE_ADMISSION", "principal": owner, "uid": uid
                }) == {"op": "BOX_ADMISSION_RETIRED"}

        runtime = GvisorRuntime(runsc=Path("/runsc"), rootfs=root,
                                state_root=root / "state", bundle_root=root / "bundles",
                                cgroup_parent=root / "cgroups")
        commands = []
        command = runtime._command

        def checked(*args, **kwargs):
            commands.append(args)
            assert args[0] not in {"run", "create", "start", "spec", "exec"}, args
            return command(*args, **kwargs)

        runtime._command = checked
        starts, start = [], runtime.start

        def checked_start(*args, **kwargs):
            starts.append(args)
            return start(*args, **kwargs)

        runtime.start = checked_start
        relays = OwnerRelays(root / "relays", Admission())
        volumes = OwnerVolumes(store, root / "mounts", ":local:/unused-snapshots")
        manager = OwnerDaemons(runtime, Admission(), volumes, relays, sockets)
        for owner in ("sleeping", "suspended"):
            uid = identities.resolve(owner, allocate=True).uid
            identities.admission("admit", owner, owner)
            box_admission.provision(identities, shared, owner, owner)
            identities.box_route(owner, rotate=True)
            identities.plans.apply(owner, tier="paid", event_created=1)
            if owner == "suspended":
                identities.plans.set_suspension(owner, reason="billing", suspended=True)
            volumes.create(uid, 2 * 1024**3)
            Admission().volume(owner, "created")
            image = store / f"{uid}.img"
            assert image.stat().st_size == 2 * 1024**3
            relay = relays.bind(owner, uid)
            directory, secret = manager._paths(uid, "synthetic-secret")
            app = socket.socket(socket.AF_UNIX)
            app.bind(str(directory / "app.sock"))
            app.close()
            ledger.execute("add", {"digest": principal_digest(owner), "principal": owner,
                                   "uids": [uid]})
            try:
                manager.resolve(owner, cleanup=True)
            except LookupError:
                pass
            else:
                raise AssertionError("cold owner acquired a cleanup box")
            manager.destroy(owner, uid)
            assert json.loads(runtime._command("list", "--format=json").stdout) in (None, [])
            assert not any(path.exists() for path in (image, relay, directory, secret))
            volumes._detached(uid)
            assert Admission().volume(owner)["uid"] == uid
            assert identities.center_state(owner) == "retired"
            with sqlite3.connect(identities.path) as db:
                assert not db.execute("SELECT 1 FROM owner_box_secrets WHERE principal=?",
                                      (owner,)).fetchall()
            print("PASS", owner, "opaque volume sockets secret admission; zero starts")
        assert commands and all(cmd[0] in {"kill", "delete", "list"} for cmd in commands)
        assert not starts
    finally:
        subprocess.run(["umount", str(store)], check=True)


if __name__ == "__main__":
    asyncio.run(probe())
