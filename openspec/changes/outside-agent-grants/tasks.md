Docs-only round 2; all boxes describe future implementation and acceptance. No code, deployment or live-proof completion is claimed. Tasks are split by independently verifiable surface; each section stays at or below the owner's requested 12 tasks.

## 1. Identity and authority foundation

- [ ] 1.1 Reconcile adjacent changes and prove AuthKit verified client claims/lookup, metadata and reconnect candidates (`auth_time` preserved on refresh; `sid` across fresh auth); specify and test protected re-consent/credential-family fallback so recovery never clears old fences.
- [ ] 1.2 Bind verified client classification on every bearer request; configure the exact first-party issuer/client-id allowlist for web/phone/desktop; test spoofing, missing identity, CIMD/DCR, same-owner independent clients and stolen-bearer attribution semantics.
- [ ] 1.3 Implement OutsideClientAuthority at the named platform-root SQLite path with a single transactional authority service shared by all workers, owner defaults, per-universe grants, owner/client fences and secret-free audit; test concurrency, store outage, no replica fallback and cross-universe visibility; register platform storage/backup ownership.
- [ ] 1.4 Implement owner-editable starter agents/levels/message mode, atomic grant revisions/expiry, first-converse bootstrap and no-home status; test concurrent snapshots and preservation of existing/revoked grants.

## 2. Admission and owner controls

- [ ] 2.1 Enforce read grants on get_status/read_graph/read_page, including aliases, mixed collections and shared pages; verify filtering before serialization and uniform cross-user refusals.
- [ ] 2.2 Enforce message/control/costly on converse/write_graph/run_graph/write_page and batch/alias operations; verify all affected agents/resources and missing-grant errors without effects.
- [ ] 2.3 Apply and test every route/method disposition in bearer-surfaces.md, including all app HTTP routes, pulse and private bearer services; unknown bearer routes fail closed; first-party authorized calls still work.
- [ ] 2.4 Propagate outside origin through existing turn/run/nested/scheduled carriers; test both owner-selected message modes, direct-call limits, mode narrowing on resume, and continuing trusted owner/universe automations and queued runs.
- [ ] 2.5 Complete the protected owner-session integration with inline-connect-and-approve and Connected apps inventory, widening/narrowing, starter/message settings, revoke/recovery and last-used; extend scoped access readback and audit; prove bearer self-elevation cannot work. Working owner controls are a hard prerequisite to enforcement.
- [ ] 2.6 Implement transactional local revoke and effect-admission fencing across universes/workers, open sessions, refresh and queued/pending work; test race boundaries and honest receipts for already committed effects, independently of provider revocation success.
- [ ] 2.7 Implement fresh-generation reconnect and provider revocation attempts; test protected owner re-consent when AuthKit generation evidence is insufficient, keeping pre-revoke credentials invalid without permanent owner lockout.
- [ ] 2.8 Integrate client provenance and current message-mode/grant revalidation with protected sensitive-action sheets and standing decisions; prove bearer approval/retry refusal and that effect approval alone cannot widen grants.
- [ ] 2.9 Implement the NEW durable outside-only kill switch and deny-only process override; test next-admission refusal on every worker, outage fail-closed behavior, and exemptions for verified first-party and trusted owner/universe work before rollback use.

## 3. Verification and live acceptance

- [ ] 3.1 Rehearse cutover only after working owner controls: grandfather existing primary connectors or obtain visible owner choices, reconcile unknown legacy identity with notice before cutover, and hold only unreconciled outside-origin work. Rehearse kill-switch activation and retained/backported admission on rollback; first-party read/write/run and owner controls continue.
- [ ] 3.2 Run focused and affected heavy tests plus ruff, cross-user mutation cases and one cross-family floor/correctness implementation review with AGREE/DISAGREE_EVIDENCE dispositions; strictly validate OpenSpec, assert deployed implementation SHA and run `python scripts/mcp_public_canary.py --assert-handles` with no added handles.
- [ ] 3.3 Mandatory rendered live pass with two clients on one owner account (claude.ai + ChatGPT, or OpenClaw): independently edit grants, prove a missing-grant refusal, revoke one and prove refusal on its next call while the other works, then prove first-party app read/write/run and protected grant widening after cutover. Record sanitized receipts; no Muse blocker can waive this task.
- [ ] 3.4 Additional rendered Muse proof at `https://tinyassets.io/mcp`: verify CIMD/DCR callback acceptance for `https://agent.meta.ai/api/hatch/oauth/callback`, starter grant, owner widening/message mode, sensitive sheet, audit and revoke; record sanitized receipts or the exact callback/configuration blocker, without representing a blocker as a live pass.
- [ ] 3.5 After implementation and mandatory live proof, sync canonical specs and archive with validation/review/deployment/live receipts, explicitly recording any additional Muse blocker; keep live/outbox, A2A and outbound attach in their later changes.
