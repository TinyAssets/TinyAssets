"""Release-shape guards for the generated Capacitor iOS application."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
from pathlib import Path

import yaml
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
MOBILE = REPO / "mobile"
BUILD_WORKFLOW = REPO / ".github" / "workflows" / "ios-build.yml"
RELEASE_WORKFLOW = REPO / ".github" / "workflows" / "ios-release.yml"
SCREENSHOT_MANIFEST = (
    REPO / "docs" / "ops" / "app-store-assets" / "screenshot-manifest.json"
)
SUBMISSION_PACKET = REPO / "docs" / "ops" / "app-store-submission-packet.md"


def _load_asset_installer():
    path = MOBILE / "scripts" / "add_ios_assets.py"
    spec = importlib.util.spec_from_file_location("add_ios_assets", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_configuration_installer():
    path = MOBILE / "scripts" / "add_ios_scheme.py"
    spec = importlib.util.spec_from_file_location("add_ios_scheme", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_catalog(directory: Path, filenames: set[str]) -> None:
    directory.mkdir(parents=True)
    (directory / "Contents.json").write_text(
        json.dumps({"images": [{"filename": name} for name in sorted(filenames)]}),
        encoding="utf-8",
    )


def test_ios_asset_installer_replaces_every_catalog_image(tmp_path: Path) -> None:
    installer = _load_asset_installer()
    catalog = tmp_path / "Assets.xcassets"
    icon_catalog = catalog / "AppIcon.appiconset"
    splash_catalog = catalog / "Splash.imageset"
    _write_catalog(icon_catalog, installer.ICON_FILES)
    _write_catalog(splash_catalog, installer.SPLASH_FILES)

    icon = MOBILE / "resources" / "icon.png"
    splash = MOBILE / "resources" / "splash.png"
    assert installer.png_color_type(icon) == 2
    assert installer.png_color_type(splash) == 2
    for name in installer.ICON_FILES:
        shutil.copyfile(icon, icon_catalog / name)
    for name in installer.SPLASH_FILES:
        shutil.copyfile(splash, splash_catalog / name)

    assert installer.install_assets(catalog, icon, splash) == 0
    for name in installer.ICON_FILES:
        assert (icon_catalog / name).read_bytes() == icon.read_bytes()
    for name in installer.SPLASH_FILES:
        assert (splash_catalog / name).read_bytes() == splash.read_bytes()
        with Image.open(splash_catalog / name) as image:
            assert image.getpixel((0, 0)) == (15, 16, 32)


def test_ios_asset_installer_fails_on_capacitor_catalog_drift(tmp_path: Path) -> None:
    installer = _load_asset_installer()
    catalog = tmp_path / "Assets.xcassets"
    _write_catalog(catalog / "AppIcon.appiconset", {"new-template-icon.png"})
    _write_catalog(catalog / "Splash.imageset", installer.SPLASH_FILES)
    for name in installer.SPLASH_FILES:
        shutil.copyfile(
            MOBILE / "resources" / "splash.png",
            catalog / "Splash.imageset" / name,
        )
    (catalog / "AppIcon.appiconset" / "new-template-icon.png").write_bytes(
        (MOBILE / "resources" / "icon.png").read_bytes()
    )

    assert installer.install_assets(
        catalog,
        MOBILE / "resources" / "icon.png",
        MOBILE / "resources" / "splash.png",
    ) == 1


def test_ios_asset_installer_rejects_an_alpha_channel_in_store_art(tmp_path: Path) -> None:
    installer = _load_asset_installer()
    catalog = tmp_path / "Assets.xcassets"
    icon_catalog = catalog / "AppIcon.appiconset"
    splash_catalog = catalog / "Splash.imageset"
    _write_catalog(icon_catalog, installer.ICON_FILES)
    _write_catalog(splash_catalog, installer.SPLASH_FILES)

    real_icon = MOBILE / "resources" / "icon.png"
    splash = MOBILE / "resources" / "splash.png"
    alpha_icon = tmp_path / "alpha-icon.png"
    alpha_bytes = bytearray(real_icon.read_bytes())
    alpha_bytes[25] = 6  # PNG IHDR truecolor + alpha; CRC is irrelevant to this header check.
    alpha_icon.write_bytes(alpha_bytes)
    for name in installer.ICON_FILES:
        shutil.copyfile(real_icon, icon_catalog / name)
    for name in installer.SPLASH_FILES:
        shutil.copyfile(splash, splash_catalog / name)

    assert installer.install_assets(catalog, alpha_icon, splash) == 1


def test_ios_build_and_release_install_real_artwork() -> None:
    build_workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
    assert "python3 scripts/add_ios_assets.py" in build_workflow
    # Once: in the main-push paths. The PR trigger was cut on 2026-09-27 (no
    # platform builds on PRs, docs/design-notes/2026-09-27-lean-ci-pipeline.md).
    assert build_workflow.count('".github/workflows/ios-release.yml"') == 1
    assert "python3 scripts/add_ios_assets.py" in RELEASE_WORKFLOW.read_text(encoding="utf-8")


def test_ios_configuration_installer_adds_release_keys_idempotently(
    tmp_path: Path,
) -> None:
    installer = _load_configuration_installer()
    info_plist = tmp_path / "Info.plist"
    info_plist.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<plist>\n<dict>\n</dict>\n</plist>\n',
        encoding="utf-8",
    )

    assert installer.install_configuration(info_plist) == 0
    first = info_plist.read_text(encoding="utf-8")
    assert first.count("<string>tinyassets</string>") == 1
    assert first.count("<key>NSMicrophoneUsageDescription</key>") == 1
    assert first.count(f"<string>{installer.MICROPHONE_PURPOSE}</string>") == 1
    assert first.count("<key>ITSAppUsesNonExemptEncryption</key>") == 1
    assert "<key>ITSAppUsesNonExemptEncryption</key>\n\t<false/>" in first

    assert installer.install_configuration(info_plist) == 0
    assert info_plist.read_text(encoding="utf-8") == first


def test_ios_configuration_installer_rejects_conflicting_microphone_copy(
    tmp_path: Path,
) -> None:
    installer = _load_configuration_installer()
    info_plist = tmp_path / "Info.plist"
    info_plist.write_text(
        "<plist>\n<dict>\n"
        "<key>NSMicrophoneUsageDescription</key>\n"
        "<string>Unexpected copy</string>\n"
        "</dict>\n</plist>\n",
        encoding="utf-8",
    )

    assert installer.install_configuration(info_plist) == 1
    assert "Unexpected copy" in info_plist.read_text(encoding="utf-8")


def test_ios_configuration_installer_rejects_non_exempt_encryption_declaration(
    tmp_path: Path,
) -> None:
    installer = _load_configuration_installer()
    info_plist = tmp_path / "Info.plist"
    info_plist.write_text(
        "<plist>\n<dict>\n"
        "<key>ITSAppUsesNonExemptEncryption</key>\n"
        "<true/>\n"
        "</dict>\n</plist>\n",
        encoding="utf-8",
    )

    assert installer.install_configuration(info_plist) == 1
    assert "<true/>" in info_plist.read_text(encoding="utf-8")


def test_ios_workflows_verify_microphone_purpose_key() -> None:
    expected = 'grep -q "<key>NSMicrophoneUsageDescription</key>"'
    assert expected in BUILD_WORKFLOW.read_text(encoding="utf-8")
    assert expected in RELEASE_WORKFLOW.read_text(encoding="utf-8")


def test_ios_workflows_verify_export_compliance_key() -> None:
    expected = 'grep -q "<key>ITSAppUsesNonExemptEncryption</key>"'
    assert expected in BUILD_WORKFLOW.read_text(encoding="utf-8")
    assert expected in RELEASE_WORKFLOW.read_text(encoding="utf-8")


def test_ios_workflows_select_the_app_store_required_sdk() -> None:
    for workflow in (BUILD_WORKFLOW, RELEASE_WORKFLOW):
        text = workflow.read_text(encoding="utf-8")
        assert "/Applications/Xcode_26.3.app/Contents/Developer" in text
        assert '[[ "$sdk_version" == 26.* ]]' in text


def test_ios_build_workflow_captures_store_screenshots_only_on_manual_request() -> None:
    workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
    assert "capture_store_screenshots:" in workflow
    assert "inputs.source_ref || github.sha" in workflow
    assert ".capture-tools/mobile/scripts/capture_ios_store_screenshots.sh" in workflow
    assert "ref: ${{ github.ref }}" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow


def test_ios_store_capture_script_enforces_apple_dimensions_and_no_alpha() -> None:
    script = (MOBILE / "scripts" / "capture_ios_store_screenshots.sh").read_text(
        encoding="utf-8"
    )
    assert "1242x2688,1284x2778" in script
    assert "2048x2732,2064x2752" in script
    assert '[[ "$alpha" != "no" ]]' in script
    assert "xcrun simctl io" in script


def test_app_store_screenshot_manifest_is_an_honest_ios_capture_contract() -> None:
    manifest = json.loads(SCREENSHOT_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["platform"] == "iOS"
    assert 1 <= manifest["minimum_count"] <= manifest["maximum_count"] <= 10
    assert manifest["alpha_allowed"] is False
    assert manifest["preferred_display"] == "6.5-inch iPhone"
    assert set(manifest["accepted_pixel_sizes"]) == {
        "1242x2688",
        "1284x2778",
    }
    assert set(manifest["required_displays"]["13-inch iPad"]) == {
        "2048x2732",
        "2064x2752",
    }
    captures = manifest["required_captures"]
    assert {capture["display"] for capture in captures} == {
        "6.5-inch iPhone",
        "13-inch iPad",
    }
    assert all(capture["alpha"] is False for capture in captures)
    assert all(
        capture["status"] == "uploaded_and_persisted_in_app_store_connect"
        for capture in captures
    )
    assert all(
        capture["source_sha"] == "76d795a1a3794fc3f3112121a063ca21b3175ce0"
        for capture in captures
    )
    assert len(manifest["shots"]) == 5
    assert all(shot["status"] == "blocked_until_ios_capture" for shot in manifest["shots"])
    rules = " ".join(manifest["capture_rules"])
    assert "actual iOS app" in rules
    assert "Do not resize or frame Android screenshots" in rules


def test_app_store_metadata_packet_meets_apple_field_constraints() -> None:
    packet = SUBMISSION_PACKET.read_text(encoding="utf-8")

    def field(label: str) -> str:
        match = re.search(rf"\*\*{re.escape(label)}[^\n]*\*\*\s*`([^`]*)`", packet)
        assert match, f"missing copy-ready {label} field"
        return match.group(1)

    assert len(field("Name").encode("utf-8")) <= 30
    assert len(field("Subtitle").encode("utf-8")) <= 30
    assert len(field("Promotional text").encode("utf-8")) <= 170
    keywords = field("Keywords").split(",")
    assert sum(len(keyword.encode("utf-8")) for keyword in keywords) + len(keywords) - 1 <= 100
    assert all(len(keyword) > 2 for keyword in keywords)
    assert not {"openai", "claude"} & {keyword.casefold() for keyword in keywords}

    assert field("Support URL") == "https://tinyassets.io/legal#contact"
    assert field("Privacy Policy URL") == "https://tinyassets.io/legal/#privacy"
    assert field("User Privacy Choices URL") == "https://tinyassets.io/account"
    assert "| User access | Full Access for the existing Account Holder" in packet
    assert "no additional user is selected" in packet
    assert "| App Store Connect record | Apple ID `6808434444`" in packet
    assert (
        "iOS 1.0, build 3, is **Rejected / Unresolved Issues** for a Guideline 2.1 "
        "information request as of 2026-09-10" in packet
    )
    assert "Apple ID `6808434444`" in packet
    assert (
        "| TestFlight | Internal group `Internal`; manual distribution for Xcode builds; "
        "Build 3 attached; Account Holder added and **Invited** on 2026-09-10 |" in packet
    )
    assert "release mode was changed to automatic\n    after approval on 2026-09-10" in packet
    assert "all\nfour entries use App Functionality only" in packet
    assert "App Functionality; Account Management" not in packet
    assert "it is not published and its legal-policy URLs" not in packet
    assert "148 non-EU storefronts enabled, including the United States" in packet
    assert "`5c6e4844-2ca2-438c-8aec-a189efb0ebb2`" in packet
    assert "2048-bit\n   Apple Distribution certificate/private-key pair" in packet
    assert "`TinyAssets App Store 2026` App Store\n   profile binds `io.tinyassets.app`" in packet
    assert "App Store Connect API access has not been" not in packet

    description_match = re.search(
        r"\*\*Description:\*\*\s*(.*?)\s*The factual App Review Notes are saved",
        packet,
        flags=re.DOTALL,
    )
    assert description_match
    description = "\n".join(
        line.removeprefix("> ")
        for line in description_match.group(1).splitlines()
        if line != ">"
    ).strip()
    assert 0 < len(description) <= 4_000


def test_ios_release_is_manual_and_upload_is_opt_in() -> None:
    workflow = yaml.load(RELEASE_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["concurrency"] == {
        "group": "ios-release",
        "cancel-in-progress": "false",
    }
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["upload_to_testflight"]["default"] == "false"
    assert workflow["jobs"]["build-signed-ipa"]["environment"] == "app-store"
    upload_job = workflow["jobs"]["upload-testflight"]
    assert "inputs.upload_to_testflight" in upload_job["if"]
    assert upload_job["environment"] == "app-store"


def test_ios_release_fails_closed_and_never_submits_for_review() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    for required in (
        "profile bundle id is",
        "development provisioning profile supplied",
        "Apple Distribution signing identity",
        "codesign --verify --deep --strict",
        "xcrun altool --validate-app",
        "xcrun altool --upload-app",
        "shasum -a 256 -c SHA256SUMS",
    ):
        assert required in workflow
    assert "--submit" not in workflow
    assert "submitForReview" not in workflow


def test_ios_bundle_id_is_one_consistent_permanent_identifier() -> None:
    config = json.loads((MOBILE / "capacitor.config.json").read_text(encoding="utf-8"))
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    scheme_installer = (MOBILE / "scripts" / "add_ios_scheme.py").read_text(
        encoding="utf-8"
    )
    assert config["appId"] == "io.tinyassets.app"
    assert "BUNDLE_ID: io.tinyassets.app" in workflow
    assert "<string>io.tinyassets.app</string>" in scheme_installer


def test_mobile_build_chain_does_not_restore_the_vulnerable_asset_generator() -> None:
    package = json.loads((MOBILE / "package.json").read_text(encoding="utf-8"))
    lockfile = (MOBILE / "package-lock.json").read_text(encoding="utf-8")
    assert "@capacitor/assets" not in package.get("devDependencies", {})
    assert "capacitor-assets" not in package.get("scripts", {}).values()
    assert 'node_modules/@capacitor/assets' not in lockfile
    assert '"sharp"' not in lockfile
