"""The log reader key is a forced command, and it never creates authorized_keys."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "install_log_reader", ROOT / "deploy" / "install-log-reader.py",
)
installer = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(installer)

PUBKEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTestsOnly0123456789 log-read"
)
DEPLOY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDeployKeyForTestsOnly012345678901 deploy"
)


def test_rewrite_keeps_other_keys_and_replaces_its_own_line():
    updated = installer.rewrite_authorized_keys(DEPLOY + "\n", PUBKEY)
    assert updated is not None
    assert DEPLOY in updated
    assert updated.count(installer.COMMAND) == 1
    assert "no-pty" in updated
    again = installer.rewrite_authorized_keys(updated, PUBKEY)
    assert again is not None
    assert again.count(installer.COMMAND) == 1
    assert DEPLOY in again


def test_empty_or_missing_authorized_keys_is_left_alone(tmp_path):
    assert installer.rewrite_authorized_keys("", PUBKEY) is None
    assert installer.rewrite_authorized_keys("# comment only\n", PUBKEY) is None
    target = tmp_path / "authorized_keys"
    result = installer.install_key(target, PUBKEY, os.getuid(), os.getgid())
    assert result == "absent"
    assert not target.exists()


def test_symlink_is_refused(tmp_path):
    target = tmp_path / "authorized_keys"
    target.symlink_to(tmp_path / "elsewhere")
    try:
        installer.install_key(target, PUBKEY, os.getuid(), os.getgid())
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("a symlink must not be rewritten")


def test_pubkey_must_be_a_single_safe_line():
    assert installer.valid_pubkey(PUBKEY)
    assert not installer.valid_pubkey(PUBKEY + "\nssh-ed25519 AAAA other")
    assert not installer.valid_pubkey('ssh-ed25519 AAAA "forced"')
    assert not installer.valid_pubkey("ssh-ed25519 AAAA\nrm -rf /")
