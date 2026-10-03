"""TinyAssets engine entry point.

Usage::

    python -m tinyassets --domain fantasy_author [--universe PATH] [--api] [--port 8000]

Loads a domain by name from the registry, builds its graph, and runs the daemon.
Delegates daemon execution to ``fantasy_daemon.__main__.DaemonController``
during the Phase 5 bridge.

The entry point:
1. Parses --domain, --universe, --api, --port arguments
2. Auto-discovers and registers domains from the domains/ directory
3. Looks up the requested domain from the registry
4. Builds the domain's graph using domain.build_graph()
5. For now, delegates daemon execution to fantasy_daemon.__main__.DaemonController
   (this is the Phase 5 bridge until runtime is fully extracted)
6. If --api is set, also starts the FastAPI server on --port
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase 5 bridge re-exports
# ---------------------------------------------------------------------------
# Re-export DaemonController from ``fantasy_daemon.__main__`` so callers can
# ``from tinyassets.__main__ import …`` without reaching into the
# fantasy_daemon package directly. Tests still target this surface; the
# block retires when the runtime fully moves out of fantasy_daemon.
#
# The Cloudflare tunnel helpers are deliberately absent: local public-ingress
# launch was removed, so there is nothing here to re-export.
# ---------------------------------------------------------------------------
import threading  # noqa: E402, F401  — tests patch tinyassets.__main__.threading

from fantasy_daemon.__main__ import (  # noqa: E402, F401
    LOCAL_TUNNEL_REMOVED_MESSAGE,
    DaemonController,
    _build_provider_router,
    _first_trace,
    _refuse_local_tunnel_request,
    _run_tray_mode,
)

__all__ = [
    "LOCAL_TUNNEL_REMOVED_MESSAGE",
    "DaemonController",
    "_build_provider_router",
    "_first_trace",
    "_refuse_local_tunnel_request",
    "_run_tray_mode",
    "main",
]


def _build_argparser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the workflow entry point."""
    parser = argparse.ArgumentParser(
        description="TinyAssets engine entry point",
        prog="python -m tinyassets",
    )

    parser.add_argument(
        "--domain",
        type=str,
        default="fantasy_author",
        help="Domain to load (default: fantasy_author)",
    )

    parser.add_argument(
        "--universe",
        type=str,
        default=None,
        help="Path to command center directory (optional)",
    )

    parser.add_argument(
        "--api",
        action="store_true",
        help="Start the FastAPI server in addition to the daemon",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to run the API server on (default: 8000)",
    )

    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to checkpoint database (optional)",
    )

    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Disable desktop tray (for headless operation)",
    )

    parser.add_argument(
        "--provider",
        type=str,
        default="",
        help="Pin the supervised writer provider",
    )

    return parser


def _truthy_env(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _declared_soul_loop_dispatch_requested(universe: str | None) -> bool:
    """Return True when the Phase 5 bridge may run a non-fantasy domain."""
    if not universe or not _truthy_env("TINYASSETS_SOUL_LOOP_DISPATCH"):
        return False
    try:
        from tinyassets.api.universe import (
            LEGACY_FANTASY_LOOP_BRANCH_DEF_ID,
            _universe_loop_dispatch,
        )

        loop_branch_def_id, _info = _universe_loop_dispatch(Path(universe))
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to resolve soul loop dispatch for command center %s",
            universe,
        )
        return False
    return bool(
        loop_branch_def_id
        and loop_branch_def_id != LEGACY_FANTASY_LOOP_BRANCH_DEF_ID
    )


def main() -> int:
    """Main entry point for the TinyAssets engine.

    Returns
    -------
    int
        Exit code (0 for success, 1 for error).
    """
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Parse arguments
    parser = _build_argparser()
    args = parser.parse_args()

    # Before any data is opened: the layout guard (storage_layout.py).
    from tinyassets.storage_layout import require_layout

    require_layout()

    # Import and auto-register domains
    try:
        from tinyassets.discovery import auto_register
        from tinyassets.registry import default_registry

        auto_register(default_registry)
        logger.info("Auto-registered domains: %s", default_registry.list_domains())
    except Exception as e:
        logger.error("Failed to auto-register domains: %s", e)
        return 1

    # Look up the requested domain
    domain = default_registry.get(args.domain)
    if domain is None:
        logger.error(
            "Domain '%s' not found. Available: %s",
            args.domain,
            default_registry.list_domains(),
        )
        return 1

    logger.info("Loaded domain: %s", args.domain)

    # Phase 5 bridge: for now, delegate to fantasy_daemon.__main__.DaemonController
    # This allows the domain abstraction to be tested without fully extracting
    # the runtime. Once the runtime is extracted, this will build and execute
    # the domain's graph directly.

    declared_soul_loop = _declared_soul_loop_dispatch_requested(args.universe)
    if args.domain != "fantasy_author" and not declared_soul_loop:
        logger.error(
            "Only fantasy_author domain is fully operational in this phase. "
            "Other domains can be registered but cannot yet be executed. "
            "Use --domain fantasy_author, or enable TINYASSETS_SOUL_LOOP_DISPATCH "
            "for a command center with a declared soul loop."
        )
        return 1
    if args.domain != "fantasy_author":
        logger.info(
            "Using Phase 5 bridge for domain %s because %s declares a "
            "flag-enabled soul loop",
            args.domain,
            args.universe,
        )

    try:
        from fantasy_daemon.__main__ import DaemonController
        from tinyassets.scoped_reset import prepare_service_writer_barrier
        from tinyassets.storage import data_dir

        writer_barrier = prepare_service_writer_barrier(data_dir())
        try:
            controller = DaemonController(
                universe_path=args.universe,
                db_path=args.db,
                no_tray=args.no_tray,
                pinned_provider=args.provider,
            )

            # If --api flag is set, start the API server alongside the daemon
            if args.api:
                logger.info("Starting API server on port %d", args.port)
                # The API server is currently managed separately in
                # fantasy_daemon.api.serve(). This integration will be completed
                # in a later phase. For now, users should run `python -m
                # fantasy_daemon serve` separately.
                logger.warning(
                    "API server integration incomplete. "
                    "Run 'python -m fantasy_daemon serve' separately."
                )

            # Run the daemon. DaemonController exposes start() (the blocking
            # daemon loop); there is no run(). start() returns None on clean
            # shutdown, so main() returns 0.
            controller.start()
            return 0
        finally:
            writer_barrier.release()

    except Exception as e:
        logger.error("Failed to run daemon: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
