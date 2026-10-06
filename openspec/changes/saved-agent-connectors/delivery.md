# L10 implementation assessment

## Scope and branch

Owner: Codex. Worktree: `wf-L10`. Branch: `feat/saved-agent-connectors`.
The draft PR targets `feat/mcp-connect-ladder` (#4496), following the explicit
lane instruction over the common-rule `main` target. No deployment or completed
connector functionality is claimed by this record.

## Inspected baseline

Parent: `7af51406e7216ad9f56ce394f2c1ad7def5d8b35`.
Fetched main: `70c30da9f94e7a3e09d0e18dd18bbb6d747ed7ea`.

- `tinyassets/ta_cli.py:extensions` discovers `extension.json` and executables
  in the existing shared and per-agent workspace directories. `main` executes
  them in the caller's jail. There is no saved-connector revision gate here.
- `tinyassets/ta_capabilities.py:Capabilities` binds authority to one bash
  invocation. Its socket rechecks serving authority and resolves current
  owner/center HTTP grants; it does not identify a particular extension revision
  or restrict that extension to declared local slots.
- `tinyassets/mcp_attachment.py` demonstrates broker-owned attachment metadata,
  incarnation checks and backing HTTP custody. This is the lifecycle to consume,
  not replace.
- `tinyassets/authoring/store.py` stores immutable node/evaluator definitions;
  `authoring/service.py:run_test` executes those definitions, not ordinary `ta`
  executable packages. A simulated effect is not a real API test receipt.
- `tinyassets/command_center_packages.py` and `api/package_requests.py` already
  provide quarantined publishing and exact install plans. Installation copies
  files into the recipient workspace. That approval is not a continuing
  per-revision extension dispatch gate.
- `tinyassets/command_center_update_policy.py` explicitly limits automatic
  updates to presentation changes; executable code changes require a decision.
  It cannot be treated as an existing connector author/permission-ceiling grant.

These are implementation findings, not grounds to treat all work as blocked.
Claude's independent review completed successfully (exit 0), verdict **ADAPT**.
The complete result is in `readiness-review.md`. It approves no implementation.

## Review disposition and handoff

- **AGREE:** ordinary extension loading has no immutable revision gate.
- **AGREE:** the capability socket is mounted at a known path throughout the
  jail, not inherited as a file descriptor; any extension reaches the launch's
  current grants. Existing secret custody and cross-owner refusal remain real.
- **AGREE:** install consent is not dispatch activation, and presentation-only
  update policy cannot authorize executable connector revisions.
- **AGREE:** legacy node authoring would introduce a second runtime/effect
  authority and is not the implementation route.
- **AGREE:** a source snapshot/status-only slice can be built independently.
  It does not complete safe testing, exact-revision activation or cross-author
  reuse. This assessment does not implement that slice or claim it is blocked.
- **AGREE:** the common immutable package-cell API belongs to
  `per-role-uid-split` tasks 2.5/2.6, also needed by MCP stdio. The concrete
  handoff is `docs/concerns/2026-10-05-saved-connectors-package-cell-prerequisite.md`.
- **DISAGREE_EVIDENCE:** the review suggests rebasing after parent integration;
  the user explicitly requires merges, no rebases or force-pushes. Consume the
  parent's reconciliation with `git merge origin/feat/mcp-connect-ladder`.

The next implementation slice can save package bytes in the existing
`store_blob`/`record_version` store, keep private bindings and receipts out of
exports, and show untested/changed status. It must leave execution and activation
unavailable until the shared admission boundary can actually enforce them.
The full L10 acceptance remains open; no new runtime or approval card was added.

## Main integration attempt

An ordinary `git merge origin/main` encountered conflicts in the parent lane's
consent, OAuth, connection-card source and tests, including generated mirrors.
The attempt was aborted with `git merge --abort` from an otherwise clean tree;
no work was discarded. Main is not merged. These are integration work, not
evidence that the connector capability itself cannot be implemented.

## Verification

Baseline Linux oracle: **61 passed, zero skips, exit 0** (31.64 seconds after
the image build):

```text
MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q
  tests/test_ta_capabilities.py tests/test_ta_capabilities_jail.py
  tests/test_mcp_attachment.py tests/test_converse_turn_cost.py --basetemp /tmp/b
```

No task checkbox is marked complete. No implementation, deployment, app proof,
or capability spec sync has happened yet.

`openspec validate saved-agent-connectors --strict` and `git diff --check`
passed. This diff changes Markdown only: no changed Python files for ruff and
no `tinyassets/` changes requiring plugin regeneration. Existing tests and
always-sent prompts are untouched. Parent was fetched and merged again after
the review; it remained at the inspected SHA and Git reported already up to date.
