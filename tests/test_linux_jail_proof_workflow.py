"""Shape invariants for .github/workflows/linux-jail-proof.yml.

The job exists because `required-tests` records every bwrap-gated jail proof
(`tests/test_delivery_node_rpc.py::test_real_linux_jail_transports_delivery_rpc`
and the owner-cell seccomp profiles in `tests/test_jail_seccomp_profiles.py`) as
SKIPPED (no bubblewrap on the hosted image), and a skip is invisible in a green
run. Each assertion here pins a property whose loss would turn the job
back into decoration or widen it past "cloud-only test infrastructure":

  - it fires on PRs touching the jail/delivery slice and on manual dispatch,
    nothing else (a schedule or push trigger would make it a deploy-adjacent
    job it is not);
  - it runs on an ephemeral GitHub-hosted Ubuntu VM with read-only contents,
    no persisted credentials, no secrets, no environment, no container;
  - it never relaxes kernel/AppArmor protection and never adds a privileged
    daemon: the only escalation is `sudo -n` on the same bwrap/pytest argv;
  - it runs ONLY the named jail-proof modules, never the suite or the required
    gate;
  - its verdict is the named-case JUnit assertion over EVERY case marked
    `@pytest.mark.real_jail`, run `always()`, so pytest's exit 0 on a skip
    cannot pass the job. The marker is the single definition of the case set;
    there is no list here or in the workflow to keep in step;
  - no `run:` block interpolates a `${{ }}` expression (values reach the shell
    through `env:` only).

The assertion helper is exercised on synthetic xunit1 reports for every state
it must distinguish, and every marked case is checked to be bwrap-gated and to
retrigger the proof.

PyYAML is imported hard: skipping this file is how the invariants would go quiet.
"""

from __future__ import annotations

import functools
import importlib.util
import re
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "linux-jail-proof.yml"
_SCRIPT = _REPO / "scripts" / "ci_assert_junit_case.py"
_NODEID = "tests/test_delivery_node_rpc.py::test_real_linux_jail_transports_delivery_rpc"
_RUN_STEP = "Run the jail proof modules"
_JOB = "linux-jail-proof"

_spec = importlib.util.spec_from_file_location("ci_assert_junit_case", _SCRIPT)
assert _spec and _spec.loader
_assert = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_assert)

_collect_marked_cases = _assert.marked_cases


@functools.cache
def _repository_cases(marker: str, tests_dir: str) -> tuple[str, ...]:
    # This checked-out tree is immutable during a test run. Collect each real
    # marker once; the synthetic JUnit verdicts below vary, not the source set.
    return tuple(_collect_marked_cases(_REPO, marker, tests_dir))


@pytest.fixture(autouse=True)
def reuse_unchanged_repository_collection(monkeypatch):
    def collect(root, marker, tests_dir="tests"):
        if Path(root).resolve() == _REPO:
            return list(_repository_cases(marker, tests_dir))
        # Temporary source trees exercise collection changes and failures.
        # They must always call the real collector, without cached results.
        return _collect_marked_cases(root, marker, tests_dir)

    monkeypatch.setattr(_assert, "marked_cases", collect)

# The guarded cases, derived the same way the job derives them: every test
# carrying @pytest.mark.real_jail. Not a hand-pinned list -- that list had to
# mirror the workflow's and drifted three times.
@functools.cache
def _marked() -> list[str]:
    # Called inside tests only, never at import: collecting the marked cases
    # imports this module too, and an import-time call would recurse.
    return _assert.marked_cases(_REPO, "real_jail")


def _load() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def _triggers(wf: dict) -> dict:
    return wf[True] if True in wf else wf["on"]


def _job(wf: dict) -> dict:
    jobs = wf["jobs"]
    assert list(jobs) == [_JOB], f"exactly one job named {_JOB!r}, got {list(jobs)}"
    return jobs[_JOB]


def _steps(wf: dict) -> list[dict]:
    return _job(wf)["steps"]


def _step(wf: dict, needle: str) -> dict:
    hits = [s for s in _steps(wf)
            if needle in (s.get("name") or "") or needle in (s.get("uses") or "")]
    assert len(hits) == 1, f"expected one step matching {needle!r}, got {len(hits)}"
    return hits[0]


def _step_index(wf: dict, needle: str) -> int:
    steps = _steps(wf)
    return steps.index(_step(wf, needle))


def _code_text() -> str:
    # The workflow body with comment lines removed: the reach/escalation
    # checks are about what the job DOES, and the header comments legitimately
    # name the things it refuses to do.
    return "\n".join(
        line for line in _text().splitlines() if not line.lstrip().startswith("#")
    )


# --- triggers ---------------------------------------------------------------

def test_triggers_are_pull_request_paths_plus_dispatch_only():
    triggers = _triggers(_load())
    assert set(triggers) == {"pull_request", "workflow_dispatch"}
    paths = triggers["pull_request"]["paths"]
    assert paths, "pull_request must be path-scoped, not repo-wide"
    for required in (
        ".github/workflows/linux-jail-proof.yml",
        "scripts/ci_assert_junit_case.py",
        "tinyassets/node_sandbox.py",
        "tests/test_delivery_node_rpc.py",
        "tinyassets/providers/codex_provider.py",
        "tests/test_jail_seccomp_profiles.py",
        "tinyassets/providers/provider_jail.py",
        "tinyassets/providers/owned_process.py",
        "tinyassets/providers/router.py",
        "tinyassets/providers/claude_provider.py",
        "tests/test_universe_tools_jail.py",
        "tinyassets/universe_tools.py",
        "tinyassets/engine_mcp_server.py",
        "tinyassets/jail_disk.py",
        "tinyassets/storage_accounting.py",
        "tests/test_jail_disk.py",
        "tests/test_storage_accounting.py",
    ):
        assert required in paths, f"{required} must retrigger the proof"


def test_every_literal_trigger_path_exists():
    # A path filter naming a file that no longer exists is a trigger that
    # silently never fires for that file.
    paths = _triggers(_load())["pull_request"]["paths"]
    for path in paths:
        if any(ch in path for ch in "*?["):
            assert list(_REPO.glob(path)), f"glob {path} matches nothing"
        else:
            assert (_REPO / path).is_file(), f"{path} is not a file"


# --- authority / isolation --------------------------------------------------

def test_read_only_contents_and_no_job_level_widening():
    wf = _load()
    assert wf["permissions"] == {"contents": "read"}
    assert "permissions" not in _job(wf)


def test_hosted_ephemeral_runner_only():
    job = _job(_load())
    assert job["runs-on"] == "ubuntu-latest"
    assert "container" not in job, "the VM is the runner; the jail is one docker run"
    assert "environment" not in job, "no deployment environment on a test job"
    assert 0 < job["timeout-minutes"] <= 30


def test_checkout_does_not_persist_credentials():
    step = _step(_load(), "actions/checkout@")
    assert step["with"]["persist-credentials"] is False


def test_no_secrets_self_hosted_desktop_or_production_reach():
    text = _code_text()
    for forbidden in (
        "secrets.", "self-hosted", "DESKTOP-KCPMGP3", "tinyassets.io",
        "deploy", "--privileged", "GITHUB_TOKEN", "--cap-add", "docker push",
        "docker login", "-v /var/run/docker.sock",
    ):
        assert forbidden not in text, f"{forbidden!r} must not appear"


def test_no_kernel_or_apparmor_relaxation():
    text = _code_text()
    assert "sysctl" not in text
    assert "apparmor_restrict_unprivileged_userns=0" not in text
    assert "aa-" not in text  # aa-complain / aa-disable / aa-teardown
    assert "/etc/apparmor" not in text
    # No test runs as root: production runs the jail as uid 1001, and root
    # inside bwrap's user namespace is what made 18 proofs fail for a non-prod
    # reason. The only sudo loads the container's named AppArmor profile.
    sudo_lines = [ln.strip() for ln in text.splitlines() if re.match(r"\s*sudo\s", ln)]
    assert sudo_lines == ['sudo apparmor_parser -r "$PROFILE"'], sudo_lines


def test_run_blocks_never_interpolate_expressions():
    for step in _steps(_load()):
        run = step.get("run")
        if run:
            assert "${{" not in run, f"expression inside shell in step {step.get('name')!r}"


# --- what it runs -----------------------------------------------------------

def test_no_hand_pinned_case_list_remains_in_the_workflow():
    """The marker is the list. A second copy is what kept drifting."""
    wf = _load()
    assert "env" not in wf, "the case set must come from the marker, not workflow env"
    assert "--nodeid" not in _code_text()


def test_the_marker_is_registered_and_guards_every_jail_module():
    pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"real_jail:' in pyproject, "register the marker so a typo is not silent"
    files = {n.split("::")[0] for n in _marked()}
    # The four slices this job exists for (see the workflow header). Losing a
    # whole file's marker would drop its cases without any red.
    assert files >= {
        "tests/test_delivery_node_rpc.py",
        "tests/test_jail_seccomp_profiles.py",
        "tests/test_universe_tools_jail.py",
    }, files


def test_every_marked_file_retriggers_the_proof():
    paths = set(_triggers(_load())["pull_request"]["paths"])
    for path in sorted({n.split("::")[0] for n in _marked()}):
        assert path in paths, f"{path} carries real_jail but does not retrigger the proof"


def test_marked_cases_follows_pytest_collection_not_spelling(tmp_path):
    """Aliases, class marks, nested classes and parameters count as pytest runs them."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_forms.py").write_text(
        "import pytest\n"
        "import pytest as pt\n"
        "mark = pytest.mark\n"
        "@mark.real_jail\n"
        "def test_alias(): pass\n"
        "@pt.mark.real_jail\n"
        "async def test_async(): pass\n"
        "class TestA:\n"
        "    pytestmark = pytest.mark.real_jail\n"
        "    def test_in_class(self): pass\n"
        "    class TestB:\n"
        "        def test_nested(self): pass\n"
        "@pytest.mark.real_jail\n"
        "@pytest.mark.parametrize('v', [1, 2])\n"
        "def test_param(v): pass\n"
        "def test_unmarked(): pass\n",
        encoding="utf-8",
    )
    (tests / "test_silent.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    real_jail: test\n", encoding="utf-8"
    )
    assert _assert.marked_cases(tmp_path, "real_jail") == [
        "tests/test_forms.py::test_alias",
        "tests/test_forms.py::test_async",
        "tests/test_forms.py::TestA::test_in_class",
        "tests/test_forms.py::TestA::TestB::test_nested",
        "tests/test_forms.py::test_param[1]",
        "tests/test_forms.py::test_param[2]",
    ]


def test_a_file_that_cannot_be_collected_refuses_rather_than_guessing(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_broken.py").write_text(
        "import pytest\nraise ImportError('boom')\n@pytest.mark.real_jail\ndef test_a(): pass\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="refusing"):
        _assert.marked_cases(tmp_path, "real_jail")


def test_list_files_and_an_unused_marker(tmp_path, capsys):
    assert _assert.main(["--marker", "real_jail", "--list-files"]) == 0
    listed = capsys.readouterr().out.split()
    assert listed == sorted({n.split("::")[0] for n in _marked()})
    # A marker nobody carries must not read as an all-clear.
    assert _assert.main(["--marker", "no_such_marker", "--list-files"]) == 2
    assert _assert.main(["--marker", "no_such_marker", "--junit", str(tmp_path / "j")]) == 2


def test_assertion_helper_marker_mode_checks_every_marked_case(tmp_path):
    clean = "".join(
        '<testcase classname="{}" name="{}"/>'.format(
            n.split("::")[0][:-3].replace("/", "."), n.split("::")[-1]
        )
        for n in _marked()
    )
    assert _assert.main(["--junit", str(_junit(tmp_path, clean)), "--marker", "real_jail"]) == 0
    dropped = clean.replace(f'name="{_marked()[-1].split("::")[-1]}"', 'name="test_renamed"')
    assert _assert.main(["--junit", str(_junit(tmp_path, dropped)), "--marker", "real_jail"]) == 1


def test_an_explicit_nodeid_still_matches_exactly(tmp_path):
    """A base name is not satisfied by one of its parameters: that hides the rest."""
    case = ('<testcase classname="tests.test_delivery_node_rpc" '
            'name="test_real_linux_jail_transports_delivery_rpc[a]"/>')
    assert _assert.check(_junit(tmp_path, case), _NODEID)[0] == 1


def test_every_marked_case_is_a_bwrap_gated_test():
    for nodeid in _marked():
        _assert_bwrap_gated(nodeid)


def _assert_bwrap_gated(nodeid: str) -> None:
    path, name = nodeid.split("::")[0], nodeid.split("::")[-1].split("[")[0]
    src = (_REPO / path).read_text(encoding="utf-8")
    assert re.search(rf"^\s*(async\s+)?def {re.escape(name)}\(", src, re.M), (
        f"{name} must exist"
    )
    # The gate takes several forms (a decorator, a module pytestmark, a named
    # skipif marker, a fixture that skips); what they share is the condition.
    # A real_jail test in a file with no bwrap condition at all would run, and
    # fail, everywhere without bwrap instead of skipping.
    assert re.search(r'which\("bwrap"\)', src), f"{path} must skip {name} without bwrap"


def test_the_jail_runs_through_the_shared_oracle_as_uid_1001():
    """CI calls scripts/linux_oracle.py, the invocation developers run locally.

    The oracle owns the image, uid 1001, the seccomp/systempaths relaxation and
    the fail-loud bubblewrap probe (exit 3 before pytest). CI adds only the
    AppArmor profile its kernel needs, loaded in the step before.
    """
    wf = _load()
    run = _step(wf, _RUN_STEP)["run"]
    assert "python scripts/linux_oracle.py --out \"$OUT_DIR\" --apparmor ta-jail-userns" in run
    assert "docker run" not in run and "docker build" not in _code_text(), (
        "a second, inline container recipe is what the oracle replaced"
    )
    assert "--as-root" not in run and "--no-bwrap" not in run
    profile = _step(wf, "Allow user namespaces for the jail container only")["run"]
    assert "profile ta-jail-userns flags=(unconfined)" in profile
    assert "'  userns,'" in profile, "the one permission the kernel withholds"
    assert "sudo apparmor_parser -r" in profile
    assert (_step_index(wf, "Allow user namespaces for the jail container only")
            < _step_index(wf, _RUN_STEP))
    paths = _triggers(wf)["pull_request"]["paths"]
    for path in ("docker/linux-oracle.Dockerfile", "scripts/linux_oracle.py"):
        assert path in paths, f"{path} must retrigger the proof"


def test_pytest_step_is_focused_and_off_repo():
    step = _step(_load(), _RUN_STEP)
    run = step["run"]
    assert "ci_assert_junit_case.py --marker real_jail --list-files" in run
    assert 'mapfile -t files <<< "$listed"' in run
    assert '"${files[@]}"' in run
    assert "-m real_jail" in run, "only the marked cases run, not the whole files"
    assert "ci_required_tests.py" not in run, "never the required gate"
    assert not re.search(r"pytest\s+tests(\s|$|/?\")", run), "never the whole suite"
    assert "--junitxml /out/junit-linux-jail.xml" in run
    assert "--basetemp /tmp/b" in run, "a short temp root: AF_UNIX paths cap at 108 bytes"
    assert "junit_family=xunit1" in run, "the assertion helper reads xunit1"
    assert step["env"]["OUT_DIR"] == "${{ runner.temp }}/jail-out"


def test_assertion_step_always_runs_and_names_the_case():
    wf = _load()
    step = _step(wf, "Assert every real-jail case")
    assert step["if"] == "always()"
    assert "scripts/ci_assert_junit_case.py" in step["run"]
    assert "--marker real_jail" in step["run"]
    run_step = _step(wf, _RUN_STEP)
    assert step["env"]["JUNIT_PATH"] == run_step["env"]["OUT_DIR"] + "/junit-linux-jail.xml"


def test_junit_uploaded_even_on_failure():
    step = _step(_load(), "actions/upload-artifact@")
    assert step["if"] == "always()"
    assert step["with"]["name"] == "junit-linux-jail-proof"
    assert step["with"]["path"].endswith("/junit-linux-jail.xml")


def test_not_the_required_context():
    job = _job(_load())
    assert job["name"] == _JOB
    assert job["name"] != "required-tests"


# --- the assertion helper ---------------------------------------------------

_CASE = ('<testcase classname="tests.test_delivery_node_rpc" '
         'name="test_real_linux_jail_transports_delivery_rpc" time="0.1">{inner}</testcase>')


def _junit(tmp_path: Path, *cases: str) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text('<?xml version="1.0"?><testsuites><testsuite name="pytest">'
                    + "".join(cases) + "</testsuite></testsuites>", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("cases", "code", "fragment"),
    [
        pytest.param([_CASE.format(inner="")], 0, "executed and passed", id="passed"),
        pytest.param(
            [_CASE.format(
                inner='<skipped type="pytest.skip" message="requires Linux bubblewrap"/>')],
            1, "skipped: requires Linux bubblewrap", id="skipped-is-not-pass",
        ),
        pytest.param([_CASE.format(inner='<failure message="boom"/>')],
                     1, "failure: boom", id="failed"),
        pytest.param([_CASE.format(inner='<error message="fixture"/>')],
                     1, "error: fixture", id="errored"),
        pytest.param(
            ['<testcase classname="tests.test_delivery_node_rpc" name="test_other"/>'],
            1, "ABSENT", id="absent-same-module",
        ),
        pytest.param(
            ['<testcase classname="tests.test_other" '
             'name="test_real_linux_jail_transports_delivery_rpc"/>'],
            1, "ABSENT", id="absent-same-name-other-module",
        ),
        pytest.param(
            [_CASE.format(inner=""), _CASE.format(inner='<skipped message="rerun"/>')],
            1, "skipped: rerun", id="any-bad-occurrence-fails",
        ),
    ],
)
def test_assertion_helper_verdicts(tmp_path, cases, code, fragment):
    got, message = _assert.check(_junit(tmp_path, *cases), _NODEID)
    assert got == code, message
    assert fragment in message


def test_assertion_helper_missing_or_broken_report_is_exit_2(tmp_path):
    code, message = _assert.check(tmp_path / "nope.xml", _NODEID)
    assert code == 2 and "no JUnit report" in message
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites><testsuite", encoding="utf-8")
    code, message = _assert.check(broken, _NODEID)
    assert code == 2 and "not parseable" in message


def test_assertion_helper_main_writes_summary_and_exit_code(tmp_path):
    summary = tmp_path / "summary.md"
    junit = _junit(tmp_path, _CASE.format(inner='<skipped message="no bwrap"/>'))
    rc = _assert.main(["--junit", str(junit), "--nodeid", _NODEID, "--summary", str(summary)])
    assert rc == 1
    line = summary.read_text(encoding="utf-8")
    assert line.startswith("- **FAIL**") and _NODEID in line and "no bwrap" in line


_OTHER_NODEID = (
    "tests/test_native_refresh_jail.py::test_removing_the_launch_mask_exposes_the_snapshot"
)
_OTHER_CASE = ('<testcase classname="tests.test_native_refresh_jail" '
               'name="test_removing_the_launch_mask_exposes_the_snapshot" time="0.1">'
               '{inner}</testcase>')


@pytest.mark.parametrize(
    ("first", "second", "code"),
    [
        pytest.param("", "", 0, id="both-passed"),
        pytest.param("", '<skipped message="no bwrap"/>', 1, id="one-skip-fails-the-run"),
        pytest.param('<failure message="boom"/>', "", 1, id="first-bad-second-clean"),
    ],
)
def test_assertion_helper_checks_every_repeated_nodeid(tmp_path, first, second, code):
    summary = tmp_path / "summary.md"
    junit = _junit(tmp_path, _CASE.format(inner=first), _OTHER_CASE.format(inner=second))
    rc = _assert.main(["--junit", str(junit), "--nodeid", _NODEID,
                       "--nodeid", _OTHER_NODEID, "--summary", str(summary)])
    assert rc == code
    lines = summary.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, "one verdict line per guarded case"
    assert _NODEID in lines[0] and _OTHER_NODEID in lines[1]


def test_assertion_helper_absent_second_case_fails(tmp_path):
    junit = _junit(tmp_path, _CASE.format(inner=""))
    rc = _assert.main(["--junit", str(junit), "--nodeid", _NODEID, "--nodeid", _OTHER_NODEID])
    assert rc == 1


def test_assertion_helper_rejects_malformed_nodeid(tmp_path):
    with pytest.raises(SystemExit):
        _assert.check(_junit(tmp_path, _CASE.format(inner="")), "not-a-nodeid")


# ---- --only-files: the selective merge-group browser proof ------------------
#
# Finding 4 from the #4359 review. A selective merge group must still refuse a
# SKIPPED browser proof for a file it selected, but the marked cases OUTSIDE the
# selection are legitimately absent from that union -- and absence is exit 1.


def _marked_browser() -> list[str]:
    return _assert.marked_cases(_REPO, "real_browser")


def _only(tmp_path: Path, *paths: str) -> Path:
    path = tmp_path / "affected.txt"
    path.write_text("".join(f"{p}\n" for p in paths), encoding="utf-8")
    return path


def test_only_files_ignores_a_marked_case_outside_the_selection(tmp_path):
    """The absent-case exit 1 that made this step unusable on a selective run."""
    marked = _marked_browser()
    assert marked, "no test carries real_browser; this contract would be vacuous"
    empty = _junit(tmp_path)
    assert _assert.main(["--junit", str(empty), "--marker", "real_browser"]) == 1
    assert _assert.main([
        "--junit", str(empty), "--marker", "real_browser",
        "--only-files", str(_only(tmp_path, "tests/test_not_a_browser_file.py")),
    ]) == 0


def test_only_files_still_refuses_a_skip_inside_the_selection(tmp_path):
    """The property the whole step exists for: a selected proof may not skip.

    These tests call `pytest.skip` when Chromium will not launch, and coverage
    accepts a skip. Without this, a selective UI change could pass its gate
    having executed no browser proof at all.
    """
    nodeid = _marked_browser()[0]
    file_, name = nodeid.split("::")[0], nodeid.split("::")[-1]
    classname = file_[:-3].replace("/", ".")
    skipped = (
        f'<testcase classname="{classname}" name="{name}"><skipped/></testcase>'
    )
    args = [
        "--junit", str(_junit(tmp_path, skipped)), "--marker", "real_browser",
        "--only-files", str(_only(tmp_path, file_)),
    ]
    assert _assert.main(args) == 1


def test_only_files_passes_a_clean_selected_proof(tmp_path):
    """EVERY marked case in a selected file, not just one of them.

    The restriction is by FILE, so selecting a file asks for all of its proofs.
    One clean case does not cover its siblings -- which is the same
    "no case covers for another" property the unrestricted mode has.
    """
    marked = _marked_browser()
    files = [n.split("::")[0] for n in marked]
    file_ = next(path for path in files if files.count(path) > 1)
    classname = file_[:-3].replace("/", ".")
    in_file = [n for n in marked if n.split("::")[0] == file_]
    assert len(in_file) > 1, "pick a file with several cases or this proves less"
    clean = "".join(
        f'<testcase classname="{classname}" name="{n.split("::")[-1]}"/>' for n in in_file
    )
    assert _assert.main([
        "--junit", str(_junit(tmp_path, clean)), "--marker", "real_browser",
        "--only-files", str(_only(tmp_path, file_)),
    ]) == 0
    # Drop one and it fails: a sibling never covers for a missing proof.
    partial = "".join(
        f'<testcase classname="{classname}" name="{n.split("::")[-1]}"/>' for n in in_file[1:]
    )
    assert _assert.main([
        "--junit", str(_junit(tmp_path, partial)), "--marker", "real_browser",
        "--only-files", str(_only(tmp_path, file_)),
    ]) == 1


def test_only_files_needs_a_marker(tmp_path):
    with pytest.raises(SystemExit):
        _assert.main([
            "--junit", str(_junit(tmp_path)), "--nodeid", "tests/a.py::t",
            "--only-files", str(_only(tmp_path, "tests/a.py")),
        ])
