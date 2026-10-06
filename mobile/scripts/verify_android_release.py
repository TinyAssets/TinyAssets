#!/usr/bin/env python3
"""Fail closed on Android package, version, SDK, manifest, and artwork drift."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from configure_android_release import (
    DEBUG_APP_NAME,
    DEBUG_APPLICATION_ID_SUFFIX,
    DEBUG_BUILD_TYPE,
    DEBUG_STRINGS,
    DEBUG_STRINGS_PATH,
    AndroidRelease,
    debug_application_id,
    load_release,
)

DEFAULT_MOBILE = Path(__file__).resolve().parents[1]
ANDROID_NS = "http://schemas.android.com/apk/res/android"
A = f"{{{ANDROID_NS}}}"
TOOLS = "{http://schemas.android.com/tools}"
# Contributed by firebase-messaging at manifest-merge time; they are never in the
# source manifest, so they are allowed but not required.
MERGED_ONLY_PERMISSIONS = (
    "android.permission.ACCESS_NETWORK_STATE",
    "android.permission.WAKE_LOCK",
    "com.google.android.c2dm.permission.RECEIVE",
)
NATIVE_SOURCES = (
    "LocalCallbackPlugin.java",
    "LocalCallbackService.java",
    "VoiceWebChromeClient.java",
    "TinyAssetsMessagingService.java",
    "NotificationReplyPlugin.java",
)


def _value(text: str, pattern: str, label: str) -> str:
    found = re.findall(pattern, text, flags=re.MULTILINE)
    if len(found) != 1:
        raise ValueError(f"expected exactly one {label}; found {len(found)}")
    return found[0]


def _png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        head = handle.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError(f"not a PNG: {path}")
    return struct.unpack(">II", head[16:24])


def verify_gradle(mobile: Path, release: AndroidRelease) -> None:
    gradle = (mobile / "android/app/build.gradle").read_text(encoding="utf-8")
    variables = (mobile / "android/variables.gradle").read_text(encoding="utf-8")
    checks = {
        "namespace": (
            _value(gradle, r'^\s*namespace\s*(?:=\s*)?["\']([^"\']+)', "namespace"),
            release.app_id,
        ),
        "applicationId": (
            _value(gradle, r'^\s*applicationId\s*(?:=\s*)?["\']([^"\']+)', "applicationId"),
            release.app_id,
        ),
        "versionCode": (
            _value(gradle, r"^\s*versionCode\s*(?:=\s*)?(\d+)", "versionCode"),
            str(release.version_code),
        ),
        "versionName": (
            _value(gradle, r'^\s*versionName\s*(?:=\s*)?["\']([^"\']+)', "versionName"),
            release.version_name,
        ),
        "minSdkVersion": (
            _value(variables, r"^\s*minSdkVersion\s*=\s*(\d+)", "minSdkVersion"),
            str(release.min_sdk),
        ),
        "targetSdkVersion": (
            _value(variables, r"^\s*targetSdkVersion\s*=\s*(\d+)", "targetSdkVersion"),
            str(release.target_sdk),
        ),
        "compileSdkVersion": (
            _value(variables, r"^\s*compileSdkVersion\s*=\s*(\d+)", "compileSdkVersion"),
            str(release.compile_sdk),
        ),
    }
    drift = [
        f"{name}={have!r}, expected {want!r}"
        for name, (have, want) in checks.items()
        if have != want
    ]
    suffix = _value(
        gradle, r'^\s*applicationIdSuffix\s*(?:=\s*)?["\']([^"\']+)', "applicationIdSuffix"
    )
    if suffix != DEBUG_APPLICATION_ID_SUFFIX or DEBUG_BUILD_TYPE not in gradle:
        drift.append(
            f"applicationIdSuffix={suffix!r} must appear only in the debug buildType as "
            f"{DEBUG_APPLICATION_ID_SUFFIX!r}"
        )
    if drift:
        raise ValueError("Android Gradle release drift: " + "; ".join(drift))
    strings = mobile / DEBUG_STRINGS_PATH
    if not strings.is_file() or strings.read_text(encoding="utf-8") != DEBUG_STRINGS:
        raise ValueError(f"debug build label missing or changed: {strings}")


def verify_manifest(path: Path, release: AndroidRelease, *, merged: bool) -> None:
    root = ET.parse(path).getroot()
    if merged:
        values = {
            "package": root.get("package"),
            "versionCode": root.get(A + "versionCode"),
            "versionName": root.get(A + "versionName"),
        }
        wanted = {
            "package": release.app_id,
            "versionCode": str(release.version_code),
            "versionName": release.version_name,
        }
        drift = [
            f"{key}={values[key]!r}, expected {wanted[key]!r}"
            for key in wanted
            if values[key] != wanted[key]
        ]
        uses_sdk = root.find("uses-sdk")
        if uses_sdk is None:
            drift.append("merged manifest has no uses-sdk")
        else:
            for attr, expected in (
                ("minSdkVersion", release.min_sdk),
                ("targetSdkVersion", release.target_sdk),
            ):
                if uses_sdk.get(A + attr) != str(expected):
                    drift.append(f"{attr}={uses_sdk.get(A + attr)!r}, expected {expected}")
        if drift:
            raise ValueError("merged manifest release drift: " + "; ".join(drift))

    application = root.find("application")
    if application is None:
        raise ValueError(f"{path} has no application element")
    if application.get(A + "allowBackup") != "false":
        raise ValueError("android:allowBackup must be false for the remote authenticated shell")
    if application.get(A + "usesCleartextTraffic") != "false":
        raise ValueError("android:usesCleartextTraffic must be explicitly false")
    if merged and application.get(A + "debuggable") == "true":
        raise ValueError("release manifest is debuggable")

    permissions = {
        item.get(A + "name")
        for tag in ("uses-permission", "uses-permission-sdk-23")
        for item in root.findall(tag)
    }
    allowed = {
        "android.permission.INTERNET",
        "android.permission.FOREGROUND_SERVICE",
        "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.RECORD_AUDIO",
        f"{release.app_id}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION",
        *MERGED_ONLY_PERMISSIONS,
    }
    unexpected = sorted(str(item) for item in permissions - allowed)
    required = allowed - {
        f"{release.app_id}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION",
        *MERGED_ONLY_PERMISSIONS,
    }
    missing = sorted(required - permissions)
    if unexpected or missing:
        raise ValueError(f"permission drift: missing={missing}, unexpected={unexpected}")

    components = []
    for tag in ("activity", "activity-alias", "service", "receiver", "provider"):
        components.extend(application.findall(tag))
    main = next(
        (item for item in components if (item.get(A + "name") or "").endswith("MainActivity")), None
    )
    if main is None or main.get(A + "exported") != "true":
        raise ValueError("MainActivity must be present and exported for the launcher/deep link")
    filters = main.findall("intent-filter")
    has_auth_callback = any(
        {action.get(A + "name") for action in intent_filter.findall("action")}
        >= {"android.intent.action.VIEW"}
        and {category.get(A + "name") for category in intent_filter.findall("category")}
        >= {"android.intent.category.DEFAULT", "android.intent.category.BROWSABLE"}
        and any(
            data.get(A + "scheme") == "tinyassets" and data.get(A + "host") == "auth"
            for data in intent_filter.findall("data")
        )
        for intent_filter in filters
    )
    has_share = any(
        {action.get(A + "name") for action in intent_filter.findall("action")}
        >= {"android.intent.action.SEND"}
        and {category.get(A + "name") for category in intent_filter.findall("category")}
        >= {"android.intent.category.DEFAULT"}
        and any(data.get(A + "mimeType") == "text/plain" for data in intent_filter.findall("data"))
        for intent_filter in filters
    )
    if not has_auth_callback or not has_share:
        raise ValueError("MainActivity is missing the tinyassets://auth or share-to-app callback")

    service = next(
        (
            item
            for item in components
            if (item.get(A + "name") or "").endswith("LocalCallbackService")
        ),
        None,
    )
    if (
        service is None
        or service.get(A + "exported") != "false"
        or service.get(A + "foregroundServiceType") != "dataSync"
    ):
        raise ValueError(
            "LocalCallbackService must be non-exported and foregroundServiceType=dataSync"
        )

    notify_service = next(
        (
            item
            for item in components
            if (item.get(A + "name") or "").endswith("TinyAssetsMessagingService")
        ),
        None,
    )
    if notify_service is None or notify_service.get(A + "exported") != "false":
        raise ValueError("TinyAssetsMessagingService must be present and non-exported")
    # Both services answer MESSAGING_EVENT. If the plugin's own survives the
    # merge, FCM may hand the data-only message to it and nothing is ever shown.
    # (The source manifest carries the tools:node="remove" marker that does the
    # removing; only a service that is NOT so marked would survive the merge.)
    if any(
        (item.get(A + "name") or "").endswith("pushnotifications.MessagingService")
        and item.get(TOOLS + "node") != "remove"
        for item in components
    ):
        raise ValueError("the push plugin's MessagingService must be removed from the manifest")

    for component in components:
        if (
            component.get(A + "exported") == "true"
            and component is not main
            and not component.get(A + "permission")
        ):
            raise ValueError(
                f"exported component lacks a protecting permission: {component.get(A + 'name')}"
            )


def verify_offline_page(mobile: Path, *, generated: bool = False) -> None:
    """Check both the source fallback and the assets Capacitor actually packages."""
    root = mobile / "android/app/src/main/assets" if generated else mobile
    capacitor = json.loads((root / "capacitor.config.json").read_text(encoding="utf-8"))
    server = capacitor.get("server", {})
    if server.get("url") != "https://tinyassets.io/app":
        raise ValueError("offline retry requires the live server.url https://tinyassets.io/app")
    if server.get("errorPath") != "index.html":
        raise ValueError("Capacitor server.errorPath must reach the bundled index.html")
    web = root / "public" if generated else mobile / capacitor["webDir"]
    page = web / "index.html"
    if not page.is_file():
        raise ValueError("bundled offline page is missing")
    if generated and _sha256(page) != _sha256(mobile / "www/index.html"):
        raise ValueError("generated offline page differs from committed source")


def verify_sources(mobile: Path, release: AndroidRelease) -> None:
    capacitor = json.loads((mobile / "capacitor.config.json").read_text(encoding="utf-8"))
    if capacitor.get("appId") != release.app_id:
        raise ValueError("capacitor appId differs from android-release.json")
    server = capacitor.get("server", {})
    if server.get("cleartext") is not False or server.get("androidScheme") != "https":
        raise ValueError("Capacitor Android transport must be HTTPS with cleartext=false")
    if not str(server.get("url", "")).startswith("https://"):
        raise ValueError("Capacitor server.url must be HTTPS")

    for name in NATIVE_SOURCES:
        text = (mobile / "native/android" / name).read_text(encoding="utf-8")
        if not re.search(rf"^package\s+{re.escape(release.app_id)};", text, re.MULTILINE):
            raise ValueError(f"{name} package differs from {release.app_id}")
    plugin = (mobile / "native/android/LocalCallbackPlugin.java").read_text(encoding="utf-8")
    # The callback intent targets THIS install's package, so a debug build signs
    # back into itself and a release build into io.tinyassets.app.
    if (
        "callbackPage(page, getContext().getPackageName())" not in plugin
        or 'package=" + appPackage + ";end"' not in plugin
        or f"package={release.app_id}" in plugin
    ):
        raise ValueError("LocalCallbackPlugin intent must target the running install's package")
    notification_safeguards = (
        "Manifest.permission.POST_NOTIFICATIONS",
        "Build.VERSION_CODES.TIRAMISU",
        '@Permission(alias = "notifications"',
        'getPermissionState("notifications") != PermissionState.GRANTED',
        'requestPermissionForAlias("notifications", call, "notificationPermissionCallback")',
        "@PermissionCallback",
        'call.reject("Notification permission is required while browser sign-in is active")',
        "LocalCallbackService.EXTRA_STARTUP_RECEIVER",
        'call.reject("Sign-in notification did not start")',
        'call.reject("Could not start the sign-in notification")',
    )
    missing = [item for item in notification_safeguards if item not in plugin]
    if missing:
        raise ValueError(f"LocalCallbackPlugin is missing notification safeguards: {missing}")
    service = (mobile / "native/android/LocalCallbackService.java").read_text(encoding="utf-8")
    startup_safeguards = (
        "ResultReceiver startup",
        "startup.send(STARTUP_OK",
        "startup.send(STARTUP_FAILED",
        'Log.e("TinyAssetsSignin"',
        "Notification.FOREGROUND_SERVICE_IMMEDIATE",
    )
    missing = [item for item in startup_safeguards if item not in service]
    if missing:
        raise ValueError(f"LocalCallbackService is missing startup safeguards: {missing}")
    notify = (mobile / "native/android/TinyAssetsMessagingService.java").read_text(encoding="utf-8")
    notify_safeguards = (
        "extends FirebaseMessagingService",
        "SecureRandom",
        "Context.MODE_PRIVATE",
        "EXTRA_NONCE",
        "PendingIntent.FLAG_IMMUTABLE",
        'if ("clear".equals(data.get("kind")))',
        "manager.cancel(requestId, NOTIFICATION_ID)",
    )
    missing = [item for item in notify_safeguards if item not in notify]
    if missing:
        raise ValueError(f"TinyAssetsMessagingService is missing safeguards: {missing}")
    voice = (mobile / "native/android/VoiceWebChromeClient.java").read_text(encoding="utf-8")
    safeguards = (
        'TRUSTED_SCHEME = "https"',
        'TRUSTED_HOST = "tinyassets.io"',
        "PermissionRequest.RESOURCE_AUDIO_CAPTURE",
        "resources.length == 1",
        "activity.hasWindowFocus()",
        "ActivityCompat.requestPermissions",
        "Manifest.permission.RECORD_AUDIO",
        "if (pendingRequest != null)",
        "onPermissionRequestCanceled",
        "request == pendingRequest",
        "isTrustedOrigin(current)",
        "track.stop()",
    )
    missing = [item for item in safeguards if item not in voice]
    if missing:
        raise ValueError(f"VoiceWebChromeClient is missing release safeguards: {missing}")


def verify_generated_java(mobile: Path, release: AndroidRelease) -> None:
    package_dir = mobile / "android/app/src/main/java" / Path(*release.app_id.split("."))
    main = (package_dir / "MainActivity.java").read_text(encoding="utf-8")
    if not re.search(rf"^package\s+{re.escape(release.app_id)};", main, re.MULTILINE):
        raise ValueError("generated MainActivity package differs from release identity")
    if "registerPlugin(LocalCallbackPlugin.class)" not in main:
        raise ValueError("generated MainActivity did not register LocalCallbackPlugin")
    if "registerPlugin(NotificationReplyPlugin.class)" not in main:
        raise ValueError("generated MainActivity did not register NotificationReplyPlugin")
    if "new VoiceWebChromeClient(bridge, this)" not in main:
        raise ValueError("generated MainActivity did not install VoiceWebChromeClient")
    if "voiceChromeClient.stopCapture(bridge.getWebView())" not in main:
        raise ValueError("generated MainActivity does not stop microphone capture on pause")
    # Without our own callback the app plugin's always-enabled one swallows the
    # back gesture at the first history entry, so the opening screen cannot be
    # left. This is a TEXT gate on Java that ships verbatim, and text cannot show
    # that the policy runs -- a disabled branch would still carry every token
    # below. So the decision itself is pinned exactly: changing what the app does
    # on back has to come here and say so. The behaviour is proved on a device
    # (docs/ops/google-play-launch.md, the ladder's device check), not here.
    back_policy = (
        "installBackPolicy();",
        "new OnBackPressedCallback(true)",
        "if (webView != null && webView.canGoBack()) {",
        "if (exitConfirmAt != 0L && now - exitConfirmAt <= EXIT_CONFIRM_WINDOW_MS) {",
        "moveTaskToBack(true);",
    )
    missing = [item for item in back_policy if item not in main]
    if missing:
        raise ValueError(f"generated MainActivity is missing the back-gesture policy: {missing}")
    # Registered BEFORE super.onCreate(), the plugin's callback is added after
    # ours and the dispatcher -- which calls the most recently added enabled
    # callback -- hands every back press to the plugin instead. The policy would
    # be dead code, and nothing else would say so.
    if main.index("installBackPolicy();") < main.index("super.onCreate(savedInstanceState);"):
        raise ValueError(
            "installBackPolicy() must run after super.onCreate(): registered before it, the "
            "app plugin's callback is added later and wins every back press"
        )
    for name in NATIVE_SOURCES:
        source = mobile / "native/android" / name
        generated = package_dir / name
        if not generated.is_file() or _sha256(generated) != _sha256(source):
            raise ValueError(f"generated native source differs from committed source: {generated}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_app_artwork(mobile: Path) -> None:
    launcher = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    foreground = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
    fixed = {
        mobile / "resources/icon.png": (1024, 1024),
        mobile / "resources/splash.png": (2732, 2732),
    }
    for path, wanted in fixed.items():
        if _png_size(path) != wanted:
            raise ValueError(f"wrong committed artwork size: {path}, expected {wanted}")
    for density, side in launcher.items():
        for name in ("ic_launcher.png", "ic_launcher_round.png"):
            path = mobile / "resources/android" / f"mipmap-{density}" / name
            if _png_size(path) != (side, side):
                raise ValueError(f"wrong committed launcher size: {path}")
    for density, side in foreground.items():
        path = mobile / "resources/android" / f"mipmap-{density}" / "ic_launcher_foreground.png"
        if _png_size(path) != (side, side):
            raise ValueError(f"wrong committed adaptive foreground size: {path}")


def verify_play_artwork(mobile: Path) -> None:
    play = mobile.parent / "docs/ops/play-assets"
    for path, wanted in (
        (play / "icon-512.png", (512, 512)),
        (play / "feature-graphic-1024x500.png", (1024, 500)),
    ):
        if _png_size(path) != wanted:
            raise ValueError(f"wrong Play artwork size: {path}, expected {wanted}")
    screenshots = sorted((play / "screenshots").glob("*.png"))
    if len(screenshots) < 2:
        raise ValueError("Play listing requires at least two phone screenshots")
    for path in screenshots:
        width, height = _png_size(path)
        short, long = sorted((width, height))
        if short < 320 or long > 3840 or long > 2 * short:
            raise ValueError(
                f"Play screenshot outside 320..3840 / 2:1 bounds: {path} ({width}x{height})"
            )


def verify_committed_artwork(mobile: Path) -> None:
    verify_app_artwork(mobile)
    verify_play_artwork(mobile)


def verify_generated_artwork(mobile: Path) -> None:
    generated = mobile / "android/app/src/main/res"
    committed = mobile / "resources/android"
    for source in committed.rglob("*.png"):
        target = generated / source.relative_to(committed)
        if not target.is_file() or _sha256(target) != _sha256(source):
            raise ValueError(f"generated artwork differs from committed source: {target}")
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        if not (generated / "mipmap-anydpi-v26" / name).is_file():
            raise ValueError(f"missing adaptive icon XML: {name}")


def verify_apk_badging(text: str, release: AndroidRelease, *, variant: str) -> str:
    """Check ``aapt2 dump badging`` output for a built APK; returns its package.

    A debug APK must NEVER carry the Play package: it is signed with a
    development key, so on a phone that installed it every Play update fails.
    """
    package = re.search(r"^package: name='([^']+)'", text, flags=re.MULTILINE)
    code = re.search(r"^package: .*\bversionCode='([^']*)'", text, flags=re.MULTILINE)
    label = re.search(r"^application-label:'([^']*)'", text, flags=re.MULTILINE)
    if package is None or code is None or label is None:
        raise ValueError("badging output has no package name, versionCode or application-label")
    name = package.group(1)
    if variant == "debug":
        wanted, wanted_label = debug_application_id(release), DEBUG_APP_NAME
        if name == release.app_id:
            raise ValueError(
                f"debug APK carries the Play package {release.app_id!r}; a sideloaded copy "
                "would block every Play update"
            )
    elif variant == "release":
        wanted, wanted_label = release.app_id, None
    else:
        raise ValueError(f"unknown variant {variant!r}")
    drift = []
    if name != wanted:
        drift.append(f"package={name!r}, expected {wanted!r}")
    if code.group(1) != str(release.version_code):
        drift.append(f"versionCode={code.group(1)!r}, expected {release.version_code}")
    if wanted_label is not None and label.group(1) != wanted_label:
        drift.append(f"application-label={label.group(1)!r}, expected {wanted_label!r}")
    if drift:
        raise ValueError(f"{variant} APK identity drift: " + "; ".join(drift))
    return name


def find_merged_manifest(mobile: Path) -> Path:
    root = mobile / "android/app/build/intermediates/merged_manifest/release"
    candidates = sorted(root.glob("**/AndroidManifest.xml"))
    if len(candidates) != 1:
        raise ValueError(
            f"expected one release merged manifest under {root}; found {len(candidates)}"
        )
    return candidates[0]


def verify(mobile: Path, *, merged: bool = False, source_only: bool = False) -> AndroidRelease:
    release = load_release(mobile)
    verify_sources(mobile, release)
    verify_offline_page(mobile)
    if source_only:
        verify_committed_artwork(mobile)
    else:
        verify_app_artwork(mobile)
    if source_only:
        print(
            f"verified Android release sources {release.app_id} "
            f"{release.version_name} ({release.version_code}) and committed artwork"
        )
        return release
    verify_gradle(mobile, release)
    verify_offline_page(mobile, generated=True)
    verify_generated_java(mobile, release)
    verify_manifest(mobile / "android/app/src/main/AndroidManifest.xml", release, merged=False)
    verify_generated_artwork(mobile)
    if merged:
        verify_manifest(find_merged_manifest(mobile), release, merged=True)
    print(
        f"verified Android release {release.app_id} "
        f"{release.version_name} ({release.version_code}), "
        f"SDK {release.min_sdk}/{release.target_sdk}/{release.compile_sdk}, manifest and artwork"
    )
    return release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mobile-root", type=Path, default=DEFAULT_MOBILE)
    parser.add_argument(
        "--merged", action="store_true", help="also verify Gradle's release merged manifest"
    )
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="verify checked-in identity and artwork without a generated Android project",
    )
    parser.add_argument(
        "--artwork-only",
        action="store_true",
        help="verify committed mobile and Play artwork without release/package inputs",
    )
    parser.add_argument(
        "--apk-badging",
        type=Path,
        help="check a saved `aapt2 dump badging` of a built APK (with --variant)",
    )
    parser.add_argument("--variant", choices=("debug", "release"))
    args = parser.parse_args(argv)
    try:
        if args.apk_badging is not None:
            if args.variant is None or args.source_only or args.merged or args.artwork_only:
                raise ValueError("--apk-badging needs --variant and no other mode")
            release = load_release(args.mobile_root.resolve())
            text = args.apk_badging.read_text(encoding="utf-8")
            name = verify_apk_badging(text, release, variant=args.variant)
            print(f"verified {args.variant} APK installs as {name}")
            return 0
        if args.artwork_only:
            if args.source_only or args.merged:
                raise ValueError("--artwork-only cannot be combined with other modes")
            mobile = args.mobile_root.resolve()
            verify_committed_artwork(mobile)
            print(f"verified committed Android and Play artwork under {mobile}")
            return 0
        if args.source_only and args.merged:
            raise ValueError("--source-only and --merged are mutually exclusive")
        verify(
            args.mobile_root.resolve(),
            merged=args.merged,
            source_only=args.source_only,
        )
    except (OSError, ValueError, ET.ParseError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
