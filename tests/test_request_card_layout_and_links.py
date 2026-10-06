"""A pending-request card the user can actually read, click and reply from.

Live 2026-09-30 on the "Waiting on you" rail at https://tinyassets.io/mcp/app
(the app's URL at the time; it moved to https://tinyassets.io/app in #4112):

1. The action row ``[Accept][Deny][Clear][Send reply]`` overflowed the 16rem
   rail. At innerWidth 1509 "Send reply" was cut to "S"/"r"; at 2400 it spanned
   x=2358..2409 past the viewport, and the only clickable sliver sat over the
   rail's scrollbar, so **a reply could not be sent from a card at all**. The
   grant blockquote quoting a hook URL was clipped mid-URL the same way.
2. The card rendered a field help link "Get it from tinyassets.io" pointing at
   ``https://tinyassets.io/settings`` -- a 404 the agent invented. An
   agent-chosen destination styled like platform help, beside a box asking for a
   secret, is a dead end and a phishing shape.
3. Text set into "Reply or note (optional)" did not stick, and a typed Deny note
   reached the thread as a bare ``Denied: "<title>"``. Both were the same cause:
   the rail re-renders on a 15-second poll and rebuilt every card, deleting what
   the user had typed into it.

The layout half is asserted against the stylesheet the page ships, because no
headless DOM here computes layout; the live proof is a screenshot at 1509px and
390px after deploy. The behaviour halves are EXECUTED under node.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.app_sheet_harness import rail_source
from tests.test_onboarding_app import _js_function
from tinyassets.onboarding import render_app_html

_NODE = shutil.which("node")


def _css() -> str:
    html, _csp = render_app_html()
    return html[html.index("<style"):html.index("</style>")]


#: Comments have to go before the rules are read. This stylesheet is heavily
#: commented, and a `/* ... */` block sitting above a rule lands INSIDE the
#: selector text of a naive `([^{}]+)\{...\}` scan -- so an exact selector match
#: silently finds nothing and the rule reads as absent. It cost a real
#: false negative on `.rtab-link.rtab-link--agent`, which was present the whole
#: time.
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)


def _rule(css: str, selector: str) -> str:
    """Every declaration that applies to ``selector``, from all its rules.

    A property can be set in a shared rule (``.rtab-grant`` is listed in the
    wrapping rule alongside six siblings) as easily as in its own, so reading
    only the first match asserts nothing about the page.
    """
    found = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", _CSS_COMMENT_RE.sub(" ", css)):
        selectors = [part.strip() for part in match.group(1).split(",")]
        if selector in selectors:
            found.append(" ".join(match.group(2).split()))
    assert found, f"no CSS rule for {selector!r}"
    return ";".join(found)


def test_the_css_reader_finds_a_rule_that_follows_a_comment():
    """The helper itself, because a stylesheet this commented breaks a naive one.

    Without comment stripping, `_rule` returns "absent" for any rule whose
    preceding `/* ... */` block gets swept into its selector text -- which is
    most of them here, and makes every layout assertion in this file vacuous in
    exactly the direction that passes.
    """
    css = "/* a comment about the thing */\n.thing{color:red}\n.other{color:blue}"
    assert _rule(css, ".thing") == "color:red"
    assert _rule(css, ".other") == "color:blue"


# --------------------------------------------------------------------------- #
# 1 + 7: nothing in a card reaches past the rail.
# --------------------------------------------------------------------------- #


def test_the_action_row_wraps_so_every_verb_stays_inside_the_rail():
    """Four verbs do not fit one 16rem line, so the row must wrap onto two.

    Without ``flex-wrap`` the row's min-content width is the sum of four
    unbreakable labels, which overflows the rail and then the viewport -- the
    2400px case where the click landed on the scrollbar.
    """
    css = _css()
    row = _rule(css, ".rtab-row")
    assert "flex-wrap:wrap" in row, "the four-verb action row still cannot wrap"
    assert "display:flex" in row
    button = _rule(css, ".rtab-row .btn")
    assert "min-width:0" in button, "a verb button could still refuse to shrink"


def test_long_unbroken_text_in_a_card_breaks_instead_of_clipping():
    """The grant blockquote quotes a hook URL; it has to wrap mid-URL."""
    css = _css()
    wrapping = _rule(css, ".rtab-grant")
    assert "overflow-wrap:anywhere" in wrapping, "a long URL still clips at the rail edge"
    for selector in (".rtab-title", ".rtab-why", ".rtab-help", ".rtab-link",
                     ".rtab-note", ".rtab-label"):
        assert _rule(css, selector), f"{selector} is not covered by the wrapping rule"


def test_a_cards_inputs_cannot_be_wider_than_the_card():
    css = _css()
    inputs = _rule(css, ".rtab-body input")
    assert "max-width:100%" in inputs and "width:100%" in inputs


def test_the_rail_can_shrink_at_phone_width():
    """The Android and iOS shells render this same SPA at ~390px."""
    css = _css()
    assert "min-width:0" in _rule(css, ".rail"), "the rail could not shrink below 16rem"
    phone = css[css.index("@media (max-width:760px)"):]
    assert "width:auto" in phone.split("}")[1] + phone.split("}")[2], \
        "the rail still has a fixed width on a phone"


# --------------------------------------------------------------------------- #
# 2: an agent-supplied field link is the universe's suggestion, not platform help.
# --------------------------------------------------------------------------- #

_LINK_HARNESS = r"""
function mk(t){return {tag:t,textContent:'',className:'',attrs:{},children:[],
  href:'',target:'',rel:'',
  appendChild(c){this.children.push(c);return c;},
  setAttribute(k,v){this.attrs[k]=v;}};}
const document={createElement:mk};
__SOURCE__
const out=[];
for(const raw of __URLS__){
  const lab=mk('label');
  railFieldLink(raw, lab);
  out.push(lab.children.map(c=>({tag:c.tag,cls:c.className,text:c.textContent,
    href:c.href,rel:c.rel})));
}
console.log(JSON.stringify(out));
"""


def _render_links(urls):
    html, _ = render_app_html()
    script = (_LINK_HARNESS
              .replace("__SOURCE__", _js_function(html, "railFieldLink"))
              .replace("__URLS__", json.dumps(urls)))
    run = subprocess.run([_NODE], input=script, capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.parametrize("length", [430, 8192])
def test_long_request_link_reaches_the_rendered_anchor_in_full(length):
    from tinyassets.api.pending_requests import _validated_fields

    assert _NODE is not None, "Node is required to verify the shipped request-link renderer"
    prefix = "https://example.com/authorize?state="
    url = prefix + "a" * (length - len(prefix) - len("#finish")) + "#finish"
    [field] = _validated_fields(
        [{"name": "key", "label": "Key", "type": "secret", "url": url}],
        {"type": "connect_http"},
    )
    [parts] = _render_links([field["url"]])
    link = parts[1]
    assert link["tag"] == "a"
    assert link["href"] == url
    assert link["text"] == url.removeprefix("https://")
    assert "overflow-wrap:anywhere" in _rule(_css(), ".rtab-link")


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_an_agent_field_link_is_labelled_as_the_universes_suggestion():
    [parts] = _render_links(["https://tinyassets.io/settings"])
    note, link = parts
    assert note["text"] == "Suggested by your agent:", \
        "an agent-chosen link was presented without saying who chose it"
    assert "rtab-suggest" in note["cls"]
    # The HOST ALONE was the bug: "Get it from tinyassets.io" over /settings read
    # as platform help for a page that does not exist. The path is visible now.
    assert link["text"] == "tinyassets.io/settings", \
        "the path is hidden, so an invented page still reads as a real one"
    assert "rtab-link--agent" in link["cls"], "styled as platform chrome"
    assert "noopener" in link["rel"] and "noreferrer" in link["rel"]


def test_an_agents_link_never_borrows_the_platform_accent_colour():
    """The accent is the platform's own voice (founder: "never styled as
    platform chrome"). `.connect-panel .rtab-link` paints links with it, so the
    agent-link rule needs two classes to outrank that one wherever a field link
    is rendered \u2014 equal specificity would hand it to whichever came last in
    the stylesheet.
    """
    css = _css()
    agent = _rule(css, ".rtab-link.rtab-link--agent")
    assert "color:var(--ink-dim)" in agent
    assert "var(--accent)" not in _rule(css, ".rtab-link--agent"), (
        "the agent-link rule sets the platform accent on itself"
    )


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_a_third_party_link_shows_its_real_host_and_path():
    [parts] = _render_links(["https://developer.twitter.com/en/portal/dashboard"])
    assert parts[1]["text"] == "developer.twitter.com/en/portal/dashboard"
    assert parts[1]["href"] == "https://developer.twitter.com/en/portal/dashboard"


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
@pytest.mark.parametrize("bad", [
    "http://provider.example/keys",            # not https
    "javascript:alert(1)",                     # not a link at all
    "https://user:pw@provider.example/keys",   # userinfo
    "not a url",
])
def test_a_link_that_is_not_plain_https_is_shown_but_not_clickable(bad):
    [parts] = _render_links([bad])
    assert parts[0]["text"] == "Suggested by your agent:"
    assert [p["tag"] for p in parts] == ["span", "span"], \
        f"{bad!r} was rendered as a clickable link"
    assert "not a usable https link" in parts[1]["text"]


# --------------------------------------------------------------------------- #
# 2 (server half): the ask is refused at ask time, cheaply and locally.
# --------------------------------------------------------------------------- #


def test_the_server_refuses_an_invented_first_party_page():
    from tinyassets.api.pending_requests import _unusable_field_url

    assert "no /settings page" in _unusable_field_url("https://tinyassets.io/settings")
    assert "raw address" in _unusable_field_url("https://203.0.113.7/keys")
    assert _unusable_field_url("https://tinyassets.io/account") == ""
    assert _unusable_field_url("https://tinyassets.io/legal#privacy") == ""
    assert _unusable_field_url("https://developer.twitter.com/en/portal/dashboard") == ""


def test_the_first_party_page_list_matches_the_site_the_repo_ships():
    """A drift guard: adding a page to the site without adding it here would
    make the new page unlinkable, and removing one would keep a 404 linkable.

    Skipped where the site tree is not in the checkout (the API container ships
    ``tinyassets/`` only); CI has the whole repo, which is where it matters.
    """
    from tinyassets.api.pending_requests import _FIRST_PARTY_PATHS

    app = Path(__file__).resolve().parents[1] / "WebSite" / "site-react" / "app"
    if not app.is_dir():
        pytest.skip("WebSite/site-react/app is not in this checkout")
    served = {
        "/" + str(page.parent.relative_to(app)).replace("\\", "/")
        for page in app.rglob("page.*")
        if page.suffix in {".tsx", ".jsx", ".mdx", ".js"}
    }
    served.discard("/.")
    missing = served - _FIRST_PARTY_PATHS
    assert missing == set(), (
        "these pages exist on the site but an agent cannot link to them: "
        f"{sorted(missing)}"
    )


# --------------------------------------------------------------------------- #
# 3 + 6: what the user typed survives the poll, and Deny carries it.
# --------------------------------------------------------------------------- #

_ASK = {"request_id": "req_gtm", "kind": "API", "status": "pending",
        "title": "Replace the rejected GitHub token to publish GTM Village",
        "body": "The stored token was refused.",
        "fields": [{"name": "token", "label": "GitHub token", "type": "secret",
                    "url": "https://github.com/settings/tokens"}],
        "action": {"type": "connect_http"}, "grant_sentence": "POST api.github.com"}

_NOTE = "do we still need this? you said people can use it through the commons now"


# A harness of its own, NOT the shared one, and the reason is the bug. The
# shared stub's `$` memoises one node per id forever, so a value written through
# it survives any number of rebuilds -- which is precisely the thing under test,
# and a test written on that stub passes with the fix reverted (checked). Here
# `$` walks the live tree the way getElementById does: a node dropped from the
# document is NOT found, and a rebuilt card yields a NEW empty control.
_LIVE_DOM_HARNESS = r"""
function mk(id){return {id:id||'',textContent:'',hidden:false,value:'',open:false,
 rows:0,placeholder:'',type:'',href:'',target:'',rel:'',
 showModal(){this.open=true;},close(){this.open=false;},
 remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(c=>c!==this);blurSubtree(this);this.parentNode=null;}},
 children:[],parentNode:null,attrs:{},dataset:{},
 classList:{set:new Set(),toggle(c,on){on?this.set.add(c):this.set.delete(c);},
  contains(c){return this.set.has(c);}},
 setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},
 appendChild(c){if(c.parentNode){const p=c.parentNode;p.children=p.children.filter(x=>x!==c);
     blurSubtree(c);}
   c.parentNode=this;this.children.push(c);return c;},
 replaceChildren(){this.children.forEach(c=>{c.parentNode=null;blurSubtree(c);});
   this.children=[];},
 querySelectorAll(){return [];},
 contains(node){return node===this || this.children.some(child=>child.contains(node));},
 addEventListener(e,f){this['on'+e]=f;},scrollIntoView(){},
 // Focus follows the DOM: a browser blurs an element that leaves the tree,
 // and re-appending it does NOT give focus back. Modelling that is the whole
 // point of the settled-rail early return.
 focus(){focused=this;}};}
let focused=null;
// A browser blurs an element the moment it leaves the document, and moving one
// within the document blurs it too. Without this the harness cannot tell a
// detach-and-reattach from an untouched node, and a focus assertion written on
// it passes against the reverted fix (checked).
function blurSubtree(node){
  if(node===focused) focused=null;
  for(const child of node.children||[]) blurSubtree(child);
}
const document={createElement:t=>{const e=mk('');e.tag=t;return e;},
  createTextNode:t=>{const e=mk('');e.tag='#text';e.textContent=t;return e;}};
// The page's own fixed elements, which exist whatever the rail is showing.
const els=new Map();
for(const id of ['request-rail','rail-items','rail-head','connect-panel',
                 'connect-other','connect-shapes','hosted-model-status','needs-you','needs-you-items',
                 'rail-add-panel','btn-rail-add'])
  els.set(id, mk(id));
const rail=els.get('request-rail'), host=els.get('rail-items');
els.get('rail-head').textContent='Request history';
rail.appendChild(host);
rail.appendChild(els.get('connect-panel'));
Object.defineProperty(host,'textContent',{get(){return '';},set(v){this.replaceChildren();}});
function inTree(node){for(let at=node;at;at=at.parentNode) if(at===rail) return true; return false;}
function findById(node,id){
  if(node.id===id) return node;
  for(const child of node.children){const hit=findById(child,id); if(hit) return hit;}
  return null;
}
// Live tree first, then the fixed elements. Nothing is invented: an id that is
// neither in the document nor a fixed element resolves to null, as in a browser.
const $=id=>findById(rail,id)||els.get(id)||null;
function text(node){return (node.textContent||'')+node.children.map(text).join(' ');}
let railOpen=null, railCache=[], NATIVE=false, connectWasBlocking=null;
let connectOtherOpen=false, railNodes=new Map();
const CONNECT_REQUEST_ID="sys_connect_llm";
const RECONNECT_REQUEST_PREFIX="reconnect-source:";
const HostedModelConnect={setup:'empty',busy:false,request:null,primary:null,
 configure(p){this.primary=p;}, adopt(r){this.adopt_seen=r;}, paint(){}};
const ConnectOAuth={decorate(){}};
function answerRail(){}
__SOURCE__
"""


def _run_rail(rows, extra):
    html, _ = render_app_html()
    source = rail_source(html) + "\n" + "\n".join(
        _js_function(html, name) for name in (
            "railFieldLink", "railFieldControl", "railBody", "frameTitle",
            "answerLine", "clearRailCards"))
    script = (_LIVE_DOM_HARNESS.replace("__SOURCE__", source)
              + "\nrenderRail(" + json.dumps(rows) + ");\n" + extra + r"""
console.log(JSON.stringify(result));
""")
    run = subprocess.run([_NODE], input=script, capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_the_harness_sees_a_rebuilt_card_as_a_new_empty_control():
    """The harness itself, proved: without this, every test below is vacuous.

    A card built, typed into, then rebuilt from scratch must read back empty --
    that is what a browser does, and what the shared rail stub cannot express.
    """
    out = _run_rail(
        [_ASK],
        "railOpen='req_gtm';renderRail(railCache);"
        "$('fb_req_gtm').value='typed';"
        # Force a rebuild by forgetting the kept nodes, which is what the code
        # did on every render before the fix.
        "railNodes=new Map();renderRail(railCache);"
        "const result={note:$('fb_req_gtm').value};",
    )
    assert out["note"] == "", "the harness cannot tell a rebuild from a reuse"


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_a_typed_reply_note_survives_the_fifteen_second_rail_poll():
    """The poll re-renders with the SAME rows; the card must not be rebuilt.

    This is the exact sequence behind the lost Deny note: open the card, type,
    wait for one poll, click. Before the fix the click read an empty box.
    """
    out = _run_rail(
        [_ASK],
        "railOpen='req_gtm';renderRail(railCache);"
        "$('fb_req_gtm').value=" + json.dumps(_NOTE) + ";"
        "$('f_req_gtm_token').value='ghp_typed_but_not_submitted';"
        # ...the 15-second poll fires with identical rows.
        "renderRail(" + json.dumps([_ASK]) + ");"
        "const result={note:$('fb_req_gtm').value,"
        "secret:$('f_req_gtm_token').value,tabs:host.children.length};",
    )
    assert out["note"] == _NOTE, "the rail poll deleted what the user typed"
    assert out["secret"] == "ghp_typed_but_not_submitted", \
        "the rail poll deleted a key the user had pasted but not submitted"
    assert out["tabs"] == 1


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_an_unchanged_rail_is_not_touched_at_all_so_the_cursor_survives():
    """Keeping the value is not enough if the caret goes with the poll.

    Detaching a node blurs it, and re-appending does not give focus back, so a
    rail that re-appends its own unchanged cards still interrupts typing every
    fifteen seconds. When the answer is "exactly what is on screen", the render
    has to touch nothing.
    """
    out = _run_rail(
        [_ASK],
        "railOpen='req_gtm';renderRail(railCache);"
        "const box=$('fb_req_gtm');box.focus();box.value='half a sen';"
        "renderRail(" + json.dumps([_ASK]) + ");"
        "const result={note:box.value,stillFocused:focused===box,"
        "attached:inTree(box),sameNode:$('fb_req_gtm')===box};",
    )
    assert out["note"] == "half a sen"
    assert out["sameNode"] is True, "the card was rebuilt"
    assert out["attached"] is True, "the reused node was left out of the document"
    assert out["stillFocused"] is True, "the poll took the caret out of the box"


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_a_settled_rail_still_updates_its_heading():
    """The early return skips DOM writes to the CARDS, not the bookkeeping.

    The rail is read-only history now; unchanged cards must not leave the old
    Waiting on you heading behind when the pending composition changes.
    """
    optional = {"request_id": "sys_connect_llm", "kind": "LLM", "sticky": False,
                "status": "optional", "title": "Connect another LLM",
                "fields": [], "action": {"type": "connect", "use": "model",
                                         "setup": {"shapes": ["api_key"]}}}
    out = _run_rail(
        [_ASK],
        "railOpen=null;renderRail(railCache);"
        "const asking=$('rail-head').textContent;"
        # Now the ask is resolved and only the optional entry is left. No card
        # is on screen either way, so the rail is settled -- the heading is not.
        "renderRail(" + json.dumps([optional]) + ");"
        "const result={asking,after:$('rail-head').textContent,"
        "tabs:host.children.length};",
    )
    assert out["asking"] == "Request history"
    assert out["after"] == "Request history", (
        "a settled rail kept claiming work was waiting"
    )
    assert out["tabs"] == 0


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_a_new_ask_arriving_beside_an_open_card_keeps_what_was_typed_in_it():
    """The rail's COMPOSITION changed, so it has to be rebuilt -- and the card
    the user is mid-reply in has to come through that rebuild intact.

    This is the case node reuse exists for; the unchanged-rail early return does
    not cover it, because the rail genuinely is not what is on screen any more.
    An agent raising a second ask while you answer the first is ordinary.
    """
    arrived = {"request_id": "req_new", "kind": "API", "status": "pending",
               "title": "One more thing", "fields": [],
               "action": {"type": "answer"}}
    out = _run_rail(
        [_ASK],
        "railOpen='req_gtm';renderRail(railCache);"
        "$('fb_req_gtm').value='most of a reply';"
        "$('f_req_gtm_token').value='ghp_typed_but_not_submitted';"
        "renderRail(" + json.dumps([_ASK, arrived]) + ");"
        "const result={note:$('fb_req_gtm').value,"
        "secret:$('f_req_gtm_token').value,tabs:host.children.length};",
    )
    assert out["tabs"] == 2, "the new ask did not render"
    assert out["note"] == "most of a reply", (
        "a second ask arriving wiped the reply being typed into the first"
    )
    assert out["secret"] == "ghp_typed_but_not_submitted", (
        "a second ask arriving wiped a key pasted into the first"
    )


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_a_changed_row_is_rebuilt_rather_than_reused():
    """Reuse is keyed on the row: a row the agent edited must re-render."""
    edited = dict(_ASK, title="Replace the rejected GitHub token (updated)")
    out = _run_rail(
        [_ASK],
        "railOpen='req_gtm';renderRail(railCache);"
        "$('fb_req_gtm').value='stale';"
        "renderRail(" + json.dumps([edited]) + ");"
        "const result={text:text(host.children[0]),note:$('fb_req_gtm').value};",
    )
    assert "(updated)" in out["text"], "an edited ask kept its old card"


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_deny_and_clear_carry_the_typed_note_into_the_thread():
    """Accept already relayed its fields; a decision without the note is a
    decision the universe cannot understand (founder 2026-09-30)."""
    out = _run_rail(
        [_ASK],
        "const req=railCache[0];const result={"
        "deny:answerLine(req,'deny',{feedback:" + json.dumps(_NOTE) + "}),"
        "clear:answerLine(req,'clear',{feedback:" + json.dumps(_NOTE) + "}),"
        "deny_bare:answerLine(req,'deny',{}),"
        "accept:answerLine(req,'accept',{values:{token:'ghp_x'},"
        "feedback:" + json.dumps(_NOTE) + "})};",
    )
    assert out["deny"].startswith('Denied: "Replace the rejected GitHub token')
    assert out["deny"].endswith(_NOTE), f"Deny dropped the note: {out['deny']!r}"
    assert out["clear"].endswith(_NOTE), f"Clear dropped the note: {out['clear']!r}"
    assert out["deny_bare"] == ('Denied: "Replace the rejected GitHub token to '
                                'publish GTM Village"')
    # A secret field's value never enters the thread, note or no note.
    assert "ghp_x" not in out["accept"] and out["accept"].endswith(_NOTE)


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_an_account_change_takes_the_rail_cards_and_their_typed_values():
    """Keeping card nodes across a refresh must not keep them across a sign-out.

    The rail's secret field is a <textarea>, so `clearCredentialFields` -- which
    matches `input[type="password"]` -- never reached it. Now that a card node
    outlives every refresh, clearing it is an explicit account-change step.
    """
    out = _run_rail(
        [_ASK],
        "railOpen='req_gtm';renderRail(railCache);"
        "const box=$('f_req_gtm_token');box.value='ghp_pasted_never_submitted';"
        "$('fb_req_gtm').value='a private note';"
        "clearRailCards();"
        "const result={cards:host.children.length,kept:railNodes.size,"
        "open:railOpen,secret:box.value};",
    )
    assert out["cards"] == 0 and out["kept"] == 0, "a previous account's cards stayed"
    assert out["secret"] == "", \
        "a key pasted into a card and not submitted survived the account change"
    assert out["open"] is None
