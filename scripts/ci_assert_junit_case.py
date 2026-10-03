"""Require named pytest cases to be PRESENT and CLEAN in a JUnit report.

Used by `.github/workflows/linux-jail-proof.yml` and
`.github/workflows/real-browser-proof.yml`. The cases it guards are
`skipif`-gated on the presence of `bwrap`, so a green pytest exit code proves
nothing about them: pytest exits 0 when a test skips. This script is the part of
the job that refuses to read a skip as a pass.

The cases are named ONE way: ``--marker real_jail`` asks pytest which tests
``-m real_jail`` selects (collection only, no jail needed). There is no
second list to keep in step --
the workflow and its shape test used to pin the same 23 node ids by hand, and
a case added to one and not the other was dropped three times.
``--list-files`` prints the files that hold them, for the pytest step.
``--nodeid`` (repeatable) still names cases explicitly. Every case is checked
on its own and the worst verdict is the exit code, so one clean case never
covers for another.

Exit codes:
    0  every case is present at least once and every occurrence has no
       <skipped>, <failure> or <error> child.
    1  some case is absent, or any occurrence skipped / failed / errored.
    2  the JUnit file is missing or not parseable (the run never got that far).

Matching is by pytest's xunit1 attributes: ``name`` is the test function name
(with any ``[param]`` suffix) and ``classname`` is the dotted module, plus
``.Class`` for methods. The nodeid is split on ``::``; the last part is the
name, the first is the file, anything between is the class path.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _expected_classname(nodeid: str) -> tuple[str, str]:
    parts = nodeid.split("::")
    if len(parts) < 2 or not parts[0].endswith(".py"):
        raise SystemExit(f"nodeid must look like path/to/test.py::name, got {nodeid!r}")
    module = parts[0][: -len(".py")].replace("\\", "/").replace("/", ".")
    classes = parts[1:-1]
    return ".".join([module, *classes]), parts[-1]


def marked_cases(root: Path, marker: str, tests_dir: str = "tests") -> list[str]:
    """Node ids of every test pytest selects with ``-m <marker>``.

    pytest's own collection decides, not a reader of decorator spellings, so a
    marker applied through an alias, a class ``pytestmark`` or a nested class
    counts exactly as pytest will run it, and parametrized cases come back
    with their full ids. Only files whose text names the marker are collected:
    a file that never mentions it cannot carry it.

    A collection error raises: a file that cannot be collected might hold a
    proof, and guessing would make the job green without it.
    """
    candidates = sorted(
        path.relative_to(root).as_posix()
        for path in (root / tests_dir).rglob("test_*.py")
        if marker in path.read_text(encoding="utf-8", errors="replace")
    )
    if not candidates:
        return []
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "-m", marker, *candidates],
        cwd=root, capture_output=True, text=True,
    )
    # 5 = nothing selected, which the caller reports as "no test carries it".
    if proc.returncode not in (0, 5):
        raise SystemExit(
            f"collecting -m {marker} failed (pytest exit {proc.returncode}); refusing "
            f"to guess the proof set:\n{proc.stdout[-3000:]}{proc.stderr[-2000:]}"
        )
    return [line.strip() for line in proc.stdout.splitlines() if "::" in line]


def _state(testcase: ET.Element) -> str:
    for tag in ("error", "failure", "skipped"):
        node = testcase.find(tag)
        if node is not None:
            message = (node.get("message") or node.text or "").strip()
            return f"{tag}: {message}" if message else tag
    return "passed"


def check(junit: Path, nodeid: str) -> tuple[int, str]:
    if not junit.is_file():
        return 2, f"no JUnit report at {junit}"
    try:
        root = ET.parse(junit).getroot()
    except ET.ParseError as exc:
        return 2, f"JUnit report at {junit} is not parseable: {exc}"
    classname, name = _expected_classname(nodeid)
    states = [
        _state(tc)
        for tc in root.iter("testcase")
        if tc.get("name") == name and tc.get("classname") == classname
    ]
    if not states:
        return 1, f"{nodeid} is ABSENT from {junit} (not collected or never ran)"
    bad = [s for s in states if s != "passed"]
    if bad:
        return 1, f"{nodeid} did not pass: {'; '.join(bad)}"
    return 0, f"{nodeid} executed and passed ({len(states)} occurrence(s))"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--junit", type=Path)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--nodeid", action="append",
                       help="a case that must be present and clean; repeatable")
    which.add_argument("--marker",
                       help="every test carrying pytest.mark.<MARKER> must be present and clean")
    parser.add_argument("--list-files", action="store_true",
                        help="with --marker: print the test files holding the cases and exit")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--label", default="linux-jail-proof",
                        help="the job name its verdict lines carry")
    parser.add_argument("--summary", type=Path, default=None,
                        help="append a one-line markdown verdict per case here")
    ns = parser.parse_args(argv)
    nodeids = ns.nodeid or marked_cases(ns.root, ns.marker)
    if not nodeids:
        # A marker nobody carries would make every run vacuously green.
        print(f"{ns.label} FAIL: no test carries pytest.mark.{ns.marker}",
              file=sys.stderr)
        return 2
    if ns.list_files:
        if not ns.marker:
            parser.error("--list-files needs --marker")
        for path in dict.fromkeys(n.split("::")[0] for n in nodeids):
            print(path)
        return 0
    if ns.junit is None:
        parser.error("--junit is required unless --list-files")
    worst = 0
    for nodeid in nodeids:
        code, message = check(ns.junit, nodeid)
        worst = max(worst, code)
        verdict = "PASS" if code == 0 else "FAIL"
        print(f"{ns.label} {verdict}: {message}")
        if ns.summary is not None:
            with ns.summary.open("a", encoding="utf-8") as handle:
                handle.write(f"- **{verdict}** `{nodeid}` — {message}\n")
    return worst


if __name__ == "__main__":
    sys.exit(main())
