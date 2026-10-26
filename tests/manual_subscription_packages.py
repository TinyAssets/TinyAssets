"""Opt-in public-registry smoke; never approves an account login.

python scripts/linux_oracle.py --out <cache-directory> -- tests/manual_subscription_packages.py -q

Uses the production Node version in a temporary directory, never changes an image.
Not named test_*: ordinary offline tests do not contact real model providers.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.broker_ipc import broker  # noqa: F401
from tests.test_owner_public_egress import native  # noqa: F401


@pytest.fixture(scope='session')
def node_runtime():
    prefix = Path('/out/node-runtime')
    binary = prefix / 'node_modules/.bin/node'
    if not binary.exists():
        subprocess.run(['npm','--prefix',str(prefix),'install','--no-audit','--no-fund',
                        'node@22.23.3'], check=True, capture_output=True, timeout=180)
    assert subprocess.check_output([str(binary),'--version']).strip() == b'v22.23.3'
    return str(binary.parent)

@pytest.mark.parametrize('source', ['claude-code','codex','gemini','kimi'])
def test_actual_package_install(native, source, node_runtime, monkeypatch):  # noqa: F811
    from tinyassets.owner_process import OwnerProcessCancelled
    from tinyassets.providers.subscription_runtime import Login, SubscriptionRuntime
    monkeypatch.setenv('PATH', node_runtime + ':' + os.environ['PATH'])
    runtime = SubscriptionRuntime(source)
    cache = Path('/out/packages') / source
    if cache.exists():
        shutil.copytree(cache, runtime.directory, dirs_exist_ok=True)
    run = runtime.run
    def logged(*args, **kwargs):
        result = run(*args, **kwargs)
        assert result.returncode == 0, (result.stdout.decode(), result.stderr.decode())
        return result
    runtime.run = logged
    runtime.install()
    if not cache.exists():
        shutil.copytree(runtime.directory, cache, dirs_exist_ok=True, symlinks=True)
    assert runtime.binary.is_file()
    assert not runtime.authenticated()
    login = Login(runtime)
    try:
        result = run(runtime.argv(runtime.source['login']['argv']), phase='login', timeout=50,
                     input_source=login.input, output_handler=login.observe,
                     terminal=runtime.source['login'].get('terminal', False),
                     should_cancel=lambda: bool(login.url))
    except OwnerProcessCancelled:
        pass
    except Exception as error:
        pytest.fail(type(error).__name__ + ": " + login._output)
    else:
        assert login.url, result.stdout.decode()
    assert login.url.startswith('https://'), login.view()
