"""Disk pressure watchdog — opens a GitHub Issue when /var/lib/docker is full.

Checks disk usage on DISK_WATCH_PATH (default /var/lib/docker). If usage
exceeds DISK_WARN_PCT (default 80%), opens a `disk-pressure` GH Issue so
the host gets an email alert before Docker builds start failing.

Mirrors the GH-issue alarm pattern from scripts/watchdog.py.

Exit codes
----------
0   Disk usage below threshold (or DRY_RUN).
10  Disk usage above threshold — issue opened (or would be in dry-run).
2   GH API error (non-fatal in production — logged, exits 0 to avoid
    masking the disk problem with a secondary auth failure).

Environment variables
---------------------
GITHUB_TOKEN       fine-grained PAT or Actions GITHUB_TOKEN (issues: write)
GITHUB_REPOSITORY  owner/repo  (default: TinyAssets/TinyAssets)
DISK_WARN_PCT      integer alert threshold, default 80
DISK_WATCH_PATH    filesystem path to check, default /var/lib/docker
DRY_RUN            set to 1/true to print plan without opening issues

Stdlib only — no third-party deps.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

try:
    from scripts.daemon_image_retention import Docker, Refusal, pressure, storage_path
except ModuleNotFoundError:
    from daemon_image_retention import Docker, Refusal, pressure, storage_path

GITHUB_API = "https://api.github.com"

_DEFAULT_PATH = "/var/lib/docker"
_DEFAULT_WARN_PCT = 80
_DEFAULT_REPO = "TinyAssets/TinyAssets"
_DISK_PRESSURE_LABEL = "disk-pressure"


def _disk_usage_pct(path: str) -> float:
    """Match storage telemetry: reserved blocks count as unavailable."""
    return pressure(path, disk_fn=shutil.disk_usage)


def _gh_ensure_label(
    token: str,
    repo: str,
    label: str,
    color: str = "e11d48",
    timeout: float = 20.0,
) -> None:
    body = json.dumps({"name": label, "color": color}).encode()
    req = urllib.request.Request(
        f"{GITHUB_API}/repos/{repo}/labels",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "tinyassets-disk-watch/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code != 422:  # 422 = already exists
            pass
    except (urllib.error.URLError, OSError):
        pass


def open_gh_issue(
    token: str,
    repo: str,
    path: str,
    pct: float,
    threshold: int,
    timeout: float = 20.0,
) -> str:
    """Open a disk-pressure GH issue. Returns issue URL."""
    _gh_ensure_label(token, repo, _DISK_PRESSURE_LABEL, timeout=timeout)

    title = f"[disk-pressure] {path} at {pct:.1f}% (threshold {threshold}%)"
    body = (
        f"**Path:** `{path}`\n"
        f"**Usage:** {pct:.1f}%\n"
        f"**Threshold:** {threshold}%\n\n"
        "Check the bounded daemon-image retention result and protected rollback "
        "images; do not broadly prune user or rollback state.\n\n"
        f"_Auto-filed by disk_watch.py running on the Droplet._"
    )
    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "labels": [_DISK_PRESSURE_LABEL],
        }
    ).encode()
    req = urllib.request.Request(
        f"{GITHUB_API}/repos/{repo}/issues",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "tinyassets-disk-watch/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
        return data.get("html_url", "(no url)")


def check(
    path: str = _DEFAULT_PATH,
    threshold: int = _DEFAULT_WARN_PCT,
    repo: str = _DEFAULT_REPO,
    token: str = "",
    dry_run: bool = False,
    *,
    disk_fn=None,  # injection seam: (path) -> float
    issue_fn=None,  # injection seam: (token, repo, path, pct, threshold) -> str
) -> int:
    _disk = disk_fn or _disk_usage_pct
    _issue = issue_fn or open_gh_issue

    try:
        pct = _disk(path)
    except (OSError, Refusal) as exc:
        print(f"[disk-watch] WARN: cannot stat {path!r}: {exc}", file=sys.stderr)
        return 0  # non-fatal — path may not exist on all hosts

    print(f"[disk-watch] {path}: {pct:.1f}% used (threshold {threshold}%)")

    if pct < threshold:
        print("[disk-watch] OK — below threshold")
        return 0

    print(f"[disk-watch] ALERT: {path} at {pct:.1f}% — above {threshold}% threshold")

    if dry_run:
        print(f"[disk-watch] DRY_RUN: would open disk-pressure issue for {path}")
        return 10

    if not token:
        print("[disk-watch] WARN: GITHUB_TOKEN not set — cannot open GH issue", file=sys.stderr)
        return 10

    try:
        url = _issue(token, repo, path, pct, threshold)
        print(f"[disk-watch] issue opened: {url}")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        print(f"[disk-watch] WARN: GH issue failed (non-fatal): {exc}", file=sys.stderr)

    return 10


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    path = os.environ.get("DISK_WATCH_PATH", "")
    if not path:
        try:
            info = Docker(time.monotonic() + 10).json("info", "--format", "{{json .}}")
            path = storage_path(info, os.environ.get("TINYASSETS_IMAGE_RETENTION_STORAGE_PATH", ""))
        except (Refusal, OSError, ValueError, TypeError):
            print("[disk-watch] UNKNOWN: image-store filesystem is not verified", file=sys.stderr)
            return 10  # Visible, but preserve the rotation/retention command chain.
    threshold = int(os.environ.get("DISK_WARN_PCT", str(_DEFAULT_WARN_PCT)))
    repo = os.environ.get("GITHUB_REPOSITORY", _DEFAULT_REPO)
    token = os.environ.get("GITHUB_TOKEN", "")
    dry_run = os.environ.get("DRY_RUN", "").strip() in ("1", "true", "yes")
    return check(path, threshold, repo, token, dry_run)


if __name__ == "__main__":
    sys.exit(main())
