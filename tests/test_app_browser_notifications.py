"""Execute the shipped browser code at its browser/HTTP boundaries."""
from __future__ import annotations

import json
import shutil
import subprocess

from tests.test_onboarding_app import _js_function
from tinyassets.onboarding import render_app_html


def run_js(source):
    node = shutil.which("node")
    assert node, "Node is required for browser behavior tests"
    result = subprocess.run([node, "-e", source], capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def functions(*names):
    html, _ = render_app_html()
    return "\n".join(_js_function(html, name) for name in names)




















