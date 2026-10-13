"""The fastmcp bound must be the same in every place that declares it.

On 2026-08-31 the canonical `pyproject.toml` carried `fastmcp>=3.0` while the
plugin runtime carried `fastmcp>=3.2,<4`. pip resolved the canonical one to
fastmcp 4.0.0 / mcp 2.1.1, which dropped `request_ctx` from
`mcp.server.lowlevel.server`. Seven tests failed with ImportError and
`required-tests` went red on every open PR at once -- a repo-wide merge stop
caused by a dependency release, with no commit touching the broken code.

Production was on 3.4.7 and kept serving, but `tinyassets/auth/middleware.py`
and `tinyassets/universe_server.py` both import that name on the LIVE MCP path,
so a container rebuild would have taken the public connector down (Hard Rule
11).

The bound is only as good as its weakest declaration, so this asserts they
agree rather than asserting one magic string: a future upgrade moves them
together or fails here.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CANONICAL = REPO / "pyproject.toml"
RUNTIME = (
    REPO
    / "packaging/claude-plugin/runtime-src/pyproject.toml"
)
REQUIREMENTS = (
    REPO
    / "packaging/claude-plugin/runtime-src/requirements.txt"
)

_SPEC = re.compile(r"^fastmcp\s*(?P<spec>[<>=!,.\d\s]+)$")


def _specs(path: Path) -> set[str]:
    """Every fastmcp constraint declared in a pyproject, from any dep table."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    found: set[str] = set()
    candidates = list(project.get("dependencies", []) or [])
    for extra in (project.get("optional-dependencies", {}) or {}).values():
        candidates.extend(extra or [])
    for entry in candidates:
        match = _SPEC.match(str(entry).strip())
        if match:
            found.add(match.group("spec").replace(" ", ""))
    return found


def test_the_canonical_package_declares_a_fastmcp_bound() -> None:
    specs = _specs(CANONICAL)
    assert specs, "no fastmcp constraint found in the canonical pyproject"


def test_every_canonical_declaration_agrees_with_itself() -> None:
    """Two tables declare it; a bound that differs between them is a bound only
    one install path respects."""
    specs = _specs(CANONICAL)
    assert len(specs) == 1, f"canonical pyproject declares fastmcp {sorted(specs)}"


def test_the_runtime_and_the_canonical_package_agree() -> None:
    canonical = _specs(CANONICAL)
    runtime = _specs(RUNTIME)
    assert runtime, "no fastmcp constraint in the plugin runtime pyproject"
    assert canonical == runtime, (
        "the canonical package and the plugin runtime disagree about fastmcp: "
        f"{sorted(canonical)} vs {sorted(runtime)}. They install the same code; "
        "a split bound means one of them resolves to a version the other has "
        "never run."
    )


def test_the_requirements_file_agrees_too() -> None:
    lines = [
        line.strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("fastmcp")
    ]
    assert lines, "no fastmcp line in the runtime requirements"
    declared = {line.replace("fastmcp", "", 1).replace(" ", "") for line in lines}
    assert declared == _specs(CANONICAL), (
        f"requirements.txt declares fastmcp {sorted(declared)} but the "
        f"canonical pyproject declares {sorted(_specs(CANONICAL))}"
    )


def test_the_bound_excludes_the_major_that_removed_request_ctx() -> None:
    """Named explicitly, because this is the fact the bound exists for.

    If someone later ports the `request_ctx` call sites and moves to 4.x, this
    test should be UPDATED with the new evidence -- not deleted for being in
    the way.
    """
    spec = next(iter(_specs(CANONICAL)))
    assert "<4" in spec, (
        "fastmcp 4.0.0 removes `request_ctx` from mcp.server.lowlevel.server, "
        "which auth/middleware.py and universe_server.py import on the live "
        f"MCP path; the declared bound {spec!r} does not exclude it"
    )


def test_the_call_sites_this_bound_protects_still_exist() -> None:
    """A pin guarding an import nobody makes any more is just debt.

    If this fails, the port happened: check whether the bound can be lifted
    rather than re-adding the import.
    """
    hits = [
        path.relative_to(REPO).as_posix()
        for path in (REPO / "tinyassets").rglob("*.py")
        if "request_ctx" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits, (
        "nothing imports request_ctx any more - the fastmcp <4 bound may no "
        "longer be needed; re-check before deleting it"
    )
