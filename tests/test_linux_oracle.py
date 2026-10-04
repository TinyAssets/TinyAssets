"""Container bootstrap regressions; actual oracle run supplies integration proof."""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

from scripts import linux_oracle


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
