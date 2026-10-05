# Current delivery: D18 daemon broker acquisition

Started from `be34773f5f`; required fast-forward pull was already current.
D12-D17 remain intact. D18 is a mechanical continuation of D6/D7: daemon
supervisor acquires the launcher-owned broker, authenticates launcher parent
and broker peers before sending proof/token, holds its fence in a process-bound
registry and stops without signals or socket removal. The legacy owner file
reader/writer and process generation argument are removed. Broker-selected
legacy workers refuse before allocation. Production CMD remains unactivated.

Release-critical paths under SENSITIVE_RE: zero in this step (cap 8).
Security-sensitive runtime files: `tinyassets/broker/supervisor.py`,
`tinyassets/broker/client.py`, `tinyassets/broker/process.py`,
`tinyassets/storage/outbound_connections.py`; generated mirrors accompany them.
Acceptance harness: `scripts/role_launcher_oracle.py`. Existing process test
names remain, with assertions amended for the required in-memory contract.

One cross-family code review through peer-agents returned ADAPT. AGREE with
both blocking findings: removed the accidental UTF-8 BOM and stale timer-loop
inventory. A second stale `_spawn` entry was found by the inventory suite;
per AGENTS loop 7, handed that bounded reconciliation to another agent, which
removed it and proved the inventory suite (5 passed) plus Ruff. No second review
round. Also accepted the nonblocking suggestion to return ProxyRequestError
when a stopped supervisor's existing client is used. Reviewer agreed with
launcher/broker peer checks, retirement, non-dumpability, generation allocation,
registry process binding and the distinction between test and kernel evidence.

No additional
2.1-2.8 task is checked complete. This step does not yet implement D11 consumers,
engine cells, full migration/deletion, successful streams or actual old-image
rollback. Startup activation still requires every mandatory probe to pass.


## D18 verification receipt (2026-10-04 local / 2026-10-05 UTC)

Final production-image command exited 0, with no skips:
`python scripts/linux_oracle.py --production-image tinyassets-uid-supervisor:d18 --build`.
The image includes the reviewed BOM/inventory/error-type fixes. Exact launch and
acceptance output:
```
[oracle] production image sha256:4529835547a444f537ede81fd02c83f641fb2f12f22e00cb7039e250722dedd8
[oracle] docker run --rm --network none --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint /opt/venv/bin/python sha256:4529835547a444f537ede81fd02c83f641fb2f12f22e00cb7039e250722dedd8 -I -B /app/scripts/role_image_oracle.py
privileged chain: PASS (root owners, protected ancestors and link targets)
non-root/writable descendant module chain refusal: PASS
identity uid=1001 groups=[] caps=all-zero nnp=1
image accounts, immutable paths, writable HOME, unprivileged bwrap: PASS
overlapping consent migration refused without mutation: PASS
broker private directory ownership/setgid readbacks without FSETID: PASS
forward dry-run, apply, repeat; service remains unadmitted: PASS
identity uid=1002 groups=[1102] caps=all-zero nnp=1
broker actual ConnectionLedger existing/fresh writes and proxy mkdir: PASS
identity uid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
identity uid=1003 groups=[1100] caps=all-zero nnp=1
direct daemon/engine-identity private path denials: PASS (not class acceptance)
identity uid=1001 groups=[] caps=all-zero nnp=1
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
launcher migration-capability retirement/readback and pre-bind refusal: PASS
daemon non-dumpable procfs; same-uid fake broker gets no proof: PASS
launcher exact-pid, malformed/oversized/SCM_RIGHTS/static-operation refusals: PASS
launcher broker uid=1002; socket=1002:1101/0660; daemon fences without disk token: PASS
daemon supervisor acquisition, private-memory channel after restart, stop without signal: PASS
launcher broker crash/restart preserves in-memory owner fence: PASS
privileged chain: PASS (root owners, protected ancestors and link targets)
broker caps=all-zero nnp=1 non-dumpable; no received-fd leak; cross-uid shutdown: PASS
launcher wrong-uid filesystem refusal; actual broker creates private ledger: PASS
LAUNCHER/BROKER SUBSTEP ONLY: real daemon CMD, streams/accounting, engine classes pending
FOUNDATION/EGRESS SUBSTEP ONLY: launcher, IPC, real engine classes, full rollback pending
```

Exact completed commands/results:
```
python -m pytest tests/test_broker_supervisor.py tests/test_broker_process.py tests/test_broker_fence.py tests/test_broker_server.py tests/test_broker_relocated_paths.py -q
20 passed, 27 skipped (Windows; skips are not kernel acceptance)
python scripts/linux_oracle.py -- tests/test_broker_supervisor.py tests/test_broker_process.py tests/test_broker_fence.py -q
24 passed, zero skips
python scripts/linux_oracle.py -- tests/test_broker_supervisor.py tests/test_broker_process.py tests/test_broker_fence.py tests/test_broker_server.py tests/test_broker_relocated_paths.py tests/test_outbound_connection_ledger.py tests/test_outbound_http_connection.py tests/test_outbound_effect_boundary.py tests/test_outbound_proxy_startup_diagnosis.py tests/test_request_budget_broker.py -q
177 passed, zero skips
python scripts/linux_oracle.py -- tests/test_broker_supervisor.py tests/test_broker_process.py tests/test_broker_fence.py tests/test_broker_server.py tests/test_broker_relocated_paths.py tests/test_role_launcher.py tests/test_broker_upstream_stream.py tests/test_broker_scan.py tests/test_platform_secret_scope.py -q
181 passed, 17 skipped (skips are not acceptance)
python -m pytest tests/test_control_plane_inventory.py -q
5 passed (handoff and lead validation)
python scripts/linux_oracle.py -- tests/test_control_plane_inventory.py -q
5 passed, zero skips
python -m ruff check tinyassets/broker/supervisor.py tinyassets/broker/client.py tinyassets/broker/process.py tinyassets/storage/outbound_connections.py tests/test_broker_supervisor.py tests/test_broker_process.py tests/control_plane_timer_inventory.py scripts/role_launcher_oracle.py
All checks passed!
python -m ruff check --output-format concise
55 pre-existing errors outside changed files
python packaging/claude-plugin/build_plugin.py
Import probe: probe-ok
python scripts/check_mirror_parity.py
mirror-parity: all 596 canonical file(s) mirror-matched
openspec validate per-role-uid-split --strict
Change 'per-role-uid-split' is valid
git diff --check
exit 0
```

Broader Windows command (recorded as a failure, not a pass):
```
python -m pytest tests/test_outbound_connection_ledger.py tests/test_outbound_http_connection.py tests/test_outbound_effect_boundary.py tests/test_outbound_proxy_startup_diagnosis.py tests/test_request_budget_broker.py tests/test_platform_secret_scope.py tests/test_role_launcher.py -q
2 failed, 144 passed, 32 skipped, 9 errors
```
All failures/errors are in unchanged test_request_budget_broker.py: nine Unix
socket fixture setups use unavailable os.getuid, and two symlink probes hit
WinError 1314. That entire file passed in the Linux 177-test run above. No test
was removed, skipped anew or weakened to make Windows green. Initial D18 Windows
fake-socket unit coverage also needed a test-only AF_UNIX constant; it now passes.
No affected test file matches .github/heavy-test-files.txt.

Implementation `a34998fd573c46fee8ccc87ab8926a432acac8b7` was committed using
explicit paths and pushed to origin/feat/per-role-uid-split. Pre-commit mirror,
mojibake, import-graph, path-resolver, cross-provider-drift and skill checks passed.
`python scripts/test_hygiene_gate.py --base be34773f5f --head HEAD` returned:
`tests added 4, removed 0, tampering findings 0, product lines added 347`.
Worktree was clean after that push. This receipt is a documentation-only follow-up.

Remaining acceptance is unchanged beyond D18: real daemon launcher/CMD and
allowlisted environment; every actual engine class/site and daemon reader matrix;
D11 ledger/accounting/refresh/deletion/backup IPC consumers; complete role migration
and runtime mode declarations; D10 two-pass deletion and actual old-image rollback;
successful broker streaming; compose/ta-op capability parity and healthcheck;
startup activation only after all probes pass. The relocation-only reverse probe
is not full role rollback. No deployment, PR, added privilege or isolation-scope
change is authorized or performed by this step.

---

# Current delivery: D16 staged launcher and broker lifecycle

Started from `d43935b600`; the required fast-forward pull was already current.
OpenSpec admission is ALLOWED. D12-D15 and the relocation substep remain intact.
D16 records mechanical IPC framing, socket group assignment after capability
retirement, durable generation allocation and explicit logical data-root routing.

Implemented but not startup-activated: stdlib launcher kernel with exact daemon
uid/pid authentication, migration capability retirement/readbacks, protected
chain verification, fixed broker argv/environment, descriptor closure, readiness,
crash restart and ordered cross-uid shutdown. The new role-split broker mints
its fence generation, checks identity/capabilities, becomes non-dumpable after
exec and uses the relocated ledger with an explicit logical command-center root.
No engine kind is admitted without a cell. Normal script startup refuses.

Release-critical paths under pr-scope-guard SENSITIVE_RE (3, cap 8): `Dockerfile`,
`deploy/role_launcher.py`, `deploy/role_egress_migration.py`. Additionally reviewed
security-sensitive files: `scripts/check_privileged_chain.py`, the two role oracle
scripts, `tinyassets/broker/fence.py`, `tinyassets/broker/process.py`, and
`tinyassets/storage/outbound_connections.py`. Generated plugin copies accompany
the three canonical runtime files.

D17 follows an actual production-image failure: chmod without target-group
membership silently cleared the setgid bit. The migration now uses its existing
SETGID authority temporarily around fchmod and asserts uid/gid/mode readbacks;
the oracle uses that helper for IPC setup and asserts private parent mode 2700.
No CAP_FSETID or new retained privilege is added.

No task 2.1-2.8 is newly checked complete. Remaining: real daemon spawn/environment
and CMD integration; every actual engine class/cell and daemon reader matrix;
all D11 authenticated ledger/accounting/refresh/deletion/backup consumers;
in-memory supervisor replacement and removal of the legacy token path;
full role migration and D10 deletion/reverse migration; actual broker streams,
old-image rollback, ta-op/compose capability parity and healthcheck acceptance.
Startup activation awaits all required proofs. No PR or deployment.

One cross-family review via peer-agents returned ADAPT. **AGREE**: a deeply
nested JSON packet can raise RecursionError on Python 3.11 and escape the
malformed-request refusal. Catch it explicitly and add the 2200-byte nesting
fixture to the production oracle. Reviewer agreed with capability retirement,
exact-pid peer binding, descriptor refusal, socket modes, lifecycle, durable
generation, logical-root separation and continued startup refusal. Readiness
was subsequently strengthened from fresh socket metadata to an actual connection
whose SO_PEERCRED must match the spawned broker pid/uid/gid. No second round.

Oracle diagnostic correction: rejecting a wrong-pid peer before reading its
request may produce AF_UNIX connection reset instead of a delivered REFUSED
packet. The probe accepts only that reset/broken-pipe or explicit REFUSED as
denial; timeout and successful replies still fail. This does not change the peer
gate, read any unauthenticated request or weaken an existing test.

## D16-D17 verification receipt (2026-10-04 local / 2026-10-05 UTC)

Production Dockerfile image:
`sha256:badccc57b43762c61b1750a7fc8be7b98d24902a4c3d5257457f584c04a7950d`.
Final command `python scripts/linux_oracle.py --production-image tinyassets-uid-launcher:d16 --build`
exited 0. Earlier failing iterations found the setgid mode, wrong-pid denial
transport interpretation, procfs directory inference and readiness/reaper race
described here; none is counted as a pass.

Executed container command (no host mounts/network; the planned seven entry
capabilities and compose security options):
```
docker run --rm --network none --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint /opt/venv/bin/python sha256:badccc57b43762c61b1750a7fc8be7b98d24902a4c3d5257457f584c04a7950d -I -B /app/scripts/role_image_oracle.py
```
Output (the full acceptance statements; no skips):
```
privileged chain: PASS (root owners, protected ancestors and link targets)
non-root/writable descendant module chain refusal: PASS
identity uid=1001 groups=[] caps=all-zero nnp=1
image accounts, immutable paths, writable HOME, unprivileged bwrap: PASS
overlapping consent migration refused without mutation: PASS
broker private directory ownership/setgid readbacks without FSETID: PASS
forward dry-run, apply, repeat; service remains unadmitted: PASS
identity uid=1002 groups=[1102] caps=all-zero nnp=1
broker actual ConnectionLedger existing/fresh writes and proxy mkdir: PASS
identity uid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
identity uid=1003 groups=[1100] caps=all-zero nnp=1
direct daemon/engine-identity private path denials: PASS (not class acceptance)
identity uid=1001 groups=[] caps=all-zero nnp=1
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
launcher migration-capability retirement/readback and pre-bind refusal: PASS
launcher exact-pid, malformed/oversized/SCM_RIGHTS/static-operation refusals: PASS
launcher broker uid=1002; socket=1002:1101/0660; daemon fences without disk token: PASS
launcher broker crash/restart preserves in-memory owner fence: PASS
privileged chain: PASS (root owners, protected ancestors and link targets)
broker caps=all-zero nnp=1 non-dumpable; no received-fd leak; cross-uid shutdown: PASS
launcher wrong-uid filesystem refusal; actual broker creates private ledger: PASS
LAUNCHER/BROKER SUBSTEP ONLY: real daemon CMD, streams/accounting, engine classes pending
FOUNDATION/EGRESS SUBSTEP ONLY: launcher, IPC, real engine classes, full rollback pending
```

The relocation dry-runs preserve content/metadata; forward/reverse repeat is a
no-op and six abrupt-exit boundaries resume. This remains **egress rollback**,
not full role rollback or actual old-image startup. D10 deletion is not yet
implemented or proven. The trusted oracle daemon fixture is explicit; it does
not replace the actual engine-class or real daemon acceptance requirements.

Other exact verification commands/results:
```
python -m pytest tests/test_role_launcher.py tests/test_broker_fence.py tests/test_broker_relocated_paths.py -q
17 passed
python -m pytest tests/test_role_launcher.py tests/test_broker_fence.py tests/test_broker_relocated_paths.py tests/test_broker_server.py tests/test_broker_process.py tests/test_dockerfile_shape.py tests/test_linux_oracle.py -q
75 passed, 27 skipped (Windows; not kernel acceptance)
python -m pytest tests/test_outbound_connection_ledger.py tests/test_broker_supervisor.py tests/test_platform_secret_scope.py -q
48 passed, 32 skipped (Windows)
python scripts/linux_oracle.py -- tests/test_role_launcher.py tests/test_broker_fence.py tests/test_broker_relocated_paths.py tests/test_broker_server.py tests/test_broker_process.py tests/test_broker_supervisor.py tests/test_outbound_connection_ledger.py tests/test_privileged_chain.py -q
75 passed, zero skips
python scripts/linux_oracle.py -- tests/test_outbound_http_connection.py tests/test_outbound_effect_boundary.py tests/test_outbound_proxy_startup_diagnosis.py tests/test_broker_scan.py tests/test_broker_upstream_stream.py tests/test_request_budget_broker.py tests/test_platform_secret_scope.py tests/test_dockerfile_shape.py tests/test_linux_oracle.py -q
295 passed, 17 skipped (skips are not acceptance)
python -m ruff check deploy/role_egress_migration.py deploy/role_launcher.py scripts/role_launcher_oracle.py scripts/role_image_oracle.py scripts/check_privileged_chain.py tinyassets/broker/fence.py tinyassets/broker/process.py tinyassets/storage/outbound_connections.py tests/test_role_launcher.py tests/test_broker_fence.py tests/test_broker_relocated_paths.py
All checks passed!
python -m ruff check --output-format concise
55 pre-existing errors, all outside changed files
python packaging/claude-plugin/build_plugin.py
Import probe: probe-ok
python scripts/check_mirror_parity.py
mirror-parity: all 596 canonical file(s) mirror-matched
openspec validate per-role-uid-split --strict
Change 'per-role-uid-split' is valid
git diff --check
exit 0
```
No affected test file matches .github/heavy-test-files.txt. Existing test names
and assertions are retained.

Implementation `6fe67a30a31a9168d77219dc1052cc7dcae9594b` was committed with explicit
paths and pushed to origin/feat/per-role-uid-split. Pre-commit mirror, mojibake,
import-graph, path-resolver, cross-provider-drift and skill checks passed.
`python scripts/test_hygiene_gate.py --base d43935b600 --head HEAD` printed:
`tests added 7, removed 0, tampering findings 0, product lines added 742`.
Worktree was clean after the implementation push; this receipt is a docs-only
follow-up. No PR, deployment, startup activation or additional completed task
is claimed. Continue from the remaining list at the top of this document.

The crash probe exposed a readiness/reaper race: a START_BROKER arriving during
broker exit could reap the child inside readiness, hiding the restart event
from the lifecycle loop. Readiness now uses waitid(WNOWAIT); only poll reaps and
counts a restart. The same crash/restart assertion remains in the oracle.
The non-dumpability probe checks the protected environ inode and attempts a
same-uid read; /proc/pid directory ownership alone was an incorrect inference.

# Previous delivery: D12 relocation and implementation

Starting HEAD `5ecf0ec8fb`; fast-forward pull was already current; clean worktree.
OpenSpec admission: ALLOWED. D12 accepts the lead relocation decision. D13 uses
2700 private directories because daemon group 1101 must not grant private access.
D14 stages the immutable image foundation without activating an unfinished root
launcher. Mechanical choices will be recorded and implemented autonomously.

Foundation release-critical paths: `Dockerfile`, `deploy/compose.yml`,
`deploy/broker_main.py`, `deploy/role_egress_migration.py`,
`.github/workflows/docker-build.yml` (5 under the gate;
`scripts/check_privileged_chain.py` is additionally security-sensitive).
No deployment or PR. Full task acceptance remains pending.

## D15 implementation and review disposition

Relocation is implemented as `deploy/role_egress_migration.py`, installed immutable
as `/usr/local/libexec/ta-egress-migration.py`, but not activated at startup.
It requires an initialized layout-2 marker with the consent migration done,
uses the existing exclusive layout lock, checkpoints crash-left WAL before
renames, fsyncs files and directories, and uses renameat2(RENAME_NOREPLACE).
It refuses symlinks, hardlinks, FIFOs, mount crossings and conflicting copies.
Forward/reverse progress is durable and resumable; dry-run never opens SQLite.
The top-level layout deliberately remains migrating after this SUBSTEP; only
the complete role migration may admit service. This is D15, recorded in design.md.

One cross-family implementation review via peer-agents returned ADAPT.
- **AGREE**: overlapping consent migration could clear the top-level fence.
  Fixed with pre-mutation layout/consent validation; the production probe now
  refuses both absent and interrupted consent state without mutation, and
  calls actual storage_layout.check after relocation to prove admission refuses.
- **AGREE**: the chain gate skipped broker site-packages under -S and only
  checked directories. Fixed by explicitly enumerating the venv site paths
  without evaluating .pth code, recursively checking modules and symlink
  targets, and probing a non-root-owned and a writable descendant module.
No second review round. Reviewer confirmed no-overwrite, link refusal,
checkpoint ordering, crash-resume and exclusive-lock mechanisms.

No tinyassets/ source changed, so plugin mirror regeneration is not applicable.
Tasks 2.1 and 2.3 have foundation work, 2.4 has the egress substep, and 2.8 has
production-image harness support and substep probes. None of 2.1-2.8 is checked
complete: launcher/CMD, full role migration, D10 deletion, vault mode consumers,
all D11 IPC/path/backup/accounting/refresh consumers, capability parity activation,
actual engine classes, broker stream and old-image rollback remain outstanding.
This branch must not be deployed or treated as the completed role split.

## Foundation verification (2026-10-04)

Production image built from the edited Dockerfile:
`sha256:1ca1b9e6dfa4890396bb1e98bee628b9f1193ac49a4a6b98cc4aead58441900c`.

Commands:
```
python scripts/linux_oracle.py --production-image tinyassets-uid-foundation:d14 --build
python scripts/linux_oracle.py --production-image tinyassets-uid-foundation:d14
```
The direct second command exited 0. It prints the complete docker argv:
```
docker run --rm --network none --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint /opt/venv/bin/python sha256:1ca1b9e6dfa4890396bb1e98bee628b9f1193ac49a4a6b98cc4aead58441900c -I -B /app/scripts/role_image_oracle.py
```
Output:
```
privileged chain: PASS (root owners, protected ancestors and link targets)
non-root/writable descendant module chain refusal: PASS
identity uid=1001 groups=[] caps=all-zero nnp=1
image accounts, immutable paths, writable HOME, unprivileged bwrap: PASS
overlapping consent migration refused without mutation: PASS
forward dry-run, apply, repeat; service remains unadmitted: PASS
identity uid=1002 groups=[1102] caps=all-zero nnp=1
broker actual ConnectionLedger existing/fresh writes and proxy mkdir: PASS
identity uid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
identity uid=1003 groups=[1100] caps=all-zero nnp=1
direct daemon/engine-identity private path denials: PASS (not class acceptance)
identity uid=1001 groups=[] caps=all-zero nnp=1
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
FOUNDATION/EGRESS SUBSTEP ONLY: launcher, IPC, real engine classes, full rollback pending
```

`python -m pytest tests/test_dockerfile_shape.py tests/test_docker_entrypoint.py tests/test_linux_oracle.py tests/test_no_platform_llm_credentials.py tests/test_no_platform_github_push_credential.py -q`: 177 passed before the two added runner regressions; `python -m pytest tests/test_linux_oracle.py -q`: 23 passed after them.
`python scripts/linux_oracle.py -- tests/test_privileged_chain.py tests/test_dockerfile_shape.py tests/test_docker_entrypoint.py tests/test_linux_oracle.py -q`: 65 passed.
After review fixes, `python scripts/linux_oracle.py -- tests/test_privileged_chain.py tests/test_linux_oracle.py -q`: 28 passed, zero skips.
Changed-file ruff, strict OpenSpec validation and git diff --check pass.
Full `python -m ruff check` still reports the same 55 pre-existing errors.
No affected file is on the heavy-test list.

The simulated-admission Docker fixture starts Uvicorn successfully with immutable
/app. Baseline and edited images both return exit 78 for unadmitted startup
(checked via subprocess.returncode; PowerShell's tool result normalized it to 1).
The synthetic fixture lacks a release receipt, so ta-op pulse correctly refuses
its absent git_sha; this is not claimed as healthcheck acceptance. No real-user
app pass, full deletion, actual old-image rollback or launcher stream is proven.

## Commit receipt

Implementation pushed: `02d5542a78` on `origin/feat/per-role-uid-split`.
`python scripts/test_hygiene_gate.py --base 5ecf0ec8fb --head HEAD`:
`tests added 7, removed 0, tampering findings 0, product lines added 718`.
Explicit paths staged; worktree clean after push. No PR or deployment.

## Historical delivery records (blockers below superseded by D12)

# Current delivery: D11 broker ownership and ledger-parent clarification

Starting HEAD `21096788fb04a3587b900ed1883ed5183d92be20`; the requested first
command `git pull --ff-only origin feat/per-role-uid-split` returned `Already
up to date.` Worktree was clean. OpenSpec admission returned `ALLOWED`.

Recorded the lead decision in design.md D11 and reconciled proposal, role spec,
tasks and rollback requirements. Broker uid 1002 owns outbound ledger/proxy
persistence with group ta-brk; daemon ledger/accounting/refresh access must use
authenticated broker IPC. D10 is unchanged. The ownership decision is accepted;
the old question about giving the broker daemon-store access is superseded.

The new [broker-access-inventory.md](broker-access-inventory.md) enumerates 41
ledger constructor sites and 25 methods opening SQL connections, plus raw SQL,
injected clients, proxy writes, account deletion and backup access. Each has an
explicit implementation disposition. **No route has yet been implemented.**
The trace also establishes that current inference accounting lives in
`.tinyassets.db`, not outbound.db, and local OAuth refresh takes vault-write
admission before spending. Those dependencies cannot be overlooked when routing.

## Pending decision: physical ledger parent

The ownership-only interpretation of the instruction cannot satisfy the broker
write/create probe while retaining D4's exact `/data = 1001:1001/0755` row.
SQLite needs directory write authority for its journal lifecycle. Even a
1002:1101/0600 outbound.db that the broker opens O_RDWR fails an actual
`ConnectionLedger.create_connection`; a fresh ledger cannot be created at all.
Startup precreation of the database is insufficient. The proxy subtree works
when startup creates it as broker-owned 2700.

A user clarification is pending: **may startup relocate the ledger to
`/data/.broker/state/outbound.db`, with reverse migration restoring the original
path?** That is the recommended resolution, preserving private-parent authority.
The alternative needs explicit parent-directory authority. Relocation must also
amend backup's top-level glob, generic account deletion and code that derives the
command-center/accounting root from the ledger parent. No relocation, root ACL,
journal-mode weakening, symlink workaround or retained capability has been applied.

The retained brief explicitly requires: **"If you hit a genuine design ambiguity,
record it in delivery.md and stop rather than guess."** The OpenSpec apply skill
also says **"Pause and ask (don't guess) on unclear tasks, design issues revealed
mid-implementation, or blockers."** Source: [.agents/skills/openspec/SKILL.md](../../../.agents/skills/openspec/SKILL.md).
This is the remaining path-layout decision, not another design review or a
reopening of D10. Runtime work is paused pending that answer.

## Production-image diagnostic (not task 2.8 acceptance)

Used the previously built production Dockerfile image, confirmed by:

```powershell
docker image inspect tinyassets-uid-baseline:664a4361e7 --format '{{.Id}}'
git diff 664a4361e7 HEAD -- Dockerfile tinyassets deploy
```

Image ID `sha256:7d30057f0d2f6a6259b44ee7164831d2c1919697c2d9cae55512051909e585d4`;
runtime/image/deploy diff is empty. This reuses an unchanged production image;
it is not a newly built launcher image. Synthetic files only, disposable
network-disabled container, no host mounts, compose security options and the
specified seven entry capabilities. Children read back all five capability
sets as zero. No refresh/network request was made.

Exact command (script is reproduced below for durable replay):

```powershell
Get-Content -Raw C:/Users/Jonathan/AppData/Local/Temp/uid-broker-parent-probe.py | docker run --rm -i --network none --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint /opt/venv/bin/python tinyassets-uid-baseline:664a4361e7 -
```

Exit 0: the diagnostic asserts the failures below, not that production works.
The private-parent control uses `.broker/outbound.db` in a synthetic fixture;
it demonstrates directory authority, not the proposed final state path/migration.

```text
uid=1002 groups=[1102] caps=all-zero nnp=1
broker existing database file open=PASS
broker actual ConnectionLedger write=FAIL: attempt to write a readonly database
broker fresh root database create=FAIL: unable to open database file
broker precreated proxy directory child create=PASS
diagnostic private-parent control actual ledger create/write=PASS
uid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
direct access denied uid=1001 path=outbound.db=PASS
direct access denied uid=1001 path=.outbound-proxy=PASS
direct access denied uid=1001 path=.broker/outbound.db=PASS
uid=1003 groups=[1100] caps=all-zero nnp=1
direct access denied uid=1003 path=outbound.db=PASS
direct access denied uid=1003 path=.outbound-proxy=PASS
direct access denied uid=1003 path=.broker/outbound.db=PASS
DIAGNOSTIC COMPLETE; not launcher, IPC, engine-class or migration acceptance
```

Diagnostic source:

```python
"""Diagnostic only: D4 parent permissions after broker ownership transfer."""
import ctypes
import os
import sqlite3
import tempfile
import traceback
from pathlib import Path

from tinyassets.storage.outbound_connections import ConnectionLedger

libc = ctypes.CDLL(None, use_errno=True)
CAP_FIELDS = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')


class Header(ctypes.Structure):
    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]


class Data(ctypes.Structure):
    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32),
                ('inheritable', ctypes.c_uint32)]


def retire(uid, groups):
    assert libc.prctl(38, 1, 0, 0, 0) == 0
    for cap in range(int(Path('/proc/sys/kernel/cap_last_cap').read_text()) + 1):
        assert libc.prctl(24, cap, 0, 0, 0) == 0
    assert libc.prctl(47, 4, 0, 0, 0) == 0
    os.setgroups(groups)
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    header, data = Header(0x20080522, 0), (Data * 2)()
    assert libc.capset(ctypes.byref(header), ctypes.byref(data)) == 0
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    assert all(int(fields[k].strip(), 16) == 0 for k in CAP_FIELDS)
    assert os.getresuid() == (uid, uid, uid)
    assert os.getresgid() == (uid, uid, uid)
    assert os.getgroups() == groups
    print(f'uid={uid} groups={groups} caps=all-zero nnp=1', flush=True)


def child(uid, groups, fn):
    pid = os.fork()
    if pid == 0:
        try:
            retire(uid, groups)
            fn()
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    assert os.waitpid(pid, 0)[1] == 0


root = Path(tempfile.mkdtemp(prefix='broker-parent-'))
os.chown(root, 1001, 1001)
root.chmod(0o755)
existing = root / 'outbound.db'
ConnectionLedger(existing)
os.chown(existing, 1002, 1101)
existing.chmod(0o600)
proxy = root / '.outbound-proxy'
proxy.mkdir()
os.chown(proxy, 1002, 1101)
proxy.chmod(0o2700)
private = root / '.broker'
private.mkdir()
os.chown(private, 1002, 1101)
private.chmod(0o2700)


def insert(path):
    ledger = ConnectionLedger(path)
    ledger.create_connection(connection_id='synthetic', owner_user_id='alice',
                             connection_class='http', connection_type='http',
                             auth_scheme='bearer', scopes=('POST',), provider='http',
                             destination='compute:synthetic', credential_ref='vault://http/synthetic',
                             allowed_endpoints=[{'host': 'models.example.com',
                                                 'path_template': '/v1/chat', 'methods': ['POST']}])


def broker():
    with existing.open('r+b'):
        print('broker existing database file open=PASS', flush=True)
    try:
        insert(existing)
    except sqlite3.OperationalError as exc:
        assert 'readonly' in str(exc), str(exc)
        print('broker actual ConnectionLedger write=FAIL: ' + str(exc), flush=True)
    else:
        raise AssertionError('unexpected write through unwritable journal parent')
    try:
        ConnectionLedger(root / 'new-outbound.db')
    except sqlite3.OperationalError as exc:
        assert 'unable to open database file' in str(exc), str(exc)
        print('broker fresh root database create=FAIL: ' + str(exc), flush=True)
    else:
        raise AssertionError('unexpected create in daemon-owned parent')
    (proxy / 'synthetic-grant').mkdir()
    print('broker precreated proxy directory child create=PASS', flush=True)
    insert(private / 'outbound.db')
    print('diagnostic private-parent control actual ledger create/write=PASS', flush=True)


child(1002, [1102], broker)


def denied():
    for path in (existing, proxy, private / 'outbound.db'):
        try:
            fd = os.open(path, os.O_RDONLY)
        except PermissionError:
            print(f'direct access denied uid={os.getuid()} path={path.relative_to(root)}=PASS', flush=True)
        else:
            os.close(fd)
            raise AssertionError(f'unexpected access: {path}')


child(1001, [1100, 1101, 1102], denied)
child(1003, [1100], denied)
print('DIAGNOSTIC COMPLETE; not launcher, IPC, engine-class or migration acceptance', flush=True)
```

## Verification and remaining work

- `openspec validate per-role-uid-split --strict`: exit 0, change is valid.
- `git diff --check`: exit 0.
- `python -m pytest tests/test_ta_op_modes.py -q --basetemp=C:/Users/Jonathan/AppData/Local/Temp/uid-broker-parent-pytest`: exit 0, `10 passed in 0.33s`.
- `python -m ruff check --output-format concise`: exit 1, `Found 55 errors.`
  All are in unchanged files; no unrelated lint changes made.
- `python scripts/linux_oracle.py -- tests/test_ta_op_modes.py -q`: exit 0,
  `[oracle] python 3.11.16 | git 2.47.3 | bwrap 0.12.0 | uid 1001`,
  `10 passed in 0.13s`. This is the existing capability baseline, not task 2.8
  production-launcher acceptance.
- `python scripts/test_hygiene_gate.py --base 21096788fb --head HEAD`: exit 0,
  `tests added 0, removed 0, tampering findings 0, product lines added 0`.

Release-critical files in this documentation step: **0; list: none**. Changed
paths are seven Markdown artifacts under this change: design.md, proposal.md,
tasks.md, specs/runtime-process-roles/spec.md, rollback.md, delivery.md and the
new broker-access-inventory.md. No runtime, gate or test files changed. No plugin
mirror regeneration applies. No fourth design review, PR or deployment.

Tasks completed: decision/inventory documentation only; **no new task checkbox**.
Tasks 2.1-2.8 remain incomplete. Actual per-class oracle probes, broker IPC
accounting, launcher stream and healthcheck, migration dry-run/apply/repeat/
interrupted-resume, rollback and deletion are **NOT RUN / NOT IMPLEMENTED**.
Do not substitute this uid-only diagnostic for any required class probe.
Continue the ordered build once the physical ledger-parent decision is resolved.

---

# Current delivery: least-privilege D10 amendment

Starting HEAD `664a4361e7`; `git pull --ff-only origin feat/per-role-uid-split`
returned `Already up to date.` Worktree was clean. Read both briefs and the full
round-3 refute, all change artifacts and PLAN operating principles. Admission
`python scripts/openspec_flow.py check-change per-role-uid-split --provider codex`
returned `ALLOWED`.

The lead resolves the prior capability ambiguity: no runtime root maintenance,
no retained DAC_OVERRIDE/FOWNER/CHOWN and no separate privileged helper. Deletion
uses capability-free engine 1003 inside the admitted owner's cell through normal
launcher spawn, then daemon 1001 for daemon entries/empty structure. Both passes
fail loudly with the path. Account deletion and scoped_reset are covered.
Reverse migration is explicit opt-in at container startup in the forward
migration code path before capability drop, dry-run capable, idempotent and
never deletes data. Group-preserving creation modes remain required.

D10, proposal, runtime-role delta, tasks and rollback runbook now agree. No probe
has been weakened; no build task is checked off. Release-critical paths changed
in this documentation step: **0; list: none**. All six changed paths are under
this change: design.md, proposal.md, tasks.md, specs/runtime-process-roles/spec.md,
rollback.md and delivery.md. No tinyassets/ edits, so mirror regeneration is not
applicable. No PR, deployment, test-name change or assertion weakening.

## Build continuation and new stop: broker filesystem authority

Task 2.1 prerequisite inspection covered the Dockerfile, daemon entry/health
paths, provider jail, node sandbox, runtime path resolution and compose image
consumers. No runtime edit was made before the blocker below was established.
The audit is not complete and task 2.1 remains unchecked. Observations to retain:

- Source-relative uses inspected in mcp_server, discovery and storage.rotation
  read packaged data; mcp_server writes default universe state under data_dir().
- Provider homes are per-launch; moving daemon HOME removes the wrapper's /app
  default from the daemon path. Catalog's implicit repo_root is cwd, requiring
  the remaining audit to check explicit sqlite_cached configurations.
- The optional slack-agent service uses the same image but overrides entrypoint.
  A root image USER would otherwise make that service root too. Preserve its
  uid-1001 execution explicitly when implementing the image/compose changes;
  this is a required compatibility adaptation, not a deployed change.

**Separate authority conflict, reproduced using the production Dockerfile.**
D1 gives the broker uid/gid 1002 and supplementary group **1102 only**, with no
capabilities. D4 assigns shared root stores **1001:1001**, strips other access,
and leaves remaining platform state with the daemon. These rows exclude the
broker from outbound.db; the design supplies no broker ACL or mediated ledger
channel. Yet the mandatory working-stream path requires these operations:

| Actual code | Required access under current implementation | Evidence |
|---|---|---|
| broker/process.py `_Dispatchers.ledger_for` (59-65), server.py `_open` (361-366) | open shared outbound.db to authorize every stream | production-image diagnostic below: unable to open database file |
| storage/outbound_connections.py `ConnectionLedger.__init__` (5148-5206) | schema initialization/upgrade and incarnation backfill in addition to reads | code inspection: executescript, ALTER TABLE and conditional UPDATE; a read ACL alone is not a complete contract |
| storage/outbound_connections.py `_build_credential_broker_dispatch` (4928-4955), `broker_dispatch_config` (6071-6089) | mkdir and write under /data/.outbound-proxy/<grant-hash>, plus open the ledger | production-image diagnostic: EACCES at runtime mkdir |
| storage/agent_request_usage.py `resolve_inference_usage`, `UsageStore`, `claim_reference` and usage dispatch (141-197, 401-425, 465+) | write accounting transactions in shared .tinyassets.db; inspect parent/owner liveness locks | source inspection only; not claimed as a runtime probe |
| process_liveness.py `owner_state` (88-109) | open .consumer_liveness/<token>.lock O_RDWR for kernel liveness check | source inspection only; inaccessible means UNKNOWN, not successful inference admission |
| connection_oauth/tokens.py `ConnectionTokens.current` (274-337) | refresh may enter local refresh_credential and its vault-write admission | source inspection only: remote service path requires provider_id plus supplied/inherited config; D3 broker allowlist supplies neither general refresh IPC nor write authority |

The last path also conflicts with the explicit owner-only vault-writer contract:
broker process `_Dispatchers.dispatch_for` does not supply oauth_service in its
config, and non-directory OAuth bundles take the local refresh path regardless.
No refresh network request was attempted. The code's lock-before-spend protection
must remain intact; granting vault write to make refresh work would contradict D4.

**Decision needed:** define the broker's data/operation authority as a whole:
which ledger/accounting operations use authenticated daemon IPC versus explicit
broker-specific storage rights, where broker audit/runtime writes live, and how
refresh remains daemon-owned. A narrowly read-only ledger consumer would also
need to stop schema/backfill writes in broker opens. Giving the broker daemon
group 1001, restoring other-read, widening ta-work, granting vault write, or
retaining capabilities would not be a faithful implementation of the stated
role/mode tables. No one of these choices is inferred.

This is a concrete build compatibility/authority issue, not a fourth design
review and not a reopening of D10. The retained brief says: **"If you hit a
genuine design ambiguity, record it in delivery.md and stop rather than guess."**
Accordingly stopped before runtime implementation. D10 remains resolved.

## Production-image diagnostic: exact command and output

Built the unchanged production Dockerfile from starting runtime HEAD 664a4361e7
(the working-tree changes were Markdown only):

```powershell
docker build -f Dockerfile -t tinyassets-uid-baseline:664a4361e7 .
docker image inspect tinyassets-uid-baseline:664a4361e7 --format '{{.Id}}'
```

Build exit 0. Image ID:
`sha256:7d30057f0d2f6a6259b44ee7164831d2c1919697c2d9cae55512051909e585d4`.
Platform manifest:
`sha256:8ae8092e8558708ebc32bb86eac86e4c91d4f67141e88ab78049c26b287922e3`.

The following script was saved outside the repo at
`C:/Users/Jonathan/AppData/Local/Temp/uid-broker-d4-probe.py`. It creates only
synthetic data inside a disposable network-disabled container with no host
mounts, applies D1/D4 identities/modes, and calls the actual production broker
methods. It is a diagnostic, **not** task 2.8 launcher/stream acceptance: no new
launcher or migration exists yet. All five child capability sets are read back
zero; all compose security options and the proposed seven entry caps are used.

```python
import ctypes
import os
import sqlite3
import tempfile
import traceback
from pathlib import Path

from tinyassets.broker.process import _Dispatchers
from tinyassets.storage.outbound_connections import ConnectionLedger, _build_credential_broker_dispatch

CAP_FIELDS = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
libc = ctypes.CDLL(None, use_errno=True)

class Header(ctypes.Structure):
    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]

class Data(ctypes.Structure):
    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]

def retire(uid, groups):
    assert libc.prctl(38, 1, 0, 0, 0) == 0
    for cap in range(int(Path('/proc/sys/kernel/cap_last_cap').read_text()) + 1):
        assert libc.prctl(24, cap, 0, 0, 0) == 0
    assert libc.prctl(47, 4, 0, 0, 0) == 0
    os.setgroups(groups)
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    header, data = Header(0x20080522, 0), (Data * 2)()
    assert libc.capset(ctypes.byref(header), ctypes.byref(data)) == 0
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    assert all(int(fields[k].strip(), 16) == 0 for k in CAP_FIELDS)
    assert os.getresuid() == (uid, uid, uid)
    assert os.getresgid() == (uid, uid, uid)
    assert os.getgroups() == groups
    print(f'identity uid={uid} gid={uid} groups={groups} caps=all-zero nnp=1', flush=True)

def child(uid, groups, action):
    pid = os.fork()
    if pid == 0:
        try:
            retire(uid, groups)
            action()
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    assert os.waitpid(pid, 0)[1] == 0

root = Path(tempfile.mkdtemp(prefix='uid-broker-d4-'))
os.chown(root, 1001, 1001)
root.chmod(0o755)

def seed():
    ledger = ConnectionLedger(root / 'outbound.db', verify_authenticated_principal=lambda: 'alice')
    ledger.create_connection(connection_id='conn-a', owner_user_id='alice', connection_class='http',
        connection_type='http', auth_scheme='bearer', scopes=('POST',), provider='http',
        destination='compute:conn-a', credential_ref='vault://http/synthetic',
        allowed_endpoints=[{'host':'models.example.com', 'path_template':'/v1/chat', 'methods':['POST']}])
    ledger.grant_connection(grant_id='grant-a', connection_id='conn-a', owner_user_id='alice', universe_id='cc-alice')
    (root / 'outbound.db').chmod(0o640)
    (root / '.outbound-proxy').mkdir(mode=0o700)
    print('seed outbound.db=1001:1001:0640 .outbound-proxy=1001:1001:0700', flush=True)

child(1001, [1100, 1101, 1102], seed)

def control():
    ledger = _Dispatchers(root, allow_test_fixtures=False).ledger_for('alice')
    grant, resource = ledger.authorize_exact(universe_id='cc-alice', grant_id='grant-a', connection_id='conn-a')
    assert grant.connection_id == resource.connection_id == 'conn-a'
    print('daemon.actual_ledger_authorize=PASS', flush=True)

child(1001, [1100, 1101, 1102], control)

def broker():
    try:
        _Dispatchers(root, allow_test_fixtures=False).ledger_for('alice')
    except sqlite3.OperationalError as exc:
        assert str(exc) == 'unable to open database file', str(exc)
        print('broker.actual_ledger_for=OperationalError: ' + str(exc), flush=True)
    else:
        raise AssertionError('D4 unexpectedly admitted broker to the ledger')
    try:
        _build_credential_broker_dispatch({'runtime_root':str(root / '.outbound-proxy' / 'synthetic-grant')})
    except PermissionError as exc:
        assert '.outbound-proxy' in str(exc.filename)
        print('broker.actual_dispatch_build=EACCES: .outbound-proxy/synthetic-grant', flush=True)
    else:
        raise AssertionError('D4 unexpectedly admitted broker writes to owner runtime')

child(1002, [1102], broker)
print('DIAGNOSTIC PASS: D1/D4 permissions block actual broker ledger and runtime setup; NOT stream acceptance', flush=True)

```

```powershell
Get-Content -Raw C:/Users/Jonathan/AppData/Local/Temp/uid-broker-d4-probe.py | docker run --rm -i --network none --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint /opt/venv/bin/python tinyassets-uid-baseline:664a4361e7 -
```

Exit 0, diagnostic assertions passed:

```text
identity uid=1001 gid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
seed outbound.db=1001:1001:0640 .outbound-proxy=1001:1001:0700
identity uid=1001 gid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
daemon.actual_ledger_authorize=PASS
identity uid=1002 gid=1002 groups=[1102] caps=all-zero nnp=1
broker.actual_ledger_for=OperationalError: unable to open database file
broker.actual_dispatch_build=EACCES: .outbound-proxy/synthetic-grant
DIAGNOSTIC PASS: D1/D4 permissions block actual broker ledger and runtime setup; NOT stream acceptance
```

## Verification

```text
python scripts/linux_oracle.py -- tests/test_ta_op_modes.py -q
[oracle] python 3.11.16 | git 2.47.3 | bwrap 0.12.0 | uid 1001
..........                                                               [100%]
10 passed in 0.26s
```

Exit 0; this is the existing test-oracle baseline capability/healthcheck contract,
not proof of the unimplemented launcher's capability retirement.

- Windows: `python -m pytest tests/test_ta_op_modes.py -q --basetemp=C:/Users/Jonathan/AppData/Local/Temp/uid-d10-least-privilege-pytest`:
  exit 0, `10 passed in 0.52s`.
- `openspec validate per-role-uid-split --strict`: exit 0,
  `Change 'per-role-uid-split' is valid`.
- `python -m ruff check --output-format concise`: exit 1, `Found 55 errors.`
  All in unchanged files (same baseline count as the preceding checkpoint).
  No Python or heavy-test file was edited; no unrelated fixes.
- `git diff --check`: exit 0.
- `python scripts/test_hygiene_gate.py --base 664a4361e7 --head HEAD`:
  exit 0, `tests added 0, removed 0, tampering findings 0, product lines added 0`.
- Commit hooks: mirror parity N/A, mojibake clean (6 text files),
  cross-provider drift clean and skill validation passed. Explicit paths staged;
  no commit -a. Worktree clean after the checkpoint. Push target remains
  origin/feat/per-role-uid-split; no PR.

**Remaining:** all build tasks 2.1-2.8; all actual-class production-image
F1-F7/C1-C6 probes; capability-free two-pass deletion/reset and failure-path
proofs; startup migration dry-run/copy/idempotence/crash-resume; startup reverse
migration followed by old-image read/write/delete; broker launcher/stream and
new-image healthcheck. All are **NOT RUN/NOT PROVEN**, distinct from the diagnostic
above. Tasks 2.9 and 2.10 remain unchecked. No deployment was authorized.
Deviation: the brief's stop condition was applied at the broker authority
conflict. No fourth design review or build-code review occurred: no runtime
implementation exists in this checkpoint.

The entries below are historical evidence only; their unresolved-decision
language and runtime root-maintenance proposal are superseded by amended D10.

---

# Current delivery: lead technical decision D10

Started with `git pull --ff-only origin feat/per-role-uid-split`:
`Already up to date.` Starting HEAD: `39f99b0155819fad4dcb5ebd5672276941810ef0`.
The worktree was clean. Read both build briefs, the full round-3 refute including
confirmed items, proposal/design/tasks and both spec deltas. OpenSpec apply is
ready; delivery admission for codex is `ALLOWED`.

## Access-preservation decision recorded

D10 records the lead's technical decision: ACLs alone are insufficient; all
owner-tree daemon deletion/reset/cleanup uses audited launcher-mediated root
maintenance with the fixed `delete-tree`, `reset-tree`, `chown-back` allowlist,
exact daemon uid/pid verification and no-follow openat traversal confined to the
requesting owner's tree. Rollback requires explicit, idempotent, dry-run-capable
reverse migration before an old image starts. Known owner-work 0700/chmod sites
must use group-preserving modes as defense in depth. No widening outside the
owner's work tree, no general root exec and no free-rollback claim.

Updated design, proposal, runtime-role delta and tasks, and added `rollback.md`.
The runbook is a specified sequence, clearly marked not yet executable/tested;
there is no maintenance CLI to document as working. Added the required
production acceptance rows for actual daemon deletion/reset of engine-created
0700 trees, other-owner and symlink-escape refusal, and reverse migration followed
by actual old-image uid-1001 read/write/delete. No build task was checked off.

## Build stop: maintenance authority versus mandatory retirement

The access-preservation choice is resolved. A separate explicit conflict remains:

- D2 and D6 step 4 require CHOWN/FOWNER/DAC_OVERRIDE to be removed from **all
  five** launcher capability sets before service.
- Task 2.2 requires that drop/readback; task 2.8 requires the launcher to refuse
  service while it holds FOWNER or DAC_OVERRIDE. D9/F6 and the runtime-role spec
  preserve this control. The new instruction says all brief probes still apply.
- The new root operation must run after service starts and traverse/re-mode
  engine-owned 0700 content and chown it back to uid 1001. These need the retired
  authority. Root uid alone does not supply it. Fork/exec from the retired
  launcher does not regain it under the bounding set and no-new-privileges.

The isolated Linux diagnostic below demonstrates the conflict, including
successful retained-capability controls. This is not a fourth design review,
not production-image acceptance and not a claim that maintenance was built.

**Decision needed:** either amend retirement to allow the existing launcher to
retain these three capabilities for the fixed scoped operations (and replace
that conflicting oracle row), or preserve launcher retirement and authorize a
separate maintenance helper created before retirement to retain them (amending
the one-long-lived-privileged-process goal). The request authorizes maintenance
but does not state which still-required security constraint changes. No choice,
helper, probe weakening or privilege-regain workaround has been implemented.
The clarification was sent to the lead during this turn.

The build brief explicitly says: "If you hit a genuine design ambiguity, record
it in delivery.md and stop rather than guess." Accordingly stopped before 2.1;
no runtime, test or gate edits. This supersedes the historical F5 stop below.

## Capability diagnostic: exact command and output

Synthetic data only in a disposable network-disabled container, no host volume
mounted, using the existing **test** oracle image. It is **not** the production
image and proves only the capability conflict. Image:
`tinyassets-linux-oracle:724828e06295`, digest
`sha256:c36872bb77443c134a36f690b86d68470f05c9353bd6be0874971874cd1dd6e5`.
The run uses all three compose confinement options, no-new-privileges and the
proposed seven-capability entry set, without SYS_ADMIN.

Write the following script to a temporary file outside the repository, e.g.
`C:/Users/Jonathan/AppData/Local/Temp/uid-maintenance-capability-probe.py`:

```python
import ctypes
import errno
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MIGRATION = (1 << 0) | (1 << 1) | (1 << 3)
FIELDS = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
libc = ctypes.CDLL(None, use_errno=True)
class Header(ctypes.Structure):
    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
class Data(ctypes.Structure):
    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]

def caps():
    lines = Path('/proc/self/status').read_text().splitlines()
    return {k: int(v.strip(), 16) for k, v in (s.split(':', 1) for s in lines) if k in FIELDS}

def setcaps(mask):
    header = Header(0x20080522, 0)
    data = (Data * 2)(Data(mask, mask, 0), Data(0, 0, 0))
    return libc.capset(ctypes.byref(header), ctypes.byref(data))

def check(label, action, expected):
    try:
        action()
        result = 'OK'
    except OSError as exc:
        result = errno.errorcode[exc.errno]
    print(f'{label}={result}', flush=True)
    assert result == expected, (label, result, expected)

def open_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    os.close(fd)

root = Path(tempfile.mkdtemp(prefix='maintenance-caps-'))
root.chmod(0o755)
for label in ('retained', 'retired'):
    tree = root / label
    tree.mkdir()
    (tree / 'data').write_text('synthetic owner A only')
    os.chown(tree / 'data', 1003, 1100)
    os.chown(tree, 1003, 1100)
    tree.chmod(0o700)
assert caps()['CapEff'] == 0x1eb, caps()
tree = root / 'retained'
check('retained.root.open_0700', lambda: open_dir(tree), 'OK')
check('retained.root.chmod', lambda: tree.chmod(0o770), 'OK')
check('retained.root.chown_back', lambda: os.chown(tree, 1001, 1001), 'OK')
assert libc.prctl(38, 1, 0, 0, 0) == 0
for cap in (0, 1, 3):
    assert libc.prctl(24, cap, 0, 0, 0) == 0
assert setcaps(0x1e0) == 0
assert all(not (value & MIGRATION) for value in caps().values())
print('retired.caps=' + ','.join(f'{k}:{v:08x}' for k, v in caps().items()), flush=True)
tree = root / 'retired'
check('retired.root.open_0700', lambda: open_dir(tree), 'EACCES')
check('retired.root.chmod', lambda: tree.chmod(0o770), 'EPERM')
check('retired.root.chown_back', lambda: os.chown(tree, 1001, 1001), 'EPERM')
assert setcaps(0x1eb) == -1 and ctypes.get_errno() == errno.EPERM
print('retired.capset_regain=EPERM', flush=True)
subprocess.run([sys.executable, '-I', '-S', '-c', '''
import os
from pathlib import Path
fields = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
values = {k: int(v.strip(), 16) for k, v in (s.split(':', 1) for s in Path('/proc/self/status').read_text().splitlines()) if k in fields}
assert os.geteuid() == 0
assert all(not (v & 0xb) for v in values.values())
print('retired.fork_exec_regain=DENIED', flush=True)
'''], check=True)
print('DIAGNOSTIC PASS: root uid alone cannot perform required maintenance after retirement', flush=True)

```

Exact command executed (PowerShell):

```powershell
Get-Content -Raw 'C:/Users/Jonathan/AppData/Local/Temp/uid-maintenance-capability-probe.py' | docker run --rm -i --network none --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint python tinyassets-linux-oracle:724828e06295 -
```

Output (exit 0, diagnostic assertions passed):

```text
retained.root.open_0700=OK
retained.root.chmod=OK
retained.root.chown_back=OK
retired.caps=CapInh:00000000,CapPrm:000001e0,CapEff:000001e0,CapBnd:000001e0,CapAmb:00000000
retired.root.open_0700=EACCES
retired.root.chmod=EPERM
retired.root.chown_back=EPERM
retired.capset_regain=EPERM
retired.fork_exec_regain=DENIED
DIAGNOSTIC PASS: root uid alone cannot perform required maintenance after retirement
```

## Verification and release-critical scope

- `python scripts/linux_oracle.py -- tests/test_ta_op_modes.py -q`: exit 0.
  Baseline capability/healthcheck-contract regression only, not role-split
  production acceptance. Output:

  ```text
  [oracle] python 3.11.16 | git 2.47.3 | bwrap 0.12.0 | uid 1001
  ..........                                                               [100%]
  10 passed in 0.15s
  ```

- `openspec validate per-role-uid-split --strict`: exit 0,
  `Change 'per-role-uid-split' is valid`.
- `python -m ruff check`: exit 1, `Found 55 errors.` All are in unchanged
  Python files; this checkpoint changes only Markdown. No unrelated fixes.
- `git diff --check`: exit 0, no whitespace errors.
- `python scripts/test_hygiene_gate.py --base 39f99b0155819fad4dcb5ebd5672276941810ef0 --head HEAD`:
  exit 0, `tests added 0, removed 0, tampering findings 0, product lines added 0`.
  An initial attempt against the staged tree object was rejected because this
  gate requires commits for merge-base; rerunning on the checkpoint commit
  succeeded. No gate code was changed.
- `python -m pytest tests/test_ta_op_modes.py -q --basetemp=C:/Users/Jonathan/AppData/Local/Temp/uid-d10-pytest`:
  exit 0, `10 passed in 0.37s` on Windows. Baseline regression only.
- Commit hooks: mirror parity N/A, mojibake clean (6 text files), cross-provider
  drift clean and skill validation passed.
- No affected runtime implementation or heavy file changed. No test name or
  assertion changed; no plugin mirror regeneration needed (no tinyassets edit).
- No fourth design review. No implementation code exists for the normal
  cross-family build review yet. No PR and no deployment.

**Release-critical files in this step: 0; list: none.** Six documentation files:
`design.md`, `proposal.md`, `tasks.md`, `specs/runtime-process-roles/spec.md`,
`rollback.md` and `delivery.md`, all under this change directory. Checked against
`.github/workflows/pr-scope-guard.yml` SENSITIVE_RE, hard cap 8. Future build
steps still require their own exact release-critical inventory before edits.

**Remaining work:** all tasks 2.1-2.8, including the maintenance implementation,
all-class F1-F7/C1-C6 production-image oracle matrix, migration dry-run/copy/
crash-resume/idempotence, reverse migration/old-image rollback, actual deletion
and reset, broker launch/stream and healthcheck. All are **NOT RUN/NOT PROVEN**.
Tasks 2.9 deployment and 2.10 sync/archive remain unchecked. No build task is
complete. Deviation: applied the brief's stop condition at the demonstrated
capability-lifetime conflict; the access-preservation decision itself is recorded.

---

The following is historical delivery evidence, superseded by D10 and the current
status above where it describes F5 as undecided.

# Current delivery: founder decision 2026-10-05

**founder decision 2026-10-05: fold + build with probes.** Started with
`git pull --ff-only origin feat/per-role-uid-split`: `Already up to date.`
Starting HEAD: `0852b897cf`. Read the full round-3 refute (including confirmed
items), earlier brief, proposal/design/tasks, both deltas and prior delivery.
D9 folds F1-F7 into the design, spec deltas and tasks. No fourth design review.
No runtime code changed, no new task checked off, no PR and no deployment.

## Build stop: F5 ACL-mask ambiguity

The prescribed access/default ACLs and umask do not guarantee daemon deletion
or rollback access after engine writes. `workspace_provision_execution.py:69`
explicitly creates `.venv` with mode 0700. Linux masks the inherited named-user
ACL to the requested group mode; an engine can also chmod an initially
accessible directory to 0700. Both uid 1001 with group 1100 and uid 1001 without
that group then receive EACCES reading or deleting its child. The existing
`workspace_fs.RealPoolFilesystem.remove_tree_no_follow` ultimately needs to open
that directory; a uid-1001 daemon cannot chmod an inode owned by uid 1003 to
repair it. Merely changing the .venv creation mode misses arbitrary engine code.

**Decision needed before implementation:** choose a mechanism that preserves
current daemon deletion and old-image rollback for restrictive engine-owned
paths. Options require different authority/compatibility contracts: control all
permission-reducing operations, or provide scoped repair/deletion plus an explicit
rollback preparation protocol. Neither additional authority nor removal of the
rollback/deletion requirement is inferred. No mechanism has been selected.
This is an implementation-design ambiguity, not a fourth refute round.

The founder explicitly instructed: "If you hit a genuine design ambiguity,
record it in delivery.md and stop rather than guess." Build stopped before 2.1.
D9 preserves all required outcomes and labels F5 unresolved; the documentation
fold is not a claim that the design is ready to implement unchanged.

## Linux counterexample command and output

Synthetic data only in a disposable network-disabled Linux container; no host
volume mounted. Existing **test** oracle image, NOT the production image and NOT
acceptance for tasks 2.1-2.8. Compose's security options are supplied explicitly.
Image ID from `docker image inspect tinyassets-linux-oracle:724828e06295 --format
'{{.Id}}'`: `sha256:c36872bb77443c134a36f690b86d68470f05c9353bd6be0874971874cd1dd6e5`.

Exact reproducible PowerShell command (the same script was run from a temporary
file outside the repository):

```powershell
@'
import ctypes
import os
import tempfile

acl = ctypes.CDLL('libacl.so.1', use_errno=True)
acl.acl_from_text.argtypes = [ctypes.c_char_p]
acl.acl_from_text.restype = ctypes.c_void_p
acl.acl_set_file.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
acl.acl_free.argtypes = [ctypes.c_void_p]

def setacl(path, kind, text):
    entry = acl.acl_from_text(text.encode())
    assert entry
    assert acl.acl_set_file(os.fsencode(path), kind, entry) == 0, ctypes.get_errno()
    acl.acl_free(entry)

root = tempfile.mkdtemp(prefix='uid-acl-')
os.chown(root, 1001, 1100)
os.chmod(root, 0o2770)
entry = 'u::rwx,u:1001:rwx,g::rwx,m::rwx,o::---'
setacl(root, 0x8000, entry)
setacl(root, 0x4000, entry)
pid = os.fork()
if pid == 0:
    os.setgroups([1100])
    os.setgid(1003)
    os.setuid(1003)
    os.umask(0o007)
    for label in ('daemon', 'older-image'):
        for name, mode in (('normal', 0o777), ('venv', 0o700), ('chmod', 0o777)):
            path = root + '/' + label + '-' + name
            os.mkdir(path, mode)
            with open(path + '/data', 'w') as handle:
                handle.write('synthetic owner A data')
            if name == 'chmod':
                os.chmod(path, 0o700)
    os._exit(0)
assert os.waitpid(pid, 0)[1] == 0
for groups, label in (([1100], 'daemon'), ([], 'older-image')):
    pid = os.fork()
    if pid == 0:
        os.setgroups(groups)
        os.setgid(1001)
        os.setuid(1001)
        for name in ('normal', 'venv', 'chmod'):
            path = root + '/' + label + '-' + name
            try:
                with open(path + '/data') as handle:
                    handle.read()
                read = 'PASS'
            except PermissionError:
                read = 'EACCES'
            try:
                os.unlink(path + '/data')
                delete = 'PASS'
            except PermissionError:
                delete = 'EACCES'
            print(label, name, 'read=' + read, 'delete=' + delete, flush=True)
            expected = 'PASS' if name == 'normal' else 'EACCES'
            assert read == expected and delete == expected
        os._exit(0)
    assert os.waitpid(pid, 0)[1] == 0
'@ | docker run --rm -i --network none --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint python tinyassets-linux-oracle:724828e06295 -
```

Output, exit 0 (asserts reproduction of the failure, not acceptance success):

```text
daemon normal read=PASS delete=PASS
daemon venv read=EACCES delete=EACCES
daemon chmod read=EACCES delete=EACCES
older-image normal read=PASS delete=PASS
older-image venv read=EACCES delete=EACCES
older-image chmod read=EACCES delete=EACCES
```

An initial diagnostic reused the daemon-deleted positive-control file for the
old-image case and exited 1 with FileNotFoundError. The command above corrects
that harness issue by creating independent fixtures; the restrictive-path
EACCES results reproduced. Neither run used real user data.

## Checkpoint verification

- `openspec validate per-role-uid-split --strict`: exit 0,
  `Change 'per-role-uid-split' is valid`.
- `git diff --check 0852b897cf HEAD`: exit 0, no output.
- `python scripts/test_hygiene_gate.py --base 0852b897cf --head HEAD`: exit 0,
  `tests added 0, removed 0, tampering findings 0, product lines added 0`.
- Task checkbox counts: design 2, build 10; no new checkmarks.
- `python -m ruff check`: exit 1, `Found 55 errors.` All occur in unchanged
  Python files; this diff contains only Markdown. No unrelated fixes were made.
- Commit hooks: mirror parity N/A, mojibake clean (6 text files),
  cross-provider drift clean, skill validation passed.

## Scope and remaining work

This step changes six documentation files under this change directory:
proposal.md, design.md, tasks.md, delivery.md and both spec deltas.
**Release-critical files in this step: 0; list: none.** Checked against the scope
guard SENSITIVE_RE (workflows, deploy/, Dockerfile, .dockerignore and listed
CI gate files). No runtime/test/gate edit, so no plugin mirror regeneration.
No implementation commit was started; there are no ordered build commits to
claim. When the F5 mechanism is resolved, inventory exact release-critical
paths before each ordered PR-sized build step and keep each step at most 8;
commits alone do not shrink a later combined PR's scope-guard diff.

Tasks 2.1-2.8 remain incomplete, 2.9 deployment is outside authorization, and
2.10 sync/archive remains pending. All F1-F7/C1-C6 production acceptance probes,
production-image build, actual-class operations, broker launch/stream, healthcheck,
migration dry-run/crash-resume/idempotence/rollback and actual deletion APIs
remain **NOT RUN**. Only the isolated ACL counterexample above ran. No affected
runtime tests exist for this docs-only diff; no test name/assertion was changed.
Normal cross-family code review remains required for the eventual build; no
code exists in this checkpoint to review. No fourth design review was requested.

Deviation: stopped before build on the demonstrated F5 ambiguity, as instructed.
There is no change to the requested isolation, rollback or deletion standard.

---

The following delivery history predates the founder's 2026-10-05 decision and
is retained as historical evidence; current status and D9 supersede it.

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
