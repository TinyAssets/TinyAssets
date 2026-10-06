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


def identity(uid=1003):
    status = fields()
    if (os.getresuid() != (uid, uid, uid) or os.getresgid() != (uid, uid, uid)
            or os.getgroups() != [] or int(status["NoNewPrivs"]) != 1
            or any(int(status[key], 16) for key in
                   ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))):
        raise RuntimeError("decoder role retirement is absent")


def enter(mime, data_root, uid=1003, *, preview=False, preview_write=False):
    identity(uid)
    host = namespaces()
    if preview_write:
        info = os.fstat(3)
        if not stat.S_ISDIR(info.st_mode) or info.st_gid != uid:
            raise RuntimeError('preview output source is invalid')
        host['source'] = [info.st_dev, info.st_ino]
        os.set_inheritable(3, True)
    # Load only the immutable stdlib-only filter definition; no package import
    # or application initialization before the owner boundary exists.
    filter_factory = runpy.run_path("/app/tinyassets/providers/jail_seccomp.py")["program_fd"]
    descriptor = filter_factory(profile="cell-nested" if preview else "cell-deny")
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
    if preview:
        for path in ('/opt/ms-playwright', '/etc/fonts'):
            argv.extend(['--ro-bind', path, path])
        argv.extend(['--setenv', 'PLAYWRIGHT_BROWSERS_PATH', '/opt/ms-playwright'])
    if preview_write:
        argv.extend(['--bind-fd', '3', '/workspace'])
    argv.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--chdir", "/tmp", "--seccomp", str(descriptor), "--",
                 "/opt/venv/bin/python", "-I", "-B", "/usr/local/libexec/ta-decoder.py",
                 'inside-preview-write' if preview_write else
                 "inside-preview" if preview else "inside-owner", mime,
                 json.dumps(host, sort_keys=True), data_root, str(uid)])
    os.execv(argv[0], argv)


def prove_cell(host, data_root, uid=1003, profile='cell-deny'):
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
    if (os.getuid() != uid or os.getgid() != uid or int(status["NoNewPrivs"]) != 1
            or any(int(status[key], 16) for key in
                   ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))):
        raise RuntimeError("decoder cell identity is invalid")
    current = namespaces()
    if any(current[key] == host[key] for key in current):
        raise RuntimeError("decoder namespace is shared")
    # Test in a child so the evidence operation cannot change this cell's
    # identity or namespace. Strict classes must refuse CLONE_NEWUSER.
    probe = os.fork()
    if probe == 0:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.unshare(0x10000000)
        os._exit(0 if (result == 0) == (profile == 'cell-nested') else 1)
    if os.waitpid(probe, 0)[1] != 0:
        raise RuntimeError('cell nested namespace policy is absent')
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
            if kind == 'symlink' and profile in ('cell-links', 'cell-nested'):
                raise RuntimeError('cell profile does not permit its declared symlinks') from None
        else:
            if kind != 'symlink' or profile not in ('cell-links', 'cell-nested'):
                raise RuntimeError("cell seccomp profile is absent")
            os.unlink('/tmp/planted-link')
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
             "namespaces": current, "host_namespaces": host, "profile": profile,
             "nested_userns": profile == 'cell-nested',
             "denied": denied, "caps": "zero", "nnp": 1}
    return proof


def preview(host, data_root, uid):
    proof = prove_cell(host, data_root, uid, 'cell-nested')
    sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n')
    sys.stdout.buffer.flush()
    sys.path.insert(0, '/app')
    from tinyassets.ui_preview import MAX_CHILD_OUTPUT, _supervised

    raw = sys.stdin.buffer.read(MAX_CHILD_OUTPUT + 1)
    if len(raw) > MAX_CHILD_OUTPUT:
        raise ValueError('preview input exceeds its bound')
    request = json.loads(raw)
    wall = request['wall_seconds']
    if type(wall) not in (int, float) or not 0 < wall <= 60:
        raise ValueError('invalid preview deadline')
    spec = request['spec']
    # No database or host-path fallback in this cell, even for an empty asset set.
    if not isinstance(spec.get('asset_bytes'), dict) or spec.get('base_path') != '/absent':
        raise ValueError('preview must carry admitted data only')
    out, err, code, breach = _supervised(json.dumps(spec).encode(), wall)
    if breach or code:
        out = json.dumps({'unavailable': breach or err.decode('utf-8', 'replace')[-300:]}).encode()
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.flush()
    return 0


def preview_write(ui_id, host, data_root, uid):
    source = host.pop('source')
    mounted = os.stat('/workspace', follow_symlinks=False)
    if [mounted.st_dev, mounted.st_ino] != source:
        raise RuntimeError('preview output mount does not match pinned source')
    proof = prove_cell(host, data_root, uid)
    proof['source'] = source
    sys.path.insert(0, '/app')
    from tinyassets.ui_preview import MAX_CHILD_OUTPUT, write_preview

    data = sys.stdin.buffer.read(MAX_CHILD_OUTPUT + 1)
    if len(data) > MAX_CHILD_OUTPUT:
        raise ValueError('preview output exceeds its bound')
    result = write_preview('/workspace', ui_id, data)
    sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n' + result.encode())
    return 0


def decode(mime, host, data_root, uid=1003):
    proof = prove_cell(host, data_root, uid)
    sys.stdout.buffer.write(json.dumps({"cell": proof}).encode() + b"\n")
    sys.stdout.buffer.flush()
    sys.path.insert(0, "/app")
    from tinyassets.tool_images import main

    sys.argv = [sys.argv[0], mime, str(1024 * 1024 * 1024), "15"]
    return main()


if __name__ == "__main__":
    if (len(sys.argv) == 5 and sys.argv[1] == 'enter-preview-write'
            and 0 < int(sys.argv[4]) < 100000):
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]), preview_write=True)
    elif (len(sys.argv) == 6 and sys.argv[1] == 'inside-preview-write'
            and 0 < int(sys.argv[5]) < 100000):
        raise SystemExit(preview_write(
            sys.argv[2], json.loads(sys.argv[3]), sys.argv[4], int(sys.argv[5])))
    elif len(sys.argv) == 4 and sys.argv[1] == 'enter-preview' and 0 < int(sys.argv[3]) < 100000:
        enter('', sys.argv[2], int(sys.argv[3]), preview=True)
    elif len(sys.argv) == 6 and sys.argv[1] == 'inside-preview' and 0 < int(sys.argv[5]) < 100000:
        raise SystemExit(preview(json.loads(sys.argv[3]), sys.argv[4], int(sys.argv[5])))
    elif len(sys.argv) == 4 and sys.argv[1] == "enter":
        enter(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] == "inside":
        raise SystemExit(decode(sys.argv[2], json.loads(sys.argv[3]), sys.argv[4]))
    elif len(sys.argv) == 5 and sys.argv[1] == "enter-owner" and 0 < int(sys.argv[4]) < 100000:
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    elif len(sys.argv) == 6 and sys.argv[1] == "inside-owner" and 0 < int(sys.argv[5]) < 100000:
        raise SystemExit(decode(
            sys.argv[2], json.loads(sys.argv[3]), sys.argv[4], int(sys.argv[5])))
    else:
        raise SystemExit("unsupported decoder bootstrap")
