from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


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


















