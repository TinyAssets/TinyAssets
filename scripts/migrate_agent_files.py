"""C8 maintenance-window copy of the current agent store into owner files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tinyassets.agent_files import migrate_all_owners, migrate_owner


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="data root of the backed-up installation")
    parser.add_argument("--owner", help="migrate only this owner")
    args = parser.parse_args()
    result = ({args.owner: migrate_owner(args.base, args.owner)} if args.owner
              else migrate_all_owners(args.base))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
