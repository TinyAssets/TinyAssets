"""Read authenticated pulse disk pressure for the existing uptime alarm sink."""

import json
import os
import urllib.error
import urllib.request


def probe(*, opener=urllib.request.urlopen):
    token = os.environ.get("TINYASSETS_WIKI_CANARY_TOKEN", "")
    if not token:
        return 2, "disk health unavailable: canary credential missing"
    request = urllib.request.Request(
        "https://tinyassets.io/mcp/pulse", headers={"Authorization": "Bearer " + token})
    try:
        with opener(request, timeout=15) as response:
            value = json.loads(response.read(65536))["disk_health"]
        status = value["status"]
        pct = value.get("used_pct")
        if status not in ("ok", "warning", "critical") or type(pct) not in (int, float):
            raise ValueError("unknown measurement")
        if not 0 <= pct <= 100:
            raise ValueError("invalid measurement")
        code = 90 if status == "critical" else 80 if status == "warning" else 0
        return code, f"shared disk {status}: {pct:.2f}% used (alert 80%, page 90%)"
    except (OSError, ValueError, KeyError, TypeError):
        return 2, "disk health unavailable: pulse observation failed"


def main():
    code, message = probe()
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"disk_status={code}\ndisk_msg={message}\n")
    print(message)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
