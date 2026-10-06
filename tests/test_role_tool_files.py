"""Owner-file preparation and recovery: preserve custody and foreign inodes."""
import os
import stat

import pytest

from tinyassets.role_tool_files import maintain


def call(root, agent='main'):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return maintain(fd, agent_id=agent)
    finally:
        os.close(fd)


def test_first_use_and_restrictive_tree_recovery(tmp_path):
    call(tmp_path)
    work = tmp_path / '.agent-workspace'
    nested = work / 'nested'
    nested.mkdir()
    data = nested / 'data'
    data.write_bytes(b'preserved')
    data.chmod(0)
    nested.chmod(0)
    call(tmp_path)
    assert data.read_bytes() == b'preserved'
    assert stat.S_IMODE(data.stat().st_mode) == 0o660
    assert stat.S_IMODE(nested.stat().st_mode) == 0o770
    assert (tmp_path / 'notes').is_dir()


def test_promotion_preserves_source_and_never_replaces_canonical_file(tmp_path):
    call(tmp_path)
    source = tmp_path / '.agent-workspace/MEMORY.md'
    source.write_bytes(b'exact\x00bytes\r\n')
    assert call(tmp_path)['promoted'] == ['MEMORY.md']
    assert (tmp_path / 'MEMORY.md').read_bytes() == source.read_bytes()
    source.write_bytes(b'later workspace copy')
    assert call(tmp_path)['promoted'] == []
    assert (tmp_path / 'MEMORY.md').read_bytes() == b'exact\x00bytes\r\n'


def test_secondary_agent_cannot_promote_main_identity(tmp_path):
    call(tmp_path)
    (tmp_path / '.agent-workspace/identity.md').write_text('private identity')
    call(tmp_path, 'secondary')
    assert not (tmp_path / 'identity.md').exists()
    call(tmp_path)
    assert (tmp_path / 'identity.md').read_text() == 'private identity'


def test_foreign_and_hardlinked_inodes_are_never_remoded(tmp_path, monkeypatch):
    call(tmp_path)
    work = tmp_path / '.agent-workspace'
    foreign = work / 'foreign'
    foreign.write_bytes(b'foreign bytes')
    if os.getuid() == 0:
        os.chown(foreign, 300002, 300002)
    else:
        # Portable unit coverage for the same identity gate; the root oracle
        # above and production probe exercise real foreign kernel identities.
        from types import SimpleNamespace

        original = os.fstat
        inode = foreign.stat().st_ino

        def foreign_stat(fd):
            info = original(fd)
            if info.st_ino == inode:
                return SimpleNamespace(st_uid=os.getuid() + 1, st_gid=os.getgid() + 1,
                                       st_mode=info.st_mode, st_nlink=info.st_nlink)
            return info

        monkeypatch.setattr(os, 'fstat', foreign_stat)
    foreign.chmod(0o600)
    hardlink = work / 'hardlink'
    os.link(foreign, hardlink)
    own = work / 'own'
    own.write_bytes(b'own linked bytes')
    own.chmod(0o400)
    os.link(own, work / 'alias')
    os.symlink(foreign, work / 'symlink')
    os.mkfifo(work / 'fifo')
    before = foreign.stat()
    call(tmp_path)
    after = foreign.stat()
    assert (after.st_uid, after.st_gid, after.st_mode, after.st_ctime_ns) == (
        before.st_uid, before.st_gid, before.st_mode, before.st_ctime_ns)
    assert stat.S_IMODE(own.stat().st_mode) == 0o400


def test_required_directory_alias_refuses_and_oversize_brain_stays_editable(tmp_path):
    (tmp_path / 'notes').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(PermissionError, match='directory'):
        call(tmp_path)
    (tmp_path / 'notes').unlink()
    call(tmp_path)
    (tmp_path / '.agent-workspace/MEMORY.md').write_bytes(b'x' * (1024 * 1024 + 1))
    assert call(tmp_path)['skipped'] == ['MEMORY.md']
    assert not (tmp_path / 'MEMORY.md').exists()
    assert (tmp_path / '.agent-workspace/MEMORY.md').is_file()


def test_selected_workspace_creator_requires_owner_cell(monkeypatch, tmp_path):
    from tinyassets import role_decoder, universe_tools
    from tinyassets.providers.provider_jail import ensure_agent_workspace

    monkeypatch.setenv('TINYASSETS_CREDENTIAL_BROKER', 'process')
    monkeypatch.setattr(role_decoder, '_bounded_client', None)
    with pytest.raises(universe_tools.UniverseToolError, match='bounded owner launcher'):
        ensure_agent_workspace(tmp_path)
    assert not (tmp_path / '.agent-workspace').exists()


def test_cell_final_accounting_preserves_force_contract(monkeypatch):
    import io
    import json
    from dataclasses import asdict
    from types import SimpleNamespace

    from tinyassets import role_tools, universe_tools

    request = dict(inner=['/bin/true'], agent_id='main', stdin=None,
                   limits=asdict(universe_tools.DEFAULT_LIMITS), wall=1, cap=1024)
    incoming = io.BytesIO(json.dumps(request).encode() + b'\n{"breach":null}\n')
    outgoing = io.BytesIO()
    monkeypatch.setattr(role_tools.sys, 'stdin', SimpleNamespace(buffer=incoming))
    monkeypatch.setattr(role_tools.sys, 'stdout', SimpleNamespace(buffer=outgoing))
    monkeypatch.setattr(universe_tools, '_seccomp_fd', lambda: os.open('/dev/null', os.O_RDONLY))
    monkeypatch.setattr(universe_tools, '_limited', lambda *args, **kw: ['/bin/true'])
    monkeypatch.setattr(universe_tools, 'tool_jail_argv', lambda *args, **kw: ['/bin/true'])

    def supervise(*args, budget, **kwargs):
        assert budget.breach(force=True) is None
        return universe_tools.ToolRun(exit_code=0, output=b'ok', killed=None, elapsed=0)

    monkeypatch.setattr(universe_tools, '_supervise', supervise)
    assert role_tools.cell_main() == 0
    frames = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert frames[0] == {'budget': True, 'force': True}
    assert frames[1]['result']['exit_code'] == 0
