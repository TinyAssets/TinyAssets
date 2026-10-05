"""Container bootstrap regressions; actual oracle run supplies integration proof."""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

from scripts import linux_oracle


def test_production_probe_pins_digest_and_exact_entry_authority(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="sha256:fixture\n")

    monkeypatch.setattr(subprocess, "run", run)
    args = linux_oracle.build_parser().parse_args(["--production-image", "image:built"])
    assert linux_oracle.production_oracle(args, Path("/repo")) == 0
    command = calls[-1]
    assert "sha256:fixture" in command and "image:built" not in command
    assert "--network" in command and "none" in command
    assert "--mount" not in command and "-v" not in command
    assert [command[i + 1] for i, part in enumerate(command) if part == "--cap-add"] == [
        "CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "SETPCAP", "KILL",
    ]
    assert command[-1] == "/app/scripts/role_image_oracle.py"


def test_production_probe_refuses_skip_and_authority_overrides(monkeypatch):
    import pytest

    def unexpected(*args, **kwargs):
        raise AssertionError("override must refuse before Docker")

    monkeypatch.setattr(subprocess, "run", unexpected)
    for extra in (["--no-bwrap"], ["--as-root"], ["--env", "X=Y"],
                  ["--shell"], ["--", "tests"], ["--out", "/tmp/output"]):
        args = linux_oracle.build_parser().parse_args(["--production-image", "image", *extra])
        with pytest.raises(SystemExit, match="refuses"):
            linux_oracle.production_oracle(args, Path("/repo"))


def test_classic_builder_gets_real_dependency_generation():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / linux_oracle.DOCKERFILE).read_text(encoding="utf-8")
    # Execute the actual Docker RUN's Python payload, not a duplicate generator.
    # A classic builder silently ignored the previous Docker-heredoc payload.
    match = re.search(
        r'^RUN python -c "(.+)" > /tmp/oracle/requirements\.txt', dockerfile, re.MULTILINE
    )
    assert match is not None
    payload = match.group(1).replace(
        "'/tmp/oracle/pyproject.toml'", repr((root / "pyproject.toml").as_posix())
    )
    result = subprocess.run(
        [sys.executable, "-c", payload], capture_output=True, text=True, check=True
    )
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert result.stdout.splitlines() == (
        project["dependencies"] + project["optional-dependencies"]["dev"]
        + project["optional-dependencies"]["browser"]
    )
    assert result.stdout.strip()
    assert "test -s /tmp/oracle/requirements.txt" in dockerfile
    assert "python -m pytest --version" in dockerfile


def test_the_oracle_pins_the_image_s_codex():
    """The jail proofs run the REAL codex, so the oracle must carry the version
    the daemon image ships: a different one would prove the jail's behaviour for
    a codex we do not run. They are two files, so assert they agree.

    tests/test_provider_jail_codex_nested.py also reaches past the npm shim for
    the vendored native binary, and linux-jail-proof fails on any skip -- so the
    build checks both the shim and that binary rather than trusting the install.
    """
    root = Path(__file__).resolve().parents[1]
    oracle = (root / linux_oracle.DOCKERFILE).read_text(encoding="utf-8")
    shipped = (root / "Dockerfile").read_text(encoding="utf-8")
    pin = r"ARG CODEX_CLI_VERSION=(\d+\.\d+\.\d+)"
    here, there = re.search(pin, oracle), re.search(pin, shipped)
    assert here and there, "both images pin the codex CLI by ARG"
    assert here.group(1) == there.group(1), (
        f"the oracle pins codex {here.group(1)}, the daemon image ships "
        f"{there.group(1)}; the jail proof must run what production runs")
    # Production's path, because the test resolves `codex` on exactly that dir.
    assert '--prefix /opt/codex-install "@openai/codex@${CODEX_CLI_VERSION}"' in oracle
    assert "/opt/codex-install/node_modules/.bin/codex --version" in oracle
    assert "-path '*/vendor/*/bin/codex'" in oracle


def test_snapshot_preserves_container_ownership_without_trusting_host_git():
    assert ".git" in linux_oracle.COPY_EXCLUDES
    assert "tar -C /work --no-same-owner -xf -" in linux_oracle._RUN_SCRIPT
    assert "git init -q ." in linux_oracle._RUN_SCRIPT
    assert "safe.directory" not in linux_oracle._RUN_SCRIPT


def _command(*argv: str) -> list[str]:
    """Parse with the script's own parser; pytest args go after ``--``."""
    flags, rest = [], list(argv)
    while rest and rest[0].startswith("--") and not rest[0].startswith("--basetemp"):
        flag = rest.pop(0)
        flags.append(flag)
        if flag in ("--out", "--apparmor", "--env"):
            flags.append(rest.pop(0))
    args = linux_oracle.build_parser().parse_args([*flags, "--", *rest])
    return linux_oracle.docker_command(args, Path("/repo"), "img:tag")


def _opts(cmd: list[str]) -> list[str]:
    return [cmd[i + 1] for i, part in enumerate(cmd) if part == "--security-opt"]


def _user_script(cmd: list[str]) -> str:
    return next(part for part in cmd if part.startswith("ORACLE_USER_SCRIPT="))


def _pytest_argv(cmd: list[str]) -> list[str]:
    """The argv pytest receives, as the container's bash would split it."""
    import shlex

    line = next(
        ln for ln in _user_script(cmd).splitlines() if ln.startswith("exec python -m pytest")
    )
    return shlex.split(line)[1:]


def test_default_runs_real_jails_as_an_unprivileged_user():
    """As root the universe tool jail refuses to start, so ~20 jail and egress
    tests failed for the oracle's own reasons; the default must not be root."""
    cmd = _command("tests/test_universe_tools_jail.py")
    assert _opts(cmd) == [
        "seccomp=unconfined", "apparmor=unconfined", "systempaths=unconfined",
    ]
    script = cmd[-1]
    assert f"useradd -u {linux_oracle.ORACLE_UID} -m oracle" in script
    assert "exec runuser -u oracle --" in script
    assert "chown -R oracle /work" in script
    # The suite and its repository run as that user, not the bootstrap root.
    assert "git init -q ." not in script
    user = _user_script(cmd)
    assert "git init -q ." in user
    assert "tests/test_universe_tools_jail.py" in _pytest_argv(cmd)


def test_default_basetemp_is_short_and_a_given_one_wins():
    # An AF_UNIX path must fit in 108 bytes; the long default tmp root broke it.
    assert f"--basetemp={linux_oracle.DEFAULT_BASETEMP}" in _pytest_argv(_command("-q"))
    assert len(linux_oracle.DEFAULT_BASETEMP) <= 8
    given = _user_script(_command("-q", "--basetemp=/tmp/mine"))
    assert "--basetemp=/tmp/mine" in _pytest_argv(_command("-q", "--basetemp=/tmp/mine"))
    assert linux_oracle.DEFAULT_BASETEMP not in given


def test_as_root_keeps_the_old_run():
    cmd = _command("--as-root", "-q")
    assert _opts(cmd) == ["seccomp=unconfined"]
    assert not any(part.startswith("ORACLE_USER_SCRIPT=") for part in cmd)
    assert "runuser" not in cmd[-1]
    assert "git init -q ." in cmd[-1]


def test_no_bwrap_drops_every_relaxation_in_both_modes():
    assert _opts(_command("--no-bwrap")) == []
    assert _opts(_command("--no-bwrap", "--as-root")) == []


def test_each_run_is_named_so_a_lane_stops_only_its_own():
    cmd = _command("-q")
    name = cmd[cmd.index("--name") + 1]
    assert name.startswith("ta-oracle-")


def test_ci_mode_options_reach_the_container():
    """CI's linux-jail-proof calls this same command: an output mount for the
    junit, its own AppArmor profile, and environment for the suite."""
    cmd = _command(
        "--out", "/runner/out", "--apparmor", "ta-jail-userns",
        "--env", "TINYASSETS_DATA_DIR=/tmp/ta-data",
        "-m", "real_jail", "--junitxml", "/out/junit.xml", "--basetemp", "/tmp/b",
    )
    assert "apparmor=ta-jail-userns" in _opts(cmd)
    assert cmd[cmd.index("-v", cmd.index("-v") + 1) + 1].endswith("/runner/out:/out")
    assert cmd[cmd.index("TINYASSETS_DATA_DIR=/tmp/ta-data") - 1] == "-e"
    user = _user_script(cmd)
    argv = _pytest_argv(cmd)
    assert argv[argv.index("--junitxml") + 1] == "/out/junit.xml"
    # A space-separated --basetemp is the caller's; no second one is appended.
    assert user.count("--basetemp") == 1


def test_env_without_a_value_is_refused():
    import pytest

    with pytest.raises(SystemExit, match="KEY=VALUE"):
        _command("--env", "NOVALUE", "-q")


def test_a_jail_that_cannot_be_made_fails_the_run_instead_of_skipping():
    user = _user_script(_command("-q"))
    assert user.index("bwrap --die-with-parent") < user.index("python -m pytest")
    assert "exit 3" in user
    # --no-bwrap exists to prove the jail tests SKIP; it must not probe.
    assert "bwrap --die-with-parent" not in _user_script(_command("--no-bwrap", "-q"))


def test_arguments_with_shell_metacharacters_reach_pytest_unchanged():
    tricky = ["tests/test_jail's.py", "-k", "a and not b'c", "$HOME", "x;y"]
    assert _pytest_argv(_command(*tricky))[-len(tricky) - 1:-1] == tricky


def test_a_failed_source_copy_stops_the_run():
    script = _command("-q")[-1]
    assert "set -o pipefail" in script
    assert script.index("set -o pipefail") < script.index("tar -C /src")


def test_the_image_shares_chromium_with_the_unprivileged_user():
    dockerfile = (Path(__file__).resolve().parents[1] / linux_oracle.DOCKERFILE).read_text(
        encoding="utf-8"
    )
    assert "ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright" in dockerfile
    assert "RUN python -m playwright install --with-deps chromium" in dockerfile
    assert "chmod -R a+rX /opt/playwright" in dockerfile
    # Chromium keeps its own sandbox, and nothing is hidden under root's cache.
    assert "/root/.cache/ms-playwright" not in dockerfile
    assert "--no-sandbox " not in dockerfile and "no-sandbox=" not in dockerfile


# ---- --required-runner: the merge gate's shards, in this venue ----------------

_SHARD_ARGS = [
    "--junit", "/out/junit-shard-3.xml",
    "--exclude-from", ".github/heavy-test-files.txt",
    "--shard", "3/6", "--profile", "shard",
]


def _runner_command(*flags: str, runner_args: list[str] | None = None) -> list[str]:
    rest = _SHARD_ARGS if runner_args is None else runner_args
    args = linux_oracle.build_parser().parse_args(["--required-runner", *flags, "--", *rest])
    return linux_oracle.docker_command(args, Path("/repo"), "img:tag")


def _exec_argv(cmd: list[str]) -> list[str]:
    """The argv the container execs, as its bash would split it."""
    import shlex

    line = next(ln for ln in _user_script(cmd).splitlines() if ln.startswith("exec "))
    return shlex.split(line)[1:]


def test_required_runner_execs_the_gate_script_with_the_callers_arguments_unchanged():
    cmd = _runner_command(
        "--out", "/runner/shard-out", "--apparmor", "ta-jail-userns",
        "--env", "TINYASSETS_DATA_DIR=/tmp/ta-data",
    )
    # The runner's command line arrives whole and in order; the only addition is
    # the short temp root outside the repo that the pytest mode also adds.
    assert _exec_argv(cmd) == [
        "python", "scripts/ci_required_tests.py", *_SHARD_ARGS,
        "--pytest-arg=--basetemp=/tmp/b",
    ]
    assert "pytest -p no:cacheprovider" not in _user_script(cmd)
    # Same venue as linux-jail-proof: unprivileged, probed, three options only.
    assert _opts(cmd) == [
        "seccomp=unconfined", "apparmor=ta-jail-userns", "systempaths=unconfined",
    ]
    assert linux_oracle.JAIL_PROBE in _user_script(cmd)
    assert "runuser -u oracle" in cmd[-1]
    assert not {"--privileged", "--cap-add", "--pid", "--network", "-it"} & set(cmd)
    # --junit /out/x.xml lands in the caller's directory, manifest beside it.
    assert cmd[cmd.index("-v", cmd.index("-v") + 1) + 1].endswith("/runner/shard-out:/out")


def test_required_runner_arguments_parse_and_write_where_the_aggregate_reads(monkeypatch):
    """The gate script itself parses what the oracle hands it, and derives the
    manifest path from --junit, so both land in the bound directory under the
    names the plan step and the aggregate already use."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ci_required_tests_for_oracle",
        Path(__file__).resolve().parents[1] / linux_oracle.REQUIRED_RUNNER,
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    argv = _exec_argv(_runner_command("--out", "/runner/shard-out"))
    assert argv[:2] == ["python", linux_oracle.REQUIRED_RUNNER]

    parsed = {}
    real_parse = runner.argparse.ArgumentParser.parse_args

    def _parse_then_stop(self, args=None, namespace=None):
        parsed["ns"] = real_parse(self, argv[2:], namespace)
        raise KeyboardInterrupt  # stop before anything runs

    monkeypatch.setattr(runner.argparse.ArgumentParser, "parse_args", _parse_then_stop)
    try:
        runner.main()
    except KeyboardInterrupt:
        pass
    ns = parsed["ns"]
    assert ns.pytest_arg == ["--basetemp=/tmp/b"]
    junit = Path(ns.junit)
    assert junit.as_posix() == "/out/junit-shard-3.xml"
    assert junit.with_suffix(".json").as_posix() == "/out/junit-shard-3.json"
    assert ns.exclude_from == ".github/heavy-test-files.txt" and ns.profile == "shard"


def test_required_runner_keeps_a_basetemp_the_caller_chose():
    cmd = _runner_command(
        "--out", "/o", runner_args=[*_SHARD_ARGS, "--pytest-arg=--basetemp=/tmp/x"],
    )
    assert _user_script(cmd).count("--basetemp") == 1


def test_required_runner_refuses_every_weaker_venue():
    import pytest

    for flag in ("--no-bwrap", "--as-root", "--shell"):
        with pytest.raises(SystemExit, match="--required-runner refuses"):
            _runner_command("--out", "/o", flag)


def test_required_runner_refuses_outputs_the_caller_would_never_see():
    import pytest

    with pytest.raises(SystemExit, match="--out DIR"):
        _runner_command()  # no --out
    for bad in (["--shard", "1/6"], ["--junit", "shard-out/j.xml"],
                ["--junit", "/out/../tmp/lost.xml"], ["--junit", "/out"],
                ["--junit", "/out/a/.."], ["--junit", "/out/dir/"],
                ["--junit", "/out/a.xml", "--junit=/out/b.xml"]):
        with pytest.raises(SystemExit, match="--out DIR"):
            _runner_command("--out", "/o", runner_args=bad)


def test_invalid_required_runner_refuses_before_any_docker_or_output_effect(monkeypatch):
    import pytest

    def unexpected(*_args, **_kwargs):
        pytest.fail("invalid required runner reached a Docker or output side effect")

    monkeypatch.setattr(linux_oracle.shutil, "which", unexpected)
    monkeypatch.setattr(linux_oracle.subprocess, "run", unexpected)
    monkeypatch.setattr(linux_oracle, "_build", unexpected)
    monkeypatch.setattr(Path, "mkdir", unexpected)
    for bad in ("--no-bwrap", "--as-root", "--shell"):
        with pytest.raises(SystemExit, match="--required-runner refuses"):
            linux_oracle.main([
                "--required-runner", "--build", "--out", "/o", bad,
                "--", "--junit", "/out/j.xml",
            ])
    with pytest.raises(SystemExit, match="--out DIR"):
        linux_oracle.main([
            "--required-runner", "--build", "--out", "/o",
            "--", "--junit", "/out/../lost.xml",
        ])


def test_default_mode_is_still_pytest():
    assert _pytest_argv(_command("-q"))[:4] == ["python", "-m", "pytest", "-p"]
    assert "ci_required_tests" not in _user_script(_command("-q"))
