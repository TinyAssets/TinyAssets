## Context

Capacitor generates mobile/ios. The signed release workflow owns signing. The hosted app and AuthKit are production dependencies; the reviewer account must have a working user-selected model connection.

## Goals / Non-Goals

Automate the packet on one real iPhone, and prove the same UI journey on Simulator. Do not submit to Apple, delete accounts, provision AWS, or manufacture agent replies.

## Decisions

- Install a checked-in Swift UI test into the generated project. A bundled sample is copied into the runner's Files-accessible Documents directory; the app's actual document picker attaches it.
- Pass credentials through environment into a private xctestrun file, never compiler settings or source. Paste the password only into an asserted secure field to avoid iOS keyboard character echo. Capture XCTest output privately, retain only sanitized summaries and named screenshots, then delete private material. Device Farm run environment is restricted by project IAM.
- The repository is public. Encrypt all review evidence with ephemeral X25519, HKDF-SHA256 and AES-256-GCM to the committed recipient public key, independently of reviewer credentials. The founder generates an owner-only private key locally; it is never printed or uploaded. Preflight rejects the committed placeholder before secret-bearing jobs. Pinned dependencies install in separate steps without secrets.
- Reuse ios-release through workflow_call. Secret-bearing proof dispatches require main; ios-build compile-only proof is available before merge.
- Dry run builds and runs Simulator before any AWS client is constructed. Physical mode discovers iPhone 14+ on the greatest available iOS version, requests one device, records video, bounds duration to 30 minutes, and fails on incomplete evidence or failed tests.
- Project-scoped IAM explicitly grants only upload/run management and evidence listing, plus ListRuns/ListUploads for the next-run cancellation recovery sweep. ListDevices requires a separate global read permission. Before each physical run, sweep runs/uploads older than two hours in the dedicated project; fail before new credentials are sent if cleanup fails.

## Risks / Trade-offs

- AuthKit UI and production latency can change: explicit bounded waits, fail closed and retain screenshots.
- Device Farm re-signs the current shell; its video is not evidence of the old build 3 binary. Apple submission remains a separate decision.
- Newest AWS iOS may trail Apple's latest public iOS: record actual device/OS for review.
- A missing model connection is a real blocker, not a reason to seed an interim API key.
- The app-store environment only permits main and has a required reviewer. Compilation and signed-out launch can run without credentials. Forced cancellation can interrupt AWS cleanup; the next physical run sweeps old resources (dry runs never call AWS). Without a next run the lead must clean up manually. GitHub has no configurable cancellation grace; the 75-minute job timeout leaves ordinary cleanup time but cannot prevent forced termination.

## Migration Plan

Merge tooling, founder generates the local key and commits its public half, provisions AWS and adds three secrets, dispatches from main in dry run then physical mode. Disable dispatch/revert tooling to roll back; no data migration.
