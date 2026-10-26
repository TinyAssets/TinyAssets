"""Pending native sign-in never delays a drain or strands its page."""
import asyncio
import base64
import json
import threading
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.real_browser
@pytest.mark.parametrize("operation", ["sleep", "snapshot", "revoke", "delete"])
def test_pending_login_cancels_without_blocking_and_page_can_retry(
        monkeypatch, tmp_path, operation):
    import httpx
    from playwright.sync_api import sync_playwright
    from starlette.responses import JSONResponse

    from tinyassets import owner_activity, owner_ingress, owner_runtime
    from tinyassets.auth.provider import Identity
    from tinyassets.owner_process import OwnerProcessCancelled
    from tinyassets.providers.subscription_runtime import Login

    gate = owner_activity.Activity()
    monkeypatch.setattr(owner_activity, "activity", gate)
    waiting = threading.Event()
    release = threading.Event()

    def run(*args, should_cancel, **kwargs):
        waiting.set()
        assert release.wait(5)
        assert should_cancel()
        raise OwnerProcessCancelled("cancelled")

    runtime = SimpleNamespace(
        image_install=True, install=lambda: None, lease=lambda **kw: nullcontext(),
        authenticated=lambda: False, cli_login={"login": {"argv": []}},
        argv=lambda args: args, run=run,
        accept=lambda: pytest.fail("cancelled login was accepted"),
    )
    idle_since = gate.last
    secret = tmp_path / "secret"
    secret.write_text("s" * 43)
    monkeypatch.setattr(owner_runtime, "current", lambda: SimpleNamespace(
        principal="owner", uid=300001))
    ingress = owner_ingress.OwnerIngress(JSONResponse({"status": "waiting"}), secret)
    identity = Identity(user_id="owner", username="owner")

    async def poll():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(ingress),
                                     base_url="http://box") as client:
            response = await client.post("/app/subscription-connect/poll", headers={
                "x-box-secret": "s" * 43, "x-owner": "owner", "x-owner-identity":
                base64.urlsafe_b64encode(json.dumps(identity.to_dict()).encode()).decode()})
            assert response.status_code == 200

    login = Login(runtime).start()
    try:
        assert waiting.wait(5)
        asyncio.run(poll())
        assert gate.active == 0 and gate.last == idle_since
        if operation == "delete":
            assert gate.quiesce(0)
        else:
            state = gate.status(**({"drain": True} if operation == "sleep"
                                   else {operation: True}))
            assert state["active"] == 0 and state["draining"]
        assert login.cancelled.is_set()
        # Even before the CLI exits, pending sign-in cannot delay shutdown.
        assert login.thread.is_alive() and gate.active == 0
    finally:
        release.set()
        login.thread.join(5)
    assert not login.thread.is_alive() and login.status == "cancelled"
    # The idle clock held while the sign-in was pending and restarts when it ends.
    assert not gate.pending_logins and gate.last > idle_since
    late = Login(runtime).start()
    late.thread.join(5)
    assert late.cancelled.is_set() and late.status == "cancelled"

    source = Path("tinyassets/onboarding/app/main.js").read_text("utf-8")
    controller = source[source.index("  const SubscriptionSources={"):]
    controller = controller[:controller.index("\n  };") + 6]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.set_content('<p id="status"></p><button id="connect">Connect</button>')
        page.add_script_tag(content="""
          const NATIVE=true,DESKTOP_SHELL=false,MCP={_loginEpoch:1};
          const pkceStore={setItem(){},removeItem(){}},queueOwner='owner';
          const appendMessage=()=>{},sleep=async()=>{};
          const ensureFreshToken=async()=>{},token=()=>true,authHeaders=()=>({});
        """ + controller)
        for failure in ("cancelled", "http", "network"):
            for resume in (False, True):
                result = page.evaluate("""async ({failure,resume})=>{
                  globalThis.fetch=async()=>{
                    if(failure==='network')throw Error('offline');
                    return {ok:failure!=='http',json:async()=>({status:'cancelled'})};
                  };
                  const post=SubscriptionSources.post.bind(SubscriptionSources);
                  SubscriptionSources.post=async(op,body,epoch)=>op==='begin'
                    ?{flow:'flow',status:'waiting'}:op==='cancel'?{}:post(op,body,epoch);
                  const ui={status:document.getElementById('status'),
                    button:document.getElementById('connect'),cancel:{},input:{},submit:{}};
                  await SubscriptionSources.start({id:'fixture',name:'Fixture'},ui,
                    resume?{flow:'flow'}:null);
                  return {text:ui.status.textContent,disabled:ui.button.disabled};
                }""", {"failure": failure, "resume": resume})
                # A dropped connection keeps the flow for a reload; a refusal or a
                # server-reported end says to start again.
                expected = ("Connection interrupted. Reload this page to continue."
                            if failure == "network"
                            else "Sign-in was interrupted. Tap Connect to try again.")
                assert result == {"text": expected, "disabled": False}
        browser.close()


pytestmark = [
    pytest.mark.verifies(
        'box-subscription-sources:Broker custody and one-run native leases',
    ),
]
