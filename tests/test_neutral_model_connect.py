"""Registry completeness and real page-controller source switching."""
import json
from pathlib import Path

import pytest

from tests.test_app_browser_notifications import run_js
from tests.test_onboarding_app import _js_function
from tinyassets.api.pending_requests import _model_connect_request
from tinyassets.onboarding import render_app_html
from tinyassets.providers import free_sources


@pytest.mark.parametrize("connected", [False, True])
def test_setup_lists_every_available_registry_source_without_primary(connected):
    request = _model_connect_request(connected=connected)
    setup = request["action"]["setup"]
    installed = json.loads(Path(free_sources.__file__).with_name(
        "acquisition_presets.json").read_text("utf-8"))
    assert "primary" not in setup
    assert {s["preset_id"] for s in setup["hosted_sources"]} == {
        key for key, row in installed.items() if row.get("available", True)}
    assert {s["id"] for s in setup["sources"] + setup["sign_in_sources"]} == {
        row["id"] for row in free_sources._SOURCES if row.get("available", True)}
    assert "api_key" in setup["shapes"]
    assert request["sticky"] is not connected
    assert request["status"] == ("optional" if connected else "pending")


def test_new_registry_sign_in_is_listed_without_page_changes(monkeypatch):
    monkeypatch.setattr(free_sources, "_SOURCES", [{
        "id": "unanticipated", "name": "New subscription", "offer": "Your plan",
        "sign_in": {"label": "Sign in"}, "billing_note": "Your account"}])
    sources = _model_connect_request()["action"]["setup"]["sign_in_sources"]
    assert sources[0]["id"] == "unanticipated"


DOM = r"""
function el(){return {hidden:false,disabled:false,textContent:'',value:'',dataset:{},children:[],
 setAttribute(){},removeAttribute(){},focus(){},appendChild(c){this.children.push(c);},
 replaceChildren(){this.children=[];},addEventListener(k,f){this['on'+k]=f;}};}
const nodes={},$=id=>nodes[id]||(nodes[id]=el());
const document={createElement:el},NATIVE=false,MCP={_loginEpoch:1};
const sessionStorage={removeItem(){},getItem(){return null;}};
const events=[],record={connectionFlow:'old-flow',modelConfirmation:{request_id:'old-confirm'}};
const InlineConnection={record,current:r=>r===record,save(){},waiting:()=>false};
function refreshRail(){}
"""


def source():
    html, _ = render_app_html()
    a = html.index("  const HostedModelConnect=")
    b = html.index("  // End hosted model connection controller.", a)
    return DOM + html[a:b]


def test_another_source_cancels_stale_flow_before_starting_and_ignores_old_confirmation():
    result = run_js(source() + r"""
(async()=>{
 HostedModelConnect.setup='recovery';
 HostedModelConnect.request={request_id:'old-confirm'};
 HostedModelConnect.post=async(op,data)=>{events.push([op,data]);return {status:'cancelled'};};
 HostedModelConnect.begin=async()=>events.push(['begin',HostedModelConnect.preset]);
 HostedModelConnect.renderSources([{preset_id:'new_source',name:'New source',label:'Connect'}]);
 await $('connect-hosted-sources').children[0].children[1].onclick();
 HostedModelConnect.adopt({request_id:'old-confirm'});
 console.log(JSON.stringify({events,flow:record.connectionFlow,request:HostedModelConnect.request,
 preset:HostedModelConnect.preset}));
})().catch(e=>{console.error(e);process.exit(1);});
""")
    assert result["events"] == [["inline_cancel", {"flow": "old-flow"}], ["begin", "new_source"]]
    assert result["flow"] is None
    assert result["request"] is None
    assert result["preset"] == "new_source"


def test_cancel_failure_keeps_old_flow_and_does_not_start_new_source():
    result = run_js(source() + r"""
(async()=>{
 HostedModelConnect.setup='recovery';
 HostedModelConnect.post=async()=>{throw new Error('offline');};
 HostedModelConnect.begin=async()=>events.push('started');
 HostedModelConnect.renderSources([{preset_id:'new_source',name:'New source',label:'Connect'}]);
 await $('connect-hosted-sources').children[0].children[1].onclick();
 console.log(JSON.stringify({events,flow:record.connectionFlow,status:$('hosted-model-status').textContent}));
})();
""")
    assert result == {"events": [], "flow": "old-flow", "status": "offline"}


def test_recovery_renders_full_list_and_generic_endpoint():
    html, _ = render_app_html()
    script = source() + _js_function(html, "connectBody") + r"""
let seen=[];
const SignInSourceCards={render:s=>seen.push(['sign_in',s])};
const FreeSourceCards={render:s=>seen.push(['keys',s])};
const ConnectShapes={render:(s,open)=>seen.push(['shapes',s,open])};
HostedModelConnect.setup='recovery';
connectBody({sticky:true,action:{setup:{hosted_sources:[{preset_id:'a',name:'A',label:'Connect'}],
 sources:[{id:'b',name:'B'}],sign_in_sources:[{id:'c'}],shapes:['api_key','local']}}});
console.log(JSON.stringify({seen,hosted:$('connect-hosted-sources').children.length,
 keysHidden:$('connect-paste').hidden,startOver:$('btn-hosted-start-over').hidden}));
"""
    result = run_js(script)
    assert result["hosted"] == 1
    assert result["keysHidden"] is False
    assert result["startOver"] is False
    assert result["seen"][-1] == ["shapes", ["api_key", "local"], True]


def test_default_layout_is_centered_wide_and_fills_phone():
    from tests.test_app_chat_cloud import call

    wide = call("cloudDefaultState(false,{w:2400,h:1000})")["open"]
    assert wide["x"] == (2400 - wide["w"]) / 2
    assert wide["w"] >= 900
    phone = call("cloudDefaultState(false,{w:390,h:844})")["open"]
    assert phone == {"x": 0, "y": 0, "w": 390, "h": 844}


def test_automatic_policy_uses_strongest_connected_source():
    from tests.test_model_policy import connection, model, order
    from tinyassets.providers.model_policy import Scores

    low = Scores("comparable-v1", "fresh", agentic=10)
    high = Scores("comparable-v1", "fresh", agentic=90)
    result = order([connection("one", models=[model("a", score=low)]),
                    connection("two", models=[model("b", score=high)])])
    assert result.candidates[0].ref.connection_id == "two"


def test_every_installed_hosted_preset_is_listed(monkeypatch):
    path = Path(free_sources.__file__).with_name("acquisition_presets.json")
    docs = json.loads(path.read_text("utf-8"))
    extra = dict(next(iter(docs.values())), display_name="Another source")
    docs["another_source"] = extra
    original = Path.read_text

    def read(candidate, *args, **kwargs):
        if candidate.resolve() == path.resolve():
            return json.dumps(docs)
        return original(candidate, *args, **kwargs)

    from tinyassets.onboarding import hosted_model_auth

    discovery = dict(hosted_model_auth.bundled_discovery_documents())
    discovery["another_source"] = next(iter(discovery.values()))
    monkeypatch.setattr(hosted_model_auth, "bundled_discovery_documents", lambda: discovery)
    monkeypatch.setattr(Path, "read_text", read)
    cards = _model_connect_request()["action"]["setup"]["hosted_sources"]
    assert {card["preset_id"] for card in cards} == set(docs)


def test_late_inline_poll_cannot_restore_abandoned_confirmation():
    html, _ = render_app_html()
    a = html.index("  const InlineConnection=")
    b = html.index("  const HostedModelConnect=", a)
    script = source().replace(
        "const InlineConnection={record,current:r=>r===record,save(){},waiting:()=>false};",
        html[a:b])
    result = run_js(script + r"""
(async()=>{
 InlineConnection.record=record;InlineConnection.current=r=>r===record;
 InlineConnection.waiting=()=>false;InlineConnection.save=()=>{};InlineConnection.schedule=()=>{};
 InlineConnection.note={};InlineConnection.connect={};InlineConnection.cancel={};
 record.modelConfirmation=null;
 let resolvePoll;
 HostedModelConnect.post=async op=>op==='inline_poll'
   ?new Promise(resolve=>resolvePoll=resolve):{status:'cancelled'};
 const polling=InlineConnection.check();
 await HostedModelConnect.startOver();
 resolvePoll({status:'confirmation_required',request_id:'old-confirm',
   request:{request_id:'old-confirm',action:{type:'bind_model_access'}}});
 await polling;
 console.log(JSON.stringify({flow:record.connectionFlow,confirmation:record.modelConfirmation}));
})().catch(e=>{console.error(e);process.exit(1);});
""")
    assert result == {"flow": None, "confirmation": None}
