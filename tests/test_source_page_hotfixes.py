"""Selection, stable sign-in codes and readable answer relays in the browser."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.real_browser
def test_source_page_selection_copy_and_answer_relay():
    source = Path("tinyassets/onboarding/app/main.js").read_text("utf-8")
    copy = source[source.index("  function copyButton("):
                  source.index("  function focusCommandCenter(")]
    messages = source[source.index("  function requestAnswerLine("):
                      source.index("  function setStatusLine(")]
    pointer = source[source.index('    $("view-chat").addEventListener("pointerdown"'):
                     source.index('    $("chat-cloud").addEventListener("pointerdown"')]
    cli = source[source.index("  const SubscriptionSources={"):]
    cli = cli[:cli.index("\n  };") + 6]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.set_content('''<div id="view-chat"><div id="empty"></div>
          <p id="text">Select this text</p><div id="plain">Plain div text</div>
          <input id="field"><dialog open id="dialog">
          <button id="dialog-button">Copy</button></dialog>
          <p id="status" role="status"></p><button id="connect">Connect</button>
          <div id="thread"></div></div>''')
        page.add_script_tag(content="""
          const $=id=>document.getElementById(id),NATIVE=true,DESKTOP_SHELL=false;
          let hasMessages=false,focusCalls=0;
          const focusCommandCenter=()=>focusCalls++,formatMessageTimestamp=()=>null;
          const firstQueuedBubble=()=>null,MCP={_loginEpoch:1},queueOwner='owner';
          const pkceStore={setItem(){},removeItem(){}},sleep=()=>new Promise(r=>window.tick=r);
          window.copied=[];
          Object.defineProperty(navigator,'clipboard',{value:{writeText:async v=>copied.push(v)}});
        """ + copy + messages + pointer + cli)
        assert page.evaluate("""()=>{
          const results={};
          for(const id of ['text','plain','field','dialog','dialog-button','empty']){
            const event=new PointerEvent('pointerdown',{bubbles:true,cancelable:true});
            $(id).dispatchEvent(event);results[id]=event.defaultPrevented;
          }
          return results;
        }""") == {"text": False, "plain": False, "field": False, "dialog": False,
                  "dialog-button": False, "empty": True}
        page.evaluate("""()=>{
          window.code='ABCD-1234';
          SubscriptionSources.post=async()=>({flow:'flow',status:'waiting',code:window.code});
          window.ui={status:$('status'),button:$('connect'),cancel:{},input:{},submit:{}};
          window.login=SubscriptionSources.start({id:'fixture',name:'Fixture'},ui);
        }""")
        page.wait_for_function("window.tick && document.querySelector('#status code')")
        page.evaluate("""()=>{
          window.codeNode=$('status').querySelector('code');
          const selection=getSelection(),range=document.createRange();
          range.selectNodeContents(codeNode);selection.removeAllRanges();selection.addRange(range);
          window.tick();
        }""")
        page.wait_for_function("window.codeNode === document.querySelector('#status code')")
        assert page.evaluate("getSelection().toString()") == "ABCD-1234"
        page.locator('#status button').click(force=True)
        assert page.evaluate("window.copied") == ["ABCD-1234"]
        page.evaluate("window.code='EFGH-5678';window.tick()")
        page.wait_for_function("document.querySelector('#status code').textContent==='EFGH-5678'")
        page.evaluate("ui.cancel.onclick();window.tick()")
        page.evaluate("window.login")
        # A second attempt with the same code must render it again.
        page.evaluate("window.login=SubscriptionSources.start("
                      "{id:'fixture',name:'Fixture'},ui);void 0")
        page.wait_for_function("document.querySelector('#status code')?.textContent==='EFGH-5678'")
        page.evaluate("ui.cancel.onclick();window.tick()")
        page.evaluate("window.login")
        for decision, verb in (("allowed", "allowed"), ("denied", "declined")):
            result = page.evaluate("""({decision})=>{
              const raw='The owner answered your request.\\n'+
                JSON.stringify({outcome:{decision,title:'Use source'}});
              const user=appendMessage('user',raw).querySelector('.msg-body').textContent;
              const other=appendMessage('system',raw).querySelector('.msg-body').textContent;
              return {user,unchanged:other===raw,rawHasJSON:raw.includes('"outcome"')};
            }""", {"decision": decision})
            assert result == {"user": f"You {verb}: Use source", "unchanged": True,
                              "rawHasJSON": True}
        browser.close()
