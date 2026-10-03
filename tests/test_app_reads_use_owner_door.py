"""Every read the app renders goes through the owner door, and the rail never vanishes.

2026-09-30: the rail, the installed-UI library and the restore-access check all
read through the MCP connector, which bounds replies for a model's context. A
heavy account (the founder's) got truncation markers where the app expected data;
the rail hid itself without a word. The app now reads only through ``Owner``
(``/app/api/*``, no bound). MCP keeps the actions.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

from tinyassets.onboarding import render_app_html

_NODE = shutil.which("node")
_READ_OVER_MCP = re.compile(r"""callTool\(\s*["'`](read_graph|get_status)["'`]""")


def _html() -> str:
    html, _csp = render_app_html()
    return html


def test_the_app_never_reads_over_the_model_door():
    """The rendered page includes app_ui.js, so this covers both
    sources. A read added over MCP fails here, whatever its size today."""
    html = _html()
    assert _READ_OVER_MCP.findall(html) == []
    mcp = html[html.index("  const MCP = {"):html.index("  const Owner = {")]
    for read in ("getStatus(", "getModelOptions(", "getConversation(",
                 "readConversationChunk(", "listRequests("):
        assert read not in mcp, f"MCP must not carry the owner read {read}"


def test_the_owner_client_speaks_only_to_the_owner_door():
    html = _html()
    owner = html[html.index("  const Owner = {"):html.index("  // ---- UI ----")]
    assert '"/app/api/read"' in owner and '"/app/api/status"' in owner
    assert "/mcp" not in owner
    for read in ("getStatus()", "getModelOptions()", "getConversation(before)",
                 "readConversationChunk(id,offset,scope,agent)", "listRequests()"):
        assert read in owner


def test_the_mcp_calls_that_remain_are_actions():
    html = _html()
    tools = set(re.findall(r"""callTool\(\s*["'`]([a-z_]+)["'`]""", html))
    assert tools <= {"write_graph", "run_graph", "converse"}, tools


def _function_source(html: str, name: str) -> str:
    match = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", html)
    assert match, name
    i = html.index("{", match.end())
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                return html[match.start():j + 1]
    raise AssertionError(name)


_RAIL = r"""
class El{constructor(){this.hidden=true;this.textContent="";}}
const els={"request-rail":new El(),"rail-error":new El(),"rail-error-text":new El()};
const $=id=>els[id];
const token=()=>"t";
let rendered=null;
function renderRail(items){ rendered=items; els["request-rail"].hidden=false; }
const Owner={listRequests:async()=>{
  const r=SCENARIO.reply; if(r&&r.throw) throw new Error(r.throw); return r;
}};
__FUNCTIONS__
(async()=>{
  await refreshRail();
  console.log(JSON.stringify({rendered, railHidden:els["request-rail"].hidden,
    errorHidden:els["rail-error"].hidden, error:els["rail-error-text"].textContent}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""


def _rail(reply) -> dict:
    html = _html()
    functions = _function_source(html, "refreshRail") + "\n" + _function_source(html, "railFailed")
    program = ("const SCENARIO=" + json.dumps({"reply": reply}) + ";\n"
               + _RAIL.replace("__FUNCTIONS__", functions))
    with tempfile.TemporaryDirectory() as scratch:
        script = os.path.join(scratch, "rail.cjs")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(program)
        run = subprocess.run([_NODE, script], capture_output=True, text=True,
                             encoding="utf-8", timeout=60, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
@pytest.mark.parametrize("reply,reason", [
    # The exact 2026-09-30 shape: a truncation marker, no `pending` list.
    ({"truncated": True, "original_bytes": 34_000, "content": "{\"pend"}, "no list came back"),
    ({"error": "not_found"}, "not_found"),
    ({"throw": "your universe answered 500"}, "your universe answered 500"),
])
def test_a_rail_that_cannot_load_stays_visible_and_says_so(reply, reason):
    out = _rail(reply)
    assert out["rendered"] is None
    assert out["railHidden"] is False, "the rail must never vanish silently"
    assert out["errorHidden"] is False
    assert "Couldn’t load what’s waiting on you" in out["error"]
    assert reason in out["error"]


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_a_rail_that_loads_clears_the_failure_line():
    out = _rail({"pending": [{"request_id": "r1"}], "count": 1})
    assert out["rendered"] == [{"request_id": "r1"}]
    assert out["errorHidden"] is True and out["error"] == ""


def _method_source(html: str, head: str) -> str:
    start = html.index(head)
    i = html.index("{", start + len(head) - 1)
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                return html[start:j + 1]
    raise AssertionError(head)


_BUNDLE = r"""
const calls=[];
const ROWS=Array.from({length:437},(_,i)=>({agent_binding_id:"b"+i}));
const Owner={read:async(a)=>{ calls.push(a.limit);
  return {bindings:ROWS.slice(0,a.limit)}; }};
const ui={ PAGE:100, __READ_WHOLE__ };
(async()=>{
  const doc=await ui.readWhole({target:"agent_bindings",graph_id:"h"},"bindings");
  console.log(JSON.stringify({rows:doc.bindings.length, calls}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_a_custom_ui_reads_a_whole_list_not_a_first_page():
    """Codex round 1: a bundle's agents and automations stopped at 100, and the
    101st agent read as "no agent of yours"."""
    html = _html()
    method = _method_source(html, "async readWhole(args,key){")
    program = _BUNDLE.replace("__READ_WHOLE__", method)
    with tempfile.TemporaryDirectory() as scratch:
        script = os.path.join(scratch, "bundle.cjs")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(program)
        run = subprocess.run([_NODE, script], capture_output=True, text=True,
                             encoding="utf-8", timeout=60, check=False)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["rows"] == 437
    assert out["calls"] == [100, 400, 1600]
    for reader in ("async listAgents(){", "async listAutomations(){"):
        body = _method_source(html, reader)
        assert "this.readWhole(" in body and "limit:" not in body, reader


def test_a_custom_ui_conversation_read_is_paged_and_fails_loudly():
    html = _html()
    body = _method_source(html, "async readConversation(args){")
    assert "call.conversation_before=args.before" in body
    assert "conversation_limit:limit" in body
    assert "has_more:more" in body and "next_before:" in body
    assert 'throw new Error("your conversation could not be read")' in body


def test_a_reply_body_that_lands_after_an_account_switch_is_refused():
    """Codex round 2: headers from account A, B signs in while the body
    downloads, A's rail renders for B. The login is re-checked after the body."""
    html = _html()
    owner = html[html.index("  const Owner = {"):html.index("  // ---- UI ----")]
    body_read = owner.index("doc=await resp.json()")
    fence = owner.index("MCP._assertLogin(loginEpoch);", body_read)
    assert fence < owner.index("return doc;", body_read)


def test_a_custom_ui_run_list_says_when_older_runs_exist():
    html = _html()
    body = _method_source(html, "async listRuns(args){")
    assert "limit:limit+1" in body and "has_more:doc.runs.length>limit" in body
    # A bundle is untrusted: one request stays bounded, and the cut is SAID.
    assert "Math.min(args.limit,this.MAX_LIST_RUNS)" in body
