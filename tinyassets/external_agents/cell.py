"""One external-agent cell: a bounded config, a pinned CLI, and ephemeral auth."""
from __future__ import annotations

import base64
import errno
import json
import os
import re
import resource
import selectors
import shutil
import signal
import stat
import subprocess
from pathlib import Path

from tinyassets.role_provider_cell import read_config, resolve_executable, safe_environment

_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_HOME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_JOB = re.compile(r"/agent-home/jobs/[a-f0-9]{32}\Z")
_MAX_AUTH = 8 * 1024 * 1024


def _write_all(fd: int, data: bytes):
    view = memoryview(data)
    while view:
        size = os.write(fd, view)
        if size <= 0:
            raise BrokenPipeError('external-agent stream stopped accepting bytes')
        view = view[size:]


def _directory(parent: str, name: str) -> str:
    path = parent + '/' + name
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    info = os.stat(path, follow_symlinks=False)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise PermissionError('external-agent home directory is not owner-private')
    return path


def _file(path: str, data: bytes):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        pass
    else:
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            raise PermissionError('external-agent auth path is not a file')
        os.unlink(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        while data:
            data = data[os.write(fd, data):]
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_file(path: str) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_size > _MAX_AUTH):
            raise PermissionError('external-agent writeback file is invalid')
        data = bytearray()
        while len(data) <= _MAX_AUTH:
            part = os.read(fd, min(65536, _MAX_AUTH + 1 - len(data)))
            if not part:
                break
            data.extend(part)
        if len(data) != info.st_size:
            raise PermissionError('external-agent writeback file changed during read')
        return bytes(data)
    finally:
        os.close(fd)


def _pump(proc, *, writeback: bool, output=_write_all) -> int:
    selector = selectors.DefaultSelector()
    group_stopped = False
    os.set_blocking(0, False)
    for source, tag in ((0, 'input'), (proc.stdout.fileno(), 'output'),
                        (proc.stderr.fileno(), 'error')):
        os.set_blocking(source, False)
        selector.register(source, selectors.EVENT_READ, tag)
    try:
        while selector.get_map():
            for key, _ in selector.select(timeout=0.2):
                try:
                    chunk = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fd)
                    if key.data == 'input' and proc.stdin is not None:
                        proc.stdin.close()
                    continue
                if key.data == 'input':
                    if proc.stdin is not None and not proc.stdin.closed:
                        try:
                            proc.stdin.write(chunk)
                            proc.stdin.flush()
                        except BrokenPipeError:
                            proc.stdin.close()
                elif key.data == 'output':
                    output(1, chunk)
                elif not writeback:
                    output(2, chunk)
            if proc.poll() is not None:
                if 0 in selector.get_map():
                    selector.unregister(0)
                if not group_stopped:
                    group_stopped = True
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        return proc.wait()
    finally:
        selector.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def cell_main(data_root: str, *, egress: bool, engine: bool, config=None,
              box_center=None, output=_write_all, private_writeback=None) -> int:
    for limit, cap in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_CPU, 600),
                       (resource.RLIMIT_NOFILE, 256), (resource.RLIMIT_NPROC, 64),
                       (resource.RLIMIT_FSIZE, 64 * 1024 * 1024)):
        resource.setrlimit(limit, (cap, cap))
    config = json.loads(read_config()) if config is None else config
    if box_center is None:
        for name in ('system.posix_acl_default', 'system.posix_acl_access'):
            try:
                os.removexattr('/agent-home', name)
            except OSError as exc:
                if exc.errno != errno.ENODATA:
                    raise
        os.chmod('/agent-home', 0o700)
    elif private_writeback is None or engine:
        raise ValueError('owner-box CLI requires private writeback and no legacy engine socket')
    if type(config) is not dict or set(config) != {
        'argv', 'env', 'home_dir', 'workdir', 'auth_files', 'writeback', 'nonce',
        'engine_port', 'catalogue_rewrite', 'requested_model',
    }:
        raise ValueError('invalid external-agent config')
    argv, env = config['argv'], config['env']
    name, workdir = config['home_dir'], config['workdir']
    files, writeback = config['auth_files'], config['writeback']
    nonce, engine_port = config['nonce'], config['engine_port']
    rewrite = config['catalogue_rewrite']
    requested_model = config['requested_model']
    if (not isinstance(argv, list) or not 1 <= len(argv) <= 128
            or any(type(arg) is not str or len(arg) > 4096 or '\0' in arg for arg in argv)
            or not isinstance(env, dict) or len(env) > 128
            or not _HOME.fullmatch(name) or not _JOB.fullmatch(workdir)
            or not isinstance(files, dict) or len(files) > 16
            or type(writeback) is not bool
            or not isinstance(nonce, str) or not re.fullmatch('[a-f0-9]{32}', nonce)
            or (rewrite is not None and not isinstance(rewrite, dict))
            or (isinstance(rewrite, dict) and (
                set(rewrite) != {'source', 'setting', 'model_field',
                                 'clear_fields', 'remove_fields'}
                or not isinstance(rewrite.get('source'), str)
                or not _FILE.fullmatch(rewrite['source'])
                or not isinstance(rewrite.get('setting'), str)
                or not re.fullmatch('[a-z_]{1,80}', rewrite['setting'])
            ))
            or (requested_model is not None and
                (not isinstance(requested_model, str) or len(requested_model) > 200))
            or ((engine_port is not None) != engine)
            or (engine and (not egress or type(engine_port) is not int
                            or not 1024 <= engine_port <= 65535))
            or any(type(key) is not str or not _FILE.fullmatch(key)
                   or type(value) is not str or len(value) > 12 * 1024 * 1024
                   for key, value in files.items())):
        raise ValueError('invalid external-agent config')
    if any(data_root in arg for arg in (*argv, *env.values())):
        raise ValueError('external-agent config names a host data path')
    argv[0] = resolve_executable(argv[0])
    fixed = {'PATH': '/usr/bin:/bin', 'HOME': '/agent-home',
             'USERPROFILE': '/agent-home', 'TMPDIR': '/tmp', 'TMP': '/tmp',
             'TEMP': '/tmp', 'LANG': 'C.UTF-8'}
    clean_env = {**safe_environment(env), **fixed}
    if box_center is None:
        _directory('/agent-home', 'jobs')
        if os.stat('/agent-home/jobs').st_dev == os.stat('/agent-home').st_dev:
            raise PermissionError('external-agent job home is not ephemeral')
        job = _directory('/agent-home/jobs', workdir.rsplit('/', 1)[1])
    else:
        # /tmp is a bounded synthetic tmpfs in the immutable owner runtime.
        # Refuse any accidental host configuration that makes it the owner bind.
        if os.stat('/tmp').st_dev == os.stat('/home/owner').st_dev:
            raise PermissionError('owner-box CLI home is not ephemeral')
        job = _directory('/tmp', 'ta-agent-' + nonce)
    home = _directory(job, name)
    shared_home = '/agent-home/' + name
    clean_env = {key: home if value == shared_home else value
                 for key, value in clean_env.items()}
    clean_env.update(HOME=job, USERPROFILE=job)
    written = []
    try:
        for filename, encoded in files.items():
            data = base64.b64decode(encoded, validate=True)
            if not data or len(data) > _MAX_AUTH:
                raise ValueError('invalid external-agent credential bytes')
            _file(home + '/' + filename, data)
            written.append(filename)
        os.chdir(job)
        command = argv
        if rewrite is not None:
            from tinyassets.external_agents.catalogue_rewrite import from_install

            install, separator, _ = argv[0].partition('/node_modules/')
            if not separator or not install.startswith('/opt/'):
                raise ValueError('external-agent catalogue source is outside install')
            catalogue = from_install(Path(install), rewrite, requested_model)
            _file(job + '/model-catalog.json', catalogue)
            command = [*argv, '-c', rewrite['setting'] + '=' +
                       json.dumps(job + '/model-catalog.json')]
        if egress:
            from tinyassets.boxes.network import FORWARDER, PROXY_ENV

            clean_env.update(PROXY_ENV)
            specs = ['3128=/provider-egress.sock']
            if box_center is not None:
                specs = ['3128=/run/boxhostd/relay.sock']
                clean_env['TINYASSETS_BOX_CENTER'] = box_center
            if engine:
                specs.append(f'{engine_port}=/provider-engine.sock')
            command = ['/opt/venv/bin/python', '-I', '-S', '-c', FORWARDER,
                       *specs, '--', *command]
        proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=clean_env, close_fds=True,
                                start_new_session=True)
        code = _pump(proc, writeback=writeback, output=output)
        if writeback:
            changed = {}
            for name in written:
                try:
                    data = _read_file(home + '/' + name)
                except FileNotFoundError:
                    if code == 0:
                        raise RuntimeError('external-agent auth writeback disappeared') from None
                else:
                    changed[name] = base64.b64encode(data).decode('ascii')
            result = {'external_agent_writeback': changed, 'nonce': nonce, 'exit': code}
            if private_writeback is None:
                _write_all(1, (json.dumps(result) + '\n').encode())
            else:
                private_writeback(result)
        return code
    finally:
        for filename in written:
            try:
                os.unlink(home + '/' + filename)
            except FileNotFoundError:
                pass
        shutil.rmtree(job)
