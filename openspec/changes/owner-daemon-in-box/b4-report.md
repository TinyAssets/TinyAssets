# Lane b4: routing snapshot

Baseline: `51b0005594d8af17ff25b5dd662a957adb20a62c`, branch
`conv/c6-research-shape`. Status: implementation committed; incomplete, handoff required. Five integration tests fail.

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
The snapshot uses the admission record's identity digest as its key, so the
same projection also preserves C6's digest-only erasures after identity removal.
It contains the complete admission map, not just owners with live box bindings.
The authenticated identity derives that key locally; no additional lookup or
exclusion authority is introduced.

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

## Real-path proof

`tests/test_owner_router.py` uses a real broker, its authenticated credential
listener and two separate owner processes. The decisive cases are:

| Requirement | Proof |
|---|---|
| Awake traffic survives control-plane outage | `test_awake_forwarding_survives_every_control_plane_rpc_outage` |
| Withdrawal cannot be undone by old publication/wake | `test_withdrawn_route_rejects_delayed_snapshot_and_wake` |
| Failed withdrawal delivery stops ingress before acknowledgment | `test_withdrawal_without_router_delivery_stops_old_ingress` |
| Cold wake uses broker; credentials survive sleep/wake | `test_cold_request_uses_broker_and_keeps_credential` (including pending restore) |
| Rotation retires acceptance before publication | `test_rotation_stops_old_ingress_before_publishing_replacement` |
| Restart cannot serve without a current snapshot | `test_router_restart_needs_snapshot_from_its_own_incarnation` |
| Missing, forged and other-box credentials fail | `test_owner_socket_requires_current_secret` |
| Bound box channel cannot inject routing snapshots | `test_box_channel_cannot_supply_owner_or_revoke_another_owners_credentials` |

The existing deleted-authentication file also proves that digest-only erasures
remain effective after projection, without a synchronous broker read.

## Validation receipts

Runtime source commit: `902b9567957`. Receipts are outside the repository at
`%TEMP%/ta-b4-proof/`. The full reproduction has completed: 25,103 passed, 5 failed, 0 errors,
and 18 existing venue skips. The affected heavy tests have zero failures/errors.
No completion claimed; no skip or xfail was added by this lane.

| Check | Result / receipt |
|---|---|
| Root Linux: final router, deleted authentication and disaster recovery | 53 passed; `digest-snapshot.xml` |
| Root Linux: router, waker and store boundary | 107 passed; `router-final.xml` |
| Root Linux + Docker engine: cutover, real volumes, cold deletion, delivery outage | 62 passed; `cutover-volume.xml` |
| Connection-door IPC | 10 passed; `connection-door-fixed.xml` |
| First-contact concurrency | 57 passed; `first-contact-fixed.xml` |
| Concurrent-owner isolation | 5 passed; `concurrent-owners-fixed.xml` |
| Real HTTP ingress process | 1 passed; `deploy-ingress-fixed.xml` |
| All 35 structural guard files plus router-store guard | 1,021 passed; `guards-final.xml` |
| Pinned-runsc synthetic archive | 1 passed in 935s before the digest-key change; `archive-complete-image.xml`; rerun needed on final source (normal shards deselect this slow proof) |
| Changed-file ruff; shapes; background-authority closure; strict OpenSpec | Passed |
| Hygiene, base `conv/c6-owner-daemon-s3c` | Exit 0; `hygiene-complete.log` |
| Full shard 1 | 5,513 passed / 3 failed, 0 errors, gate exit 1; `junit-final-1.xml` |
| Full shard 2 | 7,867 passed, 0 failures/errors, gate exit 0; `junit-final-2.xml` |
| Full shard 3 | 5,925 passed / 2 failed, 0 errors, gate exit 1; `junit-final-3.xml` |
| Full shard 4 | 5,798 passed, 0 failures/errors, gate exit 0; `junit-final-4.xml` |
| All 34 affected heavy files | 1,358 passed, zero failures/errors; `heavy-final.xml`; gate exits 1 solely for nine stale quarantine entries |

The affected selector returns ALL because the broker fixture is in the conftest
import closure. Both normal shards and all heavy files therefore run. Earlier
failures exposed incomplete synthetic startup and an incomplete admission
projection; their assertions remain in force. No skip, xfail or quarantine was
added, and no gate was weakened.

Rehearsal uses the pinned runsc and a network-disabled disposable image,
`ta-b4-rehearsal:3b4dc70`, derived from the Linux oracle image with a real
`/opt/venv` using its existing packages. The interpreter, app dependencies and
rclone were verified. This fixes the test venue, not production configuration.
The native content smoke retains its local launcher; separate production
launcher and real broker/router proofs exercise authenticated snapshot delivery.

## Required handoff

AGENTS.md:51 requires: "Same error thrice or same finding twice: hand off,
don't patch." The final shard 1 receipt repeats the connection-door fixture
finding: synthetic snapshot initialization starts a second in-process broker
under a router root that already belongs to a real broker/process fixture.
Patch work stops here. No new agent was dispatched.

Failures in `tests/test_model_connect_door_lifecycle.py:442`:

- `test_door_exchange_box_call_refresh_disconnect[dcr]`
- `test_door_exchange_box_call_refresh_disconnect[registered]`
- `test_door_exchange_box_call_refresh_disconnect[preset]`

All three finish their exchange, owner call, refresh/revoke and disconnect
assertions, then fail the existing router-directory boundary assertion because
`.broker` was created there. Evidence: `junit-final-1.xml`. The failure is in
this lane's test bootstrap, not a pre-existing failure or an out-of-scope waiver.

Relevant seam: `tests/support/broker_double.py:307-317` wraps snapshot lookup
and unconditionally starts its own registry for a new root. The model-connect
fixture supplies a real subprocess broker rooted at `custody`, overrides the
supervisor, and serves a separate `router` root, but does not initialize its
projection from that broker. The earlier connection-door proof was corrected
individually; this recurrence needs a fresh review of the fixture bootstrap
contract. Keep the router-directory assertion unchanged. Do not add a production
check, fallback, filesystem permission exception or per-request broker call.

The completed shard 3 also exposes two remaining b4 protocol-test updates in
`tests/test_owner_box_control.py` (these are not out-of-scope waivers):

- `test_control_socket_uses_kernel_router_identity_and_returns_bounded_errors`
  fails at line 110: the child imports the deleted `daemon_control.ensure_awake`
  and still expects router `resolve`/`release` success.
- `test_roll_socket_pins_broker_and_deploy_identity_and_refuses_router` fails at
  line 252: its router child expects `resolve`/`release` success, now refused.

Those tests must prove the C5 replacement: broker-only bound wake and router
refusal of lifecycle/lease operations, retaining the existing roll-role and
error-boundary assertions. Do not restore the deleted API to satisfy them.

Remaining work: resolve the test bootstrap contract and these two protocol
expectations, run their root Linux proofs, rerun the failed full shards (and any
other affected checks), and run the pinned archive on the final source. The earlier archive receipt predates the digest-key
projection change. The standard `not slow` shard command does not rerun it.

## Scope and remaining drift

The final heavy run reported nine passing tests still listed in the existing
quarantine for `test_retire_cheat_loop_deploy_fence.py`. That ledger is outside
b4 (`.github/known-failing-tests.txt:22-30`) and remains unchanged. The gate exits 1 solely for stale quarantine entries;
pytest has zero failures and errors. This is not reported as a green gate.

Residual internal host lease helpers have no production router caller, as
explained above; their cleanup belongs to the lifecycle/roll test lane. Parent
lane b remains open for b5. No production, external API, production credential,
deploy workflow, git configuration, branch switch or agent delegation was used.

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
