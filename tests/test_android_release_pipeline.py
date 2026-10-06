from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


configure = _load("configure_android_release", MOBILE / "scripts/configure_android_release.py")
scheme = _load("add_app_scheme", MOBILE / "scripts/add_app_scheme.py")
verify = _load("verify_android_release", MOBILE / "scripts/verify_android_release.py")


def _generated_mobile(tmp_path: Path) -> Path:
    mobile = tmp_path / "mobile"
    (mobile / "android/app").mkdir(parents=True)
    (mobile / "capacitor.config.json").write_text(
        json.dumps({"appId": "io.tinyassets.app"}), encoding="utf-8"
    )
    (mobile / "android-release.json").write_text(
        json.dumps(
            {
                "appId": "io.tinyassets.app",
                "versionCode": 27,
                "versionName": "1.4.2",
                "minSdk": 24,
                "targetSdk": 36,
                "compileSdk": 36,
            }
        ),
        encoding="utf-8",
    )
    (mobile / "android/variables.gradle").write_text(
        "minSdkVersion = 24\ncompileSdkVersion = 36\ntargetSdkVersion = 36\n",
        encoding="utf-8",
    )
    (mobile / "android/app/build.gradle").write_text(
        """android {\n    namespace "io.tinyassets.app"\n    defaultConfig {\n"""
        """        applicationId "io.tinyassets.app"\n        versionCode 1\n"""
        """        versionName "1.0"\n    }\n    buildTypes {\n        release {\n"""
        """            minifyEnabled false\n        }\n    }\n}\n""",
        encoding="utf-8",
    )
    return mobile


def test_android_release_config_matches_every_source_package_identity() -> None:
    release = configure.load_release(MOBILE)
    capacitor = json.loads((MOBILE / "capacitor.config.json").read_text(encoding="utf-8"))
    assert release.app_id == capacitor["appId"] == "io.tinyassets.app"
    for path in (MOBILE / "native/android").glob("*.java"):
        assert f"package {release.app_id};" in path.read_text(encoding="utf-8")
    injector = (MOBILE / "scripts/add_app_scheme.py").read_text(encoding="utf-8")
    assert f"package {release.app_id};" in injector
    assert "registerPlugin(LocalCallbackPlugin.class)" in injector
    assert "new VoiceWebChromeClient(bridge, this)" in injector
    assert "android.permission.POST_NOTIFICATIONS" in injector

    callback = (MOBILE / "native/android/LocalCallbackPlugin.java").read_text(encoding="utf-8")
    assert '@Permission(alias = "notifications"' in callback
    assert (
        'requestPermissionForAlias("notifications", call, "notificationPermissionCallback")'
        in callback
    )
    assert (
        'call.reject("Notification permission is required while browser sign-in is active")'
        in callback
    )
    assert "LocalCallbackService.EXTRA_STARTUP_RECEIVER" in callback
    assert 'call.reject("Sign-in notification did not start")' in callback
    assert 'call.reject("Could not start the sign-in notification")' in callback

    service = (MOBILE / "native/android/LocalCallbackService.java").read_text(encoding="utf-8")
    assert "startup.send(STARTUP_OK" in service
    assert "startup.send(STARTUP_FAILED" in service
    assert 'Log.e("TinyAssetsSignin"' in service
    assert "Notification.FOREGROUND_SERVICE_IMMEDIATE" in service

    voice = (MOBILE / "native/android/VoiceWebChromeClient.java").read_text(encoding="utf-8")
    assert 'TRUSTED_SCHEME = "https"' in voice
    assert 'TRUSTED_HOST = "tinyassets.io"' in voice
    assert "resources.length == 1" in voice
    assert "activity.hasWindowFocus()" in voice
    assert "ActivityCompat.requestPermissions" in voice
    assert "if (pendingRequest != null)" in voice
    assert "onPermissionRequestCanceled" in voice
    assert "request == pendingRequest" in voice
    assert "isTrustedOrigin(current)" in voice
    assert "track.stop()" in voice


def test_configure_android_release_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    mobile = _generated_mobile(tmp_path)
    configured = configure.configure(mobile, expected_version="1.4.2")
    first = (mobile / "android/app/build.gradle").read_text(encoding="utf-8")
    configure.configure(mobile, expected_version="1.4.2")
    assert (mobile / "android/app/build.gradle").read_text(encoding="utf-8") == first
    assert configured.version_code == 27
    assert "versionCode 27" in first
    assert 'versionName "1.4.2"' in first


def test_configure_android_release_rejects_tag_or_package_drift(tmp_path: Path) -> None:
    mobile = _generated_mobile(tmp_path)
    with pytest.raises(ValueError, match="tag version"):
        configure.configure(mobile, expected_version="1.4.3")
    gradle = mobile / "android/app/build.gradle"
    gradle.write_text(
        gradle.read_text(encoding="utf-8").replace(
            'applicationId "io.tinyassets.app"', 'applicationId "example.wrong.app"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="package identity drift"):
        configure.configure(mobile)


def test_android_release_rejects_a_gradle_injectable_version_name(tmp_path: Path) -> None:
    mobile = _generated_mobile(tmp_path)
    release_path = mobile / "android-release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["versionName"] = '1.4.2";apply(plugin:"wrong")//'
    release_path.write_text(json.dumps(release), encoding="utf-8")
    with pytest.raises(ValueError, match="versionName"):
        configure.load_release(mobile)

    release_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        configure.load_release(mobile)


def test_manifest_hardening_disables_backup_and_cleartext(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '  <application android:allowBackup="true" android:label="TinyAssets">\n'
        "  </application>\n</manifest>\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(scheme, "MANIFEST", manifest)
    assert scheme.harden_application() == 0
    first = manifest.read_text(encoding="utf-8")
    assert 'android:allowBackup="false"' in first
    assert 'android:usesCleartextTraffic="false"' in first
    assert scheme.harden_application() == 0
    assert manifest.read_text(encoding="utf-8") == first


def _release() -> configure.AndroidRelease:
    return configure.AndroidRelease(
        app_id="io.tinyassets.app",
        version_code=27,
        version_name="1.4.2",
        min_sdk=24,
        target_sdk=36,
        compile_sdk=36,
    )


def _source_manifest() -> str:
    return (
        """<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n"""
        """  <uses-permission android:name="android.permission.INTERNET"/>\n"""
        """  <uses-permission android:name="android.permission.FOREGROUND_SERVICE"/>\n"""
        """  <uses-permission android:name="android.permission.FOREGROUND_SERVICE_DATA_SYNC"/>\n"""
        """  <uses-permission android:name="android.permission.POST_NOTIFICATIONS"/>\n"""
        """  <uses-permission android:name="android.permission.RECORD_AUDIO"/>\n"""
        """  <application android:allowBackup="false" android:usesCleartextTraffic="false">\n"""
        """    <activity android:name=".MainActivity" android:exported="true">\n"""
        """      <intent-filter><action android:name="android.intent.action.VIEW"/>"""
        """<category android:name="android.intent.category.DEFAULT"/>"""
        """<category android:name="android.intent.category.BROWSABLE"/>"""
        """<data android:scheme="tinyassets" android:host="auth"/></intent-filter>\n"""
        """      <intent-filter><action android:name="android.intent.action.SEND"/>"""
        """<category android:name="android.intent.category.DEFAULT"/>"""
        """<data android:mimeType="text/plain"/></intent-filter>\n"""
        """    </activity>\n"""
        """    <service android:name=".LocalCallbackService" android:exported="false" """
        """android:foregroundServiceType="dataSync"/>\n"""
        """    <service android:name=".TinyAssetsMessagingService" android:exported="false">"""
        """<intent-filter><action android:name="com.google.firebase.MESSAGING_EVENT"/>"""
        """</intent-filter></service>\n"""
        """  </application>\n</manifest>\n"""
    )


def test_release_manifest_gate_accepts_only_the_intended_surface(tmp_path: Path) -> None:
    release = _release()
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text(_source_manifest(), encoding="utf-8")
    verify.verify_manifest(manifest, release, merged=False)
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'android:allowBackup="false"', 'android:allowBackup="true"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="allowBackup"):
        verify.verify_manifest(manifest, release, merged=False)


@pytest.mark.parametrize(
    "addition,error",
    [
        (
            '<uses-permission android:name="android.permission.CAMERA"/>',
            "permission drift",
        ),
        (
            '<receiver android:name=".LeakyReceiver" android:exported="true"/>',
            "exported component",
        ),
    ],
)
def test_manifest_gate_rejects_permission_or_exported_surface_drift(
    tmp_path: Path, addition: str, error: str
) -> None:
    manifest = tmp_path / "AndroidManifest.xml"
    text = _source_manifest()
    anchor = "<application" if addition.startswith("<uses-permission") else "</application>"
    text = text.replace(anchor, addition + "\n  " + anchor, 1)
    manifest.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        verify.verify_manifest(manifest, _release(), merged=False)


def test_merged_release_manifest_gate_catches_debuggable_and_version_drift(
    tmp_path: Path,
) -> None:
    release = _release()
    text = (
        _source_manifest()
        .replace(
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">',
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
            'package="io.tinyassets.app" android:versionCode="27" '
            'android:versionName="1.4.2">\n'
            '  <uses-sdk android:minSdkVersion="24" android:targetSdkVersion="36"/>\n'
            "  <uses-permission "
            'android:name="io.tinyassets.app.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"/>',
        )
        .replace(
            "</application>",
            '<receiver android:name="androidx.profileinstaller.ProfileInstallReceiver" '
            'android:exported="true" android:permission="android.permission.DUMP"/>\n'
            "  </application>",
        )
    )
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text(text, encoding="utf-8")
    verify.verify_manifest(manifest, release, merged=True)

    manifest.write_text(
        text.replace(
            'android:usesCleartextTraffic="false"',
            'android:usesCleartextTraffic="false" android:debuggable="true"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="debuggable"):
        verify.verify_manifest(manifest, release, merged=True)

    manifest.write_text(
        text.replace('android:versionCode="27"', 'android:versionCode="28"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="versionCode"):
        verify.verify_manifest(manifest, release, merged=True)


def _generated_java(tmp_path: Path) -> Path:
    """A generated tree whose Java is exactly what the injector would write."""
    mobile = tmp_path / "mobile"
    package = mobile / "android/app/src/main/java/io/tinyassets/app"
    package.mkdir(parents=True)
    (package / "MainActivity.java").write_text(scheme.MAIN_ACTIVITY_SRC, encoding="utf-8")
    (mobile / "native/android").mkdir(parents=True)
    for name in verify.NATIVE_SOURCES:
        committed = (MOBILE / "native/android" / name).read_bytes()
        (mobile / "native/android" / name).write_bytes(committed)
        (package / name).write_bytes(committed)
    return mobile


def test_the_back_gesture_gets_a_policy_instead_of_being_swallowed() -> None:
    """@capacitor/app's own callback is always enabled and, at the first history
    entry, does nothing -- so without this the opening screen eats the gesture
    and the app cannot be left by going back at all.

    This reads the Java literal, which is legitimate because it ships verbatim,
    but it is still structure: it cannot show the policy RUNS. A device check is
    what proves that (docs/ops/google-play-launch.md, the ladder).
    """
    main = scheme.MAIN_ACTIVITY_SRC
    # History first, so the page's own navigation keeps working.
    assert "webView.canGoBack()" in main and "webView.goBack();" in main
    # Then one confirmation, because an edge-swipe is easy to hit while typing.
    assert "EXIT_CONFIRM_WINDOW_MS" in main
    assert "Press back again to leave TinyAssets" in main
    # Leaving behaves like Home: the signed-in WebView survives, so returning
    # resumes the conversation instead of reloading it over the network.
    assert "moveTaskToBack(true);" in main
    # Registered before super.onCreate(), the plugin's callback would be added
    # after ours, and the dispatcher calls the most recently added enabled one --
    # so the plugin would take every back press and this policy would be dead.
    assert main.index("installBackPolicy();") > main.index("super.onCreate(savedInstanceState);")


def test_the_release_gate_refuses_a_shell_whose_back_gesture_is_dead(tmp_path: Path) -> None:
    release = _release()
    mobile = _generated_java(tmp_path)
    package = mobile / "android/app/src/main/java/io/tinyassets/app"
    verify.verify_generated_java(mobile, release)

    (package / "MainActivity.java").write_text(
        scheme.MAIN_ACTIVITY_SRC.replace("        installBackPolicy();\n", ""), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="back-gesture policy"):
        verify.verify_generated_java(mobile, release)

    # MOVED, not duplicated: leaving the late call in place would leave a winning
    # callback behind and the fixture would not be the broken shape it claims.
    moved = scheme.MAIN_ACTIVITY_SRC.replace("        installBackPolicy();\n", "").replace(
        "        super.onCreate(savedInstanceState);\n",
        "        installBackPolicy();\n        super.onCreate(savedInstanceState);\n",
    )
    assert moved.count("installBackPolicy();") == 1
    (package / "MainActivity.java").write_text(moved, encoding="utf-8")
    with pytest.raises(ValueError, match="after super.onCreate"):
        verify.verify_generated_java(mobile, release)

    # A decision that cannot fire still carries every token a presence check
    # looks for, so the gate pins the decision itself.
    (package / "MainActivity.java").write_text(
        scheme.MAIN_ACTIVITY_SRC.replace(
            "if (exitConfirmAt != 0L && now - exitConfirmAt <= EXIT_CONFIRM_WINDOW_MS) {",
            "if (false && exitConfirmAt != 0L && now - exitConfirmAt <= EXIT_CONFIRM_WINDOW_MS) {",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="back-gesture policy"):
        verify.verify_generated_java(mobile, release)


def test_release_workflow_has_fail_closed_release_gates() -> None:
    workflow = (ROOT / ".github/workflows/android-release.yml").read_text(encoding="utf-8")
    for required in (
        "configure_android_release.py",
        "verify_android_release.py",
        "lintRelease bundleRelease",
        "verify_android_release.py --merged",
        "jarsigner -verify",
        "keytool -printcert -jarfile",
        "sha256sum",
        "if-no-files-found: error",
    ):
        assert required in workflow
    assert "pull_request:" in workflow
    assert workflow.count("if: github.event_name != 'pull_request'") == 3
    assert "git merge-base --is-ancestor" in workflow
    assert 'if [ -n "$RELEASE_TAG" ] && [ -z "$expected" ]' in workflow

    debug = (ROOT / ".github/workflows/android-build.yml").read_text(encoding="utf-8")
    assert "configure_android_release.py" in debug
    assert "verify_android_release.py" in debug


def test_offline_page_is_reachable_in_the_packaged_shell(tmp_path: Path) -> None:
    mobile = tmp_path / "mobile"
    assets = mobile / "android/app/src/main/assets"
    (assets / "public").mkdir(parents=True)
    (mobile / "www").mkdir()
    config = (MOBILE / "capacitor.config.json").read_bytes()
    page = (MOBILE / "www/index.html").read_bytes()
    (mobile / "capacitor.config.json").write_bytes(config)
    (mobile / "www/index.html").write_bytes(page)
    (assets / "capacitor.config.json").write_bytes(config)
    (assets / "public/index.html").write_bytes(page)
    verify.verify_offline_page(mobile)
    verify.verify_offline_page(mobile, generated=True)

    broken = json.loads(config)
    del broken["server"]["errorPath"]
    (assets / "capacitor.config.json").write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError, match="errorPath"):
        verify.verify_offline_page(mobile, generated=True)
    (assets / "capacitor.config.json").write_bytes(config)
    (assets / "public/index.html").write_text("stale page", encoding="utf-8")
    with pytest.raises(ValueError, match="differs from committed"):
        verify.verify_offline_page(mobile, generated=True)
    (assets / "public/index.html").unlink()
    with pytest.raises(ValueError, match="offline page is missing"):
        verify.verify_offline_page(mobile, generated=True)


@pytest.mark.real_browser
@pytest.mark.parametrize("online", [False, True])
def test_offline_try_again_returns_to_live_app_in_same_phone_view(online: bool) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 360, "height": 640})
            page = context.new_page()
            page.goto((MOBILE / "www/index.html").as_uri())
            context.set_offline(not online)
            # A failed load can happen even when navigator.onLine says true.
            retry = page.get_by_role("link", name="Try again", exact=True)
            assert retry.is_visible()
            box = retry.bounding_box()
            assert box and box["height"] >= 48
            assert 0 <= box["x"] < box["x"] + box["width"] <= 360
            assert 0 <= box["y"] < box["y"] + box["height"] <= 640
            assert page.evaluate("document.documentElement.scrollWidth") == 360
            context.set_offline(False)
            # Intercept the destination, proving navigation without a production write.
            page.route("https://tinyassets.io/app", lambda route: route.fulfill(body="Recovered"))
            history_length = page.evaluate("history.length")
            retry.click()
            page.wait_for_url("https://tinyassets.io/app")
            assert page.locator("body").inner_text() == "Recovered"
            assert len(context.pages) == 1
            assert page.evaluate("history.length") == history_length
        finally:
            browser.close()


def test_release_runbook_version_matches_android_release_json() -> None:
    release = configure.load_release(MOBILE)
    runbook = (ROOT / "docs/ops/google-play-launch.md").read_text(encoding="utf-8")
    assert f"version code `{release.version_code}`" in runbook
    assert re.search(rf"version\s+name `{re.escape(release.version_name)}`", runbook)


def test_find_merged_manifest_requires_exactly_one_candidate(tmp_path: Path) -> None:
    mobile = tmp_path / "mobile"
    with pytest.raises(ValueError, match="found 0"):
        verify.find_merged_manifest(mobile)
    first = mobile / "android/app/build/intermediates/merged_manifest/release/one"
    first.mkdir(parents=True)
    (first / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")
    assert verify.find_merged_manifest(mobile) == first / "AndroidManifest.xml"
    second = mobile / "android/app/build/intermediates/merged_manifest/release/two"
    second.mkdir()
    (second / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")
    with pytest.raises(ValueError, match="found 2"):
        verify.find_merged_manifest(mobile)


def test_committed_android_and_play_artwork_has_release_dimensions() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(MOBILE / "scripts/verify_android_release.py"),
            "--source-only",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_artwork_gate_can_validate_an_independent_brand_lane() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(MOBILE / "scripts/verify_android_release.py"),
            "--mobile-root",
            str(MOBILE),
            "--artwork-only",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
