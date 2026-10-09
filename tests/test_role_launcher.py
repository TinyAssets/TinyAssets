"""Portable contract checks; kernel behavior is proven in the production oracle."""
import json
import runpy
from pathlib import Path

import pytest

from tinyassets.platform_secrets import CHILD_FORBIDDEN_ENV

LAUNCHER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "deploy/role_launcher.py"))


def test_bootstrap_failure_is_actionable_bounded_and_secret_free(monkeypatch, capfd):
    module = runpy.run_path(str(Path(__file__).resolve().parents[1]
                               / 'deploy/role_owner_launcher.py'))
    function = module['bootstrap_services']
    monkeypatch.setattr(module['os'], 'getpid', lambda: 1)
    def fail(*a):
        raise OSError(13, 'SECRET-CREDENTIAL-CONTENT', '/secret/token')
    monkeypatch.setitem(function.__globals__, '_bootstrap_services', fail)
    def exited(code):
        raise SystemExit(code)
    monkeypatch.setattr(module['os'], '_exit', exited)
    with pytest.raises(SystemExit) as error:
        function('/data', '/run', {}, {}, generation=0)
    assert error.value.code == 78
    output = capfd.readouterr().err
    assert 'errno=13' in output and 'bootstrap' in output and 'fail' in output
    assert len(output) < 1024 and 'SECRET' not in output and '/secret/token' not in output


def test_broker_allowlist_withholds_unknown_and_forbidden_environment(monkeypatch):
    for name in (*CHILD_FORBIDDEN_ENV, "UNENUMERATED_SECRET", "PYTHONPATH", "LD_PRELOAD"):
        monkeypatch.setenv(name, "must-not-cross")
    monkeypatch.setenv("TZ", "UTC")
    result = LAUNCHER["broker_environment"]("/data")
    assert set(result) == {"PATH", "LANG", "HOME", "PYTHONDONTWRITEBYTECODE",
                           "TINYASSETS_DATA_DIR", "TZ"}
    assert not CHILD_FORBIDDEN_ENV.intersection(result)
    assert "must-not-cross" not in result.values()


def test_no_engine_identity_can_run_outside_an_owner_cell():
    with pytest.raises(KeyError):
        LAUNCHER["retire_child"]("engine")


@pytest.mark.parametrize("value, expected", [
    ("1", "1"), (" TRUE ", "1"), ("yes", "1"), ("on", "1"),
    ("0", "0"), ("false", "0"), ("secret-not-a-boolean", "0"), ("", "0"),
])
def test_broker_receives_only_canonical_http_deployment_opt_in(monkeypatch, value, expected):
    name = "TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED"
    monkeypatch.setenv(name, value)
    assert LAUNCHER["broker_environment"]("/data")[name] == expected


def test_broker_http_remains_disabled_without_startup_opt_in(monkeypatch):
    name = "TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED"
    monkeypatch.delenv(name, raising=False)
    assert name not in LAUNCHER["broker_environment"]("/data")


def test_bootstrap_holds_no_migration_or_sys_admin_capability():
    # KILL, SETGID, SETUID, SETPCAP only; CHOWN/DAC_OVERRIDE/FOWNER stay in the
    # one-shot migration container and SYS_ADMIN is never held.
    assert LAUNCHER["ENTRY_CAPS"] == sum(1 << cap for cap in (5, 6, 7, 8))
    assert "retire_migration_authority" not in LAUNCHER


def test_startup_refuses_an_unmigrated_volume(tmp_path):
    (tmp_path / ".layout.json").write_text(json.dumps({"layout": 2, "state": "stable"}))
    with pytest.raises(LAUNCHER["Refused"], match="not migrated"):
        LAUNCHER["require_split"](tmp_path)
    (tmp_path / ".layout.json").write_text(json.dumps(
        {"layout": 2, "state": "stable", "split": "owner-split"}))
    assert LAUNCHER["require_split"](tmp_path)["split"] == "owner-split"


def test_startup_refuses_a_volume_without_a_marker(tmp_path):
    with pytest.raises(LAUNCHER["Refused"], match="no readable layout marker"):
        LAUNCHER["require_split"](tmp_path)


def test_admissions_bind_live_trees_and_hold_missing_ones(tmp_path, capsys):
    (tmp_path / "u-a").mkdir()
    (tmp_path / "u-gone").mkdir()
    rows = [dict(generation=1, event="admit", principal="a", center="u-a", machine=300001),
            dict(generation=2, event="admit", principal="b", center="u-lost", machine=300002),
            dict(generation=3, event="admit", principal="c", center="u-gone", machine=300003),
            dict(generation=4, event="retire", principal="c", center="u-gone",
                 machine=300003)]
    bindings, generation = LAUNCHER["admissions"](rows, tmp_path)
    assert bindings == {("a", "u-a"): 300001}
    assert generation == 4
    assert "u-lost has no tree" in capsys.readouterr().err
