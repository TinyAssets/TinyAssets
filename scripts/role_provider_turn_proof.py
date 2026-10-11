"""One real served turn: the shipped Claude CLI, in a provider-exec cell, streaming.

Synthetic data only, ``--network none``, no credential. Inside the image the
script becomes PID1, runs the production bootstrap (broker, bounded mapper,
daemon retirement), then, as the retired daemon:

* starts a fake Anthropic Messages endpoint on the daemon's loopback and
  admits exactly its name through the owner's egress proxy (the proxy's
  global-address floor is what the real one refuses loopback with; the cell
  still reaches it only through the pinned relay socket);
* runs ``ClaudeProvider.complete`` with the served-turn ``ModelConfig`` and the
  owner's sealed launch snapshot, so the command line, environment filter,
  cell, egress and stream reader are the production ones;
* asserts the streamed answer came back through the reader, then that the
  two refusal shapes that used to surface as ``ConnectionResetError`` now
  reach the daemon as the CLI's own words or the cell's bounded failure
  marker (``tinyassets/cell_diagnostics.py``, carried as ``stop_reason``).

    python scripts/role_provider_turn_proof.py --image <cutover image>
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time

CONTAINER = r'''
import asyncio, json, os, runpy, socket, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, '/app')
launch = runpy.run_path('/usr/local/libexec/ta-launch.py')
bounded = runpy.run_path('/usr/local/libexec/ta-owner-launch.py')
launch['verify_chain']()

def own(path, uid, gid, mode):
    os.chown(path, uid, gid); os.chmod(path, mode)
    assert (os.stat(path).st_mode & 0o7777, os.stat(path).st_gid) == (mode, gid), path

FAKE_HOST = 'fake-anthropic.test'
ANSWER = 'CELL-TURN-OK through the pinned egress relay'

root = Path(tempfile.mkdtemp(prefix='provider-turn-'))
own(root, 1001, 1001, 0o755)
for name in ('.broker', '.broker/state', '.broker/.outbound-proxy'):
    path = root / name; path.mkdir(); own(path, 1002, 1101, 0o2700)
reader, writer = socket.socketpair()
seed = os.fork()
if seed == 0:
    reader.close(); launch['retire_child']('broker')
    launch['close_descriptors']((writer.fileno(),))
    from tinyassets.broker.owner_identities import OwnerIdentities
    store = OwnerIdentities(root / '.broker/state/owner-identities.db', initialize=True)
    writer.sendall(json.dumps({'alice': store.resolve('alice', allocate=True).uid}).encode())
    os._exit(0)
writer.close(); identities = json.loads(reader.recv(4096)); reader.close()
assert os.waitpid(seed, 0)[1] == 0
uid = identities['alice']
center = root / 'cell-alice'; center.mkdir(); own(center, 1001, uid, 0o710)
subprocess.run(['setfacl', '-m', f'u:{uid}:x', str(center)], check=True)
parent = center
for name in ('.runtime', 'provider-launch-credentials', 'fixture'):
    parent = parent / name; parent.mkdir(); own(parent, 1001, uid, 0o700)
    subprocess.run(['setfacl', '-m', f'u:{uid}:rx', str(parent)], check=True)
snapshot = parent
# The fake endpoint's port is chosen before the snapshot is sealed (the base
# URL must be the owner's own sealed value for the cell filter to admit it)
# but bound after the bootstrap, which closes every descriptor it did not make.
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 0))
    PORT = probe.getsockname()[1]
BASE_URL = f'http://{FAKE_HOST}:{PORT}'
for name, value in (('auth.json', 'sk-ant-oat01-synthetic-cell-proof-token'),
                    ('base-url', BASE_URL)):
    path = snapshot / name; path.write_text(value); own(path, 1001, uid, 0o600)
    subprocess.run(['setfacl', '-m', f'u:{uid}:r', str(path)], check=True)
bindings = {('alice', center.name): uid}
run = Path(tempfile.mkdtemp(prefix='provider-turn-', dir='/run')); run.chmod(0o755)
ipc = run / 'broker'; ipc.mkdir(); own(ipc, 1002, 1101, 0o2750)
libc = launch['_libc']()
for cap in (0, 1, 3, 4):
    launch['_checked'](libc.prctl(24, cap, 0, 0, 0), 'drop setup capability')
launch['_capset'](launch['ENTRY_CAPS'])
launch['_assert_caps'](launch['ENTRY_CAPS'])
supervisor, client = bounded['bootstrap_services'](root, run, bindings, launch, generation=0)
os.environ['TINYASSETS_DATA_DIR'] = str(root)
assert os.getpid() == 1 and os.getuid() == 1001

REQUESTS = []
SSE = [
    ('message_start', {'type': 'message_start', 'message': {
        'id': 'msg_proof', 'type': 'message', 'role': 'assistant', 'model': 'claude-proof',
        'content': [], 'stop_reason': None, 'stop_sequence': None,
        'usage': {'input_tokens': 21, 'output_tokens': 1}}}),
    ('content_block_start', {'type': 'content_block_start', 'index': 0,
                             'content_block': {'type': 'text', 'text': ''}}),
    ('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                             'delta': {'type': 'text_delta', 'text': ANSWER[:13]}}),
    ('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                             'delta': {'type': 'text_delta', 'text': ANSWER[13:]}}),
    ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
    ('message_delta', {'type': 'message_delta', 'delta': {
        'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 9}}),
    ('message_stop', {'type': 'message_stop'}),
]

class FakeAnthropic(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass
    def _reply(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body); self.wfile.flush()
    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length)
        REQUESTS.append((self.command, self.path, self.headers.get('Host'), len(body)))
        if self.path.split('?')[0].endswith('/v1/messages'):
            payload = ''.join(f'event: {name}\ndata: {json.dumps(data)}\n\n'
                              for name, data in SSE).encode()
            return self._reply(200, payload, 'text/event-stream')
        return self._reply(200, b'{}', 'application/json')
    def do_GET(self):
        REQUESTS.append((self.command, self.path, self.headers.get('Host'), 0))
        return self._reply(200, b'{}', 'application/json')

server = ThreadingHTTPServer(('127.0.0.1', PORT), FakeAnthropic)
threading.Thread(target=server.serve_forever, daemon=True).start()

from tinyassets import universe_egress
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.daemon_server import grant_universe_access
from tinyassets.exceptions import ProviderError
from tinyassets.providers import claude_provider, owned_process
from tinyassets.providers.base import ModelConfig
from tinyassets.providers.claude_provider import ClaudeProvider
from tinyassets.providers.provider_jail import provider_launch_scope
from tinyassets import role_provider_execution
from tinyassets.universe_intelligence import _ENGINE_ALLOWED_TOOLS, _ENGINE_DISALLOWED_TOOLS
grant_universe_access(root, universe_id=center.name, actor_id='alice',
                      permission='admin', granted_by='alice')

# The one test-only seam on the daemon side: the proxy's global-address floor
# would refuse this loopback endpoint (that floor has its own proof). The cell
# still reaches the proxy only through its pinned relay; nothing else changes.
real_checked = universe_egress._checked_addresses
def checked(host, port):
    if host == FAKE_HOST and port == PORT:
        return ['127.0.0.1']
    return real_checked(host, port)
universe_egress._checked_addresses = checked

# The daemon-side env builder names no endpoint; point the CLI at the fake one
# and silence its non-essential traffic. Every value still crosses the REAL
# cell filter: the URL only because it is the owner's own sealed value.
real_env = claude_provider.subprocess_env_for_provider
def env_for_provider(*args, **kwargs):
    env = real_env(*args, **kwargs)
    env.update({'ANTHROPIC_BASE_URL': BASE_URL, 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
                'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1',
                'DISABLE_AUTOUPDATER': '1', 'DISABLE_BUG_COMMAND': '1'})
    return env
claude_provider.subprocess_env_for_provider = env_for_provider

LAUNCHES = []
real_spawn = role_provider_execution.spawn
async def recording_spawn(argv, **kwargs):
    LAUNCHES.append(list(argv))
    return await real_spawn(argv, **kwargs)
role_provider_execution.spawn = recording_spawn

CONFIG = ModelConfig(sandbox_workspace=True, sandbox_chat=True,
                     allowed_tools=_ENGINE_ALLOWED_TOOLS,
                     disallowed_tools=_ENGINE_DISALLOWED_TOOLS, engine_mcp_enabled=False,
                     credential_snapshot_dir=snapshot, init_timeout_s=90,
                     first_progress_s=90, idle_timeout_s=90, absolute_cap_s=240)
PIPES = dict(stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
             stderr=asyncio.subprocess.PIPE)

async def diagnose():
    env = env_for_provider('claude-code', universe_dir=center, credential_snapshot_dir=snapshot)
    with provider_launch_scope(center, credential_dir=snapshot):
        proc = await owned_process.aspawn_owned(
            ['/usr/local/bin/python3.11', '-c',
             'import os, json; print(json.dumps(dict(os.environ)))'], env=env, **PIPES)
    out, err = await proc.communicate(b'')
    print('IN-CELL ENV:', out.decode()[:2000], err.decode()[-500:], flush=True)
    with provider_launch_scope(center, credential_dir=snapshot):
        proc = await owned_process.aspawn_owned(
            ['/usr/local/bin/claude', '-p', '--output-format', 'stream-json', '--verbose',
             '--tools', '', '--setting-sources', '', 'say hi'], env=env, **PIPES)
    out, err = await proc.communicate(b'')
    print('RAW CLI exit', proc.returncode, 'stdout:', out.decode()[-3000:], flush=True)
    print('RAW CLI stderr:', err.decode()[-3000:], flush=True)
    print('REQUESTS:', REQUESTS, flush=True)

async def main():
    report = {}
    with identity_context(Identity('alice', 'alice')):
        import logging
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)
        if os.environ.get('PROOF_DIAG'):
            await diagnose()
        try:
            with provider_launch_scope(center, credential_dir=snapshot):
                response = await ClaudeProvider().complete(
                    'Reply with the words you are given.', 'You are a cell proof.', CONFIG,
                    universe_dir=center)
        except Exception as exc:
            print('TURN FAILED:', type(exc).__name__, exc, flush=True)
            print('REQUESTS SEEN BY THE FAKE ENDPOINT:', json.dumps(REQUESTS, indent=1),
                  flush=True)
            raise
        argv = LAUNCHES[-1]
        # The daemon names the bare CLI; the cell resolves it in the image's
        # wrapper directory (role_provider_cell.resolve_executable).
        assert argv[:2] == ['claude', '-p'] and '--output-format' in argv, argv
        assert '--include-partial-messages' in argv and '--permission-mode' in argv, argv
        assert response.text == ANSWER, response.text
        # The reader revokes the cell once the terminal result is in; the CLI
        # either exited first (0) or was ended by the mapper (-9). Both are
        # the reader's verdict, not the stream's.
        assert response.exit_code in (0, -9) and response.ttft_ms is not None, response
        assert response.reported_model == 'claude-proof' and response.input_tokens == 21
        posted = [r for r in REQUESTS
                  if r[0] == 'POST' and r[1].split('?')[0].endswith('/v1/messages')]
        assert posted and all(r[2].startswith(FAKE_HOST) for r in posted), REQUESTS
        report['turn'] = dict(text=response.text, exit_code=response.exit_code,
                              ttft_ms=round(response.ttft_ms),
                              latency_ms=round(response.latency_ms),
                              requests=REQUESTS[:8], argv=argv)

        # Refusal 1: the CLI exits before reading its prompt. On the duplex
        # socket this was ECONNRESET with no reason; on pipes it is the CLI's
        # own stderr and exit code.
        with provider_launch_scope(center, credential_dir=snapshot):
            proc = await owned_process.aspawn_owned(
                ['/usr/local/bin/claude', '--definitely-not-a-flag'],
                env=env_for_provider('claude-code', universe_dir=center,
                                     credential_snapshot_dir=snapshot), **PIPES)
        try:
            await ClaudeProvider()._read_stream(proc, 'prompt ' * 2000, CONFIG)
        except ProviderError as exc:
            message = str(exc)
        else:
            raise AssertionError('an unknown flag was accepted')
        assert 'ConnectionResetError' not in message, message
        assert 'unknown option' in message or 'error' in message.lower(), message
        report['cli_refusal'] = message

        # Refusal 2: the cell itself refuses the launch. The decoder's failure
        # marker (file, line, type, errno; never a byte of the exception) is
        # parsed by the mapper and reaches the daemon as the stop reason.
        with provider_launch_scope(center, credential_dir=snapshot):
            proc = await owned_process.aspawn_owned(['/bin/sh', '-c', 'exit 3'], env={}, **PIPES)
        try:
            await ClaudeProvider()._read_stream(proc, 'prompt', CONFIG)
        except ProviderError as exc:
            message = str(exc)
        else:
            raise AssertionError('a foreign executable was admitted')
        assert 'ConnectionResetError' not in message, message
        assert 'provider cell ended (owner cell: decoder:role_provider_cell.py:' in message, message
        assert ':ValueError:errno=None)' in message, message
        report['cell_refusal'] = message
    print(json.dumps(report, indent=1), flush=True)

asyncio.run(main())
print('PROVIDER TURN PROOF PASS', flush=True)
os._exit(0)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    digest = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    command = ['docker', 'run', '--label', 'tinyassets.disposable=true',
           '--label', f'tinyassets.created-at={int(time.time())}',
           '--rm', '-i', '--network', 'none', '--user', '0:0',
               '--cap-drop', 'ALL']
    for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'FSETID', 'SETUID', 'SETGID', 'SETPCAP',
                'KILL'):
        command += ['--cap-add', cap]
    for option in ('no-new-privileges=true', 'seccomp=unconfined', 'apparmor=unconfined',
                   'systempaths=unconfined'):
        command += ['--security-opt', option]
    if os.environ.get('PROOF_DIAG'):
        command += ['-e', 'PROOF_DIAG=1']
    command += ['--entrypoint', '/opt/venv/bin/python', digest, '-I', '-B', '-']
    print('cutover image:', digest, flush=True)
    return subprocess.run(command, input=CONTAINER, text=True, timeout=900).returncode


if __name__ == '__main__':
    raise SystemExit(main())
