"""Boundary tests for paid runs, credential injection and exported evidence."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "mobile/scripts"


def load(name, directory=SCRIPTS):
    spec = importlib.util.spec_from_file_location(name, directory / f"{name}.py")
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


def test_stopping_run_is_awaited_and_all_uploads_are_cleaned(tmp_path, monkeypatch):
    calls = []

    class Client:
        states = iter(["RUNNING", "STOPPING", "COMPLETED"])

        def get_run(self, **kwargs):
            state = next(self.states)
            calls.append(state)
            return {"run": {"status": state}}

        def stop_run(self, **kwargs):
            calls.append("stop")

        def delete_run(self, **kwargs):
            calls.append("delete")
            raise RuntimeError("fixture failure")

        def delete_upload(self, **kwargs):
            calls.append(kwargs["arn"])

    monkeypatch.setattr(farm.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="cleanup incomplete"):
        farm.cleanup(Client(), "run", ["upload-one", "upload-two"], tmp_path)
    assert calls == [
        "RUNNING",
        "stop",
        "STOPPING",
        "COMPLETED",
        "delete",
        "upload-two",
        "upload-one",
    ]
    assert json.loads((tmp_path / "cleanup-required.json").read_text()) == [
        {"operation": "delete_run", "arn": "run"}
    ]


def test_evidence_requires_recipient_key_and_rejects_tampering(tmp_path):
    from cryptography.exceptions import InvalidTag

    evidence = load("review_evidence")
    keygen = load("review_evidence_keygen", SCRIPTS.parents[1] / "scripts")
    private = tmp_path / "review.key"
    public = tmp_path / "review.pub"
    public.write_bytes(keygen.generate(private))
    wrong = tmp_path / "wrong.key"
    keygen.generate(wrong)
    source = tmp_path / "private"
    source.mkdir()
    (source / "video.mp4").write_bytes(b"private video fixture")
    encrypted = tmp_path / "evidence.enc"
    evidence.seal(source, encrypted, public)
    assert b"private video fixture" not in encrypted.read_bytes()
    with pytest.raises(InvalidTag):
        evidence.unseal(encrypted, tmp_path / "wrong-key", wrong)
    assert not (tmp_path / "wrong-key").exists()
    evidence.unseal(encrypted, tmp_path / "decrypted", private)
    assert (tmp_path / "decrypted/video.mp4").read_bytes() == b"private video fixture"
    body = bytearray(encrypted.read_bytes())
    body[-1] ^= 1
    encrypted.write_bytes(body)
    with pytest.raises(InvalidTag):
        evidence.unseal(encrypted, tmp_path / "tampered", private)
    assert not (tmp_path / "tampered").exists()


def test_recipient_placeholder_blocks_preflight_and_encryption(tmp_path, monkeypatch, capsys):
    evidence = load("review_evidence")
    placeholder = tmp_path / "recipient.pub"
    placeholder.write_text("PLACEHOLDER: lead must commit public key")
    monkeypatch.setattr(evidence, "RECIPIENT", placeholder)
    monkeypatch.setattr(sys, "argv", ["review_evidence", "validate-recipient"])
    assert evidence.main() == 1
    assert "lead's X25519 public key" in capsys.readouterr().out
    with pytest.raises(ValueError, match="Replace the recipient placeholder"):
        evidence.seal(tmp_path, tmp_path / "evidence.enc", placeholder)
    assert not (tmp_path / "evidence.enc").exists()


def test_keygen_private_permissions_public_only_and_no_overwrite(tmp_path, monkeypatch, capsys):
    from cryptography.hazmat.primitives.serialization import (
        load_pem_private_key,
        load_pem_public_key,
    )

    keygen = load("review_evidence_keygen", SCRIPTS.parents[1] / "scripts")
    evidence = load("review_evidence")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert keygen.main() == 0
    printed = capsys.readouterr()
    assert printed.err == ""
    private_path = evidence.private_key_path()
    original = private_path.read_bytes()
    private = load_pem_private_key(original, password=None)
    public = load_pem_public_key(printed.out.encode())
    assert public.public_bytes_raw() == private.public_key().public_bytes_raw()
    assert "PRIVATE" not in printed.out
    if os.name == "nt":
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                """
$acl = Get-Acl -LiteralPath $env:TA_REVIEW_KEY_PATH
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
@{ Protected = $acl.AreAccessRulesProtected; Count = $rules.Count;
   Owner = ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq $sid);
   OnlyOwner = ($rules[0].IdentityReference.Value -eq $sid);
   Allow = ($rules[0].AccessControlType.ToString() -eq 'Allow') } | ConvertTo-Json
""",
            ],
            env={**os.environ, "TA_REVIEW_KEY_PATH": str(private_path)},
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(result.stdout) == {
            "Protected": True,
            "Count": 1,
            "Owner": True,
            "OnlyOwner": True,
            "Allow": True,
        }
    else:
        assert private_path.stat().st_mode & 0o777 == 0o600
    assert keygen.main() == 1
    assert capsys.readouterr().out == ""
    assert private_path.read_bytes() == original


@pytest.mark.parametrize("fail_delete", [False, True])
def test_sweep_paginates_old_resources_preserves_recent_and_attempts_all(
    tmp_path, monkeypatch, fail_delete
):
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    old, recent = now - timedelta(hours=3), now - timedelta(minutes=20)
    calls = []

    class Client:
        def list_runs(self, **kwargs):
            assert kwargs["arn"] == "project"
            if kwargs.get("nextToken"):
                return {"runs": [{"arn": "old-two", "created": old}]}
            return {
                "runs": [
                    {"arn": "old-one", "created": old},
                    {"arn": "recent-run", "created": recent},
                ],
                "nextToken": "page2",
            }

        def list_uploads(self, **kwargs):
            assert kwargs["arn"] == "project"
            if kwargs.get("nextToken"):
                return {"uploads": [{"arn": "old-upload-two", "created": old}]}
            return {
                "uploads": [
                    {"arn": "old-upload", "created": old},
                    {"arn": "recent-upload", "created": recent},
                ],
                "nextToken": "page2",
            }

        def get_run(self, **kwargs):
            return {"run": {"status": "COMPLETED" if "stop" in calls else "RUNNING"}}

        def stop_run(self, **kwargs):
            calls.append("stop")

        def delete_run(self, **kwargs):
            calls.append(kwargs["arn"])
            if fail_delete and kwargs["arn"] == "old-one":
                raise RuntimeError("fixture delete failure")

        def delete_upload(self, **kwargs):
            calls.append(kwargs["arn"])
            if fail_delete and kwargs["arn"] == "old-upload":
                raise RuntimeError("fixture upload delete failure")

    monkeypatch.setattr(farm.time, "sleep", lambda _: None)
    if fail_delete:
        with pytest.raises(RuntimeError, match="no new run scheduled"):
            farm.sweep(Client(), "project", tmp_path, now=now)
        assert json.loads((tmp_path / "cleanup-required.json").read_text()) == [
            {"operation": "delete_run", "arn": "old-one"},
            {"operation": "delete_upload", "arn": "old-upload"},
        ]
    else:
        farm.sweep(Client(), "project", tmp_path, now=now)
        assert not (tmp_path / "cleanup-required.json").exists()
    assert calls == ["stop", "old-one", "old-two", "old-upload-two", "old-upload"]


def test_attachment_export_excludes_unnamed_screenshots_and_path_escapes(tmp_path, monkeypatch):
    private = tmp_path / "private"
    exported = private / "attachments"
    exported.mkdir(parents=True)
    (exported / "good.png").write_bytes(b"named screenshot")
    (private / "sensitive.png").write_bytes(b"must not escape")
    (exported / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "attachments": [
                        {
                            "suggestedHumanReadableName": "review-01-signed-out",
                            "exportedFileName": "good.png",
                        },
                        {
                            "suggestedHumanReadableName": "automatic screenshot",
                            "exportedFileName": "good.png",
                        },
                        {
                            "suggestedHumanReadableName": "review-02-signed-in",
                            "exportedFileName": "../sensitive.png",
                        },
                    ]
                }
            ]
        )
    )
    monkeypatch.setattr(xc.subprocess, "run", lambda *args, **kwargs: None)
    output = tmp_path / "public"
    output.mkdir()
    assert xc.export_shots(tmp_path / "result", output, private) == {"01-signed-out"}
    assert [p.name for p in output.iterdir()] == ["review-01-signed-out.png"]
