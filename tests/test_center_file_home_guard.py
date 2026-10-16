"""AST heuristic: literal center file names joined to paths at box and host sinks.

This intentionally catches direct calls and one ``path / 'name'`` expression,
not aliases or dynamic names. Existing mixed names need an explicit reason and
must be removed from KNOWN_MIXED as soon as either use disappears.
"""

from __future__ import annotations

import ast
import fnmatch
from pathlib import Path

from tinyassets.center_platform_files import CENTER_FILE_HOMES

ROOT = Path(__file__).resolve().parents[1] / "tinyassets"
BOX_CALLS = {
    "read_universe_file", "read_universe_text", "write_universe_file",
    "unlink_universe_file", "read_data_path", "write_data_path",
    "unlink_data_path", "list_universe_dir", "list_universe_entries",
}
HOST_METHODS = {
    "exists", "is_file", "is_dir", "open", "read_text", "read_bytes",
    "write_text", "write_bytes", "unlink", "glob", "stat", "lstat",
    "replace", "mkdir",
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


def _host_name(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern.replace("<id>", "*"))
               for pattern, home in CENTER_FILE_HOMES.items() if home == "host")


def test_center_file_names_have_one_home() -> None:
    boxed: set[str] = set()
    hosted: set[str] = set()
    errors: list[str] = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if relative != "center_platform_files.py" and _host_name(node.value):
                    errors.append(f"{relative}:{node.lineno}: host name literal {node.value!r}")
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in BOX_CALLS:
                boxed.update(filter(_file_name, set().union(*(
                    _strings(arg) for arg in node.args))))
            elif isinstance(func, ast.Attribute) and func.attr in BOX_CALLS:
                boxed.update(filter(_file_name, set().union(*(
                    _strings(arg) for arg in node.args))))
            if isinstance(func, ast.Attribute) and func.attr in HOST_METHODS:
                names = _joined(func.value)
                for arg in node.args:
                    names.update(_joined(arg))
                hosted.update(filter(_file_name, names))
                if relative != "universe_soul.py" and "soul.md" in names:
                    errors.append(f"{relative}:{node.lineno}: host soul path")
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in {"os", "sqlite3"}:
                    names = set().union(*(_joined(arg) for arg in node.args))
                    hosted.update(filter(_file_name, names))
    mixed = boxed & hosted
    assert not errors, "\n".join(errors)
    new_mixed = mixed - KNOWN_MIXED.keys()
    assert not new_mixed, f"new mixed file homes: {new_mixed}"
    assert KNOWN_MIXED.keys() <= mixed, f"stale mixed allowlist: {KNOWN_MIXED.keys() - mixed}"
