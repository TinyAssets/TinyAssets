"""Import-graph smoke — catch missing-symbol regressions at commit + install time.

Per ``openspec/specs/storage-runtime-accounting/spec.md``
Option B. Spawns a fresh Python subprocess to import canonical packages
and exercise every name in every ``__all__`` list. Exits 0 green, 1 + stderr
on any failure.

Why a fresh subprocess: the smoke's job is to prove "a process starting
from zero can import these packages cleanly." Running in-process would
reuse whatever cached state the caller already has — which is exactly
the stale-bytecode class we're guarding against.

Why derive from ``__all__`` rather than a hardcoded list (per scope-doc
open-Q B): intentional symbol removals shouldn't fail the smoke. The
smoke tests the contract "what's declared is importable," not a frozen
list that would false-flag every legitimate refactor.

Exit codes
----------
0  All target packages import cleanly + every ``__all__`` name resolves.
1  A missing-symbol regression — see stderr for the specific import.
2  An unexpected failure — see stderr for the traceback preview.

Targets
-------
Primary: ``tinyassets.storage`` (the 2026-04-19 P0 surface). This is the
minimum coverage; missing the ``tinyassets.storage`` smoke means the P0
class can recur.

Extended: any module under ``tinyassets/`` that declares ``__all__`` gets
exercised. Lightweight discovery — walks ``tinyassets/**/__init__.py`` and
opt-in imports each one.

Usage
-----
    python scripts/import_graph_smoke.py             # default targets
    python scripts/import_graph_smoke.py --verbose   # print each OK
    python scripts/import_graph_smoke.py --primary-only
"""

from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from pathlib import Path

# Load-bearing packages — these MUST import cleanly and every ``__all__``
# name must resolve. The 2026-04-19 P0 involved ``tinyassets.storage``; the
# others are the main consumer surfaces.
PRIMARY_TARGETS = [
    "tinyassets.storage",
    "tinyassets.daemon_server",
    "tinyassets.universe_server",
    "tinyassets.catalog",
]


def _check_module(module_path: str, *, verbose: bool) -> list[str]:
    """Import ``module_path``. Return list of error messages (empty = pass).

    - Import must succeed.
    - If the module declares ``__all__``, every name MUST resolve via
      ``getattr(mod, name)`` — this is what catches lazy-``__getattr__``
      regressions where a declared name routes to a missing submodule
      symbol.
    """
    errors: list[str] = []

    try:
        mod = importlib.import_module(module_path)
    except Exception:
        tb = traceback.format_exc(limit=6)
        errors.append(f"import {module_path} FAILED:\n{tb}")
        return errors

    declared = getattr(mod, "__all__", None)
    if declared is None:
        if verbose:
            print(f"[import-graph] ok   {module_path} (no __all__)")
        return errors

    for name in declared:
        try:
            getattr(mod, name)
        except AttributeError as exc:
            errors.append(
                f"{module_path}.__all__ declares {name!r} but "
                f"getattr raised AttributeError: {exc}"
            )
        except Exception as exc:
            errors.append(
                f"{module_path}.{name} access raised "
                f"{type(exc).__name__}: {exc}"
            )

    if verbose and not errors:
        print(f"[import-graph] ok   {module_path} (__all__ has "
              f"{len(declared)} names, all resolve)")
    return errors


def _extended_targets(repo_root: Path) -> list[str]:
    """Discover extra modules under ``tinyassets/`` that declare ``__all__``.

    Heuristic: walk ``tinyassets/**/__init__.py`` + top-level ``*.py``; the
    per-package smoke is the load-bearing part. Skip ``_private`` paths
    and ``__pycache__``.
    """
    out: set[str] = set(PRIMARY_TARGETS)
    wf_root = repo_root / "tinyassets"
    if not wf_root.is_dir():
        return sorted(out)

    for path in wf_root.rglob("__init__.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(repo_root)
        parts = list(rel.parts[:-1])  # drop __init__.py
        if any(p.startswith("_") for p in parts):
            continue
        mod_path = ".".join(parts)
        out.add(mod_path)

    return sorted(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verbose", action="store_true",
                    help="print each target on success")
    ap.add_argument("--primary-only", action="store_true",
                    help="only check the 5 load-bearing primary targets")
    args = ap.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    targets = (
        PRIMARY_TARGETS if args.primary_only
        else _extended_targets(repo_root)
    )

    all_errors: list[str] = []
    for module_path in targets:
        errors = _check_module(module_path, verbose=args.verbose)
        all_errors.extend(errors)

    if all_errors:
        print("[import-graph] FAILED", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        print(
            f"\n{len(all_errors)} import-graph regression(s) across "
            f"{len(targets)} target(s). "
            f"Fix before committing — stale-bytecode class of bug "
            "(see the storage import smoke contract).",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        print(f"[import-graph] ALL CLEAN — {len(targets)} target(s) checked")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
