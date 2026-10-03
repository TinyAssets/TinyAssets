#!/usr/bin/env python3
"""Read-only: which directories under the data root an ownership row names.

A universe exists because an ownership row says so, not because a folder is on
disk (`openspec/specs/universe-lifecycle-and-soul/spec.md`, "A universe exists
because an ownership row names it"). That change makes an unowned directory
invisible to every reader -- which is the point for a prune's archive or an
operational bucket, and a REGRESSION for a real universe that never got a row.

That second case is why this script exists. Run it against a data root BEFORE
the deploy that lands the predicate:

    python scripts/universe_ownership_inventory.py
    python scripts/universe_ownership_inventory.py --data-dir /data

It reads, and only reads. The ownership store is opened `mode=ro`, which cannot
create the file, and nothing here calls `storage._connect`,
`daemon_server.owned_universe_id` or `initialize_author_server` -- all of which
WRITE (the first creates the database and sets WAL, the last runs schema
migrations). A tool that migrates the production database it was asked to inspect
is not a pre-deploy gate.

It is NOT the prune: deciding what to remove needs a positive reason to believe a
directory was a universe, which this deliberately does not guess at. Its only
recommendation is ever "write the missing ownership row".

**Exit 0 is not "safe to deploy."** The universe signal is a heuristic, and the
ABSENCE of a signal is no evidence: a legacy universe may hold a shape nobody
thought to list. A directory that is unowned and unrecognised is reported as
needing a human look, never as disposable.

Exit codes:
  0  no RECOGNISED universe is unowned. Read the report anyway: unowned
     directories this tool does not recognise are listed and need a human look.
  1  at least one AT-RISK directory: it carries a universe signal and no
     ownership row names it, so it would go dark
  2  the data root or the ownership store could not be read -- unknown, not safe
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# The four names the retired denylist carried, plus the operational buckets a
# live root is known to hold. Used ONLY to label a row, never to decide
# anything -- an unowned directory is unowned whether or not it is on this list.
KNOWN_OPERATIONAL = frozenset({
    "cloud-automation-inputs",
    "daemon_wikis",
    "lance",
    "lancedb",
    "output",
    "runs",
    "scratch",
    "wiki",
    "workspaces",
})


#: Files and subtrees only a universe has. A legacy universe predating `soul.md`
#: still holds its notes, its wiki or its outputs, and classifying it as
#: operational data because it lacks the modern seed marker is how a real
#: universe goes dark unnoticed (Codex review round 2).
_UNIVERSE_CONTENT_SIGNALS: tuple[tuple[str, str], ...] = (
    ("soul.md", "carries soul.md"),
    ("PROGRAM.md", "carries PROGRAM.md (legacy premise)"),
    ("notes.json", "carries notes.json"),
    ("status.json", "carries status.json"),
    ("dispatcher.json", "carries dispatcher.json"),
    ("identity.md", "carries identity.md"),
    ("work_targets.json", "carries work_targets.json"),
    ("activity.log", "carries activity.log"),
    ("wiki", "holds a wiki/ subtree"),
    ("output", "holds an output/ subtree"),
    ("canon", "holds a canon/ subtree"),
)


def _looks_like_a_universe(path: Path) -> str:
    """Why this directory looks like somebody's universe, or ``""``.

    A POSITIVE signal, never the absence of one. The absence proves nothing,
    which is why `_render` reports unrecognised directories as needing eyes
    rather than as safe.
    """
    from tinyassets.ids import is_universe_serial

    if is_universe_serial(path.name):
        return "platform-generated serial id"
    for name, reason in _UNIVERSE_CONTENT_SIGNALS:
        if (path / name).exists():
            return reason
    return ""


class OwnershipStoreUnreadable(RuntimeError):
    """The ownership store exists but could not be read. Unknown, not safe."""


def _read_ownership(base: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(acl grants, home bindings) keyed by universe id, from a READ-ONLY handle.

    This deliberately does NOT go through `storage._connect`,
    `daemon_server.owned_universe_id` or `initialize_author_server`, all of which
    WRITE: `_connect` creates the database file and sets `journal_mode=WAL`, and
    `initialize_author_server` runs schema migrations -- on an older production
    database that would add `founder_home.platform_generated` as a side effect of
    a command whose whole purpose is to be safe to point at a live root (Codex
    review round 2, P1). A tool that migrates the thing it was asked to inspect
    is not a pre-deploy gate.

    `mode=ro` refuses to create the file, so an absent store raises rather than
    being conjured. No store means nothing is owned, which is reported as such.
    """
    import sqlite3

    from tinyassets.storage import db_path

    path = db_path(base)
    if not path.is_file():
        return {}, {}
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10.0)
    except sqlite3.Error as exc:
        raise OwnershipStoreUnreadable(str(exc)) from exc
    try:
        conn.row_factory = sqlite3.Row
        acl: dict[str, list[str]] = {}
        homes: dict[str, list[str]] = {}
        for table, sink, value_col in (
            ("universe_acl", acl, "actor_id"),
            ("founder_home", homes, "founder_sub"),
        ):
            try:
                rows = conn.execute(
                    f"SELECT universe_id, {value_col}"
                    + (", permission" if table == "universe_acl" else "")
                    + f" FROM {table}"
                ).fetchall()
            except sqlite3.OperationalError as exc:
                # A store that predates the table: nothing is owned THROUGH it.
                # Any other operational error is a real read failure.
                if "no such table" not in str(exc):
                    raise OwnershipStoreUnreadable(str(exc)) from exc
                continue
            for row in rows:
                uid = str(row["universe_id"] or "").strip()
                if not uid:
                    continue
                value = str(row[value_col] or "").strip()
                if table == "universe_acl":
                    value = f"{value}:{row['permission']}"
                if value:
                    sink.setdefault(uid, []).append(value)
        return acl, homes
    except sqlite3.Error as exc:
        raise OwnershipStoreUnreadable(str(exc)) from exc
    finally:
        conn.close()


def inventory(base: Path) -> dict[str, Any]:
    """Every directory under ``base``, with its owners and its risk label."""
    acl_by_universe, owners_by_home = _read_ownership(base)
    # EXACT membership, mirroring `daemon_server.owned_universe_id` -- computed
    # here rather than called, because calling it initializes the store. A dotted
    # name is never a universe whatever a row says.
    owned_ids = {
        uid for uid in (set(acl_by_universe) | set(owners_by_home))
        if uid and not uid.startswith(".")
    }

    rows: list[dict[str, Any]] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        owned_id = child.name if child.name in owned_ids else ""
        acl = acl_by_universe.get(child.name, [])
        homes = owners_by_home.get(child.name, [])
        signal = _looks_like_a_universe(child)
        rows.append({
            "directory": child.name,
            "owned_as": owned_id,
            "acl_grants": sorted(acl),
            "home_bindings": sorted(homes),
            "universe_signal": signal,
            # The whole point of the report: an owned directory is served as
            # before; an unowned one with a universe signal goes DARK.
            "verdict": (
                "owned"
                if owned_id
                else "at-risk: would go dark"
                if signal
                else "unowned, unrecognised -- needs eyes"
            ),
            "known_operational_name": child.name in KNOWN_OPERATIONAL,
        })
    return {
        "data_dir": str(base),
        "directories": rows,
        "owned": sum(1 for r in rows if r["owned_as"]),
        "at_risk": [r["directory"] for r in rows if r["verdict"].startswith("at-risk")],
        "needs_eyes": [
            r["directory"] for r in rows if r["verdict"].startswith("unowned")
        ],
    }


def _render(report: dict[str, Any]) -> str:
    lines = [f"data root: {report['data_dir']}", ""]
    width = max((len(r["directory"]) for r in report["directories"]), default=9)
    for row in report["directories"]:
        owners = ", ".join(row["acl_grants"] + row["home_bindings"]) or "-"
        lines.append(
            f"  {row['directory']:<{width}}  {row['verdict']:<28}  {owners}"
            + (f"  ({row['universe_signal']})" if row["universe_signal"] else "")
        )
    lines += ["", f"owned: {report['owned']}"]
    if report["at_risk"]:
        lines.append(
            "AT RISK -- these carry a universe signal and nobody owns them, so "
            "the ownership predicate would hide them:"
        )
        lines += [f"  - {name}" for name in report["at_risk"]]
        lines.append(
            "Write the missing ownership row (a universe_acl grant or a "
            "founder_home binding) BEFORE deploying. Nothing here is deleted "
            "either way -- an unowned directory is hidden, not removed."
        )
    else:
        lines.append("no directory carrying a universe signal is unowned")
    if report["needs_eyes"]:
        # NOT "expected" and NOT "safe". The signal list is a heuristic, and the
        # ABSENCE of a signal is no evidence at all -- a legacy universe could
        # hold a shape nobody thought to list. Naming these as disposable is the
        # one thing this report must never do (Codex review round 2).
        lines.append(
            "UNOWNED AND UNRECOGNISED -- nobody owns these and this tool does "
            "not recognise them as universes. That is NOT evidence they are "
            "disposable: look at each one before deploying."
        )
        lines += [f"  - {name}" for name in report["needs_eyes"]]
    lines.append(
        "This report is evidence, not a verdict: exit 0 means no RECOGNISED "
        "universe is unowned, never that the deploy is safe."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default="",
        help="Data root to inspect. Default: the resolver "
             "(tinyassets.storage.data_dir), so this reads the same root the "
             "daemon does rather than a re-implemented precedence.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the raw report.")
    args = parser.parse_args(argv)

    if args.data_dir:
        base = Path(args.data_dir).expanduser().resolve()
    else:
        from tinyassets.storage import data_dir

        base = data_dir()

    if not base.is_dir():
        print(f"data root does not exist: {base}", file=sys.stderr)
        return 2
    # Never read a half-migrated root: the layout guard (storage_layout.py).
    from tinyassets.storage_layout import LayoutRefused, require_layout

    try:
        require_layout(base)
    except LayoutRefused as exc:
        print(f"layout refused: {exc}", file=sys.stderr)
        return 3
    try:
        report = inventory(base)
    except Exception as exc:  # noqa: BLE001 - unknown is not safe
        print(f"could not read the ownership store: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2) if args.json else _render(report))
    return 1 if report["at_risk"] else 0


if __name__ == "__main__":
    sys.exit(main())
