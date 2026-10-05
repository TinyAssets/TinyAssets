# Delivery status

Implementation is pending; the security-scope decision is resolved by the lead amendment
recorded in design.md D8 (2026-10-04). No build task is
checked off, no runtime file has changed, and nothing has been deployed.

Base: `22560b9d0f7f79582cfea482b3c4efb2cda27e5a` (`origin/main`, fetched during
this session). The existing clean worktree is already on
`feat/per-role-uid-split`. Proposal, design, tasks and both spec deltas were read.
OpenSpec apply reports ready; delivery admission reports `ALLOWED`.

## Acceptance scope resolved: every engine class

The lead, applying founder principles, decided that cross-user isolation is the platform's
ONLY invariant and is non-negotiable, using the supplied Muse per-user runtime-cell
architecture as the reference. "Deny only for jailed providers" is REJECTED.

D8 now requires every owner-scoped engine child and descendant to enter the owner's
bubblewrap namespace through the launcher. Shared uid 1003 and `ta-work` remain for role
separation and rollback compatibility, but their access is confined to the owner view.
D8 records every known spawn site, including engine MCP, native discovery, preview,
image decoding and additional utility/box/auth-probe paths. No class gets an unjailed
fallback; a fresh implementation inventory must close any further sites before acceptance.

Acceptance is now the production-image Linux oracle matrix: owner A's actual process of
EVERY engine class is denied B's data, owner.json, vault and owner token, including
procfs/fd/IPC routes, with a working owner-A operation for each class. Synthetic uid probes
and a single provider jail cannot substitute. Existing two-round refute fixes remain.
The counterexample below is retained as evidence against the rejected shared-group-only
mechanism; it is not acceptance evidence for the amended design.

## Linux counterexample (not production-image acceptance)

This command ran successfully in a disposable, network-disabled Linux container,
using synthetic files only. It implements D4's directory and workspace permission
inventory and D1's engine identity. It does not mount or access user data.

PowerShell command:

```powershell
@'
import os
from pathlib import Path
root = Path('/tmp/role-boundary')
root.mkdir(mode=0o755)
for owner in ('owner-a', 'owner-b'):
    cc = root / owner
    cc.mkdir()
    os.chown(cc, 1001, 1100)
    os.chmod(cc, 0o2711)
    workspace = cc / 'workspace'
    workspace.mkdir()
    os.chown(workspace, 1001, 1100)
    os.chmod(workspace, 0o2770)
    data = workspace / 'private.txt'
    data.write_text(owner + ' private data')
    os.chown(data, 1001, 1100)
    os.chmod(data, 0o660)
os.setgroups([1100])
os.setresgid(1003, 1003, 1003)
os.setresuid(1003, 1003, 1003)
print(f'uid={os.getuid()} gid={os.getgid()} groups={os.getgroups()}')
for owner in ('owner-a', 'owner-b'):
    print(f'{owner}: READ ALLOWED: {(root / owner / "workspace" / "private.txt").read_text()}')
'@ | docker run --rm -i --network none python:3.11-slim python -
```

Exact output, exit 0:

```text
uid=1003 gid=1003 groups=[1100]
owner-a: READ ALLOWED: owner-a private data
owner-b: READ ALLOWED: owner-b private data
```

Adding D4's broker traverse ACL does not remove the engine's work-group access.
This is a counterexample to general denial, not a failure of the intended vault
permission boundary and not a substitute for the required production-image proof.

## Current-code differences to account for during implementation

- Both dependencies are now present: `tinyassets/broker/supervisor.py` and
  `tinyassets/platform_secrets.py`. The design's unmerged-dependency statements
  are historical.
- The jail module is `tinyassets/providers/provider_jail.py`. Current native discovery
  already calls `aspawn_owned` with `metadata_view` and `require_confinement=True`;
  preserve this narrower view when adding launcher identity enforcement.
- `engine_mcp_http._EngineServer.start` now starts from `child_env(os.environ)`,
  supplies OAuth service configuration, and propagates the execution-owner tree
  environment in addition to the six engine configuration names. Its allowlist
  requires a current consumer audit; blindly using the historical list could
  break OAuth and lease tracking.
- `platform_secrets.child_env` now excludes the `TINYASSETS_OAUTH_` namespace and
  `TINYASSETS_CONNECTION_OAUTH_SERVICE`, in addition to `CHILD_FORBIDDEN_ENV`.
- Storage layout is now version 2, with an existing consent migration. Role
  migration must preserve that document and its recovery semantics.
- Additional direct subprocess sites exist in `ui_preview._supervised` and
  `tool_images._decode_in_child`. The former uses PID containment without
  filesystem isolation. D8 now assigns both to owner-scoped engine cells.

These are audit findings, not implemented deviations.

## Task 2.1 preliminary write audit

The audit is not complete and does not prove that `/app` can yet be made read-only.
Confirmed so far:

- API data helpers, MCP universe paths and the OAuth database resolve through
  `storage.data_dir`; old `/app/output` references describe corrected bugs.
- Provider `default_view` creates `.agent-workspace` in the command center and
  binds the current credential snapshot read-write for CLI lock/session files.
- Node sandbox scratch comes from `tempfile.mkdtemp`; its jail uses `/tmp` as
  HOME. Production proofs must still establish access after the UID change.
- `codex_provider._codex_workdir` defaults to the source root. Its actual served
  call paths must be audited before deciding whether the fallback in D2 is needed.
- Preview and image decoder children use a source-tree cwd; cwd alone does not
  prove a source-tree write.
- The healthcheck canary reads its source-adjacent helper and performs HTTP I/O.

## Release-critical scope

Actual release-critical files changed: **0** (only this delivery record changed).
The scope guard's sensitive-path expression and hard cap of 8 were inspected.
Expected existing release-critical files for the specified implementation are
`Dockerfile`, `deploy/docker-entrypoint.sh`, `deploy/compose.yml`,
`deploy/native/ta_op.c`, and `.github/workflows/docker-build.yml`.
The deploy validator and any additional privileged artifacts must be inventoried
before claiming the final count. No workflow behavior has changed.

## Verification and remaining work

Docker Desktop's Linux engine is available (`docker version`: client/server
29.5.2). No production Dockerfile build, Linux oracle acceptance run, migration
dry-run, migration-copy proof, stream proof, healthcheck proof, or affected
Windows test run has occurred. No plugin mirror regeneration is needed for this
documentation-only record. Security implementation review has not been dispatched.

Documentation checks: `git diff --check` passed. The hygiene gate
(`python scripts/test_hygiene_gate.py --base origin/main --head HEAD`) reported
`tests added 0, removed 0, tampering findings 0, product lines added 0`.
`python -m ruff check` exited 1 with `Found 55 errors.` in unchanged repository
files; no Python file was edited, and no unrelated lint fixes were made.

Tasks 2.1-2.8 all remain incomplete. Tasks 2.9-2.10 remain unchecked; production
deployment is explicitly outside this request. The acceptance scope is resolved.
Finish the write/spawn inventory, then implement and prove 2.1-2.8 in order.


## Design-amendment verification (2026-10-04)

Started from `aae57d3034` with `git pull --ff-only origin feat/per-role-uid-split`
(already up to date). This amendment changes only the proposal, design, tasks, runtime
process-role delta and this delivery record. No product code or gate file changed.
`npx --yes @fission-ai/openspec validate per-role-uid-split --strict` and
`git diff --check` pass. Task sections contain 2 and 10 checkboxes (12 total).
`python -m ruff check` still reports 55 errors in unchanged files. The production-image
Linux oracle matrix is specified, not executed: its implementation and production-image
harness support belong to the still-unchecked build tasks. No deployment or PR is part
of this amendment.


Cross-family amendment review via `peer-agents` returned `DISAGREE_EVIDENCE`;
**AGREE** with all four findings, corrected in this amendment: D4 now declares relay
parent/directory/socket permissions and runtime mode-map updates; D8/spec/tasks explicitly
require private networking and cross-owner port/socket denial; root migration owns legacy
owner.json cleanup before chown; D7's owner-work exception and absent historical gh site
are narrowed explicitly. The reviewer confirmed inventory coverage and preservation of
both prior refute rounds. No second review round was dispatched.
