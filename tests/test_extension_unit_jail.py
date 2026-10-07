"""Exact revision execution through the shipping ta client and Linux jail."""
import json
import shlex

from tests.test_ta_capabilities_jail import (  # noqa: F401 -- shared Linux fixtures and guard
    _engine,
    bash,
    dynamic_data_root,
    pytestmark,
)
from tests.test_ta_capabilities_jail import world as world
from tinyassets.extension_state import ExtensionStore


def test_revision_mount_is_readonly_and_revoke_fences_dispatch(world, monkeypatch):
    from tests.test_one_extension_unit import files

    server = _engine(monkeypatch, world)
    from tinyassets.api.helpers import _universe_dir

    root = _universe_dir(server._GRAPH_ID)
    state = ExtensionStore(root.parent, owner=server._ACTOR_ID,
                           universe=root.name, agent="main")
    content = files()
    content["run.py"] = (b'#!/usr/bin/env python3\nimport json,sys\n'
                         b'print(json.dumps({"entry":sys.argv[1],"value":"PINNED"}))\n')
    installed = state.install(content)
    # Use real ta activation to capture exactly the current launch ceiling.
    pin = {"name": "sample", "revision": installed["revision"], "expected_generation": 0}
    assert json.loads(bash(server, "ta extension:activate --json " +
                           shlex.quote(json.dumps(pin))))["state"] == "active"
    mounted = f"/ta/extensions/sample/{installed['revision']}/run.py"
    name = f"extension:sample:{installed['revision']}:1:tools:hello"
    assert "PINNED" in bash(server, f"ta {name} --json '{{}}'")
    assert "READONLY" in bash(server, f"if echo changed > {mounted}; then exit 2; "
                              "else echo READONLY; fi")
    # Workspace edits cannot replace the immutable mount or installed bytes.
    bash(server, "mkdir -p extensions/sample; echo changed > extensions/sample/run.py")
    assert "PINNED" in bash(server, f"ta {name} --json '{{}}'")
    pin["expected_generation"] = 1
    # Same jail already mounted code, but daemon dispatch observes revocation.
    output = bash(server, "ta extension:revoke --json " + shlex.quote(json.dumps(pin)) +
                  f"; ta {name} --json '{{}}'", exit_code=1)
    assert "revoked" in output
    assert "unknown capability" in output
    assert "ABSENT" in bash(server, f"test ! -e {mounted} && echo ABSENT")
