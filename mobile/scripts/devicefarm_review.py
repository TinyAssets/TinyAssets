"""One bounded physical-iPhone review run; dry-run never constructs an AWS client."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from review_xctest import CREDENTIALS, SHOTS

REGION = "us-west-2"
PROJECT = re.compile(r"arn:aws:devicefarm:us-west-2:\d{12}:project:[\w-]+$")


def select_device(devices: list[dict]) -> dict:
    candidates = []
    for device in devices:
        model = re.search(r"iPhone\s+(\d+)", device.get("name", ""))
        if (
            device.get("platform") == "IOS"
            and device.get("fleetType") == "PUBLIC"
            and model
            and int(model[1]) >= 14
            and device.get("availability") in {"AVAILABLE", "HIGHLY_AVAILABLE"}
            and re.fullmatch(r"\d+(\.\d+)*", device.get("os", ""))
        ):
            candidates.append(device)
    if not candidates:
        raise ValueError("No available public iPhone 14 or newer")
    return max(candidates, key=lambda d: (tuple(map(int, d["os"].split("."))), d["name"]))


def pages(client, operation: str, key: str, **kwargs):
    while True:
        response = getattr(client, operation)(**kwargs)
        yield from response.get(key, [])
        if not response.get("nextToken"):
            break
        kwargs["nextToken"] = response["nextToken"]


def upload(client, project: str, path: Path, kind: str, created: list[str]) -> str:
    item = client.create_upload(projectArn=project, name=path.name, type=kind)["upload"]
    created.append(item["arn"])
    request = urllib.request.Request(item["url"], data=path.read_bytes(), method="PUT")
    with urllib.request.urlopen(request, timeout=180) as response:
        if response.status != 200:
            raise RuntimeError("Upload transfer failed")
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        state = client.get_upload(arn=item["arn"])["upload"]["status"]
        if state == "SUCCEEDED":
            return item["arn"]
        if state == "FAILED":
            raise RuntimeError(f"Device Farm rejected {kind}")
        time.sleep(10)
    raise TimeoutError("Device Farm upload validation timed out")


def validate_packages(ipa_dir: Path, tests: Path) -> Path:
    ipas = list(ipa_dir.glob("*.ipa"))
    if len(ipas) != 1:
        raise ValueError("Expected exactly one signed release IPA")
    ipa = ipas[0]
    manifest = json.loads((ipa_dir / "release-manifest.json").read_text())
    if manifest["bundle_id"] != "io.tinyassets.app" or manifest["file"] != ipa.name:
        raise ValueError("Wrong app release")
    if os.environ.get("GITHUB_SHA") and manifest["commit"] != os.environ["GITHUB_SHA"]:
        raise ValueError("Signed release is from a different commit")
    expected = (ipa_dir / "SHA256SUMS").read_text().split()[0]
    if hashlib.sha256(ipa.read_bytes()).hexdigest() != expected:
        raise ValueError("Signed IPA checksum mismatch")
    with zipfile.ZipFile(ipa) as archive:
        if not any(
            name.startswith("Payload/") and name.endswith(".app/Info.plist")
            for name in archive.namelist()
        ):
            raise ValueError("Invalid app IPA")
    with zipfile.ZipFile(tests) as archive:
        names = archive.namelist()
        if "review_xctest.py" not in names or not any(
            name.endswith(".xctest/ReviewUITests") for name in names
        ):
            raise ValueError("XCUITest runner package missing")
        if any(name.endswith(".xctestrun") for name in names):
            raise ValueError("Private runtime configuration must not be packaged")
    return ipa


def cleanup(client, run: str | None, uploads: list[str], output: Path) -> None:
    """A stopping run cannot be deleted. Attempt every cleanup even if one fails."""
    failures = []
    if run:
        try:
            state = client.get_run(arn=run)["run"]["status"]
            if state != "COMPLETED":
                client.stop_run(arn=run)
                deadline = time.monotonic() + 180
                while state != "COMPLETED" and time.monotonic() < deadline:
                    time.sleep(5)
                    state = client.get_run(arn=run)["run"]["status"]
            if state != "COMPLETED":
                raise TimeoutError("Run is still stopping")
            client.delete_run(arn=run)
        except Exception:
            failures.append({"operation": "delete_run", "arn": run})
    for arn in reversed(uploads):
        try:
            client.delete_upload(arn=arn)
        except Exception:
            failures.append({"operation": "delete_upload", "arn": arn})
    if failures:
        (output / "cleanup-required.json").write_text(json.dumps(failures, indent=2) + "\n")
        print("::error::AWS cleanup incomplete; see encrypted cleanup-required.json")
        raise RuntimeError("AWS cleanup incomplete")


def collect(client, run: str, output: Path) -> tuple[list[Path], bool]:
    """List at run/job/suite/test scope; collect only video and allowlisted evidence."""
    scopes = [run]
    for job in pages(client, "list_jobs", "jobs", arn=run):
        scopes.append(job["arn"])
        for suite in pages(client, "list_suites", "suites", arn=job["arn"]):
            scopes.append(suite["arn"])
            scopes.extend(
                test["arn"] for test in pages(client, "list_tests", "tests", arn=suite["arn"])
            )
    seen = set()
    videos = []
    passed = False
    with tempfile.TemporaryDirectory(prefix="devicefarm-artifacts-") as temp:
        for scope in scopes:
            for artifact in pages(client, "list_artifacts", "artifacts", arn=scope, type="FILE"):
                if artifact["arn"] in seen:
                    continue
                seen.add(artifact["arn"])
                kind = artifact.get("type")
                if kind not in {"VIDEO", "CUSTOMER_ARTIFACT"}:
                    continue
                local = Path(temp) / str(len(seen))
                with (
                    urllib.request.urlopen(artifact["url"], timeout=180) as source,
                    local.open("wb") as target,
                ):
                    shutil.copyfileobj(source, target)
                if kind == "VIDEO":
                    dest = output / f"physical-iphone-{len(videos) + 1}.mp4"
                    shutil.copyfile(local, dest)
                    videos.append(dest)
                elif zipfile.is_zipfile(local):
                    # Never extract arbitrary paths, logs, environment or xcresult.
                    with zipfile.ZipFile(local) as archive:
                        for item in archive.infolist():
                            name = Path(item.filename).name
                            if name in {f"review-{shot}.png" for shot in SHOTS}:
                                (output / name).write_bytes(archive.read(item))
                            elif name == "test-result.json":
                                result = json.loads(archive.read(item))
                                passed = (
                                    result.get("passed") is True
                                    and set(result.get("screenshots", [])) == SHOTS
                                )
                                # Assertion text may reflect a UI value. Keep AWS artifacts minimal.
                                (output / name).write_text(
                                    json.dumps(
                                        {
                                            "passed": passed,
                                            "screenshots": result.get("screenshots", []),
                                        },
                                        indent=2,
                                    )
                                    + "\n"
                                )
    return videos, passed


def execute(ipa: Path, tests: Path, output: Path) -> None:
    project = os.environ.get("AWS_DEVICEFARM_PROJECT_ARN", "")
    required = (*CREDENTIALS, "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    if not PROJECT.fullmatch(project) or not all(os.environ.get(key) for key in required):
        raise ValueError("Missing AWS/reviewer secrets or invalid us-west-2 project ARN")
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "devicefarm",
        region_name=REGION,
        config=Config(
            connect_timeout=15, read_timeout=60, retries={"max_attempts": 3, "mode": "standard"}
        ),
    )
    device = select_device(list(pages(client, "list_devices", "devices", arn=project)))
    print(f"Selected {device['name']} / iOS {device['os']}; one device, 30 minute timeout")
    output.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    run = None
    completed = False

    def stop_on_signal(_signum, _frame):
        raise InterruptedError("Workflow cancelled")

    signal.signal(signal.SIGTERM, stop_on_signal)
    signal.signal(signal.SIGINT, stop_on_signal)
    try:
        app_arn = upload(client, project, ipa, "IOS_APP", created)
        test_arn = upload(client, project, tests, "XCTEST_UI_TEST_PACKAGE", created)
        spec_arn = upload(
            client,
            project,
            Path(__file__).parents[1] / "review-ui-tests/devicefarm.yml",
            "XCTEST_UI_TEST_SPEC",
            created,
        )
        request = {
            "projectArn": project,
            "appArn": app_arn,
            "name": f"App Review {os.environ.get('GITHUB_RUN_ID', 'manual')}",
            "deviceSelectionConfiguration": {
                "maxDevices": 1,
                "filters": [{"attribute": "ARN", "operator": "EQUALS", "values": [device["arn"]]}],
            },
            "test": {"type": "XCTEST_UI", "testPackageArn": test_arn, "testSpecArn": spec_arn},
            "configuration": {
                "billingMethod": "METERED",
                "locale": "en_US",
                "environmentVariables": [
                    {"name": key, "value": os.environ[key]} for key in CREDENTIALS
                ],
            },
            "executionConfiguration": {
                "jobTimeoutMinutes": 30,
                "videoCapture": True,
                "skipAppResign": False,
                "accountsCleanup": True,
                "appPackagesCleanup": True,
            },
        }
        # Never print this request or response: AWS retains run environment values.
        run = client.schedule_run(**request)["run"]["arn"]
        deadline = time.monotonic() + 2700
        result = {}
        while time.monotonic() < deadline:
            result = client.get_run(arn=run)["run"]
            print(f"Device Farm: {result['status']}", flush=True)
            if result["status"] == "COMPLETED":
                completed = True
                break
            time.sleep(20)
        if not completed:
            raise TimeoutError("Physical run exceeded queue/execution deadline")
        videos, test_passed = collect(client, run, output)
        (output / "device.json").write_text(
            json.dumps(
                {
                    "name": device["name"],
                    "os": device["os"],
                    "result": result.get("result"),
                    "deviceMinutes": result.get("deviceMinutes"),
                    "source": os.environ.get("GITHUB_SHA"),
                    "physical": True,
                },
                indent=2,
            )
            + "\n"
        )
        if not videos or not test_passed or result.get("result") != "PASSED":
            raise RuntimeError("Physical run failed or complete video/test evidence is missing")
        if not all((output / f"review-{shot}.png").is_file() for shot in SHOTS):
            raise RuntimeError("Per-step screenshots missing")
        from verify_review_video import verify_splash

        if not any(verify_splash(video, output) for video in videos):
            raise RuntimeError("Native splash not visible in physical recording")
    finally:
        # Run metadata contains credentials. Once evidence is downloaded (also
        # on failure), remove it; the workflow artifact is the retained evidence.
        cleanup(client, run, created, output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ipa-dir", required=True, type=Path)
    parser.add_argument("--tests", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        ipa = validate_packages(args.ipa_dir, args.tests)
        if args.dry_run or os.environ.get("REVIEW_DRY_RUN", "true").lower() == "true":
            print("Dry run passed: signed IPA and runner valid; no Device Farm calls made")
            return 0
        execute(ipa, args.tests, args.output)
        return 0
    except Exception as exc:
        print(
            f"::error::Device Farm review failed ({type(exc).__name__}); raw SDK details withheld"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
