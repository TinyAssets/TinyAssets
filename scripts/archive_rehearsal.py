"""Disposable XFS venue for the deploy transaction; never logs private output."""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-10-17T00-00-00Z"
DATA_ARCHIVE = f"tinyassets-snapshot-{STAMP}.tar.gz"
BOX_ARCHIVE = f"tinyassets-box-store-{STAMP}.tar.gz"


class ImageNotFound(RuntimeError):
    pass


def run(*argv, check=True, env=None):
    result = subprocess.run(argv, text=True, capture_output=True, env=env)
    if check and result.returncode:
        raise RuntimeError("operation failed")
    return result


def docker(*argv, check=True):
    return run("docker", *argv, check=check)


def cleanup_mounts(prefix):
    """Run every mount/loop command in PID 1's mount namespace via its shell."""
    if not re.fullmatch(r"ta-s6-[a-f0-9]{12}", prefix):
        raise RuntimeError("invalid cleanup venue")

    def host(*args, check=False):
        # Keep Python in the helper: Docker Desktop's VM has only shell tools.
        return subprocess.run(["nsenter", "-t", "1", "-m", "sh", "-c",
                               'exec "$@"', "sh", *args], check=check,
                              capture_output=True, text=True)

    def loops():
        result = host("losetup", "--list", "--json", "--output", "NAME,BACK-FILE",
                      check=True)
        return {item["name"]: item["back-file"]
                for item in json.loads(result.stdout).get("loopdevices", [])
                if any(prefix + "-" + kind + "/" in item.get("back-file", "")
                       for kind in ("image", "store"))}

    def mounts():
        def decode(value):
            return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)

        return {decode(line.split()[1])
                for line in host("cat", "/proc/mounts", check=True).stdout.splitlines()
                if prefix in decode(line.split()[1])}

    for _ in range(3):
        selected = mounts()
        if not selected:
            break
        for target in sorted(selected, key=lambda path: (path.count("/"), len(path), path),
                             reverse=True):
            # Propagation may remove aliases or re-expose a covered parent.
            if target in mounts():
                host("umount", target)
    # mount -o loop may autoclear the device when its last mount is removed.
    for device in sorted(loops()):
        host("losetup", "-d", device)
    residue = mounts() | set(loops().values())
    if residue:
        raise RuntimeError("cleanup residue: " + ", ".join(sorted(residue)))


class Venue:
    def __init__(self, image, store_bytes):
        self.image, self.store_bytes = image, store_bytes
        self.prefix = "ta-s6-" + uuid.uuid4().hex[:12]
        self.names = {
            k: self.prefix + "-" + k
            for k in ("image", "store", "parent", "data", "backup", "support", "harness")
        }
        self.volumes, self.containers = [], []
        self.device = ""
        self.mounted = False

    def container(self, *argv, check=True):
        name = self.prefix + "-run-" + str(len(self.containers))
        self.containers.append(name)
        return docker("run", "--name", name, "--network", "none", *argv, check=check)

    def setup(self, archive):
        if docker("image", "inspect", self.image, check=False).returncode:
            raise ImageNotFound(f"image not found: {self.image}")
        for key in ("image", "store", "parent", "backup", "support", "harness"):
            docker("volume", "create", self.names[key])
            self.volumes.append(self.names[key])

        def mountpoint(key):
            return docker(
                "volume", "inspect", "--format", "{{ .Mountpoint }}", self.names[key]
            ).stdout.strip()

        self.store, self.parent, self.backup = map(mountpoint, ("store", "parent", "backup"))
        self.loop_image = mountpoint("image") + "/store.img"
        # cp also works inside the oracle: /work is not an engine host path.
        name = self.prefix + "-upload"
        self.containers.append(name)
        docker(
            "create",
            "--name",
            name,
            "-v",
            self.names["backup"] + ":/backup",
            "-v",
            self.names["support"] + ":/support",
            "--entrypoint",
            "true",
            self.image,
        )
        docker("cp", str(archive), name + ":/backup/input.tar.gz")
        docker("cp", str(ROOT / "deploy"), name + ":/support/deploy")
        docker(
            "cp", str(ROOT / "scripts/rehearsal_serve.py"), name + ":/support/rehearsal_serve.py"
        )
        docker("cp", str(ROOT / "scripts/rehearse_box_move.py"),
               name + ":/support/rehearse_box_move.py")
        docker("cp", str(ROOT / "scripts/rehearsal_restore_provider.py"),
               name + ":/support/rehearsal_restore_provider.py")
        docker("cp", str(ROOT / "scripts/rehearsal_launch.py"),
               name + ":/support/rehearsal_launch.py")
        # Export the candidate image once. The extraction function below is the
        # same one install_box_harness calls in deploy_fail_safe.sh.
        with tempfile.TemporaryDirectory(prefix="ta-harness-") as temporary:
            image_tar = Path(temporary) / "image.tar"
            docker("export", "--output", str(image_tar), name)
            docker("cp", str(image_tar), name + ":/support/image.tar")
        if os.environ.get("TA_REHEARSAL_RUNSC") == "1":
            docker("cp", os.environ["TA_REHEARSAL_RUNSC_DIR"], name + ":/support/gvisor")
        image_id = docker("image", "inspect", "-f", "{{.Id}}", self.image).stdout.strip()
        self.container("--rm", "--user", "0",
                       "-v", self.names["support"] + ":/support:ro",
                       "-v", self.names["harness"] + ":/harness",
                       "--entrypoint", "python", self.image, "-B",
                       "/support/rehearsal_launch.py", "extract", image_id)
        self.container(
            "--rm",
            "--user",
            "0",
            "-v",
            self.names["parent"] + ":/parent",
            "--entrypoint",
            "mkdir",
            self.image,
            "-p",
            "/parent/_data",
        )
        docker(
            "volume",
            "create",
            "--driver",
            "local",
            "--opt",
            "type=none",
            "--opt",
            "o=bind",
            "--opt",
            "device=" + self.parent + "/_data",
            self.names["data"],
        )
        self.volumes.append(self.names["data"])
        self.container(
            "--rm",
            "--privileged",
            "--pid=host",
            "--user",
            "0",
            "-v",
            self.names["image"] + ":/image",
            "--entrypoint",
            "sh",
            self.image,
            "-ec",
            'truncate -s "$1" /image/store.img; mkfs.xfs -f /image/store.img >/dev/null; '
            'nsenter -t 1 -m mount --bind "$3" "$3"; '
            'nsenter -t 1 -m mount --make-private "$3"; '
            'nsenter -t 1 -m mount -o loop,prjquota "$2" "$3"',
            "sh",
            str(self.store_bytes),
            "/proc/1/root" + self.loop_image,
            "/proc/1/root" + self.store,
        )
        self.mounted = True
        source = self.container(
            "--rm",
            "-v",
            self.names["store"] + ":/box-store",
            "--entrypoint",
            "findmnt",
            self.image,
            "--mountpoint",
            "/box-store",
            "-no",
            "SOURCE,FSTYPE,OPTIONS",
        ).stdout.split()
        if len(source) != 3 or source[1] != "xfs" or "prjquota" not in source[2]:
            raise RuntimeError("mount failed")
        self.device = source[0]
        self.container(
            "--rm",
            "--user",
            "0",
            "-v",
            self.names["parent"] + ":/parent",
            "-v",
            self.names["backup"] + ":/backup",
            "--entrypoint",
            "sh",
            self.image,
            "-ec",
            "tar -xzf /backup/input.tar.gz -C /parent --numeric-owner --acls --xattrs; "
            "mv /backup/input.tar.gz /backup/original.tar.gz; "
            'tar -czf /backup/"$1" -C /parent --numeric-owner --acls --xattrs _data; '
            "mkdir -p /tmp/empty/owners; "
            'tar -czf /backup/"$2" -C /tmp/empty owners; cd /backup; '
            'sha256sum "$1" > "$1.sha256"; sha256sum "$2" > "$2.sha256"',
            "sh",
            DATA_ARCHIVE,
            BOX_ARCHIVE,
        )

    def migration(self, mode, *, interrupt=""):
        bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
        if not bash:
            raise RuntimeError("shell unavailable")
        env = {
            **os.environ,
            "CUTOVER_CRASH_AT": interrupt,
            "MSYS_NO_PATHCONV": "1",
            "MSYS2_ARG_CONV_EXCL": "*",
        }
        args = [mode, "/data"]
        if mode in {"preflight", "apply", "verify"}:
            args += ["--store", "/box-store"]
        if mode == "apply":
            args += [
                "--backup",
                "/backup/" + DATA_ARCHIVE,
                "--box-backup",
                "/backup/" + BOX_ARCHIVE,
            ]
        return run(
            bash,
            (ROOT / "deploy/deploy_fail_safe.sh").as_posix(),
            "--box-migration-container",
            self.names["data"],
            self.store,
            self.device,
            self.backup,
            self.image,
            *args,
            check=False,
            env=env,
        )

    def helper(self, code, *args):
        # Every ephemeral container installs the same device identity proof at
        # its own mount path, as host setup does in production.
        code = ("from pathlib import Path; import subprocess; "
                "uuid=subprocess.check_output(['findmnt','--mountpoint','/box-store',"
                "'-no','UUID'],text=True).strip(); assert uuid; "
                "p=Path('/.box-store-identity'); p.write_text('xfs '+uuid+'\\n'); "
                "p.chmod(0o600); " + code)
        return self.container(
            "--rm",
            "--user",
            "0",
            "--device",
            self.device + ":" + self.device,
            "-v",
            self.names["data"] + ":/data",
            "--mount",
            "type=bind,src=" + self.store + ",dst=/box-store",
            "-v",
            self.names["support"] + ":/support:ro",
            "-v",
            self.names["backup"] + ":/backup",
            "--entrypoint",
            "python",
            self.image,
            "-B",
            "-c",
            code,
            *args,
        )

    def manifests(self):
        return json.loads(
            self.helper(
                "import json; from pathlib import Path; "
                'j=json.loads(Path("/data/.owner-cutover.json").read_text()); '
                'print(json.dumps([j["final_manifest"],j["owner_manifests"]],sort_keys=True))'
            ).stdout
        )

    def restore(self, *, replay=False, disaster=False, refuse=False):
        # Provider responses and absent host services are recorded at this
        # venue boundary. Extraction, fencing, quotas and swap are real.
        script = r"""set -eu
printf 'xfs %s\n' "$(findmnt --mountpoint /box-store -no UUID)" > /.box-store-identity
chmod 600 /.box-store-identity
mkdir -p /tmp/bin /opt/tinyassets/boxhostd/current
cat > /tmp/bin/docker <<'SH'
#!/bin/sh
case "$1 $2" in
"volume inspect") echo /parent/_data;;
"ps -q") exit 0;;
*) exit 79;;
esac
SH
printf '#!/bin/sh\nexit 0\n' > /tmp/bin/systemctl
printf '#!/bin/sh\nprintf "[]\\n"\n' > /opt/tinyassets/boxhostd/current/runsc
chmod +x /tmp/bin/* /opt/tinyassets/boxhostd/current/runsc
export PATH=/tmp/bin:$PATH
bash /support/deploy/backup-restore.sh
"""
        if disaster:
            script = script.replace("bash /support/deploy/backup-restore.sh",
                                    "bash /support/deploy/backup-restore.sh "
                                    "--disaster-restore --acknowledge-unrecoverable-facts "
                                    "--rehearsal-provider")
        result = self.container(
            "--rm",
            "--privileged",
            "--user",
            "0",
            "-v",
            self.names["parent"] + ":/parent",
            "-v",
            self.names["backup"] + ":/backup:ro",
            "-v",
            self.names["support"] + ":/support:ro",
            "-v",
            self.names["support"] + ":/run/tinyassets-rehearsal:ro",
            "--device",
            self.device + ":" + self.device,
            "--mount",
            "type=bind,src=" + self.store + ",dst=/box-store",
            "-e",
            "BACKUP_FILE=/backup/" + ("replay/tinyassets-data-2026-10-17T01-00-00Z.tar.gz"
                                       if replay else DATA_ARCHIVE),
            "-e",
            "BOX_BACKUP_FILE=/backup/" + ("replay/tinyassets-box-store-2026-10-17T01-00-00Z.tar.gz"
                                           if replay else BOX_ARCHIVE),
            "-e",
            "BACKUP_VOLUME=disposable",
            "-e",
            "BOX_STORE_DIR=/box-store",
            "-e",
            "BACKUP_LOG=/tmp/restore.log",
            "--entrypoint",
            "sh",
            self.image,
            "-ec",
            script,
            check=False,
        )
        expected = 4 if refuse else 6 if disaster else 0
        if result.returncode != expected:
            raise RuntimeError("restore failed: " + str(result.returncode))
        if replay or refuse:
            return
        self.helper(
            "from pathlib import Path; import tarfile; "
            "archive=tarfile.open('/backup/original.tar.gz'); "
            "root=Path('/data'); "
            "assert all((root / m.name.removeprefix('_data/')).read_bytes() == "
            "archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()); "
            "assert not any(p.name.isdecimal() for p in Path('/box-store').iterdir())"
        )
        self.helper(
            "from pathlib import Path; import shutil; "
            '[shutil.rmtree(p) for p in Path("/box-store").glob(".tinyassets-box-restore-old.*")]'
        )

    def reset_fixture(self):
        """Reset disposable inputs between independent crash trials, never restore."""
        self.helper(
            "from pathlib import Path; import shutil, tarfile; "
            "from tinyassets.boxes.destroy import tree; "
            "root=Path('/data'); store=Path('/box-store'); "
            "[tree(store,int(p.name),erase=True) for p in store.iterdir() "
            "if p.name.isdecimal()]; "
            "[shutil.rmtree(p) if p.is_dir() else p.unlink() for p in root.iterdir()]; "
            "archive=tarfile.open('/backup/original.tar.gz'); "
            "members=archive.getmembers(); "
            "[(setattr(m,'name',m.name.removeprefix('_data/'))) for m in members "
            "if m.name.startswith('_data/')]; "
            "[(setattr(m,'linkname',m.linkname.removeprefix('_data/'))) for m in members "
            "if m.islnk()]; "
            "archive.extractall(root,members=[m for m in members if m.name!='_data'], "
            "filter='fully_trusted'); archive.close()")

    def close(self):
        interrupted = []

        def defer(*_):
            interrupted.append(True)

        handlers = {sig: signal.signal(sig, defer) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            self._cleanup()
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        if interrupted:
            raise KeyboardInterrupt

    def _cleanup(self):
        failures = 0
        for name in reversed(self.containers):
            result = docker("rm", "-f", name, check=False)
            failures += bool(result.returncode and "No such container" not in result.stderr)
        if self.volumes:
            result = docker(
                "run",
                "--rm",
                "--privileged",
                "--pid=host",
                "--user",
                "0",
                "--network",
                "none",
                "--entrypoint",
                "python",
                self.image,
                "-c",
                "import json, re, subprocess, sys\n" +
                inspect.getsource(cleanup_mounts) +
                "\ntry:\n    cleanup_mounts(sys.argv[1])\n"
                "except RuntimeError as exc:\n    print(str(exc))\n    sys.exit(1)\n",
                self.prefix,
                check=False,
            )
            if result.returncode:
                detail = (result.stdout or "").strip()
                raise RuntimeError("cleanup residue assertion failed; volumes retained" +
                                   (": " + detail if detail.startswith("cleanup residue: ")
                                    else ""))
        if failures:
            raise RuntimeError("cleanup could not remove venue containers")
        for name in reversed(self.volumes):
            failures += bool(docker("volume", "rm", name, check=False).returncode)
        if failures:
            raise RuntimeError("cleanup failed")


def archive_rehearsal(image, archive, *, store_bytes):
    report, step = {}, "setup"
    venue = Venue(image, store_bytes)

    def passed(name, **counts):
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise RuntimeError("invalid report counts")
        report[name] = {"status": "PASS", **counts}
        print(name + " PASS" + "".join(f" {k}={v}" for k, v in counts.items()), flush=True)

    def required(result):
        if result.returncode:
            raise RuntimeError("step failed")

    try:
        venue.setup(archive)
        passed(step, mounts=1)
        step = "preflight"
        required(venue.migration(step))
        passed(step)
        step = "census"
        result = venue.migration(step)
        required(result)
        census = json.loads(result.stdout)
        if not census["ready"] or not census["owners"]:
            raise RuntimeError("census refused")
        passed(
            step,
            owners=census["owners"],
            centers=census["admitted_centers"],
            rows=sum(table["total"] for table in census["tables"].values()),
        )
        step = "apply"
        required(venue.migration(step))
        passed(step)
        step = "verify"
        required(venue.migration(step))
        baseline = venue.manifests()
        passed(step, manifests=len(baseline[1]) + 1)
        step = "restore_before_write"
        venue.restore()
        passed(step, comparisons=1)
        step = "crash_resume"
        venue.reset_fixture()
        required(venue.migration("preflight"))
        required(venue.migration("census"))
        plan = json.loads(
            venue.helper(
                "import json; from pathlib import Path; "
                "from tinyassets.box_layout_migration import _owner_plan; "
                'print(json.dumps([o.uid for o in _owner_plan(Path("/data"))]))'
            ).stdout
        )
        if len(plan) < 2:
            raise RuntimeError("two owners required for tree interruption")
        for boundary in ("after_owner_tree:1", "after_seeding"):
            result = venue.migration("apply", interrupt=boundary)
            if result.returncode != 137:
                raise RuntimeError(
                    f"interruption did not fire boundary={boundary} rc={result.returncode}"
                )
            required(venue.migration("apply"))
            required(venue.migration("verify"))
            if venue.manifests() != baseline:
                raise RuntimeError("resumed manifests differ")
            if boundary == "after_owner_tree:1":
                venue.restore()
                venue.reset_fixture()
                required(venue.migration("preflight"))
                required(venue.migration("census"))
        passed(step, interruptions=2, comparisons=2)
        step = "host_startup"
        required(venue.helper("""
import os
from pathlib import Path
from tinyassets.erasure_ledger import ErasureLedger, principal_digest
from tinyassets.restore_recovery import _schedule_trees
ledger = ErasureLedger(Path('/data'))
ledger.execute('add', {'digest': principal_digest('rehearsal-erased'),
                      'principal': 'rehearsal-erased', 'uids': [399997]})
tree = Path('/box-store/399997')
tree.mkdir(mode=0o700)
os.chown(tree, 399997, 399997)
(tree / 'note').write_text('synthetic erased bytes')
ledger.execute('add', {'digest': principal_digest('rehearsal-failed'),
                      'principal': 'rehearsal-failed', 'uids': [399998]})
# Wrong UID must fail destruction, retain the fence, and not prevent listeners.
Path('/box-store/399998').mkdir(mode=0o700)
_schedule_trees(ledger, [Path('/box-store')])
assert all(not Path(work['store']).is_absolute() and not Path(work['quarantine']).is_absolute()
           for entry in ledger.entries() for work in entry['work'].values())
"""))
        result = venue.container(
            "--rm", "--privileged", "--user", "0",
            "-v", venue.names["data"] + ":/data",
            "--mount", "type=bind,src=" + venue.store + ",dst=/var/lib/boxhostd/owners",
            "-v", venue.names["support"] + ":/support:ro",
            "-v", venue.names["harness"] + ":/harness:ro",
            "--entrypoint", "python", image, "-B", "/support/rehearsal_serve.py", "host-startup",
            check=False)
        required(result)
        startup = json.loads(result.stdout)
        if startup != {"owners": census["owners"], "erased": 1, "failed": 1, "mount_checks": 4}:
            raise RuntimeError("incomplete host startup proof")
        passed(step, **startup)
        step = "serving"
        result = venue.container(
            "--rm",
            "--privileged",
            "--cgroupns=host",
            "--user",
            "0",
            "-v",
            venue.names["data"] + ":/data",
            "--mount",
            "type=bind,src=" + venue.store + ",dst=/box-store",
            "-v",
            venue.names["support"] + ":/support:ro",
            # Match production's host filesystem: runsc prepares mountpoints;
            # the OCI root remains readonly to the owner inside the sandbox.
            "-v", venue.names["harness"] + ":/harness",
            *(["-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw", "-e", "TA_REHEARSAL_RUNSC=1"]
              if os.environ.get("TA_REHEARSAL_RUNSC") == "1" else []),
            "--entrypoint",
            "python",
            image,
            "-B",
            "/support/rehearsal_serve.py",
            check=False,
        )
        required(result)
        counts = json.loads(result.stdout)
        expected = {
            "owners",
            "centers",
            "history",
            "history_turns",
            "pending",
            "pending_saved",
            "controls",
            "plans",
            "writes",
            "reconnect",
            "production_launch",
        }
        if (
            set(counts) != expected
            or counts["owners"] != census["owners"]
            or counts["production_launch"] != 1
            or counts["writes"] < 1
            or counts["reconnect"] < 1
        ):
            raise RuntimeError("incomplete serving proof")
        passed(step, **counts)
        for phase in ("snapshot", "write"):
            venue.helper("import runpy; runpy.run_path('/support/rehearse_box_move.py')"
                         "['restore_replay_probe'](" + repr(phase) + ")")
        step = "normal_restore_after_traffic_refuses"
        venue.restore(replay=True, refuse=True)
        passed(step, refusals=1)
        step = "disaster_provider_unreachable_refuses"
        venue.helper("import runpy; runpy.run_path('/support/rehearse_box_move.py')"
                     "['provider_failure_probe']('before')")
        venue.restore(replay=True, disaster=True, refuse=True)
        venue.helper("import runpy; runpy.run_path('/support/rehearse_box_move.py')"
                     "['provider_failure_probe']('after')")
        passed(step, refusals=1)
        step = "disaster_restore_after_traffic"
        venue.restore(replay=True, disaster=True)
        replayed = json.loads(venue.helper(
            "import runpy; print(runpy.run_path('/support/rehearse_box_move.py')"
            "['restore_replay_probe']('verify'))").stdout)
        if replayed != census["owners"] or replayed < 1:
            raise RuntimeError("incomplete replay proof")
        passed(step, comparisons=1, replay_refusals=3 * replayed + 1, fenced_owners=replayed,
               stale_credentials=3, erased_owners=1, reconciled_owners=replayed)
    except BaseException:
        report[step] = {"status": "FAIL"}
        print(step + " FAIL", flush=True)
        raise
    finally:
        try:
            venue.close()
        except BaseException:
            report["cleanup"] = {"status": "FAIL"}
            print("cleanup FAIL", flush=True)
            raise
        passed("cleanup")
    return report


def main():
    from scripts.rehearse_box_move import validate_archive

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()

    def terminate(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        size = validate_archive(args.archive)
        archive_rehearsal(
            args.image, args.archive, store_bytes=max(1024**3, int(size * 1.25) + 512 * 1024**2)
        )
    except ImageNotFound as exc:
        print(str(exc), flush=True)
        raise SystemExit(1) from None
    except (ValueError, RuntimeError, OSError, KeyboardInterrupt):
        print("rehearsal FAIL", flush=True)
        raise SystemExit(1) from None
