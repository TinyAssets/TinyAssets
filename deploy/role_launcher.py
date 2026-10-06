"""Kernel boundary for the staged role launcher (stdlib only, -I -S -B).

The production CMD is deliberately unchanged until all owner-cell routes and
migration probes pass. There is no arbitrary exec, shell, path or environment
operation on this interface. Engine kinds remain refused until their complete
owner-cell implementations are installed.
"""
from __future__ import annotations

import array
import ctypes
import json
import os
import runpy
import signal
import socket
import stat
import struct
import sys
import time
from pathlib import Path

ENTRY_CAPS = sum(1 << cap for cap in (0, 1, 3, 5, 6, 7, 8))
SERVING_CAPS = sum(1 << cap for cap in (5, 6, 7, 8))
CAP_FIELDS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
ROLE_IDS = {"daemon": (1001, (1100, 1101, 1102)), "broker": (1002, (1102,))}
MAX_REQUEST = 4096


class Refused(RuntimeError):
    """A launch precondition failed; never fall back to ordinary subprocesses."""


class _Header(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _Data(ctypes.Structure):
    _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
                ("inheritable", ctypes.c_uint32)]


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def _checked(result, operation):
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, f"{operation}: {os.strerror(code)}")


def status():
    return dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())


def _assert_caps(mask):
    fields = status()
    expected = {"CapInh": 0, "CapAmb": 0, "CapEff": mask,
                "CapPrm": mask, "CapBnd": mask}
    if any(int(fields[key], 16) != value for key, value in expected.items()):
        raise Refused("unexpected capability sets")
    if int(fields["NoNewPrivs"]) != 1:
        raise Refused("no-new-privileges is absent")


def _capset(mask):
    data = (_Data * 2)()
    for index in range(2):
        data[index].effective = data[index].permitted = (mask >> (32 * index)) & 0xffffffff
    _checked(_libc().capset(ctypes.byref(_Header(0x20080522, 0)), data), "capset")


def retire_migration_authority():
    """Irreversibly drop migration authority before socket creation."""
    if os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
        raise Refused("launcher requires the root entry identity")
    _assert_caps(ENTRY_CAPS)
    libc = _libc()
    _checked(libc.prctl(8, 0, 0, 0, 0), "clear keepcaps")
    _checked(libc.prctl(47, 4, 0, 0, 0), "clear ambient")
    os.setgroups([])
    for cap in (0, 1, 3):
        _checked(libc.prctl(24, cap, 0, 0, 0), "drop migration bounding capability")
    _capset(SERVING_CAPS)
    _assert_caps(SERVING_CAPS)


def retire_child(role):
    """Called in a single-threaded fork child, before any application import."""
    uid, groups = ROLE_IDS[role]
    _retire_identity(uid, groups)


def _retire_identity(uid, groups):
    libc = _libc()
    _checked(libc.prctl(38, 1, 0, 0, 0), "no-new-privileges")
    _checked(libc.prctl(8, 0, 0, 0, 0), "clear keepcaps")
    _checked(libc.prctl(47, 4, 0, 0, 0), "clear ambient")
    for cap in range(int(Path("/proc/sys/kernel/cap_last_cap").read_text()) + 1):
        _checked(libc.prctl(24, cap, 0, 0, 0), "drop child bounding capability")
    os.setgroups(groups)
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    _capset(0)
    fields = status()
    if any([int(value) for value in fields[key].split()] != [uid] * 4
           for key in ("Uid", "Gid")) or os.getgroups() != list(groups):
        raise Refused("child identity readback failed")
    _assert_caps(0)
    os.umask(0o007)


def close_descriptors(keep=()):
    """No directory, listener or namespace handle leaks through role exec."""
    retained = {0, 1, 2, *keep}
    for name in os.listdir("/proc/self/fd"):
        fd = int(name)
        if fd not in retained:
            try:
                os.close(fd)
            except OSError as exc:
                if exc.errno != 9:  # the descriptor used by listdir has closed
                    raise


def broker_environment(data_root):
    # This exact static set is checked against CHILD_FORBIDDEN_ENV in tests.
    # Never import application code into the privileged parent to filter env.
    result = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
              "HOME": "/var/lib/ta-broker", "PYTHONDONTWRITEBYTECODE": "1",
              "TINYASSETS_DATA_DIR": str(data_root)}
    if "TZ" in os.environ:
        result["TZ"] = os.environ["TZ"]
    http_flag = "TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED"
    if http_flag in os.environ:
        # Preserve the deployment's existing opt-in, never arbitrary values.
        result[http_flag] = "1" if os.environ[http_flag].strip().lower() in (
            "1", "true", "yes", "on") else "0"
    return result


def verify_chain():
    # Verify the checker before executing it; its immutable parent/install are
    # also checked by the image gate. No site or application import runs here.
    checker = Path("/usr/local/libexec/ta-chain.py")
    for path in (checker, *checker.parents):
        info = path.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise Refused(f"unsafe chain checker: {path}")
    runpy.run_path(str(checker))["main"]()


def _peer(sock):
    return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


class BrokerLauncher:
    """Broker lifecycle owned by the serving launcher, never by daemon uid 1001.

    data_root and run_root are startup configuration, not request fields. The
    caller must be the daemon's parent and must not reap it until service ends;
    this keeps its pid from being recycled into an accepted peer.
    """

    def __init__(self, data_root, run_root, daemon_pid):
        _assert_caps(SERVING_CAPS)
        verify_chain()
        self.data_root = Path(data_root)
        self.run_root = Path(run_root)
        self.daemon_pid = daemon_pid
        self.broker_pid = None
        self.proof_hash = None
        self.restarts = 0
        self.listener = None
        self.engines = {}
        self.socket_path = self.run_root / "broker" / "broker.sock"

    def bind(self):
        _assert_caps(SERVING_CAPS)
        for path in (self.run_root, *self.run_root.parents):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise Refused("unsafe launcher socket directory or ancestor")
        broker_dir = (self.run_root / "broker").lstat()
        if (not stat.S_ISDIR(broker_dir.st_mode)
                or (broker_dir.st_uid, broker_dir.st_gid,
                    stat.S_IMODE(broker_dir.st_mode)) != (1002, 1101, 0o2750)):
            raise Refused("broker socket directory needs 1002:1101 mode 2750")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        listener.set_inheritable(False)
        # No chown after CHOWN retirement: set egid temporarily to the socket
        # group, then restore it. No request or application runs in this window.
        os.setegid(1001)
        previous = os.umask(0o117)
        try:
            listener.bind(str(self.run_root / "launcher.sock"))
        except BaseException:
            listener.close()
            raise
        finally:
            os.umask(previous)
            os.setegid(0)
        listener.listen(8)
        listener.settimeout(0.25)
        self.listener = listener

    def _spawn_broker(self):
        argv = ["/opt/venv/bin/python", "-I", "-B", "/app/broker_main.py",
                "--socket", str(self.socket_path),
                "--state", str(self.data_root / ".broker" / "state"),
                "--data-root", str(self.data_root), "--owner-uid", "1001",
                "--proof-sha256", self.proof_hash, "--role-split"]
        pid = os.fork()
        if pid == 0:
            try:
                retire_child("broker")
                close_descriptors()
                os.chdir("/")
                os.execve(argv[0], argv, broker_environment(self.data_root))
            except BaseException:
                # Do not print argv, environment or proof material on failure.
                os.write(2, b"broker identity/exec failed\n")
                os._exit(78)
        self.broker_pid = pid

    def _socket_info(self):
        try:
            os.setegid(1101)
            return self.socket_path.lstat()
        except FileNotFoundError:
            return None
        finally:
            os.setegid(0)

    def _ready(self):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if os.waitid(os.P_PID, self.broker_pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
                # Only poll reaps and increments the restart counter. A
                # START_BROKER arriving during exit must not consume that event.
                raise Refused("broker exited during startup")
            # Readiness means the new child is listening, not merely that a
            # stale socket entry exists. SETGID already authorizes IPC group
            # traversal; no private broker directory is opened.
            info = self._socket_info()
            if (info is not None and stat.S_ISSOCK(info.st_mode) and (info.st_uid, info.st_gid,
                    stat.S_IMODE(info.st_mode)) == (1002, 1101, 0o660)):
                try:
                    os.setegid(1101)
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                        connection.settimeout(0.2)
                        connection.connect(str(self.socket_path))
                        if _peer(connection) == (self.broker_pid, 1002, 1002):
                            return
                except (ConnectionRefusedError, FileNotFoundError, TimeoutError):
                    pass
                finally:
                    os.setegid(0)
            time.sleep(0.02)
        raise Refused("broker readiness timeout")

    def handle(self, connection):
        pid, uid, _gid = _peer(connection)
        if (pid, uid) != (self.daemon_pid, 1001):
            raise Refused("launcher peer is not the daemon")
        connection.settimeout(1)
        packet, ancillary, flags, _address = connection.recvmsg(
            MAX_REQUEST, socket.CMSG_SPACE(32), socket.MSG_CMSG_CLOEXEC)
        received = []
        try:
            for level, kind, payload in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    descriptors = array.array("i")
                    descriptors.frombytes(payload[:len(payload) - len(payload) % 4])
                    received.extend(descriptors)
                else:
                    raise Refused("unsupported ancillary message")
            if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                raise Refused("oversized request or descriptors")
            self._request(connection, packet, received)
        finally:
            for fd in received:
                os.close(fd)

    def _request(self, connection, packet, received):
        try:
            request = json.loads(packet)
        except (ValueError, UnicodeError, RecursionError):
            raise Refused("malformed launcher request") from None
        if isinstance(request, dict) and request.get("op") == "SPAWN":
            self._decoder(connection, request, received)
            return
        if received or not isinstance(request, dict) or set(request) != {"op", "proof_sha256"}:
            raise Refused("unsupported launcher request")
        proof = request["proof_sha256"]
        if request["op"] != "START_BROKER" or not isinstance(proof, str) \
                or len(proof) != 64 or any(char not in "0123456789abcdef" for char in proof):
            raise Refused("unsupported launcher request")
        if self.proof_hash is not None and proof != self.proof_hash:
            raise Refused("cannot replace this daemon's lease proof")
        if self.broker_pid is None:
            self.proof_hash = proof
            self._spawn_broker()
        self._ready()
        connection.sendall(json.dumps({"op": "BROKER_READY", "socket": str(self.socket_path),
                                       "restarts": self.restarts}).encode())

    def _decoder(self, connection, request, received):
        if (set(request) != {"op", "kind", "principal", "command_center", "mime"}
                or request["kind"] != "image-decoder" or len(received) != 1
                or not isinstance(request["mime"], str)
                or request["mime"] not in {"image/png", "image/jpeg", "image/webp", "image/gif"}):
            raise Refused("unsupported engine request")
        owner, center = request["principal"], request["command_center"]
        if (not isinstance(owner, str) or not owner.strip() or len(owner) > 512
                or not owner.isprintable() or not isinstance(center, str)
                or not center or center in {".", ".."} or len(center) > 128
                or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                       for c in center)):
            raise Refused("missing admitted engine scope")
        info = (self.data_root / center).lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 1001:
            raise Refused("engine scope is not a canonical daemon directory")
        # Only an anonymous endpoint created by this exact daemon. No directory,
        # regular file, listener, foreign peer or host-network socket crosses.
        fd = received[0]
        if not stat.S_ISSOCK(os.fstat(fd).st_mode):
            raise Refused("decoder requires a socketpair")
        copied = os.dup(fd)
        try:
            channel = socket.socket(fileno=copied)
        except BaseException:
            os.close(copied)
            raise
        try:
            if (channel.family != socket.AF_UNIX or channel.type != socket.SOCK_STREAM
                    or channel.getsockname() or channel.getpeername()
                    or _peer(channel)[:2] != (self.daemon_pid, 1001)):
                raise Refused("decoder endpoint is not daemon-scoped")
        finally:
            channel.close()
        if len(self.engines) >= 2:
            raise Refused("decoder capacity is occupied")
        reply = connection.dup()
        try:
            pid = os.fork()
        except BaseException:
            reply.close()
            raise
        if pid == 0:
            try:
                os.dup2(fd, 0)
                os.dup2(fd, 1)
                null = os.open("/dev/null", os.O_WRONLY)
                os.dup2(null, 2)
                close_descriptors()
                _retire_identity(1003, ())
                os.execve("/opt/venv/bin/python", ["/opt/venv/bin/python", "-I", "-B",
                    "/usr/local/libexec/ta-decoder.py", "enter", request["mime"],
                    str(self.data_root)],
                    {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/tmp",
                     "PYTHONDONTWRITEBYTECODE": "1"})
            except BaseException:
                os._exit(126)
        self.engines[pid] = (time.monotonic() + 35, reply)

    def _poll_engines(self):
        for pid, (deadline, reply) in list(self.engines.items()):
            waited, status_code = os.waitpid(pid, os.WNOHANG)
            if not waited and time.monotonic() >= deadline:
                os.kill(pid, signal.SIGKILL)
                _, status_code = os.waitpid(pid, 0)
                waited = pid
            if waited:
                del self.engines[pid]
                try:
                    reply.sendall(json.dumps({"op": "SPAWN_DONE",
                        "returncode": os.waitstatus_to_exitcode(status_code)}).encode())
                except OSError:
                    pass  # the daemon disconnected; the child is already reaped
                finally:
                    reply.close()

    def poll(self):
        self._poll_engines()
        # WNOWAIT keeps the exact admitted daemon pid reserved until shutdown.
        if os.waitid(os.P_PID, self.daemon_pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
            return False
        if self.broker_pid is not None and os.waitpid(self.broker_pid, os.WNOHANG)[0]:
            self.broker_pid = None
            self.restarts += 1
            self._spawn_broker()
            self._ready()
        try:
            connection, _ = self.listener.accept()
        except TimeoutError:
            return True
        with connection:
            try:
                self.handle(connection)
            except (Refused, ValueError, OSError):
                try:
                    connection.sendall(b'{"op":"REFUSED"}')
                except OSError:
                    pass
        return True

    def stop(self):
        # No private directory cleanup here: only its owner may unlink the
        # broker socket. /run is disposable and the broker replaces it on boot.
        for pid in (self.daemon_pid, self.broker_pid, *self.engines):
            if pid is None:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 5
                while os.waitpid(pid, os.WNOHANG)[0] == 0:
                    if time.monotonic() >= deadline:
                        os.kill(pid, signal.SIGKILL)
                        os.waitpid(pid, 0)
                        break
                    time.sleep(0.02)
            except (ProcessLookupError, ChildProcessError):
                pass
        for _, reply in self.engines.values():
            reply.close()
        self.engines.clear()
        if self.listener is not None:
            self.listener.close()
            (self.run_root / "launcher.sock").unlink()


if __name__ == "__main__":
    sys.exit("role launcher startup is not admitted: migration and engine-cell integration pending")
