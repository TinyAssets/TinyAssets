"""Run XCTest privately; export only named review screenshots and safe status.

The xctestrun and xcresult can contain environment values and XCTest typing logs.
Neither belongs in a workflow artifact or Device Farm's collected log directory.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

CREDENTIALS = ("APP_STORE_REVIEW_USERNAME", "APP_STORE_REVIEW_PASSWORD")
SHOTS = {
    "01-signed-out",
    "02-signed-in",
    "03-universe",
    "04-reply",
    "04-persisted",
    "05-attached",
    "05-grounded-answer",
    "06-account-privacy",
}


def inject_environment(document: dict, credentials: dict[str, str]) -> int:
    """Support both Xcode's v1 and test-plan v2 xctestrun formats."""
    targets = []
    if "TestConfigurations" in document:
        for configuration in document["TestConfigurations"]:
            targets.extend(configuration.get("TestTargets", []))
    else:
        targets = [
            value
            for value in document.values()
            if isinstance(value, dict) and "TestBundlePath" in value
        ]
    for target in targets:
        target.setdefault("EnvironmentVariables", {}).update(credentials)
    if len(targets) != 1:
        raise ValueError("Expected exactly one review test target")
    return len(targets)


def export_shots(result: Path, output: Path, private: Path) -> set[str]:
    exported = private / "attachments"
    subprocess.run(
        [
            "xcrun",
            "xcresulttool",
            "export",
            "attachments",
            "--path",
            str(result),
            "--output-path",
            str(exported),
        ],
        check=True,
        capture_output=True,
    )
    found: set[str] = set()

    def visit(item):
        if isinstance(item, list):
            for value in item:
                visit(value)
        elif isinstance(item, dict):
            name = item.get("suggestedHumanReadableName", "")
            filename = item.get("exportedFileName", "")
            for shot in SHOTS:
                if name.startswith(f"review-{shot}") and filename:
                    source = (exported / filename).resolve()
                    if source.is_relative_to(exported.resolve()) and source.suffix == ".png":
                        shutil.copyfile(source, output / f"review-{shot}.png")
                        found.add(shot)
            for value in item.values():
                visit(value)

    visit(json.loads((exported / "manifest.json").read_text()))
    return found


def run_test(source: Path, destination: str, output: Path, launch_only: bool = False) -> int:
    credentials = {name: os.environ.get(name, "") for name in CREDENTIALS}
    if launch_only and not destination.startswith("platform=iOS Simulator,"):
        raise ValueError("Launch-only smoke is Simulator-only")
    if not launch_only and not all(credentials.values()):
        raise ValueError("App Review credentials are missing")
    expected_shots = {"01-signed-out"} if launch_only else SHOTS
    test = "testSignedOutLaunch" if launch_only else "testGuideline21Journey"
    output.mkdir(parents=True, exist_ok=True)
    # Keep the copy beside the original: __TESTROOT__ paths must not change.
    private_run = source.with_name("review-private.xctestrun")
    document = plistlib.loads(source.read_bytes())
    inject_environment(document, credentials)
    result_code = 1
    try:
        with private_run.open("xb") as stream:
            os.chmod(private_run, 0o600)
            plistlib.dump(document, stream)
        with tempfile.TemporaryDirectory(prefix="review-private-") as temp:
            private = Path(temp)
            result = private / "Review.xcresult"
            env = {key: value for key, value in os.environ.items() if key not in CREDENTIALS}
            with (private / "xcode.log").open("w") as log:
                process = subprocess.run(
                    [
                        "xcodebuild",
                        "test-without-building",
                        "-xctestrun",
                        str(private_run),
                        "-destination",
                        destination,
                        "-resultBundlePath",
                        str(result),
                        "-parallel-testing-enabled",
                        "NO",
                        "-maximum-test-execution-time-allowance",
                        "1500",
                        f"-only-testing:ReviewUITests/ReviewVideoTests/{test}",
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=1650,
                )
            found = export_shots(result, output, private) if result.exists() else set()
            result_code = 0 if process.returncode == 0 and found == expected_shots else 1
            # Only assertion lines, with credentials redacted, are useful publicly.
            errors = []
            for line in (private / "xcode.log").read_text(errors="replace").splitlines():
                if re.search(r"ReviewVideoTests.swift:\d+: error:|xcodebuild: error:", line):
                    for value in credentials.values():
                        if value:
                            line = line.replace(value, "[REDACTED]")
                    errors.append(line)
            summary = {
                "passed": result_code == 0,
                "scope": "signed-out launch only" if launch_only else "six-step review journey",
                "screenshots": sorted(found),
                "missing": sorted(expected_shots - found),
                "assertions": errors,
            }
            (output / "test-result.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(json.dumps(summary))
    finally:
        private_run.unlink(missing_ok=True)
    return result_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xctestrun", required=True, type=Path)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--launch-only", action="store_true")
    args = parser.parse_args()
    try:
        return run_test(args.xctestrun, args.destination, args.output, args.launch_only)
    except Exception as exc:
        # A subprocess/SDK exception can carry commands, URLs or credentials.
        print(f"::error::Review test harness failed ({type(exc).__name__}); no raw logs exported")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
