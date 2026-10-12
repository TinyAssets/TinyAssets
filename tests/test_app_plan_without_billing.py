"""A server without billing offers nothing it cannot sell.

A contributor's clone or a self-hosted box has no Stripe keys, so
``/app/billing/status`` reports ``billing_enabled: false``. The plan chip used
to say "Upgrade" there anyway and lead to "Billing is not available right now"
after a checkout request that could never work. The shipped functions are pulled
out of ``app.html`` and run under Node with the DOM, ``fetch`` and the message
line replaced.
"""
from __future__ import annotations

import json

from tests.test_app_browser_notifications import functions, run_js

NAMES = ("canPurchase", "planEndsOn", "renderPlan", "onPlanClick", "loadPlan",
         "showLimitPrompt")

PRELUDE = """
const BILLING_OFF_NOTE="BILLING_OFF";
let NATIVE=false, PLAN=null;
const chip={hidden:true,textContent:'',title:''};
const view={hidden:true,textContent:''};
const $=id=>id==='btn-plan'?chip:id==='plan-view'?view:null;
const messages=[], calls=[];
const appendMessage=(kind,text,extra)=>{messages.push({text,upgrade:!!extra}); return {};};
const startSubscribe=async()=>calls.push('subscribe');
const startCancel=async()=>calls.push('cancel');
const confirm=()=>true;
let STATUS={};
const billingFetch=async(path,method)=>{calls.push(method+' '+path);
  return {ok:true,json:async()=>STATUS};};
const location={href:'https://tinyassets.io/app?upgrade=1'};
const history={replaceState:()=>{}};
const out=()=>console.log(JSON.stringify({chip,view,messages,calls}));
"""


def run(status: dict, body: str = "") -> dict:
    return run_js(functions(*NAMES) + PRELUDE + "STATUS=" + json.dumps(status) + ";"
                  + "(async()=>{await loadPlan();" + body + "out();})();")


def test_a_free_user_sees_no_upgrade_chip_when_billing_is_off():
    out = run({"tier": "free", "billing_enabled": False, "upgrade_url": "/app?upgrade=1"})

    assert out["chip"]["hidden"] is True
    # Not even the ?upgrade=1 entry starts a checkout that cannot work.
    assert "subscribe" not in out["calls"]
    assert out["messages"] == []


def test_a_paid_user_keeps_the_chip_and_is_told_why_it_cannot_change_the_plan():
    out = run({"tier": "paid", "billing_enabled": False, "ends_at": None},
              "onPlanClick();")

    assert out["chip"]["hidden"] is False
    assert out["chip"]["textContent"] == "Paid plan"
    assert out["chip"]["title"] == "BILLING_OFF"
    # Cancel goes through Stripe too: no confirm, no cancel request.
    assert "cancel" not in out["calls"]
    assert out["messages"] == [{"text": "BILLING_OFF", "upgrade": False}]


def test_with_billing_on_the_chip_still_upgrades():
    out = run({
        "tier": "free", "billing_enabled": True, "label": "Upgrade",
        "can_purchase_on_surface": True, "upgrade_available": True,
    }, "onPlanClick();")

    assert out["chip"]["hidden"] is False and out["chip"]["textContent"] == "Upgrade"
    # Once from the ?upgrade=1 entry, once from the click.
    assert out["calls"].count("subscribe") == 2
    assert out["messages"] == []


def test_a_missing_purchase_flag_does_not_offer_upgrade():
    """Buy controls fail closed. Absent is not permission to purchase."""
    out = run({"tier": "free", "billing_enabled": True, "upgrade_url": "/app?upgrade=1"})

    assert out["chip"]["hidden"] is True
    assert "subscribe" not in out["calls"]


def test_a_missing_billing_enabled_is_not_read_as_billing_off():
    out = run({
        "tier": "free", "label": "Upgrade",
        "can_purchase_on_surface": True, "upgrade_available": True,
    }, "onPlanClick();")

    assert out["chip"]["hidden"] is False
    assert out["chip"]["textContent"] == "Upgrade"
    assert out["chip"]["title"] != "BILLING_OFF"


def test_android_shows_the_plan_and_no_buy_control():
    out = run_js(functions(*NAMES) + PRELUDE + """
NATIVE=true;
STATUS={tier:'free',billing_enabled:true,label:'Free',
  can_purchase_on_surface:false,upgrade_available:true,
  purchase_unavailable_reason:'unavailable_in_app'};
(async()=>{await loadPlan();out();})();""")

    assert out["chip"]["hidden"] is True
    assert out["view"]["hidden"] is False
    assert out["view"]["textContent"] == "Free"
    assert "subscribe" not in out["calls"]
    assert "Upgrade" not in out["view"]["textContent"]


def test_the_shell_flag_does_not_hide_a_surface_that_may_purchase():
    """NATIVE is how the wrapper names the surface. The chip reads the field."""
    out = run_js(functions(*NAMES) + PRELUDE + """
NATIVE=true;
STATUS={tier:'free',label:'Upgrade',can_purchase_on_surface:true,upgrade_available:true};
(async()=>{await loadPlan();out();})();""")

    assert out["chip"]["hidden"] is False
    assert out["chip"]["textContent"] == "Upgrade"


def test_a_limit_prompt_offers_upgrade_only_when_the_surface_may_purchase():
    out = run_js(functions("canPurchase", "showLimitPrompt") + """
class El{ constructor(){ this.children=[]; }
  appendChild(c){ this.children.push(c); return c; }
  addEventListener(){} }
const document={createElement:()=>new El()};
const messages=[];
const appendMessage=(kind,text,extra)=>{messages.push({text,upgrade:!!extra}); return {};};
let PLAN={can_purchase_on_surface:true,upgrade_available:true};
showLimitPrompt('storage');
showLimitPrompt('runs');
PLAN={can_purchase_on_surface:false,upgrade_available:true,label:'Free'};
showLimitPrompt('storage');
console.log(JSON.stringify(messages));
""")

    assert out == [
        {"text": "You've reached your storage limit.", "upgrade": True},
        {"text": "You've reached your simultaneous runs limit.", "upgrade": True},
        {"text": "You've reached your storage limit.", "upgrade": False},
    ]
