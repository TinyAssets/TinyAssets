#!/usr/bin/env python3
"""C10: grow the mounted box store, with a bounded DigitalOcean resize."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

GIB = 1024**3


def private_file(path, *, secret=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        forbidden = 0o077 if secret else 0o022
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & forbidden:
            raise ValueError("root-owned protected configuration required")
        return stream.read()


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=60).stdout.strip()


def api(token, path, body=None):
    request = urllib.request.Request(
        "https://api.digitalocean.com/v2/" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    # Never print a request, response body or exception carrying credentials.
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except Exception:
        raise RuntimeError("DigitalOcean volume API failed") from None


def grow(config, mount):
    values = dict(item.split("=", 1) for item in shlex.split(private_file(config), comments=True))
    device = values["BOX_STORE_DEVICE"]
    volume_id = values["BOX_STORE_VOLUME_ID"]
    region = values["BOX_STORE_REGION"]
    droplet = int(values["BOX_STORE_DROPLET_ID"])
    maximum = int(values.get("BOX_STORE_MAX_GIB", "100"))
    step = int(values.get("BOX_STORE_STEP_GIB", "25"))
    threshold = int(values.get("BOX_STORE_FREE_PERCENT", "20"))
    if not (0 < maximum <= 100 and step > 0 and 0 < threshold < 100):
        raise ValueError("invalid growth limits; maximum cannot exceed 100 GiB")
    if not re.fullmatch(r"[a-f0-9-]{36}", volume_id):
        raise ValueError("volume ID required")
    if not stat.S_ISBLK(os.stat(device).st_mode):
        raise ValueError("growth requires the pinned block device")
    mounted = json.loads(command("findmnt", "--json", "--mountpoint", mount,
                                 "--output", "SOURCE,FSTYPE"))["filesystems"]
    if (len(mounted) != 1 or mounted[0]["fstype"] != "xfs"
            or not os.path.samefile(mounted[0]["source"], device)):
        raise ValueError("pinned device must be mounted as the XFS box store")
    info = command("xfs_info", mount)
    if not re.search(r"\breflink=1\b", info):
        raise ValueError("box store requires reflink=1")
    size = int(command("blockdev", "--getsize64", device))
    if size > maximum * GIB:
        raise ValueError("box store exceeds the configured spending cap")
    # A previous API resize may have completed before this process stopped.
    geometry = re.search(r"data\s*=\s*bsize=(\d+)\s+blocks=(\d+)", info)
    if geometry is None:
        raise ValueError("XFS geometry unavailable")
    if size - int(geometry[1]) * int(geometry[2]) >= GIB:
        command("xfs_growfs", mount)
    space = os.statvfs(mount)
    if space.f_bavail * 100 >= space.f_blocks * threshold:
        print("box store free space above threshold")
        return
    target = (size + GIB - 1) // GIB + step
    if target > maximum:
        raise ValueError("box store growth stopped at configured spending cap")
    token = private_file(values["BOX_STORE_TOKEN_FILE"], secret=True).strip()
    if not token:
        raise ValueError("volume resize credential missing")
    volume = api(token, f"volumes/{volume_id}")["volume"]
    expected_device = "/dev/disk/by-id/scsi-0DO_Volume_" + volume["name"]
    if (volume["region"]["slug"] != region or volume["droplet_ids"] != [droplet]
            or not os.path.samefile(device, expected_device)
            or volume["size_gigabytes"] != size // GIB):
        raise ValueError("attached volume does not match pinned store")
    api(token, f"volumes/{volume_id}/actions",
        {"type": "resize", "size_gigabytes": target, "region": region})
    deadline = time.monotonic() + 300
    while int(command("blockdev", "--getsize64", device)) < target * GIB:
        if time.monotonic() >= deadline:
            raise TimeoutError("resized block device did not become available")
        time.sleep(2)
    command("xfs_growfs", mount)
    print(f"box store grown online to {target} GiB")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/tinyassets/box-store.conf")
    parser.add_argument("--mount", default="/var/lib/boxhostd/owners")
    parser.add_argument("--lock", default="/run/boxhostd/box-store-grow.lock")
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise PermissionError("box store growth requires root")
        with Path(args.lock).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            grow(args.config, args.mount)
    except Exception as exc:
        # Types only: even a subprocess or malformed API reply cannot echo a token.
        message = (f"ALERT: box store growth failed ({type(exc).__name__}); "
                   "inspect capacity/configuration")
        print(message, file=sys.stderr)
        subprocess.run(["logger", "-p", "daemon.crit", "-t", "box-store-grow", message],
                       check=False)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
