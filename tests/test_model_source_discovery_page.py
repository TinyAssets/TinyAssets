"""Exercise the isolated discovery entry in a real, network-blocked browser."""
import json
import re
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.real_browser,
    pytest.mark.verifies(
        'model-connect-by-discovery:Address-based model connection',
        'model-connect-by-discovery:Expired approval recovery',
    ),
]


@pytest.mark.parametrize("width", [390, 1280])
def test_discovery_page_fallback_and_secret_clear(width):
    from playwright.sync_api import sync_playwright

    source = Path("tinyassets/onboarding/app/main.js").read_text("utf-8")
    source = source[source.index("  const ModelDiscovery={"):
                    source.index("  // ---- End unified address connection ----")]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 850})
        page.route("**/*", lambda route: route.abort())
        html = Path("tinyassets/onboarding/app.html").read_text("utf-8")
        styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
        page.set_content('<style>' + styles + '</style><div id="connect-panel" '
                         'class="connect-panel"><button>Existing sources</button></div>')
        page.add_script_tag(content="""
          const $=id=>document.getElementById(id), NATIVE=false, DESKTOP_SHELL=false;
          const ConnectedAI={load(){}};
      const CFG={model_source_address:"https://known.example"};
          const MCP={_loginEpoch:1};
          const authHeaders=()=>({});
          window.fetch=async(url,options)=>{
            if(url!=="/app/model-discovery")throw new Error("unexpected route");
            return {ok:true,json:async()=>({source:{id:"detected"}})};
          };
          const SubscriptionSources={post:async()=>({}),saved:()=>null};
          const queueScope='center';const randToken=()=> 'v'.repeat(43);
          const challengeFor=async()=> 'c'.repeat(43);
          const HostedModelConnect={paint(){}};const refreshRail=()=>{};
          window.calls=[];
          const ConnectOAuth={post:async(op,payload)=>{
            window.calls.push([op,payload]);
            if(op==='discover')return {status:'api_key_required',
              endpoint:'https://new.example/v1/chat/completions',help_url:'https://new.example/keys',
              definition:{destination:'model-test',endpoints:[]}};
            return {status:'confirmation_required',request:{}};
          }};
        """ + source + "\nModelDiscovery.render($('connect-panel'),true);")
        first = page.locator("#connect-panel > :first-child")
        assert first.get_attribute("id") == "model-discovery"
        page.get_by_label("Provider address").fill("new.example")
        page.get_by_role("button", name="Connect", exact=True).click()
        page.get_by_label("API key", exact=True).wait_for(state="visible")
        assert page.get_by_role("link", name="Get an API key").is_visible()
        page.get_by_label("API key", exact=True).fill("synthetic-key")
        page.get_by_role("button", name="Connect", exact=True).click()
        assert page.get_by_label("API key", exact=True).input_value() == ""
        assert page.evaluate("window.calls[1][0]") == "deposit"
        # Editing the address resets the previous provider's deposit.
        page.get_by_label("Provider address").fill("known.example")
        assert page.get_by_role("button", name="Connect", exact=True).is_visible()
        assert page.get_by_label("API key", exact=True).is_hidden()
        assert page.get_by_label("Provider address").input_value() == "known.example"
        assert "synthetic-key" not in json.dumps(page.content())
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=f".pytest_cache/discovery-{width}.png")
        browser.close()


@pytest.mark.parametrize("error,status,cleared", [
    ("handoff_expired_or_wrong_account", 409, True),
    ("interactive_approval_required", 403, False),
])
def test_stale_approval_handoff_cleans_only_expired_reference(error, status, cleared):
    from playwright.sync_api import sync_playwright

    source = Path("tinyassets/onboarding/app/main.js").read_text("utf-8")
    start = source.index("  const ApprovalHandoff = {")
    controller = source[start:source.index("\n  };", start) + 6]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/*", lambda route: route.fulfill(body="<body></body>",
                                                      content_type="text/html"))
        page.goto("https://app.example/app?approval=stale&keep=yes#chat")
        page.add_script_tag(content="const authHeaders=()=>({});" + controller)
        page.route("**/app/approval-handoff?*", lambda route: route.fulfill(
            status=status, content_type="application/json", body=json.dumps({"error": error})))
        result = page.evaluate("async()=>{try{return await ApprovalHandoff.restore();}"
                               "catch(error){return error.message;}}")
        if cleared:
            assert result is False
            assert page.url == "https://app.example/app?keep=yes#chat"
            assert page.evaluate("ApprovalHandoff.ref") is None
        else:
            assert result == error
            assert "approval=stale" in page.url
        browser.close()
