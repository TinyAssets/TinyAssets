"""The shipped inline approval controller in real Chromium, including phone layout."""

import json

import pytest

from tinyassets.onboarding import render_app_html

pytestmark = pytest.mark.real_browser

CARD = {
    "request_id": "req-1",
    "revision": 1,
    "action_sha256": "bound-hash",
    "approval_token": "private-token",
    "status": "pending",
    "phase": "pending",
    "title": "POST https://api.example.com/messages",
    "destination": "https://api.example.com/messages",
    "draft": "Keep these exact bytes\n",
    "agent": "researcher",
    "expires_at": 1999999999,
    "action": {"type": "approve_action"},
}


@pytest.fixture
def page():
    sync = pytest.importorskip(
        "playwright.sync_api", reason="owner=onboarding; runs-in=real-browser-proof"
    )
    html, _ = render_app_html()
    start = html.index("  // Begin protected inline approval cards.")
    end = html.index("  // End protected inline approval cards.")
    source = html[start:end]
    style = html[html.index("<style") : html.index("</style>") + len("</style>")]
    with sync.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        tab = browser.new_page(viewport={"width": 390, "height": 844})
        tab.set_content(f"""<!doctype html><html><head>{style}</head><body>
          <div id="thread"></div><aside id="request-rail">
          <h3 id="rail-head">Request history</h3></aside>
          <script>
          const $=id=>document.getElementById(id);
          const MCP={{_loginEpoch:1}}; function token(){{return 'bearer';}}
          window.calls=[];window.card={json.dumps(CARD)};window.fail=false;
          window.fetch=async(url,options)=>{{
            const payload=JSON.parse(options.body);
            window.calls.push({{url,payload}});
            if(window.fail)return {{ok:false,
              json:async()=>({{error:'interactive_approval_required'}})}};
            let result={{...window.card}};
            if(url.endsWith('/edit')){{window.card={{...result,draft:payload.draft,revision:2,approval_token:'new-token'}};result=window.card;}}
            if(url.endsWith('/decide'))result={{...result,status:'answered',phase:'confirmed',result:{{status:200}}}};
            return {{ok:true,json:async()=>result}};
          }};
          {source}
          window.mount=()=>document.getElementById('thread').appendChild(InlineApprovals.build(window.card));
          window.historyRows=rows=>InlineApprovals.history(rows);
          window.mount();
          </script></body></html>""")
        yield tab
        browser.close()


def test_phone_card_previews_edits_and_executes_without_a_chat_relay(page):
    approve = page.get_by_role("button", name="Allow once")
    approve.wait_for(state="visible")
    assert approve.is_enabled()
    draft = page.get_by_role("textbox", name="Action draft")
    assert draft.input_value() == CARD["draft"]
    assert page.get_by_text("Requested by researcher", exact=False).is_visible()
    draft.fill("Edited\n")
    assert approve.is_disabled()
    page.get_by_role("button", name="Preview edit").click()
    assert approve.is_enabled()
    approve.click()
    assert approve.is_disabled()
    calls = page.evaluate("window.calls")
    assert [c["url"] for c in calls] == [
        "/app/approvals/preview",
        "/app/approvals/edit",
        "/app/approvals/decide",
    ]
    assert calls[-1]["payload"]["approval_token"] == "new-token"
    assert calls[-1]["payload"]["expected_revision"] == 2
    assert draft.bounding_box()["width"] <= 390


def test_failed_preview_keeps_draft_and_offers_protected_sign_in(page):
    draft = page.get_by_role("textbox", name="Action draft")
    draft.fill("Keep my unsent draft")
    page.evaluate("window.fail=true")
    page.get_by_role("button", name="Review / Try again").click()
    assert page.get_by_role("link", name="Sign in to approve").is_visible()
    assert draft.input_value() == "Keep my unsent draft"
    assert page.get_by_role("button", name="Allow once").is_disabled()


@pytest.mark.parametrize("failure", [
    {"error": "provider_authority_denied", "detail": "Try again", "request_pending": True},
    {"error": "request_invalid", "detail": "Ask for the correct fields"},
])
def test_owner_answer_preserves_structured_refusals_for_existing_consumers(page, failure):
    result = page.evaluate("""async failure => {
      window.fetch=async()=>({ok:false,status:404,json:async()=>failure});
      return InlineApprovals.post('answer',{request_id:'req-1',values:{}});
    }""", failure)
    assert result == failure


@pytest.mark.parametrize("operation", ["answer", "preview", "decide"])
def test_missing_owner_proof_still_throws_sign_in_error(page, operation):
    result = page.evaluate("""async operation => {
      window.fail=true;
      try { await InlineApprovals.post(operation,{request_id:'req-1'}); return null; }
      catch(error) { return {signIn:error.signIn,message:error.message}; }
    }""", operation)
    assert result == {"signIn": True, "message": "interactive_approval_required"}


def test_history_is_read_only(page):
    page.evaluate("window.historyRows([{title:'Sent message',status:'answered'}])")
    page.evaluate("document.getElementById('request-history').hidden=false")
    rail = page.locator("#request-rail")
    assert "Request history" in rail.inner_text()
    assert rail.locator("button,input,textarea").count() == 0


@pytest.mark.parametrize("operation", ["answer", "preview", "decide"])
def test_unauthenticated_response_preserves_auth_required(page, operation):
    result = page.evaluate("""async operation => {
      window.fetch=async()=>({ok:false,status:401,json:async()=>({error:'authentication_required'})});
      try { await InlineApprovals.post(operation,{}); return null; }
      catch(error) { return {authRequired:error.authRequired}; }
    }""", operation)
    assert result == {"authRequired": True}


def test_model_answer_displays_protected_sign_in_and_keeps_request(page):
    html, _ = render_app_html()
    start = html.index("    async answer(accepted){")
    end = html.index("    wire(){", start)
    page.evaluate("""source => {
      document.body.insertAdjacentHTML('beforeend','<div id="hosted-model-status"></div>');
      window.model={epoch:1,busy:false,request:{request_id:'model-request'},
        paint(){},status(text){document.getElementById('hosted-model-status').textContent=text;}};
      Object.assign(window.model,eval('({' + source + '})'));
      MCP.answerRequest=()=>InlineApprovals.post('answer',{});
      window.fail=true;
    }""", html[start:end])
    page.evaluate("window.model.answer(true)")
    assert page.get_by_role("link", name="Sign in to approve").is_visible()
    link = page.get_by_role("link", name="Sign in to approve")
    assert link.get_attribute("href") == "/app/owner-sign-in"
    assert page.evaluate("window.model.request.request_id") == "model-request"
    assert page.evaluate("window.model.busy") is False


def test_stale_preview_disables_effects_but_allows_skip(page):
    page.evaluate("window.card.approval_unavailable='This task stopped or expired'")
    page.get_by_role("button", name="Review / Try again").click()
    assert page.get_by_text("This task stopped or expired", exact=True).is_visible()
    assert page.get_by_role("button", name="Allow once").is_disabled()
    assert page.get_by_role("button", name="Preview edit").is_disabled()
    page.get_by_role("textbox", name="Action draft").fill("Even an edit cannot reenable approval")
    assert page.get_by_role("button", name="Allow once").is_disabled()
    page.get_by_role("button", name="Skip", exact=True).click()
    assert page.evaluate("window.calls.at(-1).payload.decision") == "skip"
