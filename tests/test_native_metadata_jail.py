"""Real OS proof for shipping metadata enumeration; synthetic bytes only."""

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tinyassets.providers import base, provider_jail
from tinyassets.providers.codex_provider import CodexProvider


@pytest.mark.real_jail
@pytest.mark.skipif(sys.platform != 'linux' or not shutil.which('bwrap'),
                    reason='real metadata isolation requires Linux and bubblewrap')
def test_native_metadata_snapshot_cannot_read_foreign_or_platform_state(monkeypatch):
    import tinyassets

    root = Path(tempfile.mkdtemp(prefix='ta-metadata-jail-', dir='/tmp'))
    sentinel = None
    try:
        universe = root / 'owner-a'
        own = universe / '.runtime' / 'provider-launch-credentials' / 'own'
        own.mkdir(parents=True)
        sibling = own.parent / 'other-launch'
        sibling.mkdir()
        (own / 'auth.json').write_text('{"note":"synthetic-own-credential"}')
        (own / 'config.toml').write_text('cli_auth_credentials_store = "file"\n')
        (sibling / 'auth.json').write_text('synthetic-sibling-credential')
        foreign = root / 'owner-b'
        foreign.mkdir()
        (foreign / 'private.txt').write_text('synthetic-foreign-data')
        (universe / '.credential-vault.json').write_text('synthetic-vault-secret')
        (universe / 'notes.txt').write_text('owner content unnecessary for metadata')
        source = Path(tinyassets.__file__).resolve()
        marker = 'TA_SYNTHETIC_METADATA_HOST_SECRET'
        sentinel = subprocess.Popen(
            ['/bin/sleep', '60'], env={'PATH': '/usr/bin:/bin', marker: 'synthetic-secret'},
        )
        forbidden_paths = list(map(str, [
            sibling / 'auth.json', foreign / 'private.txt', source,
            universe / '.credential-vault.json', universe / 'notes.txt',
        ]))
        script = f'''
import json, os, sys
from pathlib import Path
snapshot = Path({str(own)!r})
assert Path.cwd() == snapshot
assert 'synthetic-own-credential' in (snapshot / 'auth.json').read_text()
assert Path(os.environ['CODEX_HOME']) == snapshot
home = Path(os.environ['HOME'])
assert str(home) == '/tmp'
(home / 'metadata-positive-control').write_text('private scratch works')
for forbidden in {forbidden_paths!r}:
    try:
        Path(forbidden).read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        pass
    else:
        raise AssertionError('metadata read forbidden state')
for process in Path('/proc').glob('[0-9]*/environ'):
    try:
        data = process.read_bytes()
    except OSError:
        continue
    assert {marker.encode()!r} not in data
for line in sys.stdin:
    request = json.loads(line)
    if request['method'] == 'initialized':
        continue
    if request['method'] == 'initialize':
        result = {{'ready': True}}
    else:
        assert request['method'] == 'model/list'
        result = {{'data': [{{'model': 'synthetic-confined-metadata', 'isDefault': True,
                            'inputModalities': ['text']}}]}}
    print(json.dumps({{'id': request['id'], 'result': result}}), flush=True)
'''
        monkeypatch.setattr(CodexProvider, 'native_command_resolver', staticmethod(
            lambda: ([sys.executable, '-u', '-c', script], False),
        ))
        # An unrelated outer inference context must not lend its engine route.
        base._sandbox_probe_cache = None
        with provider_jail.provider_launch_scope(foreign, engine_route=('other', 'owner-b')):
            result = asyncio.run(CodexProvider().enumerate_models(
                universe_dir=universe, credential_snapshot_dir=own,
            ))
        assert result.default_model_id == 'synthetic-confined-metadata'
        assert len(result.models) == 1
        assert (own / 'auth.json').read_text() == '{"note":"synthetic-own-credential"}'
        assert not (root / 'metadata-positive-control').exists()
    finally:
        if sentinel is not None:
            sentinel.kill()
            sentinel.wait(timeout=10)
        base._sandbox_probe_cache = None
        shutil.rmtree(root)
