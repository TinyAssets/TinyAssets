"""Append a quick idea capture entry to ideas/INBOX.md."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DEFAULT_INBOX = """# Idea Inbox

Quick capture surface for loose ideas, user nudges, possible features, and
half-formed experiments.

## Rules

- Capture first, refine later.
- Keep one idea per entry.
- If an idea becomes work, a design note, or a plan, add the destination in
  `Links` instead of deleting the capture history.
- Merge duplicates during triage in `ideas/PIPELINE.md`, not during capture.

## Inbox

"""


@dataclass(frozen=True)
class CaptureConfig:
    summary: str
    source: str
    owner: str
    next_step: str
    links: str
    root: Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append an idea to ideas/INBOX.md")
    parser.add_argument("summary", help="Short idea summary")
    parser.add_argument("--source", default="user-chat", help="Origin of the idea")
    parser.add_argument("--owner", default="unassigned", help="Current owner or triage target")
    parser.add_argument(
        "--next-step",
        default=(
            "triage and choose whether this belongs in "
            "an ADR, a design note, an openspec change, or an exec plan"
        ),
        help="Immediate next action for the captured idea",
    )
    parser.add_argument("--links", default="-", help="Related file paths or note links")
    parser.add_argument("--root", default=".", help="Repo root that contains ideas/INBOX.md")
    parser.add_argument("--dry-run", action="store_true", help="Print the entry without writing it")
    return parser


def build_entry(cfg: CaptureConfig) -> str:
    today = date.today().isoformat()
    return (
        f"- [{today}] (source: {cfg.source}, owner: {cfg.owner}, status: captured) {cfg.summary}\n"
        f"  Next: {cfg.next_step}\n"
        f"  Links: {cfg.links}\n"
    )


def inbox_path(root: Path) -> Path:
    return root / "ideas" / "INBOX.md"


def ensure_inbox(root: Path) -> Path:
    path = inbox_path(root)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_INBOX, encoding="utf-8")
    return path


def append_entry(cfg: CaptureConfig, *, dry_run: bool = False) -> tuple[Path, str]:
    path = inbox_path(cfg.root)
    entry = build_entry(cfg)
    if dry_run:
        return path, entry
    path = ensure_inbox(cfg.root)
    text = path.read_text(encoding="utf-8")
    if "## Inbox" not in text:
        text = text.rstrip() + "\n\n## Inbox\n\n"
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + entry, encoding="utf-8")
    return path, entry


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = CaptureConfig(
        summary=" ".join(args.summary.split()),
        source=args.source.strip() or "user-chat",
        owner=args.owner.strip() or "unassigned",
        next_step=" ".join(args.next_step.split()),
        links=args.links.strip() or "-",
        root=Path(args.root).resolve(),
    )
    path, entry = append_entry(cfg, dry_run=args.dry_run)
    if args.dry_run:
        print(f"Would append to {path}:")
    else:
        print(f"Appended to {path}:")
    print(entry.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
