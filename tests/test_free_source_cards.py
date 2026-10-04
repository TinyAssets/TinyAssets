"""Cards reach owned generic connections, discovery and existing model consent."""

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests import test_model_bootstrap as bootstrap
from tinyassets.onboarding.source_connect import connect_source
from tinyassets.providers.free_sources import discovered_agent_models, source_cards, source_preset

rig = bootstrap.rig


@pytest.mark.parametrize("source,base,help_url", [
    ("google_ai_studio", "https://generativelanguage.googleapis.com/v1beta/openai",
     "https://aistudio.google.com/apikey"),
    ("groq", "https://api.groq.com/openai/v1", "https://console.groq.com/keys"),
    ("cerebras", "https://api.cerebras.ai/v1", "https://cloud.cerebras.ai/platform/api-keys"),
    ("mistral", "https://api.mistral.ai/v1", "https://console.mistral.ai/api-keys"),
])
def test_card_urls_and_generic_model_discovery(source, base, help_url):
    card = source_preset(source)
    assert (card["base_url"], card["help_url"]) == (base, help_url)
    assert urlsplit(card["billing_url"]).scheme == "https"
    models = discovered_agent_models(card, {"data": [{"id": card["models"][0]},
                                                     {"id": "unknown-paid-model"}]})
    assert [m["id"] for m in models] == [card["models"][0]]
    with pytest.raises(ValueError, match="no supported"):
        discovered_agent_models(card, {"data": [{"id": "unknown-paid-model"}]})


def test_every_preset_exposes_only_verified_daily_cap_numbers():
    from tinyassets.providers import free_sources

    presets = json.loads(Path(free_sources.__file__).with_name(
        "free_source_presets.json").read_text(encoding="utf-8"))
    cards = {row["id"]: row for row in source_cards() + free_sources.sign_in_cards()}
    available = [row for row in presets if row.get("available", True)]
    assert set(cards) == {row["id"] for row in available}
    for preset in presets:
        cap = preset["daily_cap"]
        assert set(cap) == {"requests_per_day", "tokens_per_minute", "reset_timezone", "source_url"}
        assert urlsplit(cap["source_url"]).scheme == "https"
        assert urlsplit(cap["source_url"]).netloc
        if preset.get("available", True):
            assert cards[preset["id"]]["daily_cap"] == cap
        else:
            assert preset["id"] not in cards
        assert (cap["requests_per_day"], cap["tokens_per_minute"]) == (
            (1000, 8000) if preset["id"] == "groq" else (None, None))
        assert cap["reset_timezone"] == (
            "America/Los_Angeles" if preset["id"] == "google_ai_studio" else None)


def test_cards_do_not_promise_cerebras_is_permanently_free():
    assert "verified payment method" in source_preset("cerebras")["offer"]
    assert "No recurring free tier" in source_preset("cerebras")["offer"]
    assert "monthly" in source_preset("mistral")["offer"]


@pytest.mark.parametrize("source", [card["id"] for card in source_cards()])
def test_card_connects_with_one_key_and_returns_unanswered_consent(rig, monkeypatch, source):
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.providers.definition import list_definitions

    card = source_preset(source)
    reads = []
    def read(**kwargs):
        reads.append(kwargs)
        return {"data": [{"id": card["models"][0]}]}
    monkeypatch.setattr("tinyassets.providers.discovery_http.read_granted_discovery_document", read)
    result = connect_source(base=rig, uid="u-owner", owner="owner", preset=card, key="own-test-key")
    assert result["status"] == "confirmation_required"
    assert "own-test-key" not in json.dumps(result)
    assert load_provider_assignment(rig, universe_id="u-owner") is None
    definition, = list_definitions("u-owner")
    assert definition.access_method == "api_key_http" and definition.protocol == "openai_chat"
    assert reads[0]["url"] == card["base_url"] + "/models"
    assert reads[0]["owner_user_id"] == "owner" and reads[0]["universe_id"] == "u-owner"
    access = result["request"]["action"]["model_access"]
    assert next(iter(access.values()))["cost_caps"] is None


def test_foreign_owner_cannot_deposit_or_read_models(rig, monkeypatch):
    reads = []
    monkeypatch.setattr("tinyassets.providers.discovery_http.read_granted_discovery_document",
                        lambda **kw: reads.append(kw))
    with pytest.raises(PermissionError):
        connect_source(base=rig, uid="u-owner", owner="other", preset=source_preset("groq"),
                       key="foreign-key")
    assert reads == []


def test_shipped_card_controller_has_one_secret_and_preserves_it_only_until_submit():
    from tinyassets.onboarding import render_app_html

    html, _ = render_app_html()
    source = html[html.index("  const FreeSourceCards={"):html.index("  const ConnectShapes={")]
    program = r"""
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.value='';}
  appendChild(c){this.children.push(c);return c;}
  replaceChildren(){this.children=[];}
  setAttribute(){}
  addEventListener(k,v){this.listeners[k]=v;}
}
const host=new Element('div'), $=()=>host, document={createElement:t=>new Element(t)};
const MCP={_loginEpoch:1};let refreshed=0,sent=null,cleared=false;
const refreshRail=()=>{refreshed++;};
const HostedModelConnect={post:async(op,p)=>{
  sent={op,p};cleared=input.value==='';
  return {status:'confirmation_required'};
}};
__SOURCE__
const cards=__CARDS__;
FreeSourceCards.render(cards);
const initial=host.children[0];
const input=initial.children.find(c=>c.tag==='label').children[0];
const button=initial.children.find(c=>c.tag==='button');
input.value='private-own-key';
FreeSourceCards.render(cards);
(async()=>{
  const preserved=host.children[0]===initial&&input.value==='private-own-key';
  await button.listeners.click();
  console.log(JSON.stringify({count:host.children.length,preserved,cleared,refreshed,sent,
    type:input.type,value:input.value,
    status:initial.children[initial.children.length-1].textContent}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    node = shutil.which("node")
    assert node
    result = subprocess.run([node, "-e", program.replace("__SOURCE__", source)
                             .replace("__CARDS__", json.dumps(source_cards()))],
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["count"] == 4 and observed["preserved"] and observed["cleared"]
    assert observed["type"] == "password" and observed["value"] == ""
    assert observed["sent"] == {"op": "deposit_key", "p": {
        "preset_id": "groq", "key": "private-own-key"}}
    assert observed["refreshed"] == 1 and "Confirm model access" in observed["status"]


def test_authenticated_card_ingress_reuses_same_origin_gate(rig, monkeypatch):
    from tests.test_onboarding_model_connect import post
    from tinyassets import onboarding

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io/mcp"})
    monkeypatch.setattr(onboarding, "_read_home", lambda *a, **kw: "u-owner")
    card = source_preset("groq")
    monkeypatch.setattr("tinyassets.providers.discovery_http.read_granted_discovery_document",
                        lambda **kw: {"data": [{"id": card["models"][0]}]})
    document = {"preset_id": "groq", "key": "my-private-key"}
    denied = post("deposit_key", document, origin="https://foreign.example")
    assert denied.status_code == 403
    response = post("deposit_key", document)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "confirmation_required"
    assert "my-private-key" not in response.text


def test_key_cards_are_ordered_by_daily_allowance_and_one_reader_serves_caps():
    """Lead 2026-10-02: Groq (largest daily count) and Gemini first; HF is not a key card."""
    from tinyassets.providers.free_sources import daily_cap_for_host

    assert [c["id"] for c in source_cards()] == ["groq", "google_ai_studio", "mistral", "cerebras"]
    assert daily_cap_for_host("openrouter.ai") == {
        "requests_per_day": 50, "credit_requests_per_day": 1000, "reset_timezone": "UTC",
        "credit_amount": "$10",
        "name": "OpenRouter", "credit_url": "https://openrouter.ai/settings/credits"}
    assert daily_cap_for_host("api.groq.com") == {
        "requests_per_day": 1000, "credit_requests_per_day": None, "reset_timezone": None,
        "name": "Groq", "credit_url": "https://console.groq.com/settings/billing"}
    assert daily_cap_for_host("router.huggingface.co")["requests_per_day"] is None
    assert daily_cap_for_host("unknown.example") is None
