#!/usr/bin/env python3
"""Create and attach the production box volume without exposing API responses."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from urllib.parse import urlencode

from do_api_request import api_request

API = "https://api.digitalocean.com/v2"
DROPLET_ID = 565953121
DROPLET_NAME = "workflow-daemon-1"
VOLUME_NAME = "tinyassets-box-store"
SIZE_GIB = 20


def ensure_volume(
    token: str,
    *,
    request: Callable = api_request,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, int, str]:
    def call(method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        return json.loads(request(method=method, url=API + path, token=token, data=data))

    droplet = call("GET", f"/droplets/{DROPLET_ID}")["droplet"]
    if droplet["id"] != DROPLET_ID or droplet["name"] != DROPLET_NAME:
        raise ValueError("production droplet identity mismatch")
    region = droplet["region"]["slug"]
    query = urlencode({"name": VOLUME_NAME, "region": region})
    volumes = call("GET", f"/volumes?{query}")["volumes"]
    if len(volumes) > 1:
        raise ValueError("ambiguous box volume")
    if volumes:
        volume = volumes[0]
    else:
        volume = call("POST", "/volumes", {
            "name": VOLUME_NAME,
            "region": region,
            "size_gigabytes": SIZE_GIB,
        })["volume"]
    if (volume["name"] != VOLUME_NAME
            or volume["region"]["slug"] != region
            or volume["size_gigabytes"] != SIZE_GIB):
        raise ValueError("box volume identity or size mismatch")
    volume_id = volume["id"]
    attached = volume["droplet_ids"]
    if attached and attached != [DROPLET_ID]:
        raise ValueError("box volume is attached to another droplet")
    if not attached:
        action = call("POST", f"/volumes/{volume_id}/actions", {
            "type": "attach", "droplet_id": DROPLET_ID, "region": region,
        })["action"]
        for _ in range(60):
            status = call("GET", f"/volumes/{volume_id}/actions/{action['id']}")["action"]["status"]
            if status == "completed":
                break
            if status == "errored":
                raise RuntimeError("box volume attach failed")
            sleep(5)
        else:
            raise TimeoutError("box volume attach timed out")
    for _ in range(12):
        volume = call("GET", f"/volumes/{volume_id}")["volume"]
        if volume["droplet_ids"] == [DROPLET_ID]:
            return volume_id, SIZE_GIB, "attached"
        sleep(5)
    raise TimeoutError("box volume attachment not visible")


def main() -> int:
    try:
        volume_id, size, state = ensure_volume(os.environ["DO_TOKEN"])
    except Exception:
        # API diagnostics can contain account data. Keep public Actions logs bounded.
        print("::error::box-store volume operation failed")
        return 1
    print(f"volume_id={volume_id} size_gib={size} state={state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
