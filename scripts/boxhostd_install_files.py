#!/usr/bin/env python3
"""Files deploy must copy beside deploy/install-boxhostd-preflight.sh.

The installer is the ship list. It reads siblings from its own directory
(``${source_dir}/…``). This script resolves those names to ``deploy/`` or
``scripts/`` paths and prints them, plus the installer itself. Workflows copy
that output into one directory; they do not keep a second list.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

INSTALLER = "deploy/install-boxhostd-preflight.sh"
SEARCH_ROOTS = ("deploy", "scripts")
_NAME = r"[A-Za-z0-9_.-]+"
_VAR = r"[A-Za-z_][A-Za-z0-9_]*"
_SOURCE_DIR_USE = re.compile(r"\$\{source_dir\}(/[^\s\"'`;&|<>()]*)?")
_BARE_SOURCE_DIR_USE = re.compile(r"(?<!\{)\$source_dir(/[^\s\"'`;&|<>()]*)?")
_FOR_LIST = re.compile(rf"for\s+({_VAR})\s+in\s+([^;\n]+);")


class InstallFileError(RuntimeError):
    """The installer names a file the ship list cannot resolve."""


def referenced_basenames(text: str) -> list[str]:
    """Basenames the installer reads from the directory that contains it."""
    names: list[str] = []
    seen: set[str] = set()
    indirect: list[str] = []

    def add(name: str) -> None:
        if not re.fullmatch(_NAME, name):
            raise InstallFileError(f"unusable install file name: {name!r}")
        if name not in seen:
            seen.add(name)
            names.append(name)

    def take(suffix: str | None, raw: str) -> None:
        if suffix is None:
            return
        body = suffix[1:]
        if re.fullmatch(_NAME, body):
            add(body)
            return
        var = re.fullmatch(rf"\$\{{({_VAR})\}}|\$({_VAR})", body)
        if var:
            indirect.append(var.group(1) or var.group(2))
            return
        raise InstallFileError(f"unparsed installer reference: {raw}")

    for match in _SOURCE_DIR_USE.finditer(text):
        take(match.group(1), match.group(0))
    for match in _BARE_SOURCE_DIR_USE.finditer(text):
        take(match.group(1), match.group(0))

    lists: dict[str, list[str]] = {}
    for var, raw in _FOR_LIST.findall(text):
        lists.setdefault(var, []).append(raw.strip())
    for var in indirect:
        rows = lists.get(var)
        if not rows:
            raise InstallFileError(f"${{source_dir}}/${{{var}}} has no literal for-loop file list")
        for raw in rows:
            tokens = raw.split()
            if not tokens or any(not re.fullmatch(_NAME, token) for token in tokens):
                raise InstallFileError(f"for {var} in {raw!r} is not a literal file list")
            for token in tokens:
                add(token)
    return names


def ship_list(root: Path) -> list[str]:
    """Repo-relative paths to copy into one directory before the installer runs."""
    installer = root / INSTALLER
    if not installer.is_file():
        raise InstallFileError(f"missing {INSTALLER}")
    names = referenced_basenames(installer.read_text(encoding="utf-8"))
    paths = [INSTALLER]
    for name in names:
        hits = [f"{base}/{name}" for base in SEARCH_ROOTS if (root / base / name).is_file()]
        if len(hits) != 1:
            found = ", ".join(hits) if hits else "none"
            raise InstallFileError(
                f"{name} must be exactly one of "
                + " or ".join(f"{base}/{name}" for base in SEARCH_ROOTS)
                + f" (found {found})"
            )
        paths.append(hits[0])
    unique: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    basenames = [Path(path).name for path in unique]
    if len(basenames) != len(set(basenames)):
        raise InstallFileError(
            "ship list has two files with the same basename; the copy flattens into one directory"
        )
    return unique


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    try:
        for path in ship_list(root):
            print(path)
    except InstallFileError as error:
        print(f"boxhostd install files: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
