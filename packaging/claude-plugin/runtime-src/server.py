"""Plugin runtime entry — boots the TinyAssets Server MCP.

The build script (``packaging/claude-plugin/build_plugin.py``) stages
the live ``tinyassets/`` package next to this file. ``import
tinyassets.universe_server`` then resolves to the staged copy — no shim,
no importlib magic.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    base = os.environ.get("TINYASSETS_DATA_DIR", "").strip()
    if not base:
        raise RuntimeError(
            "TINYASSETS_DATA_DIR is required. Configure the plugin's "
            "'TinyAssets Data Directory' before launching it."
        )

    base_path = Path(base).expanduser().resolve()
    if not base_path.exists():
        raise RuntimeError(
            f"TINYASSETS_DATA_DIR does not exist: {base_path}"
        )
    if not base_path.is_dir():
        raise RuntimeError(
            f"TINYASSETS_DATA_DIR must be a directory: {base_path}"
        )

    os.environ["TINYASSETS_DATA_DIR"] = str(base_path)

    runtime_root = Path(__file__).resolve().parent
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))

    from tinyassets import universe_server
    universe_server.main(transport="stdio")


if __name__ == "__main__":
    main()
