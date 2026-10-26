import json
import re

import pytest

from tinyassets.onboarding import render_app_html

pytestmark = pytest.mark.verifies(
    "live-mcp-connector-surface:The Web App Is Served At The Apex `/app`"
)


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


def test_update_policy_behaviour_in_node():
    """The banner/wall rules run as shipped; a missing Node fails, never skips."""
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    assert node, "Node is required for browser behavior tests"
    suite = Path(__file__).with_name("app_updates") / "app_updates.test.cjs"
    result = subprocess.run([node, "--test", str(suite)], capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
