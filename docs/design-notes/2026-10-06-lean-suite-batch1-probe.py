"""Workflow deletion evidence; replay only in the disposable Linux oracle."""

import json
import runpy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
NOTE = ROOT / "docs/design-notes"


def test_workflow_mutations():
    assert ROOT == Path("/work"), "Use the disposable Linux oracle"
    # Reuse phase 2's subprocess/JUnit harness, including persisted raw logs.
    harness = runpy.run_path(str(NOTE / "2026-10-06-lean-suite-phase2-probe.py"))
    run, hits = harness["run"], harness["hits"]
    groups = json.loads((NOTE / "2026-10-06-lean-suite-batch1-removals.json").read_text())["groups"]
    out = Path("/out")
    rc, cases = run(sorted({g["survivor"] for g in groups}), out / "batch1-baseline.xml")
    summary = {"baseline_exit": rc, "baseline_cases": len(cases), "results": []}
    result_path = out / "batch1-mutations.json"
    result_path.write_text(json.dumps(summary, indent=2))
    assert rc == 0 and cases and all(
        all(c.find(tag) is None for tag in ("failure", "error", "skipped")) for c in cases
    ), (out / "batch1-baseline.log").read_text()[-16000:]
    for i, group in enumerate(groups, 1):
        path = ROOT / group["target"]
        original = path.read_bytes()
        source = original.decode("utf-8")
        if group["operation"] == "invalid_yaml":
            mutated = source + "\nbroken: [\n"
        elif group["operation"] == "replace":
            assert source.count(group["old"]) == group.get("occurrences", 1), group
            mutated = source.replace(group["old"], group["new"])
        else:
            # YAML source marks let us remove one entire executable step without
            # reserializing unrelated strings, comments, or YAML 1.1 booleans.
            def entries(node):
                if isinstance(node, yaml.MappingNode):
                    for key, value in node.value:
                        yield key, value
                        yield from entries(value)
                elif isinstance(node, yaml.SequenceNode):
                    for value in node.value:
                        yield from entries(value)

            tree = yaml.compose(source)
            sequences = [value for key, value in entries(tree) if key.value == "steps"]
            matches = [step for seq in sequences for step in seq.value
                       if any(k.value == "name" and v.value == group["step"]
                              for k, v in step.value)]
            assert len(matches) == 1, group
            step = matches[0]
            lines = source.splitlines(keepends=True)
            del lines[step.start_mark.line:step.end_mark.line]
            mutated = "".join(lines)
            yaml.safe_load(mutated)
        try:
            path.write_text(mutated, encoding="utf-8")
            rc, cases = run([group["survivor"]], out / f"batch1-mutant-{i:02}.xml")
            proved = rc == 1 and any(c.find("failure") is not None
                                    for c in hits(cases, group["survivor"])) and all(
                c.find("error") is None and c.find("skipped") is None for c in cases
            )
            summary["results"].append(dict(group, exit=rc, proved=proved,
                failed=[c.get("classname") + "::" + c.get("name") for c in cases
                        if c.find("failure") is not None]))
            result_path.write_text(json.dumps(summary, indent=2))
        finally:
            path.write_bytes(original)
    assert all(r["proved"] for r in summary["results"]), summary
