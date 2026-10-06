## Context

Request fields enforce `_MAX_URL_CHARS = 300`. The daemon router shares one QuotaTracker keyed by provider name. HTTP inference accounting rejects a POST to an installed model connection without a parent usage reference; both IPC clients collapse that refusal into a generic message.

## Decisions

Use an 8192-character URL shape bound, retaining HTTPS, userinfo, raw-address and first-party route checks. Use owner/provider tuple keys for cooldown state and reasons; both routed calls and deferred cooldown writers must carry their existing trusted owner identity. No database migration is needed for this process-local state.

Preserve inference authority and accounting checks. Trace the direct HTTP review path and distinguish a missing accounting reference from a missing grant. Return fixed, secret-free actionable diagnostics across IPC, never arbitrary server exception text. Prefer the existing accounted model invocation path over inventing an owner approval that cannot repair accounting.

## Verification and rollout

Linux oracle tests cover link boundaries, owner A/B cooldown isolation, deferred cooling and broker refusal transport. Run affected heavy files, static prompt-budget tests, ruff, plugin mirror and test hygiene. Request one Claude correctness/floor review. Draft PR delivery only; deployment and real-user proof remain separately recorded until performed.
