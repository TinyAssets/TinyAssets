"""The shared libraries a custom UI may name instead of vendoring them.

A UI can always carry any library itself, as a JS asset. These are the common
ones, vendored at a pinned upstream version and integrity-pinned by SHA-384 in
``ui_libraries.json``, so an agent building a game does not spend a megabyte of
its owner's storage on three.js. The app fetches a named library from our own
origin, checks the pin in the browser, and posts it into the sandboxed frame,
which loads it from a ``blob:`` URL -- the frame itself never reaches a network.

The manifest ships everywhere the code does; the files (``ui_libraries/``) ship
only where the app is served. The local plugin runtime has no app, so the mirror
leaves them out, and a host without them answers ``library_unavailable`` rather
than pretending.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import json
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
MANIFEST_PATH = _HERE / "ui_libraries.json"
LIBRARY_DIR = _HERE / "ui_libraries"


class LibraryUnavailable(LookupError):
    """The named library is allowlisted but its file is not on this host."""


@functools.lru_cache(maxsize=1)
def manifest() -> dict[str, dict[str, Any]]:
    """Every allowlisted library by name: version, format, file, pin, requires."""
    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return dict(document["libraries"])


def names() -> tuple[str, ...]:
    return tuple(sorted(manifest()))


def check_names(requested: Any) -> list[str]:
    """``requested`` as a validated list, or ValueError naming what is wrong."""
    if not isinstance(requested, list):
        raise ValueError("libraries must be a list of library names")
    known = manifest()
    seen: set[str] = set()
    for name in requested:
        if not isinstance(name, str) or name not in known:
            raise ValueError(
                f"library {name!r} is not one this app provides; available: {list(names())}"
            )
        if name in seen:
            raise ValueError(f"library {name!r} is listed twice")
        seen.add(name)
    return list(requested)


def load_order(requested: list[str]) -> list[str]:
    """``requested`` plus everything it requires, each after its requirements."""
    known = manifest()
    ordered: list[str] = []

    def visit(name: str) -> None:
        if name in ordered:
            return
        for dependency in known[name].get("requires") or []:
            visit(dependency)
        ordered.append(name)

    for name in check_names(requested):
        visit(name)
    return ordered


def _pin(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")


@functools.lru_cache(maxsize=None)
def library_bytes(name: str) -> bytes:
    """The vendored file, verified against its pin; refuses loudly otherwise."""
    entry = manifest().get(name)
    if entry is None:
        raise KeyError(name)
    path = LIBRARY_DIR / str(entry["file"])
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise LibraryUnavailable(name) from None
    if _pin(data) != entry["sha384"]:
        # A file that no longer matches its pin is a broken deploy, not a
        # library to serve; the browser would refuse it anyway.
        raise RuntimeError(f"vendored UI library {name!r} does not match its pinned SHA-384")
    return data


__all__ = [
    "LIBRARY_DIR",
    "MANIFEST_PATH",
    "LibraryUnavailable",
    "check_names",
    "library_bytes",
    "load_order",
    "manifest",
    "names",
]
