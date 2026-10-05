"""Execution-chain mutations, including ancestors and symlink traversal."""
import os
from pathlib import Path

import pytest

from scripts.check_privileged_chain import UnsafeChain, verify_path, verify_paths

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX modes; Linux oracle required")


@pytest.fixture
def chain(tmp_path, monkeypatch):
    # Model uid 0 only for unit tests. Production-image probes use real owners.
    original = Path.lstat

    def owned(path):
        result = original(path)
        fields = list(result)
        fields[4] = 0
        # pytest's ancestor /tmp is intentionally shared; isolate that fixture.
        if not path.is_relative_to(tmp_path):
            fields[0] &= ~0o022
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "lstat", owned)
    return tmp_path


def test_regular_protected_chain(chain):
    target = chain / "python"
    target.write_bytes(b"runtime")
    target.chmod(0o555)
    verify_path(target)


def test_writable_ancestor_refused(chain):
    directory = chain / "writable"
    directory.mkdir()
    directory.chmod(0o777)
    target = directory / "python"
    target.write_bytes(b"runtime")
    target.chmod(0o555)
    with pytest.raises(UnsafeChain, match="writable chain"):
        verify_path(target)


@pytest.mark.skipif(os.name == "nt", reason="Linux symlink semantics; oracle required")
def test_link_targets_and_intermediate_parents_are_verified(chain):
    target = chain / "python"
    target.write_bytes(b"runtime")
    target.chmod(0o555)
    link = chain / "link"
    link.symlink_to("python")
    verify_path(link)
    target.chmod(0o777)
    with pytest.raises(UnsafeChain, match="writable chain"):
        verify_path(link)
    target.chmod(0o555)
    writable = chain / "writable"
    writable.mkdir(mode=0o777)
    writable.chmod(0o777)
    link.unlink()
    link.symlink_to("writable/../python")
    with pytest.raises(UnsafeChain, match="writable chain"):
        verify_path(link)


def test_missing_optional_import_entry_still_checks_parent(chain):
    verify_path(chain / "python.zip", allow_missing=True)
    chain.chmod(0o777)
    with pytest.raises(UnsafeChain, match="writable chain"):
        verify_path(chain / "python.zip", allow_missing=True)


def test_writable_module_beneath_protected_import_directory_refused(chain):
    package = chain / "site-packages"
    package.mkdir(mode=0o755)
    module = package / "module.py"
    module.write_bytes(b"pass")
    module.chmod(0o666)
    with pytest.raises(UnsafeChain, match="writable chain"):
        verify_paths([], [str(package)])
