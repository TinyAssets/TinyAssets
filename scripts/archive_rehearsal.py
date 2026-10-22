"""Disposable XFS venue for the deploy transaction; never logs private output."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-10-17T00-00-00Z"
DATA_ARCHIVE = f"tinyassets-snapshot-{STAMP}.tar.gz"
BOX_ARCHIVE = f"tinyassets-box-store-{STAMP}.tar.gz"


def committed_key(image, archive):
    """A changed archive, migration implementation or image requires full apply."""
    digest = hashlib.sha256(b"owner-box-committed-v1\0")
    for path in sorted({ROOT / "deploy/deploy_fail_safe.sh",
                        *ROOT.glob("tinyassets/box_layout*.py"),
                        *ROOT.glob("tinyassets/box_migration*.py"),
                        ROOT / "tinyassets/cutover_control.py"}):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    with Path(archive).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    digest.update(docker("image", "inspect", "-f", "{{.Id}}", image).stdout.strip().encode())
    return digest.hexdigest()


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


def compare_restored_archive(root, archive_path):
    """Compare file bytes and SQLite schema/rows; repaired indexes change bytes."""
    import os
    import sqlite3
    import tarfile
    from collections import Counter
    from contextlib import closing

    from tinyassets.sqlite_integrity import check
    from tinyassets.sqlite_readonly import readonly_db

    def contents(db):
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                            "ORDER BY type,name").fetchall()
        rows = {}
        for kind, name, _, _ in schema:
            if kind == "table":
                quoted = '"' + name.replace('"', '""') + '"'
                rows[name] = Counter(db.execute(f"SELECT * FROM {quoted} NOT INDEXED"))
        return schema, rows

    with tempfile.TemporaryDirectory(prefix="ta-restore-compare-") as temporary:
        with tarfile.open(archive_path) as archive:
            # Owner content holds absolute symlinks (runtime temp links): the
            # "tar" filter keeps them; links are compared, never followed.
            archive.extractall(temporary, filter="tar")
        original = Path(temporary) / "_data"
        databases = set()
        for path in original.rglob("*"):
            if not path.is_symlink() and path.is_file():
                with path.open("rb") as stream:
                    if stream.read(16) == b"SQLite format 3\x00":
                        databases.add(path)
        for path in original.rglob("*"):
            restored = root / path.relative_to(original)
            if path.is_symlink():
                assert restored.is_symlink(), path.name
                assert os.readlink(restored) == os.readlink(path), path.name
                continue
            if not path.is_file():
                continue
            if path in databases:
                with readonly_db(path) as before, closing(sqlite3.connect(
                        restored.as_uri() + "?mode=rw", uri=True)) as after:
                    check(before, repair=True)
                    check(after)  # The published file must already be repaired.
                    assert contents(before) == contents(after), path.name
            elif not any(path == db.with_name(db.name + suffix) for db in databases
                         for suffix in ("-wal", "-shm")):
                assert restored.read_bytes() == path.read_bytes(), path.name


class Venue:
    def __init__(self, image, store_bytes):
        self.image, self.store_bytes = image, store_bytes
        self.prefix = "ta-s6-" + uuid.uuid4().hex[:12]
        self.names = {
            k: self.prefix + "-" + k
            for k in ("image", "store", "parent", "data", "backup", "support", "harness",
                      "control", "runtime")
        }
        self.volumes, self.containers = [], []
        self.device = ""
        self.mounted = False

    def container(self, *argv, check=True):
        name = self.prefix + "-run-" + str(len(self.containers))
        self.containers.append(name)
        return docker("run", "--name", name, "--network", "none", *argv, check=check)

    def setup(self, archive, *, committed=None):
        if docker("image", "inspect", self.image, check=False).returncode:
            raise ImageNotFound(f"image not found: {self.image}")
        for key in ("image", "store", "parent", "backup", "support", "harness",
                      "control", "runtime"):
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
        if committed is None:
            docker("cp", str(archive), name + ":/backup/input.tar.gz")
        docker("cp", str(ROOT / "deploy"), name + ":/support/deploy")
        docker(
            "cp", str(ROOT / "scripts/rehearsal_serve.py"), name + ":/support/rehearsal_serve.py"
        )
        docker("cp", str(ROOT / "scripts/rehearse_box_move.py"),
               name + ":/support/rehearse_box_move.py")
        docker("cp", str(ROOT / "scripts/rehearsal_launch.py"),
               name + ":/support/rehearsal_launch.py")
        # Stream the candidate filesystem through production extraction. Avoid
        # writing and uploading a multi-GB intermediate tar on every retry.
        docker("cp", os.environ["TA_REHEARSAL_RUNSC_DIR"], name + ":/support/gvisor")
        image_id = docker("image", "inspect", "-f", "{{.Id}}", self.image).stdout.strip()
        extraction = self.prefix + "-extract"
        self.containers.append(extraction)
        exporter = subprocess.Popen(["docker", "export", name], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
        try:
            result = subprocess.run(
                ["docker", "run", "--rm", "-i", "--name", extraction, "--network", "none",
                 "--user", "0", "-v", self.names["support"] + ":/support:ro",
                 "-v", self.names["harness"] + ":/harness", "--entrypoint", "python",
                 self.image, "-B", "/support/rehearsal_launch.py", "extract", image_id],
                stdin=exporter.stdout, capture_output=True)
            exporter.stdout.close()
            _, error = exporter.communicate(timeout=30)
            if result.returncode or exporter.returncode:
                raise RuntimeError("production extraction failed: " +
                                   (result.stderr + error).decode(errors="replace"))
        finally:
            if exporter.poll() is None:
                exporter.kill()
                exporter.communicate()
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
        if committed is not None:
            self.committed_state(committed, restore=True, initial=True)
            self.mounted = True
            return
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

    def initialize_cutover(self):
        self.helper("from tinyassets.cutover_control import initialize; "
                    "initialize(Path('/deployment/cutover'), "
                    "backup=Path('/backup/" + DATA_ARCHIVE + "'), old_image='synthetic-old-image')")

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
            "--privileged",
            "-e",
            "BACKUP_DEST=:local:/backup/owner-snapshots",
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
            "-v", self.names["control"] + ":/deployment",
            "--entrypoint",
            "python",
            self.image,
            "-B",
            "-c",
            code,
            *args,
        )

    def committed_state(self, path, *, restore=False, initial=False):
        """Copy a quiescent XFS filesystem, preserving project IDs and custody.

        Export happens before any service starts; imports always go into this
        run's disposable volumes, never mutate the cached committed generation.
        """
        if not initial:
            self.container("--rm", "--privileged", "--pid=host", "--user", "0",
                           "--entrypoint", "sh", self.image, "-ec",
                           'nsenter -t 1 -m umount "$1"', "sh", self.store)
        name = self.prefix + "-cache-" + uuid.uuid4().hex[:8]
        self.containers.append(name)
        docker("create", "--name", name, "--user", "0",
               "-v", self.names["image"] + ":/cache/image",
               "-v", self.names["parent"] + ":/cache/parent",
               "-v", self.names["control"] + ":/cache/control",
               "-v", self.names["backup"] + ":/cache/backup",
               "--entrypoint", "sh", self.image, "-ec",
               ("find /cache/parent /cache/control /cache/backup -mindepth 1 -delete; "
                "tar -xzf /state.tar.gz -C /cache --numeric-owner --acls --xattrs"
                if restore else
                "tar -czSf /state.tar.gz -C /cache --numeric-owner --acls --xattrs "
                "image parent control backup"))
        try:
            if restore:
                docker("cp", str(path), name + ":/state.tar.gz")
            docker("start", "-a", name)
            status = docker("inspect", "-f", "{{.State.ExitCode}}", name).stdout.strip()
            if status != "0":
                raise RuntimeError("committed-state copy failed")
            if not restore:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
                docker("cp", name + ":/state.tar.gz", str(temporary))
                temporary.replace(path)
        finally:
            if initial:
                self.container("--rm", "--privileged", "--pid=host", "--user", "0",
                               "--entrypoint", "sh", self.image, "-ec",
                               'nsenter -t 1 -m mount --bind "$1" "$1"; '
                               'nsenter -t 1 -m mount --make-private "$1"', "sh", self.store)
            self.container("--rm", "--privileged", "--pid=host", "--user", "0",
                           "--entrypoint", "sh", self.image, "-ec",
                           'nsenter -t 1 -m mount -o loop,prjquota "$1" "$2"',
                           "sh", self.loop_image, self.store)
            self.device = self.container("--rm", "-v", self.names["store"] + ":/box-store",
                                         "--entrypoint", "findmnt", self.image,
                                         "--mountpoint", "/box-store",
                                         "-no", "SOURCE").stdout.strip()

    def compose(self, *args, check=True):
        return docker("compose", "--project-name", self.prefix,
                      "--env-file", str(self.compose_dir / "env"),
                      "-f", str(ROOT / "deploy/compose.yml"),
                      "-f", str(self.compose_dir / "paths.yml"), *args, check=check)

    def start_stack(self):
        # The synthetic snapshot remote has the same platform namespace as production.
        self.container("--rm", "--user", "0", "-v", self.names["backup"] + ":/backup",
                       "--entrypoint", "sh", self.image, "-ec",
                       "mkdir -p /backup/owner-snapshots/platform; "
                       "chown 1002:1002 /backup/owner-snapshots/platform")
        # Caller-owned canary credential; production's healthcheck is unchanged.
        self.container("--rm", "--user", "1001:1001", "-v", self.names["data"] + ":/data",
                       "--entrypoint", "python", self.image, "-c",
                       "import json; from pathlib import Path; "
                       "p=Path('/data/.router-credentials.json'); "
                       "d=json.loads(p.read_text()) if p.exists() else {}; "
                       "d['TINYASSETS_WIKI_CANARY_TOKEN']='synthetic-rehearsal-canary-credential'; "
                       "p.write_text(json.dumps(d)); p.chmod(0o600); "
                       "Path('/data/platform-expected-instance.json').write_text(json.dumps({"
                       "'schema':'platform_expected_instance','version':1,"
                       "'expected_instance_id':'9462001'})); "
                       "Path('/data/release-state.json').write_text(json.dumps({"
                       "'git_sha':Path('/etc/tinyassets-build-sha').read_text().strip(),"
                       "'image_tag':'synthetic-rehearsal','deployed_at':'2026-10-17T00:00:00Z'}))")
        self.compose_dir = Path(tempfile.mkdtemp(prefix="ta-compose-"))
        empty = self.compose_dir / "empty.env"
        empty.write_text("")
        daemon_env = self.compose_dir / "daemon.env"
        daemon_env.write_text("UNIVERSE_SERVER_AUTH=oauth\n"
                              "UNIVERSE_SERVER_URL=https://rehearsal.invalid/mcp\n"
                              "TINYASSETS_WIKI_CANARY_TOKEN=synthetic-rehearsal-canary-credential\n")
        broker_env = self.compose_dir / "broker.env"
        broker_env.write_text("TINYASSETS_BOX_AWAKE_CAP=2\n"
                              "BACKUP_DEST=:local:/var/lib/ta-broker/owner-snapshots\n")
        def mount(key):
            return docker("volume", "inspect", "-f", "{{.Mountpoint}}",
                          self.names[key]).stdout.strip()
        # The only compose inputs are caller names, paths, env files and image.
        values = dict(TINYASSETS_IMAGE=self.image,
                      TINYASSETS_DAEMON_NAME=self.prefix + "-daemon",
                      TINYASSETS_BROKER_NAME=self.prefix + "-broker",
                      TINYASSETS_DATA_VOLUME=self.names["data"],
                      TINYASSETS_BROWSER_KEYS_VOLUME=self.names["backup"],
                      TINYASSETS_NETWORK_NAME=self.prefix + "-net",
                      TINYASSETS_BOXHOST_RUN=mount("runtime"),
                      TINYASSETS_BROKER_RCLONE=mount("support") + "/broker-rclone.conf",
                      TINYASSETS_DAEMON_ENV=str(daemon_env),
                      TINYASSETS_BROKER_ENV=str(broker_env),
                      TINYASSETS_INTERCHANGE_ENV=str(empty),
                      TINYASSETS_IDEMPOTENCY_ENV=str(empty), TINYASSETS_INGRESS_ENV=str(empty))
        # Replace only env-file paths; no service command or authority override.
        (self.compose_dir / "paths.yml").write_text(
            "services:\n  daemon:\n    env_file: !override\n"
            "      - ${TINYASSETS_DAEMON_ENV}\n"
            "      - ${TINYASSETS_INTERCHANGE_ENV}\n"
            "      - ${TINYASSETS_IDEMPOTENCY_ENV}\n"
            "      - ${TINYASSETS_INGRESS_ENV}\n"
            "  broker:\n    env_file: !override\n"
            "      - path: ${TINYASSETS_BROKER_ENV}\n        required: false\n")
        (self.compose_dir / "env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        self.container("--rm", "--user", "0", "-v", self.names["support"] + ":/support",
                       "--entrypoint", "touch", self.image, "/support/broker-rclone.conf")
        result = self.container(
            "-d", "--privileged", "--user", "0", "--cgroupns=host", "--pid=host",
            "-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw",
            "-v", self.names["data"] + ":/data",
            "--mount", "type=bind,src=" + self.store + ",dst=/var/lib/boxhostd/owners",
            "-v", self.names["support"] + ":/support:ro",
            "-v", self.names["runtime"] + ":/run/tinyassets-boxhostd",
            "-v", self.names["harness"] + ":/harness:ro",
            "-v", self.names["harness"] + ":/opt/tinyassets/boxhostd",
            "-v", self.names["backup"] + ":/snapshots",
            "--entrypoint", "python", self.image, "-B", "/support/rehearsal_launch.py", "host")
        self.host_container = result.stdout.strip()
        deadline = time.monotonic() + 45
        while docker("exec", self.host_container, "test", "-f",
                     "/run/tinyassets-boxhostd/rehearsal-ready", check=False).returncode:
            if time.monotonic() >= deadline:
                raise RuntimeError("production host startup failed: " + docker(
                    "logs", self.host_container, check=False).stdout)
            time.sleep(.2)
        self.compose("up", "-d", "daemon", "broker")
        self.start_metadata()
        deadline = time.monotonic() + 120
        while True:
            broker_logs = docker("logs", self.prefix + "-broker", check=False)
            if "broker role identity is not retired" in broker_logs.stderr:
                raise RuntimeError("broker role identity is not retired")
            state = json.loads(docker("inspect", self.prefix + "-daemon").stdout)[0]["State"]
            if state.get("Health", {}).get("Status") == "healthy":
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("compose stack not healthy: " + self.compose(
                    "logs", "--no-color", check=False).stdout)
            time.sleep(1)

    def start_metadata(self):
        """Synthetic external metadata, scoped to this daemon's bridge address.

        No resolver patch or cloud-admission bypass is installed in the image.
        Only this disposable source IP's literal metadata request is redirected.
        """
        self.metadata = self.prefix + "-metadata"
        self.containers.append(self.metadata)
        code = """
from http.server import BaseHTTPRequestHandler, HTTPServer
class Metadata(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/metadata/v1/id':
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'9462001')
    def log_message(self, *args):
        pass
HTTPServer(('0.0.0.0', 8000), Metadata).serve_forever()
"""
        docker("run", "-d", "--name", self.metadata, "--network", self.prefix + "-net",
               "--user", "1001:1001", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges=true",
               "--entrypoint", "python", self.image, "-c", code)

        def address(name):
            details = json.loads(docker("inspect", name).stdout)[0]
            return details["NetworkSettings"]["Networks"][self.prefix + "-net"]["IPAddress"]

        daemon_ip, metadata_ip = address(self.prefix + "-daemon"), address(self.metadata)
        import ipaddress
        ipaddress.IPv4Address(daemon_ip)
        ipaddress.IPv4Address(metadata_ip)
        rule = ["PREROUTING", "-s", daemon_ip + "/32", "-d", "169.254.169.254/32",
                "-p", "tcp", "--dport", "80", "-j", "DNAT",
                "--to-destination", metadata_ip + ":8000"]
        docker("exec", self.host_container, "nsenter", "-t", "1", "-m", "-n",
               "iptables", "-w", "-t", "nat", "-I", *rule)
        self.metadata_rule = rule

    def owner_operations(self, row, action, operations=None):
        from scripts.rehearsal_serve import owner_operations

        source = ("import asyncio,json,sys\n" + inspect.getsource(owner_operations) +
                  "\nprint(json.dumps(asyncio.run(owner_operations(*json.loads(sys.argv[1])))))")
        uid = row["uid"]
        result = docker("exec", self.host_container, "/support/gvisor/runsc",
                        "--root", "/var/lib/boxhostd/state/runsc", "exec", "--user", f"{uid}:{uid}",
                        f"owner-{uid}", "/opt/venv/bin/python", "-B", "-c", source,
                        json.dumps([action, row["centers"][0], operations, row["principal"]]))
        return json.loads(result.stdout)

    def host_call(self, code):
        return docker("exec", self.host_container, "python", "-B", "-c", code)

    def sleep(self, owner):
        return json.loads(self.host_call(
            "import json,sys; sys.path.insert(0,'/support'); "
            "from rehearsal_launch import published_sleep; "
            f"print(json.dumps(published_sleep({owner!r})))").stdout)

    def stop_stack(self):
        if getattr(self, "metadata_rule", None):
            self.container("--rm", "--privileged", "--user", "0", "--pid=host",
                           "--entrypoint", "nsenter", self.image, "-t", "1", "-m", "-n",
                           "iptables", "-w", "-t", "nat", "-D", *self.metadata_rule)
            self.metadata_rule = None
        if getattr(self, "metadata", None):
            result = docker("rm", "-f", self.metadata, check=False)
            if result.returncode and "No such container" not in result.stderr:
                raise RuntimeError("metadata fixture cleanup failed")
            self.metadata = None
        if hasattr(self, "compose_dir"):
            if (self.compose_dir / "paths.yml").exists() and (self.compose_dir / "env").exists():
                self.compose("down", "--timeout", "30")
            shutil.rmtree(self.compose_dir)
            del self.compose_dir
        if getattr(self, "host_container", None):
            docker("stop", "--time", "200", self.host_container)
            self.host_container = None

    def manifests(self):
        return json.loads(self.helper(
            "import json, runpy; print(json.dumps(runpy.run_path('/support/rehearse_box_move.py')"
            "['rehearsal_manifests'](), sort_keys=True))").stdout)

    def restore(self, *, refuse=False, refusal_reason=None):
        # Only the old-layout one-shot exception. Owner image objects are not
        # attached, read or restored by this deployment operation.
        code = r"""
import json, os, shutil, subprocess, tempfile
from pathlib import Path
from tinyassets.cutover_control import rollback_live
with tempfile.TemporaryDirectory() as temporary:
    binaries = Path(temporary)
    (binaries / 'systemctl').write_text('#!/bin/sh\nexit 0\n')
    (binaries / 'docker').write_text('#!/bin/sh\n'
        '[ "$*" = "ps -q --filter volume=tinyassets-data" ]\n')
    (binaries / 'old-deploy').write_text('#!/bin/sh\n'
        '[ "$*" = "--restore-bundle synthetic-old-image" ] || exit 90\n'
        'test -f /parent/_data/.layout.json\n')
    for path in binaries.iterdir():
        path.chmod(0o700)
    os.environ['PATH'] = str(binaries) + os.pathsep + os.environ['PATH']
    rollback_live(Path('/deployment/cutover'), Path('/parent/_data'),
                  deploy_script=binaries / 'old-deploy')
"""
        result = self.container(
            "--rm", "--user", "0",
            "-v", self.names["parent"] + ":/parent",
            "-v", self.names["backup"] + ":/backup:ro",
            "-v", self.names["control"] + ":/deployment",
            "--entrypoint", "python", self.image, "-B", "-c", code, check=False)
        if refuse:
            if result.returncode == 0 or (refusal_reason and refusal_reason not in result.stderr):
                raise RuntimeError("cutover rollback did not refuse for the required reason")
            return
        if result.returncode:
            raise RuntimeError("cutover rollback failed: " + result.stderr)
        self.helper(
            "import tempfile\n" + inspect.getsource(compare_restored_archive) +
            "\ncompare_restored_archive(Path('/data'), Path('/backup/original.tar.gz'))\n")

    def reset_fixture(self):
        """Reset disposable inputs between independent crash trials, never restore."""
        self.helper(
            "from pathlib import Path; import shutil, tarfile; "
            "from tinyassets.box_layout_migration import _volumes; "
            "root=Path('/data'); store=Path('/box-store'); "
            "[_volumes(store).delete(int(p.stem)) for p in store.glob('*.img')]; "
            "[shutil.rmtree(p) for p in store.iterdir() "
            "if p.name in {'.locks', '.cutover-shared'}]; "
            "[shutil.rmtree(p) if p.is_dir() else p.unlink() for p in root.iterdir()]; "
            "archive=tarfile.open('/backup/original.tar.gz'); "
            "members=archive.getmembers(); "
            "[(setattr(m,'name',m.name.removeprefix('_data/'))) for m in members "
            "if m.name.startswith('_data/')]; "
            "[(setattr(m,'linkname',m.linkname.removeprefix('_data/'))) for m in members "
            "if m.islnk()]; "
            # Extract the root entry too: rollback hardened it for the ledger.
            # An independent trial needs the archived uid/gid/mode, not 0:1001/1775.
            "[(setattr(m,'name','.')) for m in members if m.name.rstrip('/')=='_data']; "
            "archive.extractall(root,members=members, "
            "filter='fully_trusted'); archive.close(); "
            "shutil.rmtree('/deployment/cutover', ignore_errors=True)")

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
        self.stop_stack()
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


def archive_rehearsal(image, archive, *, store_bytes, from_committed=False, cache_dir=None):
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
        cache = (Path(cache_dir) / committed_key(image, archive)) if cache_dir else None
        cached = bool(from_committed and cache and (cache / "state.tar.gz").is_file()
                      and (cache / "report.json").is_file())
        if cached:
            venue.setup(archive, committed=cache / "state.tar.gz")
            previous = json.loads((cache / "report.json").read_text())
            # Cache hits report only work actually run in this invocation.
            census = previous["census"]
            passed("committed", owners=census["owners"])
        else:
            venue.setup(archive)
            venue.initialize_cutover()
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
            if cache:
                # Save the verified committed generation as soon as it exists:
                # every later retest resumes at host_startup without waiting
                # for the restore and crash-resume proofs below.
                venue.committed_state(cache / "state.tar.gz")
                (cache / "report.json").write_text(json.dumps(report))
            step = "restore_before_write"
            venue.restore()
            passed(step, comparisons=1)
            step = "crash_resume"
            venue.reset_fixture()
            venue.initialize_cutover()
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
                    venue.initialize_cutover()
                    required(venue.migration("preflight"))
                    required(venue.migration("census"))
            passed(step, interruptions=2, comparisons=2)
        step = "host_startup"
        venue.start_stack()
        passed(step, services=2)
        step = "serving"
        venue.helper("from tinyassets.cutover_control import admit; "
                     "admit(Path('/deployment/cutover'), Path('/data'))")
        daemon = venue.prefix + "-daemon"
        docker("cp", str(ROOT / "scripts/rehearsal_serve.py"), daemon + ":/tmp/rehearsal_serve.py")
        plan = venue.host_call("import json; from pathlib import Path; "
                               "print(json.dumps(json.loads(Path('/data/.owner-cutover.json')"
                               ".read_text())['plan']))").stdout
        seeded = docker("exec", daemon, "python", "/tmp/rehearsal_serve.py", "seed", plan).stdout
        result = docker("exec", daemon, "python", "/tmp/rehearsal_serve.py", "smoke", seeded,
                        check=False)
        if result.returncode:
            logs = venue.compose("logs", "--no-color", "--tail", "100", check=False)
            raise RuntimeError("serving failed: " + result.stderr + logs.stdout + logs.stderr)
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
        venue.host_call("from pathlib import Path; "
                        "ready=Path('/run/tinyassets-boxhostd/rehearsal-ready'); "
                        "parent=Path(ready.read_text()); "
                        "owners=list(parent.glob('owner-*')); assert owners; "
                        "assert all(int((p/'memory.max').read_text())>0 and "
                        "int((p/'pids.max').read_text())>0 and "
                        "int((p/'cpu.weight').read_text())>0 for p in owners)")
        passed(step, **counts)
        step = "single_owner_restore"
        inputs = json.loads(seeded)
        row = inputs["plan"][0]
        token = inputs["credentials"][row["principal"]]

        def key_call(operation, key_id=""):
            return json.loads(docker("exec", daemon, "python", "/tmp/rehearsal_serve.py", "key",
                                     token, row["centers"][0], operation, key_id).stdout)

        key = key_call("create")
        operations = venue.owner_operations(row, "prepare")
        snapshot = venue.sleep(row["principal"])
        # HTTP admission, not a separate owner launcher, wakes the sleeping box.
        token = inputs["credentials"][row["principal"]]
        wake = ("import httpx; r=httpx.post('http://127.0.0.1:8001/app/api/status', "
                f"headers={{'Authorization':'Bearer {token}', 'Host':'rehearsal.invalid'}}, "
                f"json={{'universe_id':{row['centers'][0]!r}}}, timeout=120); "
                "assert r.status_code==200, r.text")
        docker("exec", daemon, "python", "-c", wake)
        venue.owner_operations(row, "mutate", operations)
        key_call("revoke", key["key_id"])
        venue.host_call("from tinyassets.broker.lifecycle_control import request; "
                        f"request('restore', owner={row['principal']!r}, snapshot_id={snapshot!r})")
        docker("exec", daemon, "python", "-c", wake)
        restored = venue.owner_operations(row, "verify", operations)
        listing = key_call("list")
        assert next(k for k in listing["keys"] if k["key_id"] == key["key_id"])["revoked_at"]
        passed(step, **restored, revoked_keys=1)
        for item in inputs["plan"]:
            venue.sleep(item["principal"])
        venue.stop_stack()
        step = 'normal_restore_after_traffic_refuses'
        venue.restore(refuse=True, refusal_reason='consumed')
        passed(step, refusals=1)
        step = 'platform_recovery'
        result = json.loads(venue.helper(
            "import json, runpy; print(json.dumps(runpy.run_path('/support/rehearse_box_move.py')"
            "['platform_recovery_probe']()))").stdout)
        if result != {'unchanged_volumes': census['owners'], 'stale_credentials': 3,
                      'admission_open': 1, 'startup_reconciled': 1}:
            raise RuntimeError('incomplete platform recovery startup proof')
        passed(step, **result)
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
    parser.add_argument("--from", dest="start", choices=("full", "committed"), default="full")
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    if args.start == "committed" and args.cache_dir is None:
        parser.error("--from committed requires --cache-dir")

    def terminate(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        size = validate_archive(args.archive)
        archive_rehearsal(
            args.image, args.archive, store_bytes=max(1024**3, int(size * 1.25) + 512 * 1024**2),
            from_committed=args.start == "committed", cache_dir=args.cache_dir
        )
    except ImageNotFound as exc:
        print(str(exc), flush=True)
        raise SystemExit(1) from None
    except (ValueError, RuntimeError, OSError, KeyboardInterrupt):
        print("rehearsal FAIL", flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT))
    main()
