"""P0 outage auto-triage classifier.

Reads the pre-restart diagnostic bundle (docker ps + compose ps +
journalctl tail + df -h + systemctl status) from stdin or --input-file
and emits a structured classification to stdout. The p0-outage-triage
workflow uses the classification to pick the right repair branch.

Classes (narrow-by-design per task #11 spec, each detected by a single
non-overlapping regex match):

  env_unreadable      /etc/tinyassets/env perms regressed (Task #3 marker).
                      Repair: chown root:tinyassets + chmod 640.
  oom                 OOM killer triggered on the daemon container.
                      Repair: compose restart. Bump-once memory cap
                      is documented but NOT auto-applied to prevent
                      infinite ratchet.
  disk_full           /var/lib/docker or / above 90%.
                      Repair: docker system prune -af + journalctl vacuum.
  image_pull_failure  Compose up failed pulling the pinned SHA.
                      Repair: fall back to :latest + retry.
  tunnel_token        cloudflared rejects the tunnel token as unauthorized.
                      Repair: NOT auto-fixable — classifier flags it as
                      manual-only so the workflow opens a distinct issue
                      and pages priority=2.
  watchdog_hotloop    systemd reports >20 restarts in <5min on the
                      tinyassets-daemon unit. Repair: stop → sleep 60 →
                      start, to let the start-limit counter reset.
  unknown             None of the above matched. Repair path: generic
                      compose restart (the existing workflow behavior).

Exit codes
----------
  0   classification emitted on stdout (JSON). May be `unknown`.
  2   input missing / unreadable.

Usage
-----
  # From workflow:
  python scripts/triage_classify.py --input-file /tmp/diag.txt

  # From stdin:
  printf '%s' "$DIAG" | python scripts/triage_classify.py

Output format (single-line JSON so shell parsers can consume it):
  {"class": "oom", "auto_repairable": true, "manual_only": false,
   "evidence": "kernel: Out of memory: ..."}
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys

# --- detection regexes -----------------------------------------------------
# Each class has ONE anchor pattern. The first match wins in priority order,
# so more-specific-first ordering matters: ENV-UNREADABLE is checked before
# generic compose-up failures because both can co-occur and the env issue
# is the root cause.


class TriageClass:
    """Enum-ish namespace of the class strings emitted on stdout."""

    ENV_UNREADABLE = "env_unreadable"
    OOM = "oom"
    DISK_FULL = "disk_full"
    IMAGE_PULL_FAILURE = "image_pull_failure"
    TUNNEL_TOKEN = "tunnel_token"
    WATCHDOG_HOTLOOP = "watchdog_hotloop"
    PROVIDER_EXHAUSTION = "provider_exhaustion"
    UNKNOWN = "unknown"


# Priority-ordered detectors. First match wins, except provider_exhaustion,
# which needs timestamp-window logic and is handled explicitly in classify().
# (class_name, compiled_regex, auto_repairable, manual_only, description)
#
# Spec ordering (docs/architecture.md
# §Q6): env_unreadable > tunnel_token > provider_exhaustion > disk_full
# > oom > image_pull_failure > watchdog_hotloop > unknown. Rationale:
# cause-addressing repairs outrank symptom-addressing repairs. The
# 2026-04-23 P0 specifically exposed this: disk-prune fired three times
# on the symptom while the generator (sustained revert-loop) kept
# producing the pressure. provider_exhaustion PRIORITY over disk_full
# breaks that cycle — halt the generator, then let disk prune naturally.
_DETECTORS: list[tuple[str, re.Pattern, bool, bool, str]] = [
    # 1. ENV-UNREADABLE (Task #3 class) — most specific, check first.
    (
        TriageClass.ENV_UNREADABLE,
        re.compile(
            r"(?m)^(?!.*ExecStartPre=)"
            r"(?!.*\becho\s+['\"]?ENV-UNREADABLE:)"
            r".*ENV-UNREADABLE:"
        ),
        True,
        False,
        "systemd/entrypoint/sed-site emitted the canonical marker",
    ),
    # 2. Tunnel token expired — cloudflared emits these on auth reject.
    (
        TriageClass.TUNNEL_TOKEN,
        re.compile(
            r"(?:UnauthorizedError|authentication failed|"
            r"Failed to get tunnel|tunnel token|Unauthorized: Invalid tunnel secret)",
            re.IGNORECASE,
        ),
        False,
        True,
        "cloudflared rejected tunnel token — manual rotation required",
    ),
    # 3. Disk full — df output showing >=90% usage on / or /data/docker.
    (
        TriageClass.DISK_FULL,
        re.compile(
            r"\b(9[0-9]|100)%\s+(?:/|/data|/var/lib/docker|/var)(?:\s|$)",
        ),
        True,
        False,
        "df reports >=90% usage on / or /var/lib/docker or /data",
    ),
    # 4. OOM — kernel OOM killer or compose "killed as a result of limit".
    (
        TriageClass.OOM,
        re.compile(
            r"(?:Out of memory(?::| - )|"
            r"oom-killer|"
            r"killed process \d+ \(|"
            r"OOMKilled|"
            r"Memory cgroup out of memory)",
            re.IGNORECASE,
        ),
        True,
        False,
        "kernel OOM-killer or container OOMKilled event",
    ),
    # 5. Image pull failure — compose-up reports manifest-not-found or
    #    pull backoff on the pinned tag.
    (
        TriageClass.IMAGE_PULL_FAILURE,
        re.compile(
            r"(?:manifest (?:for|unknown|not found)|"
            r"pull access denied|"
            r"ImagePullBackOff|"
            r"Error response from daemon: (?:manifest|pull|Head)|"
            r"failed to (?:pull|resolve) image)",
            re.IGNORECASE,
        ),
        True,
        False,
        "compose up failed pulling pinned image; fall back to :latest",
    ),
    # 6. Watchdog hot-loop — systemd start-limit hit OR explicit restart
    #    counter >20 visible in `systemctl status`.
    (
        TriageClass.WATCHDOG_HOTLOOP,
        re.compile(
            r"(?:start request repeated too quickly|"
            r"start-limit-hit|"
            r"Failed with result 'start-limit-hit'|"
            r"Main PID: \d+ \(code=exited, status=\d+/\w+\)[\s\S]{0,200}?"
            r"Active: (?:activating|failed).*\(Result: start-limit)",
            re.IGNORECASE,
        ),
        True,
        False,
        "systemd start-limit-hit on tinyassets-daemon; stop + sleep + start",
    ),
]

_PROVIDER_EXHAUSTION_DESCRIPTION = (
    "daemon alive but revert-looping — pause worker + page host priority=2"
)
_PROVIDER_CRITICAL_PATTERN = re.compile(
    r"CRITICAL revert-loop:\s*(?P<count>\d+)\s+REVERTs?\s+in\s+last\s+"
    r"(?P<window>\d+)min",
    re.IGNORECASE,
)
_PROVIDER_REVERT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"Commit:.*score\s+\d+\.\d+\s*--\s*REVERT", re.IGNORECASE),
    re.compile(r"Commit:\s*reverting.*draft provider failed", re.IGNORECASE),
    re.compile(r"score\s+0\.0{1,2}\s*--\s*REVERT", re.IGNORECASE),
)
_TIMESTAMP_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(
        r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
        r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)"
    ),
    re.compile(r"\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"),
)
_PROVIDER_WINDOW_MIN = 20
_PROVIDER_REVERT_THRESHOLD = 5


def _parse_timestamp(raw_line: str) -> dt.datetime | None:
    for pattern in _TIMESTAMP_PATTERNS:
        match = pattern.search(raw_line)
        if not match:
            continue
        raw = match.group("ts")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        if "T" not in raw:
            raw = raw.replace(" ", "T") + "+00:00"
        try:
            parsed = dt.datetime.fromisoformat(raw)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    return None


def _line_is_provider_revert(line: str) -> bool:
    return any(pattern.search(line) for pattern in _PROVIDER_REVERT_PATTERNS)


def _provider_exhaustion_evidence(
    diag: str,
    *,
    now: dt.datetime,
) -> str | None:
    explicit = _PROVIDER_CRITICAL_PATTERN.search(diag)
    if explicit:
        return explicit.group(0)

    cutoff = now - dt.timedelta(minutes=_PROVIDER_WINDOW_MIN)
    recent_reverts: list[str] = []
    for line in diag.splitlines():
        if not _line_is_provider_revert(line):
            continue
        timestamp = _parse_timestamp(line)
        if timestamp is None or timestamp < cutoff:
            continue
        recent_reverts.append(line.strip())
        if len(recent_reverts) >= _PROVIDER_REVERT_THRESHOLD:
            return (
                f"{len(recent_reverts)} REVERTs in last "
                f"{_PROVIDER_WINDOW_MIN}min; latest={recent_reverts[-1]}"
            )

    return None


def classify(diag: str, *, now: dt.datetime | None = None) -> dict:
    """Return classification dict for a diag bundle.

    Contract:
      {
        "class": <class string>,
        "auto_repairable": <bool>,
        "manual_only": <bool>,
        "evidence": <str, first-match excerpt (≤200 chars) or empty>,
        "description": <str, human-readable class description>
      }
    """
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)

    for class_name, pattern, auto_repairable, manual_only, description in _DETECTORS[:2]:
        match = pattern.search(diag)
        if match:
            # Extract a small window around the match for log surfacing.
            start = max(0, match.start() - 40)
            end = min(len(diag), match.end() + 40)
            evidence = diag[start:end].replace("\n", " ").strip()
            if len(evidence) > 200:
                evidence = evidence[:200] + "…"
            return {
                "class": class_name,
                "auto_repairable": auto_repairable,
                "manual_only": manual_only,
                "evidence": evidence,
                "description": description,
            }

    provider_evidence = _provider_exhaustion_evidence(diag, now=now)
    if provider_evidence:
        return {
            "class": TriageClass.PROVIDER_EXHAUSTION,
            "auto_repairable": True,
            "manual_only": False,
            "evidence": provider_evidence[:200],
            "description": _PROVIDER_EXHAUSTION_DESCRIPTION,
        }

    for class_name, pattern, auto_repairable, manual_only, description in _DETECTORS[2:]:
        match = pattern.search(diag)
        if match:
            # Extract a small window around the match for log surfacing.
            start = max(0, match.start() - 40)
            end = min(len(diag), match.end() + 40)
            evidence = diag[start:end].replace("\n", " ").strip()
            if len(evidence) > 200:
                evidence = evidence[:200] + "…"
            return {
                "class": class_name,
                "auto_repairable": auto_repairable,
                "manual_only": manual_only,
                "evidence": evidence,
                "description": description,
            }

    return {
        "class": TriageClass.UNKNOWN,
        "auto_repairable": False,
        "manual_only": False,
        "evidence": "",
        "description": "no known class matched — falling back to compose restart",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Classify a P0 outage diag bundle for auto-triage.",
    )
    ap.add_argument(
        "--input-file",
        help="Read diag from this file. Default: stdin.",
    )
    args = ap.parse_args(argv)

    if args.input_file:
        try:
            with open(args.input_file, encoding="utf-8", errors="replace") as fp:
                diag = fp.read()
        except OSError as exc:
            print(f"[triage-classify] cannot read {args.input_file}: {exc}",
                  file=sys.stderr)
            return 2
    else:
        diag = sys.stdin.read()

    result = classify(diag)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
