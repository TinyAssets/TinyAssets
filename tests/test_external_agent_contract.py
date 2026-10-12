"""Shipped package data cannot add a shell, path escape, or unknown transport."""
import copy
import hashlib
import json

import pytest

from tinyassets.extension_manifest import ExtensionError, parse_manifest
from tinyassets.external_agents.registry import resolve


def _manifest():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tinyassets/external_agents/packages"
    return json.loads((path / "codex/extension.json").read_text())


def test_shipped_package_is_digest_pinned():
    package = resolve("codex")
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tinyassets/external_agents/packages"
    digest = hashlib.sha256((path / "codex/extension.json").read_bytes()).hexdigest()
    assert digest == package.digest


@pytest.mark.parametrize("mutation", [
    lambda row: row["job"]["start"]["params"].update(model="prefix-{model}"),
    lambda row: row["auth"]["files"].append("../auth.json"),
    lambda row: row["catalogue"].update(transport="shell"),
    lambda row: row["job"]["input"]["params"].update(input="{unknown}"),
])
def test_invalid_package_refused_before_launch(mutation):
    document = copy.deepcopy(_manifest())
    mutation(document["external_agents"][0])
    with pytest.raises(ExtensionError):
        parse_manifest(json.dumps(document).encode())
