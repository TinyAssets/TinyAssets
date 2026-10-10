"""Authorize rollback only for a reproduced, candidate-only platform failure.

Both images run the same synthetic fixtures without production credentials or
live model/service dependencies. Missing evidence is an alert, never a verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tinyassets.core_capabilities import validate_report  # noqa: E402


def regressions(candidate, previous, live):
    if validate_report(previous) or previous.get("probe_error") or previous.get("error"):
        return []
    observed = {row["capability"] for row in live.get("failures", [])}
    # Missing evidence (including a crashed harness) cannot convict an image.
    measured = {
        row["capability"]
        for row in candidate.get("capabilities", [])
        if row.get("status") == "failed" and row.get("code") != "evidence_missing"
    }
    return sorted(observed & measured)


def compare(candidate_image, previous_image, live, directory, apparmor):
    digest = r"ghcr\.io/tinyassets/tinyassets-daemon@sha256:[a-f0-9]{64}"
    if (
        not re.fullmatch(digest, candidate_image)
        or not re.fullmatch(digest, previous_image)
        or candidate_image == previous_image
        or not live.get("failures")
    ):
        return []
    documents = []
    for name, image in (("candidate", candidate_image), ("previous", previous_image)):
        path = directory / (name + ".json")
        path.unlink(missing_ok=True)
        subprocess.run(["docker", "pull", image], check=True, timeout=180)
        result = subprocess.run(
            [
                sys.executable,
                "scripts/core_capability_image.py",
                "--image",
                image,
                "--apparmor",
                apparmor,
                "--report",
                str(path),
            ],
            timeout=660,
            check=False,
        )
        document = json.loads(path.read_text(encoding="utf-8"))
        # A harness crash returning nonzero alongside a green report is unknown.
        if bool(result.returncode) != bool(validate_report(document)):
            return []
        documents.append(document)
        if name == "candidate" and not validate_report(document):
            return []
    return regressions(*documents, live)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", default="core-capabilities-live.json")
    parser.add_argument("--out", default="core-regression")
    parser.add_argument("--apparmor", default="ta-capability-userns")
    args = parser.parse_args()
    directory = Path(args.out)
    directory.mkdir(exist_ok=True)
    failures = []
    try:
        live = json.loads(Path(args.live).read_text(encoding="utf-8"))
        failures = compare(
            os.environ.get("CANDIDATE_IMAGE", ""),
            os.environ.get("PREVIOUS_IMAGE", ""),
            live,
            directory,
            args.apparmor,
        )
    except Exception as exc:
        print("::warning::Image comparison unavailable: " + type(exc).__name__)
    verdict = {"rollback": bool(failures), "regressions": failures}
    (directory / "verdict.json").write_text(json.dumps(verdict) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("rollback=" + str(bool(failures)).lower() + "\n")
    print(json.dumps(verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
