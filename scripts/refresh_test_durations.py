#!/usr/bin/env python3
"""Rewrite .github/test-durations.json from recent merge-group test runs.

The required shards are packed by these per-file durations
(ci_required_tests.shard_of). A single run is noisy -- on 2026-10-01 one shard
ran the same files a median 2.7x slower than another run did -- so each file's
value is its MEDIAN over the last few successful runs. A stale table costs
shard balance and PR tier placement; the conservative queue still covers all
files. Refresh when shard times drift apart.

    python scripts/refresh_test_durations.py               # last 5 runs, via gh
    python scripts/refresh_test_durations.py --junit a.xml b.xml
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / ".github" / "test-durations.json"
ARTIFACT = "junit-required-tests"


def per_file_seconds(junit: Path) -> dict[str, float]:
    totals: Counter[str] = Counter()
    for case in ET.parse(junit).getroot().iter("testcase"):
        path = (case.get("file") or "").replace("\\", "/")
        if path.startswith("tests/") and path.endswith(".py"):
            totals[path] += float(case.get("time") or 0.0)
    return dict(totals)


def median_table(runs: list[dict[str, float]], root: Path = REPO_ROOT) -> dict[str, float]:
    """Median seconds per file over the runs that saw it; files gone from disk drop."""
    files = sorted({f for run in runs for f in run if (root / f).is_file()})
    return {f: round(statistics.median(r[f] for r in runs if f in r), 1) for f in files}


def download_recent(count: int, workdir: Path) -> list[Path]:
    listed = subprocess.run(
        ["gh", "run", "list", "--workflow", "tests.yml", "--event", "merge_group",
         "--status", "success", "-L", str(count), "--json", "databaseId"],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    ).stdout
    paths = []
    for run in json.loads(listed):
        target = workdir / str(run["databaseId"])
        subprocess.run(
            ["gh", "run", "download", str(run["databaseId"]), "-n", ARTIFACT, "-D", str(target)],
            cwd=REPO_ROOT, check=True,
        )
        paths.append(target / "junit.xml")
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--junit", nargs="+", type=Path, help="merged junit files to use instead")
    ap.add_argument("--runs", type=int, default=5, help="successful merge-group runs to read")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        junits = args.junit or download_recent(args.runs, Path(tmp))
        if not junits:
            raise SystemExit("no junit reports found; nothing to refresh from")
        table = median_table([per_file_seconds(j) for j in junits])
    if not table:
        raise SystemExit("the reports named no test file under tests/; refusing to write")
    # Bytes, so a Windows run writes the same LF file a Linux one does.
    args.out.write_bytes((json.dumps(table, indent=0, sort_keys=True) + "\n").encode("utf-8"))
    print(f"{args.out}: {len(table)} files from {len(junits)} run(s), "
          f"{sum(table.values()):.0f}s total", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
