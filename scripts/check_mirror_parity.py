"""Exit nonzero if Git tracks any retired Claude plugin runtime file."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.invariants.mirror_parity import tracked_mirror  # noqa: E402


def main() -> int:
    paths = tracked_mirror()
    if paths:
        print("hosted plugin must not contain a runtime:", file=sys.stderr)
        for path in paths:
            print(f"  {path}", file=sys.stderr)
        return 1
    print("no tracked plugin runtime files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
