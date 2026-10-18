# Main vs the clean per-user-instance shape: read-only audit

## Six cutover review corrections (2026-10-18)

Starting head `a6374321c2e`, base `conv/c6-owner-daemon-s3c`. All six supplied
findings reproduce. Work stays local, on synthetic data, with billing calls
replaced at the existing adapter seam. No live API, production, workflow,
branch/configuration change, delegation or publication was used.
Receipts: `C:/Users/Jonathan/AppData/Local/Temp/c6-review-fixes/`.

| Finding | Reproduced path and correction | Regression evidence |
| --- | --- | --- |
| Paid legacy mapping | The historical `subscription_state.apply_tier_event` writes only tier/event/end; the webhook marker records delivery verification, not mappings. Census previously accepted this state and `_seed_shared` seeded absent IDs. Census now requires the verified owner-keyed shared plan mapping, preserves it, and reports `paid_subscription_mapping_missing` per pseudonymous account. It does not invent a mapping from tier metadata. | `test_cutover_billing_deletion` missing-mapping and private-inventory cases; `test_owner_cutover_apply` real publication preserves customer/subscription IDs. |
| Checkout carryover | `_seed_shared` omitted `checkout_attempt_v1` before source cleanup. Census now validates frozen inputs, identity, mode and expiry, refuses malformed/ambiguous/conflicting/unattributed attempts, and seeds the unexpired attempt into shared billing. | Census refusal matrix and real cutover followed by `_start_checkout` returning the original URL without creating another session. |
| Deletion billing | `cancel_account_billing` returned `none` for free accounts without inspecting their attempt; subscription events had no tombstone fence. The door serializes checkout/webhooks with tombstone creation. Cleanup reconciles lost session responses, confirms expiry or immediate cancellation, returns `attempt_expired`, and keeps durable cleanup evidence. Verified completion binds cancellation authority before settling, without granting a tier; late subscriptions are cancelled and recorded. | Adapter tests for open/completed/lost-response sessions, failed expiry, malformed state, completion-before-subscription ordering, late signed events and an in-flight checkout/tombstone race; real door deletion expires checkout. |
| Restore deletion authority | `fence_restore` compared identity/admission/custody/destruction but omitted router receipts and `owner_box_revocations`. Both now participate before receipt mutation or either volume swap. | Authority rollback tests plus the real restore shell with valid same-layout paired archives; a local sweep followed by unavailable broker cleanup cannot resurrect owner bytes. |
| Fleet suspension | Root lifecycle returned generic refusal and maintenance caught only `LookupError`, allowing the host finally block to close every box. The protocol preserves `BOX_SUSPENDED`; maintenance handles admission/auth/transport failures per owner, stops/refuses that box and records `suspended` or `admission_refused`. The waker already contains per-owner task failures. | Three running boxes, with two active turns surviving repeated maintenance while the third is suspended/refused; broker protocol verifies the typed refusal. |
| Suspended deletion | `cleanup=True` bypassed drain only, then normal lifecycle rejected suspension. The authenticated deletion resolution can request root-only deletion admission; startup and ingress latch the existing activity gate to deletion-only. Ordinary wakes and scheduled work remain refused. | Awake and sleeping real owner processes delete through the authenticated door; lifecycle tests prove ordinary work stays refused even after a local deletion refusal. |

The design's **Billing and deletion at cutover (review round, 2026-10-18)**
section and two matching requirements cover all six corrections; strict
OpenSpec validation passes. The shared layer still holds billing and identity
authority only; no owner-store reader, second serving path or cross-owner app
check was introduced. No test or shape probe was removed or disabled.

Validation on the final source:

- Requested patterns, touched tests and structural guards: **1,433 passed,
  zero failed/errors**, 59 files (`targeted.xml`). All 36 root-only skips have
  exact matching passes in `root-clean.xml`; none is credited as a pass.
- Root cutover/restore/control/backup/layout selection: **74 passed, zero
  skips** (`root-clean.xml`). The same-layout shell fixtures include a valid
  generation manifest, so they reach the deletion-authority refusal itself.
- All **35 structural guard files: 961 passed, zero skips**, within the targeted run.
- All **34 affected heavy files: 1,358 passed, zero skips** (`heavy-final.xml`).
- Docker-engine root rehearsal: **all six steps passed, zero skips**
  (`rehearsal-final.xml`), through the requested `--as-root --docker-engine` venue.
- Touched-file Ruff, strict OpenSpec, `check_shapes.py` and whitespace checks
  pass. Repository-wide Ruff has **32 existing findings in six files**, verified
  byte-equivalent to the starting head; no lint debt was added.

The full required CI selection contains **1,206 files** (digest
`sha256:f68881ef98bed601f61dac88da47e99b30dfb5d9d4eef9f0ae0de710f97b6f07`).
It uses `ci_required_tests.py --ci-select pr --base conv/c6-owner-daemon-s3c`
and the repository's `shard_of(path, 4)`, one pytest process per shard through
Linux oracle, with `TINYASSETS_DATA_DIR=/tmp/ta-data`, `CI=true`,
`MSYS_NO_PATHCONV=1` and `-m "not slow"`.

| Shard | Files | Passed | Subtests passed | Skipped | Deselected | Failed/errors | Exit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 253 | 5,394 | 0 | 5 | 1 | 0 | 0 |
| 2 | 450 | 7,107 | 7 | 11 | 1 | 0 | 0 |
| 3 | 256 | 5,682 | 0 | 100 | 0 | 0 | 0 |
| 4 | 247 | 5,552 | 42 | 11 | 0 | 0 | 0 |
| Total | 1,206 | 23,735 | 49 | 127 | 2 | 0 | 0 |

Receipts are `shard-{1,2,3,4}.xml`, matching logs, `shard-manifest.json` and
`shard-exits.json`. Skips are reported separately, never credited as passes.
The new regression file was registered in the index before partitioning, so
the tracked-file shard function agrees with the eventual commit. Superseded
pre-index runs are retained separately and are not credited. The final source
matches the 32-file `validated-source.json` fingerprint.

Post-commit hygiene uses `test_hygiene_gate.py --base conv/c6-owner-daemon-s3c
--head HEAD --body <receipt-directory>/hygiene-body.txt`, preserving `Registry
row(s): S8` and the existing `Test-Removal: retired --` declaration; this review
adds no retired tests.

## Required CI shard reconciliation (2026-10-17)

Starting head: `78984dc9a1c`, on the single cutover branch with base
`conv/c6-owner-daemon-s3c`. Synthetic data only; no production access, workflow
execution, delegation, worktree/configuration/branch change, push or merge.
Receipts are outside the repository at
`C:/Users/Jonathan/AppData/Local/Temp/c6-required-ci-repair/`.

The original supplied failure file has 192 named cases. The initial Linux
rerun of its 31 files recorded **581 passed, 83 failed, 67 errors, 31 skipped**.
Forty-one reported failures already pass at the starting head: 26 HTTP-provider,
14 HTTP-lifecycle and one layout-migration case. The previously failing migrated
owner-process case requires the separate root venue. No prior fix is credited
from an unprivileged skip.

| Cluster | Reproduced failures/errors | Cause and disposition |
| --- | ---: | --- |
| Wiki/pages | 69 | Fixture port: 67 wiki cases scaffolded before center admission, and two quota cases had no wiki center. A shared `admitted_center` fixture seeds real identity/plan admission and ownership, then uses the existing synthetic startup lifecycle to create the local center. The nine visibility cases also admit their additional centers. Page, visibility, deduplication and quota assertions remain. |
| HTTP inference and allocation | 3 (40 already passing) | Fixture port: the served-turn observer watched the retired engine execution route instead of local OwnerTools; it now observes the real local call and its started journal row. Two spawned allocation probes installed a broker but omitted its owner transport/lease setup; they now use the same shared protocol installer as ordinary tests. Wire, cancellation, fork and lock-order assertions remain. |
| Broker/catalog consumers | 34 | Fixture port: imported package fixtures did not carry their defining module's published-catalog transport. Install the existing shared reader with the package fixture, and explicitly in standalone browse, installation, connection and sharing fixtures. Two storage races called `monkeypatch.undo()`, removing broker admission before their final assertions; scoped fault patches now preserve the transport. Includes seven page-read, four publication-intent, eleven completion, six connection-use and six other cases. |
| Graph execution and workspaces | 31 | Fixture port: delivery/file RPC centers were never admitted (22 cases plus nested family execution); concurrent graph workers raced to construct the same test OwnerBoxd (six cases). Admit the centers and serialize fixture core creation. Two workspace tests still supplied/expected the deleted per-call `/cc` projection; they now use the exact local in-box center path. No graph executor fallback or host path was added. |
| Remaining contracts and venues | 13 | Fixture ports: three update-surface and one bootstrap case need published-catalog transport; two direct UI writes need their owner identity; one seat test must read the broker plan instead of a deleted home-subscription helper; one starter receipt names the local center. Three root-control tests use the documented root-venue convention and retain their root assertions. Workflow fix: build-image includes the two existing runtime scripts. Budget test: 145 minus 55 turn-wait and 15 backup minutes leaves 75; reserve 50 for owner-box acceptance and ten for advisory probing before checking both shared-daemon converges. |

No product runtime changed and no capability was dropped. No test was retired,
no assertion was weakened, no quarantine/xfail was added, and no shape probe was
removed. The only added skips are the three explicitly requested root-venue
guards, with actual root execution required. The branch's existing 190 retired
names and deleted-path rationale remain the prior reconciliation's inventory
and the external hygiene body's `Test-Removal: retired --` declaration.

The failure inventory subsequently added eleven cases in reply-budget,
stream-inactivity and daily-cap coverage. Ten share the reply-budget broker
fixture, which seeded only `provider_definitions.json`; publish its descriptor
through the real broker writer before accounting issues a reference. This is
a fixture port, retaining every budget, deadline and credential-scrub assertion.
The eleventh, daily-cap source switching, passes without a further change.
All three files pass together: **49 passed, zero skips** (`inference-followup`).

Cluster validation: the repaired 31-file run records **726 passed, two
failed, 34 skipped**; its two failures identified the standalone sharing
transport and exact starter receipt path. Their final two-file rerun passes
**9/9**. Combined root control/cutover/layout validation passes **61/61**, with
zero skips. The required runner's separate backup root file passes **7/7**.
`cluster-coverage.json` reconciles **761 distinct Linux passes, zero unresolved
failures**; its remaining history-dependent case passes directly against the
local Git history (`runtime-history.log`), which the oracle snapshot lacks.

Final CI selection is **1,205 files**, unchanged after both repair commits,
digest `sha256:aadaf624f138788bc89922ae3dfecb4e20c986c2eef462e35727e4f934ad166e`.
The four `ci_required_tests.shard_of(path, 4)` partitions cover it exactly once.
Each final shard ran as **one** Linux pytest process with `CI=true`,
`TINYASSETS_DATA_DIR=/tmp/ta-data`, `MSYS_NO_PATHCONV=1` in the launcher,
`-q --no-header -p no:cacheprovider` and `-m "not slow"`.
Earlier snapshots superseded by the last fixture ports are retained separately;
they are not substituted for these completed full runs.

| Shard | Files | Passed | Failed/errors | Skipped | Exit |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 449 | 6,502 | 0 / 0 | 6 | 0 |
| 2 | 250 | 5,413 | 0 / 0 | 12 | 0 |
| 3 | 254 | 6,339 | 0 / 0 | 69 | 0 |
| 4 | 252 | 5,449 | 0 / 0 | 37 | 0 |
| Total | 1,205 | **23,703** | **0 / 0** | 124 | **0** |

The two slow cases are deselected by the requested marker. Skips are not
credited as passes: 60 require the Docker Compose CLI absent from the ordinary
oracle image; the remainder are root, OS, history or unavailable-stub venues.
Only the three explicitly requested root-control guards were added here, and
all three have actual root passes. No quarantine or xfail was introduced.

All **35 structural guard files pass, 961 cases, zero skips** (`guards.xml`).
All **34 affected heavy files pass, 1,358 cases, zero skips** (`heavy.xml`).
Touched-file Ruff, `check_shapes.py` and whitespace checks pass. Repository-wide
Ruff reports **32 existing findings in six files**, all verified byte-equivalent
to the starting head. The deploy bound is **4,120 <= 4,500 seconds**, including
both shared-daemon drains/health waits and the 50-minute owner-box acceptance.

Implementation commits: `a0540fb5f98` (cluster ports and workflow filter),
`9a5bc0aa0c5` (published reply-budget fixture). Post-commit hygiene against
`conv/c6-owner-daemon-s3c` passes with `Registry row(s): S8` and the comprehensive
existing `Test-Removal: retired --` body: **196 added, 190 retired, no newly
retired tests or weakened retained assertions**. Final documentation-only
receipt reconciliation is followed by the same gate against final `HEAD`.

## Final C6 migration and source-reader reconciliation (2026-10-17)

This pass starts at `01331dd575f` on the single cutover branch. No production
tree, data, backup or service was accessed; all fixtures are synthetic. No
delegation, host Git configuration edit, branch change, push, merge, rebase, amend
or workflow execution occurred. The independent review remains one later round.

### Migration account-type API

The slice-6 merge already replaced the implementation's retired
`account_type_of(..., db_reader=readonly_db)` call: `_owner_plan` reads `census`
and converts its legacy tier using `usage_policy.account_type`. Census uses
`readonly_db`, including its external scratch for WAL recovery. Calling the
runtime account-type API here would instead require an admitted box and its
broker plan. The one surviving obsolete test now calls the cutover planner and
still requires missing-source refusal before any source database is created.
The existing live/stale WAL tests retain paid-quota and verbatim source-byte
checks; apply retains its before-any-write refusal checks. The earlier audit's
five-failure account-type limitation is superseded by this combined branch.

### Source-validation callers and process verdicts

| Caller / process | Reachability and result |
| --- | --- |
| `providers/api_key_http_provider._completion` in the owner daemon, through `request_budget.issue_reference` and `UsageStore.issue_reference` | The store is remote; it sends accounting IPC. It never executes `_validate_source` against a local owner file. Model registration separately publishes its descriptor through the owner channel. |
| `broker.server._usage` through `broker.usage.local_operation` | Injects the broker ledger and invokes the only production caller of `_validate_source`, local `UsageStore.issue_reference`. Both old token branches therefore executed in the shared broker. The host-file fallback was a dual path and is deleted. |
| Broker outbound dispatch through `resolve_inference_usage` / `claim_reference` | Consumes the already-issued private reference; it does not call `_validate_source` or read owner definitions. Retries remain on that same broker record. |
| Direct `_validate_source` calls in synthetic tests | Broker-local fixtures now inject their ledger and publish descriptors. Changed/outage/fence tests use actual accounting IPC. There is no production owner-local caller of this helper. |
| Router | Forwards owner model work; no router handler invokes source validation. Router connection operations publish shared descriptors without opening owner definition files. |

Clean production box leases are minted with `box_` by broker `_usage_lease`;
normal owner calls therefore need no non-box branch. Old same-UID fixtures
created `proc_` rows over the shared OWNER protocol, and direct helper tests
without an accounting connection also selected the fallback. Both cases put
the file reader in broker-side accounting code, never an owner-local module.
The obsolete shared compatibility path is deleted rather than rehomed.

All accounting rows now validate against `broker.owner_sources.read` and the
live grant, regardless of liveness-token prefix. The source check no longer
resolves a center through the host filesystem. The broker owner-file reader
`provider_metadata.read_definitions` and its unused metadata group-mode constant
are deleted. Private definition publication remains 0600 with no group change.
Tests prove missing publication refuses even when a matching private file
exists, for both token prefixes; accepted publication works with no owner
directory, and links, hardlinks, writable files and FIFOs cannot change it.
Malformed publication preserves the accepted descriptor. Existing API-provider
fixtures now use a real content-addressed descriptor and the correct synthetic
data root; all their wire, response and refusal assertions remain intact.

### Remaining broker-read group inventory

The search covered symbolic `BROKER_READ_GID`, literal `1102`, metadata mode
constants and their producers/consumers across runtime code, scripts, deployment
and tests. These are the remaining families, each with its process verdict:

| Family / reader | Verdict |
| --- | --- |
| `broker.owner_reads.publish/read`, `commons_catalog`, router commons reads | Legitimate published commons under `.published`; no owner-tree read. |
| `box_layout_migration._shared_modes` | Legitimate one-shot cutover. Only `.published` receives this group; retained private shared files use their service UID. |
| `onboarding.owner_sessions.store` -> `broker.policy._session` | Legitimate router identity/approval store `.runtime/owner-sessions.db`, read-only at the broker; never owner-box state. |
| `router_credentials.serve` -> `router_credentials.broker_request` | Legitimate router runtime directory and credential RPC socket. Group traversal/connect is not owner-file access. |
| `boxes.host_service` daemon control socket | Legitimate root-owned lifecycle control socket; kernel peer identity still selects permitted operations. No owner file is opened through the group. |
| `universe_files.open_broker_liveness_lock`, `process_liveness` | Group-publishing helper has no production `broker_readable=True` caller; only synthetic fixtures use it. Production owner locks are private in the box. Broker `_usage_lease` creates/holds accounting locks in its own shared root and probes those same locks, never a host copy of box liveness. No active dual path. |
| Broker custody/vault | Broker-owned `.platform/credential-vaults` and `.broker` stores, private after cutover. No live group grant for owner definitions or owner vault files; old group-related fixture wording does not grant runtime access. |
| `broker.process`, `broker.supervisor`, Compose groups, `rehearsal_serve`, test group fixtures | Service identity/admission or synthetic venue setup, not owner-file readers. |

No other active shared-process owner-file reader relying on this group was
found. The old traversal-only provider-reader tests are ported to published
descriptor isolation and malformed-publication refusal. No gate or shape probe
was removed, and no test skip, quarantine, xfail or assertion was weakened.

### Synthetic venue reconciliation

The required root selection exposed a serving-fixture collision introduced by
the persistent proxy: two owner processes shared a netns and both bound 3128.
`owner_cutover_process` now uses the existing router process rig's ephemeral
listener setting; its domain assertions and production startup stay intact.
The full privileged archive rehearsal instead gives each owner a private null
network namespace, raises only loopback before dropping UID, and retains the
real fixed proxy port. Real XFS, all ten rehearsal steps, both interruption
comparisons, serving, reconnect and both restores remain required.

Validation receipts and selections are outside the repository at
`C:/Users/Jonathan/AppData/Local/Temp/c6-final-items-validation/`.

Completed validation at the implementation commit:
- The first 171-file broad snapshot recorded **2,890 passed, 50 failed,
  nine fixture errors and 36 existing venue skips**. Its 26 API-provider and
  21 usage-store failures are fixed fixture ports, retaining every assertion;
  the nine descriptor cases needed their imported discovery/broker fixtures
  registered. The remaining three failures require actual root control peers.
  All failures/errors and all 36 skipped cases have passing receipts below;
  `coverage.json` reconciles **4,298 distinct cases, zero unresolved cases**.
  No skip is credited as a pass.
- Required root selection: **152 passed, zero failed, zero skipped**.
- Required root Docker-engine archive rehearsal: **6 passed, zero failed,
  zero skipped**. All ten steps, both crash comparisons, serving, reconnect
  and both paired restores passed.
- All **35 structural guard files: 961 passed, zero failed, zero skipped**.
- All **34 affected heavy files: 1,358 passed, zero failed, zero skipped**.
  Deleting the old broker reader makes the conservative selector choose ALL;
  every heavy file was therefore executed.
- Additional root quota, entrypoint and backup files: **17 passed, zero
  failed, zero skipped**.
- Final accounting/descriptor selection: **102 passed, zero failed, zero
  skipped**. API-provider selection: **35 passed, zero failed, zero skipped**.
- Ruff passes all **12 touched surviving Python files**; `check_shapes.py`
  and whitespace checks pass with all probes intact.
- Post-commit hygiene against `conv/c6-owner-daemon-s3c` exits **0**. Its
  external body has `Registry row(s): S8`, the comprehensive
  `Test-Removal: retired --` rationale and all **190 retired names in 32 files**
  across slices 4-6b and this reconciliation. It finds **196 added tests**,
  no reduced assertion counts in retained tests. This pass adds no skip/xfail.

## Merge 6a into 6b (2026-10-17)

Resolved the in-progress merge of `e0a9bef9daf` into `a80d01def37`.
Slice 4 owns the atomic 10-second Box leases, cleanup resolution, router
forwarding/release, 20-second deletion drain and single confirmed destroy.
The roll retains its image records, per-box rollback, bounds, cutover refusal,
provenance, driver, canary and held workflow. Initial roll and candidate rollback
now reuse sleep's lease/idle/drain decision, including deletion and active-turn
refusals. No lease map, token contract or callback path remains from 6b.

All tests and outer assertions in both conflicted test files are retained;
no duplicate tests were removed. Roll expiry coverage is ported to Box.leases,
and the root control proof uses cleanup=False and the 43-character lease.
A new regression proves roll cannot stop a deletion drain after its last turn.
The additional touched-file run exposed one stale secret-reader inventory:
its old account-deletion path is replaced by the router path, with an added
assertion that the old module reads no daemon-only secret. The original subset
assertion is unchanged and the allowed set is not widened.

Validation receipts (including selections, JUnit, assertion/scope audits and
hygiene body) are outside the repository at
`C:/Users/Jonathan/AppData/Local/Temp/c6-s6b-merge-validation-pxgv0z0_/`.

- Requested ordinary Linux selection plus every GUARDS file: **2,027 passed**
  across 97 files. Remaining affected heavy files: **671 passed** across nine.
- Other touched tests: **285 passed** across eleven files after the inventory
  correction and a Compose-enabled rerun of all 50 secret-scope cases.
  Both additional structural budget cases pass. Final root control: **six pass**.
- Full root selection with rsync: **51 passed, five failed**. The five are the
  unchanged protected slice-6 `account_type_of(..., db_reader=...)` mismatch
  already recorded below. The final union is **3,036 passed, five failed, zero
  skips**; initial missing-rsync/Compose skips are replaced by real venue runs.
- Ruff passes all 63 changed Python files; check_shapes, actionlint 1.7.12 and
  whitespace checks pass. No shape probe or test assertion was weakened.
- Post-commit hygiene uses `conv/c6-owner-daemon-s5`, `Registry row(s): S8` and
  the original `Test-Removal: retired --` line; its receipt is `hygiene.log`.

Protected migration files are unchanged from 6b; incoming egress execution/proxy
files are byte-identical to 6a. No production contact, workflow execution,
remote Git mutation, branch switch or host Git configuration change occurred.


## Slice 6b: per-box deployment and canary acceptance (2026-10-17)

Implemented locally on `conv/c6-owner-daemon-s6b`, based on `fd306784711`.
`boxhostd` has one authenticated `roll` operation for broker UID 1002 and
the root deploy caller. Prepare publishes the durable next-wake image and
returns awake UIDs plus the sleeping count. The driver visits awake boxes
with concurrency two; sleeping boxes receive no restart or individual SSH
call. Restart drains the existing HTTP/turn gate, preserves capacity while
releasing the fleet lock, checks readiness, and restores only the failed
box's previous image. A failed candidate must also drain before rollback,
including scheduled work started during startup. Unknown/busy candidates
are never killed by deploy or its later maintenance cleanup.

The host's root-private image registry records current and per-UID launch
images with fsync and atomic replacement. The static harness extractor
uses the same OCI image as the shared deploy and never mounts owner data.
Both forward and rollback launches require a statically declared owner-box
layout. Missing or older declarations refuse with the slice-6 backup/restore
path; no override, migration code or restore implementation was added.

The roll uses slice 4's atomic 10-second resolve/release wake leases and
its HTTP/turn drain accounting. Each Box owns its leases; sleep, roll and
candidate rollback share the same expiry, idle guard and drain decision,
including active-turn and deletion refusals. Router forwarding releases at
response admission or failure/cancellation. Passive SSE does not retain a
lease for its lifetime. The duplicate 6b lease map, 65-second token contract,
router callbacks and daemon-control wrappers are deleted. Door-orchestrated
20-second deletion drain, cleanup resolution and the single confirmed
boxhostd destroy remain slice 4's implementation. All tests from both sides
are retained and ported to that API; no duplicate test needed removal.

`scripts/roll_owner_boxes.py` streams its root control helper over SSH stdin,
avoiding a privileged executable in shared `/tmp`. It writes one allowlisted
receipt: outcome counts, failing UIDs, control/canary results. No owner row,
error payload, model reply, connection path or credential enters it. A
rollback, control failure or failed canary fails the step. The canary signs
in through the existing public pinned-account door, verifies its owner/home,
then runs one nonce turn through public MCP. Its response must have the same
box UID and exact deployed SHA as its own `/app/me`. OwnerIngress replaces
provenance headers using the admitted UID and image-baked build file. The
secret loader has an opt-in canary profile in both shells; default operator
secrets remain the default profile.

Bounds: 60 seconds per initial/candidate drain, 30 for graceful flush, 60
for readiness, two per lifecycle HTTP request, bounded runtime/quota commands,
600 for a control reply and 610 for SSH. Image export/extraction are 180
seconds each; create/remove are 30 each. Workflow step: 50 minutes, with its
job budget extended by that amount. D11 as built contains the control and
storage contract. The new required step follows shared/public health probes;
its receipt upload uses the workflow's existing reviewed action pin. A box
failure does not select the shared/global rollback path.

Validation receipts, selections, JUnit and the hygiene body are outside the
repository at `C:/Users/Jonathan/AppData/Local/Temp/c6-s6b-validation-7_cxur50/`.
The requested broad selection is 114 ordinary files plus six root files;
it includes every `GUARDS` key. Initial broad run: **2,719 passed, five failed,
zero skips**. Those five findings were repaired without altering their
assertions: classify the new bounded waits and image marker, preserve the
reviewed upload-action pin, keep invalid image errors distinct from cutover
refusal, and rename the extracted staging directory before making its root
read-only. Corrected focused run: **164 passed, zero skips**. Final root
control proof, including real kernel broker/router/deploy identities and
resolve/release: **six passed, zero skips**.

The root selection was repeated with rsync installed only inside the
disposable oracle: **49 passed, five failed, zero skips**. All five failures
are the unchanged protected `box_layout_migration.py` / removed
`account_type_of(..., db_reader=...)` mismatch already recorded in 6a part 1e
(both WAL parametrizations, unreadable founder preflight/apply, and the
owner-split census/move fixture). Those protected files were not edited.
The corrected complete structural/roll/workflow selection passed **1,061
tests, zero skips**, including every `GUARDS` key. The last targeted roll/
driver/canary rerun passed **31 tests, zero skips**, including an absent
rollback image while the failed candidate still has running work. Ruff,
`check_shapes.py`, pinned actionlint and strict OpenSpec validation passed.
No touched test file is in the heavy-file inventory. The dependency selection
identified 23 affected heavy files: three already executed in the requested
broad/structural selection; the remaining 20 passed **718 tests, zero skips**
in a separate Linux oracle run (`heavy.log`). Ruff passed all **19** changed
Python files. Actionlint used **1.7.12** and the existing reviewed artifact
action pin; no gate assertions or shape probes were removed or weakened.
Post-commit hygiene uses the original 6a retirement line and `Registry row(s):
S8`; its output is `hygiene.log` in the same receipt directory.

Synthetic proof includes a real owner daemon process through the router,
running-request drain, secret rotation and a separate unaffected owner;
Linux root control sockets use actual dropped UID/GID clients. Runtime/XFS
remain the slice-4 doubles. Canary success/failure tests use a loopback
synthetic router/box and a synthetic model reply, never production. Native
PowerShell canary-profile loading also passed.

The founder's external `disabled_manually` hold is unchanged. No workflow
was enabled, dispatched or run; no production host/data/backup was contacted.
No agent, new worktree, push, merge, rebase, branch switch or host Git-config
edit was used. The lead owns independent floor review, native gVisor/XFS/
cgroup proof, the deployed SHA assertion and the live public canary after
the slice-7 hold is lifted. Overall change/spec sync stays with that cutover.

## Slice 4 door-owned account deletion follow-up (2026-10-17)

The public `/app/account/delete` route is shared-router-only. Its existing
identity, same-origin JSON and confirmation validation precedes one door
orchestrator: forward to `/_box/account/delete`; receive box-local erasure;
persist the receipt and owner tombstone under the credential dispatch lock;
call fenced broker `RETIRE_OWNER`; clear router state in-process; drop the
device refresh handle and cookie; return 200. A blocked/refused box returns
409 unchanged. An incomplete receipt or failed step returns a loud error.
The tombstone stores the receipt in the existing router database, so retries
skip box resolution/deletion, finish retirement and cleanup, and return the
saved result after the process is gone.

The broker permanently fences new admissions, retires all existing owner
centers, and waits for boxhostd to revoke ingress and stop the real process.
The box's own deletion no longer calls the provider lifecycle or shared
broker/identity/billing cleanup. Router cleanup covers owner sessions,
inline/hosted/OAuth flows, refresh and grace handles, canary state, approval
handoffs, API keys and hook digests.

Removed paths: public deletion registration inside the box; the box handler's
router-revoke call and local router-store fallback; the credential dispatch
`account_revoke` operation; its broker retired-owner exception/self-stop path;
and the test-process revoke endpoint. No compatibility operation remains.
`test_a_failed_account_revoke_can_be_retried_to_completion` now drives the
public router door with a real box and injects both retirement and cleanup
failures. The old box-revocation test was replaced with local-only erasure
proof, and the old 503 expectation became a 200/receipt assertion. Additional
proof covers blocked/refused deletion, multiple center retirement, stale
fences, stop-acknowledgement retry, missing/wrong ingress secrets, and old-secret
401 immediately before process stop. No test file was deleted or quarantined.

Focused validation (logs, JUnit and selection outside the repository at
`C:/Users/Jonathan/AppData/Local/Temp/ta-s4-door-xmay9m42/`):
- Dedicated real-process router suite: **28 passed**.
- Requested owner/onboarding patterns, touched tests, broker tests and every
  structural `GUARDS` key: **1,640 passed, 6 failed** across 72 files. The
  failures were five stale MCP secret-deposit probes and one root-only control
  test run as the unprivileged user; the deletion and structural tests passed.
- The recovery probes now exercise the live MCP `requestFromUser` write,
  retaining the login-change, no-replay and session-preservation assertions.
  Secret deposits already moved to the separate shared door; the old extracted
  harness failed with `ConnectOAuth is not defined`. Full repaired file:
  **34 passed** on Linux. No product JavaScript changed.
- Root-required control proof: **1 passed**; owner-destruction file (including
  its two permanent-UID filesystem cases): **5 passed**. All six original
  failures now have passing executions. Every Linux report has zero skips.
- Ruff passed on all **13 changed Python files**; strict OpenSpec validation
  and `git diff --check` passed.
The lead retains full CI selection, independent review, deployment and live
naive-user proof; this follow-up is committed locally only.


## Slice 3 implementation follow-up (2026-10-16)

Owner-mode model and authenticated HTTP calls now acquire the admitted relay,
use the broker's existing grant/policy/accounting operations and stream over
`OPEN`. Source descriptors and allocation liveness live at the broker; these
model-source and accounting reads do not open box-private files. Plan and published-catalog reads
use `BOX_SHARED`. D3 decisions in `design.md` record the protocol choices.

Focused Linux validation: **95 passed** across the new owner-egress tests,
owner daemon app/MCP chat tests, broker server, upstream streaming and usage
IPC tests. This includes a fresh box subprocess model call, secret absence
in its environment and data root, an authenticated effector, revoked grants,
lost accounting-channel liveness, missing broker refusal and socket/HTTP
refusal. The final accounting/owner/router recheck passed **68 tests**, and
the selected AD chunk ran all **18 owner-egress cases**. Ruff, strict OpenSpec
validation and `git diff --check` pass. No tests were retired and no
skip/skipif was added.

Cutover work remains outside this slice: boxhostd supplies the relay mount,
sanitized environment and null network namespace; slice 6 seeds the shared
billing/published stores. The shared Stripe webhook/checkout writer and
commons publishing must be wired to those stores before cutover (these reads
do not migrate either writer). Provider CLIs refuse pending broker-endpoint
authentication; daemon-side subscription/credential refresh is disabled, with
no vault fallback. The founder's slice 7 unported-feature report must include
these remaining writer and authentication paths. D2's hook-digest publication
and shared credential-management integration also remain pre-cutover work;
this slice implements the requested egress and shared-read operations.

Implementation commits: `45e5035341f`, `0cf18d31da2`, `8970069eed7`,
`dd58f98022b`, `ce98cb2beb2`; validation-fixture corrections: `bea903d8cbc`.
No push, deploy, provisioning, waker or migration change was performed. No
agent was dispatched.

After committing, ran `python scripts/affected_tests.py --base origin/main
--pr --out <file>`, stripped CRs, dropped missing paths (none), and ran
`split -l 155`. The final regenerated selection is identical: **900 files**,
five 155-file chunks and one 125-file chunk. Each ordered list ran through
`linux_oracle.py`; all 34 affected heavy files ran separately. Exact lists,
logs and JUnit reports are in `%TEMP%/owner-s3-validation/`.

| Linux selection | Result |
| --- | --- |
| AA, 155 files | 3,505 passed, 12 existing skips, 1 identical main-baseline failure |
| AB, 155 files, complete corrected rerun | 2,715 passed, 2 existing skips |
| AC, 155 files | 3,683 passed, 47 existing skips |
| AD, 155 files | 3,133 passed, 20 existing skips |
| AE, 155 files | 2,841 passed, 1 existing skip |
| AF, 125 files, complete corrected rerun | 2,535 passed, 5 existing skips |
| Affected heavy files, 34 files | 1,359 passed, no skips |

AA's `test_activity_shape_migration::test_conversion_is_per_owner_idempotent_and_restoreable`
failed while archiving a disappearing `.runs.db-shm`. The same 155 files in
the same order on the initial `origin/main` (`417f6a4c003`) produced identical
JUnit failure text (3,504 passed, 12 skips, that one failure). Migration code
was not changed. Main advanced to slice 1 (`e5583246c25`) during validation.

AB exposed a real source-validation regression: a daemon-side caller tried
to open a local accounting DB. The fix reuses the broker's existing admission
transaction and preserves daemon-side grant queries; the entire AB chunk was
rerun successfully.

AF initially exposed two timing-sensitive fixture failures: catalogue expiry
during an authority-only test, and an accounting finalizer timing out during
a workspace test. The two files passed together in isolation (178 passed,
2 skips), and the full ordered main-baseline AF run passed (2,535 passed,
5 skips at `e5583246c25`), so neither was counted as a baseline exception.
The authority fixture now holds its synthetic discovery clock constant; the
workspace fixture collects prior cyclic allocations on the test thread before
an in-process broker can run their synchronous accounting finalizers. The
original assertions and runtime freshness checks are retained. The complete
corrected AF chunk passed with no warnings.

Existing skip reasons include Windows, Linux root/rsync, shellcheck, Docker Compose,
or the explicitly opt-in live CLI venue; two older vault-link fixtures also
skip on their missing destination parent. They are not counted as passes.
The selected and heavy runs total **19,771 passed, 87 existing skips**, and
the one exactly reproduced AA baseline failure. The work remains local.

## Slice 2 review fixes and validation (2026-10-16)

Local implementation: `545139dd00e`. Validation fixture correction: `50323502234`.
Review disposition: AGREE on findings 1–4; finding 5 is deferred to slice 4,
with no secret-start/rotation implementation change. Nothing was pushed or deployed.

- **1, socket:** `resolve_box` defaults to the existing Compose mount,
  `/run/tinyassets-boxhostd/<uid>/app.sock`; a test checks that deployment contract.
- **2, keys:** the router alone writes key records. Owner-mode mint/list/revoke,
  including connected-agent listing, use the bound relay, broker admission and
  broker-only router RPC; grants remain local and the box has no key database.
  Real owner-process tests mint a key through this channel, authenticate `/api`,
  revoke it from the box and observe 401. The fixture no longer copies key rows.
- **3, hooks:** the router mints and revokes the token map through the same channel;
  payload/configuration rows remain local. Box-created tokens reach the owner's
  real socket, and raw-token, prefix and source revocations all yield inbound 404.
- **4, identity:** B's bearer plus A's valid protected cookie returns 401;
  matching bearer/cookie and cookie-only requests still work.
- **Channel admission:** tests refuse owner-field injection, another owner's
  key revocation, a non-broker router-socket peer and a retired box admission;
  another owner's hook-map entry survives a foreign revocation attempt.

`design.md` D2 records the socket default, bound credential channel and sole
router writer, local grants and failure cleanup, hook revocation ordering,
single request identity, slice-4 secret-start deferral and stack-only maintenance
cutover after the `--archive` rehearsal. No deploy or provisioning files changed.

Focused Linux runs: 146 passed; the expanded router/connected-agent/key/hook and
inventory rerun passed 99; the corrected snapshot fixture passed 7. No skips in
these runs. All used `python scripts/linux_oracle.py`.

After the local implementation commit, CI's exact selection command
`python scripts/affected_tests.py --base origin/main --pr --out <file>` selected
889 files. CRs were removed, no paths were missing, and `split -l 155` produced
the ordered chunks below. Regenerating after the fixture commit produced the
same selection byte for byte. Each chunk ran through `linux_oracle.py`.

| Chunk | Files | Passed | Existing skips |
| --- | ---: | ---: | ---: |
| AA, complete corrected rerun | 155 | 3,502 | 12 |
| AB | 155 | 2,709 | 2 |
| AC | 155 | 3,694 | 47 |
| AD | 155 | 3,123 | 20 |
| AE | 155 | 2,855 | 1 |
| AF | 114 | 2,402 | 5 |
| Affected heavy files | 34 | 1,359 | 0 |

The selected chunks total 18,285 passes and 87 existing skips, with zero final
failures. No skips were added and no tests were retired. Logs, exact ordered
lists, JUnit reports and the selection digest are under
`%TEMP%/owner-s2-review-9a6wl8na/`; `final-results.json` references AA's corrected
report rather than replacing its original failed-run evidence.

AA first hit a disappearing `.runs.db-shm` while the activity migration fixture
tarred a supposedly stopped tree. Its SQLite contexts committed without closing;
garbage collection could remove a sidecar during traversal. The fixture now
explicitly closes its SQLite connections. The identical 155-file
order on clean, detached `../wf-c12-rehearse` at `origin/main`
`417f6a4c003db2bfde9cf216cb734eb5e317adc2` passed 3,501 tests with 12 skips: it did
not reproduce the failure, and no baseline exemption is claimed. The complete
corrected branch AA rerun passed, as shown above.

Ruff passes for every changed Python file; `git diff --check` passes. Bare
`python -m ruff check` reports 35 existing findings, identical to detached main
by relative filename, code, line and message; both JSON reports are retained
with the validation evidence. The baseline worktree stayed detached and clean;
the slice-3 worktree was not touched, and no agents were dispatched.

## Slice 2 implementation follow-up (2026-10-16)

The authenticated router now forwards owner-facing app routes, MCP transport, REST owner
handlers and inbound hooks through one streaming Unix HTTP proxy. Its only box
lookup is the fenced broker `OWNER_BOX_ROUTE` operation; reservations without a
live admission refuse. The broker persists and rotates per-box secrets, and
`owner_daemon --secret-file` rejects missing/wrong secrets before authentication
or handlers. The router strips public credentials and replaces all identity
headers. D2 decisions in `design.md` record the wire and shared-map contracts.

Deleted from shared serving: moved route registrations, MCP payload inspection
and connection receipts, owner startup/recovery/engine/tick execution, and the
direct unauthenticated SSE/stdio entrypoints. Recovery maintenance now runs in
the owner lifespan. Existing handler modules remain available inside the box.

Focused Linux validation: **155 passed**, covering real broker IPC, two owner
daemon processes, file/MCP/REST reads, B naming A's center, unknown/unadmitted
owners, forged broker fields/fences, durable secret rotation, wrong secrets,
hook token routing, SSE first-chunk delivery and disconnect cancellation,
router-only published/sign-in-shell/pulse routes, and the owner-store guard.
Ruff on changed Python files and `git diff --check` pass. No skips were added.

Test-Removal: retired `test_startup_initializes_before_the_assigned_consumer_starts`: shared owner initialization was deleted; replacement asserts no shared consumer startup, with storage ordering retained in owner-lifespan tests.

Test-Removal: retired `test_main_initialization_failure_does_not_start_worker_or_serve`: the shared owner initialization dependency was deleted; replacement proves router startup does not touch owner storage.

Test-Removal: retired `test_admitted_lifespan_reaches_initialization_in_order`: the router lifespan no longer initializes owner state; replacement asserts that boundary, with owner initialization coverage retained.

Test-Removal: retired `test_both_bearerless_transports_bind_the_local_operator`: shared local-operator serving was deleted; both transports now assert refusal.

Test-Removal: retired `test_staged_bundle_enumerates_seven_under_simulated_admission`: shared stdio serving was deleted; the staged artifact proves refusal even with cloud admission.

Test-Removal: retired `test_staged_bundle_enumerates_without_credentials`: shared stdio serving was deleted; the replacement proves refusal and no server response without credentials.

Remaining integration belongs to later slices: boxhostd mounting the socket and
read-only secret, rotation on start and wake-on-request (4); seeding shared maps
and splitting existing owner stores (6). Review fixes below complete shared key
and hook management from boxes in slice 2. No
provisioning, egress, migration or deploy files changed; nothing was pushed.

Local implementation commits: `38fae347fb9`, `6e3d9ecb220`, `3acb71ee293`,
`b68bc5756e5` (the connection-lifetime validation fix).
No push, provisioning, migration or deploy was performed. The user's explicit
no-delegation instruction applies to this slice; no peer agent was dispatched.

CI selection used `python scripts/affected_tests.py --base origin/main --pr --out <file>`
after the local implementation commits, removed CRs and missing paths, then
`split -l 155`. The original 878 files formed five 155-file chunks and one
103-file chunk; the final selection contains 888 files, with eight added
files and then two connection-fix additions run as supplemental chunks. Every invocation used
`python scripts/linux_oracle.py -- <ordered files> -q --tb=short`.
Logs and exact ordered lists are under `%TEMP%/owner-s2-validation/`.

| Linux selection | Result |
| --- | --- |
| AA, 155 files | 3,348 passed, 4 existing skips |
| AB, 155 files | 2,711 passed, 2 existing skips after the connection-lifetime fix |
| AC, 155 files | 3,621 passed, 47 existing skips |
| AD, 155 files | 3,255 passed, 20 existing skips |
| AE, 155 files | 2,777 passed, 1 existing skip |
| AF, 103 files | 2,188 passed, 5 existing skips |
| Final-selection addition, 8 files | 215 passed, 8 existing skips |
| Connection-fix selection addition, 2 files | 147 passed |
| Affected heavy files, 32 files | 1,330 passed |
| Connection-fix affected heavy files, 7 files | 479 passed |
| Structural targets, 39 targets | 824 passed on the complete corrected rerun |
| Focused owner routing/lifecycle | 155 passed; expanded owner-app/session coverage 111 passed |
| Registry and moved-code regression recheck | 132 passed |
| Retired transport and owner-tree tests | 83 passed |
| Connection lifetime and direct callers, 8 files | 217 passed, including the red-to-green real-SQLite regression |

First-pass failures caused by this slice were old assertions about shared
startup, maintenance source locations, the request-logger constructor and
retired shared transports; their owner-side/refusal replacements retain the
relevant coverage. The new hook map and HTTP stream calls also needed entries
in the existing store/background-call inventories. No skip or skipif was added.

AB first failed in `test_actual_competing_store_writer_is_fenced_until_commit`
(`.runs.db` case), then errored during setup of
`test_change_after_reservation_refuses[retire]`: both raised SQLite `unable to
open database file` in `branch_versions` (2,710 passed and 2 skipped on each run).
Two comparisons in the identical 155-file order on untouched `origin/main`
`417f6a4c003db2bfde9cf216cb734eb5e317adc2` each passed 2,711 tests with 2 skips;
they did **not** reproduce the failure, so this slice claims no baseline exemption.
The second baseline's sampled descriptor count reached 938 under a 1,024 soft
limit. Inspection found that version-store transaction contexts did not close
their SQLite connections. A new real-SQLite regression failed before the fix;
the scoped operations now close their connections, including setup-error paths,
while retaining commit/rollback semantics. This necessary validation fix changes
no storage shape or authorization rule. The complete original AB order then
passed 2,711 tests with 2 existing skips. Its sampled descriptor count in the
previously failing section was 100. No observed failure is carried as a baseline
exemption. Chunk reruns cover the route/lifecycle corrections; the final
connection fix additionally has the complete AB rerun, its direct-caller and
affected-heavy runs, and both added selected files recorded above.

## Slice 1 implementation follow-up (2026-10-16)

The owner entrypoint now serves the existing app with relay-derived admission,
local file helpers and `OwnerBoxd` tools. Its acceptance test covers the app
file door, MCP status, a real chat loop writing a file, local bash calling ta
through the real loopback engine endpoint, pause/resume tick dispatch, refused
startup, and a separate CLI process. Non-loopback connects are forbidden in
the in-process acceptance run. Model wires use the existing fixture and its
real in-process broker; no cloud-provenance fixture is used.

Run with `python -m tinyassets.owner_daemon --data <root> --socket <app.sock> --secret-file <read-only-secret>`;
`--relay` defaults to the existing `/run/boxhostd/relay.sock` mount.

Linux validation (working tree, no commit or deployment):

| Selection batch | Result |
|---|---|
| 310 files | 6,811 passed, 5 skipped, 2 failed, 2 setup errors; baseline comparison below |
| 155 files, first half of the second chunk | 3,348 passed, 61 skipped |
| 155 files, second half of the second chunk | 2,953 passed, 2 skipped |
| 66 files | 1,511 passed, 3 skipped |
| 18 additional affected files | 346 passed |
| 122 additional affected files | 2,165 passed, 6 skipped |

`affected_tests.py --base origin/main --pr` was run as requested. Because it
compares committed HEAD, the working-tree and untracked paths were also fed to
`--changed --pr`: all 826 selected files are covered above. Lists were stripped
of CRs, missing paths dropped, and split at 310 files. The original second
310-file process exhausted its 1,024-file-descriptor soft limit before pytest
could produce a summary; its identical file list passed in the two 155-file
batches above. No skip or test removal was introduced.

The initial first-chunk failures were SQLite `unable to open database file`
errors in the browser cases `test_manual_update_read_drops_old_selection_or_account_response[account]`
and `test_pending_manual_update_cannot_boot_saved_script_during_refresh[closed_dialog]`,
plus setup for `test_cannot_skip_a_blocked_intermediate_release` and
`test_completed_copy_registers_actual_provenance_automatically`.
The three affected files pass in isolation on both this tree and `origin/main`
(76 passed each). The same full 310-file order against an archive of untouched
`origin/main` (`417f6a4c003db2bfde9cf216cb734eb5e317adc2`), in the same Linux
oracle venue, produced 6,809 passed, 5 skipped, 4 failed and 2 setup errors.
It reproduces the same SQLite failure and the exact blocked-intermediate-release
setup error; other failing case names vary between runs. This is baseline
failure evidence, not a claim that the full selection was uniformly green.

Additional Linux checks: 8/8 owner-daemon acceptance tests; 1,295 passes across
31 affected heavy files; 74 passes in the additional heavy/structural pair;
224 passes in the focused touched-module run. The structural run found the new
state directory missing from storage classification and two file-I/O guard
findings; those were fixed, and the 92-test follow-up (including acceptance)
passes. Ruff on every changed Python file and `git diff --check` pass.

Retained paths to move or delete in later slices (not ported by this slice):

- Center admission and account deletion still select the shared box provider:
  `tinyassets/boxes/lifecycle.py:19`, `tinyassets/account_deletion.py:1006`.
- Native CLI execution/discovery still needs the shared session launch binding:
  `tinyassets/control_plane/native_launch.py:19`,
  `tinyassets/providers/owned_process.py:138`,
  `tinyassets/providers/native_discovery.py:107`. Real credential and model
  egress remains slice 3; `owner_runtime.broker_egress` names its reserved seam.
- Workspace execution and lease reclamation still select a remote provider:
  `tinyassets/workspace_worker.py:202`, `tinyassets/workspace_fs.py:1036`.
  Wake command probes also retain that path: `tinyassets/wake_conditions.py:115`.
- The retained scheduler/engine discovery loops still enumerate serving centers
  from the root database (`tinyassets/control_plane/tick.py:444`,
  `tinyassets/provider_serving_binding.py:1146`,
  `tinyassets/engine_mcp_http.py:396`). In owner mode that root contains only
  this owner's rows; slice 1 adds no owner filter.

The original main audit below is preserved as the pre-change comparison.

Repo: `C:\Users\Jonathan\Projects\wf-isolation-by-box`, HEAD `d31c8293b6a` = origin/main `417f6a4c003` plus one spec-only commit (`openspec/changes/isolation-by-box/`, 4 files, no code). All code citations below are identical on `417f6a4`. Production sha `fa29c95` is not in the local object store, so I could not diff it. Merge order on main (first-parent): C7 #4845 (10-15 00:58), C6 P2b-1..4 (#4947/#4946/#4945/#4944), C6 #4920, C7/C9 binding #4939 (10-15 08:15), C6 prep #5000, **C6 #5043 "delete the owner-cell path"** (10-15), **C10 #4937** (10-16 01:54), C6 #5164, #5166.

Clean shape (report): the shared layer holds only identity, the owner→box map/router, billing, the credential/egress proxy, a next-wake index and published data. The agent loop, history, files, owner DB, schedule rows, pause and owner-data app/MCP logic run inside the box. The shared daemon holds zero host paths to owner data.

**Verdict:** main is an intermediate shape. It has a box for **files and exec**. The **agent loop, conversation history, schedules, pause, subscription/usage state and all app/MCP logic** still run in the shared daemon. After the C6 move, the daemon keeps per-center host stores in four host places. Main has no owner-split runtime left to fall back to.

---

## 1. Where the agent loop runs

**In the shared daemon. The box is called only for files, tools/exec and provider CLIs.**

- `universe_server.converse` admits the turn and calls `session.start(..., OwnerTurn(...))` in the daemon (`tinyassets/universe_server.py:3480-3489`). `session.start` loads the agent definition and runs `_owner_turn` → `universe_intelligence.converse` → `_call_writer` (`tinyassets/control_plane/session.py:115-140, 196-208`; `tinyassets/universe_intelligence.py:1111, 1330, 1601`). Workflow and activity steps run `call_foreground_work_agent` the same way (`session.py:211-240`).
- `session._binding` binds a box handle for the turn and refuses if none is configured (`session.py:143-180`, "owner-box session provider is not configured" at 155-157). `bind_session` mints a `SessionBinding` from `provider.bind(center, account_id, turn_id)` (`tinyassets/control_plane/binding.py:136-145`).
- The loop's event loop is a daemon thread, `ExecutionOwner` "agent-execution" (`tinyassets/agent_loop/execution.py:49-63, 105-114`). Workflow turns use it at `tinyassets/workflow_agent.py:418`.
- The HTTP chat loop is "Serve HTTP chat on the shared loop with tools bound to the turn's box" (`tinyassets/agent_loop/served_chat.py:1-6, 48-80`). The four tools each become one `start_exec` on the bound box (`tinyassets/agent_loop/box_tools.py:1-26, 98-140`). Routing is in `tool_session.py:1-16`. Owner reads (`history`, `activity`) are answered "read-only in the platform process and never forwarded to the box" (`tinyassets/agent_loop/owner_reads.py:1-15`).
- Provider CLIs (Claude CLI, Codex and other external-agent packages) are the exception. They run **inside** the box as an exec through `BoxLauncher` (`tinyassets/providers/owned_process.py:1-14, 127-154`; `tinyassets/control_plane/native_launch.py:9-24`). For those providers the CLI's own inner tool loop runs in the box. The platform turn (admission, journal, history, prompt assembly) still runs in the daemon. `docs/architecture.md:154` (S3) calls this "native CLI loop `claude_provider` ... remain", marked drifted.
- `docs/architecture.md:71` states the as-built shape directly: "`agent_loop/` The thin loop: model turns in the platform, tool calls routed to the box."

**What the box runs:** short, bounded workers, not a resident agent.
- `boxd`/`BoxdCore` is a descriptor-safe file and exec core: read, write, list, stat, remove, start_exec, stream, cancel, export and import (`tinyassets/boxes/boxd.py:1-7, 154-1008`; `OwnerBoxd` at 1111-1140).
- In production, file operations are "one bounded file request, executed ONLY inside the owner's gVisor box" (`tinyassets/boxes/file_worker.py:1-5`). Execs enter through a fixed entry (`exec_worker.py:1`). Code nodes run in `node_runner.py:1-6`. The `ta` client is a credential-free stdio RPC worker (`agent_loop/box_ta_worker.py:1`, trusted end in the daemon at `box_ta.py:1-5`).
- The host (`boxhostd`, root, outside the daemon) owns receipts, generations, quotas and the sandbox lifecycle (`tinyassets/boxes/host_service.py:1`, `owner_files.py:1-6`, `executions.py:1`, `gvisor_runtime.py:1-6`).
- The host caps awake sandboxes at 2 (`tinyassets/boxes/gvisor_files.py:80-94`) and runs execs through a per-owner FIFO that quiesces the box after each command (`executions.py:270-325`; design §12 at `openspec/changes/one-box-per-owner/design.md:561-577`, "The host stops the entire sandbox before recording a terminal execution").

**What the C6 design says the box runs:** the loop, in a later PR.
- The design boundary table puts "The agent loop, one process per session" inside the box and the turn journal and timers outside (`openspec/changes/one-box-per-owner/design.md:37-45`). §3 says "start the loop process in the box" (`design.md:94`). Decisions: "The loop runs in the box" (`design.md:633-635`). ADR-019 agrees: "The box runs the work: the agent loop, its tools, child sessions..." (`docs/decisions/ADR-019-one-box-per-owner.md:31-34`).
- That move is **P3, unbuilt**: `- [ ] 1.4 P3 Loop in the box ... Deletes: the daemon-side loop placement (_call_writer path)` (`openspec/changes/one-box-per-owner/tasks.md:160-180`). P3 depends on C3 and C8 (`design.md:410, 429-434`).
- ADR-022 names "The owner-steered loop runs in the daemon" as an accepted residual between P2b and P3 (`docs/decisions/ADR-022-box-backend-gvisor-on-the-droplet.md:165-180`).
- `target-architecture` D6 still describes the opposite target: the loop "runs in the execution owner (D11)", outside the box (`openspec/changes/target-architecture/design.md:279-296`). The two specs disagree. ADR-019 amends ADR-012 (`docs/decisions/ADR-012-sealed-box-target-architecture.md:5`), so the C6 design is the newer word.

## 2. What is left on the host after `box_layout_migration.apply`

**The whole center tree is copied into the box, and the host tree is kept.**
- The module docstring says "The old trees stay in place" (`tinyassets/box_layout_migration.py:3`).
- For each owner and center, `_copy(data/center, store/<uid>/center)` runs `rsync -aH` of the entire `<data>/<center>/`. It excludes only credential entries (`.credential-vault.json`, `.credentials`, `.credentials.json`, `.oauth-refresh`) and `.runtime` (`box_layout_migration.py:35-37, 402-416, 582-587`).
- Credentials are copied separately to `<data>/.platform/credential-vaults/<center>` (`:434-453, 583`). `.platform/owners/<uid>/agent-home` is copied to `<tree>/.agent-home` (`:588-607`).
- The move then writes `box_records` and `.boxhost/boxhost.db` and the `owner-box` marker. It deletes nothing (`:609-630`).
- The design deletes old trees only after a post-cutover verified backup (`design.md:464-466`). No code on main does that deletion.
- Consequence (inference from the exclude list): the box also receives a copy of every platform dotfile in the center (`.conversation_memory.db`, `.subscription_state.db`, `.pause`, `ledger.json`, `status.json` and so on). The owner can write that copy.

**Per-center platform stores the daemon keeps on the host after the move, by category.** The full name list is in `tinyassets/command_center_layout.py:53-102` and `openspec/changes/isolation-by-box/design.md` D2.

| Category | Host location | Evidence (daemon opens a host path, not the box) |
|---|---|---|
| Conversation (chat history, attention, retrieval) | `<data>/<center>/.conversation_memory.db`, `.conversation_attention.db` | `conversation_store._db_path` = `Path(universe_dir)/_DB_NAME`, then `sqlite3.connect` (`tinyassets/conversation_store.py:75, 121-126`). Converse loads and records through `_universe_dir(uid)` (`universe_server.py:3369-3376, 3445-3449`). Also `conversation_attention.py:49-50`, `conversation_retrieval.py:28` |
| Billing/plan and metering | `.subscription_state.db`, `.usage_ledger.db` | `storage/subscription_state.py:18, 56-63` |
| Requests/approvals, idempotency, receipts, authoring | `.pending_requests.db` (legacy, fenced), `.idempotency.db`, `.external_write_receipts.db`, `.authoring.db`, `.wiki_write_back_destination_markers.db` | `storage/pending_requests.py:49, 307`; `idempotency.py:270`; `authoring/store.py:48` |
| Scheduler and control flags | `.pause`, `.worker_supervisor*.json`, `.runtime_status.json`, `.engine_mcp_*.json`, locks | The tick reads `(base_path / universe_id / ".pause").exists()` on the host (`control_plane/tick.py:975-976`). Status reads `(udir/".pause").exists()` (`api/universe.py:1155`). Pause is **written** through `write_data_path` (`api/universe.py:4106-4110`), which routes to the box when a binding exists and raises `BoxAuthError` when none does (see §3). This is the "written to the box, read from the host" bug |
| Records dir outside `<center>/` (untouched by the move) | `<data>/.agent-sessions/<center>/`: `agent-activities.db` (activities and protected requests), `activity.db`, `history.db`, `steering.db`, owner-control locks | `agent_sessions.py:55, 84-88`; `agent_activities.py:40, 160-162`; `harness_history.py:15`; `agent_steering.py:50`; `storage/request_migration.py:37-48` |
| Sidecars and custody (outside `<center>/`) | `<data>/.universe-sidecars/<center>/` (consents, seed locks), `<data>/.platform/credential-vaults/<center>` | `boxes/layout.py:25-26`; `storage/effector_consents.py:69`; `credential_vault.py:162` |
| Shared root DBs holding per-owner rows | `<data>/.tinyassets.db` (ACL, `universe_owner`, branch definitions), `<data>/.runs.db`, `<data>/.automations.db` (schedule rows), broker DBs | `automations.py:70, 540-541`; `api/market.py:364`; `provider_serving_binding.py:1146-1160`; tick iterates every serving universe (`tick.py:381, 466, 646`) |

Platform records classified as platform state that live inside the center are a mixed case: `status.json`, `activity.log`, `ledger.json`, `work_targets.json` (`command_center_layout.py:55-60`). They go through `read_data_path`/`write_data_path`. Inside a turn they therefore hit the **box** copy, which the owner can write. Outside a turn they fail with `BoxAuthError`. Example: the ledger append in `api/engine_helpers.py:112-121` only logs a warning on failure.

The guard `tests/test_universe_path_io_guard.py:36-40` counts only `read_text`/`open`/`unlink` and similar calls. It does not count `sqlite3.connect` or `.exists()` (inferred from `_RAW_ATTRS`), so these host-store accesses fall outside it.

## 3. App and MCP owner-data reads

**All of it runs in the shared daemon.** A box is reached three ways:

1. **Session binding.** Only `session.start` mints one (`session.py:143-180`). `native_discovery` (`providers/native_discovery.py:108-118, 195-205`) and account deletion (`account_deletion.py:1015`) also bind. The engine MCP server reuses the live session's binding through `active_binding(owner, center)` (`engine_mcp_server.py:4783-4790`; `binding.py:129-133`). So the agent's own tool calls during a turn reach the box.
2. **`file_binding(root)`** (`binding.py:148-166`). It returns the current binding when the root matches. With no binding, any root directly under `data_dir()` raises `BoxAuthError("owner files require an admitted session binding")` (`:162-165`). Every `read_universe_file`/`write_universe_file`/`list_universe_entries`/`read_data_path`/`write_data_path` goes through it (`tinyassets/universe_files.py:115-162, 192-217, 343-398, 655-740`). `read_data_path` re-raises the error as `UniverseFileError` (`:673-678`).
3. **Raw host paths.** `sqlite3.connect`, `Path.exists()`, `os.open` on `<data>/<center>`.

No code on main binds as the requesting account outside a turn. The `#5168` helpers (`owner_box_reads`, `ensure_host_stub`, `platform_center_path`) are absent: a grep finds nothing.

**What breaks or goes stale after the move** (an app/MCP request with no live turn):
- **Raises or returns an error (`BoxAuthError`):**
  - `universe action=control_daemon pause` writes through `write_data_path` (`api/universe.py:4106-4116`). It returns "Failed to write pause signal: owner files require an admitted session binding".
  - `read_output` (`api/universe.py:2010-2028`) returns "was not read".
  - Wiki writes (`api/wiki.py:124-2518`, `write_data_path`), premise and config writes (`api/universe.py:3203, 3583, 4540`), canon meta (`:3795`) and the `ledger.json` append (`api/engine_helpers.py:119`) refuse.
  - `_read_json` on `work_targets.json` in listing/inspect raises (`api/helpers.py:215-234`; `api/universe.py:1151`).
- **Silently wrong:**
  - Universe-listing word counts swallow the `OSError` per file and count 0 (`api/universe.py:1120-1130`).
  - First-contact home materialization degrades to `_no_home("admission")` on a `BoxAuthError` (`api/first_contact.py:168-178`).
- **Stale (reads the host copy, which the move leaves in place):**
  - The app file browser and downloads (`owner_door/files.py:42-51` → `api/universe_file_reads.py:165-177, 129-149`) open `<data>/<uid>` with `os.open`/`listdir` on POSIX. They never touch the box, so after the move they show the frozen pre-move copy and none of the agent's new files.
  - Pause status is read from the host (`api/universe.py:1155`; `tick.py:976`). A pause written during a turn goes to the box, so the scheduler never sees it.
  - Chat history (`conversation_store`) is not stale: it is a host-only platform store that the daemon still owns.
- `owner_door/routes.py:1-14` serves `/app/api/read` and `/app/api/status` through the same domain reads as MCP `read_graph`/`get_status`, so it inherits all of the above.

## 4. Single-owner self-hosted daemon, and the daemon-in-each-box idea

**Self-hosting is not supported.**
- `docs/getting-started/self-hosting.md:1-28`: "the production server is not a supported install". `universe_server.main` refuses unless the process is an admitted cloud runtime (`tinyassets/platform_runtime_provenance.py:1-30`; `universe_server.py:4180-4187`).
- ADR-008 makes the platform cloud-only (`docs/decisions/ADR-008-cloud-only-platform.md:13-29`). README line 15 says the same.
- `tests/smoke/test_selfhost_parity.py` only unit-tests `scripts/selfhost_smoke.py`. That script is the Row F "self-host migration" smoke: canonical URL vs tunnel parity for the host-to-droplet move (`scripts/selfhost_smoke.py:1-25`). It is not a per-owner runtime.

**Single-tenant remnants that would help:**
- `data_dir()` is one env-selected root (`tinyassets/storage/__init__.py:185-215`).
- `is_local_single_tenant`/`is_local_operator_process` mark a tray process (`api/permissions.py:265-276`; `auth/middleware.py:1046-1063`).
- `universe_files` keeps a Windows single-tenant tray fallback (`universe_files.py:18-20, 324-327`).
- Single-tenant defaults remain in `api/helpers.py:154`, `api/universe.py:4410, 4559` and `graph_compiler.py:411`.

**Could the same daemon run inside each owner's box, behind a shared router?** Not as main stands. Blockers, all from main:
1. **No ingress, NIC or credentials in the box.** The box's only channel is one `boxhostd` socket, it has no NIC, and every outbound call crosses the broker (`design.md:53-68`). The daemon serves `/mcp`, `/api` and OAuth itself.
2. **Provenance gate.** `universe_server.main` refuses any process that is not the admitted droplet runtime (above).
3. **The box lifecycle is per-exec, not resident.**
   - At most 2 awake sandboxes (`gvisor_files.py:80-94`).
   - 2 execution slots and 16 admitted jobs across owners, with whole-sandbox stop per command (`design.md:561-577`).
   - Resident processes, checkpoint and suspend come in P3 and P6 (`tasks.md:160-209`).
   - A resident per-owner daemon would need P3 and P6 plus a capacity change. The design itself flags G3 capacity as a possible founder spend (`tasks.md:60-62`).
4. **The daemon is multi-tenant by construction.**
   - Shared root DBs hold every owner's rows (`.tinyassets.db` ACL and `universe_owner`, `.runs.db`, `.automations.db`, broker identity).
   - The tick iterates every serving universe from the root DB (`tick.py:381, 466`; `provider_serving_binding.py:1146-1160`).
   - Splitting these into a per-owner DB plus a shared index is the "P7 Platform tables into `storage/` (S10)" work, unbuilt (`tasks.md:210`; `docs/architecture.md:161`, S10 drifted, `daemon_server.py` 27 tables).
5. **The box API is the daemon's only way into owner files.**
   - Only `boxes/frontend.py` configures a provider. It requires uid 1001, a committed `owner-box` marker and a host-side broker supervisor (`frontend.py:15-30`).
   - `file_binding` forbids host paths under `data_dir()` (`binding.py:162-165`).
   - Inside a box, the daemon would need its own tree as plain local files, but `LocalBoxProvider` is "unisolated ... Production must not instantiate" (`boxes/local.py:1-6`).
   - Every `file_binding`/`BoxProvider` call site would invert to direct I/O. Inference: this is a large but mechanical change, the opposite of #5043's routing.
6. **The planned in-box loop is a process, not the daemon.** P3 plans a "loop process in the box" with the broker in-box endpoint, while the control plane (session registry, timers, journal, inbox) stays outside (`design.md:39-44`; `tasks.md:162-178`). That matches the report's shape (loop in the box, a shared next-wake index outside). It is not a copy of the whole daemon per box.

## 5. C10 (#4937)

**What it changes** (`git show --stat dd5d4ed5d80`: 150 files, +1602/−11853):
- **Paid market retired.** Deleted `payments/actions.py`, `funding.py`, `schema.py`, `settlement_*`, `wallets.py`, `treasury/*` and `gates/actions.py`/`schema.py`. Trimmed `api/market.py` (−642), `attribution/calc.py` and `ta_cli.py`. Removed WebSite token pages, the paid-market spec and `creator-revenue-share`.
- **One MCP registration boundary.** New `control_plane/registration.py`: `engine_server(handlers)` with a fixed middleware stack and aliasing of the four tools (`registration.py:1-56`). `engine_mcp_server.py` and `served_tools.py` were touched.
- **Activities without the Activities workflow branch.** New `control_plane/activity_session.py` (+129). New `Activity(RunStep)` in `session.py`, which reads instructions through `activity_runner.activity_instructions` (`session.py:82-84, 218-220, 243-246`). `activity_runner.py` −231 net.
- **Legacy Activities conversion.** New `tinyassets/activity_shape_migration.py` (+286), plus +93 in `box_layout_migration.py` and a small `deploy_fail_safe.sh` change.

**Dependency on C6 and the owner-box layout: yes.**
- **Runtime.** `activity_instructions` reads `Activities.md`, `Activities.tools.json` and `Activities.error.txt` through `file_binding` and `binding.provider.read_many` (`activity_runner.py:15-52`). Activities start through `session.start`, and `_binding` refuses without a box provider (`session.py:143-157`). C10 merged after #5043 and was built on it (`git merge-base --is-ancestor eb25b981dac dd5d4ed5d80` is true).
- **Conversion.** The "activity conversion" is **C10 code** (commits "Convert legacy Activities under protected deploy gate", "Fence activity conversion by owner and retry missing boxes"). It is wired **only** into C6's `box_layout_migration`:
  - `check` returns 10 for an `owner-box` volume without `activity_shape: v1` (`box_layout_migration.py:168-188`).
  - `apply` calls `convert(...)` in two places: on the owner-split→owner-box move, before the copy, against host centers (`:558-566`), and as a second one-time pass on an already-`owner-box` volume against box roots (`:538-556, 280-309`).
  - `_boxed_activity_roots` resolves roots from `box_records` (`:284-286`).
  - `convert` itself takes a generic `owner_roots` map (`activity_shape_migration.py:175-205`), so it is layout-agnostic in principle. Its only caller is `box_layout_migration.apply`. On owner-split, `_owner_move_preconditions` refuses when an activity center has no admitted owner (`:483-487`).

**Can main deploy with C6's move off and still keep C10? No, not without reverting C6 runtime code.**
- Setting `ACTIVE_BOX_LAYOUT="owner-split"` makes the deploy take the owner-split branch. That branch logs "this candidate does not apply the cutover" and skips `apply`, which also skips C10's activity conversion (`deploy/deploy_fail_safe.sh:1635-1644, 2003-2014`).
- The image would then fail to serve (see §6): no legacy launcher, no box provider, and C10 activities need a binding. Legacy `activities::*` branch work would also never be converted (`activity_shape_migration.py:56-77`).
- Keeping C10 alone would need either:
  - a revert of #5043 plus C10's box-bound parts, or
  - a reworked C10 conversion that runs on owner-split host centers. That is a small change in `convert` (inference), but its runtime still reads through the box.

## 6. The switch, and what main runs with the move off

**The switch is two constants** in `tinyassets/storage_layout.py:52-55`:
- `ACTIVE_BOX_LAYOUT = "owner-box"`. The deploy reads it into `BOX_CANDIDATE_LAYOUT` (`deploy_fail_safe.sh:1635-1644`), which selects the move step (`:2003-2079`; "The candidate constant selects this step. It does not authorize it" at `:2017`).
- `SUPPORTED_BOX_LAYOUTS = {"owner-box"}`. The rollback and candidate-compatibility probe reads it (`deploy_fail_safe.sh:370-410`).
- At runtime, `storage_layout._validated` refuses any marker whose `box_layout != ACTIVE_BOX_LAYOUT` (`storage_layout.py:180-185`).
- Gates that block the move even when the switch is on: a provisioned canary, an idle-turns proof, the XFS store check, preflight and a fresh paired backup (`deploy_fail_safe.sh:1876-1878, 2015-2056`).

**With the move off, main has no working host-path (owner-split) runtime.** #5043 deleted it: "delete the owner-cell, role and jail execution path" (373 files, −44,356 lines). The chain:
- **Entry.** `deploy/box_daemon_entry.py:12-22, 37-39` sends an `owner-split` marker to `/usr/local/libexec/ta-launch.py`. The Dockerfile no longer ships that file ("the image ships no launcher, mapper or cell", `Dockerfile:355-361`; only `ta-box-entry.py` is copied). The entry exits 78 with "this image serves only owner-box data". A fresh volume with no marker hits the same refusal (`:29-31`).
- **Runtime, if the entry were bypassed.** `configure_box_provider` is called only by `boxes/frontend.py:26-29`, which requires the `owner-box` marker (`:15-19`). Without it:
  - every chat turn raises `BoxAuthError("owner-box session provider is not configured")` (`session.py:155-157`);
  - every provider CLI raises `ProviderConfinementError` (`owned_process.py:140-154`; `native_launch.py:24`);
  - every owner-file access under `data_dir()` raises (`binding.py:162-165`).
- **`boxhostd`** idles on an owner-split marker (`deploy/boxhostd.py:25-51`).
- **Inference:** a main image with both constants set to `owner-split` would pass the deploy's layout gates, then fail the health check and roll back. Production would stay on `fa29c95`.
- The C6 design states the intent: "The new image serves only from boxes. There is no flag back to cells" (`design.md:478`).
- `design.md:594-595` still says "`ACTIVE_BOX_LAYOUT` and production compose remain owner-split". That line is stale: the constant is `owner-box` on main.

---

## Gap to the clean shape (by closing shape, from the above)

- **Loop into the box.** This is C6 P3: loop process, broker in-box endpoint, sessions surviving deploys. It deletes `_call_writer`, `ExecutionOwner` and the daemon-side loop placement.
- **Per-center platform state out of `<data>/<center>/`.** `isolation-by-box` D2 moves it to the sidecar. In the clean shape most of it (history, pending requests, pause, automations rows, steering, activity) belongs **in the box**, not in a platform sidecar. Only the subscription/usage/billing rows, consents and the next-wake index stay shared. `isolation-by-box` D4 keeps `.pause` as a platform file, which contradicts the report's "put the flag where its reader lives" rule once the scheduler reads a wake index.
- **App/MCP reads.** Either bind as the requester (`isolation-by-box` D3) or move the owner door into the box. The file browser (`api/universe_file_reads.py`) is the clearest host-path reader to delete.
- **Shared root DBs** (`.tinyassets.db`, `.runs.db`, `.automations.db`) still hold per-owner rows. This is C6 P7/S10.


## Task 3c-2a local validation (2026-10-16)

Custody writers, erasers and refresh now live in the broker package. The
HTTP_CONNECT commit carries the secret and prepared revision; broker-side
policy/grant validation precedes custody publication. Rotation uses the stored
scheme and expected incarnation. DISCONNECT erase returns `secrets_removed`
and erases custody before deleting fenced ledger rows. Account erasure uses
broker deletion tombstones, and the router no longer stages vault directories.

The exact requested focused selection plus all changed test files was 88 files:
1,482 passed initially, with 21 failures and 19 environment-dependent skips.
All failures were repaired, then the affected 12-file rerun passed 219 tests
without skips. That includes all 17 Compose parser cases, after copying the
existing local validation image's Docker/Compose tools into the local oracle
image (no repository dependency or CI configuration change). The two permanent
UID erasure cases passed separately under `linux_oracle.py --as-root`.
No unresolved failure or unexecuted skipped case remains in this selection.
The full CI affected selection was not run.

New acceptance: AST guard against external vault writers; six deposit schemes
in a separate broker process with a writer-PID observation and committed grant;
public and registered-client OAuth refresh on calls from a separate owner-box
process; unknown REFRESH/REFRESH_ACK refusal; broker-only OAuth environment
rendering. Existing refresh concurrency, storage failure, disconnect retry,
rotation policy preservation and lifecycle tests were adapted to broker custody.

Deleted protocol-only tests from `test_broker_refresh.py`:
`test_foreign_stale_and_revoked_refresh_never_spends` (daemon callback admission),
`test_lost_ack_keeps_rotation_and_does_not_replay`, and
`test_broker_without_daemon_callback_cannot_refresh_locally`.
The rejected-digest test now checks local rejected-token refresh. Existing grant
and fence suites continue to cover denied dispatch. The old create-fault orphan
test now requires no deposit on a failed ledger mutation. Vault deletion tests
now check broker tombstones and locking, replacing daemon-lock staging,
nonhome-admin and absent-owner-schema assumptions. The record-builder guard
now names broker deposit/refresh modules. A pre-existing router-session I/O pin
was brought up to date for its shared session files. The streaming thread check
permits unrelated threads to exit while still forbidding new worker threads.

Also deleted: daemon `_deposit_http`, the old `broker/refresh.py` callback
preparation, the daemon OAuth service's refresh operation, refresh frames and
client callback plumbing, and the standalone Slack vault-deposit script.
Slack diagnostic scripts now use existing broker custody only.

Task 6 must migrate credential ownership/deletion metadata into
`.broker/credential-custody.db` and change credential-vault directory ownership
to the broker uid. Existing HTTP secret files remain in place. The environment
installer renders only OAuth configuration into `broker.env`; no provider code
or host provisioning was added. RFC 7009 is 3c-2b. Remaining door flows, box
secret-free actions, full CI, independent review and cutover remain with their
respective lanes/lead. No sub-agents, external reviewers, push, merge, branch
switch or deployment were used for this task.

## Task 3c-2b partial build (2026-10-16)

Local commits `5477624cf28` and `d6605267a55` implement provider revocation
and standalone `/app/connections` controls. `SOLE_CENTER` is a fenced broker
identity query; explicit centers still use `ADMITTED_OWNER`. Unknown, foreign,
retired and ambiguous centers return 404 at the door. The handler reads no
owner home, ACL, provider assignment or disconnection store. Protected-cookie
and bearer identity agreement is retained at this now-shared handler.

`DISCONNECT` erasure invokes RFC 7009 under the custody lock, with the current
refresh token or otherwise the access token, and registered client auth from
the trusted directory. A provider error never prevents local erasure. Its
fixed failure code remains on the revoked connection row (`revocation_error`,
`custody_erased`); `HTTP_CONNECT` replaces that receipt on a fresh connect.
`CONNECTION_CATALOG(include_revoked=True)` keeps interrupted local cleanup
reachable at the door without the former owner-side disconnection read.

Deleted paths: the connection handler's `_read_home`, founder-home/admin
checks, `remove_http` composition and `unfinished_disconnections` read. The
box's existing removal composition is unchanged. No tests were deleted.
Catalog and connection-lifecycle fixtures now seed broker admission; the
deleted-account deposit fixture uses broker erasure. Structural inventories
were reconciled with earlier custody/CLI deletions, and the platform-call spy
fixture now uses HTTP sources instead of retired executors.

New tests: `tests/test_connection_door.py` covers an empty router root,
admission selection, foreign centers, identity conflict, stale incarnation,
fenced cleanup retry and broker query fencing. `tests/test_broker_revocation.py`
adds `test_disconnect_revokes_current_token_then_erases` for public and
client_secret_post clients, each with refresh-token and access-token cases;
it also covers HTTP/network/directory failures, absent endpoint, stale fence,
erased-grant call refusal, idempotent removal and fresh reconnection.
These are not the requested complete per-auth door-to-box lifecycle tests.

The requested focused selection plus structural guards and touched tests was
121 files: 2,447 passed, 17 failed, 3 setup errors and 2 root-only skips on the
initial snapshot. Both skipped cases subsequently passed with
`linux_oracle.py --as-root` (the two permanent-UID cases in
`test_owner_box_destroy.py`). The first six-file implementation run passed 65;
the subsequent door/catalog/revoke run passed 27. The eleven-file repair run
passed 153 with one remaining provider-resolution fixture failure. No skip,
xfail or quarantine was added; the full CI affected selection was not run.
The final `test_platform_has_no_llm.py` rerun passed all 16 after using a real
HTTP definition/grant and a synthetic executor. Every initial failed/error case
is now covered by a passing repair rerun, and both skipped cases executed as
root. Ruff passed on all 21 changed Python files. The final full selection was
not repeated; validation is the initial selection plus these focused reruns.

Remaining work, explicitly uncompleted:
1. Move `/app/model-connect/*` (including popup, source sign-in, presets,
   generic OAuth/DCR and registered clients) to the standalone shared door;
   remove its home/pending/provider-store dependencies and apply admission
   center selection there. Only `/app/connections` moved in this build.
2. Move authorization-code/token exchanges into the broker, including preset
   PKCE exchange, and remove router token exchange/client-secret custody.
3. Remove secret parameters from box MCP and approval connect schemas,
   implementations, instructions and docs; return door links for secret/sign-in
   schemes and use bound-channel keyless HTTP_CONNECT.
4. Carry opaque `request_ref` through broker connection/grant rows and ledger
   queries, and resolve the box's pending connect request from that ledger.
5. Add the full nine-auth-type separate-process door-to-box connect/call/
   refresh/disconnect acceptance matrix and router flow-consumption assertions;
   update/delete superseded connect tests with a deletion inventory.
6. Tick 3c-2 and replace its partial as-built note only after those are complete.

No reviewer/sub-agent, new worktree, push, merge, branch switch, git-config
change or deployment was performed.


## Task 3c-2c build (2026-10-16)

Implementation commit: `d8fa40b9dae`.

`/app/model-connect/` is a shared door prefix. Preset PKCE begin/exchange/resume,
key deposit, inline popup begin/poll/cancel, generic OAuth with public DCR,
registered clients, source sign-in and the public client metadata document now
work without a home, pending-request, provider-assignment or universe-store read.
The authenticated owner selects a live admitted center, using `SOLE_CENTER` when
omitted; unknown/foreign centers return 404. Definitions come from installed
source/acquisition data, credential-free input or a broker catalog query. The
browser preserves the selected center across authorization redirects.

`HTTP_CONNECT` adds door-only `oauth_offer`, `oauth_exchange` and
`preset_exchange` actions. The broker resolves registered-client metadata and
performs authorization-code/token and preset-key exchanges, then commits custody
and the grant. Registered client secrets remain in `broker.env`; the environment
installer now excludes the OAuth namespace from `daemon.env`. Browser redirects,
state/challenges, frozen public definitions and sealed popup verifiers remain at
the door. Redemption consumes the flow before IPC; responses contain only a
credential-free projection. Key deposits use the existing prepare/commit action.
The opaque `request_ref` is stored on the connection row and returned by commit,
resume and `GRANTED_RESOURCE`; the door never resolves a pending request.

Deleted production paths: router token exchange and token-bundle encoding in
`connection_oauth.flow`; router preset exchange transport (moved into
`broker/acquisition.py`); home/ACL/pending composition in `model_connect`;
`model_bootstrap.complete_bootstrap` and `_resume_key`; source sign-in's
`raise_sign_in_ask`, `is_canonical_sign_in` and `offer_signed_in_source`.
Owner-side model-access helpers remain for the next task. No provider-specific
exchange code was introduced.

Deleted tests asserting those removed compositions:
- Entire `tests/test_model_bootstrap.py`: `test_catalogue_failure_is_resumable_without_another_oauth_exchange`,
  `test_concurrent_manual_and_oauth_keys_have_one_deposit_winner`,
  `test_deposit_composes_existing_request_but_does_not_answer_or_serve`,
  `test_disconnect_then_guided_reconnect_requires_fresh_approval`,
  `test_empty_resume_requires_authorization_not_generic_server_error`,
  `test_home_rebinding_refuses_new_deposit`,
  `test_owner_approval_uses_existing_enable_path`,
  `test_partial_activation_resumes_same_request_without_rotating_custody`,
  `test_partial_bootstrap_removal_invalidates_old_pending_approval`, and
  `test_second_oauth_result_cannot_replace_first_deposit`. Its shared fixture
  moved to `tests/support/model_connect.py`.
- `test_manual_catalogue_never_selects_paid_model` in the manual-connect suite.
- `test_sign_in_on_a_powered_command_center_asks_to_add_it` and
  `test_post_sign_in_database_failure_preserves_success` in the source sign-in
  suite. Direct source authorization and broker custody replace the old
  pending-ask/automatic-serving assertions in the remaining source tests.

Replaced/renamed tests (their former names no longer assert the deleted path):
`test_manual_owner_reaches_existing_approval_then_connects`,
`test_manual_does_not_replace_existing_setup`,
`test_deletion_between_ingress_scope_and_durable_begin_cannot_recreate_metadata`,
`test_new_owner_begin_provisions_own_empty_home_without_llm`,
`test_one_tap_raises_the_platforms_ask_from_installed_data`,
`test_sign_in_on_an_unpowered_command_center_serves_on_it`, and
`test_rail_sign_in_uses_server_provenance_for_relay`.

Manual reconnect/approval tests now verify broker grant reuse and resume.
New-owner tests require admission without bootstrapping a home; retirement
races are tested against the admission map. OAuth, popup, persistence and
router-schema fixtures now use the standalone definition and broker contract.

New acceptance tests in `tests/test_model_connect_door_lifecycle.py`:
- `test_door_exchange_box_call_refresh_disconnect[dcr]`
- `test_door_exchange_box_call_refresh_disconnect[registered]`
- `test_door_exchange_box_call_refresh_disconnect[preset]`
- `test_source_sign_in_uses_client_metadata_without_owner_state[dcr]`
- `test_inline_popup_flow_at_empty_door_polls_and_cancels[preset]`

The three lifecycle cases use separate broker and box processes with fake
providers and no provider network. They cover door connect, a box effector
call, broker refresh for OAuth, disconnect, RFC 7009 when configured and refusal
of the next box call. Credentials are generated only inside the broker process;
router token/secret/store access seams fail the test. The router root contains
only shared runtime/flow state, and flow consumption, opaque request references,
replay refusal and foreign-center 404s are asserted.

Remaining scope is unchanged: box-side secret-free connect actions and door
links, completion of the box's pending request from `request_ref`, and the other
six authentication lifecycle cases are the next task. Do not tick all of 3c-2
until that work is complete. Full CI, independent review, migration/cutover,
deployed-SHA assertion and the live naive-user pass remain with the lead.
This build is local only; no sub-agents, new worktree, push, merge, branch switch,
git-config change or deployment was performed.

Validation: the requested patterns, every structural-guard test and the touched
tests comprised a 115-file focused Linux selection: **2,327 passed, 3 failed,
2 root-only skips**. The three failures were a source-card admission fixture and
two deployment assertions that still expected registered clients in daemon.env.
The final six-file repair run passed **164 tests**, including all three failed
cases, all five new door tests using the real box effector, hosted PKCE, generic
OAuth/browser callback tests and source sign-in. Both root-only cases in
`test_owner_box_destroy.py` passed separately with `--as-root` (**2 passed**).
The additional source-pooling run passed 48 with one normalized-policy assertion
failure subsequently fixed and covered by the final 164-pass run. The prior
six-file door run passed 130 with one exact response-shape assertion failure,
also fixed and covered by the final run. All failures in the focused selection and all root-only skips now have
a passing execution; obsolete path assertions are deleted as listed above.
No skip, xfail or quarantine was introduced. The full CI affected selection was not run or claimed. Ruff passed on
all **33 changed Python files**, the background-authority inventory is clean,
and `git diff --check` passed.

After the implementation commit restored the original non-ASCII verifier test
value caught by the commit hook, the complete hosted-auth file passed again:
**51 passed** on Linux. The commit's import-graph, path-resolver, provider-drift
and skill hooks passed; final ruff still passed all 33 changed Python files.


## Task 3c-2d build (2026-10-16)

Implementation commit: `b6e2c91cac4`. Its import-graph, path-resolver,
provider-drift, skill and text-encoding commit hooks passed.

3c-2 is complete locally. Box `connect_http` and `rotate_http` no longer take
credentials; the old secret-taking implementations, `allow_oauth2`/`allow_keyless`
switches, `answer_connect_with_token`, `_assembled_secret`, `_rotate_answer`,
box-side `_with_sign_in_offer`, the keyless vault marker and `connect_source`
helper are deleted. Secret/token/key/password/capability-URL values are absent
from the box connect schema and rejected by connect approvals. The box returns
an `/app` link containing request, center and connection ID. Generic browser
connect, rotation and model-endpoint forms post secrets directly to the shared
door. The keyless action commits through HTTP_CONNECT and dispatch needs no vault.

The existing continuation tick and pending-request read reconcile broker
GRANTED_RESOURCE rows by request_ref and policy. They apply connection uses and
resolve through the existing answer/wake transaction. Failures remain pending
and are returned explicitly. Rotation also records request_ref. The door reads
no owner request and the box reads no router state; no push path was added.

New tests in `tests/test_model_connect_door_lifecycle.py`:
- `test_other_auth_connect_box_call_disconnect` (none, bearer, basic, header,
  oauth1a, url_secret; three door disconnects and three box disconnects).
- `test_box_action_and_approval_link_then_ledger_completion` (links, rejected
  secret values, unmatched/matched request_ref, repeat polling and no router read).
- `test_box_connect_schema_has_no_secret_parameters`.

Existing credential tests now enter through the shared door, then poll the box
ledger. OAuth tests discover at the door; source-pooling fixtures separately
apply model consent inside the owner context. Browser tests fail if credential
values are sent through the approval/MCP transport. A structural-guard fixture
that captured a mocked `/fake` path during imports now uses a per-test data root.

Deleted tests for the removed in-box token-answer path:
- `tests/test_consent_owner_answers.py::test_oauth_token_answer_requires_owner_proof_before_deposit`
- `tests/test_generic_oauth_connections.py::test_the_stored_token_url_is_pinned_to_the_discovered_one`

Deleted stale bootstrap/automatic-serving tests whose implementation was removed in 3c-2c:
- `tests/test_notification_is_the_setup.py::test_the_guided_sign_in_round_trips_to_a_serving_universe_with_no_llm_call`
- `tests/test_notification_is_the_setup.py::test_reconnect_after_a_product_disconnect_serves_again`
- `tests/test_notification_is_the_setup.py::test_after_the_approval_a_turn_selects_a_free_tool_model_on_its_own_connection`
- `tests/test_notification_is_the_setup.py::test_the_free_model_approval_names_no_internal_ids`
- `tests/test_notification_is_the_setup.py::test_a_disconnected_universe_turn_is_setup_not_a_mystery`

Renamed and ported tests (their assertions now exercise the door):
- `test_card_connects_with_one_key_and_returns_unanswered_consent` ?
  `test_card_connects_with_one_key_at_door` in `test_free_source_cards.py`.
- `test_oauth_is_the_primary_action_when_the_provider_offers_it` ?
  `test_oauth_discovery_begins_at_the_door` in `test_generic_oauth_connections.py`.
- `test_a_rotation_needs_a_box_to_paste_into` ?
  `test_a_fieldless_rotation_returns_the_door` in `test_replacing_a_rejected_credential.py`.

Validation (focused only; no full affected selection):
- Requested patterns, every GUARDS test and all touched test files are covered;
  the initial 161-file Linux selection ran 3,416 cases: **3,318 passed, 96 failed,
  2 root-only skips**. Collection first exposed two stale imports of the already
  deleted model-bootstrap module; they now use the shared owner fixture.
- The five-file core repair passed **202**, with four failures subsequently fixed.
  The broader repair passed **930**, with 17 failures subsequently fixed.
- Final lifecycle/OAuth/browser/guidance run: **184 passed**.
- Final continuation/pending/model-use/broker/rotation/approval run: **362 passed**,
  with three receipt/ASGI-fixture failures fixed and covered by the last two-file
  run: **153 passed**. All original failures now have passing executions or
  ported replacement tests; none were skipped, xfailed or quarantined.
- Both root-only `test_owner_box_destroy.py` cases passed separately with
  `linux_oracle.py --as-root`: **2 passed**. A skip is not counted as a pass.
- Ruff passed on all **40 changed Python files**; JavaScript syntax, the channel
  ratchet, `git diff --check` and `openspec validate owner-daemon-in-box --strict`
  passed.

Linux logs, JUnit and selection manifests are outside the repository at
`C:/Users/Jonathan/AppData/Local/Temp/ta-3c2d-7vcbyijm/`.
The full CI selection, independent review, deployment/cutover, deployed-SHA
assertion and live naive-user pass remain with the lead. This task did not push,
merge, change branches, create a worktree or dispatch another agent.


## 3c-3 shared plans and commons publication (2026-10-16)

Implementation commits: `94f4cf683a1` and `5bce9478a58`. Billing is owner-keyed at the broker;
router calls use fenced `BILLING_PLAN`, box plan/catalog reads use `BOX_SHARED`,
and `BOX_PUBLISH` selects the author from the relay bind. The one plan writer
owns tier ordering, processor mappings and checkout attempts. Admission's
attached-database transaction creates the explicit free row. Missing rows raise
`PlanMissing`; a later center admission cannot silently recreate them.

Deleted runtime paths: router home/center billing resolution and per-center
billing writes, router seat/storage usage queries, box direct commons publishing,
box shared-preview writes, and router preview-cache writes. Billing status omits
usage; its plan limits remain. Owner quota and update-preparation reads use the
channel. Legacy non-owner subscription readers remain for task 6/6a; no census
or cutover copy was built. Published data uses the existing storage inventory;
there are no new disk names or timers.

Deleted tests, by file:
- `test_checkout_lease.py`: `test_a_fresh_legacy_claim_still_blocks`,
  `test_a_stale_legacy_claim_is_cleared_and_replaced`, and
  `test_a_corrupt_legacy_claim_fails_closed` exercised runtime migration of the
  retired per-center checkout claim. The shared store keeps one owner attempt.
- `test_billing_boundary.py`: `test_an_unreadable_state_db_reads_as_free_never_paid`
  and `test_an_unreadable_db_reports_no_end_date` asserted silent defaults.
  Replacement: `test_an_unreadable_shared_plan_fails_loudly`; event ordering,
  end-date and webhook tests now use shared rows.
- `test_account_plan_action.py`: `test_no_home_is_not_an_upgrade_on_any_surface`
  is replaced by `test_missing_plan_fails_on_every_surface`.
- `test_public_run_link.py`: `test_preview_replacement_preserves_exact_bytes`
  and `test_preview_write_refuses_linked_cache_directory` exercised the deleted
  cache writer. `test_legacy_preview_renders_only_published_component_and_caches`
  is replaced by `test_legacy_preview_is_public_and_does_not_write_at_router`.
No test file was deleted; no skip, xfail or quarantine was added.

Focused Linux validation (no full affected selection):
- The requested patterns, all structural GUARDS files and touched tests ran in
  a 119-file selection: **2,228 passed, 10 failed, 2 root-only skips**.
- Seven failures came from daemon/router fixtures missing the explicit shared
  plan or seeding the public listing in the old root. The two relay-payload
  assertions and the stale session-store raw-I/O pin were existing drift.
  The nine-file repair run passed **317**, with those last three stale
  assertions still in its pre-edit snapshot; their final file rerun is recorded below.
- Core billing/checkout/ordering/provisioning run: **165 passed**. Separate
  process, binding and router-store guard proof: **33 passed**. Provisioning's
  existing rig: **4 passed**. Root-required owner-destruction file: **5 passed**,
  including both cases skipped as unprivileged; skips are not counted as proof.
- Final fixture rerun: **178 passed**. All ten initial failures now have passing
  executions; the final seven process/placement tests also pass in the repair run.
- Ruff passed on all **29 changed Python files**. `git diff --check` and
  `openspec validate owner-daemon-in-box --strict` passed. Existing storage and
  timer inventories and the channel-agnostic guard passed in the focused selection.

No new disk-name or timer registration was required; `plans.db` and `.published`
were already classified. Receipts are outside the repository in the Windows
temporary folder (`3c3-*-linux.log`, `3c3-linux-selection.json`). The lead retains the
full CI selection, independent adversarial review (delegation was prohibited),
task 6 census/rehearsal, deployment and live naive-user proof.


## 3c CI regression repair (2026-10-17)

Local repair of the 53 cases from CI's exact affected selection. D2 keeps
owner request routing, protected approvals, account revocation and one-time key
display; D3c moves discovery and all credential submission to the shared door,
with broker custody and box completion by request_ref. Category **a** repairs
a retained product behavior; **b** ports a deleted path's test to its retained
user behavior. No tests were deleted, skipped, xfailed or quarantined.

Additional product fixes found while exercising the retained behavior:
- Door deposits capture the original login and center before token refresh,
  refuse a changed login before sending, and discard late results after an
  account switch without replaying the deposit.
- A queued close event cannot erase a newly reopened API-key card.
- Rendering default secret fields does not mutate the cached request, so
  polling does not mistake that local form state for a changed request.

The starter release follows `openspec/specs/platform-check-scope/spec.md`'s
new immutable release requirement and the manifest test's version/hash pairing:
`3-broker-connections`, SHA-256
`23f4610ff686e0a449e4fb84f82645da823dab3b67439ac1ccf46879e13d4ca7`.

### Disposition of every original failure

Original IDs below remain the index even where a replacement has a more accurate
name. No user-visible behavior was removed by this repair.

| Original failing test | Category | Evidence and change |
|---|---|---|
| `tests/test_app_browser_notifications.py::test_item_verbs_use_the_existing_answer_path[accept]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_app_browser_notifications.py::test_item_verbs_use_the_existing_answer_path[deny]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_app_browser_notifications.py::test_item_verbs_use_the_existing_answer_path[reply]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_app_pending_requests_browser.py::test_cleared_reconnect_returns_on_later_failure_and_history_can_reask` | b | Assert the explicit request center now carried by the answer; clearing, later failure and history re-ask still execute. Failure was an additional universe_id, not lost behavior. |
| `tests/test_app_pending_requests_browser.py::test_pending_requests_at_latest_and_new_arrival_answer[False-390]` | a | Ordinary answers no longer add an empty selected-chat center. The existing answer and scroll behavior assertions remain. |
| `tests/test_app_pending_requests_browser.py::test_pending_requests_at_latest_and_new_arrival_answer[False-1280]` | a | Ordinary answers no longer add an empty selected-chat center. The existing answer and scroll behavior assertions remain. |
| `tests/test_app_pending_requests_browser.py::test_pending_requests_at_latest_and_new_arrival_answer[True-390]` | a | Ordinary answers no longer add an empty selected-chat center. The existing answer and scroll behavior assertions remain. |
| `tests/test_app_pending_requests_browser.py::test_pending_requests_at_latest_and_new_arrival_answer[True-1280]` | a | Ordinary answers no longer add an empty selected-chat center. The existing answer and scroll behavior assertions remain. |
| `tests/test_app_request_rail_executes.py::test_model_setup_retry_is_visible_and_not_relayed_to_an_unpowered_agent[False]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_app_request_rail_executes.py::test_model_setup_retry_is_visible_and_not_relayed_to_an_unpowered_agent[True]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_app_request_rail_executes.py::test_patch_consent_rail_uses_protected_owner_door[True-accept]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_app_request_rail_executes.py::test_patch_consent_rail_uses_protected_owner_door[True-deny]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_app_request_rail_executes.py::test_patch_consent_rail_uses_protected_owner_door[True-clear]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_app_request_rail_executes.py::test_patch_consent_rail_uses_protected_owner_door[False-accept]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_app_request_rail_executes.py::test_patch_consent_rail_uses_protected_owner_door[False-deny]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_app_request_rail_executes.py::test_patch_consent_rail_uses_protected_owner_door[False-clear]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_credential_request_browser.py::test_card_is_reachable_in_real_needs_you_and_poll_preserves_secret[390]` | a | Polling preserves an existing row center when the response omits its envelope center, preventing card replacement and loss of the one-time API key. D2 keeps key issuance at the door. |
| `tests/test_credential_request_browser.py::test_card_is_reachable_in_real_needs_you_and_poll_preserves_secret[1280]` | a | Polling preserves an existing row center when the response omits its envelope center, preventing card replacement and loss of the one-time API key. D2 keeps key issuance at the door. |
| `tests/test_huggingface_sign_in_source.py::test_an_agent_cannot_raise_the_sign_in_ask_itself` | b | Ported to a fieldless connect ask and door link without box discovery; an agent may request a connection but cannot authorize it. |
| `tests/test_huggingface_sign_in_source.py::test_an_agent_ask_under_the_same_name_gets_no_pool_offer` | b | Assert connect at the box, then OAuth at the door and no automatic model-pool offer. D3c removed box discovery and automatic serving. |
| `tests/test_inline_owner_sessions.py::test_revoke_owner_cancels_all_connect_flows_only_for_that_owner` | b | Seed request_ref and frozen definition_json in the router flow schema; keep owner-specific revocation assertions. Failure was an obsolete positional INSERT. |
| `tests/test_multi_value_credential_ask.py::test_one_box_of_four_is_refused_not_deposited_as_the_whole_credential` | b | Assert the broker door validation error and all missing OAuth bundle names, with no deposit. D3c moved bundle validation out of box answers. |
| `tests/test_multi_value_credential_ask.py::test_a_legacy_request_stored_under_the_old_rules_is_refused` | b | Send the hostile legacy answer directly to the box approval endpoint, bypassing the test helper that now deposits at the door; retain rejection and empty-vault assertions. |
| `tests/test_needs_you_open_browser.py::test_web_request_types_open_and_remain_actionable[False-model]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_web_request_types_open_and_remain_actionable[False-question]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_web_request_types_open_and_remain_actionable[False-patch]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_web_request_types_open_and_remain_actionable[False-patch_recommendation]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_web_request_types_open_and_remain_actionable[False-optional]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_web_request_types_open_and_remain_actionable[True-model]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_web_request_types_open_and_remain_actionable[True-question]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_web_request_types_open_and_remain_actionable[True-patch]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_web_request_types_open_and_remain_actionable[True-patch_recommendation]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_web_request_types_open_and_remain_actionable[True-optional]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_native_request_types_open_in_app_or_keep_visible_handoff[question-android]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_native_request_types_open_in_app_or_keep_visible_handoff[question-ios]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_native_request_types_open_in_app_or_keep_visible_handoff[question-desktop]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_native_request_types_open_in_app_or_keep_visible_handoff[patch_recommendation-android]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_native_request_types_open_in_app_or_keep_visible_handoff[patch_recommendation-ios]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_native_request_types_open_in_app_or_keep_visible_handoff[patch_recommendation-desktop]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_native_request_types_open_in_app_or_keep_visible_handoff[optional-android]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_native_request_types_open_in_app_or_keep_visible_handoff[optional-ios]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_needs_you_open_browser.py::test_native_request_types_open_in_app_or_keep_visible_handoff[optional-desktop]` | a | Do not attach an empty selected-chat center to ordinary answers. Existing web/native open, accept, clear and browser-handoff assertions are unchanged. |
| `tests/test_onboarding_mcp_session_recovery.py::test_deposit_cannot_replay_under_a_different_login[401-False]` | b | Exercise the retained keyless MCP connect under the same handshake/login race. Secret deposit no longer uses MCP. Added Chromium door-deposit tests for refresh and delayed success/401/404 after login changes. |
| `tests/test_onboarding_mcp_session_recovery.py::test_deposit_cannot_replay_under_a_different_login[404-False]` | b | Exercise the retained keyless MCP connect under the same handshake/login race. Secret deposit no longer uses MCP. Added Chromium door-deposit tests for refresh and delayed success/401/404 after login changes. |
| `tests/test_onboarding_mcp_session_recovery.py::test_deposit_cannot_replay_under_a_different_login[404-True]` | b | Exercise the retained keyless MCP connect under the same handshake/login race. Secret deposit no longer uses MCP. Added Chromium door-deposit tests for refresh and delayed success/401/404 after login changes. |
| `tests/test_onboarding_mcp_session_recovery.py::test_delayed_handshake_cannot_send_a_secret_under_the_next_login` | b | Exercise the retained keyless MCP connect under the same handshake/login race. Secret deposit no longer uses MCP. Added Chromium door-deposit tests for refresh and delayed success/401/404 after login changes. |
| `tests/test_onboarding_mcp_session_recovery.py::test_stale_repair_handshake_cannot_discard_the_new_login_session` | b | Exercise the retained keyless MCP connect under the same handshake/login race. Secret deposit no longer uses MCP. Added Chromium door-deposit tests for refresh and delayed success/401/404 after login changes. |
| `tests/test_request_answer_routing.py::test_subagent_card_answer_in_real_browser[app_sheet]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_request_answer_routing.py::test_subagent_card_answer_in_real_browser[inline_card]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_request_answer_routing.py::test_subagent_card_answer_in_real_browser[desktop]` | a | Removed the selected-chat fallback from ordinary answers; an explicit request center is retained. Missing queueScope had thrown before submission. D2 keeps request/agent answer routing. |
| `tests/test_request_fields_are_answerable.py::test_the_documented_oauth1a_names_are_the_ones_the_deposit_reads` | b | Keep OAuth bundle-name parity and assert served tools direct secrets to the shared door; removed the expectation that box tool guidance teaches secret submission. |
| `tests/test_starter_manifest.py::test_published_starter_release_is_immutable` | a | Publish version 3-broker-connections and pin its computed manifest digest together, following platform-check-scope. Version 2 content was changed by 3c; its digest was not silently repinned. |
| `tests/test_starter_ta_tasks.py::test_connect_my_google_calendar_offers_owner_sign_in_through_ta` | b | Calendar ask retains public endpoints/scopes and opens the owner door; assert no box-discovered provider_id or private secret. D3c places discovery at the door. |

### Local commits and focused validation

- `73fefaada19`: new immutable starter release.
- `85dd0f542a4`: port superseded connection tests to the D3c contract.
- `38ca04fbfaa`: repair request cards, routing and login-bound door submission.
- `6cac7542153`: clear the previous one-time key on immediate same-sheet reopen.

All 53 original cases passed in the 13-file regression run (287 passed; its
three failures were new test-interception synchronization errors, since fixed).
The final door/OAuth rerun passed 49. A further two-file run passed 163,
including the new four-field Chromium proof, and found only two stale
empty-center expectations in `test_onboarding_app.py`; after their update,
that complete file passed 147.

The 99-file owner/broker/router/structural selection passed 1,867, with those
same two onboarding expectation failures and two root-only skips. The final
147-pass file run covers both failures. The complete owner-destruction file
passed 5 under `--as-root`, including both skipped cases; skips are not
counted as passing proof. Ruff on every touched Python file, JavaScript syntax,
`git diff --check`, and strict OpenSpec validation passed.

No reviewer was dispatched because delegation was explicitly prohibited.
These are local commits only; no push, merge, branch change, host git configuration
change, worktree creation or deployment was performed.

### Full original CI chunks

Each uses the original 155-file list and order from the supplied scratch
folder, through `python scripts/linux_oracle.py -- -q --no-header
-p no:cacheprovider <files>`. The selector still reports 1,196 files. Existing
skip counts are reported separately and are not credited as passing cases.

| Chunk | Passed | Failed | Existing skips | Elapsed |
|---|---:|---:|---:|---:|
| aa | 2,999 | 0 | 0 | 833.45s |
| ac | 3,113 | 0 | 2 | 1222.75s |
| ad | 3,491 | 0 | 8 | 592.35s |
| ae | 3,192 | 0 | 5 | 608.88s |
| af | 2,979 | 0 | 0 | 715.18s |
| ag | 2,362 | 0 | 1 | 580.76s |

The immediate-reopen refinement was made after the full chunks had taken
snapshots of the original 53 repairs. Its final two-file Chromium run passed
83; the final combined 112-file run covered every requested regression file and
every owner/broker/router/structural file on the final implementation:
**2,160 passed, zero failed, two root-only skips in 432.53s**. Both skipped
owner-destruction cases passed in the separate five-pass root run above.
All six original failing chunks completed with **18,136 passed, zero failed,
16 pre-existing skips**. No skip, xfail or quarantine was added. The interrupted first
`ae` run held the pre-repair onboarding assertions and was restarted from the
corrected onboarding-assertion snapshot; it is not counted as a completed
validation run.


Receipt files are outside the repository under
`C:/Users/Jonathan/AppData/Local/Temp/`:
`3c-regression-chunk-{aa,ac,ad,ae,af,ag}.log`,
`3c-regression-final-selection.json`, `3c-regression-final-selection.log`,
`3c-regression-root.log`, and `3c-regression-reopen-final.log`.
The original chunk lists and original failure logs remain unchanged in the
supplied scratch directory.


## D3c test hygiene: restore 24 retained tests

Compared each named test with `conv/c6-owner-daemon-s3` and the D3c decisions.
All 24 still cover retained behavior, so none was deleted. No product code,
gate, skip, xfail or quarantine changed. The counts below use the hygiene
gate's own assertion counter (including expected exceptions and assertion helpers).
The retained names preserve the base-to-head comparison even where they mention
retired executor shapes.

| Test (`tests/` prefix, `.py` suffix omitted) | Removed in 3c | Replacement checked now | Base / repaired count |
|---|---|---|---|
| `test_billing_boundary::test_the_end_date_is_stored_and_cleared_with_the_tier` | Deleted metadata row after clearing the end date. | Owner-keyed SQL row retains paid tier with SQL NULL end date; another owner with no row raises PlanMissing. | 3 / 4 |
| `test_connect_free_ai_screen::test_sign_in_source_card_is_one_tap_to_the_platforms_own_ask` | Visible failure detail after unavailable sign-in. | Shared-door sign-in failure displays sign_in_unavailable and keeps retry enabled. | 9 / 9 |
| `test_conversation_failure_history::test_exception_to_code_and_fixed_notice_never_copy_diagnostics` | Native terminal-text classification and its notice. | HTTP attempt's typed rate-limit class is recognized; a diagnostic string masquerading as a class becomes unknown. Existing fixed-notice secrecy checks remain. | 9 / 9 |
| `test_engine_mcp_server::test_sandboxed_config_engine_mcp_off_by_default` | CLI tool allow/deny entries. | Disabled engine configuration carries neither actor nor graph routing identity. | 3 / 3 |
| `test_engine_mcp_server::test_sandboxed_config_on_when_all_conditions_met` | Seven CLI allow/deny and resource-reader checks. | Exactly four model tools; publish/remix remain outside backend authority; no resource-reader tool or CLI allow/deny fields; configured timeout survives. Owner/graph and backend capabilities remain checked. | 11 / 11 |
| `test_engine_mcp_server::test_connect_compute_is_exposed_in_both_provider_allowlists` | Provider-specific prefixed bash allowlist. | A connect_compute-only grant exposes bash so ta can reach registration without exposing backend handles directly. | 5 / 5 |
| `test_engine_mcp_server::test_served_allowlists_do_not_drift` | Identity of a second executor's tool tuple. | An explicit empty backend grant exposes no model tools. Existing canonical four-tool checks remain. | 4 / 4 |
| `test_first_contact::test_newborn_without_a_credential_gets_onboarding_not_a_raw_error` | Legacy advertised-handle and pasted-key setup guidance. | Setup names the connections door and omits the retired byo_api_key path. | 8 / 8 |
| `test_generic_oauth_connections::test_sign_in_round_trip_through_the_real_callback` | Immediate box answer/continuation and answer-delivery origin rows. | Broker grant created by the real callback binds the expected owner, center and connection; request_ref and token-free response remain checked. | 19 / 19 |
| `test_http_connection_provisioning::test_unsupported_auth_scheme_rejected` | Response's old allowed-scheme list. | Unsupported scheme leaves neither custody nor a broker connection row. Keyless none is supported and is no longer a negative case. | 3 / 3 |
| `test_inline_model_connect::test_popup_callback_and_once_only_server_held_exchange` | Box answer embedded in confirmation response. | Broker exchange receives the callback code and the verifier matching the stored flow challenge; once-only and secret-free response checks remain. | 11 / 12 |
| `test_manifest_connection_inventory::test_readiness_and_inventory_check_independent_http_members` | Native fallback survival and removal. | After either HTTP definition loss or grant revocation, restoring that fixture member restores both readiness and scheduler inventory without executor probing. | 9 / 9 |
| `test_model_options_composition::test_actual_owned_catalog_projects_without_enabling_serving` | Native-first ranking and zero native calls. | Advisory order contains only the owned HTTP source and leaves scheduler serving inventory empty. | 11 / 11 |
| `test_oauth_discovery_cimd::test_unlisted_mcp_through_connect_card` | Box-discovered OAuth source/scopes. | Box ask makes zero discovery requests; the real door round trip still discovers and uses the unlisted endpoint and scopes. | 20 / 20 |
| `test_one_agent_definition::test_the_guard_covers_every_registered_provider_kind` | Two native provider kinds in the registry. | Guard covers actual installed HTTP wire protocols; the native provider registry is absent and the retired process-launch module is not importable. | 3 / 3 |
| `test_one_agent_definition::test_the_turn_gives_every_executor_the_same_instructions` | Equality with native executor prompt. | HTTP round retains the agent's original instructions as well as the conditional request-budget line. | 2 / 2 |
| `test_open_serving_bind::test_open_provider_authorizes_and_reserves_a_served_turn` | Optional subscription snapshot field equals None. | The served authority has no snapshot-directory field at all; connection-grant and budget checks remain. | 5 / 5 |
| `test_owner_broker_egress::test_vault_and_provider_cli_refuse` | CLI spawn confinement exception. | Retired process-launch module is not importable; box custody access still raises PermissionError. | 2 / 2 |
| `test_refresh_session_seal::test_the_key_is_scrubbed_at_IMPORT_before_any_store_call` | Seal key absent from deleted CLI environment builder. | Real session file contains no plaintext refresh token; import-time environment scrubbing and configured-key readback remain checked. | 7 / 7 |
| `test_replacing_a_rejected_credential::test_a_scheme_with_no_pasted_key_is_refused_in_both_places` | Old direct-rotation detail text. | Refused keyless rotation at door/request rail leaves the custody vault empty. | 4 / 4 |
| `test_run_provider_session::test_foreground_run_launches_selected_open_provider_and_settles_once` | No calls to subscription snapshot helper. | Subscription snapshot API is absent altogether; real foreground HTTP launch, credential-free wire and settlement remain checked. | 8 / 8 |
| `test_served_authority_shared_chain::test_execution_uses_live_connected_chain` | Native provider selected in subscription branch. | Ready HTTP execution has connection_grant authority in addition to the expected owner and connected-request budget. | 6 / 6 |
| `test_vault_account_deletion_guard::test_deletion_waits_for_inflight_vault_persist_then_erases` | Timing of daemon tombstone relative to vault writer. | After broker erasure waits for the in-flight writer and removes custody, a later writer is rejected by the broker tombstone. | 5 / 5 |
| `test_work_agent_allowance::test_actual_foreground_receipt_uses_parent_allocation_ceiling` | Native-only admitted source. | HTTP receipt is bound to the expected command center, alongside principal, source and parent allocation limits. | 11 / 11 |


Validation for this repair:
- Final full 20-file Linux oracle selection: **624 passed, zero failed, zero skipped**
  in 147.78s, Python 3.11.17, bubblewrap 0.12.0, UID 1001. Command:
  `python scripts/linux_oracle.py -- -q --no-header -p no:cacheprovider <all 20 files above>`.
- Ruff on all 20 changed Python files and `git diff --check`: passed.
- The first oracle run found two incorrect test assumptions: reusing a revoked grant
  ID and requiring an agent codec for text-only HTTP dialects. Both were corrected;
  the complete final run above covers the corrections. No product defect was found.
- Full Linux receipt: `C:/Users/Jonathan/AppData/Local/Temp/3c-hygiene-linux-final.log`.
  Gate verification runs after commit against committed HEAD; its receipt is
  `C:/Users/Jonathan/AppData/Local/Temp/3c-hygiene-gate.log`.


## 6a part 1: compatibility cleanup (2026-10-17, incomplete)

This local step completes the pre-C6 cleanup subset below. It does not complete
6a part 1, and it is not deployed. No slice 6 migration or protected deploy file
was edited. The remaining work is explicit so this step can be committed alone.

Deleted:
- The consent-move startup invocation and interrupted-move state machine in
  `storage_layout.py`. Startup validates existing
  committed markers or initializes a fresh marker; it never relocates consent
  files or makes `.premigration` copies. Existing sidecar consent and forgery
  checks remain. The consent-move module and legacy path helper remain unchanged:
  protected `agent_file_migration.py` imports its `MIGRATING` constant. The first
  broad Linux run exposed that dependency; deleting it now would break slice 6's
  protected migration. No constant-only compatibility shim was introduced.
- `universe_soul`'s `PROGRAM.md` reader, path helper and constant; the API's
  premise mirror reads/writes and implicit legacy fantasy-loop selection;
  evaluation's old-file fallback and the fantasy package's old reads and
  auto-generation mirror. Premise create/read/inspect/status/ledger
  behavior now uses `soul.md`. Old `PROGRAM.md` content is ignored; users can
  re-create their premise and explicitly declare their loop. No new migration.
- First-contact's `OperationalError` fail-open and the dead `boxes.lifecycle`
  `owner_layout` helper. Account destruction no longer scans a legacy host home
  and refuses residual bytes; its durable receipt validation is preserved.

Tests for surviving premise behavior now read/write the soul profile. The
missing-host admission test replaces its deleted owner-layout assertion with
an assertion that missing host admission never mutates broker admission.
No surviving test has fewer assertions; no skip/xfail was added. Added checks
cover missing deletion tables, stale premises and implicit loop selection.

Guard subset: `test_router_store_guard` now statically restricts `owner_router`
to its six justified shared-control-plane imports and rejects direct file/SQLite I/O.
Mutation examples prove owner-store imports and owner file reads are caught.
The full D7 repository-wide mode-fork scan is NOT complete: the forks below
must be deleted first, rather than grandfathered into an allowlist.

Whole tests retired (startup consent relocation and residual legacy-byte
refusal only):

`tests/test_box_lifecycle.py`:
- `test_destroy_refuses_residual_legacy_bytes`

`tests/test_platform_state_outside_command_centers.py`:
- `test_a_dot_entry_at_the_root_is_not_a_command_center`
- `test_a_link_at_the_old_name_is_set_aside_not_followed`
- `test_an_interrupted_move_resumes`
- `test_both_copies_present_is_a_refusal_naming_the_command_center`
- `test_crash_after_legacy_rename_resumes`
- `test_crash_after_target_creation_resumes_without_legacy_grants`
- `test_migration_preserves_reset_for_a_home_without_consents`
- `test_the_move_carries_no_row_and_sets_the_old_file_aside`
- `test_the_move_is_idempotent`

`tests/test_storage_layout.py`:
- `test_migration_fences_older_admission_before_creating_target`

Remaining in part 1:
- Delete `storage/platform_state_move.py` and `legacy_consents_db_path` after
  slice 6 removes the protected migration's constant import.
- All B1-B25 runtime-fork/bridge/backend work still present in `compat-audit.md`
  (except already completed earlier slices and the dead owner_layout helper
  removed here). Delete the session/file binding route, BoxExecutor/start_exec,
  and frontend/production_files/file_worker/exec_worker/executions/gvisor_files
  with their callers and their old-only tests. The owner daemon's local tools
  and persistent boxhostd lifecycle remain the target.
- Delete the legacy launcher selector together with the frontend. This step
  intentionally does not leave a dangling entrypoint while that backend exists.
- Complete D7's repository-wide static mode-fork scan with only justified
  router/owner admission boundary checks; do not allow remaining owner-work forks.
- Finish the corresponding owner-mode test ports and rerun focused validation.

Part 2, after slice 6: remove the one-shot box-layout/activity/agent-file and
final census/migration modules after they run, their deploy wiring, and old-layout
deploy/backup/quota/rehearsal branches. `.premigration` copying in the protected
box migration remains owned by slice 6, then deleted with that migration.

Diff for this compatibility subset: **184 product lines removed, 50 added in
10 product files** (including the two fantasy-package readers); no whole
product module was deleted because the protected migration still imports the
consent-move module. Across product, tests and this change record the first
commit is 25 files, 463 deletions and 281 additions. `scripts/check_shapes.py`
and every slice 6 protected file are unchanged.

Validation:
- Ruff on every changed Python file, diff whitespace checks and commit hooks:
  passed. The hygiene gate against `conv/c6-owner-daemon-s5` exited **0** after
  the product commit, using `Registry row(s): S8` and the retired-test reason.
  Its inventory is four added test functions and 11 whole-test removals;
  surviving assertion counts never decreased.
- Required broad selection: **133 files**, all touched tests then present,
  `test_owner_*`, `test_broker_*`, `test_box*`, `test_router_store_guard`, and
  every GUARDS key from `ci_structural_guards.py`, through the Linux oracle.
  Initial result: **2,436 passed, three failed, 11 existing skips**. Two failures
  exposed dangling imports (the protected migration's consent-module constant
  and the fantasy package's premise helper); both were repaired. The third
  was the root control proof run as UID 1001; it passed in its root venue.
- Final focused oracle, all **13 touched test files** plus the protected box
  migration's tests: **555 passed, five existing skips**, no failures, 127.22s.
  Four skips require rsync; the fifth is the ASP stub when the real ASP engine
  is installed. The final SQLite mutation example was added after that
  snapshot; the complete latest router guard is covered by the root run below.
- Final root oracle: **43 passed, zero skipped**, 2.89s. This includes all 33
  latest router-store/structural guard cases, the control-socket proof, four
  real-copy cases, two quota cases, the host marker case and two descriptor
  erasure cases. Rsync was installed only inside the disposable oracle, which
  was closed afterwards. Across the required selection, the only unexecuted
  existing cases are the ASP stub with the real engine installed and the local
  driver disk-bound case when that driver declares no disk bound; neither is
  counted as a pass.
- An extra whole-file root run produced **35 passed, four skipped and one
  failure**: the live-WAL preflight read test changes SHM bytes when root can
  write its read-only fixture. That case passed in both unprivileged runs;
  root is the wrong venue for its permission proof. No protected migration
  code or test was changed to accommodate it.

Receipts outside the repository:
`C:/Users/Jonathan/AppData/Local/Temp/c6-s6a-validation/` (selection, broad log,
JUnit and hygiene body), `c6-s6a-final-focused/` (final focused log/JUnit), and
`c6-s6a-root/s6a-root-final.log|xml` (final root proof), under the same Temp root.
No deployment, push, merge, branch switch or production access occurred.


## 6a part 1b: owner file/engine helpers (2026-10-17, partial)

This is a coherent local subset, **not completion of the requested 1b pass**.
The full D7 mode-fork scan is still outstanding; no remaining owner-work fork
has been added to an allowlist. The existing router import/file-access guard
is unchanged. No protected migration/deploy file or old-path shape probe changed.
No production access, deployment, push, merge, rebase or branch switch occurred.

Five runtime forks removed:

| File:function | Surviving side |
|---|---|
| `universe_files.py:ensure_data_directory` | Create directories locally through the existing descriptor/no-follow implementation; delete the shared session/file-binding branch. |
| `engine_mcp_http.py:box_engine_socket` | The owner engine uses loopback. Delete this helper, the host socket directory constructor, and the second server's startup/shutdown lifecycle. |
| `engine_mcp_http.py:start_engine_mcp_http_servers` | Start inside the owner process; delete shared-daemon protection/adoption. |
| `providers/base.py:get_sandbox_status` | Owner work already runs in its box; delete configured per-call-provider availability selection. |
| `owner_activity.py:owner_work.wrapped` | Always hold the owner work/drain gate, including on exceptions. |

The retained `BOX_ENGINE_HOST` constant still serves the deferred per-call
backend's URL projection. Its consumer (`broker/public_egress.py`) is unchanged;
its now-unused host engine-socket route belongs with that backend's next-pass
retirement. `BoxExecutor`, `start_exec` and all six deferred backend modules
are untouched. Session/file-binding consumers elsewhere remain pending.

Tests: `test_engine_mcp_routes` keeps every assertion and now raises if engine
startup tries shared-daemon protection, additionally checking that neither the
second server nor the host socket directory exists. The stale raw-I/O exemption
for the deleted socket helper was removed from `test_universe_path_io_guard`;
its ratchet assertions remain intact. Five new `test_owner_local_paths` tests
prove local/idempotent directory creation beneath an admitted center without
mode/binding reads, link
refusal, provider-independent sandbox status, work/drain admission and gate
release on failure. **No whole tests retired in this subset; no skip/xfail.**

Remaining 1b work (all unchanged by this subset):
- Tools and sessions: `control_plane/binding.py:file_binding`,
  `control_plane/session.py:_binding`, `universe_tools.py:_box`, `_operation_id`,
  `read_file`, `_bash`, `agent_loop/tool_session.py:open_loop_tools`,
  `providers/call.py:make_interactive_agent_turn`,
  `engine_mcp_server.py:_universe_tool`; delete their shared-only bindings and
  port surviving tests. Other local file-I/O methods still consult file_binding.
- Broker placement: `broker/client.py:_owner_mode`, `_connect` and all peer-check
  uses; `broker/aclient.py:_connect`; `broker/supervisor.py:get_supervisor`;
  `broker/owner_identities.py:center_admission`, `admitted_owner`;
  `broker/owner_sources.py:backfill_metering`; `boxes/lifecycle.py:admit_center`.
  Router callers need explicit router-side operations; owner callers need only
  the startup-admitted relay. Do not replace these forks with runtime flags.
- Shared/owner services: `api_keys.py:current_store`,
  `onboarding/owner_sessions.py:decision_session`, `require`,
  `commons_catalog.py:publish_definition`, `get_definition`, `list_definitions`,
  `_require_definition`, and `commons_bundles.py:next_version`.
- Owner data and execution: `account_deletion.py:delete_account`,
  `command_center_update_executor.py:_connect`, `_quota_inputs`,
  `storage_accounting.py:_center_bytes`, `universe_owner.py:account_type_of`,
  `account_type_for_universe`, `control_plane/tick.py:poll_once`,
  `control_plane/continuations.py:tick`,
  `providers/model_call.py:_dispatch_args`,
  `providers/api_key_http_provider.py:_connection_context`,
  `storage/agent_request_usage.py:UsageStore.create`,
  `storage/subscription_state.py:state_db_path`, `get_tier`, `read_tier`, `get_plan`,
  `storage/webhook_hooks.py:mint`, `_revoke_routes`.
- Admission/readers: `credential_vault.py:_credential_root`,
  `platform_runtime_provenance.py:_owner_observation`, and owner startup checks
  in `agent_loop/owner_tools.py` and `owner_ingress.py`; distinguish admission
  requirements from shared fallback behavior. The router entry's app-placement
  read and owner_runtime's internal state checks are legitimate boundaries.
- Finish D7's repository-wide alias-aware static scan with an explicit justified
  boundary-only allowlist, port/retire all remaining affected tests, and validate.
  No allowlist expansion was used to make this partial change appear complete.

Validation receipts for this subset are under
`C:/Users/Jonathan/AppData/Local/Temp/s6a1b-validation/`.
`initial.log/xml` describe a discarded intermediate tree, not this commit.
Validation for product commit `384b9305d6b` and the final fixture port:
- Required selection: **110 files, 2,090 cases**, including every GUARDS-key
  file, all requested owner/broker/session/deletion files and the touched tests.
  Unprivileged broad run: **2,085 passed, three failed, two skipped**. The three
  failures were the root-only control proof and two incomplete fixtures: the
  new directory test omitted its admitted center, and the wiki attribution
  test relied on the deleted shared branch's binding double to provision its
  configured wiki root. Both fixtures now explicitly provision those roots;
  the existing wiki success/attribution assertions are unchanged.
- Final focused oracle: **92 passed, zero skipped**, including all new helper
  tests, engine-route tests and the router/file-I/O guards.
- Final wiki fixture recheck, the whole `test_storage_registry.py` file:
  **13 passed, zero skipped**.
- Root oracle: **three passed, zero skipped**: the control-socket proof and
  both descriptor/UID-erasure cases skipped by the unprivileged run.
- All **2,090 required case identities ultimately passed** across the broad
  run and these targeted follow-ups. `coverage-summary.json` asserts the
  follow-ups cover only identities in that required selection and leave no
  failed, errored or skipped case. This is combined venue coverage, not a
  claim that the initial broad invocation exited zero.
- Heavy selection: all **34 files, 1,358 passed, zero skipped**.
- Ruff on every changed Python file, `git diff --check`, commit hooks and
  strict OpenSpec validation pass. The hygiene gate against
  `conv/c6-owner-daemon-s5` exits **0** using `Registry row(s): S8` and the
  cumulative retired-test reason. Its 11 whole-test removals are the prior
  pass's retirement inventory; no surviving assertion count was reduced.
- No additional tests were retired or quarantined, and no skip/xfail was added.

The validation/fixture follow-up changes no product behavior beyond
`384b9305d6b`. D7's full static scan and the remaining 1b fork inventory above
are still incomplete.


## 6a part 1c: classified deletion subset (2026-10-17, incomplete)

This pass completes **20 classified rows**: 15 LEGACY_SHARED, four
UNREACHABLE_ROLE and the unused admission-wrapper UNSURE. All 29 ROLE_DISPATCH
rows remain. The other 21 rows and D7 enforcement remain unfinished; the 6a
dual-path bullet is deliberately unchecked. No slice 6 file, old-path probe,
production surface, branch configuration or remote Git state changed.

Every deletion was checked against its implementation and production callers.
The classification was confirmed for all sites changed here; no listed legacy
site was reclassified as role dispatch.

| File:function (under tinyassets/) | Disposition |
| --- | --- |
| `account_deletion.py:delete_account` | Keep local erasure; remove host destroy, broker erasure, billing and identity phases and their unused helpers. |
| `agent_loop/owner_tools.py:OwnerTools.__init__` | Keep startup-admitted tools; remove the unreachable None-role arm. |
| `agent_loop/tool_session.py:open_loop_tools` | Keep local tools_for/enable_ta; remove injected binder and exec-provider bridge. |
| `boxes/lifecycle.py:admit_center` | Keep bound ADMIT_CENTER; remove host provisioning. |
| `broker/owner_sources.py:backfill_metering` | Keep broker capabilities/descriptors; remove role test AND obsolete private host metadata input. |
| `commons_bundles.py:next_version` | Keep BOX_SHARED version reads. |
| `commons_catalog.py:list_definitions` | Keep BOX_SHARED catalog reads; broker _list_definitions remains. |
| `commons_catalog.py:_require_definition` | Keep missing public-definition fetch/copy, existing-row early return and final missing error. |
| `control_plane/binding.py:file_binding` | Keep None for local owner files; remove shared session lookup/refusal routing. |
| `control_plane/session.py:_binding` | Delete helper and binding-only starter scope/cancellation; load locally. Remove parent binding fields and both production carriers. |
| `engine_mcp_server.py:_universe_tool` | Keep local operation; remove binding lookup, binding-required rejection and placement scope. |
| `onboarding/owner_sessions.py:decision_session` | Keep exact forwarded protected proof; remove router-local transaction arm. |
| `owner_ingress.py:OwnerIngress.__call__` | Keep admitted-principal comparison and secret/identity checks; remove unreachable runtime-None role alternative. |
| `providers/call.py:make_interactive_agent_turn` | Always use ThinLoopChatAdapter. |
| `storage_accounting.py:_center_bytes` | Keep local owner-root walk; delete host-provider measurement and its helper. |
| `universe_tools.py:_box` | Keep local OwnerTools; remove BoxExecutor/session binding. |
| `universe_tools.py:_operation_id` | Keep UUID operation IDs. |
| `universe_tools.py:read_file` | Keep bounded local read_content image handling; delete exec-based _read_image. |
| `universe_tools.py:_bash` | Keep local enable_ta; remove provider TurnBridge arm. |
| `broker/owner_identities.py:center_admission` | UNSURE resolved: delete the unused wrapper after its sole production caller disappears. |

The tool tests now use the actual local OwnerTools/OwnerBoxd and a real local
bash/ta process. The default test double supplies startup admission at the
local-tools seam; it does not invoke the deleted BoxExecutor binder. The
journal-operation, tool inventory, steering, engine routing, hook execution,
image and local file behavior survives. The package fixtures inject only the
published-read transport and exercise the real broker catalog readers. Pure
catalog store tests call those surviving readers directly. Root/row erasure,
blocked deletion, tombstones, staging refusal and retry assertions remain;
local deletion receipts no longer claim a prior host destruction. On linked
staging refusal the original home remains intact; on partial staging the bytes
stay in the recorded staging directory until retry succeeds. No surviving
assertion count was reduced and no skip/xfail was added.

UNSURE evidence and decisions:

- `broker/owner_identities.py:center_admission`: the only production call was
  `boxes/lifecycle.py:admit_center`'s host arm. The box calls ADMIT_CENTER on the
  bound relay directly. Deleting that arm leaves no caller, so delete the whole
  wrapper. Broker CENTER_ADMISSION remains an independently tested protocol
  operation; the real-daemon retirement test now sends that operation directly.
- `broker/client.py:BrokerClient.owner_policy`: classify as LEGACY_SHARED.
  `onboarding_routes` registers `/app/api/policy`; `owner_router.forwarded_path`
  forwards it and `create_streamable_http_app` removes that route from the router.
  `_handle_policy` reads the private home and pending proposal in the BOX, then
  calls `owner_policy_operation`. Other callers are owner policy_rules,
  pending_requests and ta_capabilities. Protected authorization still lives at
  the shared door/broker: the router forwards the session proof and broker
  `PolicyStore._session` verifies the live router session row before write or
  issue_once. The D3 phrase about protected writes describes that authorization
  boundary, not a second host execution of the owner handler. Removing this
  client's host arm and porting its protocol fixtures is **still pending**.

D7 status: the router import/direct-file-access checks remain intact. The full
mode scanner is **not installed**: 21 unretired sites still violate it. The exact
29 reviewed role-boundary entries below are the allowlist to use when finishing
it; no legacy site is grandfathered. Internal owner_runtime lifecycle state is
owned by that module; branchless principal/root/tools reads are not mode forks.

| File:function | One-line reason |
| --- | --- |
| `api_keys.py:current_store` | Router opens its key store; the box uses credential RPC. |
| `broker/client.py:BrokerClient._owner_mode` | Shared management methods choose router or bound box transport. |
| `broker/client.py:BrokerClient._connect` | Router management uses the host socket; boxes use the relay. |
| `broker/client.py:BrokerClient.ledger_query` | Router connection completion and box grant reads share this client. |
| `broker/client.py:BrokerClient.connection_catalog` | Router connections page and box catalog both query the broker. |
| `broker/client.py:BrokerClient.disconnect` | Both the router door and box can revoke a connection. |
| `broker/client.py:BrokerClient.http_connect` | Router deposits secrets; boxes connect without secrets. |
| `broker/owner_identities.py:admitted_owner` | Router selects an admitted center; boxes use bound identity RPC. |
| `broker/supervisor.py:get_supervisor` | Router owns the process supervisor; boxes receive a channel adapter. |
| `commons_catalog.py:publish_definition` | Box publishes over RPC; broker writes its published store. |
| `commons_catalog.py:get_definition` | Router reads published data; box reads it over the bound channel. |
| `credential_vault.py:_credential_root` | Broker owns raw custody; direct box access must refuse. |
| `onboarding/owner_sessions.py:require` | Router resolves cookies; boxes validate forwarded protected proof. |
| `platform_runtime_provenance.py:_owner_observation` | Box provenance comes from relay admission; router observes its process. |
| `universe_server.py:create_streamable_http_app` | One factory places routes, lifespans and authentication by process role. |
| `api_keys.py:ApiKeyStore.db` | Router opens its key store; direct box access must refuse. |
| `auth/provider.py:OAuthProvider.__init__` | Router constructs OAuth storage; direct box access must refuse. |
| `auth/provider.py:OAuthProvider._connect` | Router opens OAuth storage; direct box access must refuse. |
| `billing/plan_store.py:BillingPlans.__init__` | Router billing uses broker plans; a box cannot construct this writer. |
| `broker/owner_reads.py:Plans.__init__` | Broker opens plans; boxes must use the bound channel. |
| `broker/owner_reads.py:publish` | Broker writes published data; boxes must use the bound channel. |
| `broker/policy.py:PolicyStore._session` | Broker verifies router session evidence; box cannot open that store. |
| `connection_oauth/pkce.py:flows_db` | Router owns OAuth flows; direct box access must refuse. |
| `onboarding/canary_sign_in.py:_limits` | Router owns sign-in limits; direct box access must refuse. |
| `onboarding/native_sign_in.py:flows` | Router owns native sign-in flows; direct box access must refuse. |
| `onboarding/owner_sessions.py:store` | Router owns sessions; box uses forwarded proof. |
| `onboarding/session_store.py:store_dir` | Router owns refresh sessions; direct box access must refuse. |
| `router_credentials.py:DeletedOwners.__init__` | Router owns deletion tombstones; direct box access must refuse. |
| `router_credentials.py:HookOwners.__init__` | Router owns hook digests; boxes reach it through credential RPC. |

Retirement inventory for this pass (the earlier 6a retirement inventory still
applies). These are tests of removed shared-session/provider routing, remote
handle generations/cancellation, unused wrapper parsing, private host metadata,
host destruction, or owner-side broker/key/billing/identity cleanup. The
surviving local behavior is covered by the ported suites and owner process
proofs. Renamed ports are not counted as retirements.

- `tests/test_agent_loop_tool_session.py::test_a_granted_box_tool_with_no_box_is_refused_before_anything_opens`
- `tests/test_agent_loop_served_chat.py::test_unknown_box_outcome_holds_the_turn_and_nothing_replays`
- `tests/test_agent_loop_served_chat.py::test_no_box_provider_refuses_before_any_tool_runs`
- `tests/test_agent_loop_served_chat.py::test_session_handle_is_not_rebound_for_tools`
- `tests/test_agent_loop_served_chat.py::test_no_binding_keeps_the_engine_tool_adapter`
- `tests/test_control_plane_binding.py::test_session_binds_before_real_agent_loader_without_host_directory`
- `tests/test_control_plane_binding.py::test_reconnect_reuses_durable_session_identity_and_does_not_cancel`
- `tests/test_control_plane_binding.py::test_only_explicit_owner_stop_ends_the_box_session`
- `tests/test_control_plane_binding.py::test_missing_stable_identity_refuses_before_files`
- `tests/test_control_plane_binding.py::test_foreign_parent_binding_refuses_before_loading`
- `tests/test_control_plane_binding.py::test_explicit_child_binding_survives_executor_handoff`
- `tests/test_control_plane_binding.py::test_child_cannot_replace_its_live_parent_handle`
- `tests/test_control_plane_binding.py::test_owner_box_layout_refuses_missing_provider_before_host_read`
- `tests/test_control_plane_binding.py::test_bound_file_scope_rejects_another_owner_root`
- `tests/test_control_plane_binding.py::test_mixed_agent_generations_refuse_before_executor`
- `tests/test_control_plane_binding.py::test_named_agent_uses_one_snapshot_for_settings_and_instructions`
- `tests/test_control_plane_binding.py::test_roster_change_between_pages_refuses_before_agent_files`
- `tests/test_control_plane_binding.py::test_busy_roster_does_not_look_like_an_empty_agent_list`
- `tests/test_center_admissions.py::test_client_distinguishes_a_never_admitted_retire`
- `tests/test_box_lifecycle.py::test_committed_admission_refuses_missing_host_and_invalid_layout`
- `tests/test_broker_model_grants.py::test_existing_grants_backfill_from_broker_state[platform-*] (two parametrizations: obsolete host definition input)`
- `tests/test_account_deletion.py::test_failed_two_pass_owner_tree_keeps_the_binding_and_retry_resumes`
- `tests/test_account_deletion.py::test_billing_and_identity_failures_are_reported_not_hidden`
- `tests/test_account_deletion.py::test_an_unconfigured_identity_provider_is_unfinished_work`
- `tests/test_account_deletion.py::test_an_unreachable_payment_processor_is_unfinished_work`
- `tests/test_account_deletion.py::test_account_deletion_erases_only_its_authenticated_connections`
- `tests/test_account_deletion.py::test_account_deletion_erases_only_its_api_keys`
- `tests/test_box_lifecycle.py::test_destroy_refuses_receipt_for_another_center`
- `tests/test_box_lifecycle.py::test_destroy_uses_c12_stable_id_and_validates_receipt`

Additional retired tests exposed by the broad run:

- `tests/test_broker_account_erasure.py::test_actual_account_deletion_calls_broker_after_tombstone`
- `tests/test_broker_account_erasure.py::test_unavailable_and_lost_ack_are_unfinished_not_success`

Both exercised delete_account's removed shared broker phase. Its unused
`broker/account_erasure.py:erase_account` client wrapper is also deleted; the
broker's independent ERASE_ACCOUNT protocol and transactional local_erase guards
remain. The pre-existing router retirement flow does not call that protocol;
this pass does not add a new deletion phase at the shared door.

The test double retains explicit validated handles only for the separately
deferred per-call backend's tests. Local owner tools use OwnerTools/OwnerBoxd
and never that binding seam.

Exact unfinished classification rows:

- `broker/aclient.py:AsyncBrokerClient._connect`
- `broker/client.py:BrokerClient.publish_model_source`
- `broker/client.py:BrokerClient.usage`
- `broker/client.py:BrokerClient.capability`
- `broker/client.py:BrokerClient.request`
- `broker/client.py:BrokerClient.owner_policy (UNSURE decision below; deletion pending)`
- `command_center_update_executor.py:_connect`
- `command_center_update_executor.py:_quota_inputs`
- `control_plane/continuations.py:tick`
- `control_plane/tick.py:AssignedQueueConsumer.poll_once`
- `providers/api_key_http_provider.py:ApiKeyHttpProvider._connection_context`
- `providers/model_call.py:_dispatch_args`
- `storage/agent_request_usage.py:UsageStore.create`
- `storage/subscription_state.py:state_db_path`
- `storage/subscription_state.py:get_tier`
- `storage/subscription_state.py:read_tier`
- `storage/subscription_state.py:get_plan`
- `storage/webhook_hooks.py:mint`
- `storage/webhook_hooks.py:_revoke_routes`
- `universe_owner.py:account_type_of`
- `universe_owner.py:account_type_for_universe`

Also unfinished: install the alias-aware repository D7 scan using only the 29
role boundaries above, port the remaining fixtures, rerun their required Linux
selection, and tick the dual-path bullet only then. The per-call backend,
legacy launcher, box_tools/start_exec bridge modules and slice 6 migration/deploy
cleanup remain the separately deferred work from the briefs.

Validation receipts and final local commit identities are appended below.


Native-platform validation limit: the existing
`test_in_platform_agent_systems::test_a_directory_junction_is_never_followed`
is Windows-only and is skipped by the Linux selection. A native Windows run
was attempted (`windows.xml`) but failed during the existing home fixture:
`_seed_serving_assignment` requires the Unix-only in-process broker double,
so registration raises `the credential broker is not running` before junction
assertions. This case is **unverified**, never counted as a pass. No skip or
fixture bypass was added for it.


Caller-port follow-up: remove the deleted billing/identity callback arguments
from the 11 remaining local-erasure fixture modules without reducing any
assertion. `test_delivery_account_deletion` now expects the local receipt's
`not_configured`/`not_applicable` outcomes while keeping every transactional
rollback, child-row, peer-data and durable-receipt assertion. The manual model
connect test sends the surviving fenced ERASE_ACCOUNT protocol directly and
retains the failed-deposit/empty-vault proof. The deleted client's unused
response-table constant is removed. Design D3's metering text now matches the
broker-only descriptor/capability backfill.


The broad run also exposed an obsolete structural expectation:
`test_universe_path_io_guard::test_owner_box_callers_never_open_host_paths`
required BoxExecutor/run_bound in every module, including universe_tools. The
no-raw-I/O assertion remains for every listed module. Only universe_tools now
requires its local tools_for return and forbids BoxExecutor and current_binding;
every deferred daemon-side route retains its original transport assertion.
This adds assertions rather than loosening the raw-I/O guard. No probe in
scripts/check_shapes.py changed.


Final validation for this subset:

- Code/deletion commit: `32833fecc7b`; the caller/guard/receipt follow-up is
  recorded in the next local commit. Nothing was pushed or deployed.
- Required patterns, touched tests and all GUARDS-key files, plus all **34**
  heavy files: **157 unprivileged files**, **3,957 case identities**. The broad
  snapshot returned **3,942 passed, 14 failed, one existing Windows-only skip**.
  It also emitted one broker-connection teardown/unraisable warning. This is
  not reported as a successful single broad invocation.
- The 14 initial failures were seven explicit-handle fixture dependencies in
  branch_runner, two subsequently retired tests of the removed shared broker
  phase, four remaining local deletion callers carrying removed callbacks,
  and the obsolete BoxExecutor structural expectation for universe_tools.
- Corrected local-tool/deferred-backend/deletion follow-up: **250 passed**, no
  skips. All seven branch_runner failures passed in that complete-file rerun.
- Remaining erasure caller files: **157 passed, one failed** on an obsolete
  billing/identity receipt expectation. After porting those two assertions,
  the whole delivery-account-deletion file passed **seven**, no skips.
- Manual connection file after the direct broker-protocol port: **42 passed**.
  Whole structural file after its local-tools pin update: **five passed**.
- Proper root venue, owner control and destruction files: **six passed**, no
  skips. They are separate from the unprivileged selection.
- Combined JUnit identities: **4,161 surviving cases passed**, **zero unresolved
  failures/errors**, **one unverified Windows-only case** described above.
  The two retired broad failures are removed identities, not counted as passes.
  Additional touched-file cases outside the initial selection are included.
  `coverage-summary.json` verifies this union; this is combined venue and
  follow-up coverage, not a claim that the initial broad invocation exited zero.
- Additional focused runs passed **71** admission/broker/owner-process cases,
  **246** catalog/package/storage cases, **35** local tool/hook/starter cases,
  and **82** local deletion/tool/metering cases. These overlap the union and
  are not added to its total.
- Ruff on every Python file changed by this pass, commit hooks and diff
  whitespace checks pass. No surviving assertion count dropped against this
  pass's starting sha. The post-code-commit hygiene gate against
  `conv/c6-owner-daemon-s5` exits **0** with the requested S8 registry line and
  cumulative retirement reason; it is rerun after the final follow-up commit.

Receipts: `C:/Users/Jonathan/AppData/Local/Temp/s6a1c-validation/` holds the
selections, `final.log/xml`, `followup.log/xml`, `deletion-followup.log/xml`,
`deletion-recheck.xml`, `manual-recheck.xml`, `structural-recheck.xml`, `root.xml`,
`windows.xml`, `coverage-summary.json` and `hygiene-body.txt`. `initial.*` and
`tools.*` are explicitly abandoned intermediate snapshots, not final receipts.

The task remains partial: the exact 21 classified rows above, the repository
D7 mode-fork scan, its remaining test ports and the final dual-path tick are
still required. The reviewed 29 role-boundary entries are documented, not an
executable exemption for the unfinished legacy paths.


## 6a part 1d: remaining classified forks and executable D7 (2026-10-17)

This follow-up supersedes part 1c's unfinished-site inventory. Rechecked all
21 sites against their current implementation and production callers; no
legacy site was reclassified. The 29 ROLE_DISPATCH rows and reasons in part
1c are now the executable `_ROLE_DISPATCH` dictionary in
`tests/test_router_store_guard.py`. The guard requires exact agreement with
observed boundaries, so a stale exception fails too. The existing router
import/direct-filesystem checks remain intact. The owner_runtime module owns
its lifecycle; branchless principal/root/tools reads do not select modes.

| File:function (under tinyassets/) | Deleted path; retained behavior |
| --- | --- |
| `broker/aclient.py:AsyncBrokerClient._connect` | Host socket/peer alternative; always relay socket plus BOX_CONNECT acknowledgement. |
| `broker/client.py:BrokerClient.publish_model_source` | Host verification/transport; always bound owner connection. |
| `broker/client.py:BrokerClient.usage` | Host accounting connection; always bound owner connection. |
| `broker/client.py:BrokerClient.capability` | Host capability connection; always bound owner connection. |
| `broker/client.py:BrokerClient.request` | Host OPEN and per-exec custom-handshake alternative; always bound owner connection. |
| `broker/client.py:BrokerClient.owner_policy` | Legacy host policy arm; always bound owner connection (part 1c's UNSURE resolution confirmed). |
| `command_center_update_executor.py:_connect` | Per-center account_tier attachment; keep only owner source/dependency/accounting stores. |
| `command_center_update_executor.py:_quota_inputs` | Per-center subscription_meta reads; keep remote plan in preparation snapshot. |
| `control_plane/continuations.py:tick` | Runtime-None skip; always reconcile as admitted principal, then recover. |
| `control_plane/tick.py:AssignedQueueConsumer.poll_once` | Non-owner next-wake skip; always report in finally. |
| `providers/api_key_http_provider.py:ApiKeyHttpProvider._connection_context` | Host source-publication skip; always publish MODEL_SOURCE. |
| `providers/model_call.py:_dispatch_args` | Non-owner bypass; acquire mandatory broker egress before provider preparation. |
| `storage/agent_request_usage.py:UsageStore.create` | Host owner-token/ParentUsageLease branch and local-create alternative; always channel UsageLease and broker create RPC. Broker _insert/local operations remain. |
| `storage/subscription_state.py:state_db_path` | Remove per-center path helper and its unused writers/connections/schema. Router checkout timing constants remain. |
| `storage/subscription_state.py:get_tier` | Local DB/default-free arm; read bound plan. |
| `storage/subscription_state.py:read_tier` | Local DB/default-free arm; read bound plan. |
| `storage/subscription_state.py:get_plan` | Local DB assembly; always BOX_SHARED plan. |
| `storage/webhook_hooks.py:mint` | Local token generation, supplied-principal fallback and non-box compensation skips; use router RPC, admitted principal and unconditional compensation. |
| `storage/webhook_hooks.py:_revoke_routes` | Non-box no-op; revoke router digests before local changes. |
| `universe_owner.py:account_type_of` | Home/per-center and db_reader compatibility paths; read bound plan. |
| `universe_owner.py:account_type_for_universe` | Owner-table/home fallback; read bound plan. |

The `_connect_owner` helper has no mode predicate. Shared `_connect` remains
only for genuinely shared management methods. Async client host-path and peer
fields, the deleted parent lease's lock registry/fork hook, and the dead
subscription writer inventory entry are removed.

Policy caller evidence remains the part 1c trace: the router forwards
`/app/api/policy`; its private owner handler executes in the box. The new real
relay test exposed the broker rejecting protected writes before checking the
forwarded session proof. BOX now accepts write/issue_once only with
session_proof, derives principal from its admission, and still verifies the
live router session and existing generation/ledger/admission checks in
_owner_policy. Raw box cookies are rejected. Real relay tests cover valid,
foreign, expired and missing door proof plus a forged client principal.
No owner session database is created in the box.

D7 self-tests copy a real module under tmp_path, prove the clean copy passes,
inject a new fork and require the scanner to report that exact file/function.
Variants cover direct and aliased imports, module aliases, relative imports,
truthiness, captured modes, function/module assignment aliases, returned
boolean predicates, delegated broker predicates and store refusals. A negative probe preserves branchless reads and unrelated
turn-interrupt current() calls. All 29 exemptions have explicit reasons.

Protocol unit fixtures now inject startup/transport at the owner connection
and lease seams. Their same-UID broker/socket/liveness checks remain real;
real owner-process fixtures use the real relay, BOX protocol and UsageLease.
Hook units inject only startup identity and RPC transport into a separate
router temp root, retaining the real router writer and local hook store.
Package/unit fixtures explicitly supply a free plan at their shared-read seam;
paid-plan and mutation tests use the real broker Plans store. No production
fallback, test skip/xfail or assertion reduction was introduced.

New retirements (whole tests of deleted behavior; earlier inventories remain):
- `tests/test_universe_owner.py::TestTierOf::test_no_home_is_free`
- `tests/test_universe_owner.py::TestTierOf::test_the_home_subscription_is_the_account_tier`
- `tests/test_universe_owner.py::TestTierOf::test_a_paid_non_home_universe_does_not_make_the_account_paid`
- `tests/test_universe_owner.py::TestTierOf::test_a_traversal_home_is_free_not_read`
- `tests/test_universe_owner.py::TestTierOf::test_an_unnamed_principal_is_free`
- `tests/test_tier_lookup_fail_safe.py::test_an_unusable_universe_dir_reads_as_free_rather_than_raising` (two parametrizations)
- `tests/test_tier_lookup_fail_safe.py::test_an_unusable_universe_dir_never_grants_the_paid_tier`
- `tests/test_request_usage_store.py::test_gc_finalizer_cannot_deadlock_lease_registration`

Bound-plan resolver/read tests replace obsolete home/default-free cases and
prove missing plans fail loudly. The surviving outbound-effect assertions
remain. The update fence test now uses the still-owned storage ledger;
the tier-mutation test changes the actual broker plan after reservation and
retains refusal/no-write assertions. Subscription ordering and checkout tests
already use the broker Plans store. No path probe in scripts/check_shapes.py
was removed or raised.

Per-call backend is deferred and untouched. It still has callers:
- `wake_conditions.run_probe` and `workspace_provision_execution.execute_provision`
  construct BoxExecutor.
- `box_process.run_bound` calls provider.start_exec; users include boxes.media,
  boxes.node, top-level git_bridge, ui_preview and workspace_worker.
- workspace_fs and workspace_worker still select configured_box_provider.
- host_service imports GvisorFileBackend; owner_files constructs Executions;
  frontend imports ProductionFileProvider; gvisor_files launches exec_worker
  and file_worker. boxd/provider expose start_exec and box_tools calls it.
- `boxes/git_bridge.prepare` still constructs the old guest client with a
  custom verify_peer handshake. That invocation no longer supplies an owner
  runtime to the now owner-only OPEN client; retire it with its per-exec guest
  forwarder in the next backend pass. It is not a surviving daemon caller.

Protected slice-6 code is unchanged. Its old box_layout_migration call to
account_type_of(..., db_reader=...) still references the retired runtime API;
the slice-6 lane must finish its independent read-only census path. This pass
neither restores that compatibility path nor edits the protected migration.
No production, deployment, push, merge, branch switch, Git configuration,
new worktree or agent dispatch was performed. Independent review/deployment
remain with the lead; these are local implementation and validation commits.

Validation and final task state are appended after the Linux runs complete.


Part 1d validation follow-up: source publication is mandatory after validating
local owner/center scope and before grant reads. Publication failures use the
provider's existing known-not-sent admission error. Compute fixtures now supply
that publication operation at their broker seam, use verified source IDs and
pin the data root used by the channel. The exact unavailable-provider error
replaces the owner fixture's broad OSError/RuntimeError tuple; no local provider
is called. Whole compute/real-owner-egress recheck: **55 passed**, no skips.
The corrected broker/UI/owner-daemon/hook/update recheck passed **176**, the
five heavy branch/canonical suites passed **186**, and latest D7/import-boundary
proof passed **52**. Root-only control/destruction proof: **6 passed**.
The first post-implementation hygiene gate passed with the S8/retirement body.
Full broad-run accounting and final commit receipts follow below.


Final part 1d validation accounting:

- Implementation commit: `d151f78e4cd`; the fixture/task/validation follow-up
  is the next local commit. Nothing was pushed or deployed.
- Broad selection: **158 unprivileged files**, including every requested
  owner/broker/policy/account-deletion pattern, all structural GUARDS-key files,
  all touched tests at selection time, and all **34 heavy files**. It returned
  **3,642 passed, 23 failed**, zero errors/skips. This snapshot is not claimed
  as a successful single invocation.
- Its 23 failures were the 19 compute fixture/publication cases, the async
  reconnect peer, the UI fixture missing owner context, the subprocess import
  probe missing its shared plan transport, and the structural scanner's
  fixture/thread-target name collision. All 23 passed corrected whole-file
  rechecks. The scanner itself was unchanged: the fixture function has a
  distinct name with pytest's explicit name='published'; its two importing
  suites were ported and executed too. Patch setup still occurs on the caller.
- Passing rechecks: **176** broker/UI/owner-daemon/hook/update cases; **186**
  heavy branch/canonical cases; **52** latest D7/import-boundary cases; **55**
  compute/real-owner-egress cases; **60** unchanged thread-safety guard/update
  cases; **16** companion deletion/maintenance cases. These overlap and are
  not added together as a test total.
- Root venue: the entire owner control/destruction files passed **6**, with
  no skips. They were excluded from the unprivileged run so each case executes
  in its required venue rather than being counted as a skip.
- The JUnit union verifies **3,689 surviving case identities passed** across
  **162 files**, **zero unresolved failures/errors**, **zero skips**. Every
  requested pattern, GUARDS-key file, heavy file and touched test has passing
  cases. The two additional D7 mutation variants and 16 companion cases are
  included in this union. No baseline exception or retired failure is counted
  as a pass. The abandoned initial snapshot and early diagnostic snapshots
  are retained separately and excluded from this final union.
- Ruff on every Python file changed since this pass's starting sha, strict
  OpenSpec validation, whitespace checks and commit hooks pass. No surviving
  test's assertion count decreased against `3cf3e086062`. The first
  post-implementation hygiene invocation against conv/c6-owner-daemon-s5
  exited 0 with the requested S8 and retirement body; the final-head command
  is rerun after this validation commit and its exit status is retained with
  the external receipts.

Receipts: `C:/Users/Jonathan/AppData/Local/Temp/s6a1d-validation-884p0hcw/`.
`final-selection.json` lists the broad snapshot; `root-selection.json` names
its separate venue; `coverage-summary.json` maps every surviving case to its
passing report and verifies required-file coverage. JUnit/log stems are
`final`, `recheck`, `heavy-probe`, `guards-recheck`, `compute-final`, `root`,
`thread-recheck`, and `companion-recheck` (the final import-name-only recheck is
`companion-final`). `hygiene-body.txt`, final hygiene logs/exit receipt, Ruff
output and `assertion-drops.json` are in that same external directory.

The dual-path and D7 sub-bullets are checked. Overall 6a remains unchecked
because the explicitly deferred bridge/backend, launcher and protected
migration/deploy cleanup still remain. The 21-site inventory in this section
is complete; the executable allowlist contains only the 29 reviewed role
boundaries. No work from the next backend pass was started.


## 6a part 1e: retire the per-call backend (2026-10-17)

The shared router no longer configures a file/exec provider. OwnerTools owns
its four definitions directly; OwnerBoxd performs local file operations and
supervises bounded local children inside the persistent owner box. No
BoxExecutor, start_exec protocol, streamed exec status, per-call sandbox,
file-worker entry, guest forwarder or custom git handshake remains.

### Former callers

| Caller | Clean path |
| --- | --- |
| `wake_conditions.run_probe` | Startup admission and bounded `OwnerBoxd.run`; no per-wake host binding. |
| `workspace_provision_execution.execute_provision` | Admitted manifests and reservations remain; local bounded child, cancellation and measured charge remain. Each Python install has private temporary manifest storage. |
| `box_process.run_bound` | Deleted with its per-call exec binding; surviving media/node/git/preview/workspace callers below use OwnerBoxd.run directly. |
| `boxes.media` | Image and video decoders are local children of OwnerBoxd. |
| `boxes.node` | Local bounded child with framed node RPC, cancellation and native owner workspace paths. |
| top-level `git_bridge` | Local owner repository scope and OwnerBoxd child; opaque arguments stay opaque. |
| `ui_preview` | OwnerBoxd child and local file write; owner identity and immutable public-preview checks remain. |
| `workspace_worker` / `workspace_fs` / workspace effector | Native owner lease paths, local core file APIs and local child execution; broker git routes run in the owner daemon. |
| `host_service` | Broker registration/bootstrap and persistent-daemon wake/status/retire control only. The exclusive root host lock and restart census move here from the retired backend: validate every surviving sandbox/bundle before stopping any, then admit owners. No file admission or dispatch; the host receipt-backend setup and idle coupling are gone. |
| `owner_files` | Deleted with handles, channels, execution receipts and destruction transport; persistent daemon retirement and local account erasure remain. |
| `frontend` / `box_daemon_entry` | Frontend deleted; entry starts the admitted router directly after SQLite floor and broker adoption. Legacy launcher selection is deleted. |
| `boxes.git_bridge.prepare` / guest forwarder | Deleted. Owner git uses the existing admitted broker client and invocation-scoped local routes. |
| `scripts/box_acceptance.py` | Retired old provider probe. Integrity guards now cover the real entry, owner daemon, host service and live capability script; persistent-process OS proof replaces per-call jail proofs. |

`owner_daemons.py` and `gvisor_runtime.py` are unchanged. The only sandbox
lifecycle is their persistent per-owner lifecycle. OwnerBoxd accepts opaque
validated principals as journal keys, never directory names. Its local child
admission is serialized against shutdown; output/time bounds, interactive
input and cancellation kill and reap the whole child group.

### Removed paths (original lines at this pass's starting HEAD)

| File | Deleted lines |
| --- | ---: |
| `scripts/box_acceptance.py` | 219 |
| `tests/agent_loop_fakes.py` | 98 |
| `tests/test_agent_loop_box_tools.py` | 476 |
| `tests/test_box_host_handles.py` | 41 |
| `tests/test_gvisor_file_transport.py` | 141 |
| `tests/test_owner_box_destroy.py` | 153 |
| `tests/test_owner_box_execution.py` | 337 |
| `tests/test_owner_file_runtime.py` | 366 |
| `tests/test_remote_box_extensions.py` | 308 |
| `tests/test_remote_box_ta.py` | 495 |
| `tinyassets/agent_loop/box_ta_worker.py` | 151 |
| `tinyassets/agent_loop/box_tools.py` | 662 |
| `tinyassets/box_process.py` | 104 |
| `tinyassets/boxes/exec_worker.py` | 54 |
| `tinyassets/boxes/executions.py` | 287 |
| `tinyassets/boxes/file_channel.py` | 157 |
| `tinyassets/boxes/file_wire.py` | 50 |
| `tinyassets/boxes/file_worker.py` | 57 |
| `tinyassets/boxes/frontend.py` | 50 |
| `tinyassets/boxes/git_bridge.py` | 81 |
| `tinyassets/boxes/gvisor_files.py` | 333 |
| `tinyassets/boxes/handles.py` | 84 |
| `tinyassets/boxes/owner_files.py` | 356 |
| `tinyassets/boxes/production_files.py` | 211 |

The 15 runtime/helper/script paths total 2,856 original lines. Git recognizes
the old extension test as a rename/port; the table records its old path for
the retirement inventory rather than counting it as a lost proof.

### Guards and surviving proofs

D7 rejects imports of all 14 retired runtime modules, including direct,
parent-package, relative, and constant dynamic imports; it also proves their
module specs are absent. Mutation tests exercise those import forms. The
29-entry ROLE_DISPATCH allowlist and router store/I/O checks are unchanged.
S8 keeps every previous old-path probe and adds zero-ceiling probes for the
retired modules/probe script, the guest FORWARDER definition, and any
BoxExecutor/start_exec definition. A local history check also found three
inherited zero-count probes missing from S3/S4; those are restored without
raising either ceiling.

`test_c6_owner_box_callers` and `test_owner_tools` exercise local process and
file behavior, bounds, node RPC, native leases, wake scope, ta scope,
unknown receipts, replay and cancellation. `test_owner_extensions` ports the
real engine tool/command/hook, revocation, foreign revision and failed-delivery
cases to OwnerTools. `test_owner_box_process_boundary` keeps one real Linux
jail process alive across local commands and ta calls, checks the same daemon
PID, and checks filesystem, identity and environment isolation. Bash also
uses the same OwnerBoxd supervisor: running children fence CAS/snapshot
generations, and cancellation returns only after group cleanup. CI triggers
and integrity guards name the surviving paths; no skip/xfail was added.

Root control tests preserve cross-process host-lock exclusion and release,
restart recovery of both runsc and bundle survivors, rejection of untrusted
census/bundles before any stop, and refusal of the deleted file-admission
operation. These preserve startup guarantees that previously lived in the
retired backend; the persistent lifecycle modules themselves stay unchanged.

### Retired tests

Whole modules of the deleted backend (test functions, before parametrization):
- `tests/agent_loop_fakes.py`: 0.
- `tests/test_agent_loop_box_tools.py`: 32.
- `tests/test_box_host_handles.py`: 1.
- `tests/test_gvisor_file_transport.py`: 8.
- `tests/test_owner_box_destroy.py`: 4.
- `tests/test_owner_box_execution.py`: 14.
- `tests/test_owner_file_runtime.py`: 15.
- `tests/test_remote_box_extensions.py`: 5.
- `tests/test_remote_box_ta.py`: 12.

The old remote extension file is ported to `test_owner_extensions.py`;
local ta receipts and commands are ported to `test_owner_tools.py`, with real
persistent-process isolation proved separately. The transport-specific late
start/stream recovery and per-call host identity/exec/destruction tests retire
with that transport. File, link, CAS, usage and bundle assertions in mixed
contract suites are retained. Native Windows shared-daemon checkout is a
retired path; the descriptor helper's Windows refusal test remains.

Whole retired tests from mixed files (surviving behavior is covered on the
local path above):
- `tests/test_box_admission.py::test_tombstone_is_required_for_destroy_and_checked_again_after_binding`
- `tests/test_box_daemon_entry.py::test_an_image_without_the_legacy_launcher_refuses_unmigrated_data`
- `tests/test_box_daemon_entry.py::test_owner_split_uses_existing_launcher`
- `tests/test_box_git_bridge.py::test_cli_forwarder_without_git_stays_stdlib_only`
- `tests/test_box_local_driver.py::test_a_launch_refused_before_running_can_be_retried`
- `tests/test_box_local_driver.py::test_a_missing_program_is_an_exec_result_not_a_refusal`
- `tests/test_box_local_driver.py::test_a_restart_reaps_what_the_crashed_host_left_running`
- `tests/test_box_local_driver.py::test_cas_refuses_while_an_exec_is_running`
- `tests/test_box_local_driver.py::test_read_many_refuses_while_an_exec_may_be_changing_files`
- `tests/test_box_local_driver.py::test_reply_captured_before_supervisor_close_fails_as_unavailable`
- `tests/test_box_local_driver.py::test_supervisor_cannot_recycle_stdin_during_an_inflight_reply`
- `tests/test_box_provider_contract.py::test_a_retried_exec_with_the_same_op_id_never_runs_twice`
- `tests/test_box_provider_contract.py::test_box_disk_bound_contains_a_full_box`
- `tests/test_box_provider_contract.py::test_cancel_kills_the_whole_process_tree`
- `tests/test_box_provider_contract.py::test_destroy_stops_running_execs_before_it_returns`
- `tests/test_box_provider_contract.py::test_exec_streams_output_and_exit_code`
- `tests/test_box_provider_contract.py::test_stdin_a_child_never_reads_cannot_stall_the_wall_clock`
- `tests/test_box_provider_contract.py::test_the_exec_ends_with_its_leader_and_takes_its_group_with_it`
- `tests/test_box_provider_contract.py::test_wall_clock_and_output_limits_kill_the_exec`
- `tests/test_box_public_egress.py::test_box_forwarder_child_does_not_hold_command_output_open` (the per-call implementation was retired here; part 1f ports this behavior to `tests/test_owner_public_egress.py`).
- `tests/test_c6_owner_box_callers.py::test_bound_process_uses_the_exact_session_handle`
- `tests/test_c6_owner_box_callers.py::test_code_node_accepts_only_guest_workspace_path`
- `tests/test_c6_owner_box_callers.py::test_code_node_public_runner_requires_admitted_box`
- `tests/test_c6_owner_box_callers.py::test_code_node_uses_box_runner`
- `tests/test_c6_owner_box_callers.py::test_guest_lease_uses_center_relative_box_file_api`
- `tests/test_c6_owner_box_callers.py::test_image_and_video_callers_select_box_media_on_committed_layout`
- `tests/test_c6_owner_box_callers.py::test_image_and_video_use_the_admitted_box`
- `tests/test_c6_owner_box_callers.py::test_preview_renderer_uses_box_process`
- `tests/test_c6_owner_box_callers.py::test_startup_workspace_reconciliation_binds_an_owner_box`
- `tests/test_c6_owner_box_callers.py::test_workspace_git_projects_selected_grant_to_box`
- `tests/test_c6_owner_box_callers.py::test_workspace_git_refuses_the_cell_on_committed_box_layout`
- `tests/test_c6_owner_box_callers.py::test_workspace_pool_returns_guest_lease_paths`
- `tests/test_c6_owner_box_callers.py::test_workspace_sweep_removes_only_admitted_guest_lease`
- `tests/test_workspace_effector.py::test_a_checkout_without_the_posix_helpers_fails_as_a_workspace_refusal`

### Protected slice 6 and delivery boundary

No protected migration, census or deploy file was changed. None of the named
protected files imports a deleted backend module. Historical `execs` and
`owner_destroys` schema/restore authority remain in the shared file-journal
class because protected `deploy/box-store-quotas.py` still consumes that
historical authority; no runtime exec transport API remains. Slice 6 must
reconcile that offline restore shape with the local owner journals.

The retired guest forwarder's native-command public-network capability is
restored by 6a part 1f in `tinyassets/owner_public_egress.py`. Startup gives
OwnerBoxd its admitted relay and starts one in-process loopback HTTP/CONNECT
bridge on `127.0.0.1:3128` for the daemon lifetime. A centerless `BOX_PUBLIC`
request lets the root relay use its existing owner lifecycle admission to
select a live center; the broker still repeats `BOX_ADMISSION` and applies its
unchanged public-address floor, destination pinning and per-owner/host caps
before relaying bytes. OwnerBoxd's bounded process launcher supplies all six
proxy variables and both NO_PROXY spellings to every child, including wake
probes and provisioning; bash merges these defaults ahead of invocation
overrides, preserving authenticated git's local routes. OwnerBoxd.close stops
children and closes/joins the listener and active bridges on shutdown/idle
sleep. No per-command fork, host listener, shared-router proxy or retired
backend module is restored. Local provisioning retains its consent, manifest,
cancellation and charge guards.

The protected box-layout migration still calls the already-retired
`account_type_of(..., db_reader=readonly_db)` API. Its five failures below are
the same unchanged migration/API mismatch recorded by part 1d. This pass does
not restore that API. `.premigration` copying and one-shot migration/deploy
branches remain for the protected slice-6 merge and part 2. This is local-only:
no production/data access, push, merge, rebase, branch switch, Git config edit,
new worktree or agent dispatch. Independent review and live deployment remain
with the lead.

Validation receipts: `C:/Users/Jonathan/AppData/Local/Temp/s6a1e-validation-pli6tozs/`.

- Final regular Linux oracle: **2,038 passed**, one existing Windows-only
  helper test skipped by its pre-existing platform condition, across 89 files:
  all touched surviving tests, owner/box/gvisor/boxhost patterns and every
  `GUARDS` key. The Windows helper was separately executed natively:
  **1 passed**. No skip or xfail was introduced.
- Final root venue: **48 passed, 5 failed, no skips**. The failures are the
  unchanged protected migration/API mismatch above: the two WAL parametrizations,
  unreadable-founder preflight/apply, and the owner-split census/move fixture.
  Rsync was installed only in the disposable oracle container so backup tests
  executed rather than taking their existing dependency skips.
- Local supervisor, extension, process-boundary and import-order follow-ups:
  **44 passed** and **54 passed**, respectively. Final regular oracle includes
  the decoder import-order fix. Final host startup changes have the root
  coverage above and a separate structural/host follow-up: **1,007 passed**
  across 41 files, including every structural guard.
- Ruff: all **71** changed/new Python files passed. Strict OpenSpec validation,
  `check_shapes.py`, whitespace checks and pinned actionlint **1.7.7** passed.
  No touched file is listed in `.github/heavy-test-files.txt`.
- Post-commit hygiene against `conv/c6-owner-daemon-s5` passed for the backend
  retirement commit with `Registry row(s): S8` and the documented test-retirement
  reason. The same gate is repeated at the final commit.


## 6a part 1f: persistent in-box native public egress (2026-10-17)

`owner_public_egress.OwnerPublicForwarder` is owned only by OwnerBoxd. Owner
startup passes the admitted relay, binds the single loopback listener before
serving, and fails startup if it cannot bind (releasing its journal lock).
Commands share that listener without forking a forwarder. Shutdown interrupts
active bridges, including a stalled relay admission, joins all workers and
releases port 3128 for the next wake. The daemon's existing SIGTERM/idle-sleep
finally calls OwnerBoxd.close. Process supervision retains close_fds and never
hands command pipes to the listener.

The root relay accepts centerless BOX_PUBLIC and obtains a live center via
OwnerRegistry.owner's existing broker lifecycle admission. Its bind still
supplies the principal; neither the request nor the in-box bridge supplies an
owner. The broker receives its original center-bearing BOX_PUBLIC document,
repeats BOX_ADMISSION and returns BOX_ADMITTED before raw HTTP/CONNECT. No
broker parser, DNS/public-address floor, admission implementation or capacity
limit is changed. No shared-daemon listener or retired backend is restored;
D7 still bans all fourteen retired modules. The authenticated-git recheck
also exposed two live remnants masked by direct-broker fixtures: relay calls
to the deleted OwnerRegistry.grant/authorize APIs, and git_client.exchange
writing OPEN directly to the relay without BOX_CONNECT. The relay now relies
on its existing broker admission and per-stream scope/checkpoints; the git
exchange uses BrokerClient._connect and the relay's broker peer verification.
The existing lifecycle closes retired boxes/channels. No session-grant API or
cross-owner application check is restored. S8 retains every existing probe
and adds shared-daemon/router forwarder probes plus a mutation self-test.

OwnerBoxd.run's single bounded launcher adds HTTP_PROXY, HTTPS_PROXY,
ALL_PROXY and lowercase variants plus both NO_PROXY spellings. OwnerTools
bash puts these defaults after the ambient environment and before invocation
overrides. Authenticated git's invocation-local http_proxy therefore keeps
its broker routes. Wake probes and provisioning reach the same launcher;
the package manager inherits its stage process environment.

Tests added/ported:

- `test_owner_public_egress.py`: real OwnerBoxd children, owner-bound root
  relay, OwnerRegistry lifecycle requests and BOX_HOST broker PublicEgress,
  with only destination DNS policy substituted for a local HTTP origin.
  Covers HTTP, CONNECT, repeated commands sharing the listener, request body
  and response after half-close, all proxy spellings despite ambient values,
  bash and wake probes, and execute_provision's real stage with a synthetic
  package-manager executable that checks its env and downloads locally.
- Refusals: no retained admission, absent verified box record, loopback and
  metadata destinations under the unchanged floor, owner cap and host cap;
  every refusal proves no upstream connection was opened.
- The retired command-output-EOF regression is ported to OwnerBoxd; active
  connection shutdown, stalled admission shutdown, listener restart after
  sleep and bind-failure journal cleanup have explicit proofs.
- `test_box_git_bridge.py`: a real git ls-remote child uses the authenticated
  broker route with public proxy defaults present; existing refusal/credential
  assertions remain intact. An additional native proof uses the real
  OwnerRegistry, root relay and BOX_HOST broker for git, then proves a foreign
  center still refuses on that relay. Existing broker parsing, body/half-close and
  refusal tests in `test_box_public_egress.py` are unchanged. Inspection of
  the retirement diff found no other deleted public-egress backend cases.
- Router/waker process doubles and in-process parent daemon fixtures select
  ephemeral loopback ports because they share the host netns with their child
  process proofs; real boxes and the native egress proof bind 3128. Existing
  fixture assertions stay intact and now also assert the listener is alive.

Validation receipts: `C:/Users/Jonathan/AppData/Local/Temp/s6a1f-validation-x62rdlwe/`.
No test was retired or loosened, and no skip or xfail was added. Protected
slice-6 files remain unchanged. No production/data access, dispatch, worktree,
push, merge, rebase, branch switch or host Git configuration edit occurred.
Independent review and deployment remain with the lead as required by this
local-only handoff.


Final 1f validation:

- Final unprivileged Linux oracle: **2,298 passed**, one existing Windows-only
  transport-refusal test skipped by its platform condition, across **126
  files**. Selection includes every touched test, the requested owner/egress/
  broker/box patterns and every GUARDS key. The skipped case was separately
  executed natively on Windows: **1 passed**. No skip or xfail was added.
- Root venue (`--as-root`), with rsync provisioned only in the disposable
  container before pytest: **44 passed, 5 failed, no skips** across five files.
  All five failures are the unchanged protected `box_layout_migration` API
  mismatch (`account_type_of(..., db_reader=...)`) already recorded in 1d/1e:
  `test_preflight_reads_wal_without_writing_data[False]`,
  `test_preflight_reads_wal_without_writing_data[True]`,
  `test_preflight_refuses_unreadable_founder_home`,
  `test_apply_refuses_unreadable_founder_home_before_any_write`, and
  `test_owner_split_fixture_census_moves_old_layout_and_refuses_absent_or_ambiguous`.
- Ruff on all **15 changed/new Python files**, `check_shapes.py`, strict
  OpenSpec validation and whitespace checks passed. No changed file is heavy.
  D7's existing 29 role-dispatch exceptions and fourteen retired-module bans
  remain unchanged; git uses the existing BrokerClient connection seam with
  its original peer verifier, adding no runtime-mode branch.
- The post-commit hygiene gate uses `conv/c6-owner-daemon-s5` and the copied
  body with `Registry row(s): S8` and the existing `Test-Removal: retired --`
  reason. Its output is saved alongside these receipts after the local commit.

## 6a part 1g: owner-keyed public admission (2026-10-17)

`box_admission.resolve_owner`, used by PublicEgress, admits the relay-bound
owner's verified box record without a center and retains the existing
box-route revocation/deletion fence and lifecycle plan-suspension refusals;
`resolve` keeps center-scoped admission. The relay forwards exactly
`{op: BOX_PUBLIC, principal: owner}` and rejects center-bearing public requests
before broker contact. The deleted relay grant check stays deleted because
the bound channel and broker recheck supply authority, while the OS enforces
owner isolation per the Direction. Tests cover zero-center native HTTP/CONNECT,
unknown/unadmitted owners, invalid records, tombstones, both suspensions and
forged relay operations, retaining all existing egress assertions without
retirement, loosening, skip or xfail additions. Linux: 2,137 passed unprivileged
(including all structural guards); root: 44 passed and the same five protected
migration API failures recorded above, with no skips in either final run.
Ruff, shape checks and strict spec validation passed. Validation receipts:
`C:/Users/Jonathan/AppData/Local/Temp/s6a1g-validation-8ad854f1/`.


## 6a part 1h: reconcile shared-door deletion after the slice-5 merge (2026-10-17)

Identity erasure now lives in `tinyassets/router_account_deletion.py`, called
only by `owner_router._delete_owner_account`. The WorkOS helper, principal
pattern, URL, timeout, idempotent 404 and secret-free errors are unchanged from
slice 4. Its common `AccountDeletionError` also serves the box sweep without
making the shared helper import box-local code. The four historical WorkOS
unit tests are restored in `tests/test_router_account_deletion.py`, with only
the module/error imports relocated; AST comparison proves their bodies and the
helper unchanged. Their obsolete retirement entries above are removed.

Billing already lives in shared
`billing.stripe_adapter.cancel_account_billing(root, owner)`: read the owner's
subscription mapping, confirm cancellation, then update the shared plan. The
unused home-based `cancel_stripe_billing` stays deleted. The shared fenced RPC
`broker.account_erasure.erase_account` is restored in its original module;
6a had removed it when its former box caller disappeared, overlooking the
parallel door change. Its body, response-count validation and broker receiver
are AST-identical to slice 4. Two real-broker tests prove lost-acknowledgement
failure without automatic replay, safe explicit retry, other-owner preservation
and loud failure with retained rows when the broker is unavailable.

The local sweep performs no identity, billing, custody or host lifecycle work.
No `local_only` or runtime-mode branch returns. The door phases, atomic
resolve/release wake leases, single confirmed boxhostd destroy and four-argument
HostService are unchanged. D7 now permits the three actual shared owners used
by the merged door: identity, billing and custody erasure. Its assertions still
reject owner stores and filesystem access; a new mutation input explicitly
rejects the box-local account-deletion import. Every check_shapes probe and
runtime-mode exception remains intact.

All eighteen reported failures passed with their original expectations:

- **I:** the door imported WorkOS deletion from the box-local module after 6a
  removed it. Moving the helper exposed the independently deleted shared broker
  RPC wrapper. Fix both shared dependencies, without restoring box callers.
- **P:** paid-deletion setup patched the deleted box-local WorkOS pattern/helper.
  Change only import/monkeypatch targets to the shared identity module; also fix
  I. The broker_egress injection additionally needed the restored RPC target.
- **R:** the host-lock test explicitly requires UID 0; the failing venue used
  UID 1001. Run the unchanged test with `--as-root`; no product fix needed.

All table rows are in `tests/test_owner_router.py` except the last:

| Test | Cause/fix |
| --- | --- |
| `test_door_deletion_removes_only_bound_owners_data_and_router_state` | I |
| `test_a_failed_account_revoke_can_be_retried_to_completion[retire]` | I |
| `test_a_failed_account_revoke_can_be_retried_to_completion[revoke]` | I |
| `test_box_deletion_rejection_leaves_lifecycle_and_router_state_untouched[blocked]` | I |
| `test_box_deletion_rejection_leaves_lifecycle_and_router_state_untouched[refused]` | I |
| `test_paid_deletion_confirms_every_phase_and_retries_after_tombstone[None]` | P + I |
| `test_paid_deletion_confirms_every_phase_and_retries_after_tombstone[stop]` | P + I |
| `test_paid_deletion_confirms_every_phase_and_retries_after_tombstone[owner_tree]` | P + I |
| `test_paid_deletion_confirms_every_phase_and_retries_after_tombstone[relay]` | P + I |
| `test_paid_deletion_confirms_every_phase_and_retries_after_tombstone[socket]` | P + I |
| `test_paid_deletion_confirms_every_phase_and_retries_after_tombstone[broker_egress]` | P + I |
| `test_paid_deletion_confirms_every_phase_and_retries_after_tombstone[billing]` | P + I |
| `test_paid_deletion_confirms_every_phase_and_retries_after_tombstone[plan]` | P + I |
| `test_paid_deletion_confirms_every_phase_and_retries_after_tombstone[credentials]` | P + I |
| `test_paid_deletion_confirms_every_phase_and_retries_after_tombstone[identity]` | P + I |
| `test_deletion_drains_real_turn_before_sweep_and_destroys_late_writes[False]` | I |
| `test_deletion_drains_real_turn_before_sweep_and_destroys_late_writes[True]` | I |
| `tests/test_owner_box_control.py::test_host_restart_lock_precedes_recovery_and_excludes_a_second_controller` | R |

The paid-deletion decorator collects ten cases, not eleven; these account for
all eighteen reported failures. No expectation encoded the deleted dual path,
so no expectation exception was needed. All original assertions in the three
touched existing test files are AST-identical to merge `3efd51eaa74`. No test
was retired, loosened, skipped, xfailed or quarantined in this pass.

The missing broker import also prevented the stop-failure injection from being
consumed by its deletion phase; it instead fired during fixture teardown in the
intermediate run. The drain assertions failed because forwarding never began.
Both secondary symptoms disappear with the shared wrapper restored; neither
fixture cleanup nor drain expectations changed. The baseline separately hit a
socket-refusal timing race in `test_router_credential_socket_refuses_a_non_broker_peer`:
immediate rejection can precede its sendall and raise BrokenPipeError. Its
implementation/expectations are unchanged and the 47-test run below passes it.

Validation receipts: `C:/Users/Jonathan/AppData/Local/Temp/s6a1h-validation-jjglaj5w/`.

- Final unprivileged Linux oracle: **1,744 passed, no skips**, across **87
  files**, including every structural guard and the requested owner, box, egress,
  deletion and center-admission suites. All eighteen originally reported cases
  pass across this venue and the root venue.
- Full owner-router and broker-erasure suites: **47 passed, no skips**. These
  include all seventeen deletion cases and both new real-broker proofs.
  `reported-failures-resolved.json` maps each of the eighteen reported cases
  to its passing Linux test receipt, including the unchanged root host lock.
- Final D7/identity follow-up after the extra mutation input: **61 passed,
  no skips**. The requested broad selection includes every structural GUARDS
  key, D7 and all requested owner/box/egress/deletion file patterns.
- Root venue: **37 passed, 5 failed, no skips**. The five are the unchanged
  protected slice-6 `account_type_of(..., db_reader=readonly_db)` API mismatch
  already recorded in 1e/1f/1g:
  `test_preflight_reads_wal_without_writing_data[False]`,
  `test_preflight_reads_wal_without_writing_data[True]`,
  `test_preflight_refuses_unreadable_founder_home`,
  `test_apply_refuses_unreadable_founder_home_before_any_write`, and
  `test_owner_split_fixture_census_moves_old_layout_and_refuses_absent_or_ambiguous`.
  Rsync was provisioned only inside the disposable root container so ownership
  proofs executed.
- Ruff on all eight changed/new Python files, strict OpenSpec validation,
  check_shapes and whitespace checks pass. No changed file is heavy.
- The post-commit hygiene gate uses `conv/c6-owner-daemon-s5` and the copied
  body with `Registry row(s): S8` and the existing `Test-Removal: retired --`
  line; its receipt is saved beside the test results after the local commit.

Protected slice-6 files, production, host Git configuration and remote Git state
are untouched. No dispatch, push, merge, rebase, switch or amend. Independent
review and deployment remain with the lead under this local-only handoff.

## Slice 6 census and transaction follow-up (2026-10-17)

Local census commit: `f6b6e3b8f1a`; transaction commit: `69d53399b4e`;
recovery and process-serving proof: `986e7bca0f4`.
The final transaction replaces P2b inside the
same module; D6 records the six classes and the journaled build/publish protocol.
Shared stores, explicit plans, broker custody, box records, hook digests and the
empty wake index are seeded before publication. The final correction translates
legacy principal tombstones into the broker's digest-only schema and changes
restore's deletion-frontier read to the final custody store; it does not retain
an old-metadata fallback. Owner database rows are streamed
with census-key verification; extracted/seeded SQLite databases and file copies
are verified before host-tree removal. Interrupted runs resume against the same
paired snapshot. Completed markers are idempotent; intermediate markers refuse.

The old credential copy, CLI-home copy and second activity-conversion pass are
deleted. Nine whole tests exclusively exercising those paths were retired;
retained copy, inode, snapshot, workflow-retirement and restore guards remain.
At that head restore required an unchanged cutover: exact source, shared
output and owner-output manifests had to match before returning to owner-split.
The priority follow-up below adds explicit owner-write loss while preserving
identity, billing, source and receipt guards.

Validation receipts are outside the repository under `%TEMP%/ta-s6-validation/`.
The requested migration/layout/census/backup/restore/deploy/owner/broker patterns
and every structural GUARDS key ran through Linux oracle. The initial selection
had 2,604 passes, 123 failures and nine root-only skips: 121 shell tests saw CRLF
in the edited deploy script, one case required root, and one storage inventory
needed the three new migration filenames. The shell is restored to LF and the
inventory is registered. The complete deploy/structural rerun passed 1,465 with
no skips. Root validation passed 117 with one opposite-venue skip; the original
unprivileged run covers that case. Root-only skips are not credited as passes.
The final cutover and CI venue selection passed 171 without skips, including
custody tombstone compatibility and recovery of interrupted JSON writes,
SQLite sidecars and partial rsync output. Two real owner apps read all four
migrated centers' history and pending requests through production routes; a
test probe exercises real pause/resume and plan reads over the broker relay.
The rig substitutes the mount and broker peer UID, not application behavior.
The new root-required
cutover file is registered with the existing coordinator, preserving selection
and receipt requirements. The nonroot backup-refusal case skipped in the root
run passed in the original unprivileged run.

The hygiene gate against `conv/c6-owner-daemon-s5` exited 0 at `986e7bca0f4`:
23 tests added, nine whole tests retired, no retained assertion reductions.
Its body declares `Registry row(s): S8` and the retired P2b paths. The nine
reported findings are those declared whole-test removals. One nonblocking note
identifies the process launcher's 50 ms bounded socket-readiness wait.
Receipts: `complete-final.log`, `complete-final.xml` and `hygiene-code.log`.

Ruff passes on every changed Python file. Whole-repository Ruff reports 33
findings in seven files whose contents are identical to the initial head;
none is in this change. No skip, xfail, quarantine or old-path guard was added,
removed or relaxed. No production backup/data was accessed; the remaining
production-archive reference was removed from an older design note. No agent,
host git configuration change, new worktree, push, merge or deployment was used.

Task 6 stayed unchecked at that head. The priority follow-up below records
the switch disposition and restore-after-serving proof; the lead's real-data
census, real-mount rehearsal and CI shards remain outstanding.


## Slice 6 priority pass (2026-10-17)

Only the supplied redacted counts and synthetic fixtures were used. No data
archive or production tree was accessed. The census now distinguishes retired
public-reader/platform actors from unknown named principals: the former have
no account and still follow real keys; the latter continue to refuse, as do
contradictory keys. No owner is supplied by default.

| Table / class | Attribution and disposition, derived from schemas/writers |
| --- | --- |
| `served_provider_budget_reservations` | `binding_id` to `provider_work_bindings`; that schema has no run key. Missing/unowned binding is a counted drop. |
| `provider_work_bindings` | `owner_user_id` and `universe_id` admission; no owner-bearing secondary key in its writer. |
| `authenticated_connections` | Authenticated `owner`; `connection_id` is a one-way correlation hash, not an owner-session foreign key (`connected_agents._schema`, `observe_mcp`). |
| `owner_devices` | Authenticated `owner_user_id`; `device_id` is a registration UUID, with no session link. Tokens may move between owners, so a token hash cannot infer ownership (`storage/owner_devices.py`). |
| `branch_versions`, `branch_schedules` | Publisher/owner actor and center, plus `branch_def_id` to `branch_definitions`. |
| `branch_definitions`, `goals` | `author`; `universe:<center>` resolves through broker admission. Public rows retain their explicit publication rule; unowned private rows drop. A fork's parent is not its owner's identity. |
| `agent_conversion_receipts` | `actor_id`, then `stage_id` to `agent_import_stages`. |
| `agent_conversion_receipt_links` (`receipt_links`) | `actor_id`, `stage_id`, `receipt_digest` to the stage/receipt. |
| `agent_conversion_receipt_owners` (`receipt_owners`) | `actor_id`, `receipt_digest` to the receipt. |
| `agent_import_stages` | `actor_id`, or `published_definition_id` to the created definition's `author_id`; no run/center column in historical DDL. |
| `agent_interchange_idempotency` | `actor_id`; operation-qualified `resource_id`: `stage_import` -> stage, `convert_export` -> receipt, `publish_stage` -> definition author. |
| `owner_ui_prefs` | Authenticated `owner_user_id`; viewport, agent and preference names are not ownership keys. |
| Checkpoints / writes | `thread_id` to runs; multiple runs must all agree on the same owner. A mixed-owner thread refuses. |
| `outside_switch` | Router singleton: `set_enabled` accepts no owner and `admit` reads `id=1` before any owner's grant. Never copied to each box. |

The historical interchange DDL and writers were read from the parent of the
file-based-agent deletion commit in git; no data was used to infer joins.
All reviewed owner classes drop only when the documented keys resolve to no
account; unknown named principals and conflicting bindings still refuse.
The lead's next census, not these synthetic inputs, establishes the remaining
real-data refusal count.

File classification is creator-backed (`storage_accounting`, center layout,
LanceDB writers and sidecar consumers):

| File class | Treatment |
| --- | --- |
| Admitted center contents, `.agent-sessions/<center>/`, consent/seed sidecars | Owner by admitted center; content copied verbatim. Socket files classify as regenerated (the existing inode preflight still refuses live special inodes); consent databases remain owner data. |
| `runs/<run>.*`, archived and per-run directories | Owner through the run; legacy/unowned runs drop; conflicting ownership refuses. |
| Root/per-center `lancedb`, provider `.runtime`, known process locks/PIDs/liveness, transient checkout staging | Regenerated; excluded from owner outputs and removed from the source after verified publication. Router `owner-sessions.db` is still extracted as a database before runtime classification. |
| Legacy scratch, root activity logs, never-admitted center/session trees, retired CLI homes, stale database copy | Counted drops. CLI homes use permanent UID -> account for loss reporting. |
| Center-local vault/credential entries | Dropped; census never opens the local vault document and migration never copies it into an owner output. |
| Platform custody, broker policy-deletion markers/fence, shared router markers and published stores | Stay in the named shared class; broker custody is UID 1002, directories 0700/files 0600. |
| Unknown entries | Refuse, with fully masked patterns; no catch-all deletion or assumed owner. |

`file_patterns` groups counts by class with variable names replaced by
`<center>`, `<owner>`, `<id>` and `<file>`. `losses` groups dropped row-table
classes and masked file classes by pseudonymous account, with a separate
unattributed bucket. `credentials` retains each account's carried HTTP and
dropped CLI counts. The first sorted JSON field, `account_model_source_summary`,
says **every account reconnects a model source** when the carry count is zero.
The supplied redacted input had zero carried sources for all six accounts and
322 dropped CLI records on one; it is not presented as a new census result.
The reconnect destination is the shared-door model-connect and model-use
confirmation path.

`credentials_outside_broker_custody` previously counted one file for each
center-local `.credential-vault.json`, not slots or accounts. The reported
three are now the explicit `credential_custody.outside_broker_files_dropped`
class. The synthetic malformed-local-vault fixture proves no JSON parsing is
needed. Existing validated HTTP slots remain under
`.platform/credential-vaults/` with `.broker/credential-custody.db` metadata.
The final broker identity schema (including secret/revocation tables and
permanent-identity triggers) is initialized before the restore receipt is sealed.

Restore proof uses two separate owner-daemon processes over their real HTTP
sockets. Each of four centers reads its migrated history/request, then writes
new chat history and a pending request through authenticated test probes calling
the production storage operations, reads them through production app routes,
and exercises real pause/resume and broker plan reads. After processes stop,
the real tar/SQLite/restore shell returns every pre-cutover file byte-for-byte
and removes both active owner-box trees. This proves storage/serving/restore;
it does not claim model inference, real quota mounts or native sandbox isolation.

`CUTOVER_ACCEPT_OWNER_WRITE_LOSS=1` explicitly accepts losing post-cutover owner
writes. Default restore refusals are retained. The opt-in permits only owner
output changes, ingress-secret contents and the derived next-wake index;
logical identity schema/rows and every other shared file remain fenced. Eight
mutation cases retain refusal for identity, admission, deletion, billing,
destruction, receipt permissions, source bytes and unexpected owner trees.
The census, D6 and cutover runbook state that restored owner-split data loses
post-cutover chats, pending requests, pause and other owner changes.

Implementation commit: `04918da8a6a`. Validation receipts are outside the
repository at `%TEMP%/ta-s6-priority-validation/`. Task 6 stays unchecked:
the lead owns the real-data census, real-mount rehearsal, CI shards and independent
review. No agent dispatch, new worktree, git configuration change, branch change,
push, merge or deployment was performed.


Priority validation:
- Final touched/root/census/restore and root control/destruction selection:
  **66 passed, zero skipped**, `root-verified.log` / `root-verified.xml`.
- Requested migration/layout/census/backup/restore/deploy/owner/broker patterns
  plus every structural guard: **2,732 passed, one root-venue failure and four
  root-venue skips**, `focused-user.log` / `focused-user.xml`. The failed kernel
  control case and both skipped owner-destruction cases passed in the 66-test
  root run. The remaining backup-root and special-bit copy cases passed **2/2** under
  root (`root-remaining.log` / `.xml`); every original failure/skip now has a
  passing execution in its required venue. No skip is credited as a pass.
- Structural/census/restore-authority recheck at committed implementation
  HEAD: **992 passed, zero skipped**, `guards-head.log` / `guards-head.xml`.
  This includes every `GUARDS` key after the final broker schema/mode fix.
- Hygiene against `conv/c6-owner-daemon-s5` at `04918da8a6a`: **exit 0**, 29 tests
  added, nine declared P2b whole-test removals, no retained assertion reductions.
  The only nonblocking note remains the existing 50 ms socket-readiness wait.
- Changed-file Ruff, strict OpenSpec validation and `git diff --check` pass.

Intermediate restore runs caught two actual serving changes (wake reporting and
first-lookup ingress secret creation), followed by a fixture SQLite-handle race
between snapshot and source-manifest creation. The final implementation fences
all non-ephemeral shared state. Synthetic seed connections close explicitly;
cyclic initializer handles are collected before the stopped-writer snapshot,
and the proof additionally asserts that snapshot bytes match the journal source
manifest. No assertion, old-path probe, skip or quarantine was weakened.

The final audit-only commit repeats the same hygiene command against its own
HEAD; `hygiene-final.log` is the final exit-code receipt. The body retains
`Registry row(s): S8` and the existing P2b `Test-Removal: retired -- ...` line.


## Slice 6 reason-coded census (2026-10-17)

This pass uses only the lead's redacted round-3 counts and synthetic fixtures.
No production data or archive was read. The prior priority notes describe
the old refusal policy; this pass replaces it with the founder's explicit
reason-based dispositions. The redacted 665 rows and 52 files are input counts,
not a claim that a new real-data census has run.

Every unresolved reviewed owner row now has exactly one per-table `reasons`
count, with precedence `conflicting_owners`, `orphan_reference`,
`unadmitted_principal`, `unknown_center`, `no_owner_key`. Only conflicting owners
remain unattributed/refused; the other four are counted drops. Missing parents
and parent failures propagate through budget bindings, definitions, stages,
receipts and runs. Candidate owners survive failures in other keys so an orphan
cannot hide conflict. Retired non-account actors may follow an actual parent;
a named unknown actor is never reassigned to that parent's owner. A center in
the registry without admission is `unadmitted_principal`; one absent from the
registry and admission map is `unknown_center`. A permanent identity with no
admission or binding does not by itself confer row ownership.

The old table-specific drop flag is removed. Structural admission, unknown
schema, plan and custody gates remain. A foreign-key violation in a dropped
row follows its counted disposition; the same violation in a retained row
still refuses. A conflicting run's transcript also refuses, rather than losing
the conflict when converting row evidence into file ownership.

`file_pattern_kinds` counts each masked pattern by placement and kind:
`admitted_center`, `unadmitted_center`, `unknown_dir`, `account_hash`,
`broker_account`, `platform_store`, `root_file`. Registered shared stores keep
their creator-backed rules; other unknown and never-admitted directories drop.
A hash alone does not select an owner. Root files and unknown account/broker/
platform files refuse with their kind. Identity and plan DBs use the existing
reviewed table extraction; policy deletion markers and fence JSON stay broker
state, router release markers stay router state, and custody/refresh locks
regenerate. These paths come from storage accounting, broker process/policy,
credential-vault and credential-refresh code. Browser-vault keys are deliberately
outside this data root; an arbitrary key file in it is unclassified and refuses.

`loss_summary` adds pseudonymous per-account reason/description/class/count
aggregates, including `CLI login state, N files` and `orphaned budget
reservations, N rows`. Unknown-account losses have their own bucket. No path or
identity lists are emitted. Existing masked-pattern and table totals remain.
Synthetic cases assert all five reasons and seven file kinds, precedence,
transitive conflict/missing-parent handling, actual database extraction,
retained foreign-key refusal, unchanged input bytes and report redaction.

D6 and the owner-isolation delta spec are updated. Task 6 remains unchecked;
the lead's real-data census, real-mount rehearsal and CI acceptance remain.
No agent dispatch, new worktree, host git configuration change, branch change,
push, merge or deployment was performed. Validation receipts are outside the
repository at `%TEMP%/ta-s6-reason-validation/`.


Reason-pass validation:
- Requested census/migration/layout/restore patterns, touched-module consumers
  and every structural `GUARDS` key: 54 files, 1,369 selected cases.
  The unprivileged oracle passed 1,342; its two broker-directory ownership
  failures, 24 root-fixture errors and one existing special-bit root skip all
  have passing root executions. Root receipts are **25 passed** and **60 passed**,
  both with zero skips. XML reconciliation (`coverage.json`) confirms every
  selected case passed in its required Linux venue; zero unresolved cases.
- All 57 synthetic census cases passed on Linux, including all five row reasons,
  all seven file kinds, foreign-key extraction and retained-conflict refusals.
- Changed-file Ruff, strict OpenSpec validation and `git diff --check` pass.
  Whole-repository Ruff still reports 33 findings in seven files byte-identical
  to the starting head; `ruff-all.json` records them.
- Post-commit hygiene runs against `conv/c6-owner-daemon-s5` with
  `Registry row(s): S8` and the unchanged existing P2b retirement declaration.
  The command's receipt is `hygiene-final.log` beside the XML receipts.
No test skip, xfail, quarantine or assertion reduction was introduced. Tests
whose expected disposition changed now assert the exact reason, counted drop
and unchanged carried rows, in addition to source immutability/redaction.


## Slice 6 archive-harness escalation (2026-10-17; synthetic acceptance passed)

The synthetic root venue passes every rehearsal step. The combined harness,
admission, migration and census run completed **120 passed, zero skips**:

`python scripts/linux_oracle.py --as-root --docker-engine -- -q --no-header -p no:cacheprovider tests/test_box_move_rehearsal.py tests/test_box_admission.py tests/test_box_layout_migration.py tests/test_owner_cutover_census.py`

For the harness alone:
`python scripts/linux_oracle.py --as-root --docker-engine -- -q --no-header -p no:cacheprovider tests/test_box_move_rehearsal.py`.
Unprivileged CI retains the documented root-only skip for the full XFS test.
Receipts live outside the repository in
`%TEMP%/ta-s6-escalation-validation/` (`rehearsal.log`, `rehearsal.xml`).

Final validation selected 74 files: touched tests, the requested rehearsal,
census, migration and deploy patterns, all 35 structural guard files, ten
affected heavy files, and provider/reconnect/owner-egress consumers. The
unprivileged oracle recorded **2,412 passed, two failed, two skipped**
(`final-selection.log` / `.xml`; exact selection in `final-selection.json`).
The two migration failures simulate effective root while the process still
owns its temporary directories as the unprivileged user; the broker ownership
guard correctly refuses that mismatch. Both pass under actual root. The
special-bit copy and XFS harness cases use their existing root-only skips and
also pass under actual root. Node-by-node XML reconciliation in
`coverage-final.json` confirms **2,416 distinct cases passed**, with no
unresolved cases. No failure or skip is credited as a pass. All **35 structural
guard files passed, 960 cases**, without skips.

Changed-file Ruff and `git diff --check` pass. Repository-wide Ruff records
33 pre-existing findings in seven files verified unchanged from the starting
head (`ruff-all.json`). Post-commit hygiene passes against
`conv/c6-owner-daemon-s5` with `Registry row(s): S8` and the unchanged existing
`Test-Removal: retired --` declaration in `hygiene-body.txt`. The product and
harness commit is `b96419aba6c`; this validation receipt changes prose only.

The previous interruption failed because the migration Python process is PID 1
in its container namespace: a self-sent SIGKILL is ignored there. A minimal
network-less container reproduced PID 1 surviving that signal while its child
returned -9. The profiler has been deleted. The shared apply implementation
now calls `os._exit(137)` at the exact boundary named by `CUTOVER_CRASH_AT`,
otherwise remaining inert. The harness separately restores, crashes and resumes
at `after_owner_tree:1` and `after_seeding`, comparing each result against the
uninterrupted shared and owner manifests. Both comparisons pass.

Ordinary deploy and restore entry points reject the fault input before host
actions. Every migration container overrides even an image-baked fault with an
empty value; only the explicit disposable runner may forward a nonempty one.
That runner checks the generated `ta-s6-<nonce>` volume family and the actual
store/backup mountpoints. Execution tests cover deploy/restore refusal,
arbitrary-path refusal and the empty container override. The harness still
calls deploy's exact container builder, preserving capabilities, device mapping,
read-only preflight/census/verify, writable apply, tmpfs, network denial and
2 GiB memory/swap. Apply owns the final verified marker.

Serving now uses the real broker admission client, lifecycle manager, router,
owner apps and kernel UID peers. The fixture uses full runtime schemas and the
production image's module path. Reconnect uncovered and fixed three actual
cutover gaps: host admission now reads the canonical broker custody tombstone;
seeding creates the broker-writable custody directory even without retained
credentials; local provider definitions stay private to their owner instead of
attempting the retired broker-reader group assignment. Descriptor publication
already uses the broker channel. Deletion refusal tests retain missing-state,
normal/destruction and post-binding checks, plus a stale old-table discriminator.

Reconnect confirms model use through the shared model-connect door on a
shared-store clone, preserving the original shared-authority restore fence.
Its owner writes remain on the migrated XFS trees. Both paired restores use the
actual restore script and GNU tar byte comparison against the synthetic source;
the second explicitly accepts owner-write loss after serving write readback.
The process smoke does not replace separate native-sandbox isolation acceptance.

The report is one fixed step name and PASS/FAIL per line, followed only by
nonnegative integer counters. The successful synthetic report is:

```text
setup PASS mounts=1
preflight PASS
census PASS owners=2 centers=4 rows=27
apply PASS
verify PASS manifests=3
restore_before_write PASS comparisons=1
crash_resume PASS interruptions=2 comparisons=2
serving PASS owners=2 centers=4 history=4 history_turns=4 pending=4 pending_saved=4 controls=8 plans=8 writes=4 reconnect=2
restore_after_write PASS comparisons=1
cleanup PASS
```

Tests assert every step and count, scan for identifiers, paths and content,
and verify that failure diagnostics never enter the report. Cleanup is reported
on failure as well as success. The archive has two owners with two centers each,
all six census classes and all four non-conflicting dropped reasons.

Lead command, from the same head with its image already built:
`python scripts/rehearse_box_move.py --archive <absolute-tinyassets-data-YYYY-MM-DDTHH-MM-SSZ.tar.gz> --image tinyassets-c6-rehearsal:s6-<head>`.
The harness does not build or pull. Development accessed generated synthetic
archives only. Task 6 remains open for the lead's separate acceptance; this pass
has no deployment, push, merge, branch change, new worktree or delegated work.

## Slice 6 round-5 census corrections (2026-10-17)

Input is the lead's redacted counts-only result at `edfaab53576`: 16 file
refusals (six root files, five platform-store files, two broker four-component
paths, one broker three-component path, one platform two-component path and
one other four-component path). This pass reads code and generated synthetic
fixtures only. It does not rerun a census against real data or claim these
16 refusals are cleared. The rehearsal harness and its outstanding acceptance
are unchanged.

Center wins in the shared row-evidence resolver. It tracks admitted center
owners separately from principal-only candidates and carries that evidence
through reviewed parent joins. An admitted center clears unadmitted-principal,
unknown-center and ownerless-parent evidence. A competing admitted owner still
refuses, and a missing parent still drops with `orphan_reference`. A named
unknown principal cannot select an owner without an admitted center. Versions
and schedules whose definition is gone are counted on the candidate owner's
`workflow history` line, with separate `versions` and `schedules` counts.
Unresolved usage rows still drop as `usage reservation history` and
`provider bindings`; usage rows with an admitted center follow the same
center-wins rule.

| Code-backed file family | Census disposition |
| --- | --- |
| Broker identity, plans, policy, outbound, custody, operations and next-wake SQLite names | Existing real databases retain table-by-table review; exact shared names also classify empty files as broker. |
| SQLite WAL/SHM/journal files at reviewed store locations, including detached sidecars and hashed policy-account directories | Regenerated; the read-only SQLite snapshot still resolves committed rows before classification. |
| Legacy `<owner-sha256>.key` under the outbound ledger's `browser-vault` directory, at root or under `.broker` | Broker, matching `browser_vault._owner_key`; arbitrary key locations/names still refuse. |
| Broker `fence.json`, policy `.deleted`, layout and cutover markers | Broker. |
| `platform-expected-instance.json`, `release-state.json`, billing verification marker | Router. |
| Custody/refresh locks, recovery PID, liveness/tree locks, runtime status and per-consumer heartbeat files | Regenerated. |
| Interrupted layout/cutover, broker fence and vault atomic-publication files at their writer's locations | Regenerated. |
| Existing retired CLI homes/local vaults, stale database copy, unknown directories and never-admitted centers | Existing counted drops retained. |
| Any remaining unidentified platform/root/account file, including a purported platform vault index without a code-backed filename/location | Refused with its masked filename pattern; no extension-wide custody assumption. |

Evidence comes from `CENSUS_RULES`, `broker/process.py`, `broker/owner_reads.py`,
`broker/owner_identities.py`, `broker/policy.py`, `broker/browser_vault.py`,
`broker/fence.py`, `broker/vault.py`, `broker/credential_refresh.py`,
`storage_layout.py`, `platform_runtime_provenance.py`, `process_liveness.py`,
`owner_lease.py`, `singleton_lock.py`, and the heartbeat writer in
`control_plane/tick.py`. Unknown basenames print only `<file>.<ext>` (or
`<file>` without a conventional extension), while known code-constant filenames
may print verbatim. Directory identifiers remain masked. The next authorized
counts-only census can therefore identify a remaining file family without
exposing a basename or opening file contents for diagnosis.

Synthetic coverage proves center wins directly, through a definition and into
run dependents; a present ownerless/unknown-center definition cannot drop a
version in an admitted center; missing definitions still drop and report
workflow history. Unresolved usage drops and their descriptions remain exact.
Each newly classified file family has a synthetic case, and unknown root,
broker and platform files prove exact masked patterns and refusal. Existing
cross-owner, missing-parent, source-immutability and redaction assertions remain.
D6 and the owner-isolation delta spec describe the corrected rule.

Validation receipts are outside the repository at
`%TEMP%/ta-s6-round5-validation/`. Changed-file Ruff and `git diff --check`
pass. Whole-repository Ruff has 33 existing findings in seven files verified
unchanged from the starting head. No sub-agent, new worktree, branch change,
host git configuration change, push, merge or deployment was used.

Round-5 validation:
- Requested census/migration patterns, touched-module cutover consumers and
  every `GUARDS` key selected **46 files**. The unprivileged oracle recorded
  **1,153 passed**, two broker-directory ownership failures, 27 root-fixture
  errors and one existing root-only special-bit skip. Every nonpassing case
  passed in the root run; none is waived or credited from a skip.
- Final root census/layout/cutover selection: **137 passed, zero skips**,
  `root-final.log` / `root-final.xml`. This includes all **84 census cases**
  after the last fixture correction. XML reconciliation in `coverage.json`
  confirms **1,184 distinct cases passed** across their required Linux venues,
  with zero unresolved cases. The earlier root receipt is 136 passes before
  adding the root-ledger legacy-key case.
- All **35 structural guard files passed, 900 cases** in `user.xml`.
- Changed-file Ruff, strict OpenSpec validation and `git diff --check` pass.
  `ruff-all.json` records the 33 pre-existing repository-wide findings.
- Post-commit hygiene uses `conv/c6-owner-daemon-s5` through `HEAD`, with
  `Registry row(s): S8` and the unchanged existing `Test-Removal: retired --`
  declaration in `hygiene-body.txt`; its receipt is `hygiene-final.log`.

## Slice 6 round-6 census corrections (2026-10-17)

Only the supplied redacted counts/platform names and synthetic fixtures were
used. The synthetic census with all 16 identified files is `ready=True`.
The lead must establish the real-data result separately; no production tree
or backup was read, extracted or opened in this pass.

Versions use the admitted definition author, otherwise their admitted
publisher, otherwise drop. An admitted publisher keeps the version's own
snapshot even without the definition. Schedules follow the definition or a
version, including the actual schema's `branch_def_id` join to version rows.
Competing admitted owners still refuse. Synthetic extraction asserts each
owner's retained workflow-history counts, both author and publisher paths,
verbatim snapshots, and the single unowned version drop. Dropped workflow
records retain `workflow history` version/schedule counts on their candidate
account line or the explicit unowned bucket.

| Identified files | Disposition |
| --- | --- |
| Two broker outbound-proxy account audit JSONL files; architecture-signing JSON | Broker custody, private ownership/modes, no box copies. |
| Three door handshake files | Private router custody, no box copies; names and contents absent from census reports. |
| Engine MCP route table; reset barrier; run-file blob-root lock | Regenerated. |
| Stale backup SHM and WAL | Counted drops with the stale backup; not opened for database inspection. |
| Retired executor configuration; external-agent refresh marker | Two counted `CLI login state` file drops. |
| Run-file custody body | Follows its `run_file_objects` row via `storage_key + '.body'`; moved verbatim or counted as a dropped upload. Object attribution also resolves the operation parent. |
| Scheduled-fire JSONL | Split by admitted center into each owner's root `.scheduler_fires.jsonl`; unknown/non-admitted centers count as dropped entries. |
| Root activity ledger | Split by admitted actor, falling back to admitted target, into each owner's root `ledger.json`; unresolved entries count as drops. |

The root ledger path matches `api.branches._append_global_ledger` and
`api.engine_helpers._append_ledger`. The scheduler is retired with no current
reader; its original relative path is retained in each owner's box root.
Reports describe these as `scheduled-run history, N entries` and
`activity ledger, N entries`, with pseudonymous per-owner counts and explicit
unowned drops. They also list the five accepted user costs: model reconnect
and retired CLI connections, tap-to-connect keys, hook URL changes, removed
local image descriptions and billing usage figures.

Apply rebuilds both unpublished splits on resume, reads back every owner's
entry count and content, and checks retained plus dropped equals the source
total. Synthetic mutation probes refuse changed source, owner counts, drop
counts or output. A real fork exits 137 via `CUTOVER_CRASH_AT` at
`after_scheduled_history_split`, proving source preservation between splits,
resume without duplication, output ownership, broker/router custody, cleanup
after verification and completed-apply idempotence. Both retained and dropped
custody-body cases use that crash proof. The original source files are removed
only during verified publication. D6 and the owner-isolation delta spec match.

Validation receipts are outside the repository at
`%TEMP%/ta-s6-round6-validation/`. No sub-agent, new worktree, branch/configuration
change, push, merge or deployment was used. The user's local-only scope leaves
the lead's production census, independent review and live acceptance outstanding.

Round-6 validation:
- Requested census/migration/restore patterns, touched tests, all 35 structural
  guard files, the three affected heavy files and the deploy transaction
  consumer ran through the Linux oracle. The unprivileged selection recorded
  **1,690 passed, two failed, 29 root-fixture errors and one existing root-only
  skip**. All 32 nonpassing cases passed under actual root; none is waived or
  credited from a skip. `user.log` / `user.xml` preserve the original evidence.
- Final root census/layout/cutover selection: **146 passed, zero skips**,
  `root-verified.log` / `.xml`. The final history fixture explicitly distinguishes
  a registered never-admitted center from an unknown center; its focused
  crash/classification/reconciliation rerun passed **8/8**, `history-final.*`.
- Final 35 structural guard files plus census: **1,051 passed, zero skips**,
  comprising **960 guard cases and 91 census cases**, `guards-verified.*`.
- The exact root Docker-engine harness command passed **6/6**, zero skips,
  `harness-verified.*`. Every one of its ten rehearsal steps passed, including
  both manifest comparisons after crashes, serving, reconnect confirmation,
  both real paired restores and report privacy.
- `coverage.json` reconciles **1,728 distinct passing cases**, zero unresolved
  cases, across the required Linux venues. No test was skipped, quarantined or
  weakened to obtain those passes. The two old publisher-disposition probes
  now assert the founder's exact admitted-author/publisher behavior and retain
  their conflict and reason/count checks.
- Changed-file Ruff, strict OpenSpec validation and `git diff --check` pass.
  Whole-repository Ruff retains **33 findings in seven files** verified unchanged
  from the starting head; `ruff-all.json` records them.
- Post-commit hygiene uses `conv/c6-owner-daemon-s5` through `HEAD`, with
  `Registry row(s): S8` and the unchanged existing `Test-Removal: retired --`
  line copied into the external `hygiene-body.txt`; `hygiene-final.log` retains
  the command's final result.
