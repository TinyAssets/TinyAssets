"""W1/S29: where a model source may run is source data, resolved by one resolver."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from scripts import check_shapes
from tinyassets.providers import placement
from tinyassets.providers.source_record import SourceRecord, records

_PLACED = "model-source-discovery:Placement is source data"
_CLI = {"placement": ["hosted_box"], "billing": ["plan"], "serves": "owner_only"}


@pytest.mark.verifies(_PLACED)
@pytest.mark.parametrize("execution, cli", [
    ({"placement": ["thin_loop"], "billing": ["plan"], "serves": "owner_only"}, True),
    ({"placement": ["device"], "billing": ["free"], "serves": "may_serve_others"}, False),
    ({"placement": ["thin_loop"], "billing": ["plan"], "serves": "may_serve_others"}, False),
    ({"placement": [], "billing": ["free"], "serves": "owner_only"}, False),
    ({"placement": ["cloud"], "billing": ["free"], "serves": "owner_only"}, False),
    ({"placement": ["thin_loop"], "billing": ["free"]}, False),
])
def test_execution_rules_refuse_a_malformed_block(execution, cli):
    with pytest.raises(ValueError):
        placement.validate(execution, cli=cli)


@pytest.mark.verifies(_PLACED)
def test_every_shipped_record_declares_where_it_runs():
    for record in records(strict=True).values():
        assert set(record.execution) == {"placement", "billing", "serves"}
        if "cli" in record.invoke:
            assert record.execution == _CLI


@pytest.mark.verifies(_PLACED)
def test_a_record_without_the_block_runs_where_its_mechanism_ran():
    codex = records()["codex"].document()
    detected = {**records()["groq"].document(), "origin": "detected", "execution": None}
    assert SourceRecord.parse(detected).execution == placement.DETECTED
    assert SourceRecord.parse({**codex, "execution": None}).execution == _CLI
    # A page opened before the deploy posts the old flag; it is read, not refused.
    legacy = {k: v for k, v in records()["groq"].document().items() if k != "execution"}
    assert SourceRecord.parse({**legacy, "free": True}).execution["billing"] == ["free"]


@pytest.mark.verifies(_PLACED)
def test_no_available_placement_is_refused_naming_each_one_tried():
    execution = {"placement": ["device", "hosted_box"], "billing": ["plan"],
                 "serves": "owner_only"}
    with pytest.raises(placement.PlacementRefused) as refused:
        placement.resolve_placement(
            "subscription_cli:codex", execution, {"hosted_box": "its owner box is unavailable"})
    assert refused.value.tried == {"device": placement.NOT_BUILT["device"],
                                   "hosted_box": "its owner box is unavailable"}
    assert "subscription_cli:codex" in str(refused.value)
    # Never a silent substitute: device is skipped only by refusal, hosted_box is chosen.
    assert placement.resolve_placement("s", execution, {"hosted_box": None}) == "hosted_box"


@pytest.mark.verifies(_PLACED)
def test_dispatch_reaches_only_an_executor_the_record_allows(monkeypatch):
    from tinyassets.providers.provider_resolver import provider_for_definition

    assert placement.require_dispatch(_CLI, "subscription_cli") == "hosted_box"
    cli = SimpleNamespace(access_method="subscription_cli", ref="codex")
    monkeypatch.setattr(placement, "execution_for", lambda definition: {
        **_CLI, "placement": ["device"]})
    with pytest.raises(PermissionError, match="cannot run on this executor"):
        provider_for_definition(cli)  # Refused before any executor is built.
    http = SimpleNamespace(access_method="api_key_http", ref="grant")
    monkeypatch.undo()
    assert placement.execution_for(http) == placement.DETECTED


@pytest.mark.verifies(_PLACED)
def test_the_plan_leaves_out_an_unplaceable_source_and_says_why(tmp_path, monkeypatch):
    from tinyassets.providers import definition as definitions
    from tinyassets.providers import served_model_plan, subscription_catalog

    universe = tmp_path / "center"
    universe.mkdir()
    codex = SimpleNamespace(id="codex-1", owner_user_id="alice",
                            access_method="subscription_cli", ref="codex")
    monkeypatch.setattr(definitions, "list_definitions", lambda name: [codex])

    def unavailable(*args):
        raise PermissionError("no box")

    monkeypatch.setattr(subscription_catalog, "context", unavailable)
    refused = []
    assert served_model_plan.connected_sources(tmp_path, universe, "alice",
                                               refused=refused) == frozenset()
    assert [exc.tried for exc in refused] == [
        {"hosted_box": "its owner box or sign-in is unavailable"}]
    monkeypatch.setattr(subscription_catalog, "context", lambda *args: None)
    assert served_model_plan.connected_sources(tmp_path, universe, "alice") == frozenset(
        {"subscription_cli:codex-1"})


@pytest.mark.verifies(_PLACED)
def test_an_owner_only_source_serves_only_its_owner():
    placement.require_serves(_CLI, owner="alice", caller="alice")
    with pytest.raises(PermissionError):
        placement.require_serves(_CLI, owner="alice", caller="bob")
    placement.require_serves({**_CLI, "serves": "may_serve_others"}, owner="alice", caller="bob")


@pytest.mark.verifies(_PLACED)
def test_connected_cli_digests_are_unchanged():
    """The pre-placement document, byte for byte, so no owner is asked to reconnect."""
    import subprocess

    fields = ["id", "display_name", "origin", "addresses", "sign_in", "invoke", "egress",
              "state", "suggested", "free", "models", "ranking"]
    defaults = {"suggested": False, "free": False, "models": [], "ranking": None}
    for record in records(strict=True).values():
        before = json.loads(subprocess.run(
            ["git", "show", f"origin/main:tinyassets/providers/source_records/{record.id}.json"],
            capture_output=True, text=True, check=False).stdout or "null")
        if before is None or "free" not in before:
            continue  # Not on main, or main already carries the block.
        expected = {key: before.get(key, defaults.get(key)) for key in fields}
        assert json.dumps(record.digest_document(), sort_keys=True) == json.dumps(
            expected, sort_keys=True)


@pytest.mark.verifies(_PLACED)
def test_an_owner_file_written_before_placement_keeps_its_mechanism(tmp_path):
    codex = records()["codex"].document()
    legacy = {key: value for key, value in codex.items() if key != "execution"}
    legacy["free"] = False
    sources = tmp_path / ".ta-cli" / "sources"
    sources.mkdir(parents=True)
    (sources / "codex.json").write_text(json.dumps(legacy))
    assert records(tmp_path)["codex"].execution == _CLI
    del legacy["free"]  # The flag was optional; an owner file without it still loads.
    (sources / "codex.json").write_text(json.dumps(legacy))
    assert records(tmp_path)["codex"].execution == _CLI


@pytest.mark.verifies(_PLACED)
def test_placement_branching_only_falls_and_has_one_resolver():
    shape = check_shapes.SHAPES["S29"]
    assert check_shapes.old_count(shape, check_shapes.ROOT) <= shape.ceiling
    assert re.search(check_shapes.ENTRY_SYMBOLS["S29"], "def resolve_placement(a, b, c):")
    assert check_shapes.registered_path("tinyassets/providers/placement.py", shape.paths[0])
