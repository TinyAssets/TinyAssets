"""The debug/sideload Android build can never share the Play app's identity.

A debug APK is signed with a development key. While it carried the Play
package (``io.tinyassets.app``), a phone that had sideloaded it from the website
refused every Play update with "Can't install" (2026-10-01, twice). These tests
pin the split at every layer: the generated Gradle project, the built APK's
badging, the release manifest, the in-app sign-in return, and the website's
install path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile"
PLAY_ID = "io.tinyassets.app"
DEBUG_ID = "io.tinyassets.app.debug"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


configure = _load("configure_android_release", MOBILE / "scripts/configure_android_release.py")
verify = _load("verify_android_release", MOBILE / "scripts/verify_android_release.py")

# Capacitor 8's generated app/build.gradle, reduced to the parts the scripts read.
GENERATED_GRADLE = """android {
    namespace = "io.tinyassets.app"
    compileSdk = rootProject.ext.compileSdkVersion
    defaultConfig {
        applicationId "io.tinyassets.app"
        minSdkVersion rootProject.ext.minSdkVersion
        versionCode 1
        versionName "1.0"
    }
    buildTypes {
        release {
            minifyEnabled false
            proguardFiles getDefaultProguardFile('proguard-android.txt'), 'proguard-rules.pro'
        }
    }
}
"""


def _generated_mobile(tmp_path: Path) -> Path:
    mobile = tmp_path / "mobile"
    (mobile / "android/app").mkdir(parents=True)
    shutil.copy(MOBILE / "capacitor.config.json", mobile / "capacitor.config.json")
    shutil.copy(MOBILE / "android-release.json", mobile / "android-release.json")
    (mobile / "android/variables.gradle").write_text(
        "minSdkVersion = 24\ncompileSdkVersion = 36\ntargetSdkVersion = 36\n", encoding="utf-8"
    )
    (mobile / "android/app/build.gradle").write_text(GENERATED_GRADLE, encoding="utf-8")
    return mobile


# --- generated Gradle project ---------------------------------------------------


def test_configure_gives_only_the_debug_build_a_separate_identity(tmp_path: Path) -> None:
    mobile = _generated_mobile(tmp_path)
    release = configure.configure(mobile)
    gradle_path = mobile / "android/app/build.gradle"
    gradle = gradle_path.read_text(encoding="utf-8")

    assert release.app_id == PLAY_ID
    assert configure.debug_application_id(release) == DEBUG_ID
    assert 'applicationId "io.tinyassets.app"' in gradle
    assert gradle.count("applicationIdSuffix") == 1
    debug_block = gradle[gradle.index("        debug {") :]
    debug_block = debug_block[: debug_block.index("        }\n") + 10]
    assert 'applicationIdSuffix ".debug"' in debug_block
    release_block = gradle[gradle.index("        release {") :]
    assert "applicationIdSuffix" not in release_block

    strings = (mobile / configure.DEBUG_STRINGS_PATH).read_text(encoding="utf-8")
    assert '<string name="app_name">TinyAssets (debug)</string>' in strings
    assert '<string name="title_activity_main">TinyAssets (debug)</string>' in strings

    configure.configure(mobile)
    assert gradle_path.read_text(encoding="utf-8") == gradle, "configure must be idempotent"
    verify.verify_gradle(mobile, release)


@pytest.mark.parametrize(
    "tamper",
    [
        # A suffix on the release buildType would ship a Play bundle that is
        # not io.tinyassets.app.
        lambda g: g.replace(
            "        release {\n", '        release {\n            applicationIdSuffix ".x"\n'
        ),
        lambda g: g.replace(
            'applicationId "io.tinyassets.app"\n',
            'applicationId "io.tinyassets.app"\n        applicationIdSuffix ".x"\n',
        ),
        # A debug buildType the template grew on its own is a shape change.
        lambda g: g.replace("    buildTypes {\n", "    buildTypes {\n        debug {\n        }\n"),
        lambda g: g.replace("    buildTypes {\n", "    buildTypez {\n"),
    ],
)
def test_configure_refuses_any_other_suffix_or_shape(tmp_path: Path, tamper) -> None:
    mobile = _generated_mobile(tmp_path)
    gradle = mobile / "android/app/build.gradle"
    gradle.write_text(tamper(gradle.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(ValueError, match="applicationIdSuffix|buildTypes"):
        configure.configure(mobile)


def test_verify_rejects_a_suffix_moved_off_the_debug_build(tmp_path: Path) -> None:
    mobile = _generated_mobile(tmp_path)
    release = configure.configure(mobile)
    gradle_path = mobile / "android/app/build.gradle"
    configured = gradle_path.read_text(encoding="utf-8")

    moved = configured.replace(configure.DEBUG_BUILD_TYPE, "").replace(
        "        release {\n", '        release {\n            applicationIdSuffix ".debug"\n'
    )
    gradle_path.write_text(moved, encoding="utf-8")
    with pytest.raises(ValueError, match="applicationIdSuffix"):
        verify.verify_gradle(mobile, release)

    gradle_path.write_text(configured.replace(configure.DEBUG_BUILD_TYPE, ""), encoding="utf-8")
    with pytest.raises(ValueError, match="applicationIdSuffix"):
        verify.verify_gradle(mobile, release)

    gradle_path.write_text(configured, encoding="utf-8")
    (mobile / configure.DEBUG_STRINGS_PATH).unlink()
    with pytest.raises(ValueError, match="debug build label"):
        verify.verify_gradle(mobile, release)


# --- built artefacts ------------------------------------------------------------


def _badging(package: str, label: str, code: int = 5) -> str:
    return (
        f"package: name='{package}' versionCode='{code}' versionName='1.0.4' "
        "platformBuildVersionName='16' compileSdkVersion='36'\n"
        "sdkVersion:'24'\ntargetSdkVersion:'36'\n"
        f"application-label:'{label}'\n"
        f"application: label='{label}' icon='res/mipmap-anydpi-v26/ic_launcher.xml'\n"
    )


def test_debug_apk_badging_must_not_carry_the_play_package() -> None:
    release = configure.load_release(MOBILE)
    assert (
        verify.verify_apk_badging(
            _badging(DEBUG_ID, "TinyAssets (debug)", release.version_code),
            release,
            variant="debug",
        )
        == DEBUG_ID
    )
    with pytest.raises(ValueError, match="carries the Play package"):
        verify.verify_apk_badging(
            _badging(PLAY_ID, "TinyAssets", release.version_code), release, variant="debug"
        )
    with pytest.raises(ValueError, match="application-label"):
        verify.verify_apk_badging(
            _badging(DEBUG_ID, "TinyAssets", release.version_code), release, variant="debug"
        )
    with pytest.raises(ValueError, match="package"):
        verify.verify_apk_badging(
            _badging(PLAY_ID + ".dbg", "TinyAssets (debug)", release.version_code),
            release,
            variant="debug",
        )
    with pytest.raises(ValueError, match="no package name"):
        verify.verify_apk_badging("ERROR: dump failed\n", release, variant="debug")


def test_release_apk_badging_must_be_exactly_the_play_package() -> None:
    release = configure.load_release(MOBILE)
    code = release.version_code
    assert verify.verify_apk_badging(_badging(PLAY_ID, "TinyAssets", code), release,
                                     variant="release") == PLAY_ID
    with pytest.raises(ValueError, match="package"):
        verify.verify_apk_badging(
            _badging(DEBUG_ID, "TinyAssets", code), release, variant="release"
        )


def test_badging_cli_reads_a_saved_dump(tmp_path: Path, capsys) -> None:
    release = configure.load_release(MOBILE)
    dump = tmp_path / "badging.txt"
    dump.write_text(_badging(PLAY_ID, "TinyAssets", release.version_code), encoding="utf-8")
    argv = ["--mobile-root", str(MOBILE), "--apk-badging", str(dump), "--variant", "debug"]
    assert verify.main(argv) == 1
    assert "carries the Play package" in capsys.readouterr().err
    assert verify.main(argv[:-2]) == 1, "--apk-badging without --variant must refuse"


def test_merged_release_manifest_refuses_the_debug_package(tmp_path: Path) -> None:
    release = configure.load_release(MOBILE)
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        f'package="{DEBUG_ID}" android:versionCode="{release.version_code}" '
        f'android:versionName="{release.version_name}"><application/></manifest>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="package="):
        verify.verify_manifest(manifest, release, merged=True)


def test_debug_workflow_runs_the_badging_gate_on_the_apk_it_publishes() -> None:
    workflow = (ROOT / ".github/workflows/android-build.yml").read_text(encoding="utf-8")
    gate = workflow.index("--apk-badging")
    assert "dump badging android/app/build/outputs/apk/debug/app-debug.apk" in workflow
    assert "--variant debug" in workflow[gate:]
    assert workflow.index("assembleDebug") < gate < workflow.index("Upload APK artifact")
    assert workflow.index("configure_android_release.py") < workflow.index("assembleDebug")
    # The release also carries Play's foreground-service evidence video.
    assert "delete-asset" not in workflow
    assert "gh release delete" not in workflow


def test_every_build_entry_point_runs_the_identity_split() -> None:
    for workflow in ("android-build.yml", "android-release.yml"):
        text = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
        assert "scripts/configure_android_release.py" in text, workflow
    container = (MOBILE / "container/build.sh").read_text(encoding="utf-8")
    assert container.index("configure_android_release.py") < container.index("bundleRelease")
    scripts = json.loads((MOBILE / "package.json").read_text(encoding="utf-8"))["scripts"]
    for name in ("build:debug", "build:release"):
        command = scripts[name]
        assert command.index("configure_android_release.py") < command.index("gradlew"), name


# --- the in-app sign-in return --------------------------------------------------


def test_native_callback_intent_targets_the_running_install() -> None:
    plugin = (MOBILE / "native/android/LocalCallbackPlugin.java").read_text(encoding="utf-8")
    assert "callbackPage(page, getContext().getPackageName())" in plugin
    assert "package=io.tinyassets.app" not in plugin
    verify.verify_sources(MOBILE, configure.load_release(MOBILE))


def test_native_source_gate_rejects_a_hard_coded_callback_package(tmp_path: Path) -> None:
    mobile = tmp_path / "mobile"
    shutil.copytree(MOBILE / "native", mobile / "native")
    for name in ("capacitor.config.json", "android-release.json"):
        shutil.copy(MOBILE / name, mobile / name)
    plugin = mobile / "native/android/LocalCallbackPlugin.java"
    text = plugin.read_text(encoding="utf-8")
    plugin.write_text(
        text.replace('package=" + appPackage + ";end"', 'package=io.tinyassets.app;end"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="running install"):
        verify.verify_sources(mobile, configure.load_release(mobile))


def _app_html() -> str:
    return (ROOT / "tinyassets/onboarding/app.html").read_text(encoding="utf-8")


def _js_function(html: str, name: str) -> str:
    match = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", html)
    assert match, f"app.html has no function {name}"
    start = match.start()
    depth = 0
    for index in range(html.index("{", match.end()), len(html)):
        if html[index] == "{":
            depth += 1
        elif html[index] == "}":
            depth -= 1
            if depth == 0:
                return html[start : index + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _run_node(tmp_path: Path, program: str) -> dict:
    node = shutil.which("node")
    if not node:  # pragma: no cover - environment dependent
        if os.environ.get("TINYASSETS_SKIP_JS_PROBE_TESTS"):
            pytest.skip("node absent; skip explicitly requested via env")
        pytest.fail("node executable not found - the sign-in return is JavaScript")
    script = tmp_path / "app_return.js"
    script.write_text(program, encoding="utf-8")
    proc = subprocess.run(
        [node, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, f"harness crashed:\n{proc.stderr}"
    return json.loads(proc.stdout)


_SHIM = """
const out = {};
const notice = {textContent:"", appendChild(){}};
const document = {getElementById(){ return notice; },
  createElement(){ return {}; }};
const window = {location:{href:"", origin:"https://tinyassets.io", pathname:"/app",
  search:""}};
const history = {replaceState(){}};
const store = {}; const pkceStore = {setItem(k,v){ store[k]=v; },
  getItem(k){ return store[k]; }, removeItem(k){delete store[k];}};
const sessionStorage = {getItem(){ return null; }, removeItem(){}};
const localStorage=sessionStorage;let logoutPending=false;
const PKCE_KEY = "pkce";
const CFG = {configured:true, client_id:"c", scopes:"s", resource:"r",
  authorization_endpoint:"https://auth.example/authorize"};
function redirectUri(){ return "https://tinyassets.io/app"; }
function randToken(){ return "RANDOM"; }
async function challengeFor(){ return "challenge"; }
async function openExternal(url){ out.opened = url; }
async function finishExchange(){ out.exchanged = true; return true; }
"""


@pytest.mark.parametrize(
    "app_id,prefix",
    [(PLAY_ID, "android"), (DEBUG_ID, "android-debug")],
)
def test_app_sign_in_returns_to_the_install_it_started_in(
    tmp_path: Path, app_id: str, prefix: str
) -> None:
    get_info = f"async getInfo(){{ return {{id:{json.dumps(app_id)}}}; }}"
    result = _run_node(tmp_path, _native_sign_in(get_info))
    assert result["client"] == prefix
    assert result["state"] == "r" * 43 and result["opened"] == "https://auth.example/authorize"
    assert result["verifier"] == "RANDOM" and result["challenge"] == "challenge"


@pytest.mark.parametrize(
    "get_info",
    [
        "async getInfo(){ throw new Error('not implemented'); }",
        "async getInfo(){ return {}; }",
        "",  # an App plugin without getInfo
    ],
)
def test_app_sign_in_refuses_visibly_when_the_install_is_unknown(
    tmp_path: Path, get_info: str
) -> None:
    # Guessing the Play app would strand a debug install's sign-in there.
    result = _run_node(tmp_path, _native_sign_in(get_info))
    assert "opened" not in result and result["state"] is None
    assert "didn't say which install" in result["notice"]


def _native_sign_in(get_info: str) -> str:
    html = _app_html()
    return "\n".join(
        (
            _SHIM,
            "const NATIVE = true;",
            "window.Capacitor={getPlatform:()=> 'android'};",
            "async function fetch(url,options){const body=JSON.parse(options.body);"
            "out.client=body.client;out.challenge=body.code_challenge;"
            "return {ok:true,json:async()=>({ref:'r'.repeat(43),url:'https://auth.example/authorize'})};}",
            "function resumeNativeSignIn(){}",
            f"function nativePlugin(){{ return {{ {get_info} }}; }}",
            _js_function(html, "beginSignIn"),
            "(async()=>{ await beginSignIn();",
            "  out.state = store.pkce ? JSON.parse(store.pkce).native_ref : null;",
            "  out.verifier = store.pkce ? JSON.parse(store.pkce).verifier : null;",
            "  out.notice = notice.textContent;",
            "  console.log(JSON.stringify(out)); })();",
        )
    )


# --- the website's install path -------------------------------------------------


def test_website_sends_people_to_play_not_the_debug_apk() -> None:
    site = (ROOT / "WebSite/site-react/lib/site.ts").read_text(encoding="utf-8")
    assert 'android: "https://play.google.com/apps/testing/io.tinyassets.app"' in site
    assert "\n  apk:" not in site, "the old install link must not come back"

    start = (ROOT / "WebSite/site-react/app/start/page.tsx").read_text(encoding="utf-8")
    android = start[start.index('id="android"') : start.index('id="desktop"')]
    primary = re.search(r'<a className="btn[^"]*" href=\{SITE\.(\w+)\}', android)
    assert primary and primary.group(1) == "android"
    assert "invited" in android
    assert "SITE.androidDebugApk" in android and "TinyAssets (debug)" in android

    llms = (ROOT / "WebSite/site-react/public/llms.txt").read_text(encoding="utf-8")
    assert "https://play.google.com/apps/testing/io.tinyassets.app" in llms
    assert "app-debug.apk" not in llms
