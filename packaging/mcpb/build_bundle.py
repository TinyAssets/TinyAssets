"""Stage and pack the TinyAssets Server MCPB bundle.

Per `docs/design-notes/2026-04-14-packaging-mirror-decision.md` Option 1:
the bundle stages the live `tinyassets/` package directly. The legacy
`fantasy_author/universe_server.py` shim path is gone — `server.py`
inside the bundle imports `tinyassets.universe_server` like a normal
Python package consumer.

A subprocess import probe runs against the staged bundle before pack
so a missing dependency or a broken import fails the build loudly
instead of producing a silently-broken `.mcpb` artifact.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = REPO_ROOT / "packaging" / "mcpb"
DIST_ROOT = REPO_ROOT / "packaging" / "dist"
STAGE_ROOT = DIST_ROOT / "tinyassets-universe-server-src"
BUNDLE_PATH = DIST_ROOT / "tinyassets-universe-server.mcpb"

TINYASSETS_SRC = REPO_ROOT / "tinyassets"

# Provider credentials never reach a packaging probe. `_probe_catalog` boots
# the staged runtime, so an inherited key is the shape that would let a
# packaging gate spend maintainer quota instead of proving the package.
PROVIDER_CREDENTIAL_ENV: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "XAI_API_KEY",
    "TINYASSETS_ALLOW_API_KEY_PROVIDERS",
)

# Patterns excluded when copying the tinyassets/ tree into the stage.
# Glob shapes match Path.match semantics.
_TREE_EXCLUDES: tuple[str, ...] = (
    "__pycache__",
    "*.db",
    "*.db-journal",
    "*.log",
    "*.pyc",
    ".pytest_cache",
    "*.tmp",
    # Vendored custom-UI libraries are served only by the hosted app; the
    # bundle runs no app. Same rule as the plugin mirror (build_plugin.py).
    "ui_libraries",
)


def _is_excluded(path: Path, repo_relative_root: Path) -> bool:
    """Return True if ``path`` matches any exclude pattern by name."""
    name = path.name
    for pattern in _TREE_EXCLUDES:
        if path.match(pattern):
            return True
        if name == pattern:
            return True
    return False


def _copy_tree(source: Path, destination: Path) -> int:
    """Copy a directory tree, skipping `_TREE_EXCLUDES` entries.

    Returns the file count actually copied — useful for the build log
    and as a smoke signal that the source wasn't empty.
    """
    if not source.is_dir():
        raise FileNotFoundError(f"Source tree not found: {source}")
    count = 0
    for src in source.rglob("*"):
        if any(_is_excluded(part_path, source) for part_path in src.parents):
            continue
        if _is_excluded(src, source):
            continue
        rel = src.relative_to(source)
        dst = destination / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            count += 1
    return count


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Required source file not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _stage_bundle() -> Path:
    if STAGE_ROOT.exists():
        shutil.rmtree(STAGE_ROOT)
    STAGE_ROOT.mkdir(parents=True, exist_ok=True)

    _copy_file(TEMPLATE_ROOT / "manifest.json", STAGE_ROOT / "manifest.json")
    _copy_file(TEMPLATE_ROOT / "pyproject.toml", STAGE_ROOT / "pyproject.toml")
    _copy_file(TEMPLATE_ROOT / "server.py", STAGE_ROOT / "server.py")
    _copy_file(
        REPO_ROOT / "assets" / "icon.png",
        STAGE_ROOT / "assets" / "icon.png",
    )

    # Stage the live `tinyassets/` package — single source of truth per
    # design-note Option 1. Excludes runtime artifacts (.db, __pycache__,
    # logs) that would bloat the bundle without adding value.
    file_count = _copy_tree(TINYASSETS_SRC, STAGE_ROOT / "tinyassets")
    print(f"Staged tinyassets/ package: {file_count} files")

    return STAGE_ROOT


def _probe_env() -> dict[str, str]:
    """Base environment for every subprocess probe: no provider credentials."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    for name in PROVIDER_CREDENTIAL_ENV:
        env.pop(name, None)
    return env


def _probe_import(stage: Path) -> None:
    """Fail loudly if the staged bundle can't import its entry point.

    Runs in a subprocess so the probe's import side-effects don't leak
    into the build process. Sets ``sys.path`` to the stage root so the
    bundled ``tinyassets/`` package resolves before any installed copy.
    ``PYTHONDONTWRITEBYTECODE=1`` keeps the probe from littering the
    fresh stage with ``__pycache__`` files that would then be packed.
    """
    probe_script = (
        f"import sys; sys.path.insert(0, {str(stage)!r}); "
        "import tinyassets.universe_server as us; "
        "assert hasattr(us, 'main'), 'tinyassets.universe_server.main missing'; "
        "print('probe-ok')"
    )
    env = _probe_env()
    result = subprocess.run(
        [sys.executable, "-c", probe_script],
        capture_output=True, text=True, check=False, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Staged bundle import probe failed.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    print(f"Import probe: {result.stdout.strip() or 'ok'}")


def _probe_catalog(stage: Path) -> None:
    """Fail when the staged manifest and middleware-visible tools differ."""
    manifest_path = stage / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_names = {tool["name"] for tool in manifest["tools"]}
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Staged MCPB manifest tool catalog is invalid: {exc}"
        ) from exc

    marker = "TINYASSETS_MCPB_CATALOG="
    probe_script = "\n".join(
        (
            "import asyncio",
            "import json",
            "import sys",
            f"sys.path.insert(0, {str(stage)!r})",
            "import tinyassets.universe_server as universe_server",
            (
                "names = sorted(tool.name for tool in "
                "asyncio.run(universe_server.mcp.list_tools("
                "run_middleware=True)))"
            ),
            f"print({marker!r} + json.dumps(names))",
        )
    )
    env = _probe_env()
    env.pop("TINYASSETS_REPO_ROOT", None)
    env.pop("UNIVERSE_SERVER_AUTH", None)

    with tempfile.TemporaryDirectory(
        prefix="tinyassets-mcpb-catalog-",
    ) as data_dir:
        env["TINYASSETS_DATA_DIR"] = data_dir
        result = subprocess.run(
            [sys.executable, "-c", probe_script],
            cwd=str(stage),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    if result.returncode != 0:
        raise RuntimeError(
            "Staged bundle catalog probe failed.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    payload = next(
        (
            line.removeprefix(marker)
            for line in reversed(result.stdout.splitlines())
            if line.startswith(marker)
        ),
        None,
    )
    if payload is None:
        raise RuntimeError(
            "Staged bundle catalog probe produced no catalog result.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    try:
        runtime_names = set(json.loads(payload))
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Staged bundle catalog probe returned invalid JSON: {payload!r}"
        ) from exc

    missing_from_manifest = sorted(runtime_names - manifest_names)
    extra_in_manifest = sorted(manifest_names - runtime_names)
    if missing_from_manifest or extra_in_manifest:
        raise RuntimeError(
            "Staged MCPB catalog mismatch: "
            f"missing_from_manifest={missing_from_manifest}; "
            f"extra_in_manifest={extra_in_manifest}"
        )

    print(f"Catalog parity: {', '.join(sorted(runtime_names))}")


def _run(command: list[str], *, cwd: Path) -> None:
    executable = (
        shutil.which(command[0])
        or shutil.which(f"{command[0]}.cmd")
        or command[0]
    )
    subprocess.run([executable, *command[1:]], cwd=str(cwd), check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Stage and optionally validate/pack the TinyAssets MCPB bundle. "
            "The staging step always runs; --validate adds the @anthropic-ai/mcpb "
            "manifest validator; --pack produces the .mcpb artifact."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the staged bundle with the official MCPB CLI.",
    )
    parser.add_argument(
        "--pack",
        action="store_true",
        help="Pack the staged bundle into packaging/dist/tinyassets-universe-server.mcpb.",
    )
    parser.add_argument(
        "--skip-probe",
        action="store_true",
        help=(
            "Skip the subprocess import probe. Use only when running in a "
            "minimal CI matrix that lacks the bundle's runtime deps "
            "(fastmcp etc.)."
        ),
    )
    args = parser.parse_args()
    if args.skip_probe and (args.validate or args.pack):
        parser.error(
            "--skip-probe cannot be combined with --validate or --pack; "
            "a schema-only check is not catalog-parity validation"
        )

    stage_root = _stage_bundle()
    print(f"Staged bundle source at {stage_root}")

    if not args.skip_probe:
        _probe_import(stage_root)
        _probe_catalog(stage_root)

    if args.validate or args.pack:
        _run(
            ["npx", "-y", "@anthropic-ai/mcpb", "validate", str(stage_root)],
            cwd=REPO_ROOT,
        )
        print("MCPB manifest validation passed.")

    if args.pack:
        DIST_ROOT.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "npx",
                "-y",
                "@anthropic-ai/mcpb",
                "pack",
                str(stage_root),
                str(BUNDLE_PATH),
            ],
            cwd=REPO_ROOT,
        )
        print(f"Packed bundle at {BUNDLE_PATH}")


if __name__ == "__main__":
    main()
