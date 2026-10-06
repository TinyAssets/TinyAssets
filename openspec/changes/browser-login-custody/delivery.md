# L12 prerequisite assessment (2026-10-05)

Status: blocked before implementation, browser custody remains unavailable.
Task 1.1 was investigated; its prerequisites did not pass, so every delivery
checkbox remains open. No production browser capability or storage shape was added.

Base inspected: `origin/feat/per-role-uid-split` at
`b714cfc571ffbfab9c9e16bf2ea551721246ac84`. Merged `origin/main` at
`b61e68934c9889809f8b4de53d1fc410ec749d70` as requested, without rebasing.

## Evidence and handoff

- D73 preview exists: `tinyassets/role_preview.py` admits data into the owner
  launcher; `deploy/role_decoder.py` selects the nested sandbox profile for
  preview. `tinyassets/ui_preview.py` enables Chromium's sandbox, disables DNS
  and uses a dead proxy. It is a short-lived offline renderer with no logged-in
  profile, interactive live view, accessibility action protocol or takeover.
- `deploy/role_owner_launcher.py` admits image-decoder, workspace-git,
  ui-preview and preview-write operations. There is no interactive browser
  launch kind or credentialed browser-session lifecycle to consume.
- D5 remains open in `universe-agent-harness/tasks.md` task 2.6. Searches of
  canonical Python and app JavaScript found no restricted browser broker,
  accessibility snapshot interface or Take/Return control implementation.
  The D5 owner must deliver and prove that substrate; preview acceptance alone
  does not satisfy the browser custody release gate.
- `connect-anything-ladder/tasks.md` still has all MCP metadata, protected
  secret-entry and lifecycle tasks open. Existing HTTP incarnation fencing,
  owner sessions and `tinyassets/connection_continuations.py` are useful reuse
  points, but are not proof of the required MCP/browser lifecycle contract.
- L12 will consume those APIs once available, then implement tasks 1.2-1.6 and
  exercise task 1.7. Do not create a competing D5 launcher, card, coordinator,
  vault or metadata schema in this lane while their owning lanes are unfinished.

The design and delta spec now explicitly preserve the founder's instructions:
last-resort ladder position, owner cell plus Chromium sandbox, accessibility
snapshots instead of DOM, custom protected login capture, model-independent
Take control/Return control/Stop, and agent identification to sites.

## Delivery decisions

The draft PR targets `feat/per-role-uid-split`: the explicit L12 target takes
precedence over the common-rule main target. The required main merge is included;
its inherited changes are not claimed as L12 implementation. No tests or prompt
budgets were changed. Main specs are not synced to claim an unimplemented feature.
No deployment, real-user browser connect/cancel/revoke proof or public canary
is claimed. Task 1.8 remains open until the actual implementation is deployed.

## Verification

- `openspec validate browser-login-custody --strict`: passed.
- `git diff --check`: passed.
- Ruff on all existing Python paths changed against the stacked base, excluding
  the generated mirror: passed (including the required main merge).
- `python packaging/claude-plugin/build_plugin.py`: 634 files staged, import
  probe passed, no generated diff.
- L12-only hygiene (`--base 70742af8d3 --head cdc98985f7`): 0 added,
  0 removed, 0 tampering. The broader stacked PR check is recorded separately.
- Linux oracle attempted with `MSYS_NO_PATHCONV=1` and arguments
  `-- -q tests/test_role_preview.py tests/test_ui_preview.py --basetemp /tmp/b`.
  First run exited 1: `error waiting for container: unexpected EOF`. One retry
  exited 1 because the Docker Linux engine named pipe was absent. No pytest
  result was produced; this is not a pass or a skip. No Docker restart or
  infrastructure change was attempted.

Draft PR: https://github.com/TinyAssets/TinyAssets/pull/4512.

Full stacked PR hygiene against `origin/feat/per-role-uid-split`: 254 added
tests, 0 removed, 0 tampering. Added tests/product code belong to the inherited
main merge; L12 itself remains documentation-only.

## Cross-family review

Claude via `peer-agents` reviewed commit `cdc98985f7` and inspected the cited
implementation and prerequisite tasks. Wrapper exited 0; verdict **APPROVE**,
dependency assessment **AGREE**, no floor/correctness findings. The reviewer
confirmed that preview is offline, the launcher lacks a browser-session kind,
and D5/MCP prerequisites are unfinished. The founder contract is consistent with
credential blindness and no false completion claim was found.

Nonblocking terminology note: older prohibitions name DOM snapshots while the
new agent surface is accessibility-only. Those prohibitions still apply to any
DOM-derived artifact and do not authorize a raw-DOM channel; retain them alongside
the stricter new requirement. No rebase is authorized: continue merging the stack
base, and retarget the draft when that base lands.
