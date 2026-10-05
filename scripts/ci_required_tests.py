#!/usr/bin/env python3
"""The behavioural half of the `required-tests` branch-protection gate.

Runs the test suite, then decides pass/fail against a committed quarantine
list of already-broken tests (`.github/known-failing-tests.txt`).

Why a quarantine list instead of "the suite must be green"
----------------------------------------------------------
When this gate was first run against `main` it found 65 failures and 12 errors
that predate it. Requiring a fully green suite on day one would have blocked
every PR in the repo, so the gate would have been reverted within the hour and
`main` would still have no behavioural check at all.

So the gate enforces the property that actually matters for auto-merge:

    NO PR MAY INTRODUCE A TEST FAILURE THAT MAIN DID NOT ALREADY HAVE.

Every already-broken test is enumerated by node id, in the diff, with a reason.
The list may only shrink: an entry that stops failing is a hard error, so fixed
tests cannot rot in the file and quietly re-cover a regression later.

Honesty note (same spirit as pr-scope-guard.yml)
-----------------------------------------------
A contributor CAN add their own broken test to the quarantine file to get green.
This is a declaration control, not a security boundary. What it does buy: doing
so is an explicit, reviewable line in the diff on a `.github/` path — which also
trips the scope guard's `infra-change` declaration — instead of an invisible
regression riding in on a green check.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QUARANTINE = REPO_ROOT / ".github" / "known-failing-tests.txt"

# The gate must never pass vacuously. Without a floor, a PR that mass-skips,
# mass-deselects, or deletes most of the suite goes green on nothing — pytest
# exits 0, no "new failures" exist, and only literal zero-collection trips
# exit 5 (Codex gate review 2026-08-02, finding 3). The suite ran 12,747
# tests on the 2026-08-02 baseline (run 30767266528); the floor sits far
# below natural variance and far above any vacuous run. Ratchet it upward
# as the suite grows.
MIN_RAN_FLOOR = 10000

# A heavy-only job runs ~2,235 tests, so it cannot meet the whole-suite floor --
# and LOWERING the global floor to fit it would disable the vacuity check for
# the gate too. Named profiles keep the property that matters: a floor is a
# reviewed constant here, never a number a caller can pick, and the
# workflow-shape test pins which profile each job uses.
MIN_RAN_FLOORS = {
    "full": MIN_RAN_FLOOR,   # every test under tests/
    "heavy": 2000,           # .github/heavy-test-files.txt only (~2,235 today)
    # ONE shard of the required gate (~3,600 of ~21,900 at 6 shards). The union
    # floor is checked again by `--aggregate`, but the union floor alone cannot
    # see one shard collapsing: 5 of 6 shards still clear it comfortably.
    "shard": 1000,
    # The PR-time run of scripts/affected_tests.py's selection. Zero on
    # purpose: a selection can honestly be one test or none (a docs-only
    # change), and this run is advisory -- the merge-group shards above carry
    # the floors that gate. Only reachable with --affected.
    "affected": 0,
}


# ---- budgets ------------------------------------------------------------------
#
# The suite is budgeted by what it COSTS, never by how many tests it has: a
# count budget invites deleting cheap, valuable tests (Linear grew its suite 4x
# in 2026 and still cut PR wait, by cutting per-test cost). Each is a reviewed
# constant here, like MIN_RAN_FLOOR, so raising one is a receipt-gated diff of
# a gate file and lowering one is free. None of them reads a date: the merge
# queue must never fail because of the calendar.
#
# Measured on merge-group runs 2026-10-01 (36926964890, 36924723282,
# 36920148863, 36912072109): 126-127 tests skipped; the sum of per-test seconds
# across the six shards 1,431-1,509, with one noisy-runner outlier at 1,856.
# The seconds cap sits well above that noise so a slow runner never fails a
# merge; it catches a change that makes the suite materially slower.
# 2026-10-04: #4404 adds one real_jail metadata-isolation proof. Comparing
# passing main run 37187949417 with combined run 37188645443 gives exactly one
# new skip in the ordinary shards: test_native_metadata_snapshot_cannot_read_
# foreign_or_platform_state. It executes in linux-jail-proof, whose no-skip
# assertion covers every real_jail case. Existing skip conditions are unchanged.
# 2026-10-04: #4439 adds six real_jail ta-capability proofs. Set comparison
# of main run 37227391997 and merge-group 37231265315 shows exactly these six
# new skips, with no other change. All six execute and pass in the same head's
# linux-jail-proof run 37231177806 (100 cases, zero skips); its marker assertion
# requires every real_jail case. Ordinary shards still have no bubblewrap.
# 2026-10-04: #4316 runs the six required shards inside the Linux oracle
# container (uid 1001, Chromium in the image) instead of on the bare runner. The
# oracle runs execute 26,296 cases against 26,207 on the bare runner (50 more
# collected, 39 fewer skipped: 95 against 134). Measured summed seconds: bare-runner
# main run 37241639736 = 2128s; #4457 on the bare runner (41+/5- across four files)
# failed at 2413s (37243264963); oracle runs 37243158547 = 2397s and #4316's
# whole-surface 37243991116 = 2596s, both passing every test. 3000s is a
# provisional cap 15.6% above the highest measurement, still low enough to catch a
# material slowdown. Why the bare-runner total rose from 1431-1509s (10-01, from
# JUnit artifacts) is unexplained: docs/concerns/2026-10-04-required-suite-time-drift.md.
MAX_REQUIRED_SKIPPED = 134
MAX_TEST_SECONDS = 3000
#: Entries in the quarantine ledger, flaky or not. A quarantine that only grows
#: is how a red build gets normalised.
MAX_QUARANTINE = 63
#: How far MAX_QUARANTINE may sit above the ledger. Deleting entries means
#: lowering the cap in the same PR (tests/test_ci_required_tests.py), so the
#: cap only ratchets down unless a reviewed change raises it.
QUARANTINE_SLACK = 8
#: Leading `key=value` fields a ledger line may carry, BEFORE the node id; a
#: `flaky` entry must carry owner= and expires= (the test still RUNS, it just
#: does not block). Leading, never trailing: a parameter id may end in
#: ` owner=b]`, but no node id starts with `owner=`.
_LEDGER_FIELD = re.compile(r"^(owner|expires|issue)=(\S+)\s+")


def split_ledger_line(raw: str) -> tuple[bool, str, dict[str, str]] | None:
    """(is_flaky, node_id, fields) for one ledger line, or None for a blank or
    comment line. The ONE parser: the gate, the hygiene gate, the inventory and
    the quarantine oracle all read entries through it."""
    line = raw.split("#", 1)[0].strip()
    if not line:
        return None
    is_flaky = line.startswith("flaky ")
    if is_flaky:
        line = line[len("flaky ") :].lstrip()
    fields: dict[str, str] = {}
    while (hit := _LEDGER_FIELD.match(line)) is not None:
        fields[hit.group(1)] = hit.group(2)
        line = line[hit.end() :]
    return is_flaky, line.strip(), fields


def budget_failures(skipped: set[str], seconds: float) -> list[str]:
    """What the union of the required shards spent over budget."""
    out = []
    if len(skipped) > MAX_REQUIRED_SKIPPED:
        sample = ", ".join(sorted(skipped)[:5])
        out.append(
            f"{len(skipped)} tests were SKIPPED in the required run; the budget is "
            f"{MAX_REQUIRED_SKIPPED}. A skip runs nowhere in this gate. Make the new case "
            f"run here, remove a skip elsewhere, or raise MAX_REQUIRED_SKIPPED in a "
            f"reviewed change. e.g. {sample}"
        )
    if seconds > MAX_TEST_SECONDS:
        out.append(
            f"the required tests took {seconds:.0f}s summed over all shards; the budget is "
            f"{MAX_TEST_SECONDS}s. Find the slow additions (junit `time`) and cut their "
            f"fixed cost, or raise MAX_TEST_SECONDS in a reviewed change."
        )
    return out


def collect_cost(junit: Path) -> tuple[set[str], float]:
    """(skipped node ids, summed seconds) from a junit xml."""
    skipped: set[str] = set()
    seconds = 0.0
    for tc in ET.parse(junit).getroot().iter("testcase"):
        seconds += float(tc.get("time") or 0)
        if tc.find("skipped") is not None:
            skipped.add(node_id(tc))
    return skipped, seconds


# ---- sharding ---------------------------------------------------------------
#
# The required gate runs as N parallel jobs. Each test FILE belongs to exactly
# one shard, so the partition is complete and disjoint by construction: every
# collected file maps to some index in 1..N, and the workflow runs every index.
#
# Files are packed by measured duration (.github/test-durations.json, per-file
# seconds from a merge-group junit; scripts/refresh_test_durations.py rewrites
# it). Every test_*.py under tests/ is packed longest-first onto the
# least-loaded shard; a file the table does not know yet counts as the median,
# and anything outside that set falls back to a stable path hash. A stale
# table only costs balance, never coverage. The pure hash it replaced left
# shard 3 slowest in 27 of 57 merge-group runs (2026-10-01; 289s of tests
# against 197-261s for the others).
#
# Enforced through `pytest_ignore_collect` (this module is loaded with `-p`), so
# a shard never IMPORTS another shard's files: a collection error is reported
# once, by the shard that owns the file, not six times.


DURATIONS = REPO_ROOT / ".github" / "test-durations.json"


def _hash_shard(relpath: str, total: int) -> int:
    digest = hashlib.sha256(relpath.encode("utf-8")).hexdigest()
    return int(digest, 16) % total + 1


def load_durations(path: Path = DURATIONS) -> dict[str, float]:
    """Per-file seconds. A missing or unreadable table means "no data", loudly."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in raw.items()}
    except (OSError, ValueError, AttributeError) as exc:
        print(f"WARNING: no usable test durations at {path} ({exc}); "
              "every file counts as equal", flush=True)
        return {}


def pack(files: list[str], durations: dict[str, float], total: int) -> dict[str, int]:
    """Longest-first onto the least-loaded shard; deterministic for equal inputs.

    Every shard job computes this independently, so it may depend only on the
    checkout: ties break on path and on the lowest shard index.
    """
    known = sorted(durations[f] for f in files if f in durations)
    default = known[len(known) // 2] if known else 1.0
    weight = {f: durations.get(f, default) for f in files}
    loads = [0.0] * total
    owner: dict[str, int] = {}
    for f in sorted(files, key=lambda f: (-weight[f], f)):
        index = min(range(total), key=lambda i: (loads[i], i))
        loads[index] += weight[f]
        owner[f] = index + 1
    return owner


@functools.lru_cache(maxsize=None)
def _packed(total: int) -> dict[str, int]:
    """The packing over TRACKED test files.

    Tracked, not whatever is on disk: one generated test_*.py present in one
    shard job and not another would reshuffle hundreds of owners between them
    (Codex review 2026-10-01 measured 561 for one added file). Without git the
    disk scan is the fallback, said out loud.
    """
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", "tests"],
            cwd=REPO_ROOT, capture_output=True, check=True,
        ).stdout.decode("utf-8").split("\0")
        files = [
            f for f in listed if f.rsplit("/", 1)[-1].startswith("test_") and f.endswith(".py")
        ]
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"WARNING: git ls-files failed ({exc}); packing the files on disk", flush=True)
        files = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in (REPO_ROOT / "tests").rglob("test_*.py")
        ]
    return pack(sorted(files), load_durations(), total)


def shard_of(relpath: str, total: int) -> int:
    """1-based shard index owning a repo-relative test file path."""
    rel = relpath.replace("\\", "/")
    return _packed(total).get(rel) or _hash_shard(rel, total)


def parse_shard(raw: str) -> tuple[int, int]:
    """Parse `I/N` into (index, total), rejecting anything outside 1 <= I <= N."""
    try:
        index_s, total_s = raw.split("/")
        index, total = int(index_s), int(total_s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--shard must look like I/N, got {raw!r}") from None
    if not 1 <= index <= total:
        raise argparse.ArgumentTypeError(f"--shard {raw!r}: need 1 <= I <= N")
    return index, total


def pytest_addoption(parser) -> None:  # pragma: no cover - exercised via pytest -p
    parser.addoption("--ci-shard", default=None, help="I/N: run only files hashed to shard I")


def pytest_ignore_collect(collection_path, config):
    """Skip test files owned by another shard. Directories and conftests pass."""
    raw = config.getoption("--ci-shard", default=None)
    # is_file() FIRST: a directory can be named `x.py`, and pytest asks about
    # directories before descending. Hashing one would hand the directory to
    # one shard and its files to others, and no shard would run them.
    if not raw or not collection_path.is_file() or collection_path.suffix != ".py":
        return None
    if collection_path.name in ("conftest.py", "__init__.py"):
        return None
    try:
        rel = collection_path.resolve().relative_to(Path(config.rootpath).resolve()).as_posix()
    except ValueError:
        return None
    index, total = parse_shard(raw)
    return True if shard_of(rel, total) != index else None


def _min_ran_arg(raw: str) -> int:
    """Reject a `--min-ran` below the floor, at the point of enforcement.

    Validating this only in the workflow-shape test is not enough: argparse uses
    the LAST occurrence of a repeated flag, so `--min-ran 10700 --min-ran 1`
    reads as 1 while any check that scans for the first match still sees 10700.
    Found in cross-family review 2026-08-03 and rated BLOCKING, because it
    silently disables the vacuity floor and lets a mass-deselected suite merge.

    Refusing here closes it for every caller, including ones that never go
    through the workflow. Lowering the floor legitimately means editing
    MIN_RAN_FLOOR in the same reviewed change — which is the point.
    """
    value = int(raw)
    if value < MIN_RAN_FLOOR:
        raise argparse.ArgumentTypeError(
            f"--min-ran {value} is below MIN_RAN_FLOOR ({MIN_RAN_FLOOR}); a low "
            f"floor disables the vacuity check as surely as omitting it. If the "
            f"suite legitimately shrank, lower MIN_RAN_FLOOR in the same PR and "
            f"say why."
        )
    return value


def vacuity_failure(ran_count: int, floor: int = MIN_RAN_FLOOR) -> str | None:
    """Return a failure message if too few tests ran to trust a green result."""
    if ran_count < floor:
        return (
            f"only {ran_count} tests ran; the floor is {floor}. A run this "
            "small means mass skip/deselect/deletion or a collection collapse "
            "- the gate must not go green on a vacuous run. If the suite "
            "legitimately shrank, lower MIN_RAN_FLOOR in the same PR and say "
            "why."
        )
    return None


def parse_quarantine(path: Path) -> tuple[set[str], set[str], list[str]]:
    """Return (tolerated, flaky, problems).

    Line formats (blank lines and `#` comments ignored)::

        tests/test_x.py::test_y            # tolerated failure, ratcheted
        flaky owner=dev expires=2026-10-15 tests/test_x.py::test_z
                                           # runs, never blocks, until it expires

    A plain entry is ratcheted: it must keep failing, or the line is stale and
    must be deleted. A `flaky` entry is exempt from that ratchet because it
    genuinely alternates run to run — without the escape hatch a flaky test
    would break unrelated PRs whichever way it landed. `flaky` is deliberately
    a separate, greppable keyword so the count stays visible and small.
    """
    if not path.exists():
        return set(), set(), []
    tolerated: set[str] = set()
    flaky: set[str] = set()
    problems: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        parsed = split_ledger_line(raw)
        if parsed is None:
            continue
        is_flaky, line, fields = parsed
        if "::" not in line:
            problems.append(f"{path.name}:{lineno}: not a pytest node id: {line!r}")
            continue
        if is_flaky and not ("owner" in fields and "expires" in fields):
            problems.append(
                f"{path.name}:{lineno}: a flaky quarantine entry needs owner= and expires= "
                f"(it still runs; the owner and date say who ends it and when): {line!r}"
            )
        (flaky if is_flaky else tolerated).add(line)
    if len(tolerated) + len(flaky) > MAX_QUARANTINE:
        problems.append(
            f"{path.name}: {len(tolerated) + len(flaky)} entries; the cap is {MAX_QUARANTINE}. "
            "Fix or delete entries before quarantining more."
        )
    return tolerated, flaky, problems


def node_id(testcase: ET.Element) -> str:
    """Rebuild the canonical pytest node id from an xunit1 <testcase>.

    xunit1 records `file` (path) and `classname` (dotted module [+ class]).
    The module prefix of `classname` is redundant with `file`; whatever remains
    after stripping it is the enclosing class, if any.
    """
    file_attr = (testcase.get("file") or "").replace("\\", "/")
    name = testcase.get("name") or ""
    classname = testcase.get("classname") or ""

    if not file_attr:
        # No `file` recorded — fall back to the dotted form so the entry is at
        # least identifiable. Never silently drop a failure.
        return f"{classname}::{name}"

    # A collection error records an empty classname; `file::name` keeps that
    # entry quarantinable instead of emitting a `file::::name` double colon.
    if not classname:
        return f"{file_attr}::{name}"

    module_dotted = file_attr[:-3].replace("/", ".") if file_attr.endswith(".py") else ""
    if classname == module_dotted or not module_dotted:
        return f"{file_attr}::{name}"
    if classname.startswith(module_dotted + "."):
        cls = classname[len(module_dotted) + 1 :]
        return f"{file_attr}::{cls}::{name}"
    return f"{file_attr}::{classname}::{name}"


def collect_outcomes(junit: Path) -> tuple[set[str], set[str]]:
    """Return (failing, ran) node id sets from a junit xml."""
    root = ET.parse(junit).getroot()
    failing: set[str] = set()
    ran: set[str] = set()
    for tc in root.iter("testcase"):
        nid = node_id(tc)
        # A skipped test did not execute — it can neither prove nor disprove a
        # quarantine entry, so it must not count as "ran".
        if tc.find("skipped") is not None:
            continue
        ran.add(nid)
        if tc.find("failure") is not None or tc.find("error") is not None:
            failing.add(nid)
    return failing, ran


def summarise(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "\n".join(lines)
    print(text)
    # Expose the existing diagnostic to clients unable to follow log redirects.
    # Escape command data so identifiers cannot inject runner commands.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        message = text[:16000].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::notice title=Required test gate summary::" + message)
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def evaluate(
    failing: set[str], ran: set[str], min_ran: int, pytest_exits: list[int], heading: str
) -> int:
    """Compare outcomes to the quarantine ledger: the gate's pass/fail decision.

    Shared by a single run and by `--aggregate`, which passes the UNION of every
    shard's outcomes and every shard's pytest exit code. Per-shard verdicts are
    sound too: `stale` requires an entry to have RUN, so a shard never marks a
    test it did not own as stale.
    """
    tolerated, flaky, problems = parse_quarantine(QUARANTINE)
    known = tolerated | flaky

    new_failures = sorted(failing - known)
    # An entry that ran and did NOT fail is fixed (or renamed/deleted). Either
    # way the line is stale and must go, or the list slowly stops meaning
    # anything. Entries that did not run at all are left alone — a
    # platform-skipped test is not evidence of anything. `flaky` entries are
    # exempt by definition.
    stale = sorted(n for n in tolerated if n in ran and n not in failing)

    lines = [
        heading,
        "",
        f"- ran: **{len(ran)}**",
        f"- failing: **{len(failing)}**",
        f"- known-broken on main: **{len(tolerated)}** (+{len(flaky)} flaky)",
        f"- NEW failures: **{len(new_failures)}**",
        f"- stale quarantine entries: **{len(stale)}**",
    ]

    if problems:
        lines += ["", "**Malformed quarantine file:**", ""]
        lines += [f"- `{p}`" for p in problems]

    if new_failures:
        lines += [
            "",
            "**FAILED — this PR introduces test failures that `main` does not have.**",
            "",
        ]
        lines += [f"- `{n}`" for n in new_failures[:50]]
        if len(new_failures) > 50:
            lines.append(f"- …and {len(new_failures) - 50} more")

    if stale:
        lines += [
            "",
            "**FAILED — quarantined tests are passing now. Delete these lines from",
            f"`{QUARANTINE.relative_to(REPO_ROOT).as_posix()}`:**",
            "",
        ]
        lines += [f"- `{n}`" for n in stale[:50]]
        if len(stale) > 50:
            lines.append(f"- …and {len(stale) - 50} more")

    if not new_failures and not stale and not problems:
        # ASCII only: this also runs on a Windows console (cp1252), where a
        # stray emoji raises UnicodeEncodeError and takes the gate down with it.
        lines += ["", "No new failures."]

    summarise(lines)

    if new_failures or stale or problems:
        return 1

    vacuous = vacuity_failure(len(ran), min_ran)
    if vacuous:
        summarise(["", f"**FAILED — {vacuous}**"])
        return 1

    # Guard the inverse of a green check: pytest failed for a reason the
    # comparison did not explain (collection error, usage error, no tests run).
    # Exit codes: 0 ok, 1 tests failed (already explained above), 2 interrupted,
    # 4 usage error, 5 no tests collected.
    unexplained = [code for code in pytest_exits if code not in (0, 1)]
    if unexplained:
        summarise(
            [
                "",
                f"**FAILED — pytest exited {unexplained[0]} with no new test failures",
                "to explain it (usage error, interruption, or nothing collected).**",
            ]
        )
        return 1

    return 0


def aggregate(
    directory: Path,
    expected: int,
    junit_out: Path,
    min_ran: int,
    shard_job_result: str,
    expect_selection: str | None = None,
    must_cover: list[str] | None = None,
) -> int:
    """Merge shard results and decide the gate. Every shard must be accounted for.

    A lost shard must never read as green: a shard whose job died before
    uploading, was cancelled, or ran a different split leaves a hole the union
    comparison cannot see (its tests are simply absent from `ran`, and 5 of 6
    shards clear the union floor). So the shard set is checked BEFORE any
    comparison, and each shard's truncation signals fail the whole gate.

    For a SELECTIVE run (an affected-only merge group) two further things are
    checked, because the vacuity floor cannot be:

    * ``expect_selection`` -- every shard must report the digest the `select`
      job published, so shards cannot each run a different selection;
    * ``must_cover`` -- every selected file must appear in the union with at
      least one case. This REPLACES a numeric floor rather than lowering one: a
      selection can honestly be a single test, so no count is meaningful, but
      "the files we chose all reported" is exact. Any outcome counts, skip
      included, so a platform-guarded file is not a failure; a file that
      reported nothing at all is a collapse (a collection error, a bad slice,
      or a shard that silently ran something else) and is NAMED.
    """
    problems: list[str] = []
    if shard_job_result != "success":
        # Checked here, not in shell, so it is unit-tested: a shard that failed
        # AFTER writing clean-looking results must still fail the gate.
        problems.append(f"shard jobs concluded {shard_job_result!r}, not 'success'")
    manifests: dict[int, dict] = {}
    for path in sorted(directory.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            index, total, code = int(data["shard"]), int(data["total"]), int(data["pytest_exit"])
        except (ValueError, KeyError, TypeError) as exc:
            problems.append(f"{path.name}: unreadable shard manifest ({exc!r})")
            continue
        if total != expected or not 1 <= index <= expected:
            problems.append(f"{path.name}: shard ran as {index}/{total}, expected N={expected}")
            continue
        if index in manifests:
            problems.append(f"{path.name}: shard {index} reported twice")
            continue
        if expect_selection is not None:
            # A missing key reads as "" and fails here, deliberately: a shard
            # from before this contract existed must not satisfy it silently.
            reported = str(data.get("selection") or "")
            if reported != expect_selection:
                problems.append(
                    f"{path.name}: shard {index} ran selection {reported or '<none>'}, "
                    f"expected {expect_selection}"
                )
                continue
        manifests[index] = {"exit": code, "junit": path.with_suffix(".xml")}

    missing = sorted(set(range(1, expected + 1)) - set(manifests))
    if missing:
        problems.append(f"missing shard(s) {missing} of {expected}: no manifest uploaded")

    failing: set[str] = set()
    ran: set[str] = set()
    skipped: set[str] = set()
    seconds = 0.0
    owner: dict[str, int] = {}
    merged = ET.Element("testsuites")
    for index, info in sorted(manifests.items()):
        if info["exit"] == 3:
            problems.append(f"shard {index}: pytest INTERNALERROR (exit 3), run truncated")
        if not info["junit"].exists():
            problems.append(f"shard {index}: pytest exited {info['exit']} but wrote no junit xml")
            continue
        try:
            root = ET.parse(info["junit"]).getroot()
        except ET.ParseError as exc:
            problems.append(f"shard {index}: junit xml does not parse ({exc})")
            continue
        merged.extend([root] if root.tag == "testsuite" else list(root.iter("testsuite")))
        shard_failing, shard_ran = collect_outcomes(info["junit"])
        overlap = sorted(n for n in shard_ran if n in owner)
        if overlap:
            # Disjoint by construction; the same test in two shards means the
            # partition is broken and every count built on it is suspect.
            problems.append(
                f"shard {index}: {len(overlap)} test(s) also ran in shard "
                f"{owner[overlap[0]]}, e.g. {overlap[0]}"
            )
        for nid in shard_ran:
            owner.setdefault(nid, index)
        failing |= shard_failing
        ran |= shard_ran
        shard_skipped, shard_seconds = collect_cost(info["junit"])
        skipped |= shard_skipped
        seconds += shard_seconds

    # Written even when failing, so the `junit-required-tests` artifact (what
    # --emit-quarantine and duration measurements read) keeps its old shape.
    ET.ElementTree(merged).write(junit_out, encoding="utf-8", xml_declaration=True)

    if must_cover:
        reported = {
            (case.get("file") or "").replace("\\", "/") for case in merged.iter("testcase")
        }
        absent = sorted(rel for rel in must_cover if rel not in reported)
        if absent:
            shown = ", ".join(absent[:10])
            more = f" (+{len(absent) - 10} more)" if len(absent) > 10 else ""
            problems.append(
                f"{len(absent)} of {len(must_cover)} selected test file(s) reported no "
                f"case at all: {shown}{more}"
            )

    if problems:
        summarise(
            ["### Required tests - SHARD SET INCOMPLETE", "", "The gate fails closed:", ""]
            + [f"- {p}" for p in problems]
        )
        return 1

    per_shard = ", ".join(f"{i}: exit {m['exit']}" for i, m in sorted(manifests.items()))
    verdict = evaluate(
        failing,
        ran,
        min_ran,
        [m["exit"] for m in manifests.values()],
        f"### Required tests ({expected} shards; {per_shard})",
    )
    over = budget_failures(skipped, seconds)
    summarise(
        ["", f"- skipped: **{len(skipped)}** (budget {MAX_REQUIRED_SKIPPED}); "
         f"summed test seconds: **{seconds:.0f}** (budget {MAX_TEST_SECONDS})"]
        + [f"\n**FAILED - {o}**" for o in over]
    )
    return 1 if over else verdict


def _shard_label(args: argparse.Namespace) -> str:
    return f" - shard {args.shard[0]}/{args.shard[1]}" if args.shard else ""


def selection_entries(path: Path) -> list[str] | None:
    """The selection a `select` job published; ``None`` is the whole surface.

    One reader for the digest, the shard slice and the aggregate's coverage
    check, so the three cannot disagree about what the selection IS.
    """
    entries = Path(path).read_text(encoding="utf-8").split()
    if entries == ["ALL"]:
        return None
    if "ALL" in entries:
        raise SystemExit(f"{path}: ALL must be the only entry")
    return entries


def selection_digest(entries: list[str] | None) -> str:
    """Pin WHICH selection a run used, so shards cannot each run a different one.

    Order- and duplicate-insensitive, because the digest answers "the same set
    of files?" and nothing else. ``ALL`` has its own digest rather than an empty
    one: "the whole surface" and "a selection I could not read" must never
    compare equal.
    """
    body = "ALL" if entries is None else "\n".join(sorted(set(entries)))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def excluded_prefixes(exclude_from: str | None) -> list[str]:
    if not exclude_from:
        return []
    return [
        line.strip().rstrip("/")
        for line in Path(exclude_from).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def gating_selection(entries: list[str], exclude_from: str | None) -> list[str]:
    """The files a selective run must actually cover: selection minus the heavy list.

    The heavy list is red at baseline and belongs to `heavy-tests`, so a
    selected file that lives there is legitimately not run here and must not
    count against the coverage check.
    """
    excluded = excluded_prefixes(exclude_from)
    return [
        rel
        for rel in dict.fromkeys(entries)
        if not any(rel == e or rel.startswith(e + "/") for e in excluded)
        and (REPO_ROOT / rel).is_file()
    ]


def _files_collected(files: list[str], marker: str | None) -> set[str]:
    """Which of `files` yield at least one node id under `marker`. Collection only."""
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header",
            "-p", "no:cacheprovider", "--continue-on-collection-errors",
            *(["-m", marker] if marker else []),
            *files,
        ],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    seen: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        head = line.strip().split("::", 1)[0].replace("\\", "/")
        if head.endswith(".py"):
            seen.add(head)
    return seen


def collectible_under_gate(files: list[str]) -> tuple[list[str], list[str]]:
    """Split a selection into (files the gate keeps, files positively proven slow-only).

    KEEP IS THE DEFAULT. A file leaves the selection only on POSITIVE proof that
    it collects cases and that none of them is non-slow: it must appear with no
    marker filter AND be absent under ``-m "not slow"``. Absence of node ids is
    never enough.

    That asymmetry is the fix for a real escape found in round-3 cross-family
    review. The `select` job installs only ``.[dev]``, so a browser test whose
    ``pytest.importorskip`` sits at MODULE scope collects nothing there. The
    earlier version read "no node ids" as "all slow" and dropped the file; the
    shards (which DO have Playwright) then never saw it, and the browser no-skip
    assertion filters against the pruned selection and accepts an empty
    intersection -- so a broken non-slow browser test could land, with
    `slow-tests` skipping the module too for the same missing dependency.

    Keeping such a file costs nothing and is self-correcting: the shards have
    the full extras, so they collect and run it, and if nothing reports it the
    coverage check fails and names it. The same reasoning covers an unimportable
    file and any module-level skip -- a collection error is a failure the gate
    must report, not a reason to stop looking.

    Collecting here with the shards' extras installed would also close the
    specific Playwright case, but it is the weaker fix: it only moves the line,
    since any other module-level skip reintroduces the same hole.
    """
    if not files:
        return [], []
    collected = _files_collected(files, None)
    fast = _files_collected(files, "not slow")
    prunable = [rel for rel in files if rel in collected and rel not in fast]
    return [rel for rel in files if rel not in prunable], prunable


def _write_shard_manifest(
    manifest: Path, args: argparse.Namespace, slice_: list[str] | None, pytest_exit: int
) -> None:
    """The shard's receipt. `selection` is what makes the aggregate able to refuse.

    Without a digest here a shard could run a DIFFERENT (or older, or smaller)
    selection than its siblings and the union would still look complete. The
    aggregate compares every manifest's digest to the one the `select` job
    published, so disagreement fails the gate instead of narrowing it.
    """
    manifest.write_text(
        json.dumps(
            {
                "shard": args.shard[0],
                "total": args.shard[1],
                "pytest_exit": pytest_exit,
                "selection": args.selection or "",
                "slice": -1 if slice_ is None else len(slice_),
            }
        ),
        encoding="utf-8",
    )


def _read_selection(args: argparse.Namespace) -> list[str] | None:
    """This run's slice of an affected-tests selection; None means the whole surface.

    Sliced here rather than through the `-p` plugin so a slice that owns no
    selected file is known to be empty BEFORE pytest runs (pytest would exit 5
    and the run would read as broken). Files under --exclude-from are dropped
    the same way the required shards drop them: the heavy list is red at
    baseline and belongs to `heavy-tests`.
    """
    entries = selection_entries(Path(args.affected))
    if entries is None:
        return None
    for rel in entries:
        if not (REPO_ROOT / rel).is_file():
            print(f"WARNING: {args.affected} lists a missing path: {rel}", flush=True)
    gating = gating_selection(entries, args.exclude_from)
    if not args.shard:
        return gating
    return [rel for rel in gating if shard_of(rel, args.shard[1]) == args.shard[0]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--junit", default="junit.xml")
    ap.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="extra arg passed through to pytest (repeatable)",
    )
    ap.add_argument(
        "--exclude-from",
        metavar="FILE",
        help=(
            "File listing test paths to --ignore (blank lines and # comments "
            "skipped). Used by the fast REQUIRED gate to drop the handful of "
            "files that dominate its wall clock. The inverse, --include-from, "
            "is what `heavy-tests` uses to run exactly that excluded set."
        ),
    )
    ap.add_argument(
        "--include-from",
        metavar="FILE",
        help=(
            "File listing the ONLY test paths to run (blank lines and # "
            "comments skipped). The inverse of --exclude-from, for the "
            "`heavy-tests` job: it runs exactly the files the required gate "
            "excludes, so the two together cover the suite once instead of "
            "the old `full-tests` re-running the required 10,700 a second "
            "time. Directories are accepted, matching --exclude-from. A "
            "listed path that no longer exists is dropped with a WARNING "
            "rather than aborting the run."
        ),
    )
    ap.add_argument(
        "--profile",
        choices=sorted(MIN_RAN_FLOORS),
        default="full",
        help=(
            "Which vacuity floor applies. `full` is the whole suite; `heavy` "
            "is heavy-test-files.txt only. Named here rather than passed as a "
            "number so a job cannot quietly choose a floor it can meet."
        ),
    )
    ap.add_argument(
        "--min-ran",
        type=_min_ran_arg,
        default=None,
        help=(
            "Vacuity floor: fail if fewer than this many tests actually ran. "
            "Must be set per job — the fast gate and the full suite have "
            f"different honest totals (default {MIN_RAN_FLOOR}). Values below "
            f"MIN_RAN_FLOOR ({MIN_RAN_FLOOR}) are rejected outright."
        ),
    )
    ap.add_argument(
        "--emit-quarantine",
        metavar="JUNIT",
        help=(
            "Print the failing node ids from an EXISTING junit xml and exit. "
            "How .github/known-failing-tests.txt is generated — so the list is "
            "reproducible from a CI artifact, never hand-typed."
        ),
    )
    ap.add_argument(
        "--shard",
        type=parse_shard,
        metavar="I/N",
        help=(
            "Run only the test files packed into shard I of N (see shard_of), and "
            "write a manifest beside --junit recording the shard and pytest's "
            "exit code for --aggregate. Requires --profile shard."
        ),
    )
    ap.add_argument(
        "--aggregate",
        metavar="DIR",
        help=(
            "Run no tests. Merge the shard junit files and manifests under DIR, "
            "fail if any of the --expect-shards shards is missing or broken, "
            "write the union to --junit, and apply the quarantine comparison "
            "and vacuity floor to the union. This is the `required-tests` verdict."
        ),
    )
    ap.add_argument(
        "--expect-shards",
        type=int,
        metavar="N",
        help="With --aggregate: the shard count the workflow matrix runs.",
    )
    ap.add_argument(
        "--affected",
        metavar="FILE",
        help=(
            "The output of scripts/affected_tests.py: test paths to run, or the "
            "single word ALL for the whole required surface. Combined with "
            "--shard I/N it runs that shard's slice of the selection. An empty "
            "slice is a green no-op. Requires --profile affected."
        ),
    )
    ap.add_argument(
        "--shard-job-result",
        metavar="RESULT",
        help=(
            "With --aggregate: `needs.<shard job>.result`. Anything but "
            "`success` fails the gate, whatever the shard files say."
        ),
    )
    ap.add_argument(
        "--selection",
        metavar="DIGEST",
        help=(
            "The digest the `select` job published for this run's selection. On "
            "a shard it is recorded in the manifest; with --aggregate every "
            "shard must report exactly this digest, so shards cannot each run "
            "a different selection and still look like a complete union."
        ),
    )
    ap.add_argument(
        "--print-selection-digest",
        metavar="FILE",
        help=(
            "Print the digest of an affected_tests.py output and exit. ONE "
            "implementation, so the `select` job, the shards and the aggregate "
            "cannot disagree about what the selection is."
        ),
    )
    ap.add_argument(
        "--prune-to-collectible",
        metavar="FILE",
        help=(
            "Rewrite an affected_tests.py selection in place, dropping files "
            "that collect NOTHING under this gate's `-m \"not slow\"`. Run once "
            "by the `select` job, before the digest: a slow-only file would "
            "otherwise fail the coverage check and make its shard exit 5, "
            "neither of which is a regression (`slow-tests` runs those)."
        ),
    )
    ap.add_argument(
        "--plan-shard",
        action="store_true",
        help=(
            "Run no tests. Print how many selected files this shard owns (or "
            "ALL), and when it owns NONE write the empty junit and manifest the "
            "aggregate needs. Lets the workflow skip install for a shard with "
            "nothing to do -- the install is the cost, not the tests."
        ),
    )
    args = ap.parse_args()

    if args.print_selection_digest:
        print(selection_digest(selection_entries(Path(args.print_selection_digest))))
        return 0
    if args.prune_to_collectible:
        path = Path(args.prune_to_collectible)
        entries = selection_entries(path)
        if entries is None:
            print("ALL: nothing to prune")
            return 0
        runnable, dropped = collectible_under_gate(gating_selection(entries, args.exclude_from))
        path.write_text("".join(f"{rel}\n" for rel in runnable), encoding="utf-8")
        summarise(
            [
                "### Selection pruned to what this gate runs",
                "",
                f"- kept **{len(runnable)}** of {len(entries)} selected file(s)",
                f"- dropped **{len(dropped)}** that collect nothing under "
                f'`-m \"not slow\"` (they run in `slow-tests`): '
                + (", ".join(f"`{rel}`" for rel in dropped[:10]) or "none"),
            ]
        )
        if not runnable:
            # Nothing left to run and nothing to cover. The gate must not judge
            # this as a selective pass with an empty union, so fall back to ALL.
            path.write_text("ALL\n", encoding="utf-8")
            print("no collectible file left: falling back to ALL")
        return 0
    if args.plan_shard and not (args.affected and args.shard):
        raise SystemExit("--plan-shard needs --affected FILE and --shard I/N.")
    if args.selection and not (args.affected or args.aggregate):
        raise SystemExit("--selection belongs to an --affected run or --aggregate.")

    if args.shard and args.profile not in ("shard", "affected"):
        raise SystemExit("--shard requires --profile shard (the per-shard floor).")
    if (args.profile == "affected") != bool(args.affected):
        raise SystemExit("--affected and --profile affected go together.")
    if args.affected and args.include_from:
        raise SystemExit("--affected already names what to run; drop --include-from.")
    if args.profile == "shard" and not args.shard:
        raise SystemExit("--profile shard is only meaningful with --shard I/N.")
    if args.aggregate and (
        args.shard
        or not args.expect_shards
        or args.expect_shards < 1
        or args.shard_job_result is None
    ):
        raise SystemExit(
            "--aggregate needs --expect-shards N >= 1, --shard-job-result, and no --shard."
        )

    # BEFORE running anything. argparse can only check the LOWEST profile floor
    # (the profile is not known while parsing), so `--profile full --min-ran
    # 2000` slips past it. Checking here rather than beside the vacuity
    # assertion matters: the late check ran the whole suite for ten minutes
    # first and only then refused.
    profile_floor = MIN_RAN_FLOORS[args.profile]
    if args.min_ran is None:
        # The profile carries the floor. A heavy job cannot pass `--min-ran
        # 2000` explicitly, because `_min_ran_arg` still rejects anything below
        # MIN_RAN_FLOOR at parse time -- that contract is locked by
        # test_min_ran_below_floor_is_rejected_at_parse_time and re-broke when
        # this change first tried to relax it.
        args.min_ran = profile_floor
    if args.min_ran < profile_floor:
        raise SystemExit(
            f"--min-ran {args.min_ran} is below the '{args.profile}' profile "
            f"floor ({profile_floor}). Lower MIN_RAN_FLOORS['{args.profile}'] "
            f"in the same reviewed change if the suite legitimately shrank."
        )

    if args.emit_quarantine:
        failing, _ = collect_outcomes(Path(args.emit_quarantine))
        for nid in sorted(failing):
            print(nid)
        return 0

    if args.plan_shard:
        junit = Path(args.junit)
        slice_ = _read_selection(args)
        if slice_ is None:
            print("ALL")
            return 0
        print(len(slice_))
        if not slice_:
            junit.parent.mkdir(parents=True, exist_ok=True)
            _write_shard_manifest(junit.with_suffix(".json"), args, slice_, pytest_exit=0)
            ET.ElementTree(ET.Element("testsuites")).write(
                junit, encoding="utf-8", xml_declaration=True,
            )
        return 0

    if args.aggregate:
        # On a selective run the aggregate reads the SAME selection file the
        # shards did, and refuses if the digest it was told does not match what
        # it can actually read -- otherwise "every shard reported the expected
        # digest" would be a statement about a file nobody here verified.
        covered: list[str] | None = None
        if args.affected:
            entries = selection_entries(Path(args.affected))
            if entries is None:
                # `--affected` forces `--profile affected`, whose floor is 0.
                # That is right for a selection and WRONG for the whole
                # surface, which must keep its 10,700-test floor -- so the
                # whole-surface aggregate takes the same command it always did,
                # with no --affected at all. Refuse rather than quietly judge
                # the full suite with the vacuity check switched off.
                raise SystemExit(
                    f"{args.affected} says ALL: aggregate the whole surface with "
                    "--profile full --min-ran, not --affected (which zeroes the floor)."
                )
            covered = gating_selection(entries, args.exclude_from)
            if args.selection and args.selection != selection_digest(entries):
                raise SystemExit(
                    f"--selection {args.selection} does not match {args.affected}; "
                    "refusing to judge a selection this job cannot read."
                )
        return aggregate(
            Path(args.aggregate),
            args.expect_shards,
            Path(args.junit),
            args.min_ran,
            args.shard_job_result,
            expect_selection=args.selection,
            must_cover=covered,
        )

    junit = Path(args.junit)
    manifest = junit.with_suffix(".json")
    for stale_output in (junit, manifest):
        if stale_output.exists():
            stale_output.unlink()

    # SERIAL ON PURPOSE — do not "optimise" this back to pytest-xdist.
    #
    # HISTORICAL, pre-#2199. Under `-n auto --dist loadfile` this suite was not
    # deterministic: a test leaked global state and poisoned every later test on
    # the same worker. One measured run had 70 of its 149 failures on gw2 alone,
    # against 5/11/6 on the other three, including a `git diff --cached` that
    # returned another test's PR URL.
    #
    # Which tests landed on the poisoned worker depended on the file list, so
    # simply ADDING a test file moved ~34 tests in and out of the failure set
    # between two runs of otherwise-identical code. A gate whose verdict depends
    # on scheduling cannot support a committed baseline, and would fail PRs for
    # sins they did not commit.
    #
    # That specific leak is FIXED (#2199: a `patch()` entered inside a thread
    # worker, which is process-global rather than thread-local). Do not read the
    # paragraphs above as evidence of a leak that still exists — they are the
    # reason this call went serial, not a current diagnosis. Whether any further
    # isolation problem remains is undiagnosed; see the measurement below, whose
    # run-to-run disagreements have no established cause.
    #
    # Serial remains the choice here because a gate needs a deterministic
    # verdict.
    #
    # The "~4x" this comment used to promise on the other side of that fix was a
    # HYPOTHESIS. It was never measured, and one measurement does not support it.
    #
    # Measured 2026-08-03, Windows, 20 logical CPUs, `-n auto --dist loadfile`,
    # same tree, on this gate's subset as the exclusion manifest stood that day:
    #
    #     serial        10:04   214 failing
    #     xdist run A   11:40   219 failing
    #     xdist run B    9:53   218 failing
    #
    # What that measurement supports, stated no more strongly than it earns:
    #
    # No REPEATABLE speedup. Run A was ~16% slower than serial; run B was 11
    # seconds (~1.8%) faster, and that was not repeated. "No speedup at all"
    # would be wrong — B did beat serial — but one run each cannot establish
    # timing variance, so 11 seconds is reported as the measured delta and NOT
    # classified as noise. The two parallel runs bracket serial.
    #
    # The failure SETS differ. Cardinalities alone (214 / 219 / 218) prove that
    # much without any node IDs. They do NOT settle what the gate would report,
    # and the intuition that a disagreement "inside the baseline is harmless" is
    # wrong here: a plain ledger entry is RATCHETED, so a quarantined test that
    # runs and PASSES is classified stale and fails the gate — the same
    # mechanism that deleted 30 entries in #2236. Only a `flaky` entry is
    # verdict-neutral, and even that shifts the reported count.
    #
    # Distinguishing new failures from stale-ratcheted from flaky needs the
    # node-ID and ran sets, which were not captured. So the claim here is only
    # that this configuration is not established as safe for a committed-baseline
    # gate — not that it is proven unsafe.
    #
    # Deliberately NOT claimed: any causal account of the disagreements. An
    # earlier version of this comment attributed them to resource contention and
    # enumerated causes that summed to six while calling them seven, with no
    # node-ID sets to back it. Absent those sets the honest statement is that the
    # runs disagree and the cause is undiagnosed. #2199 fixed one specific
    # threaded `patch()` leak; it does not prove the remainder is not isolation.
    #
    # Scope: this is "no win demonstrated for THIS configuration", not "xdist
    # cannot help". Fewer workers, another `--dist` scheduler, or a later suite
    # shape are all untested, as is the full suite post-#2199. Whether the files
    # in .github/heavy-test-files.txt (deliberately not a count here — the last
    # one drifted from 47 to 50 and made this comment wrong) are the best
    # remaining target is likewise unmeasured.
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "not slow",
        "-q",
        "--no-header",
        "--durations=15",
        # Without this, ONE unimportable file aborts collection and the entire
        # suite reports "1 error, nothing run" — the gate would then be blind to
        # every real regression behind it. tests/test_tinyassets_tray.py does
        # exactly that on a headless runner (pystray -> Xlib DisplayNameError).
        # With it, the bad import is reported as an ordinary error against that
        # file and everything else still runs and still gates.
        "--continue-on-collection-errors",
        "-o",
        "junit_family=xunit1",
        f"--junitxml={junit}",
    ]
    if args.exclude_from:
        excluded = [
            line.strip()
            for line in Path(args.exclude_from).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not excluded:
            # An empty list means the file was truncated or mis-parsed. Running
            # everything is the safe direction, but say so — silently widening
            # the gate is how a "fast" job quietly becomes a 37-minute one.
            print(f"WARNING: {args.exclude_from} listed no paths", flush=True)
        cmd += [f"--ignore={path}" for path in excluded]
    if args.include_from:
        listed = [
            line.strip()
            for line in Path(args.include_from).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        present = [p for p in listed if (REPO_ROOT / p).exists()]
        for missing in [p for p in listed if p not in present]:
            # A stale entry must not take the whole job down, but it must not
            # be silent either -- that is how coverage disappears unnoticed.
            print(f"WARNING: {args.include_from} lists a missing path: {missing}", flush=True)
        if not present:
            # Running EVERYTHING would be the wrong safe direction here: this
            # job exists to run a subset, and a silent full run would duplicate
            # the required gate again. Fail instead.
            raise SystemExit(
                f"{args.include_from} resolved to no existing paths; refusing to "
                f"run (an empty include list would silently run nothing)."
            )
        cmd += present
    selection = _read_selection(args) if args.affected else None
    if selection is not None:
        if not selection:
            # This slice owns no selected file. Write the manifest AND an empty
            # junit before returning: without them the aggregate reports this
            # shard as missing and fails the gate closed, which is exactly what
            # it should do for a shard that vanished -- so a shard that
            # legitimately has nothing to do has to say so in the same shape.
            _write_shard_manifest(manifest, args, selection, pytest_exit=0)
            ET.ElementTree(ET.Element("testsuites")).write(
                junit, encoding="utf-8", xml_declaration=True,
            )
            summarise(
                [
                    f"### Affected tests{_shard_label(args)}",
                    "",
                    "No selected test file falls in this slice; nothing to run.",
                ]
            )
            return 0
        cmd += selection
    elif args.shard:
        # Loads THIS module as a pytest plugin for its pytest_ignore_collect.
        # `-p` imports by module name, hence scripts/ on PYTHONPATH below.
        cmd += ["-p", "ci_required_tests", f"--ci-shard={args.shard[0]}/{args.shard[1]}"]
    cmd += [*args.pytest_arg]
    print("+ " + " ".join(cmd), flush=True)
    env = None
    if args.shard:
        env = dict(os.environ)
        scripts_dir = str(Path(__file__).resolve().parent)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [scripts_dir, env.get("PYTHONPATH")]))
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env)
    print(f"pytest exit code: {proc.returncode}", flush=True)
    if args.shard:
        # Written unconditionally, BEFORE any verdict: the aggregate needs to
        # know this shard ran and how pytest exited even when the junit is
        # missing or this shard's own verdict is red.
        _write_shard_manifest(manifest, args, selection, proc.returncode)

    # Exit 3 = INTERNALERROR (e.g. a crashed xdist worker). When that happens the
    # run is TRUNCATED: tests are silently dropped from the report, so a
    # failure-set comparison against it is meaningless. Fail loudly instead of
    # comparing garbage. This is not theoretical — it is exactly how three
    # os.name-faking tests silently stopped a whole file from running.
    if proc.returncode == 3:
        summarise(
            [
                "### Required tests — INTERNAL ERROR",
                "",
                "pytest exited 3 (INTERNALERROR). The run was truncated, so an",
                "unknown number of tests never executed. Treating this as failure:",
                "a partial run cannot prove the absence of a regression.",
            ]
        )
        return 1

    if not junit.exists():
        summarise(
            [
                "### Required tests — NO REPORT",
                "",
                f"pytest exited {proc.returncode} but wrote no junit xml. The gate",
                "cannot verify anything, so it fails closed.",
            ]
        )
        return 1

    failing, ran = collect_outcomes(junit)
    exits = [proc.returncode]
    if args.affected:
        heading = f"### Affected tests{_shard_label(args)}"
        # Exit 5 (nothing collected) is honest here: a selected file can hold
        # only `slow` tests, which `-m "not slow"` deselects.
        exits = [0 if code == 5 else code for code in exits]
    elif args.shard:
        heading = f"### Required tests - shard {args.shard[0]}/{args.shard[1]}"
    else:
        heading = "### Required tests"
    return evaluate(failing, ran, args.min_ran, exits, heading)


if __name__ == "__main__":
    raise SystemExit(main())
