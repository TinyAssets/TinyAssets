#!/usr/bin/env python3
"""Measure what an affected-only merge gate would have MISSED, and attribute it.

The evidence for `docs/design-notes/2026-10-02-affected-only-merge-gate.md`.
Committed because the note's first version cited a `miss_attr.py` that was never
in the repo, so its table could not be reproduced or re-run after the selector
changed.

For each recent FAILED `merge_group` Tests run:

* the queue branch `gh-readonly-queue/main/pr-<N>-<base>` gives the entry's base
  sha, and the run's head sha the group head, so `git diff base head` is exactly
  that entry's own diff;
* its failures come from the `junit-required-tests` aggregate artifact;
* the selection comes from ``scripts.affected_tests.select`` on that diff.

A failure OUTSIDE the selection is only an ESCAPE if the entry caused it. Two
discriminators, neither of which runs a test:

* **clustering** -- a test failing under several unrelated entries came in with
  the base;
* **the entry's own set-compare** -- these PRs routinely carry a Linux-oracle
  comparison in their comments ("0 new failures"), which settles it outright.

Anything left is printed for individual follow-up rather than counted either way.

WHY ``--entry-tree`` MATTERS. The selector's answer is derived from the TREE:
it returns ALL when a changed non-test ``.py`` no longer exists, so replaying an
old diff against TODAY's tree misclassifies deletions -- measured on PR #4297,
a deletion PR later closed, whose files still exist on main. The default is the
fast approximation, good for a distribution; ``--entry-tree`` checks out each
entry and is what any individual finding must be re-measured with.

    python scripts/miss_attr.py --limit 40
    python scripts/miss_attr.py --limit 40 --entry-tree   # slower, authoritative
"""

from __future__ import annotations

import argparse
import collections
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@functools.cache
def repo_slug() -> str:
    """``owner/name`` for this checkout, asked rather than hardcoded.

    A literal slug here named the founder's pre-org account, which the rename
    ratchet in ``tests/test_hard_rename_surfaces.py`` correctly refuses on every
    active surface -- and because this file is collected by the required suite,
    that refusal failed every merge group the PR was queued in. (This docstring
    deliberately does not quote the old slug: the ratchet would catch it here
    too, and claiming its `rename-allow` exemption to explain the bug would
    leave an exemption to maintain forever.) Deriving it cannot go stale at the
    next move either.

    ``GITHUB_REPOSITORY`` first because in Actions it is already exact and
    costs nothing; `gh` second for a local run; then a loud failure, because a
    wrong slug would silently measure another repository.
    """
    env = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if env:
        return env
    proc = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    slug = (proc.stdout or "").strip()
    if proc.returncode != 0 or "/" not in slug:
        raise SystemExit(
            "cannot determine the repository: set GITHUB_REPOSITORY or make "
            f"`gh repo view` work here (gh said: {(proc.stderr or '').strip()[:200]})"
        )
    return slug
_QUEUE = re.compile(r"^gh-readonly-queue/main/pr-(\d+)-([0-9a-f]{40})$")


@dataclass
class Row:
    """One failed merge-group run, with its entry's selection and failures."""

    run_id: int
    pr: int
    selected_all: bool
    why_all: str = ""
    selected: frozenset[str] = frozenset()
    failures: tuple[str, ...] = ()
    changed: int = 0


@dataclass
class Attribution:
    inside: int = 0
    outside: int = 0
    clustered: int = 0
    single_entry: dict[str, int] = field(default_factory=dict)
    per_test: dict[str, set[int]] = field(default_factory=dict)

    @property
    def unexplained(self) -> int:
        return sum(self.single_entry.values())


def classify(rows: list[Row]) -> Attribution:
    """Split failures into inside / carried-in / needs-a-look. Pure, so it is tested.

    A failure is INSIDE when the run selected ALL or the failing file is in the
    selection.

    For outside failures, clustering across entries is evidence that LATER
    entries inherited the failure -- and NOT evidence about the earliest one.
    If entry A introduces an unselected failure and B..N queue behind it, every
    group fails that test, and counting all of them as "carried in" would
    launder A's own regression into the thing that proves nothing escaped. That
    is the exact circularity cross-family review found in the first version of
    this function.

    So the FIRST entry to show a given test (ordered by the run id the API
    returns, oldest last) is always reported as needing base/head evidence.
    Only the later entries are counted as inherited, which is all clustering
    can honestly support.
    """
    result = Attribution()
    outside_rows: dict[str, list[Row]] = {}
    for row in rows:
        for nodeid in row.failures:
            path = nodeid.split("::", 1)[0]
            if row.selected_all or path in row.selected:
                result.inside += 1
            else:
                result.outside += 1
                result.per_test.setdefault(nodeid, set()).add(row.pr)
                outside_rows.setdefault(nodeid, []).append(row)
    for nodeid, hits in outside_rows.items():
        # Oldest first. `failed_runs` returns newest first, and a larger run id
        # is later, so sorting ascending puts the originating entry first.
        ordered = sorted(hits, key=lambda r: r.run_id)
        first_pr = ordered[0].pr
        result.single_entry[nodeid] = sum(1 for r in ordered if r.pr == first_pr)
        result.clustered += sum(1 for r in ordered if r.pr != first_pr)
    return result


def _gh(path: str, jq: str) -> object:
    proc = subprocess.run(
        ["gh", "api", path, "--jq", jq], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return json.loads(proc.stdout or "null") if proc.returncode == 0 else None


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd or REPO_ROOT, capture_output=True, text=True
    ).stdout


def failed_runs(limit: int) -> list[dict]:
    runs = _gh(
        f"repos/{repo_slug()}/actions/runs?per_page=100&event=merge_group&status=failure",
        '[.workflow_runs[] | select(.name=="Tests") '
        "| {id, br:.head_branch, head:.head_sha, at:.created_at}]",
    ) or []
    out = []
    for run in runs:
        match = _QUEUE.match(run["br"] or "")
        if match:
            run["pr"], run["base"] = int(match.group(1)), match.group(2)
            out.append(run)
    return out[:limit]


def junit_failures(run_id: int, cache: Path) -> list[str] | None:
    dest = cache / str(run_id)
    xml = dest / "junit.xml"
    if not xml.exists():
        dest.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["gh", "run", "download", str(run_id), "--repo", repo_slug(),
             "-n", "junit-required-tests", "-D", str(dest)],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        if not xml.exists():
            return None
    hits = []
    for case in ET.parse(xml).iter("testcase"):
        if any(child.tag in ("failure", "error") for child in case):
            path = (case.get("file") or case.get("classname") or "").replace("\\", "/")
            hits.append(f"{path}::{case.get('name')}")
    return hits


def _select_at(tree: Path, changed: list[str]):
    sys.path.insert(0, str(tree))
    for name in [m for m in sys.modules if m.startswith("scripts.affected_tests")]:
        del sys.modules[name]
    from scripts.affected_tests import Graph, loaded_by_conftests, select

    graph = Graph(tree)
    return select(changed, tree, graph, loaded_by_conftests(graph, tree))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument(
        "--entry-tree",
        action="store_true",
        help="Check out each entry and build the graph there. Slower and correct; "
             "the default replays old diffs against the current tree.",
    )
    ap.add_argument("--cache", default=None, help="Where to keep downloaded junits.")
    args = ap.parse_args()
    cache = Path(args.cache) if args.cache else Path(tempfile.gettempdir()) / "ta-miss-attr"

    runs = failed_runs(args.limit)
    print(f"failed merge_group Tests runs with a parseable queue branch: {len(runs)}")
    shared_tree = None if args.entry_tree else REPO_ROOT

    rows: list[Row] = []
    skipped = 0
    for run in runs:
        if subprocess.run(
            ["git", "cat-file", "-e", f"{run['head']}^{{commit}}"],
            cwd=REPO_ROOT, capture_output=True,
        ).returncode != 0:
            skipped += 1
            continue
        failures = junit_failures(run["id"], cache)
        if failures is None:
            skipped += 1
            continue
        changed = _git("diff", "--name-only", run["base"], run["head"]).split()
        if shared_tree is not None:
            selected, reasons = _select_at(shared_tree, changed)
        else:
            with tempfile.TemporaryDirectory(prefix="ta-entry-") as tmp:
                tree = Path(tmp) / "t"
                _git("worktree", "add", "--detach", str(tree), run["head"])
                try:
                    selected, reasons = _select_at(tree, changed)
                finally:
                    _git("worktree", "remove", "--force", str(tree))
        rows.append(
            Row(
                run_id=run["id"], pr=run["pr"], changed=len(changed),
                failures=tuple(failures), selected_all=selected is None,
                why_all=reasons[0].split(": ", 1)[1] if selected is None and reasons else "",
                selected=frozenset(selected or ()),
            )
        )
        print(
            f"  {run['id']} pr-{run['pr']:<5} changed={len(changed):<4} "
            f"sel={'ALL' if selected is None else len(selected):<5} "
            f"fails={len(failures)}",
            flush=True,
        )

    if not rows:
        print("no usable runs")
        return 0

    n_all = sum(1 for r in rows if r.selected_all)
    print(f"\nusable runs {len(rows)} (skipped {skipped}); selected ALL: {n_all}")
    for cause, count in collections.Counter(
        r.why_all for r in rows if r.selected_all
    ).most_common():
        print(f"   {count:3} {cause}")

    att = classify(rows)
    print(
        f"\nnew failures {att.inside + att.outside}: "
        f"inside the selection {att.inside}, outside {att.outside}"
    )
    for nodeid, prs in sorted(att.per_test.items(), key=lambda kv: -len(kv[1])):
        tag = "carried in (unrelated entries)" if len(prs) > 1 else "SINGLE ENTRY - check it"
        print(f"  {len(prs):2} entr{'ies' if len(prs) > 1 else 'y '}  {nodeid}\n        {tag}; "
              f"prs={sorted(prs)}")
    print(f"\nclustered across unrelated entries: {att.clustered} of {att.outside}")
    print(f"needing individual attribution: {att.unexplained}")
    print(
        "\nThis run cannot conclude 'no escape'. Clustering only shows that LATER "
        "entries inherited a failure; the FIRST entry to show each test is listed "
        "above as needing attribution and must be settled with base/head evidence "
        "-- its own Linux set-compare if it posted one, else the test run on the "
        "entry's base and its head."
    )
    if not args.entry_tree:
        print(
            "Re-measure any individual finding with --entry-tree first: the "
            "selector's deletion rule reads the TREE, so a diff replayed against "
            "today's tree misclassifies deletions."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
