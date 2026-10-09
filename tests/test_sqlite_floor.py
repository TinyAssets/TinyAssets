"""The serving image runs SQLite >= 3.51.3 (target-architecture S1a.1).

Production ran Debian trixie's 3.46.1, which predates the WAL-reset corruption
fix in 3.51.3 (docs/concerns/2026-10-02-sqlite-predates-wal-reset-fix.md).
Litestream replicates the WAL, so the floor has to hold before it is switched
on. Three layers, each tested here: the Dockerfile builds the pinned
amalgamation, the image build fails unless Python loads it, and the container
entry point refuses to serve below the floor.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tinyassets.sqlite_floor import MIN_SQLITE_VERSION, require_sqlite_floor

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = (REPO / "Dockerfile").read_text(encoding="utf-8")


@pytest.mark.parametrize("version", [(3, 46, 1), (3, 51, 2), (3, 40, 0), (2, 99, 99)])
def test_a_library_below_the_floor_refuses(version):
    with pytest.raises(RuntimeError, match="3.51.3"):
        require_sqlite_floor(version)


@pytest.mark.parametrize("version", [(3, 51, 3), (3, 53, 4), (4, 0, 0)])
def test_the_floor_and_above_pass(version):
    require_sqlite_floor(version)


def test_the_floor_is_the_wal_reset_fix():
    assert MIN_SQLITE_VERSION == (3, 51, 3)


def test_the_entry_point_checks_before_the_server_loads():
    """The container CMD is the PID1 launcher (deploy/role_launcher.py). Its
    main() must assert the floor before universe_server is imported, so no
    storage is opened by an older library."""
    tree = ast.parse((REPO / "deploy" / "role_launcher.py").read_text(encoding="utf-8"))
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    calls = [ast.unparse(stmt) for stmt in main.body]
    floor = next(i for i, c in enumerate(calls) if c == "require_sqlite_floor()")
    server = next(i for i, c in enumerate(calls) if "tinyassets.universe_server" in c)
    assert floor < server
    command = '["/opt/venv/bin/python", "-I", "-B", "/usr/local/libexec/ta-launch.py"]'
    assert f"CMD {command}" in DOCKERFILE


def _arg(name: str) -> str:
    match = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE, re.M)
    assert match, f"Dockerfile no longer pins {name}"
    return match.group(1)


def test_the_dockerfile_pins_the_amalgamation_by_checksum():
    version = _arg("SQLITE_AUTOCONF_VERSION")
    assert re.fullmatch(r"\d{7}", version)
    major, minor, patch = int(version[0]), int(version[1:3]), int(version[3:5])
    assert (major, minor, patch) >= MIN_SQLITE_VERSION
    assert re.fullmatch(r"[0-9a-f]{64}", _arg("SQLITE_AUTOCONF_SHA256"))
    build = DOCKERFILE.split("SQLITE_AUTOCONF_SHA256=", 1)[1].split("make install")[0]
    assert "sha256sum -c -" in build


@pytest.mark.parametrize("option", [
    # Options that change behaviour relative to the Debian build prod ran on.
    "SQLITE_ENABLE_FTS5",                 # daemon_brain uses fts5
    "SQLITE_ENABLE_FTS3", "SQLITE_ENABLE_FTS4", "SQLITE_ENABLE_RTREE",
    "SQLITE_DEFAULT_RECURSIVE_TRIGGERS=1",
    "SQLITE_MAX_VARIABLE_NUMBER=250000",
    "SQLITE_ENABLE_MATH_FUNCTIONS", "SQLITE_ENABLE_COLUMN_METADATA",
    "SQLITE_SECURE_DELETE", "SQLITE_LIKE_DOESNT_MATCH_BLOBS",
])
def test_the_build_keeps_the_debian_compile_options(option):
    assert f"-D{option}" in DOCKERFILE


def test_the_image_build_fails_unless_python_loads_the_pinned_library():
    runtime = DOCKERFILE.split("COPY --from=builder /opt/sqlite/lib/", 1)[1]
    runtime = runtime.split("COPY --from=builder /tmp/ta-op", 1)[0]
    assert "/usr/local/lib/" in runtime
    assert "ldconfig" in runtime
    assert "v >= (3, 51, 3)" in runtime and "sys.exit(" in runtime
    assert "using fts5" in runtime


def test_the_default_is_the_loaded_library_not_a_constant():
    """Every other test passes an explicit version; this pins that a bare call
    checks what is actually loaded (Codex on #4269)."""
    import sqlite3

    assert require_sqlite_floor.__defaults__ == (sqlite3.sqlite_version_info,)
