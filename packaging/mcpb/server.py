"""Bundle entry point — boots the TinyAssets Server MCP.

The build script stages the live ``tinyassets/`` package next to this
file. ``uv run`` (configured by ``manifest.json``) then runs us with
the bundle root on ``sys.path``, so a normal ``import tinyassets.universe_server``
resolves to the bundled package — no shim, no importlib magic.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _resolve_data_dir() -> Path:
    """Return the user-selected data root, or fail closed.

    ``storage.data_dir()`` resolves an unset value to a platform default
    (``%APPDATA%/TinyAssets``, ``~/.tinyassets``). A local bundle must serve
    the directory the user chose, so an unconfigured install stops here
    rather than quietly adopting a host-global one.
    """
    base = os.environ.get("TINYASSETS_DATA_DIR", "").strip()
    if not base:
        raise RuntimeError(
            "TINYASSETS_DATA_DIR is required. Configure the bundle's "
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
    return base_path


def _resolve_default_universe() -> str | None:
    """Return the optional default universe id, or ``None`` when unset.

    The host substitutes ``${user_config.default_universe}`` with an empty
    string when the user leaves the optional field alone, so blank means
    unset — passing it through would make the runtime resolve a blank or
    whitespace universe id instead of its ordinary default.

    The rejected shapes are the runtime's own: ``storage.active_universe_id``
    discards a marker containing ``/`` or ``\\`` or starting with ``.``, and
    ``api.helpers._default_universe`` skips dot-prefixed directories when it
    scans. ``:`` is added because a Windows path with a colon names an
    alternate data stream rather than a universe folder, and ``${`` catches a
    template the host never substituted. Failing here beats failing
    per-request once the transport is already up.
    """
    value = os.environ.get("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "").strip()
    if not value:
        return None
    if (
        "${" in value
        or "/" in value
        or "\\" in value
        or ":" in value
        or value.startswith(".")
    ):
        raise RuntimeError(
            "UNIVERSE_SERVER_DEFAULT_UNIVERSE must be a command center folder name "
            f"directly under the data directory, not {value!r}. Fix the "
            "bundle's 'Default Command Center' setting or leave it empty."
        )
    return value


def main() -> None:
    base_path = _resolve_data_dir()
    default_universe = _resolve_default_universe()

    os.environ["TINYASSETS_DATA_DIR"] = str(base_path)
    if default_universe is None:
        os.environ.pop("UNIVERSE_SERVER_DEFAULT_UNIVERSE", None)
    else:
        os.environ["UNIVERSE_SERVER_DEFAULT_UNIVERSE"] = default_universe

    # Ensure the bundled `tinyassets/` package wins over any system copy.
    bundle_root = Path(__file__).resolve().parent
    if str(bundle_root) not in sys.path:
        sys.path.insert(0, str(bundle_root))

    from tinyassets import universe_server
    universe_server.main(transport="stdio")


if __name__ == "__main__":
    main()
