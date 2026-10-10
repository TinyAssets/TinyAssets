#!/usr/bin/env python3
"""Run the suite on Linux, from the dev box, before pushing.

WHY THIS EXISTS. ``AGENTS.md`` says a local Windows run is not an oracle on its
own. That was a warning; on the workspace change it became six CI rounds. Every
one of those failures had the same shape: the behaviour changed, the Windows
suite went green, and a test that encodes the OLD contract survived because its
assertion only executes on POSIX -- where the old behaviour was still correct.
Two of them were not even reachable on this host at any price: the sandbox jail
needs bubblewrap, which Windows does not have and WSL here does not ship.

So the oracle is a container: the same Python CI uses, with bubblewrap and real
POSIX descriptor semantics, running the WORKING TREE (uncommitted changes
included -- a local oracle that only sees committed code is a slower CI, not an
oracle).

    python scripts/linux_oracle.py -- tests/test_workspace_end_to_end.py -q
    python scripts/linux_oracle.py --shell          # poke around inside
    python scripts/linux_oracle.py --build          # force an image rebuild

WHAT IT IS NOT. It is not a replacement for CI: it runs one Python version on
one architecture, and the container's kernel is the host's. CI stays
authoritative. This exists so a Linux-only mistake is found in a minute here
instead of ten minutes there -- and so the two bubblewrap proofs have somewhere
to run at all.

SECCOMP. The run relaxes seccomp (``--security-opt seccomp=unconfined``)
because Docker's default profile blocks the unprivileged ``clone`` flags
bubblewrap needs, so every jail test would skip and the oracle would quietly
cover less than it claims. It is a throwaway local container with no
credentials and no network access to anything of ours. ``--no-bwrap`` runs
without the relaxation, which is also how you verify the jail tests SKIP rather
than silently pass when bubblewrap is unavailable.

REAL JAILS BY DEFAULT. pytest runs as an unprivileged user (uid 1001), with
AppArmor and Docker's masked system paths relaxed as well, and with a short
``--basetemp``. As root, the universe tool jail refuses to start without a
writable cgroup, so about 20 jail and egress tests went red for the oracle's
own reasons and taught everyone to ignore them. The long default tmp root
(``/tmp/oracle-tmp/pytest-of-root/...``) also pushed the egress proxy's unix
socket past Linux's 108-byte limit. ``--as-root`` keeps the old root run for
anything that needs it.

ONE INVOCATION FOR CI AND LOCAL. CI's linux-jail-proof runs this same command,
so the jail recipe has one definition. The contract it relies on:
``--out DIR`` binds DIR at ``/out``, writable by the suite (pass
``-- --junitxml /out/x.xml``); ``--apparmor PROFILE`` swaps the AppArmor
profile on runners that restrict user namespaces; ``--env KEY=VALUE`` sets
suite environment; the exit code is pytest's, or 3 when bubblewrap cannot
make a jail as the suite's user (a skip is not a pass); stdout is one
``[oracle] ...`` banner line, then pytest's output.

THE REQUIRED-TESTS RUNNER. ``--required-runner`` hands the arguments after
``--`` to ``scripts/ci_required_tests.py`` instead of pytest, so the merge
gate's shards run in this same venue with the gate's own selection, sharding,
floors and quarantine comparison. It is one fixed script, not a command: the
mode refuses ``--shell``, ``--no-bwrap`` and ``--as-root`` (a shard must never
fall back to a venue where the jail tests skip), and requires ``--out`` with a
``--junit`` under ``/out`` so the junit and the manifest the runner writes
beside it reach the caller.
The coordinator retains root only to run the fixed inode-identity proof files;
ordinary tests still run as UID1001 after the jail probe. Both reports become
one shard receipt before the gate evaluates it.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import posixpath
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

IMAGE_REPO = "tinyassets-linux-oracle"
DOCKERFILE = Path("docker/linux-oracle.Dockerfile")
#: Copied into the container, minus what is huge, host-specific, or rebuilt
#: there. ``.git`` is excluded deliberately: in a linked worktree it is a FILE
#: pointing at a path that does not exist in the container, so the copy gets a
#: fresh repository instead (see ``_RUN_SCRIPT``).
COPY_EXCLUDES = (
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "output",
    ".codex-worktrees",
    ".codex-scratch-uptime-canary-1461",
)

#: Runs inside the container. Copies the mounted tree onto the container's own
#: filesystem (a bind mount from Windows is slow and carries host permissions),
#: gives it a real git repository so git-dependent tests have one, then hands
#: over to the command.
_COPY_SCRIPT = r"""
set -e
# A failed source tar must stop the run, not hand pytest a partial snapshot.
set -o pipefail
mkdir -p /work
# Do not restore host uid/gid onto /work: git would reject that copied root as
# dubious ownership even though its newly initialized .git belongs to us.
tar -C /src -cf - {excludes} . | tar -C /work --no-same-owner -xf -
"""

#: Runs as whoever runs the suite. In the default (unprivileged) mode it reaches
#: the container through ``ORACLE_USER_SCRIPT`` rather than being re-quoted.
_REPO_AND_RUN_SCRIPT = r"""
set -e
cd /work
# A real repository, not the host's: the worktree's .git is a file pointing at
# a path this container does not have. History is irrelevant to the suite; a
# valid HEAD is not.
git init -q .
git config user.email oracle@localhost
git config user.name "linux oracle"
git add -A >/dev/null 2>&1 || true
git commit -q -m "linux oracle snapshot" >/dev/null 2>&1 || true
_py=$(python -V 2>&1 | cut -d' ' -f2)
_git=$(git --version | cut -d' ' -f3)
_bwrap=$(bwrap --version 2>/dev/null | cut -d' ' -f2 || echo absent)
echo "[oracle] python $_py | git $_git | bwrap $_bwrap | uid $(id -u)"
exec {command}
"""

#: The old root run: copy, then repository and command, all as root.
_RUN_SCRIPT = _COPY_SCRIPT + _REPO_AND_RUN_SCRIPT

#: Prepended to the unprivileged run unless --no-bwrap: if bubblewrap cannot make
#: a jail as this user, every jail test would SKIP and the run would look green.
#: The flags are the ones node_sandbox.BwrapLauncher uses.
JAIL_PROBE = r"""
if ! bwrap --die-with-parent --new-session --unshare-all \
        --ro-bind / / --proc /proc --dev /dev -- /bin/true; then
    echo "[oracle] bubblewrap cannot create a jail as uid $(id -u); refusing to run" >&2
    exit 3
fi
"""

#: The one script ``--required-runner`` may run, relative to ``/work``.
REQUIRED_RUNNER = "scripts/ci_required_tests.py"

#: Unprivileged user the suite runs as by default.
ORACLE_UID = 1001
#: Short on purpose: an AF_UNIX path must fit in 108 bytes.
DEFAULT_BASETEMP = "/tmp/b"

#: Default run: copy as root, hand the tree to an unprivileged user, run there.
_USER_SCRIPT = _COPY_SCRIPT + r"""
useradd -u {uid} -m oracle
chown -R oracle /work
mkdir -p /tmp/t && chown oracle /tmp/t
exec runuser -u oracle -- env HOME=/home/oracle USER=oracle TMPDIR=/tmp/t \
    bash -c "$ORACLE_USER_SCRIPT"
"""


def _repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(out.stdout.strip())


def _image_tag(root: Path) -> str:
    """Tag the image with what determines its contents, so a dependency change
    rebuilds it and an unrelated edit never does."""
    digest = hashlib.sha256()
    for relative in (DOCKERFILE, Path("pyproject.toml")):
        digest.update((root / relative).read_bytes())
    return f"{IMAGE_REPO}:{digest.hexdigest()[:12]}"


def _image_exists(tag: str) -> bool:
    return subprocess.run(
        ["docker", "image", "inspect", tag],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def _build(root: Path, tag: str) -> None:
    print(f"[oracle] building {tag} (first run pulls and compiles dependencies)")
    result = subprocess.run(
        ["docker", "build", "-f", str(root / DOCKERFILE), "-t", tag, str(root)],
    )
    if result.returncode != 0:
        raise SystemExit(f"[oracle] image build failed ({result.returncode})")


def _docker_path(path: Path) -> str:
    """A host path Docker Desktop accepts as a bind source."""
    text = str(path.resolve())
    if len(text) > 2 and text[1] == ":":  # C:\... -> /c/...
        return "/" + text[0].lower() + text[2:].replace("\\", "/")
    return text


def build_parser() -> argparse.ArgumentParser:
    """The command line, shared by ``main`` and its tests."""
    parser = argparse.ArgumentParser(
        description="Run the suite on Linux, in a container, against the working tree.",
    )
    parser.add_argument("--build", action="store_true", help="rebuild the image first")
    parser.add_argument("--shell", action="store_true", help="interactive shell instead of pytest")
    parser.add_argument(
        "--no-bwrap", action="store_true",
        help="keep Docker's default seccomp, so bubblewrap cannot unshare "
             "(use to prove the jail tests SKIP rather than silently pass)",
    )
    parser.add_argument(
        "--as-root", action="store_true",
        help="run as root, as before: the universe tool jail refuses to start, "
             "so its tests fail for the oracle's reasons, not yours",
    )
    parser.add_argument(
        "--out", metavar="DIR",
        help="bind DIR at /out, writable by the suite, e.g. -- --junitxml=/out/j.xml",
    )
    parser.add_argument(
        "--apparmor", metavar="PROFILE", default="unconfined",
        help="AppArmor profile for the container (default unconfined; CI runners "
             "that restrict user namespaces load their own)",
    )
    parser.add_argument(
        "--env", metavar="KEY=VALUE", action="append", default=[],
        help="set an environment variable for the suite (repeatable)",
    )
    parser.add_argument(
        "--required-runner", action="store_true",
        help=f"run {REQUIRED_RUNNER} with the arguments after -- instead of pytest "
             "(needs --out and a --junit under /out)",
    )
    parser.add_argument(
        "pytest_args", nargs="*",
        help="passed to pytest (put them after --); default: the whole suite, quiet",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.required_runner:
        _required_runner_command(args)  # refuse before Docker or filesystem effects

    if shutil.which("docker") is None:
        raise SystemExit("[oracle] docker is not on PATH")
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.OSType}}"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0 or "linux" not in probe.stdout:
        raise SystemExit(
            "[oracle] no Linux Docker engine. Start Docker Desktop and retry; "
            f"docker info said: {(probe.stderr or probe.stdout).strip()[:200]}"
        )

    root = _repo_root()
    tag = _image_tag(root)
    if args.build or not _image_exists(tag):
        _build(root, tag)

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            out.chmod(0o777)  # the suite runs as uid 1001, not the caller
    return subprocess.run(docker_command(args, root, tag)).returncode


def _required_runner_command(args: argparse.Namespace) -> str:
    """The in-container command for ``--required-runner``, or refuse."""
    for flag in ("shell", "no_bwrap", "as_root"):
        if getattr(args, flag):
            raise SystemExit(
                f"[oracle] --required-runner refuses --{flag.replace('_', '-')}: the "
                "gate runs unprivileged, behind the jail probe, or not at all"
            )
    runner_args = list(args.pytest_args)
    junit = [a.split("=", 1)[1] for a in runner_args if a.startswith("--junit=")]
    junit += [
        runner_args[i + 1] for i, a in enumerate(runner_args[:-1]) if a == "--junit"
    ]
    output = PurePosixPath(posixpath.normpath(junit[0])) if len(junit) == 1 else None
    if (not args.out or output is None or not output.is_relative_to("/out")
            or output == PurePosixPath("/out") or junit[0].endswith("/")):
        raise SystemExit(
            "[oracle] --required-runner wants --out DIR and exactly one "
            "--junit /out/<name>.xml, so the junit and its manifest reach DIR"
        )
    if not any(a.startswith("--pytest-arg=--basetemp") for a in runner_args):
        runner_args.append(f"--pytest-arg=--basetemp={DEFAULT_BASETEMP}")
    return shlex.join(["python", REQUIRED_RUNNER, "--oracle-venues", *runner_args])


def docker_command(args: argparse.Namespace, root: Path, tag: str) -> list[str]:
    """The ``docker run`` argv for parsed ``args``."""
    if getattr(args, "required_runner", False):
        command = _required_runner_command(args)
    elif args.shell:
        command = "bash"
    else:
        pytest_args = list(args.pytest_args or ["-q", "tests"])
        if not any(a == "--basetemp" or a.startswith("--basetemp=") for a in pytest_args):
            pytest_args.append(f"--basetemp={DEFAULT_BASETEMP}")
        # shlex.join, not hand quoting: an argument with an apostrophe in it
        # (a path, a -k expression) must reach pytest unchanged.
        command = shlex.join(["python", "-m", "pytest", "-p", "no:cacheprovider", *pytest_args])
    excludes = " ".join(f"--exclude=./{name}" for name in COPY_EXCLUDES)
    # Named, so a lane can stop its own run by name; Docker is shared across lanes.
    # Reap orphaned jail descendants during a full required shard. Without an
    # init, runuser becomes PID 1 and hundreds of exited children accumulate.
    run = ["docker", "run", "--label", "tinyassets.disposable=true",
           "--label", f"tinyassets.created-at={int(time.time())}",
           "--init", "--rm", "--name", f"ta-oracle-{os.getpid()}",
           "-v", f"{_docker_path(root)}:/src:ro"]
    for pair in args.env:
        if "=" not in pair:
            raise SystemExit(f"[oracle] --env wants KEY=VALUE, got {pair!r}")
        run += ["-e", pair]
    if args.out:
        run += ["-v", f"{_docker_path(Path(args.out))}:/out"]
    if getattr(args, "required_runner", False):
        # Prepare/probe the normal identity first. Only the coordinator stays
        # root; it drops every ordinary pytest run to UID1001 and merges the
        # explicit real-inode proofs into the same required shard receipt.
        script = _USER_SCRIPT.format(excludes=excludes, uid=ORACLE_UID).replace(
            "exec runuser", "runuser")
        script += '\ngit config --global --add safe.directory /work\nexec ' + command + '\n'
        script = script.replace('\ngit config --global', '\ncd /work\ngit config --global')
        user_script = JAIL_PROBE + _REPO_AND_RUN_SCRIPT.format(command="true")
        run += ["-e", "ORACLE_USER_SCRIPT=" + user_script]
    elif args.as_root:
        script = _RUN_SCRIPT.format(excludes=excludes, command=command)
    else:
        script = _USER_SCRIPT.format(excludes=excludes, uid=ORACLE_UID)
        user_script = _REPO_AND_RUN_SCRIPT.format(command=command)
        if not args.no_bwrap:
            user_script = JAIL_PROBE + user_script
        run += ["-e", "ORACLE_USER_SCRIPT=" + user_script]
    if not args.no_bwrap:
        # Docker's default seccomp profile blocks the clone flags bubblewrap
        # needs; without this every jail test skips and the oracle covers less
        # than it says it does. Unprivileged, bubblewrap also needs AppArmor and
        # the masked /proc and /sys paths out of its way to mount its own.
        run += ["--security-opt", "seccomp=unconfined"]
        if not args.as_root:
            run += [
                "--security-opt", f"apparmor={args.apparmor}",
                "--security-opt", "systempaths=unconfined",
            ]
    if args.shell:
        run.append("-it")
    return [*run, tag, "bash", "-lc", script]


if __name__ == "__main__":
    sys.exit(main())
