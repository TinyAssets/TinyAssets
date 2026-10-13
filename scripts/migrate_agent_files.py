"""Rehearse the C8 agent-file cutover on a restored backup::

    python scripts/migrate_agent_files.py --base <data-root>

The daemon runs the same migration itself at startup, once, before it serves
(``tinyassets/agent_file_migration.py``); this entry point exists so the
founder can watch it land on a copy first. Prints ``{universe_id: [agents/<name>,
...]}``; a second run prints ``{}`` because the ledger row ends it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tinyassets.agent_file_migration import migrate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="data root of the restored backup")
    args = parser.parse_args()
    print(json.dumps(migrate(args.base), sort_keys=True))


if __name__ == "__main__":
    main()
