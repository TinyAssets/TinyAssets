"""AST heuristic: literal center file names joined to paths at box and host sinks.

This intentionally catches direct calls and one ``path / 'name'`` expression,
not aliases or dynamic names. Existing mixed names need an explicit reason and
must be removed from KNOWN_MIXED as soon as either use disappears.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tinyassets.boxes.layout import (
    HOST_CENTER_ENTRIES,
    HOST_CENTER_MOVED,
    host_center_entry,
    host_center_moved,
)

ROOT = Path(__file__).resolve().parents[1] / "tinyassets"
BOX_CALLS = {
    "box_read", "box_stat", "box_list", "box_exists", "box_tree_size",
    "read_universe_file", "read_universe_text", "write_universe_file",
    "unlink_universe_file", "read_data_path", "write_data_path",
    "unlink_data_path", "list_universe_dir", "list_universe_entries",
}
HOST_METHODS = {
    "exists", "is_file", "is_dir", "open", "read_text", "read_bytes",
    "write_text", "write_bytes", "unlink", "glob", "stat", "lstat",
    "replace", "mkdir", "rglob", "iterdir",
}
KNOWN_MIXED: dict[str, str] = {
    "log.md": "Existing journal and owner-content paths share this leaf name.",
}


def _strings(node: ast.AST) -> set[str]:
    return {child.value for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)}


def _joined(node: ast.AST) -> set[str]:
    return {child.right.value for child in ast.walk(node)
            if isinstance(child, ast.BinOp) and isinstance(child.op, ast.Div)
            and isinstance(child.right, ast.Constant)
            and isinstance(child.right.value, str)}


def _file_name(name: str) -> bool:
    return ("/" not in name and "\\" not in name and "." in name
            and not name.startswith("..") and " " not in name)


# Migration and deletion own the cutover. The read adapter contains names but no host IO.
EXEMPT = {
    "box_layout_migration.py", "account_deletion.py", "owner_box_reads.py",
}
EXEMPT_PREFIXES = ("okf/",)


def _exempt(relative: str) -> bool:
    return relative in EXEMPT or relative.startswith(EXEMPT_PREFIXES)


def _uses(tree: ast.AST, relative: str) -> tuple[set[str], set[str], list[str]]:
    boxed: set[str] = set()
    hosted: set[str] = set()
    errors = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        call_name = (func.id if isinstance(func, ast.Name)
                     else func.attr if isinstance(func, ast.Attribute) else "")
        args = [*node.args, *(kw.value for kw in node.keywords)]
        if call_name in BOX_CALLS:
            names = set().union(*(_strings(arg) for arg in args))
            boxed.update(filter(_file_name, names))
            errors.extend(f"{relative}:{node.lineno}: host entry routed to box: {name!r}"
                          for name in names if host_center_entry(name.split("/", 1)[0]))
        host_call = (isinstance(func, ast.Attribute) and func.attr in HOST_METHODS
                     or isinstance(func, ast.Name) and func.id == "open")
        root = func
        while isinstance(root, ast.Attribute):
            root = root.value
        host_call |= isinstance(root, ast.Name) and root.id in {"os", "sqlite3"}
        if host_call:
            names = _joined(func)
            for arg in args:
                names.update(_joined(arg))
            hosted.update(filter(_file_name, names))
            if not _exempt(relative):
                errors.extend(f"{relative}:{node.lineno}: host moved path {name!r}"
                              for name in names if host_center_moved(name))
    return boxed, hosted, errors


def test_center_file_names_have_one_home() -> None:
    boxed: set[str] = set()
    hosted: set[str] = set()
    errors: list[str] = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        box, host, bad = _uses(tree, relative)
        boxed.update(box)
        hosted.update(host)
        errors.extend(bad)
    mixed = boxed & hosted
    assert not errors, "\n".join(errors)
    new_mixed = mixed - KNOWN_MIXED.keys()
    assert not new_mixed, f"new mixed file homes: {new_mixed}"
    assert KNOWN_MIXED.keys() <= mixed, f"stale mixed allowlist: {KNOWN_MIXED.keys() - mixed}"


@pytest.mark.parametrize("name", sorted(HOST_CENTER_MOVED))
@pytest.mark.parametrize("operation", [
    '(root / {name}).exists()', '(root / {name}).rglob("*")',
    '(root / {name}).read_bytes()', 'os.stat(root / {name})',
    'os.path.isdir(root / {name})', 'sqlite3.connect(root / {name})',
    'open(root / {name})',
])
def test_moved_name_host_sinks_are_rejected(name, operation):
    assert _uses(ast.parse(operation.format(name=repr(name))), "reader.py")[2]


@pytest.mark.parametrize("name", sorted(HOST_CENTER_ENTRIES))
def test_host_names_are_rejected_at_box_helpers(name):
    source = f"read_universe_text(root, {name!r})"
    assert _uses(ast.parse(source), "reader.py")[2]


@pytest.mark.parametrize("relative", sorted(EXEMPT) + ["okf/seed.py"])
def test_migration_exemptions_are_explicit(relative):
    assert not _uses(ast.parse('(root / "soul.md").stat()'), relative)[2]


def test_layout_patterns_and_disjointness():
    assert HOST_CENTER_ENTRIES.isdisjoint(HOST_CENTER_MOVED)
    assert host_center_entry(".worker_supervisor.worker-42.json")
    assert not host_center_entry(".worker_supervisor..json")
    for name in HOST_CENTER_ENTRIES:
        if name.endswith((".db", ".db.premigration")):
            for suffix in ("-wal", "-shm", "-journal"):
                assert host_center_entry(name + suffix)
    assert not host_center_entry("soul.md")


@pytest.mark.parametrize("helper", [
    "box_read", "box_stat", "box_list", "box_exists", "box_tree_size",
])
def test_owner_box_helpers_enforce_file_homes(helper):
    assert _uses(ast.parse(f'{helper}(root, ".runtime_status.json")'), "reader.py")[2]
    for name in ("dispatcher_config.yaml", "activity.log", "status.json"):
        assert host_center_moved(name)
        assert not host_center_entry(name)
        assert not _uses(ast.parse(f"{helper}(root, {name!r})"), "reader.py")[2]
        assert _uses(ast.parse(f"(root / {name!r}).read_bytes()"), "reader.py")[2]
