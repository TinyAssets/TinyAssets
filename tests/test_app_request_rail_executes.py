"""The rail's four verbs, EXECUTED under node, not grepped.

What each click says in the thread is the founder's own line and the agent
reads it as a decision, so the wording is pinned by running the functions the
page ships (lifted out of the rendered app by brace matching).
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

_NODE = shutil.which("node")


def _function_source(html: str, name: str) -> str:
    start = html.index(f"function {name}(")
    if html[max(0, start - 6):start] == "async ":
        start -= 6
    i = html.index("{", start)
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                return html[start:j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _extract() -> str:
    from tinyassets.onboarding import render_app_html

    html, _csp = render_app_html()
    return "\n".join(_function_source(html, n) for n in
                     ("frameTitle", "answerLine", "replyLine", "refusedGrantLine"))


_HARNESS = r"""
%(app)s
const req = {request_id: "req_1", title: 'Extend  "GitHub"   access\nto the repo',
             fields: [{name: "token", type: "secret"}, {name: "note", type: "text"}]};
const out = {
  accept: answerLine(req, "accept", {values: {}}),
  accept_with_values: answerLine(req, "accept", {values: {token: "ghp_abc", note: "ok"}}),
  deny: answerLine(req, "deny", {feedback: "not that file"}),
  clear: answerLine(req, "clear", {dont_ask_again: true}),
  reply: replyLine(req, "  can you narrow   it? "),
  refused: refusedGrantLine(req, "a git scope needs ONE host"),
};
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_each_verb_says_what_the_founder_decided():
    script = _HARNESS % {"app": _extract()}
    run = subprocess.run([_NODE, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60, check=False)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)

    title = "Extend 'GitHub' access to the repo"       # one line, quotes framed
    assert out["accept"] == f'Approved: "{title}"'
    assert out["accept_with_values"] == f'Answered "{title}" — token: (provided); note: ok'
    assert "ghp_abc" not in out["accept_with_values"], "a secret never enters the thread"
    assert out["deny"] == f'Denied: "{title}" — not that file'
    assert out["clear"] == f'Cleared: "{title}" (and don’t ask me this again)'
    assert out["reply"] == f'About "{title}": can you narrow it?'
    assert out["refused"] == f'Could not grant "{title}": a git scope needs ONE host'


def test_the_rail_renders_the_four_verbs_and_only_those():
    from tinyassets.onboarding import render_app_html

    html, _csp = render_app_html()
    rail = _function_source(html, "answerRail")
    assert "refusedGrantLine(req" in rail, "a refused grant is relayed to the universe"
    assert 'if(!text){ note.textContent = "Type something to send."' in rail


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
@pytest.mark.parametrize("succeeds", [False, True])
def test_model_setup_retry_is_visible_and_not_relayed_to_an_unpowered_agent(succeeds):
    from tinyassets.onboarding import render_app_html

    html, _csp = render_app_html()
    functions = _extract() + "\n" + _function_source(html, "answerRail")
    response = ({"status": "answered", "suppressed": False, "receipt": "Reconnected"}
                if succeeds else {"error": "provider_authority_denied", "request_pending": True,
                                  "detail": "Source temporarily unavailable"})
    script = functions + r"""
const sent = [], note = {}, buttons = [{}];
const req = {request_id: "models", title: "Model access", fields: [],
             action: {type: "bind_model_access"}};
const $ = id => id.startsWith("mute_") ? {checked:true}
  : id === "btn-send" ? {disabled:false} : null;
const sendTurn = line => sent.push(line);
const refreshRail = async () => {};
let railOpen = "models";
const MCP = {answerRequest:async () => (RESPONSE)};
answerRail(req, "accept", note, buttons).then(() => {
  process.stdout.write(JSON.stringify({sent, note:note.textContent, disabled:buttons[0].disabled}));
});
""".replace("RESPONSE", json.dumps(response))
    run = subprocess.run([_NODE, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["disabled"] is False
    if succeeds:
        assert result["sent"] == []  # The answer transaction owns the asking-agent wake.
        assert "Reconnected" in result["note"]
    else:
        assert result["sent"] == []
        assert "Source temporarily unavailable" in result["note"]
        assert "request stays open" in result["note"]


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
@pytest.mark.parametrize("mode", ["accept", "deny", "clear"])
@pytest.mark.parametrize("signed_in", [True, False])
def test_patch_consent_rail_uses_protected_owner_door(mode, signed_in):
    from tinyassets.onboarding import render_app_html

    html, _csp = render_app_html()
    script = _extract() + "\n" + _function_source(html, "answerRail") + r"""
const sent=[], calls=[], links=[], buttons=[{}];
const note={appendChild:el=>links.push(el)};
const document={createElement:()=>({})};
const req={request_id:'patch',title:'Patch intake',fields:[],action:{type:'grant_patch_intake'}};
const $=id=>id==='btn-send'?{disabled:false}:null;
const sendTurn=line=>sent.push(line), refreshRail=async()=>{};
let railOpen='patch';
const MCP={answerRequest:async()=>{throw new Error('Bearer route used');}};
const InlineApprovals={post:async(operation,payload)=>{
  calls.push({operation,payload});
  if(!SIGNED_IN){const error=new Error('Owner sign-in required');error.signIn=true;throw error;}
  return {status:'answered'};
}};
answerRail(req, MODE, note, buttons).then(()=>{
  process.stdout.write(JSON.stringify({calls,sent,links,disabled:buttons[0].disabled}));
});
""".replace("SIGNED_IN", json.dumps(signed_in)).replace("MODE", json.dumps(mode))
    run = subprocess.run([_NODE, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["calls"] == [{"operation": "answer", "payload": {
        "request_id": "patch", **({"values": {}} if mode == "accept" else
                                {"decision": "declined"} if mode == "deny" else
                                {"dismiss": True}),
    }}]
    assert result["disabled"] is False
    assert result["sent"] == []  # Neither success nor refusal targets the selected chat.
    assert [link["href"] for link in result["links"]] == (
        [] if signed_in else ["/app/owner-sign-in"]
    )
