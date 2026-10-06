"""DA3: capability-free center-root labelling, the fixed cell class and its refusals.

The labelling tests run as root in the Linux oracle: a real daemon (1001) and a
real owner cell (300001), each with zero capability sets, forked by a root
stand-in for the mapper. The production-image probe runs the same path through
the real mapper and bubblewrap cell.
"""
import array
import json
import os
import runpy
import socket
import stat
import traceback
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MACHINE = 300001
pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and ACLs")
needs_root = pytest.mark.skipif(os.name != "posix" or os.geteuid() != 0,
                                reason="owner=uid-admission runs-in=linux_oracle.py --as-root: "
                                    "real daemon and owner identities")


def caps():
    fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    return [int(fields[key], 16) for key in ("CapInh", "CapPrm", "CapEff", "CapAmb")]


def become(uid):
    os.setgroups([])
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    os.umask(0o007)
    assert caps() == [0, 0, 0, 0]


def handoff(variant):
    decoder = runpy.run_path(str(ROOT / "deploy/role_decoder.py"))

    def run(staging):
        if variant == "lost-setgid":  # a kernel or filter that drops S_ISGID
            os.mkdir("g", 0o777, dir_fd=staging)
            fd = os.open("g", os.O_RDONLY | os.O_DIRECTORY, dir_fd=staging)
            os.fchmod(fd, 0o777)
            info = os.fstat(fd)
            os.close(fd)
            return [info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]
        made = decoder["center_root_handoff"](staging)
        if variant == "foreign-entry":
            os.mkdir("planted", 0o700, dir_fd=staging)
        return made
    return run


def launcher(channel, variant):
    """Root stand-in for the mapper: one owner cell per request, never the daemon."""
    while True:
        packet, ancillary, _, _ = channel.recvmsg(4096, socket.CMSG_SPACE(8))
        if not packet:
            os._exit(0)
        fds = array.array("i")
        for _, _, payload in ancillary:
            fds.frombytes(payload[:len(payload) - len(payload) % 4])
        staging, stream = fds
        info = os.fstat(staging)
        cell = os.fork()
        if cell == 0:
            try:
                become(MACHINE)
                inner = MACHINE - 300000
                proof = dict(uid=inner, gid=inner, source=[info.st_dev, info.st_ino],
                             fds=[0, 1, 2], groups=os.getgroups(),
                             caps="zero" if caps() == [0, 0, 0, 0] else "held", nnp=1,
                             profile="cell-deny")
                os.write(stream, json.dumps({"cell": proof}).encode() + b"\n")
                made = handoff(variant)(staging)
                # The real cell reports g in its own namespace view (inner ids).
                made = [made[0] - 300000, made[1] - 300000, made[2]]
                os.write(stream, json.dumps({"g": made}).encode() + b"\n")
                os._exit(0)
            except BaseException:
                traceback.print_exc()
                os._exit(1)
        os.close(staging)
        os.close(stream)
        _, status = os.waitpid(cell, 0)
        channel.sendall(str(os.waitstatus_to_exitcode(status)).encode())


class Cell:
    def __init__(self, stream, channel):
        self.stream, self._channel = stream, channel

    def wait(self, timeout):
        self._channel.settimeout(timeout)
        return int(self._channel.recv(16))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stream.close()


class Client:
    def __init__(self, channel):
        self.channel = channel
        self.requests = []

    def start_cell(self, *, kind, principal, command_center, identity, directory_fd):
        self.requests.append((kind, principal, command_center, identity.uid))
        ours, theirs = socket.socketpair()
        with theirs:
            self.channel.sendmsg([b"start"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                               array.array("i", [directory_fd, theirs.fileno()]))])
        return Cell(ours, self.channel)


def admit_as_daemon(tmp_path, variant="canonical", center="alice-home"):
    for parent in (tmp_path, *tmp_path.parents):  # let the daemon traverse to its root
        info = parent.stat()
        if info.st_uid == 0 and not info.st_mode & 0o001:
            parent.chmod(stat.S_IMODE(info.st_mode) | 0o011)
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    os.chown(data, 1001, 1001)
    data.chmod(0o755)
    daemon_end, launcher_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    helper = os.fork()
    if helper == 0:
        daemon_end.close()
        launcher(launcher_end, variant)
    launcher_end.close()
    reader, writer = os.pipe()
    daemon = os.fork()
    if daemon == 0:
        try:
            os.close(reader)
            become(1001)
            from tinyassets.broker.owner_identities import OwnerIdentity
            from tinyassets.role_center_admission import label_root

            client = Client(daemon_end)
            root = os.open(data, os.O_RDONLY | os.O_DIRECTORY)
            try:
                key = label_root(root, client=client, principal="alice", center=center,
                                 identity=OwnerIdentity(MACHINE, MACHINE))
                result = {"key": list(key), "requests": client.requests, "caps": caps()}
            except Exception as exc:  # noqa: BLE001 - reported to the parent
                result = {"refused": type(exc).__name__, "message": str(exc), "caps": caps()}
            os.write(writer, json.dumps(result).encode())
            os._exit(0)
        except BaseException:
            traceback.print_exc()
            os._exit(1)
    os.close(writer)
    raw = b""
    while chunk := os.read(reader, 65536):
        raw += chunk
    os.close(reader)
    assert os.waitpid(daemon, 0)[1] == 0
    daemon_end.close()
    assert os.waitpid(helper, 0)[1] == 0
    return data, json.loads(raw)


@needs_root
def test_handoff_publishes_the_canonical_migrated_label_with_zero_capabilities(tmp_path):
    from tinyassets.role_center_admission import canonical_label, read_label

    data, result = admit_as_daemon(tmp_path)
    assert "refused" not in result, result
    assert result["caps"] == [0, 0, 0, 0]
    assert result["requests"] == [["center-root", "alice", "alice-home", MACHINE]]
    fd = os.open(data / "alice-home", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        assert read_label(fd) == canonical_label(MACHINE)
        info = os.fstat(fd)
        assert [info.st_dev, info.st_ino] == result["key"]
        assert not info.st_mode & stat.S_ISGID
    finally:
        os.close(fd)
    fd = os.open(data / "alice-home" / "previews", os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert read_label(fd) == (1001, 1001, 0o700, None, None)  # explicit, not setgid
    finally:
        os.close(fd)
    assert os.listdir(data / ".role-admission") == []
    assert sorted(os.listdir(data)) == [".role-admission", "alice-home"]


@needs_root
@pytest.mark.parametrize("variant", ["lost-setgid", "foreign-entry"])
def test_a_wrong_handoff_refuses_and_publishes_nothing(tmp_path, variant):
    data, result = admit_as_daemon(tmp_path, variant)
    assert result["refused"] == "AdmissionRefused", result
    assert result["caps"] == [0, 0, 0, 0]
    assert not (data / "alice-home").exists()
    assert os.listdir(data / ".role-admission") == []


@needs_root
def test_an_existing_name_is_never_replaced(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "alice-home").mkdir()
    (data / "alice-home" / "keep").write_text("existing")
    os.chown(data / "alice-home", 1001, 1001)
    data, result = admit_as_daemon(tmp_path)
    assert result["refused"] == "FileExistsError", result
    assert (data / "alice-home" / "keep").read_text() == "existing"
    assert os.listdir(data / ".role-admission") == []


def test_canonical_acl_bytes_match_the_migration_encoding():
    from tinyassets.role_center_admission import canonical_root_acl

    # user::rwx user:M:r-x user:1002:--x group::--- mask::r-x other::---
    expected = bytes.fromhex(
        "02000000" "0100" "0700" "ffffffff" "0200" "0100" "ea030000"
        "0200" "0500" "e1930400" "0400" "0000" "ffffffff" "1000" "0500" "ffffffff"
        "2000" "0000" "ffffffff")
    assert canonical_root_acl(MACHINE) == expected


def mapper(tmp_path, bound=(), machine=MACHINE, state="unadmitted"):
    module = runpy.run_path(str(ROOT / "deploy/role_owner_launcher.py"))
    value = object.__new__(module["OwnerLauncher"])
    value.bindings = {(principal, center): 300002 for principal, center in bound}
    value.data_root = str(tmp_path)
    value.overflow_uid, value.overflow_gid = os.getuid(), os.getgid()
    value.jobs, value.delete_fences = {}, {}
    value.asked = []

    def owner_machine(principal):
        value.asked.append(("OWNER_MACHINE", principal))
        return machine

    def center_state(center):
        value.asked.append(("CENTER_STATE", center))
        return state

    value._owner_machine, value._center_state = owner_machine, center_state
    return value


def staging(tmp_path, token="a" * 32, mode=0o700):
    path = tmp_path / ".role-admission" / token
    path.mkdir(parents=True)
    path.chmod(mode)
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY)


REQUEST = dict(op="START", kind="center-root", principal="alice", command_center="alice-home")


def test_mapper_resolves_machine_from_the_broker_for_a_fresh_name(tmp_path):
    value = mapper(tmp_path)
    fd = staging(tmp_path)
    try:
        assert value._center_root_machine(REQUEST, fd) == MACHINE
    finally:
        os.close(fd)
    assert value.asked == [("OWNER_MACHINE", "alice"), ("CENTER_STATE", "alice-home")]


@pytest.mark.parametrize("changes", [
    dict(bound=[("bob", "alice-home")]), dict(machine=None), dict(state="admitted"),
    dict(state="retired")])
def test_mapper_refuses_a_bound_unreserved_or_logged_center(tmp_path, changes):
    value = mapper(tmp_path, **changes)
    fd = staging(tmp_path)
    try:
        with pytest.raises(ValueError):
            value._center_root_machine(REQUEST, fd)
    finally:
        os.close(fd)


@pytest.mark.parametrize("where", ["token", "mode", "outside"])
def test_mapper_refuses_staging_that_is_not_daemon_private(tmp_path, where):
    value = mapper(tmp_path)
    if where == "outside":
        (tmp_path / "elsewhere").mkdir()
        fd = os.open(tmp_path / "elsewhere", os.O_RDONLY | os.O_DIRECTORY)
    else:
        fd = staging(tmp_path, token="short" if where == "token" else "b" * 32,
                     mode=0o707 if where == "mode" else 0o700)
    try:
        with pytest.raises(ValueError, match="daemon-private"):
            value._center_root_machine(REQUEST, fd)
    finally:
        os.close(fd)


@pytest.mark.parametrize("changes", [
    {"op": "SPAWN"}, {"uid": MACHINE}, {"machine": MACHINE}, {"path": "/data/x"},
    {"argv": ["/bin/sh"]}])
def test_center_root_schema_refuses_numbers_paths_programs_and_blocking(tmp_path, changes):
    value = mapper(tmp_path)
    with pytest.raises(ValueError, match="unsupported"):
        value._decoder(REQUEST | changes, [0, 0, 0])
    assert value.asked == []
