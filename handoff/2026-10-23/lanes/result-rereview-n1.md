I re-reviewed the fix round of PR #5228, reading only `git diff d560fceccb3..HEAD`. All seven earlier findings are resolved. The new hunks introduce two problems, one of which breaks a requirement, so the verdict is DISAGREE_EVIDENCE.

**Earlier findings**

| # | Finding | Status | Where |
|---|---|---|---|
| H1 | Routing open; body-selector option | **Resolved.** Option (a) is chosen: every channel gets its own owner-controlled callback per binding. Shared callbacks for several owners and body selectors are ruled out, and transports that can't do this are unsupported in N1. With no selector, the uniqueness question goes away. | `design.md:30-38`, `spec.md:4` |
| M1 | 404 vs maintenance 503 | **Resolved.** Maintenance and cutover return retryable 503 `maintenance`, which overrides the 404. Outside that window the answer is a stable 404, and there is a separate scenario for it. (This also changes current behaviour: today's router answers every unadmitted owner with 503, at `owner_router.py:121-123`. The router will need `admits` to tell maintenance apart from a permanent refusal.) | `design.md:42`, `spec.md:19-25` |
| M2 | T1 broker exception unstated; dispatcher unnamed | **Resolved.** It is now an explicit, narrow T1 exception that covers only profiles needing a secret. The dispatcher is named, `INBOUND_VERIFY` in `broker/server.py::_Connection._connection_op` (that function is at `server.py:373`). The baseline test `test_owner_router.py:413` stays unchanged. | `design.md:19,24`, `spec.md:27-31` |
| M3 | Generic hooks with no event ID | **Resolved.** Generic senders must send `Idempotency-Key` and `X-Event-Timestamp`; without them they get 400 `event_identity_required`. There is no fallback, and the cutover breaking existing senders is stated. | `design.md:50,62`, `spec.md:59-61` |
| M4 | Missing test files; no one-route scenario | **Resolved.** The paths are fixed, and the three files I checked exist. The two missing C2 tests are recorded and their assertions move into the new guard. An exact-one-route scenario is added. | `design.md:76`, `tasks.md:14`, `spec.md:15-18` |
| L1 | Shared key fetcher; profile too broad | **Resolved.** The box pins the keys and shared code never fetches them. Profiles are narrowed to token, HMAC-SHA256 and RS256 JWT. | `design.md:28` |
| L2 | Unbounded receipts; credential scope | **Resolved.** Receipts are kept only for the freshness window plus clock skew, counted from the event's own timestamp. Credential references only resolve in the binding owner's broker, and foreign ones are refused. | `design.md:28,50`, `spec.md:63-66,76-79` |

**New problems in this round**

- **MEDIUM (correctness, T7 / Direction 8): Teams can lock itself out after a key rotation.**
  - `design.md:28` says expired or unknown keys are refused until a revised profile is installed, and only the box fetches keys.
  - A sleeping box only wakes on a verified request. Once Microsoft rotates its Bot Framework signing key, every Teams message fails verification, so the box never wakes to fetch the new key.
  - With no host online, the channel stays dead until something else wakes the box.
  - Fix:
    - Require the connector to refresh and republish keys on a C2 timed wake, within the provider's rotation lead time.
    - Return a retryable status for an unknown key, not a permanent one.
    - Add a rotation-while-asleep case to `test_inbound_door.py`.

- **LOW (placement): the router-side verifier has no home.**
  - `design.md:24` says that URL-capability and pinned-key checks run against the router's installed snapshot without the broker.
  - But the only verifier module listed is `tinyassets/broker/inbound_verification.py` (`design.md:19`), and the middleware row forbids "a second verifier".
  - Fix: state that the router calls the same pure verifier code for the profiles that need no secret, and that only the secret lookup is broker-side.

Two non-blocking nits:
- The HMAC profile doesn't list how the signature header is encoded or prefixed (Slack's `v0=`, WhatsApp's `sha256=`).
- Option (a) means every owner sets up their own Slack, WhatsApp or Teams app. That is a deliberate trade-off against naive-user onboarding (Direction 7), not a floor problem.

A `.claude/hooks/supervisor_record.py` post-command hook failed once because one of my commands changed into the change directory. It doesn't affect this review.

VERDICT: DISAGREE_EVIDENCE
