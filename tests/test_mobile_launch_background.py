"""A cold start and offline recovery keep the dark app's ground colour."""

import json
import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile"


def test_shell_splash_and_offline_page_match_app_background() -> None:
    app = (ROOT / "tinyassets/onboarding/app.html").read_text(encoding="utf-8")
    match = re.search(r"--bg:\s*(#[0-9a-fA-F]{6})", app)
    assert match
    background = match[1].lower()
    config = json.loads((MOBILE / "capacitor.config.json").read_text())
    assert config["android"]["backgroundColor"].lower() == background
    assert config["plugins"]["SplashScreen"]["backgroundColor"].lower() == background
    offline = (MOBILE / "www/index.html").read_text(encoding="utf-8")
    assert re.search(r"html, body\s*\{[^}]*background:\s*" + background, offline)
    rgb = tuple(int(background[i:i + 2], 16) for i in (1, 3, 5))
    splashes = sorted((MOBILE / "resources/android").glob("drawable*/splash.png"))
    assert len(splashes) == 11
    for path in [MOBILE / "resources/splash.png", *splashes]:
        with Image.open(path) as image:
            for x, y in ((0, 0), (image.width - 1, 0),
                         (0, image.height - 1), (image.width - 1, image.height - 1)):
                assert image.convert("RGB").getpixel((x, y)) == rgb, path
