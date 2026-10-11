"""Combine scheduled observations without confusing unavailable coverage with health."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping

PROBES = ("handshake", "tool", "activity", "revert", "wiki", "disk")


def classify_observations(
    statuses: Mapping[str, str], *, revert_observation: str = "", revert_reason: str = "",
) -> dict:
    observations = {}
    for name in PROBES:
        code = statuses.get(name, "")
        state = "green" if code == "0" else "red" if code.isdecimal() else "unknown"
        if name == "disk":
            state = {"0": "green", "80": "warning", "90": "critical"}.get(code, "unknown")
        reason = "observed" if state != "unknown" else "result_unavailable"
        if (
            name == "revert" and code == "5" and revert_observation == "unknown"
            and revert_reason == "legacy_evidence_unavailable"
        ):
            state, reason = "unknown", revert_reason
        observations[name] = {"state": state, "reason": reason, "exit": code}
    red = [name for name in PROBES if observations[name]["state"] == "red"]
    unknown = [name for name in PROBES if observations[name]["state"] == "unknown"]
    return {
        "overall": "red" if red else "unknown" if unknown else "green",
        "status": statuses[red[0]] if red else "unknown" if unknown else "0",
        "page_needed": bool(red),
        "disk_overall": "red" if observations["disk"]["state"] in ("warning", "critical")
        else observations["disk"]["state"],
        "disk_page_needed": observations["disk"]["state"] == "critical",
        "observations": observations,
    }


def main() -> int:
    statuses = {name: os.environ.get(f"{name.upper()}_STATUS", "") for name in PROBES}
    result = classify_observations(
        statuses,
        revert_observation=os.environ.get("REVERT_OBSERVATION", ""),
        revert_reason=os.environ.get("REVERT_REASON", ""),
    )
    message = "\n\n".join(
        f"--- {name} ({result['observations'][name]['state']}, "
        f"exit {statuses[name] or 'unavailable'}) ---\n"
        + os.environ.get(f"{name.upper()}_MSG", "")
        for name in PROBES
    )
    delimiter = "RESULT_" + uuid.uuid4().hex
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"overall={result['overall']}\nstatus={result['status']}\n")
        output.write(f"page_needed={str(result['page_needed']).lower()}\n")
        output.write(f"disk_overall={result['disk_overall']}\n")
        output.write(f"disk_page_needed={str(result['disk_page_needed']).lower()}\n")
        output.write(f"msg<<{delimiter}\n{message}\n{delimiter}\n")
    summary = ["### Scheduled Layer-1 observation", "", f"- Result: {result['overall']}"]
    summary.extend(
        f"- {name}: {item['state']} ({item['reason']})"
        for name, item in result["observations"].items()
    )
    if any(item["state"] == "unknown" for item in result["observations"].values()):
        summary.append(
            "- Monitoring coverage is incomplete. Automatic incident recovery is unavailable "
            "until every required observation is positively green."
        )
    if result["observations"]["revert"]["reason"] == "legacy_evidence_unavailable":
        summary.append(
            "- Required: an authoritative private-free current-engine execution-quality "
            "observation. Coordinator liveness does not supply that proof."
        )
    print("\n".join(summary))
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as output:
            output.write("\n".join(summary) + "\n")
    return 0  # Recording an observation is not itself an uptime verdict.


if __name__ == "__main__":
    raise SystemExit(main())
