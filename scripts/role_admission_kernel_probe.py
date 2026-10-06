"""DA3 kernel facts for capability-free center-root labelling (admission task 1).

Measures, as real daemon (1001) and owner (300001) processes with zero
capability sets, on a Docker ext4 named volume and on tmpfs:

1. setgid inheritance into a daemon mkdir under an owner-owned 02777 directory;
2. the exact inherited access ACL (the canonical migrated root ACL);
3. the final daemon chmod clearing S_ISGID while keeping the named entries;
4. the cross-parent RENAME_NOREPLACE and the staging removals, no capability.

Synthetic state only; no host mounts other than the throwaway named volume.
Any failed fact exits non-zero: that is an acceptance stop, not a workaround.
"""
from __future__ import annotations

import argparse
import subprocess
import uuid

CONTAINER = r'''
import ctypes, json, os, stat, struct, sys
from pathlib import Path

MACHINE = 300001
ACCESS, DEFAULT = 'system.posix_acl_access', 'system.posix_acl_default'
libc = ctypes.CDLL(None, use_errno=True)


def acl(owner, named, group=0, *, mask, other=0):
    entries = [(1, owner, 0xFFFFFFFF)]
    entries += [(2, rights, uid) for uid, rights in sorted(named.items())]
    entries += [(4, group, 0xFFFFFFFF), (16, mask, 0xFFFFFFFF), (32, other, 0xFFFFFFFF)]
    return struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *e) for e in entries)


CANONICAL = acl(7, {MACHINE: 5, 1002: 1}, mask=5)  # role_owner_migration root kind


def xattr(fd, name):
    try:
        return os.getxattr(fd, name)
    except OSError:
        return None


def caps():
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    return {k: int(fields[k], 16) for k in ('CapInh', 'CapPrm', 'CapEff', 'CapAmb')}


def become(uid):
    os.setgroups([])
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    os.umask(0o007)
    if any(caps().values()):
        raise AssertionError('capability retained after identity change')


def as_child(uid, work):
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(reader)
            become(uid)
            os.write(writer, json.dumps(work()).encode())
            os._exit(0)
        except BaseException as exc:
            os.write(writer, json.dumps({'error': repr(exc)}).encode())
            os._exit(1)
    os.close(writer)
    data = b''
    while chunk := os.read(reader, 65536):
        data += chunk
    os.close(reader)
    _, status = os.waitpid(pid, 0)
    result = json.loads(data)
    if status or 'error' in result:
        raise AssertionError(f'uid {uid} step failed: {result}')
    return result


def measure(mount):
    base = Path(mount) / ('admission-' + os.urandom(4).hex())
    data = base / 'data'
    data.mkdir(parents=True)
    os.chown(data, 1001, 1001)
    data.chmod(0o755)
    staging = data / '.role-admission' / 'token'
    facts = {'filesystem': mount}

    def daemon_stage():
        (data / '.role-admission').mkdir(0o700)
        staging.mkdir(0o700)
        fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.setxattr(fd, ACCESS, acl(7, {MACHINE: 7}, mask=7))
            os.setxattr(fd, DEFAULT, CANONICAL)
            info = os.fstat(fd)
            return {'caps': caps(), 'staging': [info.st_uid, info.st_gid,
                                                oct(stat.S_IMODE(info.st_mode))]}
        finally:
            os.close(fd)

    facts['daemon_stage'] = as_child(1001, daemon_stage)
    staging_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def cell():
        os.mkdir('g', 0o777, dir_fd=staging_fd)
        fd = os.open('g', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=staging_fd)
        try:
            os.fchmod(fd, 0o2777)
            info = os.fstat(fd)
            return {'caps': caps(), 'g': [info.st_uid, info.st_gid,
                                          oct(stat.S_IMODE(info.st_mode))],
                    'g_default_is_canonical': xattr(fd, DEFAULT) == CANONICAL}
        finally:
            os.close(fd)

    facts['cell'] = as_child(MACHINE, cell)
    os.close(staging_fd)

    def daemon_label():
        g = os.open(staging / 'g', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(g)
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (
                    MACHINE, MACHINE, 0o2777):
                raise AssertionError('setgid hand-off directory has the wrong shape')
            os.mkdir('root', 0o750, dir_fd=g)
            root = os.open('root', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=g)
            try:
                created = os.fstat(root)
                result = {
                    'fact1_setgid_inherited': created.st_gid == MACHINE
                    and bool(created.st_mode & stat.S_ISGID) and created.st_uid == 1001,
                    'fact2_access_acl_canonical': xattr(root, ACCESS) == CANONICAL,
                    'inherited_default_copy': xattr(root, DEFAULT) == CANONICAL,
                    'created': [created.st_uid, created.st_gid,
                                oct(stat.S_IMODE(created.st_mode))]}
                os.removexattr(root, DEFAULT)
                os.fchmod(root, 0o750)
                final = os.fstat(root)
                result['fact3_chmod_clears_setgid_keeps_acl'] = (
                    stat.S_IMODE(final.st_mode) == 0o750
                    and xattr(root, ACCESS) == CANONICAL and xattr(root, DEFAULT) is None
                    and (final.st_uid, final.st_gid) == (1001, MACHINE))
                result['final'] = [final.st_uid, final.st_gid, oct(stat.S_IMODE(final.st_mode))]
                key = (final.st_dev, final.st_ino)
            finally:
                os.close(root)
            target = os.open(data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                code = libc.renameat2(g, b'root', target, b'center', 1)  # RENAME_NOREPLACE
                if code:
                    raise OSError(ctypes.get_errno(), 'renameat2')
                os.mkdir('other', 0o750, dir_fd=g)
                refused = libc.renameat2(g, b'other', target, b'center', 1) != 0 \
                    and ctypes.get_errno() == 17
                os.rmdir('other', dir_fd=g)
                published = os.stat('center', dir_fd=target, follow_symlinks=False)
            finally:
                os.close(target)
        finally:
            os.close(g)
        os.rmdir(staging / 'g')
        os.rmdir(staging)
        center = data / 'center'
        os.mkdir(center / 'previews', 0o700)
        preview = (center / 'previews').lstat()
        result['fact4_rename_and_removals'] = (
            (published.st_dev, published.st_ino) == key and refused
            and not staging.exists() and preview.st_uid == 1001 and preview.st_gid == 1001
            and not preview.st_mode & stat.S_ISGID)
        result['noreplace_refused_existing'] = refused
        result['previews'] = [preview.st_uid, preview.st_gid, oct(stat.S_IMODE(preview.st_mode))]
        result['caps'] = caps()
        return result

    facts['daemon_label'] = as_child(1001, daemon_label)
    published = (data / 'center')
    fd = os.open(published, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        facts['published'] = {'ids': [info.st_uid, info.st_gid],
                              'mode': oct(stat.S_IMODE(info.st_mode)),
                              'access_canonical': xattr(fd, ACCESS) == CANONICAL,
                              'default_absent': xattr(fd, DEFAULT) is None}
    finally:
        os.close(fd)
    label = facts['daemon_label']
    facts['passed'] = all(label[key] for key in (
        'fact1_setgid_inherited', 'fact2_access_acl_canonical',
        'fact3_chmod_clears_setgid_keeps_acl', 'fact4_rename_and_removals'))
    facts['passed'] &= facts['cell']['g'] == [MACHINE, MACHINE, '0o2777']
    facts['passed'] &= all(not any(step['caps'].values()) for step in (
        facts['daemon_stage'], facts['cell'], label))
    return facts


fstypes = {}
for line in Path('/proc/self/mounts').read_text().splitlines():
    fields = line.split()
    fstypes[fields[1]] = fields[2]
results = []
for mount in ('/probe-ext4', '/probe-tmpfs'):
    facts = measure(mount)
    facts['fstype'] = fstypes.get(mount)
    results.append(facts)
    print(json.dumps(facts, sort_keys=True), flush=True)
expected = {'/probe-ext4': 'ext4', '/probe-tmpfs': 'tmpfs'}
ok = all(r['passed'] and r['fstype'] == expected[r['filesystem']] for r in results)
print(json.dumps({'da3_kernel_facts': 'PASS' if ok else 'FAIL',
                  'kernel': os.uname().release}), flush=True)
sys.exit(0 if ok else 1)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    digest = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    volume = 'ta-admission-probe-' + uuid.uuid4().hex[:12]
    subprocess.run(['docker', 'volume', 'create', volume], check=True, capture_output=True)
    try:
        command = ['docker', 'run', '--rm', '-i', '--network', 'none', '--user', '0:0',
                   '--cap-drop', 'ALL', '-v', f'{volume}:/probe-ext4',
                   '--tmpfs', '/probe-tmpfs:rw,mode=0755']
        for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID', 'SETPCAP', 'KILL'):
            command += ['--cap-add', cap]
        command += ['--security-opt', 'no-new-privileges=true',
                    '--entrypoint', '/opt/venv/bin/python', digest, '-I', '-B', '-']
        print('production image:', digest, flush=True)
        return subprocess.run(command, input=CONTAINER, text=True, timeout=180).returncode
    finally:
        subprocess.run(['docker', 'volume', 'rm', '-f', volume], capture_output=True)


if __name__ == '__main__':
    raise SystemExit(main())
