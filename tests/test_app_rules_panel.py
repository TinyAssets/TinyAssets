"""Harness D1b: the owner edits their agent's rules in the app's Account view.

Runs the page's own ``renderRules`` / ``saveRule`` under node: the list renders
one choice per rule, and loosening a hand-back asks the owner with the
server's own plain words before sending ``confirm_handback``.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.test_onboarding_app import _js_function
from tinyassets import onboarding

_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(_NODE is None, reason="node runs the page's own source")

_SHIM = r"""
class El{constructor(t){this.tagName=t;this.children=[];this._text="";this.value="";
  this.className="";this.listeners={};this.selected=false;}
  get textContent(){ return this._text; }
  set textContent(v){ this._text=v; this.children=[]; }
  appendChild(c){this.children.push(c);return c;}
  addEventListener(n,f){this.listeners[n]=f;}}
const document={createElement:t=>new El(t)};
const els={};
function $(id){ return els[id]||(els[id]=new El("div")); }
function authHeaders(){ return {}; }
const posts=[];
let confirmed=[];
const window={confirm:(text)=>{confirmed.push(text); return SCENARIO.confirm;}};
const LISTING={rules:[{id:1,action_class:"money.move",connection:"",operation:"",
  behaviour:"hand_off",note:""},{id:2,action_class:"app.write",connection:"",operation:"",
  behaviour:"do",note:""}],
  behaviours:{do:"Take action without asking",do_if_preapproved:"Take action if pre-approved",
    ask_first:"Ask before taking action",hand_off:"Hand off to you"},
  classes:{"money.move":"Moving money or making a payment","app.write":"Changing an app"},
  operation_kinds:[{id:9,connection:"stripe",method:"POST",path_prefix:"/v1/charges",
    kind:"payment"}], kinds:{read:"app.read",payment:"money.move"},
  review_on:["app.write"], review_off:[], review_never:["app.read"], review_always:[]};
async function fetch(url, init){
  const body=init.body?JSON.parse(init.body):null;
  if(body) posts.push(body);
  if(body&&body.undeclare&&!body.confirm)
    return {ok:false,status:409,
      json:async()=>({detail:"Calls to stripe will be decided as a write."})};
  if(body&&body.action_class==="money.move"&&!body.confirm_handback)
    return {ok:false,status:409,json:async()=>({detail:"Your agent will be able to move money."})};
  return {ok:true,status:200,json:async()=>LISTING};
}
"""

_FUNCS = ("rulesRequest", "rulesSay", "renderRules", "loadRules", "saveRule",
          "sendConfirmed")

_BODY = r"""
(async()=>{
  await loadRules();
  const rows=$("rules-list").children;
  const choices=rows.map(r=>r.children[1].children.filter(o=>o.selected).map(o=>o.value)[0]);
  await saveRule(LISTING.rules[0], "ask_first", false);
  console.log(JSON.stringify({rows:rows.length, choices,
    kinds:$("rules-kinds").children.length, posts, confirmed}));
})();
"""


def _run(tmp_path, scenario):
    page, _csp = onboarding.render_app_html()
    funcs = "\n".join(_js_function(page, name) for name in _FUNCS)
    script = tmp_path / "rules_panel.js"
    script.write_text(_SHIM + f"const SCENARIO={json.dumps(scenario)};\n" + funcs + _BODY,
                      encoding="utf-8")
    proc = subprocess.run([_NODE, str(script)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_each_rule_shows_its_current_choice(tmp_path):
    out = _run(tmp_path, {"confirm": False})
    assert out["rows"] == 2 and out["choices"] == ["hand_off", "do"]
    assert out["kinds"] == 1


def test_loosening_a_handback_asks_with_the_servers_words_then_confirms(tmp_path):
    out = _run(tmp_path, {"confirm": True})
    assert out["confirmed"] == ["Your agent will be able to move money."]
    assert [p.get("confirm_handback") for p in out["posts"]] == [None, True]


def test_declining_sends_nothing_more(tmp_path):
    out = _run(tmp_path, {"confirm": False})
    assert len(out["posts"]) == 1 and "confirm_handback" not in out["posts"][0]


_UNDECLARE = r"""
(async()=>{
  await sendConfirmed({undeclare:9});
  console.log(JSON.stringify({posts, confirmed}));
})();
"""


def test_removing_a_declaration_that_loosens_asks_first(tmp_path):
    page, _csp = onboarding.render_app_html()
    funcs = "\n".join(_js_function(page, name) for name in _FUNCS)
    script = tmp_path / "rules_undeclare.js"
    script.write_text(_SHIM + 'const SCENARIO={"confirm": true};\n' + funcs + _UNDECLARE,
                      encoding="utf-8")
    proc = subprocess.run([_NODE, str(script)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["confirmed"] == ["Calls to stripe will be decided as a write."]
    assert out["posts"] == [{"undeclare": 9}, {"undeclare": 9, "confirm": True}]


_CHECKS = r"""
(async()=>{
  await loadRules();
  const rows=$("rules-list").children;
  console.log(JSON.stringify(rows.map(r=>({
    check:(r.children[2]||{}).textContent||null, disabled:!!(r.children[2]||{}).disabled}))));
})();
"""


def test_each_consequential_rule_shows_its_check_and_handbacks_keep_it(tmp_path):
    """Reviews default off and every consequential kind is owner-controlled."""
    page, _csp = onboarding.render_app_html()
    funcs = "\n".join(_js_function(page, name) for name in _FUNCS)
    script = tmp_path / "rules_checks.js"
    script.write_text(_SHIM + 'const SCENARIO={"confirm": false};\n' + funcs + _CHECKS,
                      encoding="utf-8")
    proc = subprocess.run([_NODE, str(script)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [{"check": "Check is off", "disabled": False},
                                       {"check": "Checks first", "disabled": False}]


def test_review_default_notice_tracks_explicit_opt_ins(tmp_path):
    page, _csp = onboarding.render_app_html()
    funcs = "\n".join(_js_function(page, name) for name in _FUNCS)
    script = tmp_path / "rules_notice.js"
    script.write_text(_SHIM + 'const SCENARIO={"confirm": false};\n' + funcs + r'''
      const notices=[];
      for(const review_on of [[], ["app.write"], []]){
        renderRules({...LISTING, review_on});
        notices.push($("rules-review-notice").textContent);
      }
      console.log(JSON.stringify(notices));
    ''', encoding="utf-8")
    proc = subprocess.run([_NODE, str(script)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    notice = "Checks are now off unless you turn them on"
    assert json.loads(proc.stdout) == [notice, "", notice]
    assert 'id="rules-review-notice"' in page
