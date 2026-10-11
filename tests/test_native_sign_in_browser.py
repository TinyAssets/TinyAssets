"""Rendered app proof of callback-only native completion and launch failures."""

import pytest

from tests.test_app_chat_cloud_browser import app_url as app_url_fixture

app_url = app_url_fixture
pytestmark = pytest.mark.real_browser


@pytest.mark.parametrize("platform", ["android", "ios", "desktop"])
def test_native_waits_for_matching_return_then_enters_chat(app_url, platform):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            user_agent="TinyAssets Electron/43.7.9" if platform == "desktop" else None
        )
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})
        page.add_init_script(
            """(() => {const platform=PLATFORM;
          window.launches=[];window.returns={};window.__TA_ONBOARDING__={configured:true};
          if(platform==='desktop')window.tinyassetsDesktop={
            openExternal:async url=>launches.push(url),onAppReturn:callback=>returns.app=callback};
          else window.Capacitor={isNativePlatform:()=>true,getPlatform:()=>platform,Plugins:{
            App:{getInfo:async()=>({id:'io.tinyassets.app'}),addListener:(name,cb)=>returns[name]=cb,
                 getLaunchUrl:async()=>({})},
            Browser:{open:async({url})=>launches.push(url),close:async()=>{}},
            SystemAuth:{open:async({url})=>{launches.push(url);
              return new Promise(resolve=>returns.ios=resolve);}}
          }};
        })();""".replace("PLATFORM", repr(platform))
        )
        exchanges = []
        page.route(
            "**/app/native-sign-in",
            lambda route: route.fulfill(
                json={"ref": "r" * 43, "url": "https://id.example/authorize"}
            ),
        )

        def exchange(route):
            body = route.request.post_data_json
            if "native_ref" not in body:
                route.fulfill(status=401, json={"error": "no_session"})
                return
            exchanges.append(body)
            route.fulfill(
                json={"access_token": "fixture-token", "expires_in": 300, "session_ref": "h" * 43}
            )

        page.route("**/app/token", exchange)
        page.route(
            "**/app/me",
            lambda route: route.fulfill(
                json={
                    "principal_id": "fixture-owner",
                    "universe_id": "fixture-home",
                    "name": "Fixture",
                }
            ),
        )
        page.goto(app_url)
        expect(page.locator("#view-signin")).to_be_visible()
        page.locator("#btn-signin").click()
        page.wait_for_function("launches.length===1")
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        assert exchanges == []
        scheme = "tinyassets-desktop" if platform == "desktop" else "tinyassets"
        wrong = f"{scheme}://auth?signin={'x' * 43}&return_secret={'s' * 43}"
        assert page.evaluate("url=>handleAppReturn(url)", wrong) is False
        assert exchanges == []
        good = f"{scheme}://auth?signin={'r' * 43}&return_secret={'s' * 43}"
        if platform == "ios":
            page.evaluate("url=>returns.ios({url})", good)
        elif platform == "android":
            page.evaluate("url=>returns.appUrlOpen({url})", good)
        else:
            page.evaluate("url=>returns.app(url)", good)
        expect(page.locator("#view-chat")).to_be_visible()
        assert len(exchanges) == 1 and exchanges[0]["return_secret"] == "s" * 43
        assert exchanges[0]["native_ref"] == "r" * 43
        assert page.evaluate("token()") == "fixture-token"
        assert page.evaluate("pkceStore.getItem(PKCE_KEY)") is None
        browser.close()


def test_desktop_launch_failure_is_visible_and_clears_pending(app_url):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(user_agent="TinyAssets Electron/43.7.9")
        page.add_init_script("""window.__TA_ONBOARDING__={configured:true};
          window.tinyassetsDesktop={onAppReturn:()=>{},openExternal:async()=>{
            throw new Error('The system browser could not open. '+
              'Check your default browser and try again.');}};""")
        page.route(
            "**/app/native-sign-in",
            lambda route: route.fulfill(
                json={"ref": "r" * 43, "url": "https://id.example/authorize"}
            ),
        )
        page.goto(app_url)
        expect(page.locator("#view-signin")).to_be_visible()
        page.locator("#btn-signin").click()
        expect(page.locator("#signin-notice")).to_contain_text("system browser could not open")
        expect(page.locator("#btn-signin")).to_be_enabled()
        assert page.evaluate("pkceStore.getItem(PKCE_KEY)") is None
        browser.close()


def test_cached_android_legacy_return_exchanges_saved_verifier_and_enters_chat(app_url):
    from playwright.sync_api import expect, sync_playwright

    state = "app." + "r" * 24
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.add_init_script(
            """(() => {window.returns={};window.__TA_ONBOARDING__={configured:true};
          const now=Date.now;Date.now=()=>Math.min(now(),1792700000000);
          localStorage.setItem('ta_pkce',JSON.stringify({state:STATE,verifier:'v'.repeat(64)}));
          window.Capacitor={isNativePlatform:()=>true,getPlatform:()=>'android',Plugins:{
            App:{getInfo:async()=>({id:'io.tinyassets.app'}),addListener:(name,cb)=>returns[name]=cb,
                 getLaunchUrl:async()=>({})},
            Browser:{open:async()=>{},close:async()=>{}}}};
        })();""".replace("STATE", repr(state))
        )
        exchanges = []

        def exchange(route):
            body = route.request.post_data_json
            if "code" not in body:
                route.fulfill(status=401, json={"error": "no_session"})
                return
            exchanges.append(body)
            route.fulfill(
                json={"access_token": "legacy-token", "expires_in": 300, "session_ref": "h" * 43}
            )

        page.route("**/app/token", exchange)
        page.route(
            "**/app/me",
            lambda route: route.fulfill(
                json={"principal_id": "fixture-owner", "universe_id": "fixture-home"}
            ),
        )
        page.goto(app_url)
        expect(page.locator("#view-signin")).to_be_visible()
        wrong = f"tinyassets://auth?code=legacy-code&state=app.{'w' * 24}"
        assert page.evaluate("url=>handleAppReturn(url)", wrong) is False
        assert exchanges == []
        page.evaluate(
            "url=>returns.appUrlOpen({url})", f"tinyassets://auth?code=legacy-code&state={state}"
        )
        expect(page.locator("#view-chat")).to_be_visible()
        assert exchanges == [
            {"code": "legacy-code", "code_verifier": "v" * 64, "redirect_uri": app_url}
        ]
        assert page.evaluate("token()") == "legacy-token"
        assert page.evaluate("pkceStore.getItem(PKCE_KEY)") is None
        browser.close()
