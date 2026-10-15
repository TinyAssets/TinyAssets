"""Run the core-canary provisioner from a checkout.

The daemon image does not include ``scripts/``. On the host, run
``python3 -m tinyassets.core_canary`` inside the daemon container.
"""

from __future__ import annotations

from tinyassets.core_canary import main

if __name__ == "__main__":
    raise SystemExit(main())
