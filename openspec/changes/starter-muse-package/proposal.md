## Why

The default agent needs Muse-level capabilities as editable package content on minimal pi plumbing, per founder direction of 2026-10-06 (PR #4518).

## What Changes

- Publish proactivity settings, goals, feed, ideas, notification workflow recipes and an app_ui component in the starter bundle.
- Add on-demand skills for proactivity, goals, monitors/reminders, images, inbox setup and layout activation.
- Verify package content, workflow notification decisions, discovery and browser rendering without enlarging resident instructions.

## Capabilities

### New Capabilities
- `starter-muse-package`: User-editable personal-agent defaults composed from existing primitives.

### Modified Capabilities
None.

## Impact

Owner: Codex. Branch: feat/starter-muse-package. One draft PR to main.
Only starter package assets, its content publisher, tests and generated plugin mirror change. No platform runtime, prompt, authority or provisioning changes.
Automatic fresh-account installation depends on starter-seed-lifecycle and the starter consumer cutover: main currently has no caller of starter_agent_files(). K3 must not counterfeit that integration with another installer.
