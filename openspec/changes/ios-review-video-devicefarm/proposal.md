## Why

Apple's Guideline 2.1 request needs a physical-iPhone recording, and the founder has no iPhone. Automate the existing six-step packet on a rented physical device.

## What Changes

- Add one production XCUITest journey, used unchanged on Simulator and Device Farm.
- Reuse the signed iOS release build; add explicit dispatch, dry run, bounded device selection, recording and screenshot collection.
- Replace manual recording instructions with AWS account, project, IAM and secret setup.

## Capabilities

### New Capabilities
- `ios-review-video`: automated, credential-safe App Review recording.

### Modified Capabilities
None.

## Impact

Mobile test tooling, iOS Actions workflows, AWS Device Farm metered usage and founder setup. No product API, storage or production authentication changes. Owner: Codex; branch: feat/ios-review-video-devicefarm.
