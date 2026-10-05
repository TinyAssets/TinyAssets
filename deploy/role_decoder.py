"""Static data-free image cell bootstrap. Runs only after uid/capability retirement."""
from __future__ import annotations

import json
import os
import runpy
import socket
import stat
import sys


def fields():
    with open("/proc/self/status") as handle:
        return dict(line.split(":", 1) for line in handle.read().splitlines())


def namespaces():
    return {key: os.readlink("/proc/self/ns/" + key) for key in ("mnt", "pid", "ipc", "net")}


def identity():
    status = fields()
    if (os.getresuid() != (1003, 1003, 1003) or os.getresgid() != (1003, 1003, 1003)
            or os.getgroups() != [] or int(status["NoNewPrivs"]) != 1
            or any(int(status[key], 16) for key in
                   ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))):
        raise RuntimeError("decoder role retirement is absent")


def enter(mime, data_root):
    identity()
    host = namespaces()
    # Load only the immutable stdlib-only filter definition; no package import
    # or application initialization before the owner boundary exists.
    filter_factory = runpy.run_path("/app/tinyassets/providers/jail_seccomp.py")["program_fd"]
    descriptor = filter_factory(profile="cell-deny")
    os.set_inheritable(descriptor, True)
    argv = ["/usr/bin/bwrap", "--die-with-parent", "--new-session", "--unshare-all",
            "--cap-drop", "ALL", "--clearenv", "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "HOME", "/tmp", "--setenv", "LANG", "C.UTF-8",
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    for path in ("/usr", "/opt/venv", "/app", "/etc/ld.so.cache"):
        argv.extend(["--ro-bind", path, path])
    for path in ("/bin", "/lib", "/lib64"):
        if os.path.exists(path):
            argv.extend(["--ro-bind", path, path])
    argv.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--chdir", "/tmp", "--seccomp", str(descriptor), "--",
                 "/opt/venv/bin/python", "-I", "-B", "/usr/local/libexec/ta-decoder.py",
                 "inside", mime, json.dumps(host, sort_keys=True), data_root])
    os.execv(argv[0], argv)


def decode(mime, host, data_root):
    # bwrap does not promise to close caller mount/seccomp fds. Close them here,
    # before the decoder (or any native image parser) imports or reads bytes.
    for name in os.listdir("/proc/self/fd"):
        fd = int(name)
        if fd > 2:
            try:
                os.close(fd)
            except OSError as exc:
                if exc.errno != 9:
                    raise
    # Supplementary host groups are unmapped in the new user namespace. The
    # role identity was asserted before entry; the cell must retain uid/gid.
    status = fields()
    if (os.getuid() != 1003 or os.getgid() != 1003 or int(status["NoNewPrivs"]) != 1
            or any(int(status[key], 16) for key in
                   ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))):
        raise RuntimeError("decoder cell identity is invalid")
    current = namespaces()
    if any(current[key] == host[key] for key in current):
        raise RuntimeError("decoder namespace is shared")
    retained = []
    for name in os.listdir("/proc/self/fd"):
        fd = int(name)
        try:
            info = os.fstat(fd)
        except OSError:
            continue
        if fd > 2 or stat.S_ISDIR(info.st_mode):
            raise RuntimeError("decoder inherited an undeclared descriptor")
        try:
            escaped = os.open("..", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
        except OSError:
            pass
        else:
            os.close(escaped)
            raise RuntimeError("decoder descriptor traverses host parents")
        retained.append(fd)
    denied = ["/data", "/run/tinyassets", "/home/tinyassets", "/data/.broker/outbound.db",
              "/data/bob/.credential-vault.json", "/data/.broker/owner.json", data_root,
              data_root + "/decoder-bob/workspace/private",
              data_root + "/decoder-bob/.credential-vault.json",
              data_root + "/decoder-bob/owner.json", data_root + "/.broker/outbound.db",
              data_root + "/.broker/owner.json"]
    for path in denied:
        for access in (os.O_RDONLY, os.O_WRONLY):
            try:
                fd = os.open(path, access | os.O_NOFOLLOW | os.O_NONBLOCK)
            except (FileNotFoundError, PermissionError, NotADirectoryError):
                pass
            else:
                os.close(fd)
                raise RuntimeError("decoder has a host data mount")
    try:
        os.link(data_root + "/decoder-bob/workspace/private", "/tmp/planted-hardlink")
    except (FileNotFoundError, PermissionError, NotADirectoryError):
        pass
    else:
        raise RuntimeError("decoder could alias foreign data")
    for kind in ("symlink", "fifo"):
        try:
            if kind == "symlink":
                os.symlink("/data/bob/private", "/tmp/planted-link")
            else:
                os.mkfifo("/tmp/planted-fifo")
        except PermissionError:
            pass
        else:
            raise RuntimeError("decoder cell-deny profile is absent")
    for address in (("127.0.0.1", 39281), "\0ta-uid-cross-owner"):
        family = socket.AF_INET if isinstance(address, tuple) else socket.AF_UNIX
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.2)
            try:
                connection.connect(address)
            except OSError:
                pass
            else:
                raise RuntimeError("decoder reached host network or IPC")
    proof = {"uid": os.getuid(), "gid": os.getgid(), "fds": sorted(retained),
             "groups": os.getgroups(),
             "namespaces": current, "host_namespaces": host, "profile": "cell-deny",
             "denied": denied, "caps": "zero", "nnp": 1}
    sys.stdout.buffer.write(json.dumps({"cell": proof}).encode() + b"\n")
    sys.stdout.buffer.flush()
    sys.path.insert(0, "/app")
    from tinyassets.tool_images import main

    sys.argv = [sys.argv[0], mime, str(1024 * 1024 * 1024), "15"]
    return main()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "enter":
        enter(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] == "inside":
        raise SystemExit(decode(sys.argv[2], json.loads(sys.argv[3]), sys.argv[4]))
    else:
        raise SystemExit("unsupported decoder bootstrap")
