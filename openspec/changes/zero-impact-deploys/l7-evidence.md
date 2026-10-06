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
produce one admission and one deterministic fixture effect. Its terminal reply
is durable. The old long turn STILL cuts off: overall continuity stays RED.
`l7-acceptance-evidence.json` records this distinction.

Verification: Linux oracle **71 passed, zero skipped** across
`test_ingress_journal.py`, `test_deploy_during_traffic.py`,
`test_conversation_run_admissions.py`, and `test_converse_turn_cost.py`.
Changed Python ruff checks pass; plugin mirror rebuilt and import probe passes.

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
