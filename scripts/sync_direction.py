#!/usr/bin/env python3
"""Copy README.md's Direction block verbatim into AGENTS.md.

README.md § Direction is the only home of the founder's current direction. No
harness loads README on its own, but every harness loads AGENTS.md, so the block
is mirrored there between the same markers. The copy is generated: edit README,
run this, commit both.

The block is also capped here. `scripts/check_context_budget.py` measures
AGENTS.md without it (it is founder-owned, not a rule), so the cap and the
verbatim check are what stop rules being written into the copy.

Usage:
    python scripts/sync_direction.py           # rewrite the AGENTS.md copy
    python scripts/sync_direction.py --check   # exit 1 if the copy differs or the block is too big
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START = "<!-- direction:start -->"
END = "<!-- direction:end -->"
MAX_LINES = 25      # between the markers
MAX_BYTES = 4096

_BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)


def block(text: str, name: str) -> str:
    """The one marked block in ``text``, markers included, LF line endings."""
    found = _BLOCK.findall(text.replace("\r\n", "\n"))
    if len(found) != 1:
        raise SystemExit(f"{name}: expected one {START} ... {END} block, found {len(found)}")
    return found[0]


def problems(root: Path) -> list[str]:
    readme = block((root / "README.md").read_text(encoding="utf-8"), "README.md")
    agents = block((root / "AGENTS.md").read_text(encoding="utf-8"), "AGENTS.md")
    out = []
    inner = readme.split("\n")[1:-1]
    if len(inner) > MAX_LINES:
        out.append(f"README.md Direction is {len(inner)} lines; the cap is {MAX_LINES}. "
                   "Replace a line instead of adding one.")
    if len(readme.encode("utf-8")) > MAX_BYTES:
        out.append(f"README.md Direction is over {MAX_BYTES} bytes.")
    if agents != readme:
        out.append("AGENTS.md Direction differs from README.md. "
                   "Edit README.md, then run: python scripts/sync_direction.py")
    return out


def sync(root: Path) -> bool:
    """Rewrite the AGENTS.md copy; return whether it changed."""
    readme = block((root / "README.md").read_text(encoding="utf-8"), "README.md")
    path = root / "AGENTS.md"
    raw = path.read_bytes().decode("utf-8")
    block(raw, "AGENTS.md")
    newline = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    new = _BLOCK.sub(lambda _m: readme, text, count=1)
    if new == text:
        return False
    path.write_bytes(new.replace("\n", newline).encode("utf-8"))
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="Fail instead of writing.")
    ap.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    root = Path(args.root)
    if not args.check:
        print("AGENTS.md Direction " + ("updated." if sync(root) else "already in sync."))
    found = problems(root)
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
