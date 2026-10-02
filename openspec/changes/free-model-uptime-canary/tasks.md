Implementation tasks below are deliberately unchecked. This session drafts and validates only; no code, tests, deployment or push is authorized by this drafting task.

## 1. Provision and constrain the principal

- [ ] 1.1 Add locked droplet-only generate-if-absent provisioning through the atomic `set-once` env helper, the reserved private account/home and designated enrollment-owner mapping; verify stable redeploy, malformed/duplicate refusal, no secret output/export/child inheritance, and no founder action.
- [ ] 1.2 Implement constant-time bearer recognition and exact pre-dispatch allowlist for fixed-message own-home `converse` plus empty-argument own-home `get_status`, with trusted persisted principal context and no automatic home recreation.
- [ ] 1.3 Add parameterized negative tests over every registered action other than the two allowed shapes (new actions default denied): all connect/OAuth/deposit/approval routes, read/write/run/page/graph tools, account/admin/delete/share actions, cross-home inputs, prompt/model overrides, malformed/extra/duplicate keys, batches, notifications/discovery, methods/routes, nested/model tools and identity fallback; assert zero dispatch, disclosure, mutation or provider work on refusal.

## 2. Connect by owner clicks

- [ ] 2.1 Implement the inert owner-only enrollment landing and same-origin mint/start endpoint, 600-second digest-stored single-use nonce, existing connect-screen PKCE ceremony and purpose-specific server bind context; retain ordinary current-home guards and free-only eligibility, target generation/lifecycle fences and a durable partial-completion receipt.
- [ ] 2.2 Add enrollment tests for success to the canary home only, non-owner/unauthenticated refusal, reuse/expiry, concurrent callback winner, target/preset substitution, CSRF, verifier/session mismatch, scanner GET, deletion/revocation races and uncertain exchange/deposit recovery; prove no founder-home mutation, broad ACL grant, key disclosure or runtime-principal connect authority.

## 3. Execute and account for one request

- [ ] 3.1 Enforce the principal-scoped provider-send budget of one, fixed cheap prompt/bounded output, no tools/retries/fallback/repair/compaction inference, and no learning extraction or deferred learning debt; test actual dispatch count, async continuation and unchanged ordinary-user learning behavior.
- [ ] 3.2 Project a correlated own-turn status receipt with exact structured failure code and failure `requests`, success round count and actual answering model; test pre-send zero, sent failure, timeout unknowns, mismatched count and no cross-home/fleet/key leakage.
- [ ] 3.3 Add the public-edge host runner, hourly timer/service and installer manifest integration with durable host/server hourly admission, bounded latency, journal and atomic state file; test duplicate/crash slots, no catch-up, TLS/redirect refusal, dark behavior before key binding (zero model calls/no outage alarm), active missing/revoked key or missing home, and corrupt/unwritable state. Use the required Linux oracle for host filesystem/process behavior during implementation; a skip is not a pass.

## 4. Alarm, document and prove delivery

- [ ] 4.1 Add persistent two-consecutive-failure incidents to the shared watchdog alarm sink/journal and optional existing-host-token GitHub issue create/update/close; test one-failure recovery, threshold, duplicate suppression, pending notification recovery, uncertain issue creation, absent/insufficient GH_TOKEN and no daemon restart or added model request.
- [ ] 4.2 Add the `docs/host-actions.md` row with the nonsecret owner-only URL and exactly: 1. Sign up at OpenRouter. 2. Open this link while signed in to TinyAssets. 3. Click Authorize. Document dark/active/disabled state, issue-delivery limitations and automated revocation/rollback without founder secret work.
- [ ] 4.3 During implementation, run affected focused/heavy tests and ruff, obtain the required floor/auth review under the repository workflow, and verify install/redeploy/rollback. No review agent or tests run in this drafting session.
- [ ] 4.4 After deployment, assert the implementation SHA, perform a real owner click-only app enrollment and one public served canary turn occupying its hourly slot, inspect actual count/model/latency and alarm evidence, then sync the spec delta and record delivery proof before archiving.
