"""The daemon and broker identity checks pass under production's compose stanzas.

Cutover attempt 7 (2026-10-21) committed, then the broker refused to start:
Docker lists a named user's own primary group among the supplementary groups,
so `getgroups()` was [1002, 1102] where the check demanded [1102]. Every
earlier proof launched the roles through its own launcher, never compose.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.verifies('owner-daemon-in-box:C14')


def _stanza(service):
    document = yaml.safe_load((ROOT / "deploy/compose.yml").read_text(encoding="utf-8"))
    return document["services"][service]


def _flags(stanza):
    flags = ["--user", stanza["user"]]
    for group in stanza.get("group_add", []):
        flags += ["--group-add", str(group)]
    for cap in stanza.get("cap_drop", []):
        flags += ["--cap-drop", cap]
    for option in stanza.get("security_opt", []):
        flags += ["--security-opt", option]
    return flags


@pytest.mark.parametrize("service, predicate", [
    ("broker", "tinyassets.broker.process:role_identity_retired"),
    ("daemon", "tinyassets.broker.supervisor:daemon_identity_split"),
])
def test_role_identity_holds_under_production_compose(service, predicate):
    if not hasattr(os, "geteuid") or os.geteuid() != 0 or sys.platform != "linux":
        pytest.skip("requires linux_oracle.py --docker-engine --as-root; "
                    "owner=c6-owner-box runs-in=linux_oracle.py")
    base = os.environ["TA_REHEARSAL_BASE_IMAGE"]
    image = "ta-role-identity-" + uuid.uuid4().hex[:12] + ":proof"
    context = f"FROM {base}\nCOPY tinyassets /app/tinyassets\n"
    built = subprocess.run(
        ["docker", "build", "-q", "-t", image, "-f", "-", str(ROOT)],
        input=context, capture_output=True, text=True, timeout=600)
    assert built.returncode == 0, built.stderr
    module, name = predicate.split(":")
    probe = (
        "import importlib\n"
        "fields = dict(l.split(':', 1) for l in open('/proc/self/status').read().splitlines())\n"
        f"print(importlib.import_module({module!r}).{name}(fields))\n"
    )
    try:
        result = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", *_flags(_stanza(service)),
             "--entrypoint", "/opt/venv/bin/python", image, "-I", "-c", probe],
            capture_output=True, text=True, timeout=300)
    finally:
        subprocess.run(["docker", "image", "rm", "-f", image], capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True", (service, result.stdout, result.stderr)
