#!/usr/bin/env python3
"""Gate: every repo-authored exec into the daemon container goes through ta-op.

A bare ``docker exec tinyassets-daemon <anything>`` reaches the runtime target
with whatever identity and capability set Docker happened to configure, and
asserts nothing about it. ``/usr/local/libexec/ta-op <mode>`` reaches the same
target only after a verified uid/gid 1001 + all-five-cap-sets-empty posture
(``deploy/native/ta_op.c``), and only for a mode in the closed table
``deploy/native/ta_op_modes.tsv`` — the same file this gate reads, so the gate
and the runtime cannot drift apart.

Red on: a bare privileged-surface exec, an alias of the daemon container, a
backslash-continued or YAML-block-scalar variant, a ``sudo`` prefix, an
interactive ``-t``/``-it`` exec, and an exec that IS wrapped but names a mode
the runtime does not implement.

There is NO grandfather allowlist and NO exemption. An earlier revision of this
gate reported a TTY-allocating exec as a note rather than a violation, on the
reasoning that no automation can allocate a TTY. That reasoning was wrong as a
*gate* rule: a TTY is a property of the invocation, not proof that nothing
automated can reach the command, and the exemption admitted arbitrary
repo-authored argv. The one real shape it covered — an operator subscription
login inside the container — no longer exists: the platform has no LLM (Hard
Rule 15), so there is no login mode and a TTY exec is refused like any other.

STATED LIMIT, not papered over: this governs repo-authored invocations only.
``scripts/droplet.py ssh -- <cmd>`` forwards an arbitrary remote command, and
any host admin holding the deploy key already has arbitrary root SSH on the
droplet. Ad-hoc admin SSH is outside the repo gate by construction; that
residual belongs to the root-start decision, not to this check.

Usage:  python scripts/check_drop_first_exec.py [PATH ...]
Exit 0 clean, 1 violations found, 2 bad invocation.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODES_TSV = REPO / "deploy" / "native" / "ta_op_modes.tsv"
WRAPPER = "/usr/local/libexec/ta-op"
# The wrapper under every name a callsite spells it by. A shell/Python variable
# bound to the literal path is the same invocation; resolving these is not an
# exemption, it is name resolution.
WRAPPER_ALIASES = {WRAPPER, "$TA_OP", "${TA_OP}", "{TA_OP}"}

# Tokens that end the command word list: a redirection, a pipe, a list
# operator, or a trailing prose comment in a runbook.
STOP_TOKENS = {"|", "||", "&&", ";", "&", "#", ">", ">>", "<", "}", ","}

SCAN_DIRS = ("scripts", "deploy", ".github/workflows", "docs/ops", "docs/reference")
SCAN_SUFFIXES = {".py", ".sh", ".yml", ".yaml", ".md", ".template", ".ps1", ".bash"}

# The daemon container under every name the repo calls it by.
DAEMON_ALIASES = {
    "tinyassets-daemon",
    "$DAEMON_CONTAINER",
    "${DAEMON_CONTAINER}",
    "daemon",
}

# `docker exec`, `docker compose exec`, `docker-compose exec`, with or without
# a sudo prefix. The prefix is normalised away before matching.
EXEC_RE = re.compile(r"\bdocker(?:[ \t]+compose|-compose)?[ \t]+exec\b")

# docker exec flags that consume the following token.
FLAGS_WITH_VALUE = {"-e", "--env", "-u", "--user", "-w", "--workdir", "--env-file",
                    "--detach-keys", "--index"}

# This gate's own source and the mode table quote exec forms as documentation.
SELF_EXEMPT = {
    "scripts/check_drop_first_exec.py",
    "deploy/native/ta_op_modes.tsv",
}


def load_modes(tsv: Path = MODES_TSV) -> dict[str, dict[str, object]]:
    """Parse the closed mode table. Single source shared with the runtime."""
    modes: dict[str, dict[str, object]] = {}
    for raw in tsv.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) != 4:
            raise ValueError(f"malformed mode row: {raw!r}")
        name, argc, kind, argv = parts
        if kind not in ("builtin", "exec"):
            raise ValueError(f"unknown mode kind {kind!r} in {raw!r}")
        modes[name] = {
            "argc": int(argc),
            "kind": kind,
            "argv": [] if kind == "builtin" else argv.split("|"),
        }
    if not modes:
        raise ValueError(f"{tsv} declares no modes")
    return modes


def _normalise(text: str) -> list[tuple[int, str]]:
    """Join backslash continuations, drop sudo prefixes, keep line numbers.

    YAML block scalars (``run: |``) need no special handling — their body is
    ordinary physical lines once continuations are joined.
    """
    out: list[tuple[int, str]] = []
    buf = ""
    start = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not buf:
            start = lineno
        stripped = line.rstrip()
        # A continued line inside a comment block re-opens with its own `#`
        # (deploy/tinyassets-env.template). Strip it so the joined command is
        # the command, not a token sequence starting with `#`.
        if buf.lstrip().startswith("#"):
            stripped = re.sub(r"^\s*#\s?", "", stripped)
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        buf += stripped
        out.append((start, re.sub(r"\bsudo(?:[ \t]+-\S+)*[ \t]+", "", buf)))
        buf = ""
    if buf:
        out.append((start, re.sub(r"\bsudo(?:[ \t]+-\S+)*[ \t]+", "", buf)))
    return out


def _tokenize(rest: str) -> list[str]:
    # Give shell list separators their own tokens so a command that ends
    # `... "$1"; }` or a Python f-string that ends `... env-summary',` is cut
    # at the separator instead of counting it as an argument.
    rest = re.sub(r"([;,])", lambda m: " " + m.group(1) + " ", rest)
    try:
        return shlex.split(rest, posix=True)
    except ValueError:
        return rest.replace("'", " ").replace('"', " ").split()


def scan_text(
    text: str, modes: dict[str, dict[str, object]]
) -> list[tuple[int, str]]:
    """Return the violations for daemon execs found in ``text``.

    There is no second, softer channel. A finding is a violation or it is not
    reported at all — a "note" tier is how an exemption survives a green gate.
    """
    findings: list[tuple[int, str]] = []
    for lineno, line in _normalise(text):
        for match in EXEC_RE.finditer(line):
            tokens = _tokenize(line[match.end():])
            cut = len(tokens)
            for idx, tok in enumerate(tokens):
                if tok in STOP_TOKENS or tok.startswith(">") or tok.startswith("2>"):
                    cut = idx
                    break
            tokens = tokens[:cut]
            i = 0
            while i < len(tokens) and tokens[i].startswith("-"):
                flag = tokens[i]
                if flag in FLAGS_WITH_VALUE:
                    i += 2
                else:
                    i += 1
            if i >= len(tokens):
                continue  # no container named — not an invocation we can judge
            container = tokens[i].strip("\"'")
            if container not in DAEMON_ALIASES:
                continue
            argv = tokens[i + 1:]
            if not argv:
                findings.append((lineno, "exec into the daemon with no command"))
                continue
            if argv[0] not in WRAPPER_ALIASES:
                findings.append(
                    (lineno, f"bare privileged-surface exec: {argv[0]!r} "
                             f"must be {WRAPPER} <mode>"))
                continue
            if len(argv) < 2:
                findings.append((lineno, "ta-op invoked with no mode"))
                continue
            mode = argv[1]
            if mode not in modes:
                findings.append((lineno, f"unknown ta-op mode {mode!r}"))
                continue
            want = int(modes[mode]["argc"])  # type: ignore[arg-type]
            got = 1 + len(argv[1:])
            if got != want:
                findings.append(
                    (lineno, f"ta-op {mode} takes argc {want}, callsite passes {got}"))
    return findings


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", *SCAN_DIRS],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    files = []
    for rel in out.split("\0"):
        if not rel:
            continue
        if rel in SELF_EXEMPT:
            continue
        path = REPO / rel
        if path.suffix in SCAN_SUFFIXES and path.is_file():
            files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    try:
        modes = load_modes()
    except (OSError, ValueError) as exc:
        print(f"drop-first-exec: cannot read the mode table: {exc}", file=sys.stderr)
        return 2
    targets = [Path(a) for a in args] if args else tracked_files()
    violations = 0
    for path in targets:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"drop-first-exec: {path}: {exc}", file=sys.stderr)
            return 2
        found = scan_text(text, modes)
        rel = path.relative_to(REPO) if path.is_absolute() else path
        for lineno, reason in found:
            print(f"{rel}:{lineno}: {reason}")
            violations += 1
    if violations:
        print(
            f"\ndrop-first-exec: {violations} violation(s). Route daemon execs "
            f"through `{WRAPPER} <mode>` using a mode declared in "
            f"deploy/native/ta_op_modes.tsv.",
            file=sys.stderr,
        )
        return 1
    print(f"drop-first-exec: clean ({len(targets)} files, {len(modes)} modes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
