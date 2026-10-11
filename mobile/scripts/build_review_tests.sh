#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RUNNER_TEMP:?}"
gem install xcodeproj --version 1.27.0 --no-document --user-install
export GEM_HOME="$(ruby -e 'puts Gem.user_dir')"
export GEM_PATH="$GEM_HOME:$(ruby -e 'puts Gem.path.join(":")')"
ruby scripts/install_review_ui_tests.rb
for platform in iphonesimulator iphoneos; do
  destination='generic/platform=iOS'
  if [ "$platform" = iphonesimulator ]; then destination='generic/platform=iOS Simulator'; fi
  xcodebuild -project ios/App/App.xcodeproj -scheme ReviewVideo -configuration Debug \
    -sdk "$platform" -destination "$destination" \
    -derivedDataPath "$RUNNER_TEMP/review-$platform" CODE_SIGNING_ALLOWED=NO \
    build-for-testing > "$RUNNER_TEMP/review-build-$platform.log" 2>&1 || {
      tail -80 "$RUNNER_TEMP/review-build-$platform.log"; exit 1;
    }
  runner="$RUNNER_TEMP/review-$platform/Build/Products/Debug-$platform/ReviewUITests-Runner.app"
  python3 - "$runner/Info.plist" <<'PY'
import plistlib
import sys
from pathlib import Path
p = Path(sys.argv[1])
info = plistlib.loads(p.read_bytes())
info.update(UIFileSharingEnabled=True, LSSupportsOpeningDocumentsInPlace=True,
            CFBundleDisplayName="Review Files")
p.write_bytes(plistlib.dumps(info))
PY
  if [ "$platform" = iphonesimulator ]; then codesign --force --deep --sign - "$runner"; fi
done
mkdir -p "$RUNNER_TEMP/review-package"
cp -R "$RUNNER_TEMP/review-iphoneos/Build/Products/Debug-iphoneos/ReviewUITests-Runner.app" "$RUNNER_TEMP/review-package/"
cp scripts/review_xctest.py "$RUNNER_TEMP/review-package/"
(cd "$RUNNER_TEMP/review-package" && zip -qr "$RUNNER_TEMP/review-tests.zip" .)
