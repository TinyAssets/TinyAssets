# Decisions Index

Accepted and proposed first; superseded last. An ADR is never edited after
acceptance except its Status line (ADR-005).

- [ADR-005-retire-plan-md.md](ADR-005-retire-plan-md.md) — PLAN.md retired · one home per kind of fact · approval gate is README § Direction plus ADR acceptance
- [ADR-006-the-platform-has-no-llm.md](ADR-006-the-platform-has-no-llm.md) — only a powered command center calls an LLM, on its owner's credentials
- [ADR-007-vendor-neutral-connections.md](ADR-007-vendor-neutral-connections.md) — any model or platform through generic connection primitives · OAuth first · no vendor code
- [ADR-008-cloud-only-platform.md](ADR-008-cloud-only-platform.md) — the platform runs only in the cloud · the founder's desktop is never infrastructure (amended by ADR-024)
- [ADR-009-nothing-runs-outside-a-users-command-center.md](ADR-009-nothing-runs-outside-a-users-command-center.md) — every execution belongs to one owner, who can see, pause and delete it · no platform fleet (amended by ADR-027)
- [ADR-010-private-by-default.md](ADR-010-private-by-default.md) — every creation path writes `private` · exposure is an explicit owner act · privacy guidance is commons content
- [ADR-011-owner-door-complete-model-door-bounded.md](ADR-011-owner-door-complete-model-door-bounded.md) — the app's reads are complete · only the model door bounds · `AccountType` is the one per-account input
- [ADR-012-sealed-box-target-architecture.md](ADR-012-sealed-box-target-architecture.md) — one sealed box per command center (amended by ADR-019, ADR-031) · control plane is the only always-on layer · capacity later
- [ADR-019-one-box-per-owner.md](ADR-019-one-box-per-owner.md) — one box per owner runs tools, browser and CLIs (amended by ADR-023, ADR-025, ADR-026, ADR-030, ADR-032) · gVisor driver · owner-only policy enforced at the box boundary · email via the owner's connector
- [ADR-013-primitives-not-operators.md](ADR-013-primitives-not-operators.md) — ship the missing primitive, not an operator · irreducibility finding for new handles · no graph-size caps · test via the app agent · generator, evaluator and ground truth separate
- [ADR-014-an-agent-is-a-node.md](ADR-014-an-agent-is-a-node.md) — an agent node runs the `converse` turn under the owner's grant
- [ADR-015-only-the-floor-blocks-a-deploy.md](ADR-015-only-the-floor-blocks-a-deploy.md) — the six floor classes · everything else is tracked and re-judged after live use · outages equal severity
- [ADR-016-identity-follows-the-account.md](ADR-016-identity-follows-the-account.md) — WorkOS AuthKit · one principal across every client · no anonymous principal · Claude/ChatGPT/browser parity
- [ADR-017-knowledge-is-files-okf-by-default.md](ADR-017-knowledge-is-files-okf-by-default.md) — OKF bundle is the default canonical knowledge form · indexes are derived
- [ADR-018-command-centers-are-exportable.md](ADR-018-command-centers-are-exportable.md) — one action exports a runnable, publish-ready folder · not yet built
- [ADR-020-lean-ci-successor.md](ADR-020-lean-ci-successor.md) — affected tests and structural guards on PRs; full suite after merge
- [ADR-023-core-agent-loop-runs-outside-every-box.md](ADR-023-core-agent-loop-runs-outside-every-box.md) — the core loop leaves the box (where C6 put it) for a loop-worker pool, never the daemon · tools reached through a box tool door · user customizations run in the box at extension points (amended by ADR-032) (amended by ADR-034)
- [ADR-024-a-paired-device-runs-only-its-owners-work.md](ADR-024-a-paired-device-runs-only-its-owners-work.md) — a paired device is a placement for its owner's own work, never infrastructure · outbound polls park at the broker · amends ADR-008
- [ADR-025-the-trusted-ledger-and-conversations-live-outside-the-box.md](ADR-025-the-trusted-ledger-and-conversations-live-outside-the-box.md) — the broker keeps the messaging ledger · bodies, board and turn-based rooms live in a trusted conversation store · messaging never wakes a box (store technology superseded by ADR-032)
- [ADR-026-boxes-sleep-when-idle-and-are-admitted-by-size.md](ADR-026-boxes-sleep-when-idle-and-are-admitted-by-size.md) — zero idle tail · admission by memory size class, not a flat count · suspend and restore in the main build-out
- [ADR-027-a-shared-real-time-relay-is-infrastructure.md](ADR-027-a-shared-real-time-relay-is-infrastructure.md) — one thin relay object per match forwards opaque frames · devices do the work · amends ADR-009
- [ADR-028-scoped-api-keys-on-mcp-for-agents-without-oauth.md](ADR-028-scoped-api-keys-on-mcp-for-agents-without-oauth.md) — owner-minted per-agent API keys may call `/mcp` and `/api` as that agent · needs floor review
- [ADR-029-one-app-manifest.md](ADR-029-one-app-manifest.md) — `apps/{slug}/app.json` carries the one manifest with a `multiplayer` block · N11 reads it
- [ADR-030-the-box-holds-only-tools-user-code-and-owner-files.md](ADR-030-the-box-holds-only-tools-user-code-and-owner-files.md) — slim box harness · owner MCP, app and `/api` requests served on the trusted side through `BoxProvider` (amended by ADR-032)
- [ADR-031-subscription-use-follows-each-vendors-sanctioned-paths.md](ADR-031-subscription-use-follows-each-vendors-sanctioned-paths.md) — API keys for anything unattended · TinyAssets never holds consumer login tokens · hosted CLIs are human-started · wakes use each vendor's own door (amended by ADR-033)
- [ADR-032-clean-sheet-platform.md](ADR-032-clean-sheet-platform.md) — one SQLite-backed Durable Object per owner holds the trusted side and the pi-durable loop (pi's own mechanisms; user hooks in a separate Worker's object over RPC) · edge Workers are the only binder · a gateway Worker holds sealed credentials · owner files in object storage · a Firecracker sandbox only for `bash` · supersedes ADR-022, the droplet, box storage, broker-held SQLite and the router/broker serving design (amended by ADR-034)
- [ADR-033-any-source-powers-any-agent.md](ADR-033-any-source-powers-any-agent.md) — any connected source powers any agent, scheduled and unattended included, where its vendor allows · a subscription CLI runs the whole turn as the agent's engine · TinyAssets' loop uses plan credentials only through a vendor-approved program
- [ADR-034-agents-run-on-unmodified-engines.md](ADR-034-agents-run-on-unmodified-engines.md) — every agent runs on an unmodified engine (a provider CLI in the sandbox on a subscription or an egress-keyed API key, the user's main agent over MCP, or pi's CLI) · an agent is files plus TinyAssets state · the unit of work is a run · pi leaves the owner object
- [ADR-004-merge-attribution-and-the-deploy-gap.md](ADR-004-merge-attribution-and-the-deploy-gap.md) — merged-is-not-deployed and PRs stuck BEHIND are ONE root cause: the default `GITHUB_TOKEN` cannot originate the events this repo's automation needs · measured #2259 vs #2260 · host mints a PAT/App token
- [ADR-003-required-test-aggregator.md](ADR-003-required-test-aggregator.md) — `required-tests`: the first required check that tests behaviour · runs unconditionally · no-new-failures ratchet · host `gh api` command to make it required
- [ADR-022-box-backend-gvisor-on-the-droplet.md](ADR-022-box-backend-gvisor-on-the-droplet.md) — gVisor on the droplet as the one box backend · superseded by ADR-032
- [ADR-001-truth-hierarchy-and-freshness.md](ADR-001-truth-hierarchy-and-freshness.md) — superseded by ADR-005
- [ADR-002-static-vs-dynamic-context-budget.md](ADR-002-static-vs-dynamic-context-budget.md) — superseded by ADR-005
