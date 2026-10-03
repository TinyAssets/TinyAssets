"""Metadata's explicit owner/snapshot fence and no-host-fallback boundary."""

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tinyassets.exceptions import ProviderError
from tinyassets.providers import native_jsonrpc_discovery as transport
from tinyassets.providers import owned_process, provider_jail
from tinyassets.providers.codex_provider import CodexProvider


def snapshot(universe):
    result = universe / '.runtime' / 'provider-launch-credentials' / 'one'
    result.mkdir(parents=True)
    return result


def test_metadata_view_binds_only_exact_snapshot_and_private_runtime(tmp_path):
    own = snapshot(tmp_path)
    view = provider_jail.metadata_view(tmp_path, own, {'CODEX_HOME': str(own)})
    assert view.mounts == (provider_jail.JailMount('bind', str(own), own),)
    assert view.chdir == str(own)
    assert dict(view.setenv)['HOME'] == '/tmp'
    assert dict(view.setenv)['CODEX_HOME'] == str(own)
    assert dict(view.setenv)['CLAUDE_CONFIG_DIR'].startswith('/tmp/')


@pytest.mark.parametrize('kind', ['none', 'root', 'parent', 'foreign', 'missing', 'symlink'])
def test_bad_metadata_owner_or_snapshot_never_spawns(tmp_path, monkeypatch, kind):
    universe = tmp_path / 'owned'
    own = snapshot(universe)
    other = snapshot(tmp_path / 'foreign')
    if kind == 'none':
        universe = None
    elif kind == 'root':
        own = universe
    elif kind == 'parent':
        own = own.parent
    elif kind == 'foreign':
        own = other
    elif kind == 'missing':
        own = own.parent / 'absent'
    elif kind == 'symlink':
        link = own.parent / 'redirected'
        link.symlink_to(own, target_is_directory=True)
        own = link
    spawn = AsyncMock(side_effect=AssertionError('must refuse before process creation'))
    monkeypatch.setattr(transport, 'aspawn_owned', spawn)
    with pytest.raises(ProviderError, match='^native model discovery unavailable$'):
        asyncio.run(transport.read_native_catalogue(
            ['synthetic'], protocol=CodexProvider.native_discovery_protocol,
            env={}, cwd=str(own), universe_dir=universe,
        ))
    spawn.assert_not_awaited()


@pytest.mark.parametrize('failure', ['unavailable', 'unbound'])
def test_no_jail_never_reaches_host_spawn(tmp_path, monkeypatch, failure):
    own = snapshot(tmp_path)
    spawn = AsyncMock(side_effect=AssertionError('unconfined fallback'))
    monkeypatch.setattr(owned_process, '_aspawn_anchored', spawn)
    if failure == 'unavailable':
        def refuse():
            raise provider_jail.ProviderConfinementError('private path/token must not escape')
        monkeypatch.setattr(provider_jail, 'BWRAP_RESOLVER', refuse)
    else:
        # Even losing BOTH scope and explicit-view recognition cannot activate
        # the generic launcher's legitimate non-provider process fallback.
        monkeypatch.setattr(provider_jail, 'confine_launch', lambda *a, **k: None)
    with pytest.raises(ProviderError, match='^native model discovery unavailable$') as error:
        asyncio.run(transport.read_native_catalogue(
            ['synthetic'], protocol=CodexProvider.native_discovery_protocol,
            env={}, cwd=str(own), universe_dir=tmp_path,
        ))
    spawn.assert_not_awaited()
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def test_metadata_replaces_ambient_scope_and_never_inherits_engine_route(tmp_path, monkeypatch):
    own = snapshot(tmp_path)
    seen = []

    async def inspect(argv, **kwargs):
        seen.append(provider_jail._SCOPE.get())
        assert kwargs['require_confinement'] is True
        assert kwargs['universe_view'].universe_dir == tmp_path
        assert kwargs['universe_view'].mounts[0].source == own
        raise provider_jail.ProviderConfinementError('synthetic refusal')

    monkeypatch.setattr(transport, 'aspawn_owned', inspect)
    with provider_jail.provider_launch_scope(tmp_path / 'foreign', engine_route=('other', 'other')):
        before = provider_jail._SCOPE.get()
        with pytest.raises(ProviderError, match='^native model discovery unavailable$'):
            asyncio.run(transport.read_native_catalogue(
                ['synthetic'], protocol=CodexProvider.native_discovery_protocol,
                env={}, cwd=str(own), universe_dir=tmp_path,
            ))
        assert provider_jail._SCOPE.get() is before
    assert len(seen) == 1
    assert seen[0].universe_dir == tmp_path
    assert seen[0].credential_dir == own
    assert seen[0].engine_route is None


def test_provider_async_spawn_sites_remain_in_owned_process():
    # The pre-existing synchronous developer authentication probe in base is
    # outside this metadata repair. All asynchronous provider process creation
    # belongs to the shared launcher, including Windows teardown's taskkill.
    root = Path(transport.__file__).parent
    violations = []
    for source in root.glob('*.py'):
        if source.name == 'owned_process.py':
            continue
        tree = ast.parse(source.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {'create_subprocess_exec', 'create_subprocess_shell'}:
                    violations.append((source.name, node.lineno))
    assert violations == []


@pytest.mark.parametrize('error_type', [owned_process.FamilyAnchorError, OSError])
def test_metadata_launch_errors_are_sanitized(tmp_path, monkeypatch, error_type):
    own = snapshot(tmp_path)
    monkeypatch.setattr(transport, 'aspawn_owned', AsyncMock(
        side_effect=error_type('secret path must not escape'),
    ))
    with pytest.raises(ProviderError, match='^native model discovery unavailable$') as error:
        asyncio.run(transport.read_native_catalogue(
            ['synthetic'], protocol=CodexProvider.native_discovery_protocol,
            env={}, cwd=str(own), universe_dir=tmp_path,
        ))
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


@pytest.mark.parametrize("loop", ["snapshot", "parent", "owner"])
def test_metadata_symlink_loops_refuse_without_leaking_private_path(tmp_path, monkeypatch, loop):
    universe = tmp_path / "private-owner-marker"
    own = snapshot(universe)
    own.rmdir()
    if loop == "snapshot":
        own.symlink_to(own.name, target_is_directory=True)
    elif loop == "parent":
        own.parent.rmdir()
        own.parent.symlink_to(own.parent.name, target_is_directory=True)
    else:
        own.parent.rmdir()
        own.parent.parent.rmdir()
        universe.rmdir()
        universe.symlink_to(universe.name, target_is_directory=True)
    spawn = AsyncMock(side_effect=AssertionError("must refuse before process creation"))
    monkeypatch.setattr(transport, "aspawn_owned", spawn)
    with pytest.raises(ProviderError, match="^native model discovery unavailable$") as error:
        asyncio.run(transport.read_native_catalogue(
            ["synthetic"], protocol=CodexProvider.native_discovery_protocol,
            env={}, cwd=str(own), universe_dir=universe,
        ))
    spawn.assert_not_awaited()
    assert error.value.__cause__ is None and error.value.__suppress_context__
