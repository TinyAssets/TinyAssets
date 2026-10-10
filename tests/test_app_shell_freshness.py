"""Shell responses revalidate; the version follows served bytes, not receipts."""

import asyncio
import shutil
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from tinyassets import onboarding


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_shell_never_reuses_a_stale_conditional_response(monkeypatch, method):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    client = TestClient(Starlette(routes=onboarding.onboarding_routes()))
    response = client.request(
        method,
        "/app",
        headers={
            "If-None-Match": '"old-build"',
            "If-Modified-Since": "Wed, 01 Jan 2025 00:00:00 GMT",
        },
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-tinyassets-build"] == onboarding.build_sha()
    assert response.headers["x-tinyassets-shell"] == onboarding.shell_version()
    assert "etag" not in response.headers
    if method == "GET":
        assert '"build": "' + response.headers["x-tinyassets-build"] + '"' in response.text


def test_shell_version_changes_with_each_served_source(tmp_path, monkeypatch):
    names = ("app.html", "app_ui.js", "app_recovery.js", "chat_render.js", "request_theme.json")
    for name in names:
        shutil.copyfile(onboarding._HTML_PATH.with_name(name), tmp_path / name)
    monkeypatch.setattr(onboarding, "_HTML_PATH", tmp_path / "app.html")
    versions = {onboarding.shell_version()}
    for name in names:
        path = tmp_path / name
        path.write_bytes(path.read_bytes() + b"\n")
        versions.add(onboarding.shell_version())
    assert len(versions) == len(names) + 1


def test_module_cache_key_and_shell_probe_are_independent(monkeypatch):
    from tinyassets.onboarding import app_modules

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    build = app_modules.build_segment()
    request = SimpleNamespace(method="GET", path_params={"build": build, "name": "main.js"})
    response = asyncio.run(app_modules.handle_app_module(request))
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    request.path_params["build"] = "0" * 16
    response = asyncio.run(app_modules.handle_app_module(request))
    assert response.status_code == 307
    assert response.headers["cache-control"] == "no-store"
    assert build in response.headers["location"]


@pytest.mark.parametrize("late_text", ["latest unsent draft", ""])
def test_departing_page_cannot_consume_its_saved_draft(tmp_path, late_text):
    import json
    import subprocess

    source = onboarding._HTML_PATH.with_name("app_recovery.js").read_text(encoding="utf-8")
    source = source.replace("__TA_ONBOARDING_CONFIG__", "{}")
    script = tmp_path / "draft-race.cjs"
    script.write_text(
        """const vm=require('node:vm');
const source=SOURCE;
const data=new Map(), scope={owner:'owner',home:'home',agent:'main'};
function page(text){
  const input={value:text,dispatchEvent(){}},timers=[],events={};
  const context={console,URL,Event:class{},Date,
    sessionStorage:{getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)},
    document:{getElementById:id=>id==='composer-input'?input:{hidden:true},addEventListener(){}},
    location:{href:'https://example.test/app',replace(){}},
    setInterval:fn=>timers.push(fn),setTimeout(){},clearTimeout(){}};
  context.window={addEventListener:(name,fn)=>{events[name]=fn;}};
  vm.createContext(context);vm.runInContext(source,context);
  const api=context.window.AppRecovery;
  api.attach({scope:()=>scope,health:()=>({expected:false,healthy:true})});api.started();
  return {input,timers,events,api};
}
const old=page('unsent draft');old.api.upgrade();
old.timers[0]();old.timers[0](); // Response has not arrived; old page keeps ticking.
const saved=JSON.parse(data.get('ta_recovery_draft')||'null');
old.input.value=LATE_TEXT;old.events.pagehide();
const next=page('');
console.log(JSON.stringify({saved,restored:next.input.value,remaining:data.has('ta_recovery_draft')}));
""".replace("SOURCE", json.dumps(source)).replace("LATE_TEXT", json.dumps(late_text)),
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, check=True)
    observed = json.loads(result.stdout)
    assert observed["saved"]["text"] == "unsent draft"
    assert observed["restored"] == late_text
    assert observed["remaining"] is False


def test_deploy_commit_header_is_preserved_separately_from_content_version(monkeypatch):
    sha = "a" * 40
    monkeypatch.setattr(onboarding, "build_sha", lambda: sha)
    response = onboarding.app_response()
    assert response.headers["x-tinyassets-build"] == sha
    assert response.headers["x-tinyassets-shell"] == onboarding.shell_version()
    assert response.headers["x-tinyassets-shell"] != sha
    assert ('"shell_version": "' + onboarding.shell_version() + '"') in response.body.decode()


def test_content_version_probe_updates_before_release_receipt_moves(tmp_path):
    from tests.test_onboarding_app import _run_app

    result = _run_app(
        tmp_path,
        {
            "kind": "build",
            "build": "same-receipt",
            "liveBuild": "same-receipt",
            "shellVersion": "old-content",
            "liveShell": "new-content",
        },
    )
    assert result["reloaded"] is True
    assert result["fetched"] == 1
