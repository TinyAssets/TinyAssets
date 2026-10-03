"""The chat cloud in a real Chromium: drag, resize, shrink, restore, stay on screen.

The real app page (``render_app_html``, with its own CSP) is served locally,
signed-in state is entered the way the page itself does it (``showView``), and
every gesture is a real mouse or keyboard input.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tinyassets.onboarding import render_app_html
from tinyassets.onboarding.ui_frame import BOOTSTRAP_HTML, FRAME_HEADERS

# Run, with a skip counted as a failure, by .github/workflows/real-browser-proof.yml.
pytestmark = pytest.mark.real_browser


@pytest.fixture
def app_url():
    html, csp = render_app_html()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path.split("?")[0] == "/app/ui-frame":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                for name, value in FRAME_HEADERS.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(BOOTSTRAP_HTML.encode("utf-8"))
                return
            if self.path.split("?")[0] != "/app":
                self.send_response(404)
                self.end_headers()
                return
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_response(404)
            self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/app"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def browser():
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="owner=codex runs-in=real-browser-proof"
    )
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - no browser binary on this host
            pytest.skip(
                "owner=codex runs-in=real-browser-proof Chromium is not available here: "
                f"{exc.__class__.__name__}"
            )
        try:
            yield chromium
        finally:
            chromium.close()


def _enter_chat(page, url, *, layout=False):
    page.goto(url)
    # Let the page finish its own boot first: with no session it lands on the
    # sign-in view, and a boot that finished later would hide the chat again.
    page.wait_for_selector("#view-signin", state="visible")
    page.wait_for_load_state("networkidle")
    page.evaluate("""(layout) => {
        setQueueOwner('owner-1');
        if (layout) document.getElementById('view-chat').classList.add('ui-custom-active');
        showView('chat'); refreshChatCloud();
    }""", layout)
    page.wait_for_function("() => cloudState !== null")


def _box(page, selector):
    return page.locator(selector).bounding_box()


def _drag(page, selector, dx, dy, *, at=(0.5, 0.5)):
    box = _box(page, selector)
    x, y = box["x"] + box["width"] * at[0], box["y"] + box["height"] * at[1]
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=10)
    page.mouse.up()



# The retired #cc-blank stand-in had a different focus/composer contract.
# The replacement test below mounts and clicks the shipped platform bundle;
# its browser execution is required in real-browser-proof, not inferred from
# the synthetic play UI or the Node bridge tests.


def test_default_bundle_build_focuses_composer_at_prompt_end_without_sending(app_url, browser):
    from playwright.sync_api import expect

    from tinyassets.command_center_picker import BUILD_PROMPT, PLATFORM_DEFAULT_UI

    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        _enter_chat(page, app_url)
        page.evaluate("""({bundle,prompt}) => {
            // Synthetic owner/read responses are at the server boundary. The
            // real default script, iframe bridge, verify and prefill all run.
            fetchMe=async()=>({principal_id:'owner-1',universe_id:'home-1',setup:'connected'});
            Owner.read=async args=>{
                if(args.target!=='command_center_packages'||args.graph_id!=='home-1')
                    throw new Error('unexpected picker read');
                return {packages:[],build_prompt:prompt,can_try:false};
            };
            window.pickerSendCalls=[];
            const originalSendTurn=sendTurn;
            sendTurn=(...args)=>{
                window.pickerSendCalls.push(args);return originalSendTurn(...args);
            };
            AppUI.enabled=true;AppUI.home='home-1';AppUI.principal='owner-1';
            AppUI.platformDefault=bundle;
            if(!AppUI.mountDefault())throw new Error('default did not mount');
        }""", {"bundle": PLATFORM_DEFAULT_UI, "prompt": BUILD_PROMPT})
        frame = page.frame_locator("#ui-frame")
        build = frame.get_by_role("button", name="Build your own", exact=True)
        expect(build).to_be_visible()
        expect(frame.locator("#try-one")).to_be_visible()
        frame.locator("#try-one").click()
        expect(frame.locator("#packages")).to_have_text(
            "No shared command centers are available yet. You can build your own.")
        expect(frame.locator("#dismiss")).to_have_count(0)
        # Mounting the command center starts the chat as a bubble. Open it
        # through the owner control before entering the draft under test.
        expect(page.locator("#chat-cloud-bubble")).to_be_visible()
        page.click("#chat-cloud-bubble")
        expect(page.locator("#composer-input")).to_be_visible()
        page.fill("#composer-input", "draft before Build")
        page.click("#btn-cloud-shrink")
        expect(page.locator("#chat-cloud-bubble")).to_be_visible()
        build.click()
        composer = page.locator("#composer-input")
        expect(composer).to_have_value(BUILD_PROMPT)
        expect(composer).to_be_focused()
        expect(page.locator("#chat-cloud-bubble")).to_be_hidden()
        assert composer.evaluate("e=>[e.selectionStart,e.selectionEnd]") == [
            len(BUILD_PROMPT), len(BUILD_PROMPT)]
        assert page.evaluate("window.pickerSendCalls") == []
        assert page.evaluate("AppUI.isPlatformDefault()") is True
        expect(frame.locator("#message")).to_have_text("")
    finally:
        page.close()


def test_play_never_needs_a_second_click(app_url, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _enter_chat(page, app_url)
    page.evaluate("""() => AppUI.mount({ui_id:'play', name:'Play',
      markup:'<div id="hero"></div>',
      style:'#hero{position:absolute;left:0;top:100px;width:20px;height:20px;background:red}',
      script:`document.addEventListener('keydown',e=>{
        if(e.key==='ArrowRight'){
          const h=document.getElementById('hero');
          h.style.left=(parseInt(h.style.left||'0')+10)+'px';
          h.dataset.trusted=String(e.isTrusted);
        }
      });`})""")
    hero = page.frame_locator("#ui-frame").locator("#hero")
    hero.wait_for()
    position = 0

    def walk(*, native=True):
        nonlocal position
        page.keyboard.press("ArrowRight")
        position += 10
        from playwright.sync_api import expect
        expect(hero).to_have_css("left", f"{position}px")
        # Native walks prove the focus handoff itself.
        if native:
            expect(hero).to_have_attribute("data-trusted", "true")

    walk()
    page.click("#chat-cloud-bubble")
    assert _box(page, "#chat-cloud")["width"] <= 440
    # Put the cloud centrally so all four stage edges and corners are exposed.
    _drag(page, "#chat-cloud-resize", -100, -156)
    _drag(page, "#chat-cloud-bar", -400, -80, at=(0.8, 0.5))
    stage = _box(page, "#chat-stage")
    w, h = stage["width"], stage["height"]
    for x, y in [(2, 2), (w-2, 2), (2, h-2), (w-2, h-2),
                 (w/2, 2), (w/2, h-2), (2, h/2), (w-2, h/2)]:
        page.mouse.click(x, y)
        walk()
    page.click("#chat-cloud-title")
    walk()
    cloud = _box(page, "#chat-cloud")
    for x, y in [(cloud["x"], cloud["y"]+100),
                 (cloud["x"]+cloud["width"]-1, cloud["y"]+100),
                 (cloud["x"]+100, cloud["y"]),
                 (cloud["x"]+100, cloud["y"]+cloud["height"]-1)]:
        page.mouse.click(x, y)
        assert page.evaluate("document.activeElement.id") == "ui-frame"
        walk(native=True)
    page.click("#chat-cloud-resize")
    walk()
    _drag(page, "#chat-cloud-resize", 20, 20)
    walk()
    for close in ("toggle", "escape", "outside"):
        page.click("#btn-cloud-menu")
        assert page.locator("#cloud-menu").is_visible()
        if close == "toggle":
            page.click("#btn-cloud-menu")
        elif close == "escape":
            page.keyboard.press("Escape")
        else:
            page.mouse.click(2, 2)
        page.locator("#cloud-menu").wait_for(state="hidden")
        walk()
    page.fill("#composer-input", "hello")
    page.keyboard.press("Enter")
    walk()
    page.locator("#btn-stop").wait_for(state="hidden")
    page.focus("#composer-input")
    page.keyboard.press("Escape")
    walk()
    page.click("#btn-cloud-shrink")
    walk()
    page.focus("#chat-cloud-bubble")
    page.keyboard.press("Escape")
    walk()
    page.click("#chat-cloud-bubble")
    page.mouse.click(2, 2)
    walk()
    for dialog in ("model-dialog", "ui-dialog"):
        page.evaluate("id=>document.getElementById(id).showModal()", dialog)
        assert page.evaluate(
            "id=>document.getElementById(id).contains(document.activeElement)", dialog)
        page.keyboard.press("Escape")
        page.wait_for_function("""() => !document.querySelector('dialog[open]') &&
            document.activeElement.id === 'ui-frame'""")
        walk()
    page.fill("#composer-input", "draft survives ready")
    page.focus("#composer-input")
    assert page.evaluate("document.activeElement.id") == "composer-input"
    page.evaluate("AppUI.ready=false; AppUI.deliver()")
    assert page.evaluate("document.activeElement.id") == "composer-input"
    assert page.input_value("#composer-input") == "draft survives ready"
    hero.wait_for()
    page.evaluate("AppUI.mount(AppUI.active)")
    hero.wait_for()
    position = 0
    assert page.evaluate("document.activeElement.id") == "composer-input"
    assert page.input_value("#composer-input") == "draft survives ready"
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    assert page.evaluate("document.activeElement.id") == "composer-input"
    page.focus("#btn-cloud-menu")
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    walk()
    page.evaluate("document.getElementById('view-chat').style.paddingTop='24px'")
    page.focus("#composer-input")
    page.mouse.click(5, 5)
    walk()
    # Exercise the fallback in one task: a pending frame-focus message must
    # not turn this into another native walk between focus() and key dispatch.
    forwarded = page.evaluate("""() => {
      const stage=document.getElementById('chat-stage');
      stage.setAttribute('tabindex','-1'); stage.focus();
      const event=new KeyboardEvent('keydown', {
        key:'ArrowRight', code:'ArrowRight', bubbles:true, cancelable:true
      });
      const focused=document.activeElement===stage;
      stage.dispatchEvent(event);
      stage.dispatchEvent(new KeyboardEvent('keyup', {
        key:'ArrowRight', code:'ArrowRight', bubbles:true, cancelable:true
      }));
      return {focused, prevented:event.defaultPrevented};
    }""")
    assert forwarded == {"focused": True, "prevented": True}
    from playwright.sync_api import expect
    expect(hero).to_have_css("left", f"{position+10}px")
    expect(hero).to_have_attribute("data-trusted", "false")
    position += 10
    for control in ("btn-attach", "btn-models"):
        page.focus("#" + control)
        page.keyboard.press("ArrowRight")
        position += 10
        expect(hero).to_have_css("left", f"{position}px")
    # Unmounting ends the forwarding: with no frame there is nothing to forward
    # to, and the keys stay with the control that has them. In the app a bare
    # unmount does not happen -- chooseDefault mounts the platform's blank
    # command center in its place -- so this only pins that the teardown is
    # clean, not where focus lands.
    page.evaluate("AppUI.unmount()")
    assert page.locator("#ui-frame").count() == 0
    assert page.locator("#ui-frame-host").is_hidden()
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.id") == "btn-models", "keys stay put"
    page.close()


def test_picker_return_restores_native_frame_typing(app_url, browser):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _enter_chat(page, app_url)
    page.evaluate("""() => AppUI.mount({ui_id:'play', name:'Play',
      markup:'<div id="hero"></div><input id="field">',
      style:'#hero{width:20px;height:20px;background:red}',
      script:`window.addEventListener('focus',()=>{
        document.getElementById('field').focus();
      });`})""")
    field = page.frame_locator("#ui-frame").locator("#field")
    field.wait_for()
    page.click("#chat-cloud-bubble")
    page.focus("#btn-attach")
    page.evaluate("""() => {
        window.dispatchEvent(new Event('blur'));
        window.dispatchEvent(new Event('focus'));
    }""")
    expect(field).to_be_focused()
    page.keyboard.type("ab")
    assert field.input_value().endswith("ab")
    page.focus("#btn-attach")
    page.dispatch_event("#file-input", "change")
    expect(field).to_be_focused()
    page.keyboard.type("cd")
    assert field.input_value().endswith("abcd")
    page.close()


@pytest.mark.parametrize("phone", [False, True], ids=["desktop", "phone"])
@pytest.mark.parametrize("target", ["composer-input", "btn-cloud-menu"])
def test_queued_frame_focus_does_not_override_newer_chat_gesture(app_url, browser, phone, target):
    context = browser.new_context(
        viewport={"width": 390 if phone else 1280, "height": 844 if phone else 800},
        is_mobile=phone, has_touch=phone,
    )
    page = context.new_page()
    _enter_chat(page, app_url)
    page.evaluate("""() => AppUI.mount({ui_id:'play', name:'Play',
      markup:'<canvas id="scene" tabindex="0"></canvas>', style:'', script:''})""")
    scene = page.frame_locator("#ui-frame").locator("#scene")
    scene.wait_for()
    page.click("#chat-cloud-bubble")
    page.fill("#composer-input", "unsent draft")
    scene.evaluate("""el => {
      window.focusBarrierSeen=false;
      window.addEventListener('message', event => {
        if(event.source===parent && event.data?.testFocusBarrier) focusBarrierSeen=true;
      });
    }""")
    # Deterministically order a pending frame handoff before a newer owner
    # gesture, without a sleep or relying on process scheduling during a click.
    page.evaluate("""target => {
      focusCommandCenter();
      const input=document.getElementById('composer-input');
      input.setSelectionRange(2, 7);
      const control=document.getElementById(target);
      control.focus();
      if(target==='btn-cloud-menu') control.click();
      document.getElementById('ui-frame').contentWindow.postMessage({testFocusBarrier:true}, '*');
    }""", target)
    # Messages from this parent are ordered, so the barrier proves the queued
    # focus message was consumed before we inspect focus and selection.
    scene.evaluate("""() => new Promise(resolve => {
      if(window.focusBarrierSeen) return resolve();
      window.addEventListener('message', function observed(event) {
        if(event.source===parent && event.data?.testFocusBarrier) {
          window.removeEventListener('message', observed); resolve();
        }
      });
    })""")
    assert page.evaluate("document.activeElement.id") == target
    assert page.input_value("#composer-input") == "unsent draft"
    selection = page.locator("#composer-input").evaluate("e=>[e.selectionStart,e.selectionEnd]")
    assert selection == [2, 7]
    if target == "btn-cloud-menu":
        assert page.locator("#cloud-menu").is_visible()
        assert page.locator("#btn-cloud-menu").get_attribute("aria-expanded") == "true"
    else:
        page.keyboard.type("X")
        assert page.input_value("#composer-input") == "unXdraft"
    # The guard must still allow the next deliberate handoff into the frame.
    page.evaluate("focusCommandCenter()")
    from playwright.sync_api import expect
    expect(page.locator("#ui-frame")).to_be_focused()
    context.close()


def test_phone_send_keeps_composer_focus(app_url, browser):
    context = browser.new_context(viewport={"width": 390, "height": 844},
                                  is_mobile=True, has_touch=True)
    page = context.new_page()
    _enter_chat(page, app_url)
    page.locator("#composer-input").tap()
    page.keyboard.type("one")
    page.keyboard.press("Enter")
    page.keyboard.type("two")
    assert page.evaluate("document.activeElement.id") == "composer-input"
    assert page.input_value("#composer-input") == "two"
    page.keyboard.press("Escape")
    assert page.evaluate("document.activeElement.id") == "composer-input"
    page.locator("#btn-stop").wait_for(state="hidden")
    page.keyboard.press("Escape")
    assert page.evaluate("document.activeElement.id") == "composer-input"
    context.close()


def test_layout_arrival_preserves_typing_in_default_cloud(app_url, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _enter_chat(page, app_url)
    page.focus("#composer-input")
    page.keyboard.type("x")
    page.evaluate("""() => AppUI.mount({ui_id:'play', name:'Play',
      markup:'<div id="hero">Hero</div>', style:'', script:''})""")
    page.frame_locator("#ui-frame").locator("#hero").wait_for()
    assert page.locator("#chat-cloud").is_visible()
    assert page.evaluate("document.activeElement.id") == "composer-input"
    assert page.input_value("#composer-input") == "x"
    assert page.evaluate("cloudState.userSet") is False
    assert page.evaluate("localStorage.getItem(cloudStoreKey)") is None
    page.focus("#btn-attach")
    page.evaluate("refreshChatCloud()")
    assert page.locator("#chat-cloud").is_hidden()
    assert page.locator("#chat-cloud-bubble").is_visible()
    page.close()


def test_phone_play(app_url, browser, tmp_path):
    from playwright.sync_api import expect

    context = browser.new_context(viewport={"width": 390, "height": 844},
                                  is_mobile=True, has_touch=True)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _enter_chat(page, app_url)
    page.evaluate("""() => AppUI.mount({ui_id:'play', name:'Play',
      markup:'<div id="hero"></div>',
      style:'#hero{position:absolute;left:0;top:100px;width:20px;height:20px;background:red}',
      script:`document.addEventListener('keydown',e=>{
        if(e.key==='ArrowRight'){
          const h=document.getElementById('hero');
          h.style.left=(parseInt(h.style.left||'0')+10)+'px';
          h.dataset.trusted=String(e.isTrusted);
        }
      });`})""")
    hero = page.frame_locator("#ui-frame").locator("#hero")
    hero.wait_for()
    position = 0

    def walk():
        nonlocal position
        page.keyboard.press("ArrowRight")
        position += 10
        expect(hero).to_have_css("left", f"{position}px")
        expect(hero).to_have_attribute("data-trusted", "true")

    page.locator("#chat-cloud-bubble").tap()
    assert page.locator("#chat-cloud").is_visible()
    page.wait_for_timeout(300)
    assert page.evaluate("document.activeElement.id") == "composer-input"
    page.locator("#btn-cloud-shrink").tap()
    assert page.locator("#chat-cloud").is_hidden()
    assert _box(page, "#ui-frame") == pytest.approx(_box(page, "#chat-stage"), abs=1)
    walk()
    page.screenshot(path=tmp_path / "two-surfaces-phone-bubble.png")
    page.locator("#chat-cloud-bubble").tap()
    assert _box(page, "#chat-cloud") == pytest.approx(_box(page, "#chat-stage"), abs=1)
    for selector in ("#btn-cloud-shrink", "#btn-cloud-menu", "#btn-attach", "#btn-send"):
        box = _box(page, selector)
        assert box["width"] >= 36 and box["height"] >= 36
    page.screenshot(path=tmp_path / "two-surfaces-phone.png")
    page.locator("#composer-input").tap()
    page.set_viewport_size({"width": 390, "height": 500})
    page.wait_for_function(
        "() => document.getElementById('composer-input').getBoundingClientRect().bottom <= 500"
    )
    for selector in ("#composer-input", "#btn-send"):
        box = _box(page, selector)
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
        assert box["y"] >= 0 and box["y"] + box["height"] <= 500
    page.keyboard.type("hi")
    page.locator("#btn-send").tap()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.activeElement.id") == "composer-input"
    page.locator("#btn-stop").wait_for(state="hidden")
    page.locator("#btn-cloud-shrink").tap()
    walk()
    before = _box(page, "#chat-cloud-bubble")
    page.locator("#chat-cloud-bubble").evaluate("""el => {
        const r=el.getBoundingClientRect(), x=r.x+28, y=r.y+28;
        const events=[['pointerdown',0,0],['pointermove',-600,-150],['pointerup',-600,-150]];
        for (const [type,dx,dy] of events)
            el.dispatchEvent(new PointerEvent(type,{bubbles:true,pointerId:7,pointerType:'touch',
                isPrimary:true,button:0,buttons:type==='pointerup'?0:1,clientX:x+dx,clientY:y+dy}));
        el.click(); // Browsers emit a compatibility click after pointerup.
    }""")
    after = _box(page, "#chat-cloud-bubble")
    assert after != before
    assert 0 <= after['x'] <= 390-after['width']
    assert 0 <= after['y'] <= 844-after['height']
    assert page.locator("#chat-cloud").is_hidden()
    page.evaluate("history.pushState({}, '', '#play')")
    page.go_back()
    walk()
    page.locator("#chat-cloud-bubble").tap()
    assert page.locator("#chat-cloud").is_visible()
    assert errors == []
    context.close()


# A UI that grabs the keyboard the way a real game does (Furry House): a canvas
# it focuses itself, and a keydown handler that swallows everything it sees.
_GRABBER = """() => AppUI.mount({ui_id:'play', name:'Play',
  markup:'<canvas id="scene" tabindex="0"></canvas><input id="say">',
  style:'#scene{width:100%;height:60%;background:#234}#say{width:50%}',
  script:`const scene=document.getElementById('scene');
    scene.focus();
    window.keysSeen=[];
    document.addEventListener('keydown',e=>{
      window.keysSeen.push(e.key);
      e.stopPropagation(); e.preventDefault();   // a game that eats every key
    });`})"""


@pytest.mark.parametrize("phone", [False, True], ids=["desktop", "phone"])
def test_the_reserved_key_is_the_way_out_of_a_ui_that_holds_the_keyboard(
        app_url, browser, phone):
    """Founder, 2026-10-03: stuck inside a UI with no visible way back.

    The whole loop in a real browser: a UI that focuses itself and swallows
    keys, "/" to the chat, typing lands there, Escape back into the UI -- and
    the ring says who has the keyboard at every step.
    """
    context = browser.new_context(
        viewport={"width": 390 if phone else 1280, "height": 844 if phone else 800},
        is_mobile=phone, has_touch=phone,
    )
    page = context.new_page()
    _enter_chat(page, app_url)
    page.evaluate(_GRABBER)
    scene = page.frame_locator("#ui-frame").locator("#scene")
    scene.wait_for()
    page.evaluate("focusCommandCenter()")
    from playwright.sync_api import expect

    expect(page.locator("#ui-frame")).to_be_focused()
    assert page.locator("#chat-stage").get_attribute("data-keys") == "cc"
    # A mounted layout shrinks the chat to a bubble, so there is no composer on
    # screen to click: this is the state the founder got stuck in.
    assert page.evaluate("cloudState.mode") == "bubble"
    assert not page.locator("#composer-input").is_visible()

    # "/" pressed inside the frame: the UI never gets it, the composer does,
    # and the collapsed chat opens to receive it.
    page.frame_locator("#ui-frame").locator("#scene").press("/")
    page.wait_for_function("() => document.activeElement.id === 'composer-input'")
    assert page.evaluate("cloudState.mode") == "open"
    assert page.locator("#chat-stage").get_attribute("data-keys") == "chat"
    assert page.frame_locator("#ui-frame").locator("#scene").evaluate(
        "() => window.keysSeen.indexOf('/')") == -1, "the UI must not also see it"
    # Now the chat is on screen, the hint is hidden: these keys are already here.
    if not phone:
        assert page.locator("#chat-cloud-hint").is_hidden()

    # Typing lands in the chat, not in the UI.
    page.keyboard.type("hello")
    assert page.input_value("#composer-input") == "hello"

    # Escape hands the keyboard back to the command center.
    page.keyboard.press("Escape")
    expect(page.locator("#ui-frame")).to_be_focused()
    assert page.locator("#chat-stage").get_attribute("data-keys") == "cc"
    assert page.input_value("#composer-input") == "hello", "the draft is not discarded"
    # Back in the UI, with the chat open, the way out is named again.
    if not phone:
        assert page.locator("#chat-cloud-hint").is_visible()
    context.close()


# The same UI without the key-swallowing handler: this one lets its own input
# receive what it is given, which is the point of the test below.
_TEXT_UI = """() => AppUI.mount({ui_id:'play', name:'Play',
  markup:'<canvas id="scene" tabindex="0"></canvas><input id="say">',
  style:'#scene{width:100%;height:40%;background:#234}#say{width:50%}',
  script:`document.getElementById('scene').focus();`})"""


@pytest.mark.parametrize("phone", [False, True], ids=["desktop", "phone"])
def test_a_text_field_in_the_ui_still_receives_the_reserved_key(app_url, browser, phone):
    """A game's own command box keeps "/": typing a slash is typing a slash.

    This is the carve-out that makes the reserved key safe to reserve -- without
    it, every UI with a search or chat field would lose the character."""
    context = browser.new_context(
        viewport={"width": 390 if phone else 1280, "height": 844 if phone else 800},
        is_mobile=phone, has_touch=phone,
    )
    page = context.new_page()
    _enter_chat(page, app_url)
    page.evaluate(_TEXT_UI)
    say = page.frame_locator("#ui-frame").locator("#say")
    say.wait_for()
    say.click()
    say.press("/")

    assert say.input_value() == "/", "the UI's own field keeps the character"
    # Not stolen: the page's keyboard owner never changed.
    assert page.evaluate("document.activeElement.id") == "ui-frame"
    assert page.locator("#chat-stage").get_attribute("data-keys") == "cc"
    assert page.input_value("#composer-input") == ""
    context.close()


@pytest.mark.parametrize("phone", [False, True], ids=["desktop", "phone"])
def test_clicking_the_composer_while_the_ui_holds_focus_gives_the_chat_the_keys(
        app_url, browser, phone):
    """The stuck scenario exactly as it happened: the UI holds the keyboard and
    the owner clicks the composer. The chat is highlighted and typing lands."""
    context = browser.new_context(
        viewport={"width": 390 if phone else 1280, "height": 844 if phone else 800},
        is_mobile=phone, has_touch=phone,
    )
    page = context.new_page()
    _enter_chat(page, app_url)
    page.evaluate(_GRABBER)
    page.frame_locator("#ui-frame").locator("#scene").wait_for()
    # The owner opens the chat, then the UI takes the keyboard back.
    page.click("#chat-cloud-bubble")
    page.evaluate("focusCommandCenter()")
    assert page.locator("#chat-stage").get_attribute("data-keys") == "cc"
    if not phone:
        assert page.locator("#chat-cloud-hint").is_visible()

    page.click("#composer-input")
    page.wait_for_function("() => document.activeElement.id === 'composer-input'")
    assert page.locator("#chat-stage").get_attribute("data-keys") == "chat"
    page.keyboard.type("typed after the click")
    assert page.input_value("#composer-input") == "typed after the click"
    context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_shared_system_preview_and_permanent_trusted_switcher(app_url, browser, width):
    from playwright.sync_api import expect

    from tinyassets.command_center_picker import BUILD_PROMPT, PLATFORM_DEFAULT_UI

    page = browser.new_page(viewport={"width": width, "height": 850})
    try:
        _enter_chat(page, app_url)
        page.evaluate("""({bundle,prompt})=>{
            window.copyAsks=[];
            const own={kind:AppUI.KIND,version:1,ui_id:'own',name:'My own',
                markup:'<p>My own screen</p>',style:'',script:''};
            window.testRow={universe_id:'home-1',revision:1,ui_library:[own],
                ui_selection:null,platform_default:bundle};
            const catalogue={packages:[],systems:[{agent_definition_id:'public-village',
                publication_kind:'system',name:'Fantasy Village',description:'Shared village',
                author_id:'publisher',workflow_count:2,automation_count:2,
                available:true,unavailable_reason:''}],build_prompt:prompt,can_try:true};
            fetchMe=async()=>({principal_id:'owner-1',universe_id:'home-1',setup:'connected'});
            Owner.read=async args=>{
                if(args.target==='command_center_packages')return structuredClone(catalogue);
                if(args.target==='app_ui')return {app_ui:structuredClone(window.testRow)};
                if(args.target==='agent_bindings')return {bindings:[]};
                throw Error('unexpected read '+args.target);
            };
            MCP.callTool=async(tool,args)=>{
                if(tool!=='write_graph')throw Error('unexpected tool');
                if(args.operation==='try_package'){
                    window.copyAsks.push(args);return {request_id:'copy-1',title:'Copy Village'};
                }
                if(args.target==='app_ui'){
                    Object.assign(window.testRow,JSON.parse(args.payload_json));
                    window.testRow.revision++;
                    return {status:'saved',app_ui:structuredClone(window.testRow)};
                }
                throw Error('unexpected write');
            };
            AppUI.enabled=true;AppUI.home='home-1';AppUI.principal='owner-1';
            document.getElementById('btn-ui-switch').hidden=false;
            AppUI.adopt(window.testRow);
        }""", {"bundle": PLATFORM_DEFAULT_UI, "prompt": BUILD_PROMPT})
        frame = page.frame_locator("#ui-frame")
        frame.get_by_role("button", name="Try someone else's", exact=True).click()
        expect(frame.locator("#packages")).to_contain_text("Public system")
        expect(frame.locator("#packages")).to_contain_text("Components only; no files")
        frame.get_by_role("button", name="Preview copy", exact=True).click()
        expect(frame.locator("#message")).to_contain_text("preview and confirm")
        assert len(page.evaluate("window.copyAsks")) == 1
        page.evaluate("AppUI.open()")
        menu = page.locator("#ui-dialog")
        for label in ("Build your own", "Try someone else's", "Blank command center"):
            expect(menu.get_by_role("button", name=label, exact=True)).to_be_enabled()
        menu.get_by_role("button", name="Use My own", exact=True).click()
        expect(page.frame_locator("#ui-frame").get_by_text("My own screen")).to_be_visible()
        page.evaluate("AppUI.load()")
        assert page.evaluate("AppUI.active.ui_id") == "own"
        page.evaluate("AppUI.open()")
        menu.get_by_role("button", name="Try someone else's", exact=True).click()
        expect(menu).to_contain_text("Fantasy Village")
        menu.get_by_role("button", name="Preview component copy", exact=True).click()
        expect(page.locator("#ui-status")).to_contain_text("Nothing installs before you confirm")
        assert len(page.evaluate("window.copyAsks")) == 2
        menu.get_by_role("button", name="Blank command center", exact=True).click()
        expect(page.frame_locator("#ui-frame").get_by_role(
            "button", name="Build your own", exact=True)).to_be_visible()
        page.evaluate("AppUI.open()")
        menu.get_by_role("button", name="Build your own", exact=True).click()
        expect(menu).not_to_be_visible()
        expect(page.locator("#composer-input")).to_have_value(BUILD_PROMPT)
        expect(page.locator("#composer-input")).to_be_focused()
    finally:
        page.close()
