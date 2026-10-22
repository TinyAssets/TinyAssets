import json
import re

from tinyassets.onboarding import render_app_html


def test_app_page_declares_platform_versions_and_update_ui():
    html, _ = render_app_html(build="test")
    match = re.search(r"const CFG = window.__TA_ONBOARDING__ \|\| (\{.*?\});", html)
    assert match
    versions = json.loads(match.group(1))["app_versions"]
    assert set(versions) == {"desktop", "android", "ios"}
    assert versions["desktop"]["minimum"] == "0.1.1"
    assert "id=\"app-update-required\"" in html
    assert "id=\"app-update-banner\"" in html
    assert "__TA_APP_UPDATES__" not in html
