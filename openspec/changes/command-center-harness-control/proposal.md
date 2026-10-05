## Why

Audit L11–L13 found packaged settings that runtime ignores, tool-only extensions, and a custom-UI bridge that cannot operate the owner's harness. Founder direction (2026-10-04) makes the owner king of the command center, including the main agent, with cross-user isolation as the only platform invariant.

## What Changes

- Read versioned `settings.yaml` at runtime for model selection, exposed tools, skills, extensions, starter hooks and loop behavior.
- Extend the existing extension mechanism with lifecycle hooks, tool replacement, commands and inline cards; no second plugin runtime.
- Add owner-permitted `ta` capability discovery/dispatch to the existing custom-UI bridge, including connections, memory and harness files.
- Keep authentication and execution scoping enforceable while owner behavior and approval policy remain editable; no platform LLM or immutable starter/main-agent policy.

## Capabilities

### New Capabilities

- `command-center-harness-control`: settings snapshots, loop extension protocol and owner-permitted UI/ta parity.

### Modified Capabilities

None. This supplies the missing D7 runtime contract and extends D6/`composable-ui-experiences` through their existing mechanisms. It does not replace agent-model-selection, package installation or inline approval requirements.

## Impact

Planning owner: Codex. Branch: `spec/muse-pi-gap-proposals`; baseline `origin/main` at `9e96ff9595`. Draft only, no product code or PR. Future implementation is one separate lane/PR.

Expected integration: `universe_intelligence.py`, `universe_tools.py`, `ta_cli.py`, `ta_capabilities.py`, `ui_frame.py`, `app_ui.js`, harness history and package settings. Coordinate UI changes with the app lane; this branch does not touch `tinyassets/onboarding/app.html`.

Dependencies: `starter-agent-out-of-plumbing` owns instruction extraction/main replacement proof; D7 owns memory/history/editor and learning retirement; D8/D9 plus `command-center-agent-templates` own roster/package installation, quarantine and main selection; `composable-ui-experiences` owns renderer isolation; `inline-connect-and-approve` owns interactive decisions. These responsibilities stay there.
