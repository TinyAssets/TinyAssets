#!/usr/bin/env python3
"""Apply the checked-in Android release identity and version to a generated project.

Capacitor generates ``versionCode 1`` / ``versionName \"1.0\"`` every time the
gitignored Android project is recreated.  Keeping the release values in a small,
reviewable file makes local and CI bundles reproduce the same Play identity.

It also gives the debug buildType its own identity (``<appId>.debug``, labelled
"TinyAssets (debug)"), so a sideloaded development build can never block a Play
update. Every Android build -- the debug APK workflow, the release AAB workflow
and the container -- runs this script, so the split cannot be skipped.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MOBILE = Path(__file__).resolve().parents[1]
MAX_PLAY_VERSION_CODE = 2_100_000_000

# Debug/sideload builds are a SEPARATE install from the Play app. They are signed
# with a development key, and Android refuses an update whose signature differs:
# a debug APK sharing the Play applicationId made every Play update fail with
# "Can't install" for anyone who had sideloaded it (2026-10-01). A suffixed id
# lets both live side by side, and the visible name says which one is which.
DEBUG_APPLICATION_ID_SUFFIX = ".debug"
DEBUG_APP_NAME = "TinyAssets (debug)"
DEBUG_BUILD_TYPE = (
    "        debug {\n"
    "            // Sideload builds never share the Play identity (configure_android_release.py).\n"
    f'            applicationIdSuffix "{DEBUG_APPLICATION_ID_SUFFIX}"\n'
    "        }\n"
)
DEBUG_STRINGS = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<!-- Written by mobile/scripts/configure_android_release.py: the debug build's"
    " launcher label. -->\n"
    "<resources>\n"
    f'    <string name="app_name">{DEBUG_APP_NAME}</string>\n'
    f'    <string name="title_activity_main">{DEBUG_APP_NAME}</string>\n'
    "</resources>\n"
)
DEBUG_STRINGS_PATH = Path("android/app/src/debug/res/values/strings.xml")


@dataclass(frozen=True)
class AndroidRelease:
    app_id: str
    version_code: int
    version_name: str
    min_sdk: int
    target_sdk: int
    compile_sdk: int


def load_release(mobile: Path) -> AndroidRelease:
    path = mobile / "android-release.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc

    expected = {"appId", "versionCode", "versionName", "minSdk", "targetSdk", "compileSdk"}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain one JSON object")
    if set(raw) != expected:
        raise ValueError(f"{path} keys must be exactly {sorted(expected)}; got {sorted(raw)}")
    app_id = raw["appId"]
    version_name = raw["versionName"]
    numbers = {name: raw[name] for name in ("versionCode", "minSdk", "targetSdk", "compileSdk")}
    if not isinstance(app_id, str) or not re.fullmatch(
        r"[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*)+", app_id
    ):
        raise ValueError(f"invalid Android appId: {app_id!r}")
    if (
        not isinstance(version_name, str)
        or len(version_name) > 50
        or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]*", version_name)
    ):
        raise ValueError(
            "versionName must be 1-50 characters using only ASCII letters, digits, . _ + -"
        )
    if any(type(value) is not int for value in numbers.values()):
        raise ValueError("versionCode and SDK levels must be integers")
    if not 1 <= numbers["versionCode"] <= MAX_PLAY_VERSION_CODE:
        raise ValueError(f"versionCode must be in 1..{MAX_PLAY_VERSION_CODE}")
    if not 1 <= numbers["minSdk"] <= numbers["targetSdk"] <= numbers["compileSdk"]:
        raise ValueError("SDK levels must satisfy 1 <= minSdk <= targetSdk <= compileSdk")
    return AndroidRelease(
        app_id=app_id,
        version_code=numbers["versionCode"],
        version_name=version_name,
        min_sdk=numbers["minSdk"],
        target_sdk=numbers["targetSdk"],
        compile_sdk=numbers["compileSdk"],
    )


def debug_application_id(release: AndroidRelease) -> str:
    return release.app_id + DEBUG_APPLICATION_ID_SUFFIX


def with_debug_identity(gradle: str) -> str:
    """``gradle`` with the suffixed debug buildType, or ValueError on any other shape.

    Only the debug buildType may change the id: a suffix anywhere else (the
    release buildType, defaultConfig, a flavor) would ship a Play bundle that is
    not ``io.tinyassets.app``, so any foreign suffix or debug block fails loudly.
    """
    suffixes = len(re.findall(r"^\s*applicationIdSuffix\b", gradle, flags=re.MULTILINE))
    debug_blocks = len(re.findall(r"^\s*debug\s*\{", gradle, flags=re.MULTILINE))
    if DEBUG_BUILD_TYPE in gradle and suffixes == 1 and debug_blocks == 1:
        return gradle
    if suffixes or debug_blocks:
        raise ValueError(
            "generated build.gradle already declares applicationIdSuffix or a debug "
            f"buildType (suffixes={suffixes}, debug blocks={debug_blocks}); "
            "only the suffixed debug buildType this script writes is allowed"
        )
    gradle, count = re.subn(
        r"(?m)^(\s*buildTypes\s*\{[ \t]*\n)", lambda m: m.group(1) + DEBUG_BUILD_TYPE, gradle
    )
    if count != 1:
        raise ValueError(f"expected exactly one buildTypes block in build.gradle; found {count}")
    return gradle


def _one_value(text: str, pattern: str, label: str) -> str:
    values = re.findall(pattern, text, flags=re.MULTILINE)
    if len(values) != 1:
        raise ValueError(
            f"expected exactly one {label} in generated build.gradle; found {len(values)}"
        )
    return values[0]


def configure(mobile: Path) -> AndroidRelease:
    release = load_release(mobile)

    capacitor_path = mobile / "capacitor.config.json"
    capacitor = json.loads(capacitor_path.read_text(encoding="utf-8"))
    if capacitor.get("appId") != release.app_id:
        raise ValueError(
            f"package identity drift: {capacitor_path} has {capacitor.get('appId')!r}, "
            f"android-release.json has {release.app_id!r}"
        )

    gradle_path = mobile / "android" / "app" / "build.gradle"
    variables_path = mobile / "android" / "variables.gradle"
    gradle = gradle_path.read_text(encoding="utf-8")
    variables = variables_path.read_text(encoding="utf-8")

    namespace = _one_value(gradle, r'^\s*namespace\s*(?:=\s*)?["\']([^"\']+)["\']', "namespace")
    application_id = _one_value(
        gradle, r'^\s*applicationId\s*(?:=\s*)?["\']([^"\']+)["\']', "applicationId"
    )
    if namespace != release.app_id or application_id != release.app_id:
        raise ValueError(
            "generated package identity drift: "
            f"namespace={namespace!r}, applicationId={application_id!r}, "
            f"expected={release.app_id!r}"
        )

    sdk_names = {
        "minSdkVersion": release.min_sdk,
        "targetSdkVersion": release.target_sdk,
        "compileSdkVersion": release.compile_sdk,
    }
    for name, wanted in sdk_names.items():
        found = _one_value(variables, rf"^\s*{name}\s*=\s*(\d+)", name)
        if int(found) != wanted:
            raise ValueError(
                f"generated {name}={found}, expected {wanted} from android-release.json"
            )

    code_pattern = r"(?m)^(\s*)versionCode\s*(?:=\s*)?\d+\s*$"
    name_pattern = r"(?m)^(\s*)versionName\s*(?:=\s*)?[\"'][^\"']*[\"']\s*$"
    gradle, code_count = re.subn(
        code_pattern, lambda match: f"{match.group(1)}versionCode {release.version_code}", gradle
    )
    gradle, name_count = re.subn(
        name_pattern, lambda match: f'{match.group(1)}versionName "{release.version_name}"', gradle
    )
    if code_count != 1 or name_count != 1:
        raise ValueError(
            "generated version fields changed shape: "
            f"versionCode matches={code_count}, versionName matches={name_count}"
        )
    gradle = with_debug_identity(gradle)
    gradle_path.write_text(gradle, encoding="utf-8")
    strings_path = mobile / DEBUG_STRINGS_PATH
    strings_path.parent.mkdir(parents=True, exist_ok=True)
    strings_path.write_text(DEBUG_STRINGS, encoding="utf-8")
    print(
        f"configured {release.app_id} versionCode={release.version_code} "
        f"versionName={release.version_name} "
        f"SDK={release.min_sdk}/{release.target_sdk}/{release.compile_sdk}; "
        f"debug builds install as {debug_application_id(release)} ({DEBUG_APP_NAME!r})"
    )
    return release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mobile-root", type=Path, default=DEFAULT_MOBILE)
    args = parser.parse_args(argv)
    try:
        configure(args.mobile_root.resolve())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
