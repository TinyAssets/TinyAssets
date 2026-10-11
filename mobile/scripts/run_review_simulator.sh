#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RUNNER_TEMP:?}"
output="$RUNNER_TEMP/review-simulator"
mkdir -p "$output"
# A new device, not an existing logged-in simulator, proves cold signed-out launch.
selection=$(xcrun simctl list -j | python3 -c '
import json,sys
d=json.load(sys.stdin)
r=max((r for r in d["runtimes"] if r["isAvailable"] and r["identifier"].split(".")[-1].startswith("iOS-") and any(p["isAvailable"] and p["name"].startswith("iPhone") for p in d["devices"].get(r["identifier"],[]))), key=lambda r:tuple(map(int,r["version"].split("."))))
# A device type in the global catalogue may require an uninstalled runtime.
# Pick a type already known to work with this runtime, then create a clean copy.
p=next(p for p in d["devices"][r["identifier"]] if p["isAvailable"] and p["name"].startswith("iPhone"))
t=next(t for t in d["devicetypes"] if t["name"]==p["name"])
print(t["identifier"], r["identifier"])
')
read -r device_type runtime <<< "$selection"
device=$(xcrun simctl create ReviewVideo "$device_type" "$runtime")
video_pid=''
cleanup() {
  if [ -n "$video_pid" ]; then kill -INT "$video_pid" 2>/dev/null || true; wait "$video_pid" || true; fi
  xcrun simctl shutdown "$device" >/dev/null 2>&1 || true
  xcrun simctl delete "$device"
}
trap cleanup EXIT
xcrun simctl boot "$device"
xcrun simctl bootstatus "$device" -b
xcrun simctl status_bar "$device" override --time '9:41' --batteryState charged --batteryLevel 100
xcrun simctl io "$device" recordVideo --codec=h264 "$output/simulator-proof.mp4" > /dev/null 2>&1 &
video_pid=$!
run_file=$(find "$RUNNER_TEMP/review-iphonesimulator/Build/Products" -maxdepth 1 -name '*.xctestrun' -print -quit)
test -n "$run_file"
python3 scripts/review_xctest.py --xctestrun "$run_file" \
  --destination "platform=iOS Simulator,id=$device" --output "$output" "$@"
