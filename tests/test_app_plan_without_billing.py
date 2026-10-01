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

NAMES = ("planEndsOn", "renderPlan", "onPlanClick", "loadPlan")

PRELUDE = """
const BILLING_OFF_NOTE="BILLING_OFF";
let NATIVE=false, PLAN=null;
const chip={hidden:true,textContent:'',title:''};
const $=id=>id==='btn-plan'?chip:null;
const messages=[], calls=[];
const appendMessage=(kind,text)=>messages.push(text);
const startSubscribe=async()=>calls.push('subscribe');
const startCancel=async()=>calls.push('cancel');
const confirm=()=>true;
let STATUS={};
const billingFetch=async(path,method)=>{calls.push(method+' '+path);
  return {ok:true,json:async()=>STATUS};};
const location={href:'https://tinyassets.io/app?upgrade=1'};
const history={replaceState:()=>{}};
const out=()=>console.log(JSON.stringify({chip,messages,calls}));
"""


def run(status: dict, body: str = "") -> dict:
    return run_js(functions(*NAMES) + PRELUDE + "STATUS=" + json.dumps(status) + ";"
                  + "(async()=>{await loadPlan();" + body + "out();})();")


def test_a_free_user_sees_no_upgrade_chip_when_billing_is_off():
    out = run({"tier": "free", "billing_enabled": False, "upgrade_url": "/app?upgrade=1"})

    assert out["chip"]["hidden"] is True
    # Not even the ?upgrade=1 entry starts a checkout that cannot work.
    assert "subscribe" not in out["calls"]


def test_a_paid_user_keeps_the_chip_and_is_told_why_it_cannot_change_the_plan():
    out = run({"tier": "paid", "billing_enabled": False, "ends_at": None},
              "onPlanClick();")

    assert out["chip"]["hidden"] is False
    assert out["chip"]["textContent"] == "Paid plan"
    assert out["chip"]["title"] == "BILLING_OFF"
    # Cancel goes through Stripe too: no confirm, no cancel request.
    assert "cancel" not in out["calls"]
    assert out["messages"] == ["BILLING_OFF"]


def test_with_billing_on_the_chip_still_upgrades():
    out = run({"tier": "free", "billing_enabled": True, "upgrade_url": "/app?upgrade=1"},
              "onPlanClick();")

    assert out["chip"]["hidden"] is False and out["chip"]["textContent"] == "Upgrade"
    # Once from the ?upgrade=1 entry, once from the click.
    assert out["calls"].count("subscribe") == 2
    assert out["messages"] == []


def test_a_status_without_the_field_is_not_read_as_billing_off():
    """The no-home answer carries no ``billing_enabled``; absent is not false."""
    out = run({"tier": "free", "ends_at": None, "reason": "no_home_universe"})

    assert out["chip"]["hidden"] is False and out["chip"]["textContent"] == "Upgrade"


def test_the_phone_app_still_shows_no_plan_chip():
    out = run_js(functions(*NAMES) + PRELUDE + """
NATIVE=true; STATUS={tier:'free',billing_enabled:true};
(async()=>{await loadPlan();out();})();""")

    assert out["chip"]["hidden"] is True and "subscribe" not in out["calls"]
