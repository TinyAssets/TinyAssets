"""The merge gate's tree selection, and the runner audit that proves it.

A diff confined to the trees no Python imports (``platform/``, the prose) runs
only the Python tests that may read them (scripts/affected_tests.py
``tree_readers``). The runner's ``--ci-tree-audit`` fails any test that reads a
tree the map does not give it, so the map cannot quietly miss a reader.
"""
from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts.affected_tests import (
    Graph,
    gate_selection,
    provable_shape,
    retiring_tests,
    tree_readers,
)

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "scripts"

pytestmark = pytest.mark.verifies(
    "ci-linux-proof-venue:A tree-only diff runs the Python tests that can read it",
)


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_only_paths_in_the_trees_have_a_shape() -> None:
    assert provable_shape(["platform/owner/src/a.ts"]) == "platform"
    assert provable_shape(["platform/doors/x.ts", "docs/a.md", "openspec/t.md",
                           "AGENTS.md"]) == "*.md+docs+openspec+platform"
    # A Python file anywhere, any other root, or nothing at all: the whole suite.
    assert provable_shape(["platform/sandbox/template/build.py"]) is None
    assert provable_shape(["platform/a.ts", "tinyassets/x.py"]) is None
    assert provable_shape(["platform/a.ts", ".github/workflows/tests.yml"]) is None
    assert provable_shape(["deploy/x.md"]) is None
    assert provable_shape([]) is None


def _fixture_repo(root: Path) -> Path:
    return _tree(root, {
        "AGENTS.md": "# rules\n",
        "platform/owner/x.json": "{}\n",
        "docs/a.md": "a\n",
        "tinyassets/__init__.py": "",
        "tinyassets/thing.py": "VALUE = 1\n",
        "scripts/__init__.py": "",
        "scripts/shapes.py": 'GLOBS = ("platform/owner/**",)\n',
        "tests/__init__.py": "",
        # Names the tree as a path: a reader.
        "tests/test_named.py": (
            "from pathlib import Path\nROOT = Path(__file__).parents[1]\n"
            "def test_it(): assert (ROOT / 'platform' / 'owner' / 'x.json').exists()\n"),
        # Globs a temp directory and tests old code: never a reader (the router race's
        # and the volumes proof's shape).
        "tests/test_tmp_glob.py": (
            "from tinyassets.thing import VALUE\n"
            "def test_it(tmp_path): assert not list(tmp_path.glob('*')) and VALUE\n"),
        # Walks the checkout's root: reads every tree.
        "tests/test_root_walk.py": (
            "from pathlib import Path\n"
            "def test_it(): assert list(Path('.').rglob('*.ts')) is not None\n"),
        # Runs git: sees every tree.
        "tests/test_git.py": (
            "import subprocess\n"
            "def test_it(): subprocess.run(['git', 'status'], check=False)\n"),
        # Reaches the tree through a module it imports.
        "tests/test_helper.py": (
            "from scripts.shapes import GLOBS\ndef test_it(): assert GLOBS\n"),
        # Names a top-level markdown file.
        "tests/test_rules.py": "def test_it(): assert open('AGENTS.md').read()\n",
    })


def test_a_tree_diff_runs_its_readers_and_not_the_tests_that_cannot_see_it(tmp_path) -> None:
    root = _fixture_repo(tmp_path)
    selected, reasons = gate_selection(["platform/owner/x.json"], root=root, shared=set())
    assert selected == ["tests/test_git.py", "tests/test_helper.py", "tests/test_named.py",
                        "tests/test_root_walk.py"], reasons
    selected, _ = gate_selection(["AGENTS.md"], root=root, shared=set())
    assert "tests/test_rules.py" in selected and "tests/test_tmp_glob.py" not in selected
    # Outside the trees the gate still runs everything, and so does deleting a whole tree,
    # which a listing of the checkout's root would see.
    assert gate_selection(["tinyassets/thing.py"], root=root, shared=set())[0] is None
    assert gate_selection(["ideas/gone.md"], root=root, shared=set())[0] is None


def test_a_tree_a_conftest_reads_runs_the_whole_suite(tmp_path) -> None:
    root = _fixture_repo(tmp_path)
    (root / "tests" / "conftest.py").write_text(
        "from pathlib import Path\nDOC = Path('docs/a.md')\n", encoding="utf-8")
    readers, shared = tree_readers(Graph(root))
    assert shared == {"docs"}
    assert gate_selection(["docs/a.md"], root=root, shared=set())[0] is None
    assert gate_selection(["platform/owner/x.json"], root=root, shared=set())[0] is not None


def test_retiring_tests_reach_only_the_old_stack(tmp_path) -> None:
    root = _tree(tmp_path, {
        "tinyassets/__init__.py": "",
        "tinyassets/old.py": "X = 1\n",
        "scripts/__init__.py": "",
        "scripts/ops.py": "from tinyassets.old import X\n",
        "scripts/tool.py": "Y = 2\n",
        "tests/test_old.py": "from tinyassets.old import X\ndef test_it(): assert X\n",
        "tests/test_old_ops.py": "from scripts.ops import X\ndef test_it(): assert X\n",
        "tests/test_mixed.py": (
            "from tinyassets.old import X\nfrom scripts.tool import Y\n"
            "def test_it(): assert X and Y\n"),
        "tests/test_text.py": "def test_it(): assert True\n",
    })
    assert retiring_tests(Graph(root)) == ["tests/test_old.py", "tests/test_old_ops.py"]


def _run_audited(root: Path) -> dict[str, str]:
    junit = root / "junit.xml"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(_SCRIPTS), str(root)])}
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "ci_required_tests", "--ci-tree-audit", f"--rootdir={root}",
         f"--junitxml={junit}", "tests"],
        cwd=root, env=env, capture_output=True, text=True, timeout=120)
    outcomes = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        bad = case.find("failure") if case.find("failure") is not None else case.find("error")
        outcomes[case.get("classname", "").rsplit(".", 1)[-1]] = (
            "passed" if bad is None else bad.get("message", "") + (bad.text or ""))
    return outcomes


def test_the_audit_fails_a_test_reading_a_tree_the_map_does_not_give_it(tmp_path) -> None:
    root = _fixture_repo(tmp_path)
    _tree(root, {
        "scripts/lister.py": (
            "import subprocess\n"
            "def listed(root): return subprocess.run(['git', 'ls-files'], cwd=root,"
            " capture_output=True, text=True).stdout\n"),
        # Builds the path without naming the tree: the map cannot see it.
        "tests/test_hidden.py": (
            "from pathlib import Path\nROOT = Path(__file__).parents[1]\n"
            "def test_it(): assert (ROOT / ('plat' + 'form') / 'owner' / 'x.json').read_text()\n"),
        # Reads the checkout through a helper's git: also invisible to the map.
        "tests/test_hidden_git.py": (
            "from pathlib import Path\nfrom scripts.lister import listed\n"
            "def test_it(): listed(Path(__file__).parents[1])\n"),
        # A module-level read is the module's, and fails each of its tests.
        "tests/test_hidden_import.py": (
            "from pathlib import Path\n"
            "DOC = (Path(__file__).parents[1] / ('do' + 'cs') / 'a.md').read_text()\n"
            "def test_it(): assert DOC\n"),
    })
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    outcomes = _run_audited(root)
    for mapped in ("test_named", "test_tmp_glob", "test_root_walk", "test_helper",
                   "test_rules", "test_git"):
        assert outcomes[mapped] == "passed", (mapped, outcomes[mapped])
    assert "platform: platform/owner/x.json" in outcomes["test_hidden"]
    assert "Fix: name the path" in outcomes["test_hidden"]
    assert "git ls-files" in outcomes["test_hidden_git"]
    assert "docs: docs/a.md" in outcomes["test_hidden_import"]


def test_every_runner_run_loads_the_audit(tmp_path, monkeypatch) -> None:
    import scripts.ci_required_tests as runner

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["env"] = cmd, kwargs.get("env") or {}
        return subprocess.CompletedProcess(cmd, 5)

    selection = tmp_path / "affected.txt"
    selection.write_text("tests/test_gate_tree_selection.py\n", encoding="utf-8")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", [
        "ci_required_tests.py", "--junit", str(tmp_path / "j.xml"),
        "--affected", str(selection), "--profile", "affected"])
    runner.main()
    cmd = seen["cmd"]
    assert cmd[cmd.index("ci_required_tests") - 1] == "-p" and "--ci-tree-audit" in cmd
    assert str(_SCRIPTS) in seen["env"]["PYTHONPATH"].split(os.pathsep)
