"""Two surfaces: default geometry and keyboard handoff using shipped functions."""
from tests.test_app_browser_notifications import functions, run_js
from tests.test_app_chat_cloud import call


def _shipped_chat_key() -> str:
    """The page's own reserved key, so the tests follow a change to it."""
    import re

    from tinyassets.onboarding import render_app_html

    html, _ = render_app_html()
    match = re.search(r'const CHAT_KEY="[^"]+";', html)
    assert match, "app.html must declare the reserved chat key"
    return match.group(0) + "\n"


CHAT_KEY_DECL = _shipped_chat_key()


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
 'chat-stage':{}, 'composer-input':{tagName:'TEXTAREA'},
 'cloud-menu':{hidden:true}};
const $=id=>nodes[id]; const document={body:{}};
"""


def test_focus_only_ever_targets_the_frame():
    """ONE code path. The command center is always a mounted bundle -- the
    platform's own blank one when the owner has chosen nothing -- so there is
    no in-document stand-in to fall back to and no second branch to keep
    working. With no frame at all this does nothing rather than inventing a
    target."""
    out = run_js(functions('focusCommandCenter') + DOM + """
focusCommandCenter(); const first=focused;
focused=''; delete nodes['ui-frame']; focusCommandCenter();
console.log(JSON.stringify({first,focused,tabindex:frame.tabindex,messages}));
""")
    assert out == {"first": "frame", "focused": "", "tabindex": "0",
                   "messages": [[{"ta_ui": 1, "type": "focus"}, "*"]]}


def test_forward_only_from_unclaimed_focus():
    out = run_js(functions('isTypingTarget', 'forwardCommandCenterKey') + DOM + """
const e={key:'ArrowRight',code:'ArrowRight',shiftKey:true,altKey:false,
 ctrlKey:false,metaKey:false,repeat:true,preventDefault(){}};
for(const target of [document.body,nodes['chat-stage'],nodes['ui-frame-host']]){
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
                            "ctrlKey": False, "metaKey": False, "repeat": True,
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


# -- the keyboard safety net: who has the keys, and the way out ----------------
# Founder, 2026-10-03, after getting stuck in a UI that held the keyboard: "its
# very easy currently for the user to get stuck like that and not know how to
# get out of it". Two halves: the owner is SHOWN, and one key always leads back.

SAFETY_DOM = """
let focused='', messages=[], mode='open';
const frame={setAttribute(k,v){this[k]=v},focus(){focused='frame'},
 contentWindow:{focus(){focused='frame'},postMessage(m,origin){messages.push([m,origin])}}};
function node(id,inside){ return {id, closest(sel){
  return (inside||[]).some(s=>sel.indexOf(s)>=0) ? this : null; }}; }
const composer={id:'composer-input', tagName:'TEXTAREA',
  closest(sel){ return sel.indexOf('#chat-cloud')>=0 ? this : null; },
  focus(){ focused='composer'; document.activeElement=this; }};
const stage={dataset:{}};
const nodes={'ui-frame':frame,
 'ui-frame-host':{id:'ui-frame-host',hidden:false,closest(){return null}},
 'chat-stage':stage,'composer-input':composer,'cloud-menu':{hidden:true},
 'chat-cloud-bubble':node('chat-cloud-bubble',['#chat-cloud'])};
const $=id=>nodes[id];
const document={body:{id:'body',closest(){return null}},activeElement:null};
document.activeElement=document.body;
let cloudState={mode:'open'};
function setChatCloudMode(m){ mode=m; cloudState.mode=m; }
"""


def _safety(body: str):
    return run_js(functions('isTypingTarget', 'keyboardOwner', 'paintKeyboardOwner',
                            'focusChatComposer', 'reservedChatKey',
                            'forwardCommandCenterKey')
                  + CHAT_KEY_DECL + SAFETY_DOM + body)


def test_the_ring_names_the_surface_that_has_the_keys():
    out = _safety("""
const seen=[];
document.activeElement=document.body; paintKeyboardOwner(); seen.push(stage.dataset.keys);
document.activeElement=nodes['ui-frame']; paintKeyboardOwner(); seen.push(stage.dataset.keys);
document.activeElement=nodes['ui-frame-host']; paintKeyboardOwner();
seen.push(stage.dataset.keys);
document.activeElement=composer; paintKeyboardOwner(); seen.push(stage.dataset.keys);
document.activeElement=nodes['chat-cloud-bubble']; paintKeyboardOwner();
seen.push(stage.dataset.keys);
console.log(JSON.stringify(seen));""")

    # Nothing focused is the command center's: that is where the keys go.
    assert out == ["cc", "cc", "cc", "chat", "chat"]


def test_the_reserved_key_focuses_the_composer_from_the_command_center():
    out = _safety("""
const taken=[];
for(const key of ['/','Enter']){
  document.activeElement=nodes['ui-frame']; focused=''; mode='bubble'; cloudState.mode='bubble';
  let prevented=false;
  reservedChatKey({key,target:document.body,preventDefault(){prevented=true}});
  taken.push({key,prevented,focused,mode,keys:stage.dataset.keys});
}
console.log(JSON.stringify(taken));""")

    for row in out:
        # Opened from a bubble, focused, and the ring followed in one step.
        assert row["prevented"] is True and row["focused"] == "composer"
        assert row["mode"] == "open" and row["keys"] == "chat"


def test_the_reserved_key_is_not_taken_from_a_text_field_or_a_control():
    out = _safety("""
const kept=[];
function attempt(label,key,target,extra){
  document.activeElement=nodes['ui-frame']; focused='';
  let prevented=false;
  reservedChatKey(Object.assign({key,target,preventDefault(){prevented=true}},extra||{}));
  kept.push({label,prevented,focused});
}
attempt('text field','/',{tagName:'INPUT',type:'text',closest(){return null}});
attempt('textarea','/',{tagName:'TEXTAREA',closest(){return null}});
attempt('dialog','/',{closest(sel){return sel.indexOf('dialog')>=0?this:null}});
attempt('button + Enter','Enter',{closest(sel){
  return sel.indexOf('button')>=0?this:null}});
attempt('ctrl-/','/',{closest(){return null}},{ctrlKey:true});
attempt('meta-/','/',{closest(){return null}},{metaKey:true});
attempt('other key','x',{closest(){return null}});
// Already in the chat: the key is the composer's own character.
document.activeElement=composer; focused='';
let prevented=false;
reservedChatKey({key:'/',target:composer,preventDefault(){prevented=true}});
kept.push({label:'in the chat',prevented,focused});
console.log(JSON.stringify(kept));""")

    labels = [row["label"] for row in out]
    assert labels == ["text field", "textarea", "dialog", "button + Enter", "ctrl-/",
                      "meta-/", "other key", "in the chat"]
    for row in out:
        assert row["prevented"] is False, row["label"]
        assert row["focused"] == "", row["label"]


def test_a_claimed_reserved_key_is_never_also_forwarded_to_the_frame():
    """reservedChatKey runs first and prevents the default; the forwarder skips
    an event whose default is prevented, so the frame never sees the key the
    chat just took."""
    out = _safety("""
document.activeElement=nodes['ui-frame'];
const event={key:'/',code:'Slash',shiftKey:false,altKey:false,ctrlKey:false,metaKey:false,
  repeat:false,type:'keydown',target:document.body,defaultPrevented:false,
  preventDefault(){ this.defaultPrevented=true; }};
reservedChatKey(event);
forwardCommandCenterKey(event);
console.log(JSON.stringify({forwarded:messages.map(m=>m[0].key),focused}));""")

    assert out == {"forwarded": [], "focused": "composer"}


def test_escape_from_the_composer_returns_the_keyboard_to_the_command_center():
    out = run_js(functions('isTypingTarget', 'keyboardOwner', 'paintKeyboardOwner',
                           'focusCommandCenter') + CHAT_KEY_DECL + SAFETY_DOM + """
document.activeElement=composer; paintKeyboardOwner();
const before=stage.dataset.keys;
focusCommandCenter();                       // what the composer's Escape calls
document.activeElement=nodes['ui-frame']; paintKeyboardOwner();
console.log(JSON.stringify({before,focused,after:stage.dataset.keys,
  handoff:messages.map(m=>m[0].type)}));""")

    assert out == {"before": "chat", "focused": "frame", "after": "cc",
                   "handoff": ["focus"]}


# -- the frame half: a focused cross-origin UI hands the reserved key back -----


def test_the_frame_forwards_the_reserved_key_but_not_a_replayed_one():
    """The bootstrap's contract, pinned at its source: the way out is taken in
    the CAPTURE phase so a UI cannot swallow it first, only for a real
    keypress (the parent replays keys into the frame as synthetic events, and
    posting those back would loop), and never out of a text field."""
    from tinyassets.onboarding.ui_frame import BOOTSTRAP_HTML

    assert 'var RESERVED_KEY = "/";' in BOOTSTRAP_HTML
    assert 'type: "reserved_key"' in BOOTSTRAP_HTML
    assert "if (!event.isTrusted) { return; }" in BOOTSTRAP_HTML
    assert "typingHere(event.target) || typingHere(document.activeElement)" in BOOTSTRAP_HTML
    # Registered in the capture phase on the frame's own window.
    assert '  }, true);' in BOOTSTRAP_HTML
    # The frame asks for nothing else: focus is the parent's to move.
    assert BOOTSTRAP_HTML.count('type: "reserved_key"') == 1


def test_the_frames_own_text_fields_keep_the_reserved_key():
    from tests.test_onboarding_app import _js_function
    from tinyassets.onboarding.ui_frame import BOOTSTRAP_HTML

    out = run_js(_js_function(BOOTSTRAP_HTML, "typingHere") + """
const excluded=['button','submit','checkbox','radio','range','file','color','reset','image'];
const typing=['text','password','email','search','number','date','time','url','tel'];
console.log(JSON.stringify([
 ...excluded.map(type=>typingHere({tagName:'INPUT',type})),
 ...typing.map(type=>typingHere({tagName:'INPUT',type})),
 typingHere({tagName:'INPUT'}),
 ...['TEXTAREA','SELECT'].map(tagName=>typingHere({tagName})),
 typingHere({tagName:'DIV',isContentEditable:true}),
 typingHere({tagName:'CANVAS'}), typingHere({tagName:'BUTTON'}), typingHere(null)]));
""")

    # An <input> with no type is a text field; a canvas (the game case) is not.
    assert out == [False] * 9 + [True] * 9 + [True] + [True] * 2 + [True] + [False] * 3


def test_escape_is_the_composers_own_when_no_layout_holds_the_keyboard():
    """The safety net exists to escape a UI that took the keyboard. With no
    command center mounted there is nothing to escape, so Escape stays the
    composer's -- a phone keyboard's "done" sends it mid-conversation
    (test_phone_send_keeps_composer_focus)."""
    out = run_js(functions('cloudHasLayout') + """
const nodes={'view-chat':{classList:{has:false,contains(c){
  return c==='ui-custom-active' && this.has; }}}};
const $=id=>nodes[id];
const seen=[cloudHasLayout()];
nodes['view-chat'].classList.has=true;
seen.push(cloudHasLayout());
console.log(JSON.stringify(seen));""")

    assert out == [False, True]
    # And the handler consults exactly that before taking the key.
    from tinyassets.onboarding import render_app_html

    html, _ = render_app_html()
    assert 'if(event.key!=="Escape" || event.defaultPrevented) return;\n      ' \
           'if(!cloudHasLayout()) return;' in html
