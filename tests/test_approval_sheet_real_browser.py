"""Whole app sheet/inbox routing and account fencing in real Chromium."""

import pytest

from tests.test_app_chat_cloud_browser import _enter_chat
from tests.test_app_chat_cloud_browser import app_url as _app_url
from tests.test_app_chat_cloud_browser import browser as _browser

app_url = _app_url
browser = _browser
pytestmark = pytest.mark.real_browser


@pytest.mark.verifies('owner-daemon-in-box:T6')
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
      MCP.answerRequest=async()=>{throw new Error('credential sent to box');};
      ConnectOAuth.post=async(op,payload)=>{
        answers.push({op,payload});asks=[];return {status:'provisioned'};
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
    assert page.evaluate("answers[0].payload.secret") == "secret-for-vault-only"
    assert page.evaluate("relays.length") == 0
    assert page.locator("#f_service-ask_secret").count() == 0
    assert "secret-for-vault-only" not in page.locator("#thread").inner_text()
    page.close()
