"""Raw creation regression guard complements the real split-UID image probe.

The existing universe I/O ratchet does not count mkdir or scan domain modules.
These content-facing functions must delegate creation, including their parents.
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTENT_MODULES = (
    'tinyassets/soul_edit.py',
    'tinyassets/universe_soul.py',
    'tinyassets/universe_bundle.py',
    'tinyassets/ingestion/core.py',
    'tinyassets/ingestion/canon_io.py',
    'tinyassets/executors/node_bid.py',
    'domains/fantasy_daemon/phases/_activity.py',
    'domains/fantasy_daemon/phases/commit.py',
    'domains/fantasy_daemon/phases/reflect.py',
    'domains/fantasy_daemon/phases/worldbuild.py',
)
CREATES = {'mkdir', 'makedirs', 'write_text', 'write_bytes', 'touch',
           'copy', 'copy2', 'copyfile', 'copytree', 'symlink', 'link'}


def raw_creates(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr in {'rename', 'replace'}:
            # String replacement is not filesystem publication.
            if (function.attr == 'rename' or len(node.args) < 2
                    or isinstance(function.value, ast.Name) and function.value.id == 'os'):
                yield node.lineno, function.attr
        if isinstance(function, ast.Attribute) and function.attr in CREATES:
            yield node.lineno, function.attr
        if ((isinstance(function, ast.Name) and function.id == 'open') or
                (isinstance(function, ast.Attribute) and function.attr == 'open')):
            if (isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name)
                    and function.value.id == 'os'):
                if any(isinstance(flag, ast.Attribute) and flag.attr in {'O_CREAT', 'O_TMPFILE'}
                       for flag in ast.walk(node)):
                    yield node.lineno, 'os.open creation'
                continue
            modes = [*node.args, *(kw.value for kw in node.keywords if kw.arg == 'mode')]
            if any(isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                   and set(mode.value) <= set('rwax+bt')
                   and set(mode.value) & set('wax+') for mode in modes):
                yield node.lineno, 'open for writing'


@pytest.mark.parametrize('source', [
    'path.open("a")', 'path.open(mode="w")', 'open(path, "x")',
    'os.open(path, os.O_WRONLY | os.O_CREAT)', 'path.rename(destination)',
    'os.replace(source, destination)', 'path.replace(destination)',
])
def test_raw_creation_guard_catches_alternate_write_forms(source):
    assert list(raw_creates(ast.parse(source)))


@pytest.mark.parametrize('filename', CONTENT_MODULES)
def test_content_writers_delegate_all_entry_creation(filename):
    tree = ast.parse((ROOT / filename).read_text(encoding='utf-8'))
    assert list(raw_creates(tree)) == []


def test_canon_request_never_precreates_owner_directory():
    tree = ast.parse((ROOT / 'tinyassets/api/universe.py').read_text(encoding='utf-8'))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == '_action_add_canon')
    assert list(raw_creates(function)) == []


def test_missing_soul_history_is_created_only_by_content_writer(tmp_path, monkeypatch):
    from tinyassets import soul_edit, universe_files

    calls = []
    original = universe_files.write_data_path

    def write(path, payload, **kwargs):
        calls.append((Path(path).name, kwargs.get('mode', 'replace')))
        if len(calls) == 1:
            assert not (tmp_path / 'soul_versions').exists()
        return original(path, payload, **kwargs)

    monkeypatch.setattr(universe_files, 'write_data_path', write)
    soul_edit._write_edit_snapshot(tmp_path, files={'identity.md': 'owner content'},
        source='test', context='missing history', summary='new history', stamp='now')
    assert calls == [('0001.md', 'exclusive'), ('index.md', 'replace')]
    assert 'owner content' in (tmp_path / 'soul_versions/0001.md').read_text(encoding='utf-8')
