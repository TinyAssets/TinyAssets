"""Boundary tests for paid runs, credential injection and exported evidence."""

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "mobile/scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


xc = load("review_xctest")
farm = load("devicefarm_review")


def test_runtime_environment_reaches_v1_and_v2_targets():
    secret = {
        "APP_STORE_REVIEW_USERNAME": "fixture-user",
        "APP_STORE_REVIEW_PASSWORD": "fixture-secret",
    }
    for document, target in [
        ({"ReviewUITests": {"TestBundlePath": "runner"}}, "v1"),
        ({"TestConfigurations": [{"TestTargets": [{"TestBundlePath": "runner"}]}]}, "v2"),
    ]:
        xc.inject_environment(document, secret)
        entry = (
            document["ReviewUITests"]
            if target == "v1"
            else document["TestConfigurations"][0]["TestTargets"][0]
        )
        assert entry["EnvironmentVariables"] == secret
    with pytest.raises(ValueError):
        xc.inject_environment({}, secret)


def device(name="Apple iPhone 16", os="26.0", **overrides):
    return {
        "name": name,
        "os": os,
        "platform": "IOS",
        "fleetType": "PUBLIC",
        "availability": "AVAILABLE",
        **overrides,
    }


def test_newest_ios_numeric_selection_excludes_old_busy_and_private_devices():
    wanted = device("Apple iPhone 14", "26.10")
    assert (
        farm.select_device(
            [
                device("Apple iPhone 17", "26.9"),
                wanted,
                device("Apple iPhone 13", "27.0"),
                device(os="28", availability="BUSY"),
                device(os="28", fleetType="PRIVATE"),
                device("Apple iPad Pro", "28"),
            ]
        )
        == wanted
    )
    with pytest.raises(ValueError):
        farm.select_device([device("Apple iPhone 13")])


def packages(tmp_path):
    ipa = tmp_path / "App.ipa"
    with zipfile.ZipFile(ipa, "w") as archive:
        archive.writestr("Payload/App.app/Info.plist", "fixture")
    (tmp_path / "SHA256SUMS").write_text(
        hashlib.sha256(ipa.read_bytes()).hexdigest() + " App.ipa\n"
    )
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"bundle_id": "io.tinyassets.app", "file": "App.ipa", "commit": "fixture-sha"})
    )
    tests = tmp_path / "tests.zip"
    with zipfile.ZipFile(tests, "w") as archive:
        archive.writestr("review_xctest.py", "fixture")
        archive.writestr(
            "ReviewUITests-Runner.app/PlugIns/ReviewUITests.xctest/ReviewUITests", "fixture"
        )
    return ipa, tests


def test_dry_run_never_imports_aws_or_reads_credentials(tmp_path, monkeypatch):
    _, tests = packages(tmp_path)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setitem(sys.modules, "boto3", None)
    for name in (*xc.CREDENTIALS, "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devicefarm_review",
            "--ipa-dir",
            str(tmp_path),
            "--tests",
            str(tests),
            "--output",
            str(tmp_path / "output"),
            "--dry-run",
        ],
    )
    assert farm.main() == 0


def test_wrong_sha_or_private_configuration_blocks_upload(tmp_path, monkeypatch):
    _, tests = packages(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "different-sha")
    with pytest.raises(ValueError, match="different commit"):
        farm.validate_packages(tmp_path, tests)
    monkeypatch.delenv("GITHUB_SHA")
    with zipfile.ZipFile(tests, "a") as archive:
        archive.writestr("review-private.xctestrun", "must never upload")
    with pytest.raises(ValueError, match="Private runtime"):
        farm.validate_packages(tmp_path, tests)


def test_missing_step_screenshot_cannot_count_as_complete():
    assert len(xc.SHOTS) == 8
    assert "04-persisted" in xc.SHOTS
    assert "05-grounded-answer" in xc.SHOTS
    assert "06-account-privacy" in xc.SHOTS
