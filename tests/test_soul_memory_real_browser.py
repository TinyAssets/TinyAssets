"""The shipped Account editors in Chromium against real owner handlers."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_app_soul import app  # noqa: F401 -- shared owner handler fixture
from tests.test_onboarding_app import _js_function
from tinyassets import onboarding

pytestmark = pytest.mark.real_browser


@pytest.fixture
def editor(app):  # noqa: F811 -- imported pytest fixture
    root, call = app
    html, _ = onboarding.render_app_html()
    start = html.index('<section id="view-account"')
    markup = html[start:html.index("</section>", start) + len("</section>")]
    markup = markup.replace('class="view view--connect" hidden', 'class="view view--connect"')
    funcs = "\n".join(_js_function(html, name) for name in (
        "memoryContext", "ownsMemory", "clearTypedValues", "clearMemoryState",
        "memoryRequest", "renderMemory", "loadMemory", "saveMemory",
        "soulRequest", "renderSoul", "loadSoul"))
    script = '''
      const $=id=>document.getElementById(id), MCP={_loginEpoch:1};
      let queueOwner='alice',queueScope='u-alice';
      function authHeaders(){return {Authorization:'Bearer alice'};}
      function loadProfile(){}
    ''' + funcs + '''
      $('btn-soul-reload').addEventListener('click',loadSoul);
      $('btn-memory-add').addEventListener('click',()=>saveMemory({text:$('memory-new').value}));
      loadSoul();loadMemory();
    '''
    document = f"<!doctype html><html><body>{markup}<script>{script}</script></body></html>"
    # Imported here like the other real_browser suites: the slow-tests job has no
    # Playwright, and a module-level import fails collection there.
    from playwright.sync_api import sync_playwright

    with ThreadPoolExecutor(max_workers=1) as server, sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.set_default_timeout(10000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(request):
            path = request.request.url.split("tinyassets.io")[-1]
            if path == "/app":
                request.fulfill(status=200, content_type="text/html; charset=utf-8", body=document)
                return
            handler = onboarding._handle_memory if path == "/app/memory" else None
            kwargs = {"handler": handler} if handler else {}
            status, body = server.submit(call, request.request.method,
                                         request.request.post_data_json, **kwargs).result()
            request.fulfill(status=status, content_type="application/json", body=json.dumps(body))

        page.route("https://tinyassets.io/**", route)
        page.goto("https://tinyassets.io/app")
        assert not errors, errors
        page.get_by_role("textbox", name="Soul — purpose and style").wait_for()
        yield page, root
        browser.close()


def test_phone_owner_edits_files_deletes_one_memory_and_reloads(editor):
    page, root = editor
    for label, path, text in (
        ("Soul — purpose and style", "soul.md", "# Soul\nCalm and concise"),
        ("Identity — name and personality", "identity.md", "# Identity\nName: Willow"),
        ("Memory — full file", "MEMORY.md", "# Notes\n- Likes tea\n- Likes walks\n"),
    ):
        page.get_by_role("textbox", name=label).fill(text)
        page.get_by_role("button", name="Save " + path, exact=True).click()
        page.get_by_text("Saved " + path + ".", exact=True).wait_for()
        assert (root / "u-alice" / path).read_text() == text
    rows = page.locator("#memory-list .rule-row")
    assert rows.count() == 2
    rows.first.get_by_role("button", name="Delete").click()
    page.wait_for_function("document.querySelectorAll('#memory-list .rule-row').length===1")
    assert "Likes tea" not in (root / "u-alice" / "MEMORY.md").read_text()
    assert "Likes walks" in (root / "u-alice" / "MEMORY.md").read_text()
    page.reload()
    soul = page.get_by_role("textbox", name="Soul — purpose and style")
    assert soul.input_value() == "# Soul\nCalm and concise"
    assert "Likes walks" in page.get_by_role("textbox", name="Memory — full file").input_value()
    assert "Likes tea" not in page.get_by_role("textbox", name="Memory — full file").input_value()


def test_conflict_keeps_draft_and_account_reset_erases_it(editor):
    page, root = editor
    field = page.get_by_role("textbox", name="Soul — purpose and style")
    field.fill("unsaved private draft")
    (root / "u-alice" / "soul.md").write_text("newer agent soul")
    page.get_by_role("button", name="Save soul.md", exact=True).click()
    page.get_by_text("This file changed. Reload before saving again.", exact=True).wait_for()
    assert field.input_value() == "unsaved private draft"
    assert (root / "u-alice" / "soul.md").read_text() == "newer agent soul"
    page.evaluate("MCP._loginEpoch++;clearMemoryState();queueOwner='bob';queueScope='u-bob';")
    assert page.locator("#soul-editors textarea").count() == 0
    assert page.locator("#soul-status").inner_text() == ""


def test_previous_account_controls_and_delayed_response_are_discarded(editor):
    page, root = editor
    page.get_by_role("textbox", name="Soul — purpose and style").fill("Alice private draft")
    page.evaluate("window.oldSave=document.querySelector('#soul-editors button')")
    pending = []
    page.route("**/app/soul", lambda route: pending.append(route))
    with page.expect_request("**/app/soul"):
        page.evaluate("void loadSoul()")
    page.evaluate("MCP._loginEpoch++;clearMemoryState();queueOwner='bob';queueScope='u-bob';oldSave.click()")
    assert len(pending) == 1  # retained Save did not send with Bob's credentials
    pending[0].fulfill(status=200, content_type="application/json", body=json.dumps({
        "documents": [{"path": "soul.md", "text": "Alice private reply", "revision": "abc"}]}))
    page.wait_for_load_state("networkidle")
    assert page.locator("#soul-editors").inner_text() == ""
    assert not (root / "u-bob" / "soul.md").exists()
