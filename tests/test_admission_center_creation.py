"""DA4/DA8 at the daemon's center-creation sites.

With the bounded client installed, creation goes through admit_center, a
failure after publish keeps the root and grant and resumes in place, and no
write site can mkdir a center root. Without it, legacy behaviour is unchanged
(the existing first-contact and creation suites cover that path).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_first_contact import _login, _reset_auth, _serial_dirs  # noqa: F401
from tinyassets import role_center_admission, role_decoder

pytestmark = pytest.mark.role_split  # the real write-site guards


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch) -> Path:
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    return base


@pytest.fixture
def admission(monkeypatch):
    """A bounded client and a recorded admit_center that publishes the root."""
    calls = []
    failures = []

    def admit_center(data_root, *, principal, center):
        calls.append((Path(data_root), principal, center))
        (Path(data_root) / center).mkdir(exist_ok=True)  # publish (resumable)
        if failures:
            raise failures.pop(0)
        return len(calls)

    monkeypatch.setattr(role_decoder, "_bounded_client", object())
    monkeypatch.setattr(role_center_admission, "admit_center", admit_center)
    return calls, failures


def test_selected_create_routes_through_admission(data_dir, admission):
    from tinyassets.api.first_contact import ensure_founder_home

    calls, _ = admission
    _login("founder-1")
    uid = ensure_founder_home(data_dir, "founder-1")
    assert uid and (data_dir / uid / "soul.md").is_file()
    assert calls == [(data_dir, "founder-1", uid)]


@pytest.mark.parametrize("step", ["log-append", "bind", "seeding"])
def test_a_failure_after_publish_keeps_root_and_grant_then_resumes(
        data_dir, admission, monkeypatch, step):
    from tinyassets.api import universe as universe_api
    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import get_founder_home, universe_access_permission

    calls, failures = admission
    real_seed = universe_api.seed_okf_bundle
    if step == "seeding":
        def boom(*_a, **_k):
            raise OSError("seed failed mid-bundle")
        monkeypatch.setattr(universe_api, "seed_okf_bundle", boom)
    else:
        failures.append(RuntimeError(f"{step} failed"))
    _login("founder-1")
    assert ensure_founder_home(data_dir, "founder-1") == ""
    uid = get_founder_home(data_dir, "founder-1")
    assert uid and (data_dir / uid).is_dir()               # root kept, never rmtree'd
    assert not (data_dir / uid / "soul.md").is_file()
    assert universe_access_permission(data_dir, universe_id=uid,
                                      actor_id="founder-1") == "admin"  # grant kept
    monkeypatch.setattr(universe_api, "seed_okf_bundle", real_seed)
    assert ensure_founder_home(data_dir, "founder-1") == uid   # same id, in place
    assert (data_dir / uid / "soul.md").is_file()
    assert [call[2] for call in calls] == [uid, uid]
    assert len(_serial_dirs(data_dir)) == 1


def test_a_complete_or_foreign_center_is_never_resumed(data_dir, admission):
    import json

    from tinyassets.api.universe import _universe_impl
    from tinyassets.daemon_server import grant_universe_ownership

    _login("founder-1")
    first = json.loads(_universe_impl(action="create_universe", universe_id="alpha",
                                      allow_named_universe_id=True))
    assert first["status"] == "created"
    again = json.loads(_universe_impl(action="create_universe", universe_id="alpha",
                                      allow_named_universe_id=True))
    assert "already exists" in again["error"]
    grant_universe_ownership(data_dir, universe_id="beta", owner_id="founder-2")
    (data_dir / "beta").mkdir()
    (data_dir / "beta" / "keep").write_text("theirs")
    with pytest.raises(Exception):  # noqa: B017,PT011 - the ownership conflict re-raises
        _universe_impl(action="create_universe", universe_id="beta",
                       allow_named_universe_id=True)
    assert (data_dir / "beta" / "keep").read_text() == "theirs"


def test_write_sites_never_mkdir_a_center_root_when_selected(data_dir, admission):
    from tinyassets.role_center_admission import AdmissionRefused, ensure_center_dir

    with pytest.raises(AdmissionRefused):
        ensure_center_dir(data_dir / "missing")
    assert not (data_dir / "missing").exists()
    (data_dir / "present").mkdir()
    ensure_center_dir(data_dir / "present")


@pytest.mark.skipif(os.name != "posix", reason="link-free POSIX writer")
def test_implicit_parent_creation_cannot_make_a_center_root(data_dir, admission):
    from tinyassets.universe_files import UniverseFileError, write_data_path

    with pytest.raises(UniverseFileError, match="admission"):
        write_data_path(data_dir / "missing" / "soul.md", "x")
    assert not (data_dir / "missing").exists()
    write_data_path(data_dir / ".platform-state" / "value", "x")  # not a center
    (data_dir / "present").mkdir()
    write_data_path(data_dir / "present" / "deep" / "value", "x")  # inside a root
