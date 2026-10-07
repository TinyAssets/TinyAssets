"""App-text deletion evidence; replay only in the disposable Linux oracle.

Each group names one app.html mutation, the removed tests/assertions, and an
executing survivor. On a tree that still holds a removed test, the mutation must
fail it (for a trimmed assertion: inside the trimmed lines) and the survivor.
On the final tree the removed tests are gone and the survivor alone must fail.
BATCH2_PHASE=pre (an unedited tree) checks both; the default, post, the survivor.
"""

import json
import os
import re
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTE = ROOT / "docs/design-notes"


def test_app_text_mutations():
    assert ROOT == Path("/work"), "Use the disposable Linux oracle"
    harness = runpy.run_path(str(NOTE / "2026-10-06-lean-suite-phase2-probe.py"))
    run, hits = harness["run"], harness["hits"]
    groups = json.loads((NOTE / "2026-10-06-lean-suite-batch2-removals.json").read_text())["groups"]
    out = Path("/out")
    survivors = sorted({g["survivor"] for g in groups if g["survivor"]})
    rc, cases = run(survivors, out / "batch2-baseline.xml")
    summary = {"baseline_exit": rc, "baseline_cases": len(cases), "results": []}
    result_path = out / "batch2-mutations.json"
    result_path.write_text(json.dumps(summary, indent=2))
    assert rc == 0 and cases and all(
        all(c.find(tag) is None for tag in ("failure", "error", "skipped")) for c in cases
    ), (out / "batch2-baseline.log").read_text()[-16000:]
    for i, group in enumerate(groups, 1):
        path = ROOT / group.get("target", "tinyassets/onboarding/app.html")
        original = path.read_bytes()
        source = original.decode("utf-8")
        newline = "\r\n" if "\r\n" in source else "\n"  # a Windows checkout copies CRLF
        old, new = (group[k].replace("\n", newline) for k in ("old", "new"))
        assert source.count(old) == 1, group["group"]
        present = group["removed"] if os.environ.get("BATCH2_PHASE") == "pre" else []
        nodes = sorted({r["node"] for r in present} | {group["survivor"]} - {None})
        if not nodes:  # a retired copy pin with no survivor, checked only in pre
            continue
        try:
            path.write_bytes(source.replace(old, new).encode("utf-8"))
            rc, cases = run(nodes, out / f"batch2-mutant-{i:03}.xml")
        finally:
            path.write_bytes(original)
        clean = all(c.find("error") is None and c.find("skipped") is None for c in cases)

        def failed_in(node, lines=None):
            fails = [c.find("failure") for c in hits(cases, node)]
            fails = [f for f in fails if f is not None]
            if not lines:
                return bool(fails)
            file = node.split("::")[0]
            text = "\n".join((f.text or "") + (f.get("message") or "") for f in fails)
            seen = {int(n) for n in re.findall(re.escape(file) + r":(\d+)", text)}
            return any(a <= n <= b for n in seen for a, b in lines)

        removed_ok = all(failed_in(r["node"], r.get("lines")) for r in present)
        survivor_ok = group["survivor"] is None or failed_in(group["survivor"])
        summary["results"].append(dict(
            group=group["group"], exit=rc, checked_removed=[r["node"] for r in present],
            removed_failed=removed_ok, survivor_failed=survivor_ok, clean=clean,
            proved=clean and removed_ok and survivor_ok and rc == 1,
            failed=[c.get("classname") + "::" + c.get("name") for c in cases
                    if c.find("failure") is not None]))
        result_path.write_text(json.dumps(summary, indent=2))
    assert all(r["proved"] for r in summary["results"]), [
        r["group"] for r in summary["results"] if not r["proved"]]
