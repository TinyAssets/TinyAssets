"""Exercise the isolated discovery entry in a real, network-blocked browser."""
import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.parametrize("width", [390, 1280])
def test_discovery_page_fallback_and_secret_clear(width):
    source = Path("tinyassets/onboarding/app/main.js").read_text("utf-8")
    source = source[source.index("  const ModelDiscovery={"):
                    source.index("  // ---- End Lane X: address discovery ----")]
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
          const CFG={model_discovery_addresses:[{name:'Known source',address:'known.example'}]};
          const queueScope='center';const randToken=()=> 'v'.repeat(43);
          const challengeFor=async()=> 'c'.repeat(43);
          const HostedModelConnect={paint(){}};const refreshRail=()=>{};
          window.calls=[];
          const ConnectOAuth={post:async(op,payload)=>{
            window.calls.push([op,payload]);
            if(op==='discover')return {status:'api_key_required',
              endpoint:'https://new.example/v1/chat/completions',
              definition:{destination:'model-test',endpoints:[]}};
            return {status:'confirmation_required',request:{}};
          }};
        """ + source + "\nModelDiscovery.render($('connect-panel'),true);")
        first = page.locator("#connect-panel > :first-child")
        assert first.get_attribute("id") == "model-discovery"
        page.get_by_label("Provider address").fill("new.example")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.get_by_label("API key", exact=True).wait_for(state="visible")
        assert "https://new.example/v1/chat/completions" in page.get_by_role("status").inner_text()
        page.get_by_label("API key", exact=True).fill("synthetic-key")
        page.get_by_role("button", name="Use an API key", exact=True).click()
        assert page.get_by_label("API key", exact=True).input_value() == ""
        assert page.evaluate("window.calls[1][0]") == "deposit"
        # A prefilled provider choice resets the previous provider's deposit.
        page.get_by_role("button", name="Known source", exact=True).click()
        assert page.get_by_role("button", name="Continue", exact=True).is_visible()
        assert page.get_by_label("API key", exact=True).is_hidden()
        assert page.get_by_label("Provider address").input_value() == "known.example"
        assert "synthetic-key" not in json.dumps(page.content())
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=f".pytest_cache/discovery-{width}.png")
        browser.close()
