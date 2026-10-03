"""Two surfaces: default geometry and keyboard handoff using shipped functions."""
from tests.test_app_browser_notifications import functions, run_js
from tests.test_app_chat_cloud import call


def test_medium_geometry():
    assert call('cloudDefaultState(false,{w:1280,h:800})')["open"] == {
        "x": 828, "y": 168, "w": 440, "h": 620}
    assert call('cloudDefaultState(false,{w:390,h:700})')["open"] == {
        "x": 0, "y": 0, "w": 390, "h": 700}


DOM = """
let focused='', messages=[];
const frame={setAttribute(k,v){this[k]=v},focus(){focused='frame'},
 contentWindow:{focus(){focused='frame'},postMessage(m,origin){messages.push([m,origin])}}};
const nodes={'ui-frame':frame,'ui-frame-host':{hidden:false},
 'cc-blank':{focus(){focused='blank'}}, 'chat-stage':{}, 'composer-input':{tagName:'TEXTAREA'},
 'cloud-menu':{hidden:true}};
const $=id=>nodes[id]; const document={body:{}};
"""


def test_focus_frame_or_blank():
    out = run_js(functions('focusCommandCenter') + DOM + """
focusCommandCenter(); const first=focused;
delete nodes['ui-frame']; focusCommandCenter();
console.log(JSON.stringify({first,focused,tabindex:frame.tabindex,messages}));
""")
    assert out == {"first": "frame", "focused": "blank", "tabindex": "0",
                   "messages": [[{"ta_ui": 1, "type": "focus"}, "*"]]}


def test_forward_only_from_unclaimed_focus():
    out = run_js(functions('isTypingTarget', 'forwardCommandCenterKey') + DOM + """
const e={key:'ArrowRight',code:'ArrowRight',shiftKey:true,altKey:false,
 ctrlKey:false,metaKey:true,repeat:true,preventDefault(){}};
for(const target of [document.body,nodes['chat-stage'],nodes['cc-blank']]){
 for(const type of ['keydown','keyup']) forwardCommandCenterKey({...e,target,type});
}
forwardCommandCenterKey({...e,target:nodes['composer-input'],type:'keydown'});
nodes['ui-frame-host'].hidden=true;
forwardCommandCenterKey({...e,target:document.body,type:'keydown'});
console.log(JSON.stringify(messages));
""")
    assert len(out) == 6
    for i, message in enumerate(out):
        assert message == [{"ta_ui": 1, "type": "key", "key": "ArrowRight",
                            "code": "ArrowRight", "shiftKey": True, "altKey": False,
                            "ctrlKey": False, "metaKey": True, "repeat": True,
                            "phase": "up" if i % 2 else "down"}, "*"]


def test_typing_targets():
    out = run_js(functions('isTypingTarget') + """
const excluded=['button','submit','checkbox','radio','range','file','color','reset','image'];
const typing=['text','password','email','search','number','date','time','url','tel','hidden'];
console.log(JSON.stringify([
 ...excluded.map(type=>isTypingTarget({tagName:'INPUT',type})),
 ...typing.map(type=>isTypingTarget({tagName:'INPUT',type})),
 ...['TEXTAREA','SELECT'].map(tagName=>isTypingTarget({tagName})),
 isTypingTarget({tagName:'DIV',isContentEditable:true}),
 isTypingTarget({tagName:'BUTTON'}),isTypingTarget(null)]));
""")
    assert out == [False] * 9 + [True] * 13 + [False] * 2


def test_forward_respects_native_controls_and_overlays():
    out = run_js(functions('isTypingTarget', 'forwardCommandCenterKey') + DOM + """
const control={closest(selector){return selector==='button, a, [role=button]' ? this : null}};
const e={type:'keydown',target:control,preventDefault(){}};
for(const key of ['Enter',' ','Tab']) forwardCommandCenterKey({...e,key});
forwardCommandCenterKey({...e,key:'ArrowRight'});
const dialog={closest(selector){return selector==='dialog[open]' ? this : null}};
forwardCommandCenterKey({...e,target:dialog,key:'ArrowRight'});
nodes['cloud-menu'].hidden=false;
const menu={closest(selector){return selector==='#cloud-menu' ? this : null}};
forwardCommandCenterKey({...e,target:menu,key:'ArrowRight'});
console.log(JSON.stringify(messages.map(m=>m[0].key)));
""")
    assert out == ['ArrowRight']
