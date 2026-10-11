## Context

Capacitor generates mobile/ios. The signed release workflow owns signing. The hosted app and AuthKit are production dependencies; the reviewer account must have a working user-selected model connection.

## Goals / Non-Goals

Automate the packet on one real iPhone, and prove the same UI journey on Simulator. Do not submit to Apple, delete accounts, provision AWS, or manufacture agent replies.

## Decisions

- Install a checked-in Swift UI test into the generated project. A bundled sample is copied into the runner's Files-accessible Documents directory; the app's actual document picker attaches it.
- Pass credentials through environment into a private xctestrun file, never compiler settings or source. Capture raw XCTest output privately, publish only sanitized summaries and named screenshots, then delete private material. Device Farm run environment is restricted by project IAM.
- Reuse ios-release through workflow_call. ios-build has a manual proof entry so the new workflow can be exercised from the branch before merge.
- Dry run builds and runs Simulator before any AWS client is constructed. Physical mode discovers iPhone 14+ on the greatest available iOS version, requests one device, records video, bounds duration to 30 minutes, and fails on incomplete evidence or failed tests.
- Project-scoped IAM includes child upload/run/job/test/artifact resources. ListDevices requires a separate global read permission; a literal project-only policy cannot implement current-device discovery.

## Risks / Trade-offs

- AuthKit UI and production latency can change: explicit bounded waits, fail closed and retain screenshots.
- Device Farm re-signs the current shell; its video is not evidence of the old build 3 binary. Apple submission remains a separate decision.
- Newest AWS iOS may trail Apple's latest public iOS: record actual device/OS for review.
- A missing model connection is a real blocker, not a reason to seed an interim API key.

## Migration Plan

Merge tooling, founder provisions AWS and adds three secrets, dispatch dry run then physical mode. Disable dispatch/revert tooling to roll back; no data migration.
