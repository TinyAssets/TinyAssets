"""Real Chromium, shipped card, fake transport only. No live key or screenshots."""
from pathlib import Path

import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url

app_url = _app_url
pytestmark = pytest.mark.real_browser

ROOT = Path(__file__).resolve().parents[1]
FAKE = "test-only-not-a-valid-api-key"
HARNESS = """
const $=id=>document.getElementById(id);
let queueOwner='alice',queueScope='home',viewGeneration=1,railOpen='req-test';
const MCP={_loginEpoch:1},RequestSheets={selected:'req-test'},railNodes=new Map();
const authHeaders=()=>({Authorization:'Bearer fake-app-token'});
let refreshed=0; const refreshRail=async()=>{refreshed++};
const req={request_id:'req-test',universe_id:'home'};
let requests=[],existing=null,failCreate=false,refuse=false,pauseCreate=false,release=null;
const action={type:'credential_request',provider:'tinyassets',name:'Muse',scopes:
 ['read_graph','converse','run_graph'].map(capability=>({universe:'home',agent:'main',capability}))};
const fetch=async(url,init)=>{
 const body=JSON.parse(init.body);requests.push({url,...body});
 if(refuse)return {ok:false,status:403};
 let doc={};
 if(body.operation==='request_preview')doc={action,digest:'fake-digest',status:'pending',existing};
 if(body.operation==='request_create'){
  if(pauseCreate)await new Promise(resolve=>{release=resolve});
  if(existing)doc={key_id:'fake-id',already_created:true};
  else {existing={key_id:'fake-id',revoked_at:null};
    doc={key_id:'fake-id',key:'test-only-not-a-valid-api-key'};}
  if(failCreate)throw new Error('connection lost');
 }
 if(body.operation==='request_revoke'){existing.revoked_at=1;doc={revoked:true};}
 if(body.operation==='owner_list')doc={keys:existing?[{...existing,name:'Muse',
   scopes:action.scopes,last_used_at:null}]:[]};
 if(body.operation==='owner_revoke'){existing.revoked_at=1;doc={revoked:true};}
 return {ok:true,status:200,json:async()=>doc};
};
"""


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/*", lambda route: route.fulfill(
            content_type="text/html", body="<dialog id='request-rail'></dialog>"))
        page.goto("https://tinyassets.test/")
        page.add_script_tag(content=HARNESS)
        page.add_script_tag(content=(ROOT / "tinyassets/onboarding/credential_requests.js")
                            .read_text(encoding="utf-8"))
        yield page
        browser.close()


def open_card(page):
    page.evaluate("""() => {
      $('request-rail').showModal();
      $('request-rail').appendChild(CredentialRequests.build(req));
    }""")


def approve(page):
    page.get_by_role("checkbox").check()
    page.get_by_role("button", name="Create API key", exact=True).click()


def test_confirm_copy_done_and_no_secret_in_requests_or_storage(page):
    open_card(page)
    assert page.get_by_role("button", name="Create API key", exact=True).is_disabled()
    assert "Full supported REST access" in page.locator("body").inner_text()
    page.evaluate("navigator.clipboard.writeText=async value=>{window.copied=value}")
    approve(page)
    page.get_by_role("button", name="Copy key", exact=True).click()
    assert page.evaluate("window.copied") == FAKE
    assert page.get_by_label("One-time API key").input_value() == FAKE
    assert page.evaluate("requests.filter(r=>r.operation==='request_create').length") == 1
    assert page.evaluate(
        "requests.find(r=>r.operation==='request_create').expected_digest") == "fake-digest"
    assert FAKE not in page.evaluate("JSON.stringify(requests)")
    assert page.evaluate("localStorage.length+sessionStorage.length") == 0
    page.get_by_role("button", name="Done", exact=True).click()
    assert page.get_by_label("One-time API key").count() == 0
    assert page.evaluate("refreshed") == 1


def test_lost_response_reentry_never_mints_again_and_revoke_confirms(page):
    page.evaluate("failCreate=true")
    open_card(page)
    approve(page)
    assert "Creation is not confirmed" in page.get_by_role("status").inner_text()
    assert page.get_by_role("button", name="Create API key", exact=True).is_disabled()
    page.get_by_role("button", name="Check status", exact=True).click()
    assert "already exists" in page.get_by_role("status").inner_text()
    assert page.get_by_label("One-time API key").input_value() == ""
    page.get_by_role("button", name="Revoke key", exact=True).click()
    page.get_by_role("button", name="Keep key", exact=True).click()
    assert page.evaluate("requests.filter(r=>r.operation==='request_revoke').length") == 0
    page.get_by_role("button", name="Revoke key", exact=True).click()
    page.get_by_role("button", name="Confirm revoke", exact=True).click()
    assert "Key revoked" in page.get_by_role("status").inner_text()
    assert page.evaluate("requests.filter(r=>r.operation==='request_create').length") == 1


def test_double_click_close_during_create_and_reopen(page):
    page.evaluate("pauseCreate=true")
    open_card(page)
    page.get_by_role("checkbox").check()
    page.get_by_role("button", name="Create API key", exact=True).dblclick()
    assert page.evaluate("requests.filter(r=>r.operation==='request_create').length") == 1
    page.evaluate("$('request-rail').close()")
    page.wait_for_function("CredentialRequests.cards.size===0")
    page.evaluate("release()")
    open_card(page)
    page.wait_for_function("document.body.textContent.includes('already exists')")
    assert page.get_by_label("One-time API key").input_value() == ""


@pytest.mark.parametrize("change", ["MCP._loginEpoch++", "queueOwner='bob'",
                                    "queueScope='other'", "viewGeneration++"])
def test_late_create_result_cannot_paint_another_context(page, change):
    page.evaluate("pauseCreate=true")
    open_card(page)
    approve(page)
    page.evaluate(change + "; release()")
    assert page.get_by_label("One-time API key").input_value() == ""
    assert page.get_by_role("button", name="Copy key", exact=True).is_hidden()


def test_cancel_and_interrupted_login_are_read_only(page):
    page.evaluate("refuse=true")
    open_card(page)
    assert page.get_by_role("link", name="Sign in to review this request").is_visible()
    assert page.get_by_role("button", name="Create API key", exact=True).is_disabled()
    page.get_by_role("button", name="Not now", exact=True).click()
    page.wait_for_function("CredentialRequests.cards.size===0")
    page.evaluate("refuse=false")
    open_card(page)
    assert not page.get_by_role("checkbox").is_checked()
    assert page.evaluate("requests.every(r=>r.operation==='request_preview')")


def test_close_clears_secret_even_without_done(page):
    open_card(page)
    approve(page)
    page.evaluate("$('request-rail').close()")
    page.wait_for_function("CredentialRequests.cards.size===0")
    assert page.get_by_label("One-time API key").count() == 0
    open_card(page)
    assert page.get_by_label("One-time API key").input_value() == ""


def test_decline_does_not_create_key(page):
    open_card(page)
    page.get_by_role("button", name="Decline request", exact=True).click()
    assert page.evaluate("requests.filter(r=>r.operation==='request_decline').length") == 1
    assert page.evaluate("requests.filter(r=>r.operation==='request_create').length") == 0


def test_account_list_revoke_requires_confirmation(page):
    page.evaluate("""() => {
      document.body.insertAdjacentHTML('beforeend',
        '<div id="api-keys-list"></div><p id="api-keys-status"></p>');
      existing={key_id:'fake-id',revoked_at:null};
      CredentialRequests.list();
    }""")
    page.get_by_role("button", name="Revoke Muse", exact=True).click()
    page.get_by_role("button", name="Keep key", exact=True).click()
    assert page.evaluate("requests.filter(r=>r.operation==='owner_revoke').length") == 0
    page.get_by_role("button", name="Revoke Muse", exact=True).click()
    page.get_by_role("button", name="Confirm revoke", exact=True).dblclick()
    assert page.evaluate("requests.filter(r=>r.operation==='owner_revoke').length") == 1
    assert "Revoked" in page.locator("#api-keys-list").inner_text()


@pytest.mark.parametrize("width", [390, 1280])
def test_card_is_reachable_in_real_needs_you_and_poll_preserves_secret(app_url, width):
    import json

    from playwright.sync_api import expect, sync_playwright

    from tests.test_credential_requests import action

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 844})
        _enter_chat(page, app_url)
        calls = []

        def respond(route):
            body = route.request.post_data_json
            calls.append(body)
            doc = ({"key": FAKE, "key_id": "fake-id"} if body["operation"] == "request_create"
                   else {"action": action(), "digest": "fake-digest", "status": "pending",
                         "existing": None})
            route.fulfill(content_type="application/json", body=json.dumps(doc))

        page.route("**/app/api-keys", respond)
        page.evaluate("""action => {
          setQueueScope('home');token=()=> 'test-session';
          window.testAsk={request_id:'req-test',universe_id:'home',kind:'API key',
            title:'Create API key for Muse',action,fields:[],status:'pending',agent:'main'};
          Owner.listRequests=async()=>({pending:[testAsk],recently_answered:[]});
          renderRail([testAsk]);
        }""", action())
        page.locator("#needs-you-open").click()
        page.get_by_role("button", name="Create API key for Muse", exact=False).click()
        expect(page.get_by_text("Full supported REST access.", exact=False)).to_be_visible()
        approve(page)
        expect(page.get_by_label("One-time API key")).to_have_value(FAKE)
        page.evaluate("refreshRail()")
        expect(page.get_by_label("One-time API key")).to_have_value(FAKE)
        assert page.locator("#request-rail").evaluate(
            "el=>el.scrollWidth<=el.clientWidth+1")
        assert FAKE not in page.evaluate("JSON.stringify(railCache)")
        assert len([c for c in calls if c["operation"] == "request_create"]) == 1
        # Reopen before the queued close event runs: the old key is gone,
        # and that stale event must not destroy the newly built card.
        page.evaluate("$('request-rail').close();RequestSheets.open(testAsk.request_id)")
        expect(page.get_by_label("One-time API key")).to_have_value("")
        expect(page.get_by_role("checkbox")).to_be_visible()
        page.locator("#request-sheet-close").click()
        expect(page.get_by_label("One-time API key")).to_have_count(0)
        browser.close()


@pytest.mark.real_browser
@pytest.mark.parametrize("stage,status", [
    ("refresh", 200), ("response", 200), ("response", 401), ("response", 404),
])
def test_door_deposit_stays_with_original_login_and_center(app_url, stage, status):
    """The shipped door transport fences refresh and never replays a deposit."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        calls = []
        switch_login = """() => {
          MCP.endLogin();token=()=> 'next-token';queueScope='next-center';
          MCP.sessionId='next-login-session';
          if(releaseRefresh)releaseRefresh();
        }"""

        def deposit(route):
            calls.append({"body": route.request.post_data_json,
                          "authorization": route.request.headers.get("authorization")})
            # Hold the response until the next account owns the page.
            page.evaluate(switch_login)
            route.fulfill(status=status, json={"status":"provisioned"})

        page.route("**/app/model-connect/deposit", deposit)
        _enter_chat(page, app_url)
        page.evaluate("""stage => {
          token=()=> 'original-token';queueScope='original-center';
          window.releaseRefresh=null;window.depositOutcome=null;
          ensureFreshToken=stage==='refresh'
            ?()=>new Promise(resolve=>{releaseRefresh=resolve;}) :async()=>{};
          MCP.connectHTTP('service','test-only-secret',[
            {host:'api.example.com',path_template:'/v1',methods:['GET']}],'bearer')
            .then(value=>{depositOutcome={value};},error=>{
              depositOutcome={error:error.transport||error.message};});
        }""", stage)
        if stage == "refresh":
            page.wait_for_function("() => releaseRefresh!==null")
            page.evaluate(switch_login)
        page.wait_for_function("() => depositOutcome!==null")
        assert page.evaluate("depositOutcome") == {"error": "login_changed"}
        assert page.evaluate("MCP.sessionId") == "next-login-session"
        if stage == "refresh":
            assert calls == []
        else:
            assert len(calls) == 1
            assert calls[0]["authorization"] == "Bearer original-token"
            assert calls[0]["body"]["center"] == "original-center"
            assert calls[0]["body"]["secret"] == "test-only-secret"
        browser.close()


def test_fieldless_connection_poll_preserves_values_and_deposits_only_at_door(app_url):
    import json

    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        _enter_chat(page, app_url)
        deposits = []
        sent = []
        page.on("request", lambda request: sent.append((request.url, request.post_data or "")))

        def deposit(route):
            deposits.append(route.request.post_data_json)
            route.fulfill(json={"status": "provisioned"})

        page.route("**/app/model-connect/deposit", deposit)
        page.evaluate("""() => {
          setQueueScope('home');token=()=> 'test-session';
          window.connectAsk={request_id:'four-fields',universe_id:'home',title:'Connect service',
            status:'pending',server_continuation:true,fields:[],action:{type:'connect',
              destination:'service',auth_scheme:'oauth1a',endpoints:[{
                host:'api.example.com',path_template:'/v1',methods:['GET']}]}};
          Owner.listRequests=async()=>({universe_id:'home',pending:[structuredClone(connectAsk)]});
          renderRail([structuredClone(connectAsk)]);RequestSheets.open('four-fields');
        }""")
        values = {name: "test-only-" + name for name in (
            "api_key", "api_secret", "access_token", "access_token_secret")}
        for name, value in values.items():
            page.locator("#f_four-fields_" + name).fill(value)
        page.evaluate("refreshRail()")
        assert page.evaluate("railCache[0].fields") == []
        for name, value in values.items():
            expect(page.locator("#f_four-fields_" + name)).to_have_value(value)
        with page.expect_response("**/app/model-connect/deposit"):
            page.get_by_role("button", name="Accept", exact=True).click()
        assert len(deposits) == 1
        assert json.loads(deposits[0]["secret"]) == values
        assert deposits[0]["request_ref"] == "four-fields"
        assert deposits[0]["center"] == "home"
        secret_requests = [(url, body) for url, body in sent if "test-only-api_key" in body]
        assert len(secret_requests) == 1
        assert secret_requests[0][0].endswith("/app/model-connect/deposit")
        browser.close()
