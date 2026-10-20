# Lane b4: routing snapshot

Baseline: `51b0005594d8af17ff25b5dd662a957adb20a62c`, branch
`conv/c6-research-shape`. Status: implemented; validation in progress. No completion claimed.

Binding scope: C5, T1 and T5; C6 supplies the admission record. The research
test is research.md:117. No shared owner-volume content access is permitted.

## Decisions and their owners

| Decision | Sole component | Requirement |
|---|---|---|
| Authenticate incoming identity and select its installed route | Router/map | T1, T5 |
| Mutate admission; serialize cold wake, withdrawal and explicit revocation | Broker admission/lifecycle authority | C5, C6 |
| Publish ordered admission, socket and secret projections | Broker admission/lifecycle authority | C5 |
| Atomically install projections and refuse stale updates | Router/map | C5 |
| Admit an owner channel or public egress stream | Egress proxy | C6, T5 |
| Select a scheduled owner wake | Waker | T1, C5 |
| Accept the bound caller credential | Owner box ingress | T1 |
| Store the persistent box credential | Private broker identity store | C5 secret custody |
| Supply the one-box read-only runtime credential copy | boxhostd | C5 secret custody |

## Deletion inventory

Baseline ranges (inclusive) removed or replaced by this lane:

- `tinyassets/owner_router.py:61-63,66-93`: synchronous admission, host resolve/release,
  and `OWNER_BOX_ROUTE` lookup (31 lines replaced with snapshot selection).
- `tinyassets/owner_router.py:194-195,217-220`: per-request release calls (6 lines).
- `tinyassets/boxes/host_service.py:138-146`: router resolve/release RPCs (9 lines).
- `tinyassets/boxes/host_service.py:156-158`: router-originated wake dispatch
  (3 lines replaced with broker-only bound wake).
- `tinyassets/boxes/volume_lifecycle.py:46`: per-start rotation request (1 line).
- `tinyassets/broker/owner_identities.py:228-232`: start-triggered credential
  replacement (5 lines).
- `tinyassets/broker/owner_identities.py:403-405,407-408,411-417`: route/rotate client protocol
  (12 lines replaced with cold-wake-only request).
- `tinyassets/boxes/daemon_control.py:30-37`: direct host wake helper (8 lines).
- `tinyassets/broker/waker.py:9-10`: direct host-wake import (2 lines); C5 now
  requires the injected broker lifecycle function.
- Per-request erasure-ledger reads and router deletion orchestration were already
  absent at the baseline; no deletion credit is claimed for them.

## Shared additions and structural argument

C5 requires `broker/routing.py` (publication, ordered retirement and serialized
cold wake), `routing_snapshot.py` (atomic in-memory installation), broker
admission locks/callbacks and the broker process's publication task. This task
only establishes/retries delivery to a router incarnation: installed routes have
no expiry. A durable sequence orders broker publications; a router incarnation
nonce prevents pre-restart deliveries from initializing a fresh router.

C5 secret custody requires the initial cutover secret, persistent broker store,
and broker-authenticated snapshot transport in `router_credentials.py`.
Routing installation has its own outer protocol operation: a box's credential
request cannot reach it. C5 requires broker-only bound launch records at
`boxes/host_service.py` and the generation refresh after volume recovery in
`boxes/volume_lifecycle.py`. Neither path opens owner-volume content. C6's
admission record remains the single work-admission authority. T1's box ingress
still checks the box-specific credential on each request.

Admission withdrawal first publishes the withdrawn state, then closes channels
and stops old ingress before returning.
A failed router delivery can acknowledge withdrawal only after that stop; a
failed stop propagates as pending. Revocation stops old ingress before changing
the durable secret and installing its replacement. Wake holds the same owner
lock, republishes current state, and supplies the complete bound launch record;
its reply contains no route that could overwrite a newer snapshot.

C5 requires the router to reject HTTP serving while its projection is absent,
including public handlers; this check reads only process memory. C5 also requires
the waker to receive the broker lifecycle authority explicitly, with no default
direct-host wake path. No deploy module was changed. Existing local deployment-drain and authentication
checks remain separate from the work route lookup. The router RPC entry points for host leases are deleted. Residual internal host
lease/cleanup helpers are exercised only by older lifecycle/roll tests; they
have no production caller. That dead-code cleanup is reported outside this
lane rather than expanding b4 into the roll/cleanup test contracts.

## Proof required

The real broker/two-process router tests pass the core C5 paths: awake
forwarding with all control-plane RPCs unavailable, stale snapshot rejection,
broker cold wake, stable secrets across sleep/wake, stop-before-rotation, fresh
router snapshot requirement, and missing/forged/wrong-box ingress refusal. The
full touched-file rerun also exercises a broker wake with pending restore work.

The first broader root run reported 187 passes and 5 failures. Fixes retained
the existing restore-read refusal, published withdrawal before draining, and
made the kernel-peer refusal test assert rejection before parsing a frame. Its
focused rerun passed all 14 cases; no test was skipped or xfailed.

Passed: all 35 structural guard files plus router-store guard (1,021 tests),
and focused real-path withdrawal/channel-closure checks (2 tests).

Passed: current changed-file ruff and strict OpenSpec validation.

Running: root Linux oracle touched tests, pinned-runtime
synthetic archive rehearsal, and full four-shard reproduction.
Passed: final shape registry and background-authority inventory closure.
Passed: hygiene against `conv/c6-owner-daemon-s3c` exits 0.
The committed-tree rerun also passes ruff and strict OpenSpec.

No production, external API, production credential, deployment, git-config or
branch-changing operation is authorized in this lane. Independent delegation
is prohibited by the lane brief.

## Validation progress

- Latest router/waker/store-guard root run: 107 passed (router-final.xml).
- Expanded root touched run: 310 passed, 6 failed. Four failures were the
  superseded cutover expectation of no initial secret; it now asserts one
  unique credential for each admitted owner and retains byte-identical resume
  verification. Two proofs require the Docker-engine venue, omitted from that
  invocation; both are being rerun with the required venue.
- First pinned-runtime archive run passed setup, census, apply, verify,
  restore-before-write, crash-resume, and host startup; serving refused the
  synthetic router child because its role groups/capabilities were incomplete.
  The child now uses the same complete role retirement as the other router
  proof children; no production identity check was relaxed. Cleanup passed.
- Native content smoke retains its existing local launcher. Its router fixture
  is initialized from actual broker-bound records; the separate production
  launch and real broker/router tests exercise authenticated snapshot delivery.

Test-Removal: retired -- C5 retires per-start rotation and router resolve/lease RPC behavior; replacement proofs cover stable credentials, broker-only cold wake and box-admitted work. Earlier lane-b changes retire the independent C6 authorities and router deletion orchestration.

## Exact shared deletion/replacement audit

Old line numbers against `51b0005594d`; replaced lines count as removals here.
New modules have no old ranges. This is a literal diff audit, not a net-size claim.

| File | Removed old ranges | Lines |
|---|---|---:|
| `tinyassets/box_layout_migration.py` | 161, 2165 | 2 |
| `tinyassets/boxes/admission.py` | 80, 89, 100, 109-110, 112-114 | 8 |
| `tinyassets/boxes/daemon_control.py` | 30-37 | 8 |
| `tinyassets/boxes/host_service.py` | 138-146, 153, 156-158, 165 | 14 |
| `tinyassets/boxes/owner_daemons.py` | 114, 117, 125, 128, 258, 264, 313, 351 | 8 |
| `tinyassets/boxes/volume_lifecycle.py` | 37, 46 | 2 |
| `tinyassets/broker/admission.py` | 132-136 | 5 |
| `tinyassets/broker/box_admission.py` | 11, 24, 31-34, 39-40, 42 | 9 |
| `tinyassets/broker/owner_identities.py` | 215, 228-232, 236, 244-247, 249, 324, 403-405, 407-408, 411-417 | 25 |
| `tinyassets/broker/process.py` | 177 | 1 |
| `tinyassets/broker/server.py` | 589-590, 814, 816-819, 821-823, 827, 873 | 12 |
| `tinyassets/broker/waker.py` | 9-10, 18, 32 | 4 |
| `tinyassets/owner_router.py` | 1, 3-4, 10, 57-58, 61-63, 66-93, 173, 187, 194-195, 217-220 | 45 |
| `tinyassets/router_credentials.py` | 32, 36, 109-111, 212-213, 215-216 | 9 |

Total old shared lines removed or replaced: 152.

- Corrected root cutover/real-volume/outage run: 62 passed, 0 failures/errors
  (`cutover-volume.xml`), including both Docker-engine proofs omitted earlier.
- Hygiene against `conv/c6-owner-daemon-s3c`: exit 0. The baseline spans prior
  lanes as requested; retired contracts are explained above.
- The affected-test selector returns ALL because the broker fixture is in the
  conftest import closure. Its 34 heavy files are also running in the required
  runner's Linux venues, in addition to the requested four-shard reproduction.

- Connection-door IPC fixture now receives a real broker publication before
  serving its separate router root; all 10 tests pass with the unchanged
  no-owner-state assertion.
- Full first-contact file: 57 passed after serializing synthetic broker startup,
  so concurrent fixture callers cannot observe its pre-install snapshot.
- The first heavy run had 1,357 passes and that startup-race failure. It also
  reported nine stale quarantine entries in the pre-existing deploy-fence
  ledger. Those entries are outside b4; no gate or quarantine was changed.
- Rehearsal venue: the generic oracle image lacked `/opt/venv`; the older launch
  image lacked `rclone`. A network-disabled disposable image now derives from
  `tinyassets-linux-oracle:bf4b7c48e0f6`, adds a real `/opt/venv` with its existing
  site packages, and verifies the interpreter, app dependencies and rclone.
  Image: `ta-b4-rehearsal:3b4dc70`. Production source/configuration is unchanged.
