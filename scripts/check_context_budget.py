#!/usr/bin/env python3
"""Guard the rulebook's size: the always-loaded set, and the shrink-only ratchet.

**The rulebook only shrinks: a new rule must displace an old one; the ratchet
enforces it** (`AGENTS.md` § *The rulebook only shrinks*). Every rulebook file is
pinned at the byte size it had after the 2026-09-26 cut. Lowering a pin is always
allowed — that is the ratchet turning. Raising one is not: it is the edit that
must not happen quietly, so it happens here, in a reviewed diff, or not at all.

Best-practice basis (2026-06-24 SDLC/vibe-coding + Claude-large-codebases audit,
`docs/audits/2026-06-24-sdlc-vibe-coding-claude-best-practices-adoption.md`):
instruction files that load on *every* turn are static context the model pays
for unconditionally. The "lean and layered" rule only holds if something
measures it — a 2026-04-28 cross-check put AGENTS.md at ~17.6 KB, and by
2026-06-24 it had tripled to ~54 KB with no guardrail noticing. This script is
that guardrail: it measures the rulebook and flags growth.

These numbers are OURS, not a vendor limit. Anthropic publishes no line or
token ceiling for CLAUDE.md; its stated test is behavioral -- "for each line,
ask: would removing this cause Claude to make mistakes?" -- and it warns that
"minimal does not necessarily mean short". So do not cite this budget as best
practice. It exists because an unmeasured file grew 17.6 KB -> 54 KB with
nothing noticing, and a ratchet is the cheapest thing that catches that. If a
rule inside the budget is still being skipped, the file is too long FOR THAT
RULE regardless of what this script says.

Two budget classes:
  * HARD  — a ceiling the project has committed to. Enforcing a stated
            contract is not a judgement call, so `--strict` exits 2 when a
            HARD budget is exceeded.
  * SOFT  — advisory; WARNs but never fails, even under --strict. No entry is
            SOFT today; the class is kept for content whose ceiling is a
            genuine judgement call rather than a committed limit.

Usage:
  python scripts/check_context_budget.py            # report table, exit 0
  python scripts/check_context_budget.py --strict    # exit 2 if a HARD budget is busted
  python scripts/check_context_budget.py --json       # machine-readable
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Budget:
    path: str
    kind: str  # "hard" | "soft"
    max_bytes: int
    max_lines: int  # 0 = unchecked; bytes are the ratchet
    note: str
    always_loaded: bool = True


# The rulebook. Two scopes, one authority per file:
#
#   always_loaded=True  -- CLAUDE.md imports @AGENTS.md, so both load every
#                          session. They also count toward COMBINED_HARD_BYTES.
#   always_loaded=False -- pointer-loaded procedure under docs/reference/. Not
#                          part of the per-turn payload, but part of the rulebook,
#                          so it is ratcheted the same way.
#
# PLAN.md (retired 2026-10-06, ADR-005) and STATUS.md (retired 2026-08-25) left
# the set entirely.
#
# Pins are EXACT post-recut sizes (2026-09-26), not round numbers: a ceiling set at
# the achieved value is a ratchet, one set at a comfortable round number is a
# wish. This set grew from ~17.6 KB (2026-04-28) to 62,082 B under SOFT budgets
# that only warned, with the invariant registered and VIOLATED the whole time.
#
# Line caps are unchecked (0). Bytes dominate context cost, and two measures of
# one fact means a reflow that removes words can still fail — a second authority
# for the same thing, which is what the 2026-09-26 cut was removing.
CONFIG: tuple[Budget, ...] = (
    Budget("AGENTS.md", "hard", 2816, 0,
           "The loop and the un-inferable facts. Direction lives in README.md."),
    Budget("CLAUDE.md", "hard", 420, 0,
           "Two harness quirks. Nothing else belongs here."),
    Budget("docs/reference/executable-gates.md", "hard", 1154, 0,
           "Index of gates -> scripts. Add a row only by deleting one.",
           always_loaded=False),
)

# Deleted 2026-09-26 because a procedure doc is where a rule goes to hide: both
# restated what the loop in AGENTS.md says, and quality-gates.md additionally
# contradicted `pr-scope-guard.yml`. Recreating either is how the rulebook grows
# back, so their absence is pinned like a size.
FORBIDDEN: tuple[str, ...] = (
    "docs/reference/quality-gates.md",
    "docs/reference/delivery-flow.md",
)

# HARD ceiling for the combined always-loaded payload (AGENTS.md + CLAUDE.md +
# anything they @import), pinned at the achieved post-recut total.
COMBINED_HARD_BYTES = 3236


@dataclass
class Aggregate:
    """A whole directory of rule files under one pin.

    Per-file pins alone leave an offload hole: the next rule gets written into a
    file nobody pinned, and the rulebook grows while every pin stays green. An
    aggregate closes it -- new prose in the set has to be paid for out of the set.
    """

    label: str
    pattern: str
    max_bytes: int
    exclude: tuple[str, ...] = ()
    note: str = ""


# `environment-variables.md` and `workos-authkit-integration.md` are excluded
# deliberately: they catalog SYSTEM facts (every env var; one integration's
# endpoints and claims) and grow when the product does, which is not rulebook
# growth. Every other docs/reference file is procedure, and procedure is capped.
AGGREGATES: tuple[Aggregate, ...] = (
    Aggregate("docs/reference/*.md", "docs/reference/*.md", 6741,
              exclude=("environment-variables.md", "workos-authkit-integration.md"),
              note="Procedure docs. A new gate here displaces an old one."),
    Aggregate(".agents/skills/*/SKILL.md", ".agents/skills/*/SKILL.md", 51730,
              note="Skills are rulebook too -- a rule moved into a skill is still a rule."),
    Aggregate("docs/reviews/*", "docs/reviews/*", 538389,
              note="Only reviews an open concern or spec CITES -- a transcript "
                   "nobody reaches for does not live in the repo."),
)


@dataclass
class Result:
    path: str
    kind: str
    exists: bool
    bytes: int
    lines: int
    max_bytes: int
    max_lines: int
    over_bytes: bool
    over_lines: bool
    note: str
    always_loaded: bool = True

    @property
    def over(self) -> bool:
        return self.over_bytes or self.over_lines

    @property
    def status(self) -> str:
        if not self.exists:
            return "MISSING"
        if not self.over:
            return "OK"
        return "OVER-HARD" if self.kind == "hard" else "OVER-soft"


# README.md's Direction block, mirrored into AGENTS.md by scripts/sync_direction.py.
# It is founder-owned direction, not a rule, so it is not ratcheted here: the
# sync check holds the copy verbatim to README and caps its size.
_DIRECTION_RE = re.compile(rb"<!-- direction:start -->.*?<!-- direction:end -->", re.S)


def measure(budget: Budget, root: Path) -> Result:
    fp = root / budget.path
    if not fp.is_file():
        return Result(budget.path, budget.kind, False, 0, 0,
                      budget.max_bytes, budget.max_lines, False, False, budget.note,
                      budget.always_loaded)
    data = fp.read_bytes()
    if budget.path == "AGENTS.md":   # the only file sync_direction.py writes
        data = _DIRECTION_RE.sub(b"", data)
    nbytes = len(data)
    nlines = data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)
    # `max_lines == 0` means unchecked: bytes are the ratchet. A file that shrinks
    # in bytes while gaining a wrapped line must not read as a violation.
    return Result(
        budget.path, budget.kind, True, nbytes, nlines,
        budget.max_bytes, budget.max_lines,
        nbytes > budget.max_bytes,
        budget.max_lines > 0 and nlines > budget.max_lines,
        budget.note, budget.always_loaded,
    )


# Claude resolves `@path` imports INLINE (not only on their own line), for any
# file type, relative to the IMPORTING file, skipping code spans and fences,
# bounded to four hops. Ref: https://code.claude.com/docs/en/memory#import-additional-files
#
# The first version of this parser matched `^@(...)\.md$` only, which a
# cross-family probe broke six ways: inline imports missed, non-.md missed,
# uppercase extension missed, fenced imports falsely counted, nested imports
# resolved from the repo root, and `@../outside.md` accepted.
IMPORT_RE = re.compile(r"(?<![\w`])@([A-Za-z0-9_./\\~-]+\.[A-Za-z0-9]{1,8})")
MAX_IMPORT_DEPTH = 4

_FENCE_RE = re.compile(r"^\s*(```|~~~)", re.MULTILINE)
_SPAN_RE = re.compile(r"`[^`\n]*`")


def strip_code(text: str) -> str:
    """Blank out fenced blocks and inline code spans.

    An `@file.md` shown as an EXAMPLE inside a fence is documentation, not an
    import, and counting it inflates the budget against a file that is never
    loaded. Fences are removed first so a span regex cannot straddle one.
    """
    out, fenced = [], False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        out.append("" if fenced else line)
    return _SPAN_RE.sub("", "\n".join(out))


def imported_files(root: Path, seeds: list[str]) -> list[str]:
    """Every file reachable by `@import` from the seeds, breadth-first.

    Resolution is relative to the IMPORTING file, matching Claude. Targets that
    escape the repo are ignored rather than counted: they are outside what this
    budget governs, and following them would let an import walk the filesystem.
    """
    from collections import deque

    root = root.resolve()
    seen: set[str] = set()
    order: list[str] = []
    queue: deque[tuple[str, int]] = deque((s, 0) for s in seeds)
    seeds_set = set(seeds)

    while queue:
        rel, depth = queue.popleft()   # deque: pop(0) on a list is superlinear
        if rel in seen or depth > MAX_IMPORT_DEPTH:
            continue
        seen.add(rel)
        if rel not in seeds_set:
            order.append(rel)

        path = (root / rel)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        base = path.parent
        for match in IMPORT_RE.finditer(strip_code(text)):
            raw = match.group(1).replace("\\", "/").strip()
            if not raw or raw.startswith("~"):
                continue
            try:
                target = (base / raw).resolve()
                target_rel = target.relative_to(root).as_posix()
            except (ValueError, OSError):
                continue          # outside the repo -> not our budget
            if target_rel not in seen:
                queue.append((target_rel, depth + 1))
    return order


def measure_aggregate(agg: Aggregate, root: Path) -> Result:
    """Total the matched set. An empty match is a violation, not a pass.

    A glob that stops matching -- a rename, a moved directory -- would otherwise
    read as "0 bytes, well under the pin", which is how a ratchet quietly stops
    ratcheting.
    """
    paths = sorted(
        p for p in root.glob(agg.pattern)
        if p.is_file() and p.name not in agg.exclude
    )
    total = sum(len(p.read_bytes()) for p in paths)
    lines = len(paths)  # the "lines" column carries the file count for a set
    return Result(
        agg.label, "hard", bool(paths), total, lines,
        agg.max_bytes, 0, total > agg.max_bytes, False, agg.note,
        always_loaded=False,
    )


def run(root: Path) -> tuple[list[Result], int, bool]:
    results = [measure(b, root) for b in CONFIG]
    results += [measure_aggregate(a, root) for a in AGGREGATES]

    # Anything reachable by @import is always-loaded too, so it counts toward
    # the combined ceiling even though it has no budget line of its own.
    seeds = [b.path for b in CONFIG if b.always_loaded]
    extra_bytes = 0
    extra: list[str] = []
    for rel in imported_files(root, seeds):
        path = root / rel
        try:
            extra_bytes += len(path.read_bytes())
            extra.append(rel)
        except OSError:
            continue

    combined = sum(r.bytes for r in results if r.exists and r.always_loaded) + extra_bytes

    # A configured always-loaded file that has VANISHED is a violation, not a
    # quiet pass. Deleting AGENTS.md must never be the cheapest way to satisfy
    # its own budget.
    missing = [r.path for r in results if not r.exists]

    # ...and the mirror case: a file deleted ON PURPOSE must not come back, or the
    # cut is undone one well-meaning recreation at a time.
    returned = [rel for rel in FORBIDDEN if (root / rel).is_file()]
    missing.extend(f"{rel} (deleted on purpose; do not recreate)" for rel in returned)

    hard_busted = (
        any(r.kind == "hard" and r.over for r in results)
        or combined > COMBINED_HARD_BYTES
        or bool(missing)
    )
    return results, combined, hard_busted, extra, missing


def _fmt_table(results: list[Result], combined: int) -> str:
    width = max([len(r.path) for r in results] + [len("COMBINED")])
    rows = [
        f"{'file':<{width}} {'scope':<8} {'lines':>6} {'bytes':>7}/{'pin':<6} status",
        "-" * (width + 38),
    ]
    for r in results:
        scope = "always" if r.always_loaded else "pointer"
        rows.append(
            f"{r.path:<{width}} {scope:<8} {r.lines:>6} "
            f"{r.bytes:>7}/{r.max_bytes:<6} {r.status}"
        )
    rows.append("-" * (width + 38))
    combined_flag = "  (!) OVER-HARD" if combined > COMBINED_HARD_BYTES else ""
    rows.append(
        f"{'COMBINED':<{width}} {'always':<8} {'':>6} "
        f"{combined:>7}/{COMBINED_HARD_BYTES:<6} always-loaded{combined_flag}"
    )
    for r in results:
        if r.over:
            rows.append(f"  - {r.path}: {r.note}")
    rows.append(
        "The rulebook only shrinks: lowering a pin is always allowed, raising one "
        "is not."
    )
    return "\n".join(rows)


def _fmt_extras(imported: list[str], missing: list[str]) -> str:
    lines: list[str] = []
    if imported:
        lines.append("  @imports counted toward COMBINED: " + ", ".join(imported))
    for path in missing:
        lines.append(f"  - {path}: MISSING -- a configured always-loaded file is gone")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Guard the always-loaded agent-context budget.")
    ap.add_argument("--strict", action="store_true",
                    help="Exit 2 if a HARD budget is exceeded (for CI / PostToolUse hook).")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ap.add_argument("--root", default=str(REPO_ROOT), help="Repo root to scan.")
    args = ap.parse_args(argv)

    results, combined, hard_busted, imported, missing = run(Path(args.root))

    if args.json:
        print(json.dumps({
            "results": [asdict(r) | {"status": r.status} for r in results],
            "combined_bytes": combined,
            "combined_hard_bytes": COMBINED_HARD_BYTES,
            "imported": imported,
            "missing": missing,
            "hard_busted": hard_busted,
        }, indent=2))
    else:
        print(_fmt_table(results, combined))
        extras = _fmt_extras(imported, missing)
        if extras:
            print(extras)
        if hard_busted:
            print("\nHARD budget exceeded -- a rulebook file grew past its pin. "
                  "Displace an old rule instead of adding one.")
        soft_over = [r.path for r in results if r.over and r.kind == "soft"]
        if soft_over:
            print(f"\nSoft target exceeded (advisory): {', '.join(soft_over)} -- "
                  "consider moving content to a pointer-loaded file or a skill.")

    if args.strict and hard_busted:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
