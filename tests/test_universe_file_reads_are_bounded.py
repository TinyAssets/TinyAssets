"""Enforcement: the daemon never reads a universe file except through the safe reader.

Since harness S1 the universe agent writes its own folder, so every file under
a universe dir is untrusted input to the daemon that reads it from OUTSIDE the
jail. Three review findings in a row were the same class -- a raw
``read_text`` / ``yaml.safe_load`` of such a file with no bound and no
no-follow (the persona bundle, ``skills/*/SKILL.md``, then ``config.yaml``, the
soul-edit frontmatter and ``soul_versions/``). This test makes the class
unrepeatable in the modules on the daemon's turn path: any raw file read or
YAML/JSON file load there fails, unless it is listed below with the reason it
cannot touch an agent-writable universe file.

The sanctioned route is :mod:`tinyassets.universe_files` (``read_universe_text``,
``read_universe_file``, ``list_universe_dir``, ``load_untrusted_yaml``): no link
followed at any component, regular files only, an explicit byte bound, and a
YAML loader that refuses anchors/aliases -- all BEFORE any parse.

Which modules, and why (enumerated by grep, 2026-09-24): every module that
touches a universe dir AND reads a file (``grep -l universe_dir|udir`` crossed
with ``read_text|read_bytes|open(|yaml.*load``). The served turn and the
agent's own engine tools run the ones in ``TURN_PATH``. The rest read only
platform-owned paths the tool jail masks or mounts read-only (hidden root
entries such as ``.credential-vault.json`` / ``.runtime``; ``config.yaml``,
``work_targets.json``, ``soul.md``) and are listed in
``docs/concerns/2026-09-24-universe-file-readers-outside-the-turn-path.md``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

#: The daemon turn path and the agent's own engine tools.
TURN_PATH = (
    "tinyassets/universe_intelligence.py",
    "tinyassets/universe_soul.py",
    "tinyassets/universe_self_model.py",
    "tinyassets/persona.py",
    "tinyassets/soul_edit.py",
    "tinyassets/config.py",
    "tinyassets/universe_tools.py",
    "tinyassets/engine_mcp_server.py",
    # The agent writes its own MEMORY.md (harness D7a): every reader of it.
    "tinyassets/memory_items.py",
    "tinyassets/harness_history.py",
    # The agent writes its own wiki (harness W): every wiki reader.
    "tinyassets/api/helpers.py",
    "tinyassets/api/wiki.py",
    "tinyassets/effectors/wiki_write_back.py",
    "tinyassets/wiki/okf_export.py",
)

#: (module, enclosing function) -> why this read can never be of an
#: agent-writable universe file. Adding a row needs a reason a reviewer can
#: check; a row that stops matching fails the test (no dead exemptions).
ALLOWED = {
    ("tinyassets/universe_tools.py", "_root_cgroup"):
        "reads /sys/fs/cgroup controller lists, not a universe path",
    ("tinyassets/universe_tools.py", "_remove_cgroup"):
        "reads /sys/fs/cgroup/<jail>/cgroup.procs, not a universe path",
    ("tinyassets/universe_tools.py", "_try_lock_one"):
        "opens a slot lock under the data dir's .universe-tool-slots, O_NOFOLLOW",
    ("tinyassets/wiki/okf_export.py", "_conformance_report"):
        "reads the bundle this export just wrote into target_dir, which is refused "
        "when inside the wiki root; not an agent-written file",
}

_READ_ATTRS = {"read_text", "read_bytes", "open"}
_YAML_LOADERS = {"load", "safe_load", "full_load", "unsafe_load", "load_all", "safe_load_all"}


def _violations(path: str) -> list[tuple[str, int, str]]:
    source = (_REPO / path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path)
    found: list[tuple[str, int, str]] = []

    def visit(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = func
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, ast.Call):
                target = child.func
                what = None
                if isinstance(target, ast.Name) and target.id == "open":
                    what = "open()"
                elif isinstance(target, ast.Attribute):
                    owner = target.value.id if isinstance(target.value, ast.Name) else ""
                    if target.attr in _READ_ATTRS:
                        what = f"{owner + '.' if owner else ''}{target.attr}()"
                    elif owner == "yaml" and target.attr in _YAML_LOADERS:
                        what = f"yaml.{target.attr}()"
                    elif owner == "json" and target.attr == "load":
                        what = "json.load()"
                if what is not None:
                    found.append((name, child.lineno, what))
            visit(child, name)

    visit(tree, "<module>")
    return found


@pytest.mark.parametrize("path", TURN_PATH)
def test_turn_path_reads_universe_files_only_through_the_safe_reader(path):
    bad = [
        f"{path}:{line} {what} in {func}()"
        for func, line, what in _violations(path)
        if (path, func) not in ALLOWED
    ]
    assert not bad, (
        "raw file read or YAML/JSON load on the daemon turn path; route it "
        "through tinyassets.universe_files (bounded, no-follow, alias-free "
        "YAML) or add an ALLOWED row with the reason it cannot read an "
        "agent-writable universe file:\n" + "\n".join(bad)
    )


def test_every_exemption_still_matches_a_read():
    """An exemption whose read moved or vanished is a stale hole in the gate."""
    live = {(path, func) for path in TURN_PATH for func, _l, _w in _violations(path)}
    stale = sorted(set(ALLOWED) - live)
    assert not stale, f"ALLOWED rows no longer match any read: {stale}"


def test_the_gate_catches_a_raw_read():
    """Detection control: the scan is not vacuous."""
    probe = _REPO / "tests" / "_raw_read_probe_module.py"
    probe.write_text(
        "import yaml\n"
        "def f(p):\n"
        "    return yaml.safe_load(p.read_text())\n",
        encoding="utf-8",
    )
    try:
        found = {what for _f, _l, what in _violations("tests/_raw_read_probe_module.py")}
    finally:
        probe.unlink()
    assert {"p.read_text()", "yaml.safe_load()"} <= found
