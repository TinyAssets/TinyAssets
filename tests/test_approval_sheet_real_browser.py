"""Whole app sheet/inbox routing and account fencing in real Chromium."""

import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url
from tests.test_app_chat_cloud_browser import browser as _browser

app_url = _app_url
browser = _browser
pytestmark = pytest.mark.real_browser


def test_electron_sheet_opens_browser_handoff_only_after_click(app_url, browser):
    context = browser.new_context(user_agent="TinyAssets Electron/43.4.1")
    page = context.new_page()
    launched = []
    page.route("**/app/approval-handoff", lambda route: (
        launched.append(route.request.post_data_json),
        route.fulfill(json={"launch_path": "/app/approval-handoff/" + "x" * 43}),
    ))
    _enter_chat(page, app_url)
    page.evaluate("""() => {
      window.handoffs=[];window.open=url=>window.handoffs.push(url);
      renderRail([{request_id:'connect', title:'Connect TikTok', sticky:true,
                   action:{type:'connect'}, fields:[]}]);
    }""")
    assert page.locator("#request-rail").is_hidden()
    assert launched == []
    page.evaluate("RequestSheets.inbox()")
    page.locator("#needs-you-items button").click()
    page.wait_for_function("() => window.handoffs.length === 1")
    assert launched == [{"request_id": "connect", "client": "desktop"}]
    assert page.evaluate("window.handoffs[0]").endswith("/app/approval-handoff/" + "x" * 43)
    assert page.locator("#request-rail").is_hidden()
    context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_notification_link_waits_for_its_request_then_focuses_its_item(app_url, browser, width):
    page = browser.new_page(viewport={"width": width, "height": 844})
    _enter_chat(page, app_url)
    page.evaluate("""() => {
      railLink='notification-request';railDeepItem='second';
      renderRail([]);
    }""")
    assert page.evaluate("railLink") == "notification-request"
    assert page.locator("#request-rail").is_hidden()
    page.evaluate("""() => renderRail([{request_id:'notification-request',title:'Today',
      items:[{item_id:'first',title:'First'},{item_id:'second',title:'Second'}]}])""")
    assert page.locator("#request-rail").evaluate('el=>el.matches(":modal")')
    assert page.evaluate("railOpen") == "notification-request"
    assert page.evaluate("document.activeElement.id") == "item_notification-request::second"
    assert page.evaluate("railLink===null && railDeepItem===null")
    page.locator("#request-sheet-close").click()
    page.evaluate("renderRail(railCache)")
    assert page.locator("#request-rail").is_hidden()
    page.close()


def test_notification_item_does_not_focus_a_different_request(app_url, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _enter_chat(page, app_url)
    page.evaluate("""() => {
      railLink='linked';railDeepItem='shared-id';
      renderRail([{request_id:'other',sticky:true,title:'Other',
        items:[{item_id:'shared-id',title:'Unrelated'}]}]);
    }""")
    assert page.evaluate("railDeepItem") == "shared-id"
    assert page.evaluate("document.activeElement.id") != "item_other::shared-id"
    page.evaluate("renderRail([{request_id:'linked',title:'Linked',items:[]}])")
    assert page.evaluate("railOpen") == "linked"
    assert page.evaluate("railLink===null && railDeepItem===null")
    page.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_foreground_sheet_scopes_inbox_history_and_account_fence(app_url, browser, width, tmp_path):
    page = browser.new_page(viewport={"width": width, "height": 844})
    _enter_chat(page, app_url)
    page.evaluate("""() => {
      window.calls=[];window.rows=[];window.receipts=[];
      token=()=>'test-owner';readInflight=()=>({message:'Send my update'});
      Owner.listRequests=async()=>({pending:rows,recently_answered:receipts});
      window.row={request_id:'bound-1',status:'pending',revision:1,agent:'main',
        title:'POST https://api.example.com/message',action_sha256:'hash',
        destination:'https://api.example.com/message',draft:'Exactly this message',
        expires_at:1999999999,action:{type:'approve_action',envelope:{subject:{turn:'live'}}}};
      const previous=fetch;
      window.fetch=async(url,options)=>{
        if(!String(url).startsWith('/app/approvals/'))return previous(url,options);
        const payload=JSON.parse(options.body);calls.push({url,payload});
        if(url.endsWith('/decide')){
          rows=[];receipts=[{...row,status:'answered'}];
          return Response.json({...row,status:'answered',phase:'confirmed',result:{status:200}});
        }
        return Response.json({...row,scope:payload.scope,approval_token:'token-'+payload.scope,
          predicate:{scope:payload.scope,action_class:'app.write',operation:'POST',
            origin:'https://api.example.com',expires_at:1999999999}});
      };
      rows=[row];renderRail(rows);
    }""")
    sheet = page.locator("#request-rail")
    assert sheet.evaluate('el=>el.matches(":modal")')
    assert page.get_by_role("textbox", name="Action draft").input_value() == "Exactly this message"
    box = sheet.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert box["y"] >= 0 and box["y"] + box["height"] <= 844
    page.get_by_role("combobox", name="Approval scope").select_option("site")
    page.get_by_role("button", name="Allow for this site", exact=True).click()
    page.wait_for_function("() => rows.length===0 && !document.getElementById('request-rail').open")
    payload = page.evaluate("calls.at(-1).payload")
    assert payload["scope"] == "site"
    assert payload["approval_token"] == "token-site"
    assert payload["expected_revision"] == 1
    assert payload["decision"] == "approve"
    assert page.evaluate("calls.length") == 3
    page.locator("#needs-you-open").click()
    page.get_by_role("button", name="Answered history", exact=True).click()
    assert "answered" in page.locator("#request-history").inner_text()
    history = page.locator("#request-history")
    assert history.locator("button").all_text_contents() == ["Ask again"]
    assert history.locator("input,textarea,select,a,[role=button]").count() == 0
    page.evaluate("() => { window.relays=[];sendTurn=(...args)=>relays.push(args); }")
    history.get_by_role("button", name="Ask again", exact=True).click()
    assert page.evaluate("calls.length") == 3  # No answer, grant, or approval replay.
    assert page.evaluate("rows") == []
    assert page.evaluate("receipts[0].status") == "answered"
    assert page.evaluate("relays.length") == 1
    assert "request_id=bound-1" in page.evaluate("relays[0][0]")
    assert page.evaluate("relays[0][2].agentId") == "main"
    assert not sheet.is_visible()
    page.evaluate("""() => {
      readInflight=()=>null;
      rows=[{...row,request_id:'away',draft:'Private away draft'}];renderRail(rows);
    }""")
    assert not sheet.is_visible()
    page.locator("#needs-you-open").click()
    assert page.locator("#needs-you-items button").count() == 1
    assert page.locator("#needs-you-items button").inner_text() == "Action approval \u00b7 main"
    page.locator("#needs-you-items button").click()
    assert sheet.evaluate('el=>el.matches(":modal")')
    page.get_by_role("textbox", name="Action draft").fill("Private unsent edit")
    page.screenshot(path=tmp_path / f"approval-sheet-{width}.png")
    page.evaluate("clearRailCards()")
    assert not sheet.is_visible()
    assert page.locator("#rail-items textarea").count() == 0
    assert page.locator("#needs-you-items").inner_text() == ""
    page.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_settings_connection_shapes_clear_staged_secrets_and_label_accounts(
    app_url, browser, width
):
    page = browser.new_page(viewport={"width": width, "height": 844})
    _enter_chat(page, app_url)
    page.evaluate("""() => {
      showView('account');engineConnected=false;window.deposits=[];
      MCP.connectHTTP=async(...args)=>{deposits.push(args);return {status:'provisioned'};};
    }""")
    page.locator("#settings-connect").click()
    assert page.locator("#request-rail").evaluate('el=>el.matches(":modal")')
    page.locator("#http-destination").fill("example")
    page.locator("#http-account-label").fill("personal")
    page.locator("#http-host").fill("api.example.com")
    page.locator("#http-path").fill("/messages")
    page.locator("#http-secret").fill("staged-personal-secret")
    page.locator("#http-auth-scheme").select_option("oauth")
    assert page.locator("#http-secret").input_value() == ""
    assert not page.locator("#http-secret-field").is_visible()
    page.locator("#http-auth-scheme").select_option("bearer")
    page.locator("#http-secret").fill("personal-secret")
    page.locator("#btn-connect-http").click()
    page.wait_for_function("() => deposits.length===1")
    assert page.evaluate("deposits[0][0]") == "example:personal"
    page.locator("#http-destination").fill("example")
    page.locator("#http-account-label").fill("work")
    page.locator("#http-host").fill("api.example.com")
    page.locator("#http-path").fill("/messages")
    page.locator("#http-secret").fill("work-secret")
    page.locator("#btn-connect-http").click()
    page.wait_for_function("() => deposits.length===2")
    assert page.evaluate("deposits.map(d=>d[0])") == ["example:personal", "example:work"]
    assert page.locator("#http-secret").input_value() == ""
    assert "personal-secret" not in page.locator("#thread").inner_text()
    assert "work-secret" not in page.locator("#thread").inner_text()
    page.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_agent_connection_answer_uses_server_continuation_without_chat_relay(
    app_url, browser, width
):
    page = browser.new_page(viewport={"width": width, "height": 844})
    _enter_chat(page, app_url)
    page.evaluate("""() => {
      token=()=>'owner';window.answers=[];window.relays=[];
      window.asks=[{request_id:'service-ask',kind:'API',title:'Connect work account',
        body:'Send the update through your work account.',agent:'main',status:'pending',
        server_continuation:true,fields:[{name:'secret',label:'API key',type:'secret'}],
        action:{type:'connect',destination:'service:work'}}];
      Owner.listRequests=async()=>({pending:asks,recently_answered:[]});
      MCP.answerRequest=async payload=>{
        answers.push(payload);asks=[];return {status:'answered',server_continuation:true};
      };
      sendTurn=(...args)=>relays.push(args);
      renderRail(asks,{foregroundAgent:'main'});
    }""")
    assert page.locator("#request-rail").evaluate('el=>el.matches(":modal")')
    page.locator("#f_service-ask_secret").fill("secret-for-vault-only")
    page.get_by_role("button", name="Accept", exact=True).click()
    page.wait_for_function(
        "() => answers.length===1 && !document.getElementById('request-rail').open"
    )
    assert page.evaluate("answers[0].values.secret") == "secret-for-vault-only"
    assert page.evaluate("relays.length") == 0
    assert page.locator("#f_service-ask_secret").count() == 0
    assert "secret-for-vault-only" not in page.locator("#thread").inner_text()
    page.close()
