"""Role-split startup and healthcheck are wired but default OFF.

The production-image ON path (migration, PID1 bootstrap, serve, switched
healthcheck, reverse) is proven by scripts/role_startup_probe.py.
"""
import re
from pathlib import Path

import pytest

from deploy import role_startup

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 1", "1 "])
@pytest.mark.parametrize("mode", ["start", "reverse", "health"])
def test_every_mode_refuses_unless_switch_is_exactly_one(monkeypatch, value, mode):
    def loaded(name):
        raise AssertionError("an OFF switch must not load any privileged helper")

    monkeypatch.setattr(role_startup, "_load", loaded)
    environ = {} if value is None else {role_startup.SWITCH: value}
    assert role_startup.main([mode], environ) == role_startup.REFUSE


@pytest.mark.parametrize("argv", [[], ["serve"], ["start", "extra"]])
def test_unknown_modes_refuse_before_the_switch(monkeypatch, argv):
    monkeypatch.setattr(role_startup, "_load", lambda name: pytest.fail("loaded"))
    assert role_startup.main(argv, {role_startup.SWITCH: "1"}) == role_startup.REFUSE


def test_bindings_are_principal_center_pairs_from_inventory():
    facts = {"principals": {"u-1": "alice", "u-2": "bob"},
             "bindings": {"u-1": 300001, "u-2": 300002}, "unallocated": []}
    helpers = {"inventory": {"inventory": lambda root, owner, egress: facts},
               "owner": None, "egress": None}
    assert role_startup.bindings({}, helpers) == {("alice", "u-1"): 300001,
                                                  ("bob", "u-2"): 300002}
    facts["unallocated"] = ["carol"]
    with pytest.raises(RuntimeError, match="unallocated"):
        role_startup.bindings({}, helpers)


def test_default_image_and_compose_never_select_the_split():
    dockerfile = (REPO / "Dockerfile").read_text()
    assert 'ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/libexec/ta-entry.sh"]' in dockerfile
    assert 'CMD ["python", "-m", "tinyassets.serve"]' in dockerfile
    assert "COPY --chmod=0555 deploy/role_startup.py /usr/local/libexec/ta-role-start.py" in (
        dockerfile)
    compose = (REPO / "deploy/compose.yml").read_text()
    assert 'test: ["CMD", "/usr/local/libexec/ta-op", "pulse"]' in compose
    assert role_startup.SWITCH not in compose and "ta-role-start" not in compose
    overlay = (REPO / "deploy/compose.role-split.yml").read_text()
    assert f'{role_startup.SWITCH}: "1"' in overlay
    assert re.search(r'"ta-role-start\.py", "health"\]', overlay.replace("\n", " ")
                     .replace("/usr/local/libexec/", ""))
    for path in (*REPO.glob("deploy/*.sh"), *REPO.glob(".github/workflows/*.yml"),
                 *REPO.glob("scripts/deploy*.py")):
        assert "compose.role-split" not in path.read_text(errors="replace"), path
