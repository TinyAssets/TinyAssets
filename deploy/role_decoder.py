"""Static data-free image cell bootstrap. Runs only after uid/capability retirement."""
from __future__ import annotations

import glob
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


def identity(uid):
    status = fields()
    if (os.getresuid() != (uid, uid, uid) or os.getresgid() != (uid, uid, uid)
            or os.getgroups() != [] or int(status["NoNewPrivs"]) != 1
            or any(int(status[key], 16) for key in
                   ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))):
        raise RuntimeError("decoder role retirement is absent")


def tool_mounts(uid, root_fd=3):
    """Pin only owner content; never mount the command-center root itself."""
    # Platform history/sidecars never enter this view and must not consume its
    # descriptor/mount budget. A migrated production center has hundreds of them.
    entries = [name for name in os.listdir(root_fd)
               if not ((name.startswith('.') and name != '.agent-workspace')
                       or name in ('owner.json', 'provider_definitions.json'))]
    if len(entries) > 256:
        raise ValueError('tool center has too many immediate entries')
    required = {'.agent-workspace', 'skills', 'prompts', 'extensions',
                'workflows', 'bin', 'notes', 'wiki'}
    if not required <= set(entries):
        raise ValueError('tool center has not been prepared')
    mounts = ['--tmpfs', '/center']
    for name in sorted(entries):
        fd = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=root_fd)
        info = os.fstat(fd)
        if stat.S_ISLNK(info.st_mode):
            os.close(fd)
            continue
        if (not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or (info.st_uid, info.st_gid) != (uid, uid)
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
                or (name in required and not stat.S_ISDIR(info.st_mode))):
            raise ValueError('tool mount is not an exclusive owner content inode')
        os.set_inheritable(fd, True)
        mounts.extend(['--bind-fd', str(fd), '/center/' + name])
    mounts.extend(['--remount-ro', '/center'])
    return mounts


def enter(mime, data_root, uid, *, preview=False, preview_write=False, node=False,
          tool=False, video=False, provider=False, tool_files=False, package=False,
          owner_delete=False, provider_exec=False, center_root=False, content=False, measure=False,
          browser=False):
    identity(uid)
    host = namespaces()
    mounted = (preview_write or tool or provider or tool_files or package or owner_delete
               or center_root or content or measure or (node and mime == 'workspace'))
    if center_root or content:
        # DA3: daemon-private staging S; the mapper matched its exact path and
        # daemon owner. Its ACL, not its group, grants this owner rwx.
        info = os.fstat(3)
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError('center-root staging is invalid')
        host['source'] = [info.st_dev, info.st_ino]
        os.set_inheritable(3, True)
    elif provider or package:
        # D82: the daemon-sealed snapshot is daemon-owned; D73 grants the owner
        # read access only. The mapper already matched its exact path.
        info = os.fstat(3)
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError('provider snapshot source is invalid')
        host['source'] = [info.st_dev, info.st_ino]
        os.set_inheritable(3, True)
    elif mounted:
        info = os.fstat(3)
        if not stat.S_ISDIR(info.st_mode) or info.st_gid != uid:
            raise RuntimeError('preview output source is invalid')
        host['source'] = [info.st_dev, info.st_ino]
        os.set_inheritable(3, True)
    # A package names its sockets after the 64-hex revision, which may itself
    # contain 'e'; only the suffix selects descriptors.
    selector = mime[64:] if package else mime
    if tool or provider or package:
        host['sockets'] = {}
        slots = (('e', 4), ('g', 5)) if provider else (('e', 4), ('t', 5))
        for key, fd in slots:
            if key in selector:
                info = os.fstat(fd)
                if not stat.S_ISSOCK(info.st_mode) or info.st_nlink != 1:
                    raise RuntimeError('tool relay is not a socket')
                host['sockets'][key] = [info.st_dev, info.st_ino]
                os.set_inheritable(fd, True)
    if tool and 'x' in selector:
        info = os.fstat(6)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise RuntimeError('tool extension descriptor is invalid')
        host['sockets']['x'] = [info.st_dev, info.st_ino]
        os.set_inheritable(6, True)
    # Load only the immutable stdlib-only filter definition; no package import
    # or application initialization before the owner boundary exists.
    filter_factory = runpy.run_path("/app/tinyassets/providers/jail_seccomp.py")["program_fd"]
    profile = 'cell-links' if package else (
        'cell-nested' if preview or browser or node or tool else 'cell-deny')
    descriptor = filter_factory(profile=profile)
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
    if preview or browser:
        for path in ('/opt/ms-playwright', '/etc/fonts'):
            argv.extend(['--ro-bind', path, path])
        argv.extend(['--setenv', 'PLAYWRIGHT_BROWSERS_PATH', '/opt/ms-playwright'])
    if video:
        # Debian links ffmpeg's BLAS/LAPACK through /etc/alternatives; bind only
        # those two image entries, and only when they resolve inside /usr/lib.
        for name in ('libblas.so.3', 'liblapack.so.3'):
            path = f'/etc/alternatives/{name}-{os.uname().machine}-linux-gnu'
            if not os.path.realpath(path).startswith('/usr/lib/'):
                raise RuntimeError(('video library alternative escapes /usr/lib', path))
            argv.extend(['--ro-bind', path, path])
    if package:
        argv.extend(['--ro-bind-fd', '3', '/package'])
        if 'e' in selector:
            argv.extend(['--bind-fd', '4', '/package-egress.sock'])
            argv.extend(['--ro-bind', '/etc/ssl/certs', '/etc/ssl/certs'])
        if 't' in selector:
            argv.extend(['--bind-fd', '5', '/package-broker.sock'])
    elif provider:
        # Fixed immutable shipped CLI trees only; node itself lives under /usr.
        for path in sorted(glob.glob('/opt/*-install')):
            if os.path.isdir(path) and not os.path.islink(path):
                argv.extend(['--ro-bind', path, path])
        argv.extend(['--ro-bind-fd', '3', '/snapshot'])
        if 'e' in mime:
            argv.extend(['--bind-fd', '4', '/provider-egress.sock'])
            argv.extend(['--ro-bind', '/etc/ssl/certs', '/etc/ssl/certs'])
        if 'g' in mime:
            argv.extend(['--bind-fd', '5', '/provider-engine.sock'])
    elif tool:
        argv.extend(tool_mounts(uid))
        if 'e' in mime:
            # Public trust roots for HTTPS through the pinned checking proxy.
            argv.extend(['--ro-bind', '/etc/ssl/certs', '/etc/ssl/certs'])
        if 'x' in mime:
            argv.extend(['--ro-bind-fd', '6', '/tool-extensions'])
        for key, fd, destination in (('e', 4, '/tool-egress.sock'), ('t', 5, '/tool-ta.sock')):
            if key in mime:
                argv.extend(['--bind-fd', str(fd), destination])
    elif mounted:
        argv.extend(['--ro-bind-fd' if measure else '--bind-fd', '3', '/workspace'])
    argv.extend(["--proc", "/proc", "--dev", "/dev"])
    if package or provider_exec:
        argv.extend(['--size', str(256 * 1024 * 1024)])
    argv.extend(["--tmpfs", "/tmp",
                 "--chdir", "/tmp", "--seccomp", str(descriptor), "--",
                 "/opt/venv/bin/python", "-I", "-B", "/usr/local/libexec/ta-decoder.py",
                 'inside-browser' if browser else 'inside-measure' if measure else
                 'inside-owner-delete' if owner_delete else
                 'inside-content' if content else
                 'inside-center-root' if center_root else
                 'inside-package' if package else 'inside-tool-files' if tool_files else
                 'inside-provider-exec' if provider_exec else
                 'inside-provider' if provider else 'inside-video' if video else
                 'inside-tool' if tool else 'inside-node' if node else
                 'inside-preview-write' if preview_write else
                 "inside-preview" if preview else "inside-owner", mime,
                 json.dumps(host, sort_keys=True), data_root, str(uid)])
    os.execv(argv[0], argv)


def prove_cell(host, data_root, uid, profile='cell-deny'):
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
    from tinyassets.role_preview_cell import supervised
    from tinyassets.ui_preview import MAX_CHILD_OUTPUT

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
    out, err, code, breach = supervised(json.dumps(spec).encode(), wall)
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


def decode(mime, host, data_root, uid):
    proof = prove_cell(host, data_root, uid)
    sys.stdout.buffer.write(json.dumps({"cell": proof}).encode() + b"\n")
    sys.stdout.buffer.flush()
    sys.path.insert(0, "/app")
    from tinyassets.tool_images import main

    sys.argv = [sys.argv[0], mime, str(1024 * 1024 * 1024), "15"]
    return main()


#: Owner-owned entries every center root carries; role_center_admission.SEED_ENTRIES.
SEED_ENTRIES = ('.agent-workspace', 'previews', 'skills', 'prompts', 'extensions',
                'workflows', 'bin', 'notes', 'wiki')


def center_root_handoff(staging):
    """DA3: one setgid directory ``g`` and the fixed seed entries inside it.

    No caller path, executable, environment, relay or credential. The owner is
    in its own group, so S_ISGID survives the fchmod. Each seed inherits the
    staging default ACL (the daemon's named entry) and is 0770 without
    S_ISGID, so the daemon can rename it into the root it publishes.
    """
    os.mkdir('g', 0o777, dir_fd=staging)
    handoff = os.open('g', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=staging)
    try:
        os.fchmod(handoff, 0o2777)
        made = os.fstat(handoff)
        seeds = {}
        for name in SEED_ENTRIES:
            os.mkdir(name, 0o770, dir_fd=handoff)
            seed = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=handoff)
            try:
                os.fchmod(seed, 0o770)
                info = os.fstat(seed)
            finally:
                os.close(seed)
            seeds[name] = [info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]
    finally:
        os.close(handoff)
    return [made.st_uid, made.st_gid, stat.S_IMODE(made.st_mode)], seeds


if __name__ == "__main__":
    # Immutable stdlib-only code; do not print traceback source/owner bytes.
    sys.excepthook = runpy.run_path('/app/tinyassets/cell_diagnostics.py')['exception_hook']
    if (len(sys.argv) == 5 and sys.argv[1] in ('enter-provider', 'enter-provider-exec')
            and sys.argv[2] in ('-', 'e', 'eg') and 0 < int(sys.argv[4]) < 100000):
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]), provider=True,
              provider_exec=sys.argv[1] == 'enter-provider-exec')
    elif (len(sys.argv) == 6 and sys.argv[1] in ('inside-provider', 'inside-provider-exec')
            and sys.argv[2] in ('-', 'e', 'eg') and 0 < int(sys.argv[5]) < 100000):
        host = json.loads(sys.argv[3])
        source = host.pop('source')
        sockets = host.pop('sockets')
        mounted = os.stat('/snapshot', follow_symlinks=False)
        if [mounted.st_dev, mounted.st_ino] != source or not stat.S_ISDIR(mounted.st_mode):
            raise RuntimeError('provider snapshot mount differs from pinned source')
        if set(sockets) != set(sys.argv[2].strip('-')):
            raise RuntimeError('provider relays differ from their admitted flags')
        for key, path in (('e', '/provider-egress.sock'), ('g', '/provider-engine.sock')):
            if key in sockets:
                info = os.stat(path, follow_symlinks=False)
                if [info.st_dev, info.st_ino] != sockets[key] or not stat.S_ISSOCK(info.st_mode):
                    raise RuntimeError('provider relay mount differs from pinned source')
        # prove_cell closes every mount/seccomp descriptor before config is read.
        proof = prove_cell(host, sys.argv[4], int(sys.argv[5]))
        proof['source'] = source
        proof['sockets'] = sockets
        sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n')
        sys.stdout.buffer.flush()
        sys.path.insert(0, '/app')
        from tinyassets.role_provider_cell import cell_main

        raise SystemExit(cell_main(sys.argv[4], execution=sys.argv[1] == 'inside-provider-exec',
                                   egress='e' in sockets, engine='g' in sockets))
    elif (len(sys.argv) == 5 and sys.argv[1] == 'enter-video'
            and sys.argv[2] == 'video' and 0 < int(sys.argv[4]) < 100000):
        enter('video', sys.argv[3], int(sys.argv[4]), video=True)
    elif (len(sys.argv) == 6 and sys.argv[1] == 'inside-video'
            and sys.argv[2] == 'video' and 0 < int(sys.argv[5]) < 100000):
        proof = prove_cell(json.loads(sys.argv[3]), sys.argv[4], int(sys.argv[5]))
        sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n')
        sys.stdout.buffer.flush()
        sys.path.insert(0, '/app')
        from tinyassets.role_video_codec import cell_main

        raise SystemExit(cell_main())
    elif (len(sys.argv) == 5 and sys.argv[1] == 'enter-package'
            and sys.argv[2][64:] in ('-', 'e', 't', 'et')
            and len(sys.argv[2]) >= 65
            and all(c in '0123456789abcdef' for c in sys.argv[2][:64])
            and 0 < int(sys.argv[4]) < 100000):
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]), package=True)
    elif (len(sys.argv) == 6 and sys.argv[1] == 'inside-package'
            and 0 < int(sys.argv[5]) < 100000):
        host = json.loads(sys.argv[3])
        source, sockets = host.pop('source'), host.pop('sockets')
        info = os.stat('/package', follow_symlinks=False)
        if [info.st_dev, info.st_ino] != source:
            raise RuntimeError('package source differs from pinned source')
        if set(sockets) != set(sys.argv[2][64:].strip('-')):
            raise RuntimeError('package sockets differ from their admitted flags')
        for key, path in (('e', '/package-egress.sock'), ('t', '/package-broker.sock')):
            if key in sockets:
                info = os.stat(path, follow_symlinks=False)
                if [info.st_dev, info.st_ino] != sockets[key] or not stat.S_ISSOCK(info.st_mode):
                    raise RuntimeError('package relay differs from pinned source')
        proof = prove_cell(host, sys.argv[4], int(sys.argv[5]), 'cell-links')
        proof.update(source=source, sockets=sockets, revision=sys.argv[2][:64])
        sys.path.insert(0, '/app')
        from tinyassets.role_package_cell import manifest, run
        from tinyassets.role_provider_cell import read_config

        doc = manifest(sys.argv[2][:64])
        sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n')
        sys.stdout.buffer.flush()
        if json.loads(read_config()) != {'start': True}:
            raise ValueError('package execution was not acknowledged')
        raise SystemExit(run(doc, broker='t' in sockets, egress='e' in sockets))
    elif (len(sys.argv) == 5 and sys.argv[1] == 'enter-owner-delete'
            and sys.argv[2] == 'delete' and 0 < int(sys.argv[4]) < 100000):
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]), owner_delete=True)
    elif (len(sys.argv) == 6 and sys.argv[1] == 'inside-owner-delete'
            and sys.argv[2] == 'delete' and 0 < int(sys.argv[5]) < 100000):
        host = json.loads(sys.argv[3])
        source = host.pop('source')
        info = os.stat('/workspace', follow_symlinks=False)
        if [info.st_dev, info.st_ino] != source:
            raise RuntimeError('owner deletion source differs from pinned source')
        proof = prove_cell(host, sys.argv[4], int(sys.argv[5]))
        proof['source'] = source
        import resource

        for kind, bound in ((resource.RLIMIT_AS, 256 * 1024 * 1024),
                            (resource.RLIMIT_CPU, 25), (resource.RLIMIT_NOFILE, 192),
                            (resource.RLIMIT_FSIZE, 0), (resource.RLIMIT_CORE, 0)):
            resource.setrlimit(kind, (bound, bound))
        sys.path.insert(0, '/app')
        from tinyassets.role_owner_delete_cell import remove_owned
        from tinyassets.role_provider_cell import read_config

        sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n')
        sys.stdout.buffer.flush()
        if json.loads(read_config()) != {}:
            raise ValueError('invalid fixed owner deletion request')
        fd = os.open('/workspace', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        import array

        control = socket.socket(fileno=os.dup(0))
        def classify_daemon(descriptor):
            control.sendmsg([b'?'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                     array.array('i', [descriptor]))])
            response = control.recv(1)
            if response not in (b'0', b'1'):
                raise RuntimeError('owner deletion classifier ended early')
            return response == b'1'
        try:
            result = remove_owned(fd, classify_daemon=classify_daemon)
        except (OSError, RuntimeError) as exc:
            sys.stdout.buffer.write(b'!' + json.dumps(
                {'error': str(exc)[:2048]}).encode() + b'\n')
            raise SystemExit(1) from exc
        finally:
            os.close(fd)
            control.close()
        sys.stdout.buffer.write(b'!' + json.dumps(result).encode() + b'\n')
    elif (len(sys.argv) == 5 and sys.argv[1] == 'enter-center-root'
            and sys.argv[2] == 'root' and 0 < int(sys.argv[4]) < 100000):
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]), center_root=True)
    elif (len(sys.argv) == 6 and sys.argv[1] == 'inside-center-root'
            and sys.argv[2] == 'root' and 0 < int(sys.argv[5]) < 100000):
        host = json.loads(sys.argv[3])
        source = host.pop('source')
        info = os.stat('/workspace', follow_symlinks=False)
        if [info.st_dev, info.st_ino] != source:
            raise RuntimeError('center-root staging differs from pinned source')
        proof = prove_cell(host, sys.argv[4], int(sys.argv[5]))
        proof['source'] = source
        import resource

        for kind, bound in ((resource.RLIMIT_AS, 128 * 1024 * 1024),
                            (resource.RLIMIT_CPU, 5), (resource.RLIMIT_NOFILE, 32),
                            (resource.RLIMIT_FSIZE, 0), (resource.RLIMIT_CORE, 0)):
            resource.setrlimit(kind, (bound, bound))
        sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n')
        sys.stdout.buffer.flush()
        staging = os.open('/workspace', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            made, seeds = center_root_handoff(staging)
        finally:
            os.close(staging)
        sys.stdout.buffer.write(json.dumps({'g': made, 'seeds': seeds}).encode() + b'\n')
    elif (len(sys.argv) == 5 and sys.argv[1] == 'enter-content'
            and sys.argv[2] == 'content' and 0 < int(sys.argv[4]) < 100000):
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]), content=True)
    elif (len(sys.argv) == 6 and sys.argv[1] == 'inside-content'
            and sys.argv[2] == 'content' and 0 < int(sys.argv[5]) < 100000):
        host = json.loads(sys.argv[3])
        source = host.pop('source')
        info = os.stat('/workspace', follow_symlinks=False)
        if [info.st_dev, info.st_ino] != source:
            raise RuntimeError('owner content staging differs from pinned source')
        proof = prove_cell(host, sys.argv[4], int(sys.argv[5]))
        proof['source'] = source
        import resource

        for kind, bound in ((resource.RLIMIT_AS, 512 * 1024 * 1024),
                            (resource.RLIMIT_CPU, 25), (resource.RLIMIT_NOFILE, 64),
                            (resource.RLIMIT_CORE, 0)):
            resource.setrlimit(kind, (bound, bound))
        sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n')
        sys.stdout.buffer.flush()
        sys.path.insert(0, '/app')
        from tinyassets.role_content import cell_main

        cell_main()
    elif (len(sys.argv) == 5 and sys.argv[1] == 'enter-measure'
            and sys.argv[2] == 'measure' and 0 < int(sys.argv[4]) < 100000):
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]), measure=True)
    elif (len(sys.argv) == 6 and sys.argv[1] == 'inside-measure'
            and sys.argv[2] == 'measure' and 0 < int(sys.argv[5]) < 100000):
        host = json.loads(sys.argv[3])
        source = host.pop('source')
        info = os.stat('/workspace', follow_symlinks=False)
        if [info.st_dev, info.st_ino] != source:
            raise RuntimeError('storage source differs from pinned source')
        proof = prove_cell(host, sys.argv[4], int(sys.argv[5]))
        proof['source'] = source
        import resource

        for kind, bound in ((resource.RLIMIT_AS, 256 * 1024 * 1024),
                            (resource.RLIMIT_CPU, 25), (resource.RLIMIT_NOFILE, 192),
                            (resource.RLIMIT_FSIZE, 0), (resource.RLIMIT_CORE, 0)):
            resource.setrlimit(kind, (bound, bound))
        sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n')
        sys.stdout.buffer.flush()
        sys.path.insert(0, '/app')
        from tinyassets.role_storage import cell_main

        cell_main()
    elif (len(sys.argv) == 5 and sys.argv[1] == 'enter-tool-files'
            and sys.argv[2] == 'files' and 0 < int(sys.argv[4]) < 100000):
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]), tool_files=True)
    elif (len(sys.argv) == 6 and sys.argv[1] == 'inside-tool-files'
            and sys.argv[2] == 'files' and 0 < int(sys.argv[5]) < 100000):
        host = json.loads(sys.argv[3])
        source = host.pop('source')
        info = os.stat('/workspace', follow_symlinks=False)
        if [info.st_dev, info.st_ino] != source:
            raise RuntimeError('tool maintenance source differs from pinned source')
        proof = prove_cell(host, sys.argv[4], int(sys.argv[5]))
        proof['source'] = source
        import resource

        for kind, bound in ((resource.RLIMIT_AS, 256 * 1024 * 1024),
                            (resource.RLIMIT_CPU, 25), (resource.RLIMIT_NOFILE, 192),
                            (resource.RLIMIT_FSIZE, 1024 * 1024), (resource.RLIMIT_CORE, 0)):
            resource.setrlimit(kind, (bound, bound))
        sys.path.insert(0, '/app')
        from tinyassets.role_tool_files import maintain
        from tinyassets.role_tools import _frame, _read

        sys.stdout.buffer.write(_frame({'cell': proof}, 16384))
        sys.stdout.buffer.flush()
        request = _read(sys.stdin.buffer, 4096)
        if (set(request) != {'agent_id'} or type(request['agent_id']) is not str
                or not request['agent_id'].strip() or len(request['agent_id']) > 512):
            raise ValueError('invalid tool maintenance request')
        fd = os.open('/workspace', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            answer = maintain(fd, agent_id=request['agent_id'])
        finally:
            os.close(fd)
        sys.stdout.buffer.write(_frame({'files': answer}, 16384))
        sys.stdout.buffer.flush()
    elif (len(sys.argv) == 5 and sys.argv[1] == 'enter-tool'
            and sys.argv[2] in ('-', 'e', 't', 'et', 'x', 'ex', 'tx', 'etx')
            and 0 < int(sys.argv[4]) < 100000):
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]), tool=True)
    elif len(sys.argv) == 6 and sys.argv[1] == 'inside-tool' and 0 < int(sys.argv[5]) < 100000:
        host = json.loads(sys.argv[3])
        source = host.pop('source')
        sockets = host.pop('sockets')
        for key, target in (('e', '/tool-egress.sock'), ('t', '/tool-ta.sock')):
            if key in sockets:
                info = os.stat(target, follow_symlinks=False)
                if [info.st_dev, info.st_ino] != sockets[key] or not stat.S_ISSOCK(info.st_mode):
                    raise RuntimeError('tool socket mount differs from pinned source')
        if set(sockets) != set(sys.argv[2].strip('-')):
            raise RuntimeError('tool mounts differ from their admitted flags')
        if 'x' in sockets:
            info = os.stat('/tool-extensions', follow_symlinks=False)
            if [info.st_dev, info.st_ino] != sockets['x'] or not stat.S_ISDIR(info.st_mode):
                raise RuntimeError('tool extension mount differs from pinned source')
        proof = prove_cell(host, sys.argv[4], int(sys.argv[5]), 'cell-nested')
        proof['source'] = source
        proof['sockets'] = sockets
        sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n')
        sys.stdout.buffer.flush()
        sys.path.insert(0, '/app')
        from tinyassets.role_tools import cell_main

        raise SystemExit(cell_main(egress='e' in sockets, ta='t' in sockets,
                                   extensions='x' in sockets))
    elif (len(sys.argv) == 5 and sys.argv[1] == 'enter-node'
            and sys.argv[2] in {'data', 'workspace'} and 0 < int(sys.argv[4]) < 100000):
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]), node=True)
    elif (len(sys.argv) == 6 and sys.argv[1] == 'inside-node'
            and sys.argv[2] in {'data', 'workspace'} and 0 < int(sys.argv[5]) < 100000):
        host = json.loads(sys.argv[3])
        source = host.pop('source', None)
        if source is not None:
            mounted = os.stat('/workspace', follow_symlinks=False)
            if [mounted.st_dev, mounted.st_ino] != source:
                raise RuntimeError('node mount differs from pinned source')
        proof = prove_cell(host, sys.argv[4], int(sys.argv[5]), 'cell-nested')
        proof['source'] = source
        sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n')
        sys.stdout.buffer.flush()
        sys.path.insert(0, '/app')
        from tinyassets.role_node import cell_main

        raise SystemExit(cell_main(sys.argv[2] == 'workspace'))
    elif (len(sys.argv) == 5 and sys.argv[1] == 'enter-preview-write'
            and 0 < int(sys.argv[4]) < 100000):
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]), preview_write=True)
    elif (len(sys.argv) == 6 and sys.argv[1] == 'inside-preview-write'
            and 0 < int(sys.argv[5]) < 100000):
        raise SystemExit(preview_write(
            sys.argv[2], json.loads(sys.argv[3]), sys.argv[4], int(sys.argv[5])))
    elif len(sys.argv) == 4 and sys.argv[1] == 'enter-browser' and 0 < int(sys.argv[3]) < 100000:
        enter('', sys.argv[2], int(sys.argv[3]), browser=True)
    elif len(sys.argv) == 6 and sys.argv[1] == 'inside-browser' and 0 < int(sys.argv[5]) < 100000:
        proof = prove_cell(json.loads(sys.argv[3]), sys.argv[4], int(sys.argv[5]), 'cell-nested')
        sys.stdout.write(json.dumps({'cell': proof}) + '\n')
        sys.stdout.flush()
        sys.path.insert(0, '/app')
        from tinyassets.browser_cell import main

        main()
    elif len(sys.argv) == 4 and sys.argv[1] == 'enter-preview' and 0 < int(sys.argv[3]) < 100000:
        enter('', sys.argv[2], int(sys.argv[3]), preview=True)
    elif len(sys.argv) == 6 and sys.argv[1] == 'inside-preview' and 0 < int(sys.argv[5]) < 100000:
        raise SystemExit(preview(json.loads(sys.argv[3]), sys.argv[4], int(sys.argv[5])))
    elif len(sys.argv) == 5 and sys.argv[1] == "enter-owner" and 0 < int(sys.argv[4]) < 100000:
        enter(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    elif len(sys.argv) == 6 and sys.argv[1] == "inside-owner" and 0 < int(sys.argv[5]) < 100000:
        raise SystemExit(decode(
            sys.argv[2], json.loads(sys.argv[3]), sys.argv[4], int(sys.argv[5])))
    else:
        raise SystemExit("unsupported decoder bootstrap")
