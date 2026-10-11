"""Canary-only shared filesystem pressure, without host paths or owner data."""

import math
import shutil


def snapshot(paths=("/", "/data"), *, disk_fn=shutil.disk_usage):
    try:
        readings = [disk_fn(path) for path in paths]
        if not readings or any(
            not all(math.isfinite(n) for n in (row.total, row.free))
            or row.total <= 0 or not 0 <= row.free <= row.total
            for row in readings
        ):
            raise ValueError("invalid measurement")
        pct = max(100 * (row.total - row.free) / row.total for row in readings)
        return {"status": "critical" if pct >= 90 else "warning" if pct >= 80 else "ok",
                "used_pct": round(pct, 2), "free_bytes": min(row.free for row in readings)}
    except (OSError, ValueError):
        return {"status": "unknown"}
