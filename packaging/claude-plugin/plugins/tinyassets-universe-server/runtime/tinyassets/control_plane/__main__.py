"""``python -m tinyassets.control_plane metrics``: the scheduler's metrics as JSON.

Operator read of platform state only (due-vs-fired lag, coalesced count,
decay states, fire outcomes over the last 24 h). Run it inside the daemon
container, where ``TINYASSETS_DATA_DIR`` is set.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tinyassets.control_plane")
    parser.add_argument("command", choices=["metrics"])
    parser.add_argument("--data-dir", default="")
    args = parser.parse_args(argv)
    from tinyassets.control_plane.scheduler import scheduler_metrics
    from tinyassets.storage import data_dir

    base = args.data_dir or str(data_dir())
    json.dump(scheduler_metrics(base), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
