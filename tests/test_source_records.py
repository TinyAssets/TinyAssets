"""One data contract, including owner declarations and mechanism-only consumers."""
import json
from pathlib import Path

import jsonschema
import pytest

from tests.test_model_source_detector import API, AS, CATALOGUE, fetcher, server
from tinyassets.connection_oauth.model_discovery import discover
from tinyassets.providers.source_record import SourceRecord, records, resolve, suggested_address


def test_declared_and_detected_records_share_schema():
    schema = json.loads(Path("tinyassets/providers/source_record.schema.json").read_text())
    fetch = fetcher({API + "/v1/models": (200, CATALOGUE)})
    detected = discover(API, fetch=fetch)
    assert isinstance(detected, SourceRecord) and detected.origin == "detected"
    assert detected.sign_in == {"api_key": {"header_style": "bearer", "help_url": API}}
    for record in [detected, *records().values()]:
        jsonschema.validate(record.document(), schema)
        assert SourceRecord.parse(record.document()) == record
    assert resolve(suggested_address()).suggested


def test_oauth_record_freezes_endpoints_and_egress():
    fetch = fetcher({
        API + "/v1/models": (200, CATALOGUE),
        API + "/.well-known/oauth-protected-resource": (
            200, {"resource": API, "authorization_servers": [AS]}),
        AS + "/.well-known/oauth-authorization-server": server(
            registration_endpoint=AS + "/register"),
    })
    record = discover(API, fetch=fetch)
    oauth = record.sign_in["oauth"]
    assert oauth["client_strategy"] == "dcr" and oauth["pkce"] == "S256"
    assert oauth["token_endpoint"] == AS + "/token"
    assert record.egress == {"provider": ["api.provider.example", "login.provider.example"]}
    document = record.document()
    document["sign_in"]["oauth"]["token_endpoint"] = "https://changed.example/token"
    with pytest.raises(ValueError, match="egress"):
        SourceRecord.parse(document)


def test_every_alias_resolves_without_network_and_suffixes_do_not():
    def forbidden(*args, **kwargs):
        raise AssertionError("a declared address must not probe the network")

    for record in records().values():
        for address in record.addresses:
            assert discover(address, fetch=forbidden) == record
            assert resolve(address.removeprefix("https://") + "/") == record
            host, _, path = address.removeprefix("https://").partition("/")
            assert resolve("https://" + host.upper() + ":443/" + path) == record
    assert resolve("https://not-claude.ai") is None
    assert resolve("https://claude.ai.evil.example") is None


def test_owner_can_add_same_schema_file_and_cannot_change_custody(tmp_path):
    doc = discover(API, fetch=fetcher({API + "/v1/models": (200, CATALOGUE)})).document()
    doc.update(id="owner-source", origin="declared")
    directory = tmp_path / ".ta-cli" / "sources"
    directory.mkdir(parents=True)
    (directory / "owner-source.json").write_text(json.dumps(doc))
    assert resolve(API, tmp_path).id == "owner-source"
    doc["state"] = {"location": "owner_box", "paths": ["token"]}
    with pytest.raises(ValueError, match="custody"):
        SourceRecord.parse(doc)


def test_page_connect_controllers_have_no_provider_names():
    from tests.test_model_source_detector import PROVIDERS

    source = Path("tinyassets/onboarding/app/main.js").read_text("utf-8")
    for start, end in [("  const SubscriptionSources={", "\n  };"),
                       ("  const ModelDiscovery={", "\n  };")]:
        block = source[source.index(start):source.index(end, source.index(start))].lower()
        for name in PROVIDERS:
            assert name not in block


def test_malformed_record_does_not_hide_other_sources(tmp_path):
    directory = tmp_path / ".ta-cli/sources"
    directory.mkdir(parents=True)
    before = records(tmp_path)
    (directory / "broken.json").write_text("{")
    assert records(tmp_path) == before
    ident = next(iter(before))
    (directory / (ident + ".json")).write_text("{")
    assert ident not in records(tmp_path)
    assert len(records(tmp_path)) == len(before) - 1
