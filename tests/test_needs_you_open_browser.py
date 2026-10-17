"""Needs you clicks in the served app, including native browser handoffs."""

import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as app_url_fixture
from tests.test_app_chat_cloud_browser import browser as browser_fixture

app_url = app_url_fixture
browser = browser_fixture
pytestmark = pytest.mark.real_browser

KINDS = ["approve_action", "connect_browser", "setup", "optional_setup", "model", "folded",
         "question", "patch", "patch_recommendation", "optional", "notification"]


def _prepare(page, app_url, kind):
    _enter_chat(page, app_url)
    page.route("**/app/browser-login", lambda route: route.fulfill(json={"blocked": True}))
    page.evaluate("""kind => {
      token=()=>'test-owner';window.answers=[];
      window.ask={request_id:'ask',title:'Review this request',status:'pending',fields:[],
        action:{type:'answer'}};
      if(kind==='approve_action')Object.assign(ask,{revision:1,action_sha256:'hash',
        draft:'Exact draft',destination:'https://example.com',action:{type:kind}});
      if(kind==='connect_browser')ask.action={type:kind,connection_id:'browser-1'};
      if(kind==='model'||kind==='folded')ask.action={type:'bind_model_access'};
      if(kind==='patch')ask.action={type:'grant_patch_intake'};
      if(kind==='patch_recommendation')ask.title='Patch recommendation';
      if(kind==='notification')ask.action={type:'notify'};
      if(kind==='optional')ask.status='optional';
      window.setup={request_id:CONNECT_REQUEST_ID,title:'Connect your model',sticky:true,
        status:'pending',fields:[],action:{type:'connect',setup:{shapes:[]}}};
      if(kind==='setup'||kind==='optional_setup')ask=setup;
      if(kind==='optional_setup')ask.status='optional';
      window.rows=kind==='folded'?[setup,ask]:[ask];
      Owner.listRequests=async()=>({pending:rows,recently_answered:[]});
      MCP.answerRequest=async payload=>{answers.push(payload);rows=[];return {status:'answered'};};
      MCP.callTool=async(name,payload)=>{
        answers.push(payload);rows=[];return {status:'withdrawn'};};
      const previous=fetch;
      window.fetch=async(url,options)=>{
        if(!String(url).startsWith('/app/approvals/'))return previous(url,options);
        const payload=JSON.parse(options.body);
        if(url.endsWith('/answer')||url.endsWith('/decide')){
          answers.push(payload);rows=[];return Response.json({status:'answered',phase:'confirmed'});
        }
        return Response.json({...ask,approval_token:'protected-token',scope:'once'});
      };
      renderRail(rows);
      document.getElementById('request-rail').close();railOpen=null;RequestSheets.selected=null;
    }""", kind)


def _click_request(page, kind):
    if kind in ("optional", "optional_setup"):
        # Optional offers are deliberately absent from Needs you; other entry points open them.
        page.evaluate("RequestSheets.open(ask.request_id)")
    else:
        page.locator("#needs-you-open").click()
        page.locator("#needs-you-items button").click()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("missing_node", [False, True])
def test_web_request_types_open_and_remain_actionable(app_url, browser, kind, missing_node):
    from playwright.sync_api import expect

    page = browser.new_page()
    _prepare(page, app_url, kind)
    if missing_node:
        page.evaluate("""() => {
          const panel=document.getElementById('connect-panel');
          document.getElementById('request-rail').appendChild(panel);panel.hidden=true;
          for(const entry of railNodes.values())entry.node.remove();railNodes.clear();
        }""")
    _click_request(page, kind)
    sheet = page.locator("#request-rail")
    expect(sheet).to_be_visible(timeout=2000)
    assert not page.locator("#needs-you").is_visible()
    if kind in ("setup", "optional_setup", "folded"):
        expect(sheet.locator("#connect-panel")).to_be_visible()
        if kind == "folded":
            assert page.locator("#btn-hosted-resume").is_visible()
    elif kind == "connect_browser":
        expect(sheet.get_by_role("button", name="Retry sign-in")).to_be_visible()
    elif kind == "approve_action":
        sheet.get_by_role("button", name="Allow once", exact=True).click()
        page.wait_for_function("() => answers.length===1")
        assert page.evaluate("answers[0].decision") == "approve"
        assert page.evaluate("answers[0].approval_token") == "protected-token"
    elif kind == "notification":
        sheet.get_by_role("button", name="Dismiss", exact=True).click()
        page.wait_for_function("() => answers.length===1")
        assert page.evaluate("answers[0].operation") == "withdraw_request"
    else:
        sheet.get_by_role("button", name="Accept", exact=True).click()
        page.wait_for_function("() => answers.length===1")
        assert page.evaluate("answers[0]") == {"request_id": "ask", "values": {}}
        expect(sheet).to_be_hidden()
        page.evaluate("rows=[ask];renderRail(rows)")
        _click_request(page, kind)
        sheet.get_by_role("button", name="Clear", exact=True).click()
        page.wait_for_function("() => answers.length===2")
        assert page.evaluate("answers[1]") == {"request_id": "ask", "dismiss": True}
    page.close()


def _native_context(browser, platform, failure=False):
    context = browser.new_context(
        user_agent="TinyAssets Electron/43.7.9" if platform == "desktop" else None
    )
    context.add_init_script("""(() => {
      window.launches=[];window.failLaunch=FAILURE;
      const launch=async url=>{
        launches.push(url);if(failLaunch)throw Error('Browser launch failed');};
      if(PLATFORM==='desktop')window.tinyassetsDesktop={openExternal:launch,onAppReturn:()=>{}};
      else window.Capacitor={isNativePlatform:()=>true,getPlatform:()=>PLATFORM,Plugins:{
        App:{getInfo:async()=>({id:'io.tinyassets.app'}),addListener:()=>{},getLaunchUrl:async()=>({})},
        Browser:{open:async({url})=>launch(url),close:async()=>{}}
      }};
    })();""".replace("PLATFORM", repr(platform)).replace("FAILURE", str(failure).lower()))
    return context


@pytest.mark.parametrize("platform", ["android", "ios", "desktop"])
@pytest.mark.parametrize("kind", KINDS)
def test_native_request_types_open_in_app_or_keep_visible_handoff(app_url, browser, platform, kind):
    from playwright.sync_api import expect

    context = _native_context(browser, platform)
    page = context.new_page()
    handoffs = []
    page.route("**/app/approval-handoff", lambda route: (
        handoffs.append(route.request.post_data_json),
        route.fulfill(json={"launch_path": "/app/approval-handoff/" + "r" * 43}),
    ))
    _prepare(page, app_url, kind)
    assert handoffs == []  # Polls/foreground setup must never launch the system browser.
    _click_request(page, kind)
    sheet = page.locator("#request-rail")
    if kind in ("question", "patch_recommendation", "optional", "notification"):
        expect(sheet).to_be_visible(timeout=2000)
        assert handoffs == []
        assert page.evaluate("launches") == []
        if kind == "notification":
            sheet.get_by_role("button", name="Dismiss", exact=True).click()
            page.wait_for_function("() => answers.length===1")
            assert page.evaluate("answers[0].operation") == "withdraw_request"
        else:
            sheet.get_by_role("button", name="Accept", exact=True).click()
            page.wait_for_function("() => answers.length===1")
            assert page.evaluate("answers[0]") == {"request_id": "ask", "values": {}}
            expect(sheet).to_be_hidden()
            page.evaluate("rows=[ask];renderRail(rows)")
            _click_request(page, kind)
            sheet.get_by_role("button", name="Clear", exact=True).click()
            page.wait_for_function("() => answers.length===2")
            assert page.evaluate("answers[1]") == {"request_id": "ask", "dismiss": True}
        expect(sheet).to_be_hidden()
        assert page.evaluate("launches") == []
        context.close()
        return
    page.wait_for_function("() => launches.length===1")
    expect(sheet).to_be_visible(timeout=2000)
    link = sheet.get_by_role("link", name="Open request in browser")
    expect(link).to_be_visible()
    assert link.get_attribute("href").endswith("/app/approval-handoff/" + "r" * 43)
    expected_id = "sys_connect_model" if kind in ("setup", "optional_setup", "folded") else "ask"
    assert handoffs == [{"request_id": expected_id,
                         "client": platform}]
    assert sheet.locator("input:visible,textarea:visible,select:visible").count() == 0
    assert page.evaluate("answers") == []  # Native never acquires browser approval authority.
    page.evaluate("renderRail(rows)")
    expect(link).to_be_visible()
    assert len(handoffs) == 1
    expect(sheet.get_by_role("status")).to_have_text("Opened in your browser to finish securely")
    sheet.get_by_role("button", name="Cancel", exact=True).click()
    expect(sheet).to_be_hidden()
    page.evaluate("renderRail(rows)")
    expect(sheet).to_be_hidden()
    assert len(handoffs) == 1
    _click_request(page, kind)
    page.wait_for_function("() => launches.length===2")
    # The OS return is observation only: the authenticated list determines completion.
    assert page.evaluate("handleAppReturn('tinyassets-desktop://auth?completion='+ 'r'.repeat(43))")
    expect(sheet).to_be_visible()
    page.evaluate("rows=[]")
    assert page.evaluate("handleAppReturn('tinyassets-desktop://auth?completion='+ 'r'.repeat(43))")
    expect(sheet).to_be_hidden()
    assert page.evaluate("answers") == []
    context.close()


@pytest.mark.parametrize("platform", ["android", "ios", "desktop"])
@pytest.mark.parametrize("failure", ["launch", "server"])
def test_handoff_failure_is_visible_and_retryable(app_url, browser, platform, failure):
    from playwright.sync_api import expect

    context = _native_context(browser, platform, failure == "launch")
    page = context.new_page()
    page.route("**/app/approval-handoff", lambda route: route.fulfill(
        status=503 if failure == "server" else 200,
        json={"error": "Handoff unavailable"} if failure == "server" else {
            "launch_path": "/app/approval-handoff/" + "r" * 43},
    ))
    _prepare(page, app_url, "approve_action")
    _click_request(page, "approve_action")
    sheet = page.locator("#request-rail")
    expect(sheet).to_be_visible(timeout=2000)
    expect(sheet.get_by_role("alert")).to_contain_text(
        "Handoff unavailable" if failure == "server" else "browser", ignore_case=True)
    if failure == "launch":
        expect(sheet.get_by_role("link", name="Open request in browser")).to_be_visible()
    page.unroute("**/app/approval-handoff")
    page.route("**/app/approval-handoff", lambda route: route.fulfill(
        json={"launch_path": "/app/approval-handoff/" + "s" * 43}))
    page.evaluate("failLaunch=false")
    sheet.get_by_role("button", name="Open again", exact=True).click()
    page.wait_for_function("() => launches.some(url=>url.endsWith('s'.repeat(43)))")
    expect(sheet.get_by_role("link", name="Open request in browser")).to_be_visible()
    context.close()


def test_stale_inbox_click_reports_missing_request(app_url, browser):
    from playwright.sync_api import expect

    page = browser.new_page()
    _prepare(page, app_url, "question")
    page.locator("#needs-you-open").click()
    page.evaluate("railCache=[]")  # Request disappears between listing and clicking.
    page.locator("#needs-you-items button").click()
    expect(page.locator("#request-rail")).to_be_visible(timeout=2000)
    expect(page.locator("#request-rail").get_by_role("alert")).to_contain_text(
        "no longer available")
    page.close()


def test_direct_folded_request_opens_setup_and_question_fields_submit(app_url, browser):
    from playwright.sync_api import expect

    page = browser.new_page()
    _prepare(page, app_url, "folded")
    page.evaluate("RequestSheets.open(ask.request_id)")
    expect(page.locator("#connect-panel")).to_be_visible()
    assert page.evaluate("railOpen") == "sys_connect_model"
    _prepare(page, app_url, "question")
    page.evaluate("""() => {
      ask={...ask,fields:[{name:'answer',label:'Your answer',type:'text'}]};
      rows=[ask];renderRail(rows);
    }""")
    _click_request(page, "question")
    page.locator("#f_ask_answer").fill("Use the smaller patch")
    page.get_by_role("button", name="Accept", exact=True).click()
    page.wait_for_function("() => answers.length===1")
    assert page.evaluate("answers[0].values") == {"answer": "Use the smaller patch"}
    expect(page.locator("#request-rail")).to_be_hidden()
    page.close()


@pytest.mark.parametrize("leave", ["cancel", "account"])
def test_late_handoff_does_not_launch_after_cancel_or_account_change(app_url, browser, leave):
    context = _native_context(browser, "desktop")
    page = context.new_page()
    pending = []
    page.route("**/app/approval-handoff", lambda route: pending.append(route))
    _prepare(page, app_url, "approve_action")
    page.evaluate("""() => {
      const start=ApprovalHandoff.start.bind(ApprovalHandoff);window.handoffSettled=false;
      ApprovalHandoff.start=(...args)=>start(...args).finally(()=>window.handoffSettled=true);
    }""")
    with page.expect_request("**/app/approval-handoff"):
        _click_request(page, "approve_action")
    if leave == "cancel":
        page.get_by_role("button", name="Cancel", exact=True).click()
    else:
        page.evaluate("MCP.endLogin();clearRailCards()")
    pending[0].fulfill(json={"launch_path": "/app/approval-handoff/" + "r" * 43})
    # Draining the response also runs the continuation that might open the browser.
    page.wait_for_function("() => handoffSettled && !document.getElementById('request-rail').open")
    assert page.evaluate("launches") == []
    assert page.evaluate("answers") == []
    context.close()
