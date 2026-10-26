"""The image builder consumes reviewed bytes, never resolves dependencies."""

import hashlib
import json
from types import SimpleNamespace

import pytest


def test_image_uses_committed_lock_and_rejects_tampering(tmp_path, monkeypatch):
    from tinyassets.providers import subscription_image

    locks = tmp_path / "locks"
    directory = locks / "fixture"
    directory.mkdir(parents=True)
    manifest = {
        "name": "owner-subscription-source",
        "version": "1.0.0",
        "private": True,
        "dependencies": {"fixture": "1.0.0"},
    }
    lock = {
        "lockfileVersion": 3,
        "packages": {
            "": manifest,
            "node_modules/fixture": {
                "version": "1.0.0",
                "integrity": "sha512-fixture",
                "resolved": "https://registry.npmjs.org/fixture/-/fixture-1.0.0.tgz",
            },
        },
    }
    (directory / "package.json").write_text(json.dumps(manifest))
    raw = json.dumps(lock).encode()
    (directory / "package-lock.json").write_bytes(raw)
    source = SimpleNamespace(
        id="fixture",
        sign_in={
            "cli_login": {
                "install": {
                    "manager": "npm",
                    "package": "fixture",
                    "version": "1.0.0",
                    "bin": "fixture",
                    "integrity": "sha512-fixture",
                    "lock_sha256": hashlib.sha256(raw).hexdigest(),
                }
            }
        },
        egress={"registry": ["registry.npmjs.org"]},
    )
    monkeypatch.setattr(subscription_image, "LOCKS", locks, raising=False)
    calls = []

    def run(self, argv, **kwargs):
        calls.append(argv)
        self.binary.parent.mkdir(parents=True, exist_ok=True)
        self.binary.write_text("fixture")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subscription_image.ImageInstaller, "run", run)
    monkeypatch.setattr(subscription_image.ImageInstaller, "verify_lock", lambda *args: None)
    monkeypatch.setattr(
        subscription_image.ImageInstaller, "install_digest", lambda *args: "fixture"
    )
    image = subscription_image.ImageInstaller(source, tmp_path / "image")
    image.build()
    assert len(calls) == 1 and "ci" in calls[0] and "install" not in calls[0]
    assert (image.directory / "package-lock.json").read_bytes() == raw
    calls.clear()
    (directory / "package-lock.json").write_bytes(raw + b" ")
    with pytest.raises(Exception, match="lock digest"):
        image.build()
    assert not calls


def test_build_environment_excludes_host_credentials(tmp_path, monkeypatch):
    from tinyassets.providers.subscription_image import ImageInstaller

    monkeypatch.setenv("NPM_TOKEN", "host-secret")
    monkeypatch.setenv("NODE_OPTIONS", "host-options")
    observed = {}
    monkeypatch.setattr("subprocess.run", lambda argv, **kwargs: observed.update(kwargs))
    installer = object.__new__(ImageInstaller)
    installer.directory = tmp_path
    installer.run(["npm", "ci"], phase="registry")
    assert "env" in observed
    assert "NPM_TOKEN" not in observed["env"] and "NODE_OPTIONS" not in observed["env"]


pytestmark = pytest.mark.verifies('box-subscription-sources:Owner-box subscription runtime')
