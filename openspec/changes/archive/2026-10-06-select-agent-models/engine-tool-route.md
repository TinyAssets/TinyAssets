# Shared owner-bound engine tool route

September10,2026. Pre-build design for task2.4's shared HTTP tool transport
prerequisite. No public tool or owner workflow changes; this is the existing
private loopback route publisher/consumer boundary.

## Scope and contract

The supervisor pins each server to owner and graph but publishes only graph,
URL and bearer. Claude and Codex duplicate that lookup and can fall back to CWD.
Before adding a third HTTP consumer, introduce one internal reader reused by
both CLI adapters and the future HTTP tool client. It returns an immutable
owner/graph route with secret omitted from repr, or no usable route. A route is
transport configuration, never fresh tool permission or an inference grant.

Extend each existing graph-keyed entry with version1, actor_id from that
server's pinned owner and integer port. Reader requires exact version1, matching
nonempty actor, matching graph key, valid port and a urlsafe bearer of at least
32 characters. It constructs http://127.0.0.1:<port>/mcp itself. The publisher
retains the derived url field for old readers/rollback, but new readers ignore
that field entirely. Reject legacy missing-owner entries, foreign owners,
malformed JSON/duplicate keys and wrong types. Never log the bearer or its record.
No global cache; each new consumer invocation re-reads the atomically published
map. Under the controlled route file only, protocol identifiers remain opaque.

Use storage.data_dir() for the default root. An explicitly supplied root must
already be absolute; never select CWD or mutate process-global environment to
resolve a request. Both CLI adapters use the same canonical resolver, without
an alternative subprocess-env root branch. The supervisor's
child data-root must match the root from which it enumerates serving bindings
and publishes routes (not an inconsistent ambient env value when base is given).

When serving rows contain multiple distinct owners for one universe, publish no
route for that universe rather than let dictionary order pick an owner. Repeated
rows for the same owner remain valid. Existing allowlist and engine flag remain
in force. The supervisor already retires/replaces changed owners with a new
secret; test the reader rejecting an old owner against the replacement record.

Existing CLI no-route behavior is retained: Codex receives no engine server and
Claude can use its existing owner-pinned stdio path. Do not mislabel those legacy
fallbacks as proven full-agent readiness. New HTTP full-agent execution must hold
without a usable route; its client, per-tool fresh authority, allowance/journal
and inference loop are still unbuilt. This slice does not claim immediate
revocation of a route already handed to a running CLI or change its live tools.

## Compatibility and verification

Old readers ignore added fields. New readers reject old records until the new
supervisor publishes them at startup; no user credential re-entry or permission
widening. No new persistent authority store or public response shape.

Test actual publication/readback, canonical root from an unrelated CWD, explicit
root/child-root consistency, both adapter wire configurations, missing/legacy/
cross-owner/duplicate/malformed records, URL and header injection, opaque Unicode
identifiers, ambiguous serving owners and replacement-owner rejection. Run
existing engine/provider suites on Windows and actual Linux plus plugin parity.
Independent shape and exact implementation reviews gate activation/landing.

## Shape review disposition: ADAPT276s

Claude verified the publisher pin/caller principal equivalence, actual CWD
fallbacks, ambiguous-owner overwrite, secret rotation and missing publisher test
coverage. Accepted: drop Codex's test-only subprocess-root precedence (the real
served child env does not contain that variable); use integer port plus a locally
constructed URL; require actor_id and graph_id as keyword-only caller inputs;
update existing fixtures rather than weakening the guard. Keep duplicate-key
rejection as a cheap ambiguity/corruption check, not protection against a trusted
writer who can create any valid record.

For stale records after the supervisor is disabled, the reader rechecks the
existing engine flag and graph allowlist rather than deleting a shared file it
does not own. No route is returned while disabled/outside the allowlist. This
is not a live-server probe or immediate revocation of an already running CLI.
Publisher/reader root consistency is test-only in today's production caller,
which passes no base; do not represent it as a reproduced production outage.

Before runtime changes, four new real-adapter tests fail for foreign-owner routes
and CWD lookup. Existing baseline:111 Windows passes/3 symlink-only skips and114
actual Linux passes/zero skips across engine MCP, hardening, provider sandbox and
Codex compatibility. No live route or credential inspected.

## Implementation and isolated release checkpoint, September10 02:40UTC

Feature runtime644d6d74 received independent Claude APPROVE250s. The reviewer
reproduced56 route tests and confirmed the verified caller chain, safe loopback
construction, stale child bearer removal, flag/allowlist guard, ambiguous-owner
refusal, canonical-root consistency and byte-identical mirrors. Nonblocking
notes: configuration errors can fail loudly; unprintable owners fail closed;
pre-existing Claude config-file bearer permissions remain outside this repair.
Full review: output/engine-tool-route-implementation-result.md in this worktree.

Feature test-only2b3a6a9f strengthens duplicate JSON cases to be otherwise valid
and drives actual child-start environment construction through fake Popen. Normal
56 route tests pass. Process-local mutation replacing _unique_route_keys with dict
produces exactly two expected failures/five passes, proving duplicate guards are
exercised; no runtime mutation persisted and no live child/key was accessed.

Isolated release draft PR3728, ddb343b1eef59caec58161b4b973c31454dd239a on base
9648a0022f4405063ce4e655792d17028a30f2f6, excludes all unfinished model selection.
Final local Windows Python3.14:182pass3symlinkskip20.49s. Actual Docker Linux
Python3.11.16/Git2.47.3/bubblewrap0.12.0:185pass0skip11.85s. Both commands run
test_engine_mcp_routes, test_engine_mcp_server, test_engine_mcp_hardening,
test_provider_sandbox, test_codex_cli_compat and test_mirror_parity_gate, using
pytest -q --tb=short -rs. Ruff and diff checks pass;396 shipping mirrors/import.
Final isolated exact-head review48549 terminated APPROVE270s, independently
reproducing56 route tests, mirror parity and Ruff:
https://github.com/Jonnyton/TinyAssets/pull/3728#issuecomment-5611864132
PR marked ready and normal squash auto-merge enabled02:44UTC on unchanged head.
Ready-event CI superseded the earlier run; Tests34430615987 required job102725219716
passed03:00:55UTC with zero new failures (known9 failures/2errors unchanged).
Normal merge3b541c116e7ca3a6bc029f9e453a13246c6ba216 at03:01:01UTC; build34431687041
and deploy34432022405 passed. CI authenticated `mcp_public_canary.py --url
https://tinyassets.io/mcp --assert-handles` passed03:07:14UTC and
`deployed_sha.py --assert-contains 3b541c116e7ca3a6bc029f9e453a13246c6ba216`
returned SHIPPED03:07:17UTC. Not a claim the full suite has no baseline failures.

Owned extension tab1346517854 refreshed after deployment. Exact retest message
sent20:09PDT, rendered20:12PDT response names3b541c116e7c and five fresh passes,
including workspace contention745378b5bcad4f95 and recovery1bf9ae6e30f445c3.
Webhook404 remains owner-held receiver scope; earlier16:38PDT200/204 completion
remains valid. No operator workflow/grant change. No organic post-fix use visible.

After normal gated deployment, require authenticated deployed SHA/public canary
and send only "Retest your workflow checklist" in the refreshed owned app tab.
Do not approve the owner's pending wildcard receiver grant. Roll back by reviewed
revert/redeploy if existing tool access or owner confinement regresses, reverify
live SHA/canary/rendered use; no user data migration or workflow edits to reverse.
HTTP client, tool-call loop, per-inference accounting and durable continuation
remain task2.4 work. A private route alone never establishes full-agent readiness.
