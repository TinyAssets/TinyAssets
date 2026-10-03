from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_ROOT = _REPO_ROOT / "tinyassets"


def _imports_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_authority_core_cannot_import_operational_adapters() -> None:
    forbidden = (
        "tinyassets.api",
        "tinyassets.bid",
        "tinyassets.branch_tasks",
        "tinyassets.credentials",
        "tinyassets.daemon_registry",
        "tinyassets.effectors",
        "tinyassets.graph",
        "tinyassets.providers",
        "tinyassets.sandbox",
        "tinyassets.storage",
    )
    offenders: list[str] = []
    authority_root = _PACKAGE_ROOT / "execution_authority"
    if not authority_root.is_dir():
        pytest.fail("D0 execution_authority package is missing")
    for path in authority_root.rglob("*.py"):
        for imported in _imports_in(path):
            if imported.startswith(forbidden):
                offenders.append(f"{path.name}: {imported}")
    assert offenders == []
