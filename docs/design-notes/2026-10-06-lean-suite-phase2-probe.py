"""One-off phase-2 research, run only in the disposable Linux oracle copy."""

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(nodes, junit):
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            *nodes,
            "--basetemp",
            "/tmp/probe-case",
            "-p",
            "no:cacheprovider",
            "-o",
            "junit_family=xunit1",
            "--junitxml",
            str(junit),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    junit.with_suffix(".log").write_text(proc.stdout + proc.stderr)
    cases = list(ET.parse(junit).getroot().iter("testcase")) if junit.exists() else []
    return proc.returncode, cases


def hits(cases, node):
    name = node.split("::")[-1]
    return [c for c in cases if c.get("name", "").split("[")[0] == name]


def test_sample():
    assert ROOT == Path("/work"), "Use the disposable Linux oracle, never a live checkout"
    sample = json.loads(
        (ROOT / "docs/design-notes/2026-10-06-lean-suite-phase2-mutations.json").read_text()
    )["results"]
    out = Path("/out")
    out.mkdir(exist_ok=True)
    nodes = sorted({row[k] for row in sample for k in ("candidate", "survivor")})
    rc, cases = run(nodes, out / "baseline.xml")
    summary = {"baseline_exit": rc, "baseline_cases": len(cases), "results": []}
    (out / "mutation-results.json").write_text(json.dumps(summary, indent=2))
    assert (
        rc == 0
        and cases
        and all(
            not any(c.find(t) is not None for t in ("failure", "error", "skipped")) for c in cases
        )
    ), (out / "baseline.log").read_text()[-12000:]
    path = ROOT / "tinyassets/onboarding/app.html"
    original = path.read_bytes()
    text = original.decode("utf-8")
    try:
        for i, row in enumerate(sample, 1):
            if "function" in row:
                pattern = r"(function " + re.escape(row["function"]) + r"\([^)]*\)\s*\{)"
                mutated, n = re.subn(
                    pattern, lambda m: m[0] + "\n    " + row["statement"], text, count=1
                )
                assert n == 1, row
            else:
                assert text.count(row["old"]) == 1, row
                mutated = text.replace(row["old"], row["new"])
            path.write_text(mutated, encoding="utf-8")
            rc, cases = run([row["candidate"], row["survivor"]], out / f"mutant-{i:02}.xml")
            result = dict(
                row,
                index=i,
                exit=rc,
                failed=[
                    c.get("classname") + "::" + c.get("name")
                    for c in cases
                    if c.find("failure") is not None
                ],
            )
            result["proved"] = (
                rc == 1
                and all(
                    any(c.find("failure") is not None for c in hits(cases, row[key]))
                    for key in ("candidate", "survivor")
                )
                and all(c.find("error") is None and c.find("skipped") is None for c in cases)
            )
            summary["results"].append(result)
            path.write_bytes(original)
            (out / "mutation-results.json").write_text(json.dumps(summary, indent=2))
            print(f"probe {i}/20: {result['proved']}", flush=True)
    finally:
        path.write_bytes(original)
    assert all(r["proved"] for r in summary["results"]), [
        (r["index"], r["proved"]) for r in summary["results"]
    ]
