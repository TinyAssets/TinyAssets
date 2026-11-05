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
