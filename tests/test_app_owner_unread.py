"""Execute shipped receipt and public-run controllers at their browser boundaries."""
from pathlib import Path

import pytest

from tests.test_app_browser_notifications import run_js
from tinyassets.onboarding import render_app_html


def unread_source():
    html, _ = render_app_html()
    return html[html.index("  const OwnerUnread={"):html.index("  const RequestSheets = {")]


HARNESS = """
const calls=[],paint=[],MCP={_loginEpoch:1};let queueScope='home';
const token=()=> 'token',authHeaders=()=>({Authorization:'Bearer token'});
const document={visibilityState:'visible'},cloudState={mode:'open'},innerHeight=700;
const visible={serverTurn:{speaker:'universe',id:2},
 getBoundingClientRect:()=>({top:50,bottom:150,height:100})};
const offscreen={serverTurn:{speaker:'universe',id:4},
 getBoundingClientRect:()=>({top:-200,bottom:-100,height:100})};
const nodes={'thread':{children:[visible,offscreen],
 getBoundingClientRect:()=>({top:20,bottom:600})},'chat-cloud-badge':{},
 'needs-you-open':{},'bubble-needs-you':{}};
const $=id=>nodes[id],setCloudUnread=count=>paint.push(count);
let responseHook=()=>{};
const fetch=async(url,options)=>{calls.push({url,options});responseHook();
 return {ok:true,json:async()=>({messages:1,asks:0})};};
"""


@pytest.mark.parametrize("hidden,bubble,expected", [(False, False, ["2"]),
                                                   (True, False, []), (False, True, [])])
def test_only_visible_messages_are_acknowledged(hidden, bubble, expected):
    out = run_js(HARNESS + unread_source() + f"""
document.visibilityState={'"hidden"' if hidden else '"visible"'};
cloudState.mode={'"bubble"' if bubble else '"open"'};
(async()=>{{await OwnerUnread.refresh();console.log(JSON.stringify(calls));}})();
""")
    assert len(out) == 1
    import json

    sent = json.loads(out[0]["options"].get("body", "{}"))
    assert sent.get("messages", []) == expected
    assert out[0]["options"]["method"] == ("POST" if expected else "GET")


def test_old_account_result_cannot_repaint_counts_or_record_receipts():
    out = run_js(HARNESS + unread_source() + """
responseHook=()=>{MCP._loginEpoch++;queueScope='another-home';};
(async()=>{await OwnerUnread.refresh();
 console.log(JSON.stringify({paint,seen:Array.from(OwnerUnread.seen)}));})();
""")
    assert out == {"paint": [0], "seen": []}


def test_viewing_inbox_sends_exact_ids_and_never_an_approval():
    out = run_js(HARNESS + unread_source() + """
cloudState.mode='bubble';OwnerUnread.syncScope();OwnerUnread.viewedAsks.add('ask_one');
(async()=>{await OwnerUnread.refresh();await OwnerUnread.refresh();
 console.log(JSON.stringify(calls));})();
""")
    import json

    assert json.loads(out[0]["options"]["body"]) == {
        "universe": "home", "messages": [], "asks": ["ask_one"]}
    assert out[1]["options"]["method"] == "GET"
    assert all("/app/unread" in call["url"] for call in out)


def test_public_run_resumes_without_model_connection_and_opens_normal_approval():
    source = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")
    method = source[source.index("    async resumePublicRun("):source.index(
        "    // One read of the viewer's own row.")]
    out = run_js("""
const calls=[],storage=new Map([['ta_public_run','agent_listing']]);
const sessionStorage={getItem:k=>storage.get(k),removeItem:k=>storage.delete(k)};
const queueScope='new-owner-home',MCP={_loginEpoch:3,callTool:async(name,args)=>{
 calls.push({name,args});return {request_id:'install-ask'};}};
const openInstallRequest=async id=>calls.push({approval:id});
const AppUI={enabled:false,
""" + method + """};
(async()=>{await AppUI.resumePublicRun(queueScope);await AppUI.resumePublicRun(queueScope);
 console.log(JSON.stringify(calls));})();
""")
    assert out == [{"name": "write_graph", "args": {
        "target": "connection", "operation": "try_package", "graph_id": "new-owner-home",
        "payload_json": '{"agent_definition_id":"agent_listing"}'}}, {"approval": "install-ask"}]
