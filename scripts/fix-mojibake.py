"""Detect + optionally auto-fix UTF-8 mojibake in text files.

Mojibake: UTF-8 bytes interpreted as CP-1252 and re-encoded as UTF-8 —
produces garbled sequences like `â€"` where an em-dash should be. Most
commonly introduced when a session pastes text through a CP-1252 shell
on Windows.

Two modes:

    python scripts/fix-mojibake.py PATH [PATH ...]
        Detection only. Prints file:line:column for each occurrence
        plus the suggested fix character. Exits non-zero on any match.

    python scripts/fix-mojibake.py --autofix PATH [PATH ...]
        Applies every replacement in MOJIBAKE_MAP. Exits zero on success.
        Does NOT re-stage files — the caller (e.g. the pre-commit hook)
        is responsible for `git add` on any autofixed paths.

Used by:
- `.git/hooks/pre-commit` — called in detection-only mode on every
  staged `.md` / `.py` / `.json` / `.toml` / `.txt` file.
- Human operators — invoked manually with `--autofix` on specific
  paths when the hook flags mojibake and the user wants the repair.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Mojibake → UTF-8 replacement table. Keys are what lands in the file
# when UTF-8 bytes get re-encoded through CP-1252; values are the
# intended UTF-8 character. Order matters ONLY when one key is a prefix
# of another — we iterate longest-key-first to avoid partial matches
# eating the correct replacement (e.g. `â€œ` vs `â€`).
MOJIBAKE_MAP: dict[str, str] = {
    # Dashes + bullets
    "â€”": "—",   # em-dash
    "â€“": "–",   # en-dash
    "â€¢": "•",   # bullet
    # Smart quotes
    "â€œ": "“",   # left double quote
    "â€": "”",    # right double quote (must be checked AFTER the longer ones)
    "â€™": "’",   # right single quote
    "â€˜": "‘",   # left single quote
    "â€ž": "„",   # double low-9 quote
    # Common latin-1 punctuation / symbols
    "Â§": "§",    # section sign
    "Â·": "·",    # middle dot
    "Â°": "°",    # degree sign
    "Â®": "®",    # registered
    "Â©": "©",    # copyright
    "Â£": "£",    # pound sign
    "Â¢": "¢",    # cent sign
    "Â¥": "¥",    # yen
    "Â¶": "¶",    # pilcrow
    # Math
    "â‰¤": "≤",  # less-or-equal
    "â‰¥": "≥",  # greater-or-equal
    "â‰ ": "≠",  # not-equal
    "Ã—": "×",    # multiplication
    "Ã·": "÷",    # division
    # Arrows
    "â†’": "→",   # right arrow
    "â†'": "→",   # variant
    "â†‘": "↑",   # up arrow
    # Misc
    "â„¢": "™",   # trademark
    "â€¦": "…",   # horizontal ellipsis
}


# Anything the table does not list: a UTF-8 lead-byte character followed by
# the right number of continuation characters, whose cp1252 bytes decode as
# valid UTF-8 (box drawing, arrows, symbols...). The decode requirement keeps
# real Latin-1 text (``café``) out.
_GENERIC = re.compile("[\u00e0-\u00ef][^\x00-\x7f]{2}|[\u00c2-\u00df][^\x00-\x7f]")


def _cp1252_byte(ch: str) -> int | None:
    try:
        return ch.encode("cp1252")[0]
    except UnicodeEncodeError:
        # The five bytes cp1252 leaves undefined come back as their latin-1
        # code points (``\x90`` in a mangled ``═``).
        return ord(ch) if ord(ch) < 256 else None


def _generic_fix(run: str) -> str | None:
    """The intended character for a mojibake ``run``, or None if it is not one."""
    raw = [_cp1252_byte(c) for c in run]
    if None in raw:
        return None
    try:
        return bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _replacement_order(table: dict[str, str]) -> list[tuple[str, str]]:
    """Yield (key, value) pairs longest-key-first for safe replacement."""
    return sorted(table.items(), key=lambda kv: len(kv[0]), reverse=True)


class Finding:
    __slots__ = ("path", "line", "column", "mojibake", "fix")

    def __init__(
        self, path: Path, line: int, column: int, mojibake: str, fix: str,
    ) -> None:
        self.path = path
        self.line = line
        self.column = column
        self.mojibake = mojibake
        self.fix = fix

    def format(self) -> str:
        return (
            f"{self.path}:{self.line}:{self.column}: "
            f"mojibake {self.mojibake!r} → {self.fix!r}"
        )


def scan_file(path: Path) -> list[Finding]:
    """Return every mojibake occurrence in *path* as a list of Findings."""
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings

    pairs = _replacement_order(MOJIBAKE_MAP)
    for line_no, line in enumerate(text.splitlines(), start=1):
        # Track positions already accounted for by a longer match so a
        # shorter-key prefix (e.g. `â€`) doesn't double-report at the
        # same column when `â€œ` was already matched.
        claimed_cols: set[int] = set()
        for key, fix in pairs:
            col = 0
            while True:
                idx = line.find(key, col)
                if idx < 0:
                    break
                if idx not in claimed_cols:
                    findings.append(
                        Finding(
                            path=path,
                            line=line_no,
                            column=idx + 1,  # 1-indexed for human report
                            mojibake=key,
                            fix=fix,
                        )
                    )
                    # Claim each column the match spans.
                    for c in range(idx, idx + len(key)):
                        claimed_cols.add(c)
                col = idx + len(key)
        for match in _GENERIC.finditer(line):
            fix = _generic_fix(match.group(0))
            if fix is None or match.start() in claimed_cols:
                continue
            findings.append(Finding(
                path=path, line=line_no, column=match.start() + 1,
                mojibake=match.group(0), fix=fix,
            ))
    return findings


def fix_file(path: Path) -> int:
    """Apply every replacement from MOJIBAKE_MAP. Return change count."""
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0

    updated = original
    change_count = 0
    for key, fix in _replacement_order(MOJIBAKE_MAP):
        if key in updated:
            change_count += updated.count(key)
            updated = updated.replace(key, fix)

    def _generic(match: re.Match[str]) -> str:
        nonlocal change_count
        fix = _generic_fix(match.group(0))
        if fix is None:
            return match.group(0)
        change_count += 1
        return fix

    updated = _GENERIC.sub(_generic, updated)

    if change_count:
        path.write_text(updated, encoding="utf-8")
    return change_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect or auto-fix UTF-8 mojibake in text files.",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="Files to scan.")
    parser.add_argument(
        "--autofix",
        action="store_true",
        help="Apply replacements in-place instead of only reporting.",
    )
    args = parser.parse_args(argv)

    if args.autofix:
        total = 0
        for p in args.paths:
            if not p.is_file():
                continue
            changes = fix_file(p)
            if changes:
                print(f"autofixed {changes} occurrence(s) in {p}")
                total += changes
        print(f"autofix complete: {total} replacement(s)")
        return 0

    # Detection mode.
    all_findings: list[Finding] = []
    for p in args.paths:
        if not p.is_file():
            continue
        all_findings.extend(scan_file(p))

    if not all_findings:
        return 0

    for f in all_findings:
        print(f.format(), file=sys.stderr)
    print("", file=sys.stderr)
    print(
        f"Found {len(all_findings)} mojibake occurrence(s). "
        f"Run `python scripts/fix-mojibake.py --autofix <paths>` to repair.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
