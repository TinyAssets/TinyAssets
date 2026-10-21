#!/usr/bin/env python3
"""Resolve test claims against current OpenSpec text, without importing tests.

IDs are active change design rows (table rows or numbered headings), active
change requirement titles, or main-spec requirement titles. Archived changes
are historical evidence only. Coverage input is ``coverage json --show-contexts``
from pytest-cov's ``--cov-context=test`` run, at this exact source revision.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.M)
ROW = re.compile(r"^\|\s*([A-Z]\d+(?:[a-z]|\.\d+)*)\s*\|", re.M)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, encoding="utf-8")


def sources(root: Path, rev: str | None = None) -> dict[str, str]:
    """Read only test/spec text. Gate callers use blobs, never checkout/import PR code."""
    paths = (
        git(root, "ls-tree", "-r", "--name-only", rev).splitlines()
        if rev
        else [
            p.relative_to(root).as_posix()
            for home in ("tests", "openspec")
            for p in (root / home).rglob("*")
            if p.is_file()
        ]
    )
    selected = [
        p
        for p in paths
        if (p.startswith("openspec/") and p.endswith(".md"))
        or (
            p.startswith("tests/")
            and p.endswith(".py")
            and (Path(p).name.startswith("test_") or Path(p).name.endswith("_test.py"))
        )
    ]
    if rev:
        # One process, not a git process for each of thousands of source files.
        names = [f"{rev}:{p}" for p in selected]
        data = subprocess.check_output(
            ["git", "-C", str(root), "cat-file", "--batch"],
            input=("\n".join(names) + "\n").encode(),
        )
        result = {}
        offset = 0
        for path in selected:
            end = data.index(b"\n", offset)
            size = int(data[offset:end].split()[-1])
            offset = end + 1
            result[path] = data[offset : offset + size].decode("utf-8", errors="replace")
            offset += size + 1
        return result
    return {p: (root / p).read_text(encoding="utf-8", errors="replace") for p in selected}


def _sections(text: str):
    headings = list(HEADING.finditer(text))
    for i, match in enumerate(headings):
        end = next((h.start() for h in headings[i + 1 :] if len(h[1]) <= len(match[1])), len(text))
        yield match, text[match.end() : end]


def _retired(title: str, body: str) -> bool:
    # A deletion LIST within a live correction does not retire the correction.
    first = next((s for s in body.splitlines() if s.strip()), "")
    return bool(
        re.search(r"\bsuperseded\b", title, re.I)
        or re.search(r"\((?:deleted|removed)\)", title, re.I)
        or re.match(r"\s*(?:>\s*)?(?:\*\*)?(?:Superseded|Deleted|Removed)\b", first, re.I)
    )


def registry(files: dict[str, str]) -> dict[str, str]:
    result = {}
    for path, text in sorted(files.items()):
        parts = path.split("/")
        if len(parts) < 4 or parts[0] != "openspec":
            continue
        active = parts[1] == "changes" and parts[2] != "archive"
        main = parts[1] == "specs"
        if not (active or main):
            continue
        namespace = parts[2]
        sections = list(_sections(text))

        def retired_section(heading, body):
            return _retired(heading[2], body) or any(
                _retired(h[2], b)
                for h, b in sections
                if h.start() < heading.start() < h.end() + len(b)
            )

        if path.endswith("/spec.md"):
            for heading, body in sections:
                if heading[2].startswith("Requirement: "):
                    title = heading[2].removeprefix("Requirement: ").strip()
                    before = text[: heading.start()]
                    operations = re.findall(r"^## (\w+) Requirements", before, re.M)
                    retired = retired_section(heading, body) or bool(
                        operations and operations[-1] == "REMOVED"
                    )
                    result[f"{namespace}:{title}"] = "superseded" if retired else "current"
        if active and path.endswith("/design.md"):
            for heading, body in sections:
                hit = re.match(r"([A-Z]\d+(?:[a-z]|\.\d+)*)(?:[. :]|$)", heading[2])
                if hit:
                    result[f"{namespace}:{hit[1]}"] = (
                        "superseded" if retired_section(heading, body) else "current"
                    )
            for row in ROW.finditer(text):
                line = text[row.start() :].splitlines()[0]
                parents = [(h, b) for h, b in sections if h.end() <= row.start() < h.end() + len(b)]
                cells = line.split("|")
                retired = any(
                    re.match(r"\s*(?:\*\*)?Superseded\b", cell, re.I) for cell in cells[2:]
                ) or any(_retired(h[2], b) for h, b in parents)
                result[f"{namespace}:{row[1]}"] = "superseded" if retired else "current"
    return result


def _claims(node: ast.AST) -> set[str]:
    # A mark inside parametrize data or a test body is not a declaration on
    # every case of the test. Restrict claims to direct literal pytest marks.
    if isinstance(node, (ast.List, ast.Tuple)):
        return set().union(*(_claims(child) for child in node.elts))
    if not isinstance(node, ast.Call) or ast.unparse(node.func) != "pytest.mark.verifies":
        return set()
    return {
        arg.value
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        else "<nonliteral verifies id>"
        for arg in node.args
    }


@dataclass(frozen=True)
class Test:
    node: str
    ids: frozenset[str]
    start: int
    end: int


def tests(path: str, text: str) -> list[Test]:
    tree = ast.parse(text, filename=path)
    result = []

    def walk(body, parents=(), inherited=frozenset()):
        declared = set()
        for node in body:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
                    if isinstance(node, ast.AugAssign):
                        declared.update(_claims(node.value))
                    else:
                        declared = _claims(node.value)
        local = set(inherited) | declared
        for node in body:
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                extra = set().union(*(_claims(d) for d in node.decorator_list))
                walk(node.body, (*parents, node.name), frozenset(local | extra))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("test_"):
                    continue
                extra = set().union(*(_claims(d) for d in node.decorator_list))
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                result.append(
                    Test(
                        "::".join((path, *parents, node.name)),
                        frozenset(local | extra),
                        start,
                        node.end_lineno,
                    )
                )

    walk(tree.body)
    return result


def inventory(files: dict[str, str]) -> list[Test]:
    return [
        test
        for path, text in sorted(files.items())
        if path.startswith("tests/")
        for test in tests(path, text)
    ]


def audit(items: list[Test], rows: dict[str, str], previous=None) -> dict:
    previous = previous or {}
    untagged = []
    invalid = []
    counts = Counter()
    for test in items:
        if not test.ids:
            untagged.append(test.node)
        for claim in sorted(test.ids):
            if rows.get(claim) != "current":
                state = rows.get(claim, "deleted" if claim in previous else "unknown")
                invalid.append({"test": test.node, "id": claim, "state": state})
            else:
                counts[claim] += 1
    return {
        "tests": len(items),
        "untagged": untagged,
        "unresolvable": invalid,
        "tests_per_row": dict(sorted(counts.items())),
        "shape_gaps": sorted(
            r for r, state in rows.items() if state == "current" and not counts[r]
        ),
    }


def coverage_lines(document: dict) -> dict[str, set[tuple[str, int]]]:
    if not document.get("meta", {}).get("show_contexts"):
        raise ValueError("coverage JSON needs --show-contexts and --cov-context=test")
    lines = defaultdict(set)
    for path, data in document["files"].items():
        if path.replace("\\", "/").startswith("tests/"):
            continue
        for line, contexts in data.get("contexts", {}).items():
            for context in contexts:
                # Ignore collection/setup shared contexts; only attributed test calls.
                if context.endswith("|run"):
                    node = context[:-4].split("[", 1)[0]
                    lines[node].add((path, int(line)))
    return dict(lines)


def duplicates(items: list[Test], lines: dict, protected=frozenset()) -> dict[str, str]:
    """A deterministic kept witness covers ALL of a candidate's claimed rows.

    Union parameter cases before comparing. Never remove both equal checks or
    nominate a witness for removal later. Empty/missing coverage proves nothing.
    Structural guards stay witnesses even when a larger test covers their lines.
    """
    result = {}
    kept = []
    ordered = sorted(
        items,
        key=lambda t: (t.node.split("::")[0] not in protected, -len(lines.get(t.node, ())), t.node),
    )
    for test in ordered:
        covered = lines.get(test.node)
        if not covered or not test.ids:
            continue
        witness = next(
            (other for other in kept if test.ids <= other.ids and covered <= lines[other.node]),
            None,
        )
        if witness and test.node.split("::")[0] not in protected:
            result[test.node] = witness.node
        else:
            kept.append(test)
    return result


def prune(root: Path, items: list[Test], removals: set[str]) -> None:
    """Delete whole functions (all parameter cases), preserving unrelated source."""
    by_file = defaultdict(list)
    for test in items:
        if test.node in removals:
            by_file[test.node.split("::")[0]].append(test)
    for rel, selected in by_file.items():
        path = (root / rel).resolve()
        path.relative_to((root / "tests").resolve())
        text = path.read_text(encoding="utf-8")
        # Helper-only modules can be imported by other tests; retain their helpers.
        # Empty classes need pass. Work from the ORIGINAL tree's source spans.
        tree = ast.parse(text)
        replacements = {}
        for cls in sorted(
            (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)),
            key=lambda n: n.lineno,
            reverse=True,
        ):
            children = [t for t in selected if cls.lineno < t.start <= cls.end_lineno]
            if children and all(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any(t.end == n.end_lineno for t in children)
                for n in cls.body
            ):
                # Rebuild with a pass replacing the first removed function.
                anchor = min(t.start for t in children) - 1
                replacements[anchor] = [" " * (cls.col_offset + 4) + "pass\n"]
        lines = text.splitlines(keepends=True)
        for test in sorted(selected, key=lambda t: t.start, reverse=True):
            lines[test.start - 1 : test.end] = replacements.get(test.start - 1, [])
        updated = "".join(lines)
        ast.parse(updated)
        path.write_text(updated, encoding="utf-8")


def pytest_addoption(parser):
    parser.addoption(
        "--shape-collection", help="write actual collected node IDs and verifies claims"
    )


def pytest_collection_finish(session):
    """Optional collection evidence, including parameter cases and imported tests."""
    destination = session.config.getoption("--shape-collection")
    if destination:
        records = [
            {
                "node": item.nodeid,
                "ids": sorted(
                    {claim for mark in item.iter_markers("verifies") for claim in mark.args}
                ),
            }
            for item in session.items
        ]
        Path(destination).write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base", help="classify deleted IDs against this revision")
    parser.add_argument("--exclude-file", type=Path, help="temporary tag-at-merge paths, JSON list")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--body", type=Path)
    args = parser.parse_args(argv)
    if args.prune and not args.body:
        parser.error("--prune requires --body")
    files = sources(args.root)
    rows = registry(files)
    excluded = set(json.loads(args.exclude_file.read_text()) if args.exclude_file else ())
    items = [t for t in inventory(files) if t.node.split("::")[0] not in excluded]
    before = registry(sources(args.root, args.base)) if args.base else {}
    report = audit(items, rows, before)
    report["tag_at_merge"] = sorted(excluded)
    duplicate = {}
    if args.coverage:
        from scripts.ci_structural_guards import GUARDS

        document = json.loads(args.coverage.read_text(encoding="utf-8"))
        duplicate = duplicates(
            [t for t in items if all(rows.get(i) == "current" for i in t.ids)],
            coverage_lines(document),
            {f"tests/{name}.py" for name in GUARDS},
        )
    report["duplicates"] = duplicate
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.prune:
        orphans = {t.node for t in items if not any(rows.get(i) == "current" for i in t.ids)}
        prune(args.root, items, orphans | set(duplicate))
        body = ["Prune tests against the current OpenSpec shape.", "", "Registry row(s): S8", ""]
        if orphans:
            body.append(f"Test-Removal: retired -- {len(orphans)} tests name no current shape row.")
        if duplicate:
            body.append(
                f"Test-Removal: consolidated -- {len(duplicate)} tests have kept coverage witnesses"
            )
        body.extend(["", "Requires ordinary review; do not auto-merge.", ""])
        body.extend(f"- `{node}`: orphaned" for node in sorted(orphans))
        body.extend(f"- `{node}`: covered by `{witness}`" for node, witness in duplicate.items())
        args.body.write_text("\n".join(body) + "\n", encoding="utf-8")
        return 0
    print(
        f"{report['tests']} tests; {len(report['untagged'])} untagged; "
        f"{len(report['unresolvable'])} unresolvable; {len(duplicate)} coverage subsets"
    )
    for node in report["untagged"]:
        print(f"untagged: {node}")
    for finding in report["unresolvable"]:
        print(f"{finding['state']}: {finding['test']} -> {finding['id']}")
    return int(bool(report["untagged"] or report["unresolvable"]))


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
