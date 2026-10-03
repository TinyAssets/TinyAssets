"""Finish the generated iOS project with release-required Info.plist keys.

`npx cap add ios` generates ios/ (gitignored); this runs right after it in CI
(and locally) to register the ``tinyassets://auth`` custom URL scheme and the
user-facing microphone purpose string required before the dark realtime-voice
slice can request capture. The URL scheme lets the in-app OAuth browser tab hand
the sign-in code back to the app — the iOS counterpart of the Android
intent-filter added by add_app_scheme.py.

The generated shell uses only exempt operating-system encryption (HTTPS/TLS via
WebKit and URLSession), so it also declares ``ITSAppUsesNonExemptEncryption`` as
false. This keeps App Store Connect's export-compliance answer tied to the built
artifact instead of a manual submission-time memory.

iOS needs no loopback foreground-service (unlike Android's dataSync service):
ASWebAuthenticationSession + the custom scheme carry the sign-in return natively.

Idempotent.
"""

from __future__ import annotations

import pathlib
import sys

MOBILE = pathlib.Path(__file__).resolve().parents[1]
INFO_PLIST = MOBILE / "ios" / "App" / "App" / "Info.plist"

# The CFBundleURLTypes block registering the tinyassets:// scheme. Inserted once,
# right before the plist's final closing </dict>.
URL_TYPES_BLOCK = """\t<key>CFBundleURLTypes</key>
\t<array>
\t\t<dict>
\t\t\t<key>CFBundleURLName</key>
\t\t\t<string>io.tinyassets.app</string>
\t\t\t<key>CFBundleURLSchemes</key>
\t\t\t<array>
\t\t\t\t<string>tinyassets</string>
\t\t\t</array>
\t\t</dict>
\t</array>
"""

MICROPHONE_PURPOSE = (
    "TinyAssets uses the microphone only while voice conversation is active "
    "so you can speak with your command center."
)
MICROPHONE_BLOCK = f"""\t<key>NSMicrophoneUsageDescription</key>
\t<string>{MICROPHONE_PURPOSE}</string>
"""
EXPORT_COMPLIANCE_BLOCK = """\t<key>ITSAppUsesNonExemptEncryption</key>
\t<false/>
"""


def install_configuration(info_plist: pathlib.Path = INFO_PLIST) -> int:
    if not info_plist.exists():
        print(f"Info.plist not found at {info_plist} — run `npx cap add ios` first.")
        return 1
    text = info_plist.read_text(encoding="utf-8")
    microphone_key = "<key>NSMicrophoneUsageDescription</key>"
    microphone_value = f"<string>{MICROPHONE_PURPOSE}</string>"
    export_key = "<key>ITSAppUsesNonExemptEncryption</key>"
    if microphone_key in text and microphone_value not in text:
        print("::error:: existing microphone purpose does not match the release copy")
        return 1
    if export_key in text and f"{export_key}\n\t<false/>" not in text:
        print("::error:: existing export-compliance declaration is not false")
        return 1
    additions: list[str] = []
    if "<string>tinyassets</string>" not in text:
        additions.append(URL_TYPES_BLOCK)
    if microphone_key not in text:
        additions.append(MICROPHONE_BLOCK)
    if export_key not in text:
        additions.append(EXPORT_COMPLIANCE_BLOCK)
    if not additions:
        print("TinyAssets iOS release configuration already registered — nothing to do.")
        return 0

    marker = "</dict>\n</plist>"
    block = "".join(additions)
    if marker not in text:
        # Tolerate trailing whitespace variations.
        idx = text.rfind("</dict>")
        if idx == -1:
            print("::error:: could not find closing </dict> in Info.plist")
            return 1
        new_text = text[:idx] + block + text[idx:]
    else:
        new_text = text.replace(marker, block + marker, 1)
    info_plist.write_text(new_text, encoding="utf-8")

    check = info_plist.read_text(encoding="utf-8")
    if "<string>tinyassets</string>" not in check:
        print("::error:: failed to register the tinyassets URL scheme")
        return 1
    if microphone_value not in check:
        print("::error:: failed to register the microphone purpose")
        return 1
    if f"{export_key}\n\t<false/>" not in check:
        print("::error:: failed to register exempt export-compliance declaration")
        return 1
    print("registered TinyAssets iOS release configuration in Info.plist")
    return 0


def main() -> int:
    return install_configuration()


if __name__ == "__main__":
    sys.exit(main())
