#!/usr/bin/env python3
"""Read-only live preflight: refuse, before the first host mutation, on what only a live host has.

deploy/deploy_host.sh `live-preflight` pipes this to `sudo python3 -` on the
host, right after the current image is captured and before any file under
/etc/tinyassets changes. It inspects and never writes. Each refusal is one
line, ``preflight_refused: check=<name> ...``, on stdout; exit 1 when any
check refused, else ``live_preflight=ok``.

The C6 cutover on 2026-10-21 refused five times on conditions only the real
droplet had. Each is one check here, reusing the rule the deploy itself
applies later so the two cannot disagree:

core_canary       an unprovisioned canary (the resolved gate, waiver included)
                  while the data layout still has a move pending. The same
                  rule as deploy_fail_safe.sh (`core_canary_not_provisioned`),
                  minutes earlier and before any mutation.
disk_low_water    root (or the Docker storage root) used above the retention
                  low-water mark (DISK_AUTOPRUNE_LOW_PCT in the env file,
                  default 75, the value scripts/daemon_image_retention.py
                  grades against). The report names every unreferenced
                  harness-<digest> directory, the leftover that filled the
                  disk on the fifth refused attempt.
box_store         the owner store is not the mounted XFS project-quota store
                  deploy/box-store-check.sh requires, or, with a move pending,
                  it holds anything but lost+found (the migration's own
                  emptiness rule), or, after the move, a stray top-level file
                  that is not an owner volume image.
special_files     a socket, FIFO or device on the data volume outside the
                  directories the runtime regenerates on every start
                  (tinyassets/box_layout_migration.py skips sockets there).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

REGENERATED_PARTS = frozenset({
    ".agent-sessions", ".universe-sidecars", ".runtime", ".consumer_liveness",
    ".workspace-staging",
})
OWNER_IMAGE = re.compile(r"\d+\.img\Z")
DEFAULT_LOW_WATER_PCT = 75.0


def read_env_value(env_file: Path, key: str) -> str | None:
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    value = None
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if line.startswith(key + "="):
            value = line[len(key) + 1:].strip().strip("'\"")
    return value


def docker_volume_root(name: str) -> Path:
    result = subprocess.run(
        ["docker", "volume", "inspect", name, "--format", "{{ .Mountpoint }}"],
        check=True, capture_output=True, text=True, timeout=30)
    root = Path(result.stdout.strip())
    if not root.is_absolute() or not root.is_dir():
        raise RuntimeError(f"data volume {name} has no mountpoint directory")
    return root


def layout_marker(root: Path) -> dict:
    marker = root / ".layout.json"
    try:
        document = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        return {"unreadable": type(error).__name__}
    return document if isinstance(document, dict) else {"unreadable": "not_a_mapping"}


def move_pending(marker: dict) -> bool:
    """True unless the layout is already the committed owner-box shape."""
    return not (marker.get("box_layout") == "owner-box"
                and marker.get("state") in {"stable", "recovering"})


def check_core_canary(provisioned: str, marker: dict) -> list[str]:
    if provisioned == "1" or not move_pending(marker):
        return []
    return [f"check=core_canary_not_provisioned move=box_layout_pending "
            f"layout={marker.get('box_layout', 'absent')}"]


def unreferenced_harnesses(harness_root: Path) -> list[tuple[str, int]]:
    current = harness_root / "harness-current"
    try:
        keep = current.resolve(strict=True)
    except OSError:
        keep = None
    found = []
    for entry in sorted(harness_root.glob("harness-*")):
        if entry.name == "harness-current" or entry.is_symlink() or not entry.is_dir():
            continue
        if keep is not None and entry.resolve() == keep:
            continue
        size = 0
        for base, _dirs, files in os.walk(entry):
            for name in files:
                try:
                    size += os.lstat(os.path.join(base, name)).st_size
                except OSError:
                    pass
        found.append((entry.name, size))
    return found


def check_disk(low_pct: float, harness_root: Path, paths=("/", "/var/lib/docker"),
               disk_fn=shutil.disk_usage) -> list[str]:
    refusals = []
    seen = set()
    for path in paths:
        try:
            device = os.stat(path).st_dev
        except OSError:
            continue
        if device in seen:
            continue
        seen.add(device)
        usage = disk_fn(path)
        used_pct = 100.0 * (usage.total - usage.free) / usage.total if usage.total else 0.0
        if used_pct > low_pct:
            leftovers = unreferenced_harnesses(harness_root)
            detail = " ".join(f"{name}={size // (1024 * 1024)}MiB" for name, size in leftovers)
            refusals.append(
                f"check=disk_low_water path={path} used_pct={used_pct:.1f} "
                f"low_water_pct={low_pct:g} unreferenced_harnesses={len(leftovers)}"
                + (f" [{detail}]" if detail else ""))
    return refusals


def check_box_store(store: Path, marker: dict, store_check: Path) -> list[str]:
    if store_check.is_file():
        result = subprocess.run(["bash", str(store_check), str(store)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            reason = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["unknown"]
            return [f"check=box_store_mount reason={reason[0]}"]
    else:
        return [f"check=box_store_check_missing path={store_check}"]
    try:
        entries = sorted(store.iterdir())
    except OSError as error:
        return [f"check=box_store_unreadable reason={type(error).__name__}"]
    if move_pending(marker):
        stray = [entry.name for entry in entries if entry.name != "lost+found"]
        if stray:
            return [f"check=box_store_not_empty move=box_layout_pending "
                    f"entries={len(stray)} names={','.join(stray[:5])}"]
        return []
    stray = []
    for entry in entries:
        info = entry.lstat()
        if entry.name == "lost+found" or (stat.S_ISDIR(info.st_mode)
                                           and not stat.S_ISLNK(info.st_mode)):
            continue
        if stat.S_ISREG(info.st_mode) and OWNER_IMAGE.fullmatch(entry.name):
            continue
        stray.append(entry.name)
    if stray:
        return [f"check=box_store_stray_files entries={len(stray)} names={','.join(stray[:5])}"]
    return []


def check_special_files(root: Path) -> list[str]:
    stray = []
    root_dev = root.stat().st_dev
    for base, dirs, files, fd in os.fwalk(root, follow_symlinks=False):
        relative = Path(base).relative_to(root)
        if set(relative.parts) & REGENERATED_PARTS:
            dirs[:] = []
            continue
        for name in list(dirs) + files:
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(info.st_mode) and info.st_dev != root_dev:
                dirs.remove(name)
                continue
            if (stat.S_ISSOCK(info.st_mode) or stat.S_ISFIFO(info.st_mode)
                    or stat.S_ISCHR(info.st_mode) or stat.S_ISBLK(info.st_mode)):
                stray.append(str(relative / name))
    if stray:
        return [f"check=special_files count={len(stray)} paths={','.join(stray[:5])}"]
    return []


def run(args) -> list[str]:
    root = args.data if args.data is not None else docker_volume_root(args.data_volume)
    if not root.is_dir():
        return [f"check=data_missing path={root}"]
    marker = layout_marker(root)
    low = DEFAULT_LOW_WATER_PCT
    configured = read_env_value(args.env_file, "DISK_AUTOPRUNE_LOW_PCT")
    if configured:
        try:
            low = float(configured)
        except ValueError:
            pass
    refusals = []
    refusals += check_core_canary(
        "1" if args.waiver else os.environ.get("CORE_CANARY_PROVISIONED", ""), marker)
    refusals += check_disk(low, args.harness_root)
    refusals += check_box_store(args.box_store, marker, args.box_store_check)
    refusals += check_special_files(root)
    return refusals


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, help="data directory (default: Docker volume)")
    parser.add_argument("--data-volume", default="tinyassets-data")
    parser.add_argument("--waiver", action="store_true", help="waive only the canary precondition")
    parser.add_argument("--box-store", type=Path, default=Path("/var/lib/boxhostd/owners"))
    parser.add_argument("--box-store-check", type=Path,
                        default=Path("/opt/tinyassets/boxhostd/box-store-check.sh"))
    parser.add_argument("--harness-root", type=Path, default=Path("/opt/tinyassets/boxhostd"))
    parser.add_argument("--env-file", type=Path, default=Path("/etc/tinyassets/env"))
    args = parser.parse_args(argv)
    refusals = run(args)
    for refusal in refusals:
        print(f"preflight_refused: {refusal}")
        print(f"::error::live preflight: {refusal}; the host is unchanged", file=sys.stderr)
    if refusals:
        return 1
    print("live_preflight=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
