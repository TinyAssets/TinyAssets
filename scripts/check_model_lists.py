"""Refuse a malformed public model list. CI moderation for agent-opened PRs.

A user's agent may open a PR adding a model id to ``models/<source-kind>.json`` like
any contributor. This is the mechanical half of the review: valid JSON, a known source
kind, well-formed identifiers, no duplicates, sorted. The judgement half is the lead
reading the PR, and there is deliberately no automatic merge path for these.

Exit 0 clean, 1 with findings. Every finding names the file and the reason.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tinyassets.providers.public_model_lists import (  # noqa: E402
    PublicModelListError,
    lists_directory,
    read_list,
)

#: Source kinds a connection can actually report. A file for anything else is a typo
#: or a guess, and would be silently ignored at runtime -- which is worse than refused.
KNOWN_SOURCE_KINDS = frozenset({"subscription", "local", "http"})


def findings(directory: Path) -> list[str]:
    found: list[str] = []
    if not directory.is_dir():
        return [f"{directory} is missing"]
    for path in sorted(directory.glob("*.json")):
        source_kind = path.stem
        if source_kind not in KNOWN_SOURCE_KINDS:
            found.append(
                f"{path.name}: unknown source kind {source_kind!r} "
                f"(known: {', '.join(sorted(KNOWN_SOURCE_KINDS))})"
            )
            continue
        try:
            listed = read_list(source_kind, directory=directory)
        except PublicModelListError as exc:
            found.append(f"{path.name}: {exc}")
            continue
        if not listed:
            # An empty file is pointless rather than harmful, but it is also almost
            # certainly a mistake in a PR whose purpose was to ADD an id.
            found.append(f"{path.name}: lists no models")
        if source_kind == "subscription":
            from tinyassets.provider_serving_binding import _PROVIDER_SERVICE

            keys = json.loads(path.read_text(encoding="utf-8"))["models"]
            for key in keys:
                if key not in _PROVIDER_SERVICE:
                    found.append(f"{path.name}: unknown subscription source {key!r}")
    return found


def main(argv: list[str]) -> int:
    directory = Path(argv[1]) if len(argv) > 1 else lists_directory()
    problems = findings(directory)
    if problems:
        print("model list check found problems:")
        for problem in problems:
            print(f"  - {problem}")
        print("\nSee models/README.md for the file shape.")
        return 1
    files = sorted(path.name for path in directory.glob("*.json"))
    print(f"model list check clean ({len(files)} file(s): {', '.join(files) or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
