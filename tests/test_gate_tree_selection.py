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
    deploy_paths,
    gate_selection,
    provable_shape,
    retiring_tests,
    select,
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
    # The workflows, the provider files and the files beside the scripts that are not Python.
    assert provable_shape([".github/workflows/tests.yml", ".claude/settings.json",
                           ".agents/skills/x/SKILL.md", "scripts/probes/p.mjs",
                           "scripts/load_secrets.sh"]) == ".agents+.claude+.github+scripts"
    # Python that nothing here imports: the platform's own, and the process's tests.
    assert provable_shape(["platform/sandbox/template/image.py"]) == "platform"
    assert provable_shape(["scripts/tests/test_main_red.py"]) == "scripts"
    # The old stack, any other Python, any other root, what the runner itself reads, or
    # nothing at all: the whole suite.
    for path in ("tinyassets/x.py", "tinyassets/providers/quota.json", "deploy/x.md",
                 "tests/test_x.py", "tests/fixtures/x.json", "packaging/x.json",
                 "Dockerfile", "pyproject.toml", "docker/linux-oracle.Dockerfile",
                 "scripts/affected_tests.py", "scripts/invariants/x.py",
                 "scripts/__pycache__/x.cpython-311.pyc", ".claude/hooks/bash_guard.py",
                 # Whatever else the import system loads beside a script: no tree file.
                 "scripts/sourceless.pyc", "scripts/native.so", "scripts/stub.pyi",
                 "docs/audits/repro.py", ".github/known-failing-tests.txt",
                 ".github/heavy-test-files.txt", ".github/test-durations.json"):
        assert provable_shape([path]) is None, path
        assert provable_shape(["platform/a.ts", ".github/workflows/tests.yml", path]) is None
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


def _process_repo(root: Path) -> Path:
    """The old stack with its tests, and the files a platform PR edits beside it."""
    return _tree(root, {
        ".github/workflows/tests.yml": "name: Tests\n",
        ".github/workflows/deploy.yml": "name: Deploy\n",
        ".github/known-failing-tests.txt": "",
        ".claude/settings.json": "{}\n",
        "platform/import/host.py": "print('host')\n",
        "platform/import/window.sh": "true\n",
        "scripts/__init__.py": "",
        "scripts/tool.py": "from pathlib import Path\nHERE = Path(__file__).resolve().parent\n",
        "scripts/probe.mjs": "export {}\n",
        "tinyassets/__init__.py": "",
        "tinyassets/thing.py": "VALUE = 1\n",
        "tests/__init__.py": "",
        "tests/test_old.py": "from tinyassets.thing import VALUE\ndef test_it(): assert VALUE\n",
        "tests/test_tool.py": "from scripts.tool import HERE\ndef test_it(): assert HERE\n",
        "tests/test_pin.py": (
            "from pathlib import Path\nROOT = Path(__file__).parents[1]\n"
            "def test_it(): assert (ROOT / '.github' / 'workflows' / 'tests.yml').read_text()\n"),
        "tests/test_probe.py": (
            "from pathlib import Path\nROOT = Path(__file__).parents[1]\n"
            "def test_it(): assert (ROOT / 'scripts' / 'probe.mjs').read_text()\n"),
    })


def test_a_workflow_or_script_diff_runs_its_readers_and_the_old_stack_runs_everything(
        tmp_path) -> None:
    root = _process_repo(tmp_path)
    for changed, expected in (
        # A workflow, the gate's own included: the tests that read the workflows.
        ([".github/workflows/tests.yml"], ["tests/test_pin.py"]),
        ([".github/workflows/deploy.yml"], ["tests/test_pin.py"]),
        # A file beside the scripts that is not Python: the tests that read such files.
        (["scripts/probe.mjs"], ["tests/test_probe.py"]),
        # Nothing reads these, so nothing runs: the platform's Python (changed or
        # deleted), its shell, a provider file.
        (["platform/import/host.py", "platform/import/window.sh"], []),
        (["platform/import/gone.py"], []),
        ([".claude/settings.json"], []),
        ([".github/workflows/deploy.yml", "platform/import/host.py", "scripts/probe.mjs"],
         ["tests/test_pin.py", "tests/test_probe.py"]),
    ):
        selected, reasons = gate_selection(changed, root=root, shared=set())
        assert selected == expected, (changed, reasons)
    # The PR's advisory slice no longer runs everything for the platform's Python either.
    selected, _ = select(["platform/import/host.py"], root=root, graph=Graph(root),
                         shared=set(), pr=True)
    assert selected == []
    # The old stack's code, tests, manifest and image, any Python that Python can import,
    # and what the runner reads outside any test: everything, alone or beside a tree file.
    for path in ("tinyassets/thing.py", "tests/test_old.py", "scripts/tool.py",
                 "pyproject.toml", "Dockerfile", "deploy/compose.yml", "packaging/x.json",
                 ".github/known-failing-tests.txt"):
        assert gate_selection([path], root=root, shared=set())[0] is None, path
        assert gate_selection([".github/workflows/tests.yml", path], root=root,
                              shared=set())[0] is None, path


def test_the_audit_holds_the_workflows_and_the_non_python_files_beside_the_scripts(
        tmp_path) -> None:
    root = _process_repo(tmp_path)
    _tree(root, {
        # Imports a script and resolves its path: the import system lists scripts/ and
        # realpath checks that the directory exists. Neither reads a file a tree-only
        # diff can change, so neither needs the map.
        "tests/test_exists.py": (
            "from pathlib import Path\nfrom scripts.tool import HERE\n"
            "def test_it(): assert HERE.is_dir() and (HERE / 'tool.py').read_text()\n"),
        # Reads a workflow, and a file beside the scripts, by paths the map cannot see.
        "tests/test_hidden_workflow.py": (
            "from pathlib import Path\nROOT = Path(__file__).parents[1]\n"
            "HUB = ROOT / ('.git' + 'hub')\n"
            "def test_it(): assert (HUB / 'workflows' / 'deploy.yml').exists()\n"),
        "tests/test_hidden_probe.py": (
            "from scripts.tool import HERE\n"
            "def test_it(): assert (HERE / 'probe.mjs').read_text()\n"),
        # Lists the scripts' directory: that sees which non-Python files exist.
        "tests/test_hidden_listing.py": (
            "from scripts.tool import HERE\n"
            "def test_it(): assert sorted(p.name for p in HERE.iterdir())\n"),
        # The platform's Python is a tree file like any other.
        "tests/test_hidden_host.py": (
            "from pathlib import Path\nROOT = Path(__file__).parents[1]\n"
            "HOST = ROOT / ('plat' + 'form') / 'import' / 'host.py'\n"
            "def test_it(): assert HOST.read_text()\n"),
        # The quarantine ledger is in no tree (a diff to it runs everything), so reading
        # it needs no map.
        "tests/test_ledger.py": (
            "from pathlib import Path\nROOT = Path(__file__).parents[1]\n"
            "HUB = ROOT / ('.git' + 'hub')\n"
            "def test_it(): assert (HUB / 'known-failing-tests.txt').exists()\n"),
    })
    outcomes = _run_audited(root)
    for mapped in ("test_old", "test_tool", "test_pin", "test_probe", "test_exists",
                   "test_ledger"):
        assert outcomes[mapped] == "passed", (mapped, outcomes[mapped])
    assert ".github: .github/workflows/deploy.yml" in outcomes["test_hidden_workflow"]
    assert "scripts: scripts/probe.mjs" in outcomes["test_hidden_probe"]
    assert "scripts: scripts" in outcomes["test_hidden_listing"]
    assert "platform: platform/import/host.py" in outcomes["test_hidden_host"]


def test_a_path_whose_merge_deploys_the_old_stack_runs_the_whole_suite(tmp_path) -> None:
    root = _process_repo(tmp_path)
    readers = ["tests/test_pin.py"]
    # No build workflow (after the cutover): a workflow is a tree file like any other.
    assert gate_selection([".github/workflows/deploy.yml"], root=root, shared=set())[0] == readers
    _tree(root, {
        ".github/workflows/build-image.yml": (
            "name: Build\non:\n  push:\n    branches: [main]\n    paths:\n"
            "      - 'tinyassets/**'\n      - '.github/actions/canary/**'\n"
            "      - '.github/workflows/build-image.yml'\n"
            "      - '.github/workflows/deploy.yml'\n  workflow_dispatch:\n"),
        ".github/actions/canary/action.yml": "name: canary\n",
        ".github/workflows/lint.yml": "name: Lint\n",
    })
    # Merging any of these builds and deploys the old stack, whoever reads them.
    for path in (".github/workflows/deploy.yml", ".github/workflows/build-image.yml",
                 ".github/actions/canary/action.yml", ".github/workflows/gone-from-disk.yml"):
        expected = readers if path.endswith("gone-from-disk.yml") else None
        assert gate_selection([path], root=root, shared=set())[0] == expected, path
        if expected is None:
            assert gate_selection([path, "platform/import/host.py", ".github/workflows/lint.yml"],
                                  root=root, shared=set())[0] is None, path
    # Every other workflow, the gate's own included, still runs its readers.
    for path in (".github/workflows/tests.yml", ".github/workflows/lint.yml"):
        assert gate_selection([path], root=root, shared=set())[0] == readers, path
    # With no path filter every merge builds, and a trigger that cannot be read is no answer.
    _tree(root, {".github/workflows/build-image.yml": "name: Build\non:\n  push:\n"})
    assert gate_selection(["platform/import/window.sh"], root=root, shared=set())[0] is None
    _tree(root, {".github/workflows/build-image.yml": "on: [push\n"})
    with pytest.raises(RuntimeError, match="push paths cannot be read"):
        gate_selection(["platform/import/window.sh"], root=root, shared=set())
    # On this checkout no path of the old stack's build trigger is left to a reader map.
    patterns = deploy_paths(_REPO)
    assert ".github/workflows/deploy-prod.yml" in patterns and "tinyassets/**" in patterns
    for pattern in patterns:
        sample = pattern[:-2] + "x" if pattern.endswith("/**") else pattern
        assert gate_selection([sample], root=_REPO, shared=set())[0] is None, pattern


def test_a_tree_named_as_data_selects_no_test_that_merely_walks(tmp_path) -> None:
    # tests/test_restore_legacy_credentials.py's shape: "platform" is a vault kind,
    # looped over under tmp_path, the test walks a temp tree, and its helper lists /proc.
    # It reads nothing of the checkout's platform/, so a platform change must not run it.
    root = _tree(tmp_path, {
        "platform/owner/x.ts": "export {}\n",
        "tinyassets/__init__.py": "",
        "tinyassets/thing.py": "X = 1\n",
        "tinyassets/repair.py": (
            "from pathlib import Path\nPROC_ROOT = Path('/proc')\n"
            "def live(): return [p for p in PROC_ROOT.iterdir() if p.name.isdecimal()]\n"),
        "tests/__init__.py": "",
        "tests/test_legacy.py": (
            "from tinyassets.repair import live\n"
            "KINDS = ('center', 'platform', 'provider')\n"
            "def test_it(tmp_path):\n"
            "    for kind in KINDS:\n"
            "        base = tmp_path / kind\n"
            "        (base / 'platform').mkdir(parents=True)\n"
            "    report = {'platform': []}\n"
            "    assert live() is not None and report['platform'] == []\n"
            "    assert len(list(tmp_path.rglob('*'))) == 6\n"),
        # True readers, each naming the tree as a checkout path, must still run.
        "tests/test_looped.py": (
            "from pathlib import Path\nfrom tinyassets.thing import X\n"
            "ROOT = Path(__file__).parents[1]\nTOPS = ('tinyassets', 'platform')\n"
            "def test_it(): assert [p for top in TOPS for p in (ROOT / top).rglob('*')] and X\n"),
        "tests/test_root_listing.py": (
            "from pathlib import Path\nfrom tinyassets.thing import X\n"
            "REPO_ROOT = Path(__file__).resolve().parents[1]\n"
            "def test_it(): assert list(REPO_ROOT.iterdir()) and X\n"),
    })
    selected, reasons = select(["platform/owner/x.ts"], root=root, graph=Graph(root),
                               shared=set(), pr=True)
    assert selected == ["tests/test_looped.py"], reasons
    selected, reasons = gate_selection(["platform/owner/x.ts"], root=root, shared=set())
    assert selected == ["tests/test_looped.py", "tests/test_root_listing.py"], reasons
    # The loop still names tinyassets/ as a path for a change under it.
    selected, _ = select(["tinyassets/data.json"], root=root, graph=Graph(root),
                         shared=set(), pr=True)
    assert selected == ["tests/test_looped.py"]
    # And the audit finds no unmapped read in any of them.
    assert set(_run_audited(root).values()) == {"passed"}


_HEAD = ("from pathlib import Path\nimport os, glob, subprocess\nimport pytest\n"
         "ROOT = Path(__file__).resolve().parents[1]\n")
#: Ways to name platform/ as a path that a bare-word match caught; the path rule must too.
#: Each reads the tree, so each must run on a platform/ change in both tiers.
_READERS = {
    "listcomp": "def test_x():\n    for n in [d for d in ('platform',)]:\n"
                "        os.listdir(os.path.join(ROOT, n))\n",
    "sorted": "DIRS = ('platform',)\ndef test_x():\n"
              "    for d in sorted(DIRS):\n        list((ROOT / d).rglob('*'))\n",
    "concat": "A = ('platform',)\nB = ('docs',)\ndef test_x():\n"
              "    for d in A + B:\n        list((ROOT / d).rglob('*'))\n",
    "tuple_target": "def test_x():\n    for top, sub in (('platform', 'owner'),):\n"
                    "        list((ROOT / top / sub).rglob('*'))\n",
    "enumerate": "DIRS = ('platform',)\ndef test_x():\n"
                 "    for i, d in enumerate(DIRS):\n        list((ROOT / d).rglob('*'))\n",
    "dict_keys": "TOPS = {'platform': 'ts'}\ndef test_x():\n"
                 "    for t in TOPS:\n        list((ROOT / t).rglob('*'))\n",
    "parametrize": "@pytest.mark.parametrize('top', ['platform'])\n"
                   "def test_x(top):\n    assert list((ROOT / top).rglob('*'))\n",
    "fixture": "@pytest.fixture\ndef top():\n    return 'platform'\n"
               "def test_x(top):\n    assert list((ROOT / top).rglob('*'))\n",
    "starred_join": "def test_x():\n"
                    "    assert os.listdir(os.path.join(ROOT, *('platform', 'owner')))\n",
    "helper_param": "def scan(root):\n    return list((root / 'platform').rglob('*'))\n"
                    "def test_tmp(tmp_path):\n    root = tmp_path\n    assert root.exists()\n"
                    "def test_real():\n    assert scan(ROOT)\n",
    "temp_named_param": "def walk(tmp_path):\n    return list((tmp_path / 'platform').rglob('*'))\n"
                        "def test_x():\n    assert walk(ROOT)\n",
    "call_arg": "def scan(t):\n    return list((ROOT / t).rglob('*'))\n"
                "def test_x():\n    assert scan('platform')\n",
    "call_keyword": "def scan(root, t):\n    return list((root / t).rglob('*'))\n"
                    "def test_x():\n    assert scan(ROOT, t='platform')\n",
    "default_arg": "def walk(root=ROOT):\n    return list((root / 'platform').rglob('*'))\n"
                   "def test_x(tmp_path):\n    assert walk() and walk(tmp_path) is not None\n",
    "argv": "def test_x():\n    list(ROOT.glob('x'))\n"
            "    subprocess.run(['ls', 'platform'], cwd=ROOT, check=True)\n",
    "fstring_glob": "def test_x():\n    t = 'platform'\n    assert list(ROOT.glob(f'{t}/**'))\n",
    "class_attr": "class K:\n    DIRS = ('platform',)\ndef test_x():\n"
                  "    for d in K.DIRS:\n        list((ROOT / d).rglob('*'))\n",
    "dynamic": "DIRS = ('platform',)\ndef test_x():\n"
               "    for d in globals()['DI' + 'RS']:\n        list((ROOT / d).rglob('*'))\n",
    "relative_glob": "def test_x():\n    assert glob.glob('platform/**', recursive=True)\n",
}


@pytest.mark.parametrize("shape", sorted(_READERS))
def test_every_way_to_name_a_tree_as_a_path_still_selects_its_reader(tmp_path, shape) -> None:
    root = _tree(tmp_path, {"platform/owner/x.ts": "export {}\n", "docs/a.md": "a\n",
                            "tests/__init__.py": "", "tests/test_it.py": _HEAD + _READERS[shape]})
    selected, reasons = select(["platform/owner/x.ts"], root=root, graph=Graph(root),
                               shared=set(), pr=True)
    assert selected == ["tests/test_it.py"], reasons
    selected, reasons = gate_selection(["platform/owner/x.ts"], root=root, shared=set())
    assert selected == ["tests/test_it.py"], reasons


def test_a_root_that_climbs_back_up_is_the_checkout(tmp_path) -> None:
    root = _tree(tmp_path, {
        "platform/owner/x.ts": "export {}\n", "tests/__init__.py": "",
        "tests/test_it.py": ("from pathlib import Path\nREPO_ROOT = Path(__file__).parent / '..'\n"
                             "def test_x():\n    assert list(REPO_ROOT.iterdir())\n")})
    assert gate_selection(["platform/owner/x.ts"], root=root, shared=set())[0] == [
        "tests/test_it.py"]


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
    found: dict[str, list[str]] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        bad = case.find("failure") if case.find("failure") is not None else case.find("error")
        found.setdefault(case.get("classname", "").rsplit(".", 1)[-1], []).append(
            "" if bad is None else bad.get("message", "") + (bad.text or ""))
    # A module passes when every case in it does; otherwise its failures, joined.
    return {module: "\n".join(m for m in messages if m) or "passed"
            for module, messages in found.items()}


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


def _commit(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm",
                    message], cwd=root, check=True)


def test_a_move_into_a_tree_runs_the_whole_suite(tmp_path) -> None:
    from scripts.affected_tests import changed_files

    root = _fixture_repo(tmp_path)
    _tree(root, {"tinyassets/data.json": '{"rows": [1, 2, 3, 4, 5, 6, 7, 8]}\n',
                 "tinyassets/port.py": "".join(f"VALUE_{i} = {i}\n" for i in range(40))})
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    _commit(root, "base")
    # Git's diff detects the move and, without --no-renames, lists only the new path.
    subprocess.run(["git", "mv", "tinyassets/data.json", "platform/data.json"], cwd=root, check=True)
    _commit(root, "move data")
    changed = changed_files("HEAD~1", root)
    assert "tinyassets/data.json" in changed, changed
    assert gate_selection(changed, root=root, shared=set())[0] is None
    subprocess.run(["git", "mv", "tinyassets/port.py", "platform/port.ts"], cwd=root, check=True)
    _commit(root, "port")
    changed = changed_files("HEAD~1", root)
    assert "tinyassets/port.py" in changed, changed
    assert gate_selection(changed, root=root, shared=set())[0] is None


def test_a_deleted_top_level_md_still_selects_the_helpers_readers(tmp_path) -> None:
    root = _fixture_repo(tmp_path)
    _tree(root, {
        "scripts/rules.py": "RULES = 'AGENTS.md'\n",
        "tests/test_rules_helper.py": "from scripts.rules import RULES\ndef test_it(): assert RULES\n",
    })
    (root / "AGENTS.md").unlink()  # the diff deletes it; the helper's test still reads it
    selected, _ = gate_selection(["AGENTS.md"], root=root, shared=set())
    assert "tests/test_rules_helper.py" in selected


def _audit(root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(_SCRIPTS))
    from scripts.ci_required_tests import TreeAudit

    return TreeAudit(root)


def test_git_inside_the_checkout_reads_every_tree_unless_it_reads_nothing(
        tmp_path, monkeypatch) -> None:
    root = _fixture_repo(tmp_path)
    audit = _audit(root, monkeypatch)
    here = str(root)
    reads = [
        ["git", "-c", "safe.directory=*", "ls-files"],
        ["git", "cat-file", "-p", "HEAD"],
        ["git", "worktree", "add", "/tmp/elsewhere"],
        [r"C:\Program Files\Git\bin\git.exe", "status"],
        ["git", "--git-dir", f"{root}/.git", "log"],
        ["git", "clone", str(root), "/tmp/copy"],
    ]
    harmless = [
        ["git", "rev-parse", "HEAD"],
        ["git", "--version"],
        ["git", "-c", "core.x=1", "config", "--get", "user.name"],
        ["git", "-C", "/tmp/elsewhere", "status"],
        ["git", "--git-dir=/tmp/elsewhere/.git", "--work-tree=/tmp/elsewhere", "diff"],
        ["git", "init", "-q", "/tmp/elsewhere"],
        ["git", "init", "--bare", "-b", "main", "/tmp/elsewhere"],
        ["gitk", "status"],
    ]
    for argv in reads:
        assert audit._git_reads(argv, here, None), argv
    for argv in harmless:
        assert not audit._git_reads(argv, here, None), argv
    # A git dir alone leaves the working directory as the work tree: still a read.
    assert audit._git_reads(["git", "status"], here, {"GIT_DIR": "/tmp/elsewhere/.git"})
    assert audit._git_reads(["git", "--git-dir=/tmp/elsewhere/.git", "status"], here, None)
    assert not audit._git_reads(["git", "status"], here, {
        "GIT_DIR": "/tmp/elsewhere/.git", "GIT_WORK_TREE": "/tmp/elsewhere"})
    assert not audit._git_reads(["git", "http-backend"], here, {"GIT_PROJECT_ROOT": "/tmp/x"})
    assert audit._git_reads(["git", "init", "-b", "main"], here, None)


def test_a_dir_fd_resolves_on_each_host_and_reads_everything_when_it_cannot(
        tmp_path, monkeypatch) -> None:
    import fcntl

    root = _fixture_repo(tmp_path / "repo")
    audit = _audit(root, monkeypatch)
    fd = os.open(root / "docs", os.O_RDONLY)
    try:
        assert list(audit._path("a.md", dir_fd=fd)) == [("docs", "docs/a.md")]  # /proc
        audit.PROC_FD = str(tmp_path / "no-proc")
        monkeypatch.delattr(fcntl, "F_GETPATH", raising=False)
        unresolved = list(audit._path("a.md", dir_fd=fd))
        assert {tree for tree, _ in unresolved} == set(audit.every), unresolved
        # macOS names the fd with fcntl(F_GETPATH).
        monkeypatch.setattr(fcntl, "F_GETPATH", 50, raising=False)
        monkeypatch.setattr(fcntl, "fcntl",
                            lambda fd, cmd, buf: os.fsencode(root / "docs") + bytes(8))
        assert list(audit._path("a.md", dir_fd=fd)) == [("docs", "docs/a.md")]
    finally:
        os.close(fd)


def test_shell_strings_and_sqlite_are_read_as_paths(tmp_path, monkeypatch) -> None:
    root = _fixture_repo(tmp_path)
    audit = _audit(root, monkeypatch)
    monkeypatch.chdir(root)
    shell = ["/bin/sh", "-c", "cat platform/owner/x.json && FOO=1 git status | wc -l"]
    found = set(audit._trees("subprocess.Popen", ("/bin/sh", shell, None, None)))
    assert ("platform", "platform/owner/x.json") in found
    assert {tree for tree, path in found if path.startswith("git ")} == set(audit.every)
    found = set(audit._trees("os.system", ("ls docs/ > /dev/null",)))
    assert ("docs", "docs") in found
    audit.current = "tests/test_named.py"
    audit("sqlite3.connect", (str(root / "docs" / "a.db"),))
    assert [t[:2] for t in audit.touches["tests/test_named.py"]] == [("docs", "docs/a.db")]


def test_the_audit_sees_existence_checks_helpers_and_its_own_errors(tmp_path) -> None:
    root = _fixture_repo(tmp_path)
    _tree(root, {
        # Reads a tree once, at import, by a path the map cannot see; two tests load it.
        "scripts/once.py": (
            "from pathlib import Path\nROOT = Path(__file__).parents[1]\n"
            "DATA = (ROOT / ('plat' + 'form') / 'owner' / 'x.json').read_text()\n"),
        "tests/test_once_a.py": (
            "from scripts.once import DATA\n"
            "def test_it(): assert DATA and 'platform/owner/x.json'\n"),
        "tests/test_once_b.py": "from scripts.once import DATA\ndef test_it(): assert DATA\n",
        # Only checks that a tree path exists.
        "tests/test_exists.py": (
            "from pathlib import Path\nROOT = Path(__file__).parents[1]\n"
            "def test_it(): assert (ROOT / ('plat' + 'form') / 'owner').is_dir()\n"),
        # os.open relative to the working directory, and relative to a dir_fd.
        "tests/test_os_open.py": (
            "import os\n"
            "def test_cwd(): os.close(os.open(('plat' + 'form') + '/owner/x.json', os.O_RDONLY))\n"
            "def test_dir_fd():\n"
            "    fd = os.open(('do' + 'cs'), os.O_RDONLY)\n"
            "    os.close(os.open('a.md', os.O_RDONLY, dir_fd=fd)); os.close(fd)\n"),
        # The import system lists the checkout (it is on sys.path): no test's read.
        "tests/test_imports.py": (
            "import importlib, importlib.metadata\n"
            "def test_it():\n"
            "    importlib.invalidate_caches()\n"
            "    assert list(importlib.metadata.distributions()) is not None\n"),
        # The wrapped os calls still answer os.supports_* as the originals did.
        "tests/test_capability.py": (
            "import os\n"
            "def test_it():\n"
            "    assert os.open in os.supports_dir_fd and os.stat in os.supports_fd\n"
            "    assert os.stat.__name__ == 'stat' and os.open.__doc__\n"),
        # A broader-scoped fixture's read is judged by every file that loads the fixture.
        "tests/fx.py": (
            "import pytest\nfrom pathlib import Path\nROOT = Path(__file__).parents[1]\n"
            "@pytest.fixture(scope='session')\n"
            "def shared():\n"
            "    return (ROOT / ('plat' + 'form') / 'owner' / 'x.json').read_text()\n"),
        "tests/test_fx_a.py": (
            "from tests.fx import shared\n"
            "def test_it(shared): assert shared and 'platform/owner/x.json'\n"),
        "tests/test_fx_b.py": "from tests.fx import shared\ndef test_it(shared): assert shared\n",
        # A test that deletes its working directory: no path there is the checkout's.
        "tests/test_gone_cwd.py": (
            "import os, subprocess\n"
            "def test_it(tmp_path, monkeypatch):\n"
            "    gone = tmp_path / 'gone'\n"
            "    gone.mkdir(); monkeypatch.chdir(gone); gone.rmdir()\n"
            "    try:\n"
            "        open('x.md')\n"
            "    except OSError:\n"
            "        pass\n"
            "    subprocess.run(['true'], check=False)\n"
            "    assert not os.path.exists('platform')\n"),
        # A listing of the checkout's root sees the top-level .md files.
        "tests/test_lists_root.py": (
            "import os\nfrom pathlib import Path\n"
            "def test_it(): assert os.listdir(Path(__file__).parents[1])\n"),
        # An event the audit cannot read is a failure, never a silent pass.
        "tests/test_audit_error.py": (
            "import subprocess, pytest\n"
            "class Bad:\n"
            "    def __fspath__(self): raise RuntimeError('unreadable')\n"
            "def test_it():\n"
            "    with pytest.raises(Exception):\n"
            "        subprocess.Popen(['true', Bad()])\n"),
    })
    outcomes = _run_audited(root)
    # Judged by the helper's users, not by the mapped test that happened to load it
    # first (test_once_a), and the fix names the helper.
    once = outcomes["test_once_a"]
    assert "read once by scripts/once.py" in once and "tests/test_once_b.py" in once, once
    assert "Fix: name the path in scripts/once.py" in once
    assert "platform: platform/owner" in outcomes["test_exists"]
    assert "platform: platform/owner/x.json" in outcomes["test_os_open"]
    assert "docs: docs/a.md" in outcomes["test_os_open"]
    assert outcomes["test_imports"] == "passed", outcomes["test_imports"]
    assert outcomes["test_capability"] == "passed", outcomes["test_capability"]
    assert outcomes["test_gone_cwd"] == "passed", outcomes["test_gone_cwd"]
    fixture = outcomes["test_fx_a"]  # the mapped test that set the fixture up
    assert "read once by tests/fx.py" in fixture and "tests/test_fx_b.py" in fixture, fixture
    assert "*.md: ." in outcomes["test_lists_root"]
    assert "audit error" in outcomes["test_audit_error"]
    # Loaded alone, the helper's unmapped user still fails: no order decides it.
    for name in list(root.glob("tests/test_*.py")):
        if name.name not in ("test_once_b.py",):
            name.unlink()
    assert "read once by scripts/once.py" in _run_audited(root)["test_once_b"]


@pytest.mark.verifies("ci-linux-proof-venue:The required shards run the unchanged gate runner")
def test_a_run_a_leaked_thread_holds_open_ends_and_names_it(tmp_path) -> None:
    from scripts.ci_required_tests import HUNG_AFTER_RUN

    _tree(tmp_path, {"tests/__init__.py": "", "tests/test_leak.py": (
        "import threading\n"
        "def test_it(): threading.Thread(target=threading.Event().wait, name='leaked').start()\n")})
    env = {**os.environ, "PYTHONPATH": str(_SCRIPTS), "TA_CI_EXIT_GRACE": "2"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "ci_required_tests",
         f"--rootdir={tmp_path}", "tests"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == HUNG_AFTER_RUN, proc.stdout + proc.stderr
    assert "still running: leaked" in proc.stderr


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
