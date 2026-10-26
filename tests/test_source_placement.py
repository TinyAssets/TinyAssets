"""W1/S29: where a model source may run is source data, resolved by one resolver."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from scripts import check_shapes
from tinyassets.providers import placement
from tinyassets.providers.source_record import SourceRecord, records

_CLI = {"placement": ["hosted_box"], "billing": ["plan"], "serves": "owner_only"}


@pytest.mark.verifies("model-source-discovery:Placement is source data")
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


@pytest.mark.verifies("model-source-discovery:Placement is source data")
def test_every_shipped_record_declares_where_it_runs():
    for record in records(strict=True).values():
        assert set(record.execution) == {"placement", "billing", "serves"}
        if "cli" in record.invoke:
            assert record.execution == _CLI


@pytest.mark.verifies("model-source-discovery:Placement is source data")
def test_a_record_without_the_block_runs_where_its_mechanism_ran():
    codex = records()["codex"].document()
    detected = {**records()["groq"].document(), "origin": "detected", "execution": None}
    assert SourceRecord.parse(detected).execution == placement.DETECTED
    assert SourceRecord.parse({**codex, "execution": None}).execution == _CLI
    # A page opened before the deploy posts the old flag; it is read, not refused.
    legacy = {k: v for k, v in records()["groq"].document().items() if k != "execution"}
    assert SourceRecord.parse({**legacy, "free": True}).execution["billing"] == ["free"]


@pytest.mark.verifies("model-source-discovery:Placement is source data")
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


@pytest.mark.verifies("model-source-discovery:Placement is source data")
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


@pytest.mark.verifies("model-source-discovery:Placement is source data")
def test_the_plan_leaves_out_an_unplaceable_source_and_says_why(tmp_path, monkeypatch):
    from tinyassets.providers import definition as definitions
    from tinyassets.providers import served_model_plan, subscription_catalog

    universe = tmp_path / "center"
    universe.mkdir()
    codex = SimpleNamespace(id="codex-1", owner_user_id="alice",
                            access_method="subscription_cli", ref="codex")
    monkeypatch.setattr(definitions, "list_definitions", lambda name: [codex])

    refused = []  # Outside an owner box a hosted_box source has nowhere to run.
    assert served_model_plan.connected_sources(tmp_path, universe, "alice",
                                               refused=refused) == frozenset()
    assert [exc.tried for exc in refused] == [
        {"hosted_box": "subscription CLI requires an admitted owner box"}]

    def unavailable(*args):
        raise PermissionError("no sign-in")

    monkeypatch.setattr(placement, "execution_for", lambda definition: _CLI)  # In its box.
    monkeypatch.setattr(subscription_catalog, "context", unavailable)
    refused = []
    assert served_model_plan.connected_sources(tmp_path, universe, "alice",
                                               refused=refused) == frozenset()
    assert [exc.tried for exc in refused] == [
        {"hosted_box": "its owner box or sign-in is unavailable"}]
    monkeypatch.setattr(subscription_catalog, "context", lambda *args: None)
    assert served_model_plan.connected_sources(tmp_path, universe, "alice") == frozenset(
        {"subscription_cli:codex-1"})


@pytest.mark.verifies("model-source-discovery:Placement is source data")
def test_one_unplaceable_definition_never_stops_the_others_registering(monkeypatch):
    from tinyassets.providers import definition as definitions
    from tinyassets.providers.provider_resolver import register_universe_open_providers

    cli = SimpleNamespace(id="cli", owner_user_id="alice", universe_id="center",
                          access_method="subscription_cli", ref="codex")
    http = SimpleNamespace(id="http", owner_user_id="alice", universe_id="center",
                           access_method="api_key_http", ref="grant")
    monkeypatch.setattr(definitions, "list_definitions", lambda name: [cli, http])
    built = []

    class Router:
        def register(self, provider):
            built.append(provider)

    monkeypatch.setattr("tinyassets.providers.api_key_http_provider.ApiKeyHttpProvider",
                        lambda definition: SimpleNamespace(name=definition.id))
    assert register_universe_open_providers(Router(), "center") == ["http"]


@pytest.mark.verifies("model-source-discovery:Placement is source data")
def test_an_owner_only_source_serves_only_its_owner():
    placement.require_serves(_CLI, owner="alice", caller="alice")
    with pytest.raises(PermissionError):
        placement.require_serves(_CLI, owner="alice", caller="bob")
    placement.require_serves({**_CLI, "serves": "may_serve_others"}, owner="alice", caller="bob")


#: Digests of each CLI record on main before placement: the connection marker,
#: the catalogue fingerprint, and the three older shapes upgrade_connection accepts.
_MAIN_DIGESTS = {
    "codex": {
        "plain": "3133eb4e3f094a8d8af6fc814dfb654e183bf3d166ffa127926f933b181643b8",
        "catalog": "c0c499881953e0b47b437f4f25e8c575373f184c47b0b1cd1c5d031fd37117b3",
        "upgrade": [
            "6e3546e2915bdd0c43614596f0a7d2f6b645e66a9d0b0a2d42618322288bf915",
            "225bf3334873aa8714be57cd5306c6e8e60bdcffbee43e5f83907973c1170375",
            "32aa42190f727426b4ab6b6221bb1920e8a706693596b128ae55aae38c0066fe",
        ],
    },
    "claude-code": {
        "plain": "1a72496c07cc1d1bd71005fc562bb41f81e57ecee291ff860d279d4e12375e89",
        "catalog": "38d68fc05e486bb5d32127544d58380d3c760a0c54c0697394e9a8d2c9231e00",
        "upgrade": [
            "e4da6d4d8fafa4865638df70ddd2fec27e8c242a3cbf1deca9c9e570dda96148",
            "f31f7719c160082e6772b1d08ad4ec2a5887414eaab0c268338cabd9e4550e44",
            "187ac777379b55e93964b8e2ca0b21d294ea56a2258c31ac3a34b16a3e35f606",
        ],
    },
    "gemini": {
        "plain": "45181b7334565f9aa88bfa177804970c98d8a2b4cc9047c94b9192ab9e9927f9",
        "catalog": "a80d7710f5939d1e1b05820b50f3bbc375071cc0f13045302575eb6917c3e886",
        "upgrade": [
            "c2e53d44b91bfaf4ed00c166503c01e5ea497e862e6ec363039f6f22e2a0ca4d",
            "c160b85b1d86dd32baefb428eed19bbe6ac4405739ef92d22db6c4c2ec636dc8",
            "ac4e9c99cc728c718a300ce621199c2404879d20100afcc124e6f4501c35c431",
        ],
    },
    "kimi": {
        "plain": "0fb5a6fc370ec069d419c1b83b1815ae728f183655187f8453e78ecd521140e1",
        "catalog": "e3cdb5980beae351aee397460f283cdf7fbc33663aec17242cdb91f11371fa11",
        "upgrade": [
            "30edb6fbe977d6668bc783f7156fe07de4f0a9084e45c283d771665e50ee0373",
            "383a09b43bea2ff2640a67360500ff8931921e64195e33e6a5ff97150aa39c28",
            "c7f7fb58f3a9643f5984167a2f4e87b0e0a23b5bb8cb03c08e631bd610249c03",
        ],
    },
}


@pytest.mark.verifies("model-source-discovery:Placement is source data")
def test_connected_cli_digests_are_unchanged(tmp_path):
    """Byte for byte, so no connected CLI source is asked to reconnect."""
    from tinyassets.providers.subscription_lease import upgrade_connection
    from tinyassets.providers.subscription_source import digest

    shipped = records(strict=True)
    assert set(_MAIN_DIGESTS) == {key for key, r in shipped.items() if "cli" in r.invoke}
    for key, expected in _MAIN_DIGESTS.items():
        record = shipped[key]
        assert digest(record) == expected["plain"]
        assert digest({"source": record, "connection": "x"}) == expected["catalog"]
        for legacy in expected["upgrade"]:  # A marker from before custody or the lock pin.
            runtime = SimpleNamespace(source=record, directory=tmp_path)
            marker = {"source": legacy}
            upgrade_connection(runtime, marker)
            assert marker["source"] == expected["plain"]
            assert json.loads((tmp_path / "connection").read_text())["source"] == (
                expected["plain"])


@pytest.mark.verifies("model-source-discovery:Placement is source data")
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


@pytest.mark.verifies("model-source-discovery:Placement is source data")
def test_placement_branching_only_falls_and_has_one_resolver():
    shape = check_shapes.SHAPES["S29"]
    assert check_shapes.old_count(shape, check_shapes.ROOT) <= shape.ceiling
    assert re.search(check_shapes.ENTRY_SYMBOLS["S29"], "def resolve_placement(a, b, c):")
    assert check_shapes.registered_path("tinyassets/providers/placement.py", shape.paths[0])
