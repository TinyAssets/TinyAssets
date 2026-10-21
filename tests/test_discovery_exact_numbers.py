"""Exact discovery transport through the real scoped broker with synthetic wires."""

import json
from copy import deepcopy
from decimal import Decimal

import pytest

from tests.test_discovery_catalogue_shapes import (
    BENCHMARK,
    CATALOGUE,
    CONNECTION,
    NOW,
    PRICES,
)
from tests.test_discovery_http import rig as rig
from tinyassets.exceptions import ProviderProtocolError, ProviderUnavailableError
from tinyassets.providers.discovery_catalogue import BenchmarkShape, CatalogueShape, PriceFields


def numeric_prices(encoding="number"):
    document = deepcopy(PRICES)
    for field in document["fields"].values():
        field["encoding"] = encoding
    return document


def test_exact_array_catalogue_transport_to_compiled_prices_preserves_ledger(rig):
    before_grant = rig.ledger.get_grant("grant-discovery")
    before_view = rig.ledger.get_connection_view("conn-discovery")
    rig.response["body"] = """[{
      "identity":{"key":"new-provider/model-2027"},
      "features":{"in":["text"],"out":["text"],"functions":true},
      "limits":[64000,32000],
      "tariff":{"input":1.234567,"output":2.5,"call":0}
    }]"""
    parsed = rig.read(json_mode="exact")
    assert type(parsed[0]["tariff"]["input"]) is Decimal
    assert parsed[0]["tariff"]["input"] == Decimal("1.234567")
    spec = {key: value for key, value in CATALOGUE.items()
            if key not in {"count", "next_page", "default_model"}}
    spec["rows"] = ""
    result = CatalogueShape.compile(spec, numeric_prices()).decode(parsed, connection=CONNECTION)
    model = result.models[0]
    assert model.model_id == "new-provider/model-2027"
    assert model.context_tokens == 32000 and model.tools is True
    assert {charge.component: charge.amount_micros for charge in model.pricing.charges} == {
        "input_million_tokens_usd": 1234567,
        "output_million_tokens_usd": 2500000, "request_usd": 0,
    }
    assert not result.owner_filtered and not result.executor_tools
    assert result.authenticated_account_id is None and result.source_kind == "http"
    assert rig.ledger.get_grant("grant-discovery") == before_grant
    assert rig.ledger.get_connection_view("conn-discovery") == before_view
    assert rig.definition.model == "unchanged-legacy-pin"
    assert len(rig.calls) == 1 and rig.closes == 1


def test_submicro_charge_and_high_precision_token_do_not_round_to_free(rig):
    rig.response["body"] = '{"input":0.000000000000000000000000000001,"output":0,"call":0}'
    values = rig.read(json_mode="exact")
    assert values["input"] == Decimal("0.000000000000000000000000000001")
    pricing = PriceFields.compile(numeric_prices()).decode(values, "fresh")
    assert "input_million_tokens_usd" in pricing.unknown_components
    assert all(charge.component != "input_million_tokens_usd" for charge in pricing.charges)


def test_new_benchmark_uses_exact_numeric_tokens_and_legacy_stays_float(rig):
    from datetime import timedelta

    rig.response["body"] = """{"measured_at":"2026-09-11T00:00:00Z","evaluations":[{
      "subject":"model-key","measurement":{
        "source":"independent-lab/schema-v3","agent_score":14.250,"reason_score":22.5
      }}]}"""
    exact = rig.read(json_mode="exact")
    legacy = rig.read()
    assert type(exact["evaluations"][0]["measurement"]["agent_score"]) is Decimal
    assert type(legacy["evaluations"][0]["measurement"]["agent_score"]) is float
    score = BenchmarkShape.compile(BENCHMARK).decode(
        exact, now=NOW, max_age=timedelta(days=1),
    )["model-key"]
    assert (score.agentic, score.general, score.freshness) == (14250, 22500, "fresh")


@pytest.mark.parametrize("encoding,value,expected", [
    ("string", "0.25", 250000), ("string", 0, None), ("string", Decimal("0.25"), None),
    ("number", 0, 0), ("number", Decimal("0.25"), 250000), ("number", "0.25", None),
    ("either", "0.25", 250000), ("either", Decimal("0.25"), 250000),
    ("number", 0.25, None), ("either", 0.25, None), ("number", True, None),
    ("number", Decimal("NaN"), None), ("number", Decimal("Infinity"), None),
    ("number", Decimal("-1"), None), ("number", Decimal("1E+100"), None),
])
def test_declared_price_encoding_never_accepts_binary_float_money(encoding, value, expected):
    pricing = PriceFields.compile(numeric_prices(encoding)).decode(
        {"input": value, "output": value, "call": value}, "fresh",
    )
    if expected is None:
        assert not pricing.charges and len(pricing.unknown_components) == 3
    else:
        assert not pricing.unknown_components
        assert all(charge.amount_micros == expected for charge in pricing.charges)


@pytest.mark.parametrize("encoding", [None, False, 1, [], {}, "", "float", "callback"])
def test_invalid_price_encoding_cannot_be_compiled(encoding):
    with pytest.raises(ValueError):
        PriceFields.compile(numeric_prices(encoding))


@pytest.mark.parametrize("mode", [None, True, [], {}, "float", ""])
def test_invalid_json_mode_refuses_before_network(rig, mode):
    with pytest.raises(ValueError, match="invalid discovery JSON mode"):
        rig.read(json_mode=mode)
    assert not rig.starts and not rig.calls


@pytest.mark.parametrize("body", [
    '{"price":NaN}', '[{"price":Infinity}]', '[{"x":1,"x":2}]',
    '{"price":1e999999999999999999999999999}', 'null', '0', '"scalar"', 'true', '[bad]',
])
def test_exact_mode_rejects_invalid_documents_after_closing_proxy(rig, body):
    rig.response["body"] = body
    with pytest.raises(ProviderProtocolError) as error:
        rig.read(json_mode="exact")
    assert str(error.value) == "discovery response is not a bounded JSON document"
    assert rig.closes == 1


def test_array_root_is_not_silently_activated_for_legacy_callers(rig):
    rig.response["body"] = "[]"
    with pytest.raises(ProviderProtocolError, match="bounded JSON object"):
        rig.read()
    assert rig.read(json_mode="exact") == []


@pytest.mark.parametrize("change", ["owner", "grant", "endpoint"])
def test_exact_parsing_mode_does_not_widen_existing_authority(rig, change):
    args = {"json_mode": "exact"}
    if change == "owner":
        args["owner_user_id"] = "someone-else"
    elif change == "grant":
        rig.ledger.revoke_grant("grant-discovery")
    else:
        args["url"] = "https://another.example.com/foreign"
    if change == "endpoint":
        assert rig.read(**args) == {"data": []}
        assert rig.auth == ["none"]
        assert rig.calls == [("GET", {"url": args["url"]})]
    else:
        with pytest.raises(ProviderUnavailableError):
            rig.read(**args)
        assert not rig.starts and not rig.calls


def test_numeric_override_maxima_and_unknown_fees_remain_conservative(rig):
    rig.response["body"] = """{"input":0,"output":0,"call":0,
      "tiers":[{"input":0.25,"when":"peak"}],"unknown_extra_fee":0}"""
    pricing = PriceFields.compile(numeric_prices()).decode(rig.read(json_mode="exact"), "fresh")
    assert pricing.unknown_components == frozenset({"unknown_extra_fee"})
    assert dict((charge.component, charge.amount_micros) for charge in pricing.charges)[
        "input_million_tokens_usd"
    ] == 250000


def test_unrecoverable_float_rounding_is_not_reinterpreted_as_exact_money():
    raw = '{"input":0.00000099999999999999999999,"output":0,"call":0}'
    exact = json.loads(raw, parse_float=Decimal)
    rounded = json.loads(raw)
    shape = PriceFields.compile(numeric_prices())
    assert shape.decode(exact, "fresh").unknown_components
    assert shape.decode(rounded, "fresh").unknown_components


@pytest.mark.parametrize("encoding", ["string", "either"])
@pytest.mark.parametrize("value", [
    "0__0", "_0", "0e_1", "0_1", " 0", "0 ", "+0", ".0", "0.", "00", "٠",
    "0\n", "1,000", "", "NaN", "Infinity",
])
def test_new_price_strings_require_complete_ascii_decimal_syntax(encoding, value):
    shape = PriceFields.compile(numeric_prices(encoding))
    pricing = shape.decode({"input": value, "output": value, "call": value}, "fresh")
    assert not pricing.charges and len(pricing.unknown_components) == 3


@pytest.mark.parametrize("value", ["0", "-0", "0.25", "2.5e-1", "25E-2", "0e+1"])
def test_new_price_strings_accept_exact_decimal_tokens(value):
    shape = PriceFields.compile(numeric_prices("either"))
    pricing = shape.decode({"input": value, "output": value, "call": value}, "fresh")
    assert not pricing.unknown_components
    assert all(charge.amount_micros == int(Decimal(value) * 1000000)
               for charge in pricing.charges)


@pytest.mark.parametrize("value", ["0__0", "_0", "0e_1", " 0", "+0", ".0", "00", "٠"])
def test_legacy_price_presets_keep_their_original_decimal_interpretation(value):
    shape = PriceFields.compile(PRICES, legacy=True)
    pricing = shape.decode({"input": value, "output": value, "call": value}, "fresh")
    assert not pricing.unknown_components
    assert all(charge.amount_micros == 0 for charge in pricing.charges)


def test_new_benchmark_strings_share_strict_syntax_without_changing_legacy():
    from datetime import timedelta

    payload = {"measured_at": "2026-09-11T00:00:00Z", "evaluations": [{
        "subject": "model-key", "measurement": {
            "source": "independent-lab/schema-v3", "agent_score": "0__0",
            "reason_score": "0e_1",
        },
    }]}
    scores = BenchmarkShape.compile(BENCHMARK).decode(
        payload, now=NOW, max_age=timedelta(days=1),
    )["model-key"]
    assert scores.agentic is None and scores.general is None
    legacy = BenchmarkShape.compile(BENCHMARK, legacy=True).decode(
        payload, now=NOW, max_age=timedelta(days=1),
    )["model-key"]
    assert legacy.agentic == 0 and legacy.general == 0
