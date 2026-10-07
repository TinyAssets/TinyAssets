"""The deploy chain's runtime classifier: skip what production does not run.

Every deploy recreates the production daemon and kills in-flight user turns.
``scripts/runtime_paths.py`` decides whether a merge changes anything
production runs; build-image.yml, release-reconcile.yml and deployed_sha.py all
ask it. The dangerous direction is a runtime change classified as skippable,
so most of these tests pin the FAIL-OPEN cases.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.runtime_repo_fixture import make_repo

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

_SPEC = importlib.util.spec_from_file_location(
    "runtime_paths_under_test", REPO_ROOT / "scripts" / "runtime_paths.py"
)
assert _SPEC is not None and _SPEC.loader is not None
rp = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = rp
_SPEC.loader.exec_module(rp)

_WINDOWS_GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
_BASH = str(_WINDOWS_GIT_BASH) if _WINDOWS_GIT_BASH.exists() else shutil.which("bash")

yaml = pytest.importorskip("yaml")


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "repo")


def _decide(repo, base, head):
    return rp.decide(repo.root, base, head)


# --- Dockerfile parsing -----------------------------------------------------


def test_real_dockerfile_context_copies_are_all_runtime():
    sources, everything = rp.dockerfile_copy_sources(
        (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    )
    assert everything is False
    assert {
        "pyproject.toml",
        "tinyassets",
        "domains",
        "fantasy_daemon",
        "data/world_rules.lp",
        "deploy/native/ta_op.c",
        "deploy/codex-flock-wrapper.sh",
        "deploy/docker-entrypoint.sh",
        "scripts/codex_cli_smoke.py",
        "scripts/mcp_public_canary.py",
        "scripts/_canary_common.py",
        "scripts/workspace_bwrap_oracle.py",
    } <= set(sources)
    # Stage-to-stage copies read no build context.
    assert not any(src.startswith(("build/", "opt/", "tmp/")) for src in sources)


def test_parser_handles_flags_json_form_continuations_and_stage_copies():
    text = (
        "FROM x AS b\n"
        "# COPY commented/out.py /x\n"
        "COPY --chown=1:1 --chmod=0755 a.py \\\n"
        "     b/ /dst/\n"
        'COPY ["c d.txt", "/dst/"]\n'
        "COPY --from=b /build/x /x\n"
        "ADD https://example.invalid/f.tgz /tmp/\n"
        "COPY ./e/ ./e/\n"
        "RUN echo COPY not/an/instruction\n"
    )
    sources, everything = rp.dockerfile_copy_sources(text)
    assert everything is False
    assert sources == ["a.py", "b", "c d.txt", "e"]


@pytest.mark.parametrize(
    "line", ["COPY . /app/", "COPY *.py /app/", "COPY ./ /app/", 'COPY ["broken', "COPY onlyone"]
)
def test_an_unbounded_copy_makes_every_path_runtime(line):
    _, everything = rp.dockerfile_copy_sources(f"FROM x\n{line}\n")
    assert everything is True


def test_a_wildcard_is_bounded_by_its_directory():
    sources, everything = rp.dockerfile_copy_sources("FROM x\nCOPY scripts/*.py /app/\n")
    assert (sources, everything) == (["scripts"], False)


def test_host_manifest_matches_what_the_installer_prints():
    """Differential: the parsed array against the script's own manifest mode."""
    if not _BASH:
        pytest.skip("bash is required to run the installer's manifest mode")
    installer = REPO_ROOT / "deploy" / "install-host-uptime-services.sh"
    parsed = rp.host_manifest_files(installer.read_text(encoding="utf-8"))
    printed = subprocess.run(
        [_BASH, installer.as_posix()],
        cwd=REPO_ROOT,
        env={"TINYASSETS_PRINT_MANIFEST": "1", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert parsed, "RUNTIME_FILES array not found"
    assert set(parsed) <= set(printed)
    # Everything the installer ships is runtime, whether it came from the
    # array, the unit list, or deploy/.
    inputs = _working_tree_inputs()
    assert [p for p in printed if not inputs.covers(p)] == []


# --- decisions --------------------------------------------------------------


def test_docs_only_merge_skips(repo):
    r, base = repo
    head = r.commit(
        "docs", {"docs/notes.md": "more\n", "AGENTS.md": "x\n", "tests/test_x.py": "#\n"}
    )
    decision = _decide(r, base, head)
    assert decision.build is False, decision.reason


def test_runtime_merge_builds(repo):
    r, base = repo
    head = r.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    decision = _decide(r, base, head)
    assert decision.build is True
    assert decision.runtime_paths == ("tinyassets/app.py",)


def test_a_dockerfile_copy_source_outside_the_package_builds(repo):
    """scripts/_canary_common.py is COPYed into the image (the healthcheck
    imports it) but was never in build-image.yml's path filter."""
    r, base = repo
    head = r.commit("canary helper", {"scripts/_canary_common.py": "TOKEN = 2\n"})
    decision = _decide(r, base, head)
    assert decision.build is True
    assert decision.runtime_paths == ("scripts/_canary_common.py",)


def test_a_host_service_input_builds(repo):
    r, base = repo
    head = r.commit("watchdog", {"scripts/watchdog.py": "WATCH = 2\n"})
    assert _decide(r, base, head).runtime_paths == ("scripts/watchdog.py",)


def test_a_script_nothing_ships_does_not_build(repo):
    r, base = repo
    head = r.commit("tool", {"scripts/unrelated_tool.py": "TOOL = 2\n"})
    assert _decide(r, base, head).build is False


def test_batched_merges_are_judged_as_one_range(repo):
    """One push carrying a runtime merge and then a docs merge must build."""
    r, base = repo
    r.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    head = r.commit("docs", {"docs/notes.md": "more\n"})
    decision = _decide(r, base, head)
    assert decision.build is True
    assert "tinyassets/app.py" in decision.runtime_paths


def test_docs_merge_after_an_undeployed_runtime_merge_builds(repo):
    """Production still serves `base`: the runtime merge never deployed (its
    build was cancelled by the next push, or its deploy rolled back). The docs
    merge on top must carry it out."""
    r, base = repo
    runtime = r.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    docs = r.commit("docs", {"docs/notes.md": "more\n"})
    assert _decide(r, base, docs).build is True
    # Once the runtime merge IS what production serves, the same docs head skips.
    assert _decide(r, runtime, docs).build is False


def test_a_file_renamed_out_of_a_runtime_directory_builds(repo):
    r, base = repo
    head = r.rename("move", "tinyassets/app.py", "docs/app.py")
    decision = _decide(r, base, head)
    assert decision.build is True
    assert "tinyassets/app.py" in decision.runtime_paths


def test_a_dockerfile_change_builds(repo):
    r, base = repo
    head = r.commit("df", {".dockerignore": "docs/\ntests/\n"})
    assert _decide(r, base, head).build is True


def test_a_file_the_image_stops_copying_is_no_longer_a_runtime_input(repo):
    """The Dockerfile's COPY manifest is the source of truth, not a hand-kept
    list: a file leaving the image stops being a runtime input by itself."""
    r, _ = repo
    df = (r.root / "Dockerfile").read_text(encoding="utf-8")
    no_copy = r.commit(
        "stop shipping the rules file",
        {
            "Dockerfile": df.replace(
                'COPY ["data/world_rules.lp", "/app/data/world_rules.lp"]\n', ""
            )
        },
    )
    assert "world_rules" not in (r.root / "Dockerfile").read_text(encoding="utf-8")
    head = r.commit("rules", {"data/world_rules.lp": "rule2.\n"})
    assert _decide(r, no_copy, head).build is False


def test_a_file_the_image_starts_copying_becomes_a_runtime_input(repo):
    """The safe direction, enforced by the manifest rather than by a rule:
    adding a COPY is by itself enough to make every edit to that file runtime.
    A runtime that starts reading a doc has to ship it, and shipping it arms
    this."""
    r, _ = repo
    df = (r.root / "Dockerfile").read_text(encoding="utf-8")
    patched = df.replace(
        "COPY pyproject.toml ./\n", "COPY pyproject.toml ./\nCOPY docs/notes.md ./\n", 1
    )
    assert patched != df, "the anchor COPY line moved; update this test"
    copied = r.commit("ship the notes", {"Dockerfile": patched})
    head = r.commit("notes", {"docs/notes.md": "more\n"})
    decision = _decide(r, copied, head)
    assert decision.build is True
    assert decision.runtime_paths == ("docs/notes.md",)


def test_no_dockerfile_makes_everything_runtime(repo):
    r, base = repo
    gone = r.commit("drop df", {"Dockerfile": None})
    head = r.commit("docs", {"docs/notes.md": "more\n"})
    assert _decide(r, gone, head).build is True


def test_unreadable_host_manifest_makes_all_scripts_runtime(repo):
    r, _ = repo
    gone = r.commit("drop manifest", {"deploy/install-host-uptime-services.sh": None})
    head = r.commit("tool", {"scripts/unrelated_tool.py": "TOOL = 2\n"})
    assert _decide(r, gone, head).build is True


# --- what production serves -------------------------------------------------


def test_unknown_served_sha_builds(repo):
    r, base = repo
    head = r.commit("docs", {"docs/notes.md": "more\n"})
    assert _decide(r, None, head).build is True
    assert _decide(r, "", head).build is True


def test_served_sha_unknown_to_git_builds(repo):
    r, _ = repo
    head = r.commit("docs", {"docs/notes.md": "more\n"})
    assert _decide(r, "0" * 40, head).build is True


def test_served_sha_off_the_head_line_builds(repo):
    """A branch image deployed by hand: the head does not descend from it."""
    r, base = repo
    r.git("checkout", "-q", "-b", "side")
    side = r.commit("side", {"docs/notes.md": "side\n"})
    r.git("checkout", "-q", "main")
    head = r.commit("docs", {"docs/other.md": "main\n"})
    decision = _decide(r, side, head)
    assert decision.build is True
    assert "not an ancestor" in decision.reason


def test_unresolvable_head_builds(repo):
    r, base = repo
    assert _decide(r, base, "no-such-ref").build is True


def test_a_git_failure_builds(repo, monkeypatch):
    r, base = repo
    head = r.commit("docs", {"docs/notes.md": "more\n"})

    def broken(*_a, **_k):
        raise rp.ClassifyError("simulated git failure")

    monkeypatch.setattr(rp, "changed_paths", broken)
    decision = _decide(r, base, head)
    assert decision.build is True
    assert "could not classify" in decision.reason


def test_decide_cli_writes_github_outputs(repo, tmp_path):
    r, base = repo
    head = r.commit("docs", {"docs/notes.md": "more\n"})
    out = tmp_path / "gh-output"
    argv = ["--repo", str(r.root), "decide", "--base", base, "--head", head]
    assert rp.main([*argv, "--github-output", str(out)]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "decision=skip"
    assert lines[1].startswith("reason=production's ")


# --- history walks (release-reconcile, deployed_sha) ----------------------------


def test_newest_runtime_commit_walks_past_every_non_runtime_commit(repo):
    r, _ = repo
    runtime = r.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    r.commit("root doc", {"AGENTS.md": "agents x\n"})
    head = r.commit("docs", {"docs/notes.md": "more\n"})
    assert rp.newest_runtime_commit(r.root, head) == runtime


def test_newest_runtime_commit_fails_open_when_the_walk_runs_out(repo):
    r, _ = repo
    r.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    head = r.commit("docs", {"docs/notes.md": "more\n"})
    assert rp.newest_runtime_commit(r.root, head, limit=1) == head


def test_runtime_commits_counts_only_runtime_commits(repo):
    r, base = repo
    runtime = r.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    r.commit("root doc", {"AGENTS.md": "agents x\n"})
    head = r.commit("docs", {"docs/notes.md": "more\n"})
    assert rp.runtime_commits(r.root, base, head) == [runtime]


# --- the workflows agree with the classifier ---------------------------------


def _working_tree(path: str) -> str | None:
    target = REPO_ROOT / path
    return target.read_text(encoding="utf-8") if target.is_file() else None


def _working_tree_workflows() -> list[str]:
    return sorted(
        f".github/workflows/{p.name}" for p in WORKFLOWS.iterdir() if p.is_file()
    )


def _working_tree_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return [line for line in out.splitlines() if (REPO_ROOT / line).is_file()]


def _working_tree_inputs():
    return rp.runtime_inputs_from(_working_tree, _working_tree_files)


def _reconcile_path_parser() -> str:
    """The exact inline parser release-reconcile.yml uses to read the filter."""
    wf = yaml.safe_load((WORKFLOWS / "release-reconcile.yml").read_text(encoding="utf-8"))
    step = next(
        s
        for s in wf["jobs"]["reconcile"]["steps"]
        if s.get("name") == "Compare main against the last successful deploy"
    )
    script = step["run"]
    start = script.index("<<'PY'") + len("<<'PY'")
    return script[start : script.index("\nPY\n", start)].lstrip("\n")


def _build_image_push_paths(tmp_path: Path) -> list[str]:
    """build-image's push filter AS RELEASE-RECONCILE READS IT."""
    parser = tmp_path / "reconcile_paths.py"
    parser.write_text(_reconcile_path_parser(), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(parser)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def test_reconcile_reads_every_path_in_the_build_filter(tmp_path):
    """The filter carries comment lines; reconcile's parser once stopped at the
    first one and silently saw 10 paths."""
    wf = yaml.safe_load((WORKFLOWS / "build-image.yml").read_text(encoding="utf-8"))
    declared = list(wf.get("on", wf.get(True))["push"]["paths"])
    assert _build_image_push_paths(tmp_path) == declared


def _filter_covers(patterns: list[str], path: str) -> bool:
    for pattern in patterns:
        if pattern.endswith("/**"):
            prefix = pattern[: -len("**")]
            if path.rstrip("/") + "/" == prefix or path.startswith(prefix):
                return True
        elif pattern == path.rstrip("/"):
            return True
    return False


def test_build_image_path_filter_is_a_superset_of_every_runtime_input(tmp_path):
    """The dangerous direction: a runtime input the pre-filter never sees is a
    change that never builds, never deploys, and never alarms. Read through
    release-reconcile's own parser, so the backstop sees the same list."""
    inputs = _working_tree_inputs()
    assert inputs.everything is False
    patterns = _build_image_push_paths(tmp_path)
    missing = [p for p in inputs.paths if not _filter_covers(patterns, p)]
    assert missing == [], f"build-image.yml push paths omit runtime inputs: {missing}"


@pytest.mark.parametrize("workflow", ["deploy-prod.yml", "install-host-services.yml"])
def test_every_script_the_deploy_workflows_ship_is_runtime(workflow):
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    named = set(re.findall(r"scripts/[A-Za-z0-9_.-]+\.py", text))
    assert named, f"{workflow} names no scripts -- the scan is broken"
    inputs = _working_tree_inputs()
    assert sorted(p for p in named if not inputs.covers(p)) == []


# --- the deploy chain is runtime (#3936 shape) --------------------------------


def test_real_deploy_chain_is_derived_from_the_workflows():
    chain = rp.deploy_chain_workflows(
        {p: _working_tree(p) for p in _working_tree_workflows() if p.endswith(".yml")}
    )
    assert chain is not None
    assert {
        ".github/workflows/build-image.yml",
        ".github/workflows/deploy-prod.yml",
        ".github/workflows/install-host-services.yml",
    } <= set(chain)
    # Post-deploy observers hold no host credential: not part of what deploys.
    assert ".github/workflows/uptime-canary.yml" not in chain
    assert ".github/workflows/tests.yml" not in chain


def test_deploy_chain_follows_triggers_calls_and_the_host_group():
    workflows = {
        ".github/workflows/build-image.yml": "name: Build\n",
        ".github/workflows/a.yml": (
            "name: A\non:\n  workflow_run:\n    workflows: ['Build']\n"
            "jobs:\n  x:\n    uses: ./.github/workflows/called.yml\n"
            "    secrets: ${{ secrets.DO_SSH_KEY }}\n"
        ),
        ".github/workflows/called.yml": "name: Called\n",
        ".github/workflows/b.yml": (
            "name: B\non:\n  workflow_run:\n    workflows:\n    - A\n"
            "env:\n  K: ${{ secrets.DO_SSH_KEY }}\n"
        ),
        ".github/workflows/observer.yml": (
            "name: Obs\non:\n  workflow_run:\n    workflows: [\"B\"]\n"
        ),
        ".github/workflows/manual.yml": (
            "name: Manual\nconcurrency:\n  group: 'production-host-mutation'\n"
        ),
        ".github/workflows/other.yml": "name: Other\n",
    }
    assert rp.deploy_chain_workflows(workflows) == [
        ".github/workflows/a.yml",
        ".github/workflows/b.yml",
        ".github/workflows/build-image.yml",
        ".github/workflows/called.yml",
        ".github/workflows/manual.yml",
    ]


def test_a_deploy_workflow_edit_builds(repo):
    """#3936 shape: only deploy-prod.yml changed. Inert until the next deploy,
    so it must deploy itself -- never read as runtime-equivalent."""
    r, base = repo
    text = (r.root / ".github/workflows/deploy-prod.yml").read_text(encoding="utf-8")
    head = r.commit("deploy tweak", {".github/workflows/deploy-prod.yml": text + "# x\n"})
    decision = _decide(r, base, head)
    assert decision.build is True
    assert decision.runtime_paths == (".github/workflows/deploy-prod.yml",)


@pytest.mark.parametrize(
    "path", [".github/workflows/install-host-services.yml", ".github/workflows/build-image.yml"]
)
def test_every_deploy_chain_edit_builds(repo, path):
    r, base = repo
    text = (r.root / path).read_text(encoding="utf-8")
    head = r.commit("chain tweak", {path: text + "# x\n"})
    assert _decide(r, base, head).runtime_paths == (path,)


def test_an_observer_or_ci_workflow_edit_skips(repo):
    r, base = repo
    head = r.commit(
        "ci",
        {
            ".github/workflows/uptime-canary.yml": "name: Uptime canary\n# edited\n",
            ".github/workflows/tests.yml": "name: Tests\n# edited\n",
        },
    )
    assert _decide(r, base, head).build is False


def test_unreadable_deploy_chain_makes_every_workflow_runtime(repo):
    r, _ = repo
    gone = r.commit("drop root", {".github/workflows/build-image.yml": None})
    head = r.commit("ci", {".github/workflows/tests.yml": "name: Tests\n# edited\n"})
    assert _decide(r, gone, head).build is True


def test_the_real_3936_merge_builds():
    """#3936 touched deploy-prod.yml, recovery-retag-image.yml, a review doc
    and a test. It changed what the next deploy does to the host."""
    sha = "92ee9488"
    if subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=REPO_ROOT, capture_output=True
    ).returncode:
        pytest.skip("shallow checkout lacks #3936")
    decision = rp.decide(REPO_ROOT, f"{sha}^", sha)
    assert decision.build is True
    assert ".github/workflows/deploy-prod.yml" in decision.runtime_paths


# --- the Python the deploy runs, and everything it imports (round 2) ---------


def test_a_helper_the_deploy_imports_builds(repo):
    """deploy-prod runs prepare_state.py, which imports preflight_helper.py via
    the sys.path-insert idiom. A helper-only commit changes what the next deploy
    installs on the host."""
    r, base = repo
    head = r.commit(
        "helper", {"scripts/preflight_helper.py": "def resolve_expected():\n    return {}\n"}
    )
    decision = _decide(r, base, head)
    assert decision.build is True
    assert decision.runtime_paths == ("scripts/preflight_helper.py",)


def test_real_deploy_python_closure_includes_the_preflight_helper():
    """prepare_expected_instance_state.py imports resolve_expected_droplet from
    cloud_only_preflight.py; its output is installed on the droplet."""
    inputs = _working_tree_inputs()
    assert inputs.notes == ()
    for path in (
        "scripts/prepare_expected_instance_state.py",
        "scripts/cloud_only_preflight.py",
        "scripts/retire_cheat_loop_deploy_fence.py",
    ):
        assert inputs.covers(path), path


def _closure(files: dict[str, str], seeds: list[str]):
    return rp.python_import_closure(
        seeds, set(files), files.get, lambda path: path.startswith("image/")
    )


def test_closure_follows_every_local_import_form():
    files = {
        "scripts/run.py": (
            "import os, yaml\n"
            "import helper_a\n"
            "from pkg import sub\n"
            "from pkg.deep import thing\n"
            "def f():\n    import late_helper\n"
            "import image.module\n"
        ),
        "scripts/helper_a.py": "from shared import X\n",
        "scripts/late_helper.py": "",
        "shared.py": "X = 1\n",
        "scripts/pkg/__init__.py": "from .inner import y\nfrom . import sibling\n",
        "scripts/pkg/inner.py": "from ..helper_a import *\n",
        "scripts/pkg/sibling.py": "",
        "scripts/pkg/sub.py": "",
        "scripts/pkg/deep.py": "",
        "image/__init__.py": "",
        "image/module.py": "import never_walked_because_image_is_runtime\n",
        "scripts/unused.py": "",
    }
    closure, unresolved = _closure(files, ["scripts/run.py"])
    assert unresolved == []
    assert set(closure) == set(files) - {"scripts/unused.py"} | {"scripts/pkg/__init__.py"}


@pytest.mark.parametrize(
    ("source", "why"),
    [
        ("import pkg.missing\n", "pkg.missing"),
        ("import importlib\nimportlib.import_module(NAME)\n", "dynamic import"),
        ("def broken(:\n", "scripts/run.py"),
    ],
)
def test_an_unresolvable_import_is_reported(source, why):
    files = {"scripts/run.py": source, "scripts/pkg/__init__.py": ""}
    _, unresolved = _closure(files, ["scripts/run.py"])
    assert any(why in item for item in unresolved), unresolved


def test_an_unresolvable_import_makes_all_scripts_runtime(repo):
    r, _ = repo
    broken = r.commit(
        "dynamic", {"scripts/preflight_helper.py": "import importlib\nimportlib.import_module(X)\n"}
    )
    head = r.commit("tool", {"scripts/unrelated_tool.py": "TOOL = 2\n"})
    decision = _decide(r, broken, head)
    assert decision.build is True
    assert decision.runtime_paths == ("scripts/unrelated_tool.py",)


def test_a_named_script_that_does_not_exist_fails_open(repo):
    r, _ = repo
    wf = ".github/workflows/deploy-prod.yml"
    text = (r.root / wf).read_text(encoding="utf-8")
    renamed = r.commit("stale", {wf: text + "      - run: python scripts/gone.py\n"})
    head = r.commit("tool", {"scripts/unrelated_tool.py": "TOOL = 2\n"})
    assert _decide(r, renamed, head).build is True


def test_a_composite_action_the_chain_uses_is_runtime(repo):
    r, _ = repo
    wf = ".github/workflows/deploy-prod.yml"
    text = (r.root / wf).read_text(encoding="utf-8")
    with_action = r.commit(
        "action",
        {
            wf: text + "      - uses: ./.github/actions/prep\n",
            ".github/actions/prep/action.yml": (
                "runs:\n  using: composite\n  steps:\n"
                "    - run: python scripts/action_tool.py\n      shell: bash\n"
            ),
            "scripts/action_tool.py": "import action_dep\n",
            "scripts/action_dep.py": "",
        },
    )
    for path, content in (
        ("scripts/action_dep.py", "X = 1\n"),
        (".github/actions/prep/action.yml", "runs:\n  using: composite\n  steps: []\n"),
    ):
        head = r.commit("edit", {path: content})
        assert _decide(r, with_action, head).build is True, path
        with_action = head
