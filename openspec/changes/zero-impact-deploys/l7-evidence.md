# L7 implementation evidence

## First slice: RED component control

Verified on Linux oracle (Python 3.11.16, uid 1001, Chromium sandbox enabled).
`tests/test_deploy_during_traffic.py`: 1 passed, no skips. The test passes only
when the continuity invariant is RED: a send encounters connection refusal
after SIGTERM closes the origin listener, and an active real FastMCP response
ends with `RemoteProtocolError` without a terminal reply. One exclusive effect
file and the workspace update survive. The browser preserves its unsent draft.

The isolated edge explicitly maps upstream transport failure to 520. This is
not a measurement of Cloudflare's mapping. The page and deterministic MCP tool
are fixtures, not the production application/provider. Source digests, HTTP,
turn and effect evidence are in `l7-red-baseline.json`; screenshots and server
logs are emitted as CI artifacts. No production requests or changes occurred.

Command:

```powershell
$env:MSYS_NO_PATHCONV='1'
python scripts/linux_oracle.py --out C:/Users/Jonathan/AppData/Local/Temp/l7-evidence --env DEPLOY_TRAFFIC_EVIDENCE=/out -- -q tests/test_deploy_during_traffic.py --basetemp /tmp/b
```

The new draft-PR workflow runs this component oracle without skipping drafts.
It is not yet the complete release gate: task 1.1 remains unchecked pending
real Compose old/new image digests, full app and multi-surface traffic,
ownership/resource evidence, and enforcement before production rollout.
The existing production deploy workflow and ingress are unchanged.

## Second slice: dormant acceptance and replay

`tinyassets/storage/ingress_journal.py` provisions an explicit independent v1
SQLite journal. Normal opens require that schema; no runtime-layout migration
or public route is added. A trusted adapter must supply an authenticated
principal/target/thread/operation scope, a current-authority context held
through access/import, and existing admission policy checks. The client key
is `client_send_id`, with the existing 1–128 ASCII key contract. Server ingress
IDs are receipt identities, not new client keys.

Acceptance commits exact payload bytes and scoped identity before returning.
Retries return that identity; changed bytes conflict. Reply sequences commit
before publication, support cursor reads and reject changed/gapped events.
Terminal payload expiry keeps identity tombstones and never deletes pending
work. Runtime import mapping and reservation share the existing admission
transaction; loss of either acknowledgement does not create a second run.

The same Linux traffic exercise now has a candidate variant. A send made while
the old listener is closed receives 202. A fresh process reopens the journal
and imports using real `conversation_run_admissions`; two replay attempts
produce one admission and one deterministic transactional fixture effect. The
fixture's terminal event is durable, but no actual runtime turn executes:
`acceptance_import` is GREEN, execution is a fixture, and the old long turn STILL
cuts off. Overall continuity stays RED; this is not send-completion proof.
`l7-acceptance-evidence.json` records this distinction.

Verification after review corrections: Linux oracle **72 passed, zero skipped** across
`test_ingress_journal.py`, `test_deploy_during_traffic.py`,
`test_conversation_run_admissions.py`, and `test_converse_turn_cost.py`.
Changed Python ruff checks pass; plugin mirror rebuilt and import probe passes.
Actionlint and strict OpenSpec validation pass. Hygiene: 0 removed / 0 tampering.
Merged `origin/main` at `69612a32d6` before final push (merge `917a73d9da`);
no runtime code from the acceptance slice conflicted.

Task 2.1 remains unchecked: public authenticated adapters, principal deletion
and custody policy, quotas, production receipt/status integration, the S8b pump,
and complete runtime ingress wiring are not implemented. The journal and test
fixtures grant no execution authority and do not enable a second owner. No
agent guidance or always-sent prompt content changed. As-built specs are not
synced because no rollout or real-user deployed proof is claimed.

Data-loss/cross-user mutation cases: reject pre-commit failure; roll back both
admission and mapping; replay after lost post-commit ack; preserve exact bytes;
deny changed payload/key scope; deny revoked access; preserve terminal
tombstones. Each is exercised by the focused journal tests.

## Claude review (one cross-family round)

Verdict **ADAPT**. Dispositions:

- **AGREE** (findings 2/3): bind import to the live canonical runtime scope and
  verify the returned admission under that scope on first import AND replay.
  Store runtime identity in the mapping. This adapter explicitly refuses
  noncanonical threads and non-converse operations rather than aliasing them;
  they need separate adapters. Tests cover every scope dimension and a callback
  returning another principal's admission. The current canonical adapter also
  requires the UUIDv4 keys used by the app; the independent journal preserves
  the broader existing `client_send_id` syntax for future adapters.
- **AGREE** (finding 4): label only acceptance/import GREEN. The deterministic
  transactional effect sink is fixture evidence, not execution fencing or real
  turn completion. Overall continuity remains RED.
- **DISAGREE_EVIDENCE** (finding 1): required shards already run through
  `scripts/linux_oracle.py` with Chromium and the `ta-jail-userns` AppArmor
  profile (`.github/workflows/tests.yml`, Allow user namespaces for the jail
  container only / Run this shard). No need to remove this test from required
  coverage. The dedicated job also runs the journal guards explicitly.
- **AGREE** (finding 5): policy callback is a pure check, not external quota
  charging. Provisioning tightens the containing directory to 0700 so SQLite
  sidecars are private too, and closes its connection explicitly.
- **AGREE** on incomplete task order/completion: full 1.1 and 2.1 remain
  unchecked; no production adapter/ingress rollout is authorized by these
  component results. Reviewer found no lane collision. No approval verdict is
  claimed after corrections; this remains a draft with outstanding full proof.

## CI repair, 2026-10-05

Merged origin/main before work. Classified ingress_journal.py in FENCE_BEFORE_C2: it writes acceptance and reply state without owner-generation fencing; handover remains disabled and the existing gate remains intact. Plugin mirror regenerated. Linux oracle: 34 passed / 0 skipped (owner_stores, ingress_journal, deploy_during_traffic, converse_turn_cost); changed-file ruff PASS. No production configuration changes.


## Authenticated HTTP ingress slice, 2026-10-05

CI repair 0996d37699 is green on GitHub: all six affected shards, the dedicated
traffic job, required-tests, invariants, lint and scope gate. Claude approved
that exact dormant slice; raw review is in l7-ci-repair-review.md.

The next slice installs an optional adapter in create_streamable_http_app,
inside the real AuthContextMiddleware and outside runtime MCP dispatch. Default
startup supplies no adapter. A receipt-aware harness client negotiates accept-v1
on /mcp; receipt-v1 observes the same client_send_id. The authenticated principal
and current home/admin authority are held through acceptance and receipt reads.
The policy callback is required; only the fixture supplies a policy in this
slice. See l7-ingress-slice.md for supported arguments and exact-body retry rules.
No production deploy config, app defaults, prompt head or static budget changed.

The retained RED control still maps real listener refusal to fixture HTTP 520.
The candidate browser receives 202 through the production HTTP factory while
the execution listener is closed. After killing and replacing the frontend,
receipt-v1 returns the same ingress_id, exact-body retry returns that receipt,
and two fresh importer processes produce one canonical admission. Evidence:
l7-ingress-evidence.json. Runtime effects/terminal completion remain the explicitly
labelled transactional fixture sink; long-turn continuity remains RED. The
fixture substitutes only token issuer/cloud observations and disables execution
lifespan startup. This is component proof, not Compose or production app proof.

Verification before the final main merge: Linux oracle 190 passed / 0 skipped,
including test_ingress_http, test_ingress_journal, test_deploy_during_traffic,
test_owner_stores, test_conversation_run_admissions, test_converse_turn_cost,
test_universe_server_isolation (heavy), test_mcp_discovery_html,
test_onboarding_auth_boundary and test_cloud_admission_serving_startup.
An earlier oracle copy stopped on a changing __pycache__ during local feedback;
that was not counted as verification. Local HTTP feedback: 15 passed.

Claude cross-family review for this separate next slice: APPROVE, no floor or
correctness findings (l7-ingress-review.md). AGREE with its scope limitation:
no execution/production enablement claim. Merged origin/main 70c30da9f9 via
9e71824e2e before final verification/push; merge was clean.

Data-loss/cross-user guard evidence: real middleware refuses unsigned/bad-token
requests with its existing MCP linking error (never an ingress receipt); foreign
principal and revoked ACL cannot accept or read receipts; payload conflict is
409; injected failed commit is 503 with zero stored rows; exact payload and
receipt survive frontend process death. No test was removed, skipped or xfailed.
Full 1.1/2.1, quota/custody erasure integration, S8b pump, production client
negotiation, long-turn continuity, rollout and as-built spec sync remain undone.

Final merged-main verification: the same ten-file Linux oracle command passed
again, **190 passed / 0 skipped** in 42.41s. Changed-file ruff PASS, plugin build
and import probe PASS, strict OpenSpec validation PASS, git diff --check PASS.
Final fetch confirms origin/main 70c30da9f9 is already merged. Both cross-family
reviews approved their bounded slices; production configuration is unchanged.

Command (PowerShell sets MSYS_NO_PATHCONV=1 first):

    python scripts/linux_oracle.py --out <external-temp>/l7-ingress-final --env DEPLOY_TRAFFIC_EVIDENCE=/out -- -q tests/test_ingress_http.py tests/test_ingress_journal.py tests/test_deploy_during_traffic.py tests/test_owner_stores.py tests/test_conversation_run_admissions.py tests/test_converse_turn_cost.py tests/test_universe_server_isolation.py tests/test_mcp_discovery_html.py tests/test_onboarding_auth_boundary.py tests/test_cloud_admission_serving_startup.py --basetemp /tmp/b

## CI follow-up: exact HTTP-stream inventory

GitHub Tests run 37402901697 passed five affected shards; shard 6 reported only
an unregistered request.stream call in AppIngressMiddleware.__call__, in source
and plugin (2309 passed / 1 failed). The traffic job passed. This is the existing
name collision between bounded Starlette HTTP-body reads and graph streams.
Registered both exact callsites with occurrence count one and updated the
background-authority audit. No scanner/matcher rule, product code or test changed.
Extra calls still fail the gate. Claude reviewed this gate correction separately:
APPROVE (l7-inventory-review.md); AGREE with its classification and exact-count
reasoning. Earlier product review remains applicable.

Linux oracle: **56 passed / 0 skipped** across test_background_authority_inventory
(including its mutation guards), test_ingress_http, test_deploy_during_traffic,
test_owner_stores and test_converse_turn_cost. Changed-script ruff PASS. No
runtime/mirror content changed after its verified build. The earlier 190-test
merged-main proof still covers the unchanged product slice. Fetched and merged
origin/main before this correction's push: already up to date.
