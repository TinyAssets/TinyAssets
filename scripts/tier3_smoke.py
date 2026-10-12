"""Tier-3 OSS clone structural smoke.

Per ``docs/ops/host-independence-runbook.md`` §5.
Runs after ``pip install -e .`` on a fresh clone and asserts the
load-bearing imports succeed. This is NOT a feature-correctness test —
the main pytest suite owns that surface. This script catches "fresh
install is broken" failures that show up silently for OSS contributors.

Stdlib-only. Each failure raises loudly with a message naming the
specific import that regressed (AGENTS.md Hard Rule 8: fail loudly).

Exit codes
----------
0  All structural imports succeeded.
1  A structural import failed — see stderr for the specific regression.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Callable

# Add repo root to sys.path so the script works BOTH in a post-install
# GHA environment (where ``workflow`` is pip-installed) AND when run
# directly from a checkout without install (dev smoke-run).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _check(label: str, fn: Callable[[], object]) -> None:
    try:
        fn()
    except Exception as exc:  # intentionally broad — any failure = regression
        print(f"[tier3_smoke] FAIL {label}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"[tier3_smoke] ok   {label}")


def _import_attr(module: str, attr: str) -> object:
    mod = importlib.import_module(module)
    if not hasattr(mod, attr):
        raise AttributeError(f"{module} missing attribute {attr!r}")
    return getattr(mod, attr)


def main() -> int:
    # 1. Core public entry point — the module everything else hangs off.
    _check("workflow package imports", lambda: importlib.import_module("tinyassets"))

    # 2. daemon_server — the SQLite-backed multiplayer substrate.
    _check(
        "tinyassets.daemon_server imports",
        lambda: importlib.import_module("tinyassets.daemon_server"),
    )

    # 3. FastMCP object — proves universe_server wired up.
    def _mcp_has_tools() -> object:
        mcp = _import_attr("tinyassets.universe_server", "mcp")
        if mcp is None:
            raise AssertionError("tinyassets.universe_server.mcp is None")
        return mcp

    _check("tinyassets.universe_server.mcp exists", _mcp_has_tools)

    # 4. Bid surface (R2 end-state). Regression here means the Phase-G
    #    refactor left a broken re-export.
    _check(
        "tinyassets.bid imports",
        lambda: importlib.import_module("tinyassets.bid"),
    )

    # 5. Catalog surface (R7a end-state). Regression here means the
    #    storage-split rename left a broken import.
    _check(
        "tinyassets.catalog imports",
        lambda: importlib.import_module("tinyassets.catalog"),
    )

    # 6. Domain skill discovery. fantasy_daemon is the benchmark
    #    domain; if it can't import, domain registration is broken.
    _check(
        "domains.fantasy_daemon imports",
        lambda: importlib.import_module("domains.fantasy_daemon"),
    )

    print("[tier3_smoke] all structural imports passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
