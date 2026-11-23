# Decisions Index

The platform's set first; the superseded ADRs after it. An ADR is never edited after
acceptance except its Status line (ADR-039 decision 1).

## The platform's set

- [ADR-032-clean-sheet-platform.md](ADR-032-clean-sheet-platform.md) — one SQLite-backed Durable Object per owner holds the trusted side · edge Workers are the only binder · a gateway Worker holds sealed credentials · owner files in object storage · a Firecracker sandbox per owner · supersedes ADR-001 to ADR-031 (amended by ADR-034, ADR-035 and ADR-039)
- [ADR-033-any-source-powers-any-agent.md](ADR-033-any-source-powers-any-agent.md) — any connected source powers any agent, scheduled and unattended included, where its vendor allows · a subscription CLI runs the whole turn as the agent's engine · TinyAssets' loop uses plan credentials only through a vendor-approved program
- [ADR-034-agents-run-on-unmodified-engines.md](ADR-034-agents-run-on-unmodified-engines.md) — every agent runs on an unmodified engine (a provider CLI in the sandbox on a subscription or an egress-keyed API key, the user's main agent over MCP, or pi's CLI) · an agent is files plus TinyAssets state · the unit of work is a run · pi leaves the owner object (amended by ADR-039)
- [ADR-035-large-bodies-live-in-r2.md](ADR-035-large-bodies-live-in-r2.md) — a body past 2,048 bytes (a collection row's, a history entry's, a message's) lives in R2 through the files plane, its row a content id · written R2 first, then the reference, an orphan swept · charged to its writer's files scope within the tier's storage · erased by namespace with the owner · amends ADR-032 decisions 1 and 5 (decision 5 superseded for mail by ADR-036, for collection history by ADR-039)
- [ADR-036-mail-is-never-let-go.md](ADR-036-mail-is-never-let-go.md) — at full storage new mail is refused `storage_full`, acked mail never let go · acked mail moves to archive segments instead of eviction, `storage_busy` at the hot ceiling · mail counted at its real bytes · one owner-wide unread ceiling · supersedes ADR-035 decision 5 for mail
- [ADR-037-memory-is-bounded-by-structure.md](ADR-037-memory-is-bounded-by-structure.md) — an isolate's live memory is counts the code enforces times costs it constructs · large data only as streams or 4 MiB chunks · past it `busy`, retryable, no acknowledged write lost · CI proves size-independence by differences at checkpoints
- [ADR-038-every-pr-is-reviewed-by-one-check.md](ADR-038-every-pr-is-reviewed-by-one-check.md) — every PR is reviewed, and the one gate is the required `review` check, which only the review App posts · the verdict is a `COMMENT` PR review on the commit read, first line `APPROVE` or `BLOCK` · supersedes ADR-015's review line
- [ADR-039-the-founders-answers-of-2026-11-21.md](ADR-039-the-founders-answers-of-2026-11-21.md) — ADR-001 to ADR-031 are history, and what stands of them is its decision 2 · one agent definition reaches every engine · code mode off until H2 · limits only Fact 15's kinds · runs past the tier's ceiling wait · the control record served from its last good copy

## Superseded by ADR-032 (founder, 2026-11-21)

What stands of each is held by ADR-039 decision 2, item named.

- [ADR-001-truth-hierarchy-and-freshness.md](ADR-001-truth-hierarchy-and-freshness.md) — `PLAN.md`, `STATUS.md` and freshness rules (superseded first by ADR-005)
- [ADR-002-static-vs-dynamic-context-budget.md](ADR-002-static-vs-dynamic-context-budget.md) — the always-loaded set and its budget (superseded first by ADR-005)
- [ADR-003-required-test-aggregator.md](ADR-003-required-test-aggregator.md) — `required-tests`, the first required check that tested behaviour · stands: 2.21
- [ADR-004-merge-attribution-and-the-deploy-gap.md](ADR-004-merge-attribution-and-the-deploy-gap.md) — the default token's merges raise no push · stands: 2.22
- [ADR-005-retire-plan-md.md](ADR-005-retire-plan-md.md) — `PLAN.md` retired, one home per kind of fact · stands: decision 1, 2.1
- [ADR-006-the-platform-has-no-llm.md](ADR-006-the-platform-has-no-llm.md) — the platform has no LLM · stands: 2.2
- [ADR-007-vendor-neutral-connections.md](ADR-007-vendor-neutral-connections.md) — vendor-neutral connections · stands: 2.3
- [ADR-008-cloud-only-platform.md](ADR-008-cloud-only-platform.md) — cloud only, the founder's desktop never infrastructure · stands: 2.4
- [ADR-009-nothing-runs-outside-a-users-command-center.md](ADR-009-nothing-runs-outside-a-users-command-center.md) — every execution has one owner · stands: 2.5
- [ADR-010-private-by-default.md](ADR-010-private-by-default.md) — private by default · stands: 2.6
- [ADR-011-owner-door-complete-model-door-bounded.md](ADR-011-owner-door-complete-model-door-bounded.md) — the owner door complete, the model door bounded · stands: 2.7
- [ADR-012-sealed-box-target-architecture.md](ADR-012-sealed-box-target-architecture.md) — a sealed box per command center · stands: 2.4, 2.13
- [ADR-013-primitives-not-operators.md](ADR-013-primitives-not-operators.md) — ship primitives, not operators · stands: 2.8
- [ADR-014-an-agent-is-a-node.md](ADR-014-an-agent-is-a-node.md) — an agent node runs the `converse` turn
- [ADR-015-only-the-floor-blocks-a-deploy.md](ADR-015-only-the-floor-blocks-a-deploy.md) — only the floor blocks a deploy · stands: 2.9
- [ADR-016-identity-follows-the-account.md](ADR-016-identity-follows-the-account.md) — identity follows the account · stands: 2.10
- [ADR-017-knowledge-is-files-okf-by-default.md](ADR-017-knowledge-is-files-okf-by-default.md) — knowledge is files, OKF by default · stands: 2.11
- [ADR-018-command-centers-are-exportable.md](ADR-018-command-centers-are-exportable.md) — the owner can always leave with everything · stands: 2.12
- [ADR-019-one-box-per-owner.md](ADR-019-one-box-per-owner.md) — one box per owner, an owner-only policy layer · stands: 2.13
- [ADR-020-lean-ci-successor.md](ADR-020-lean-ci-successor.md) — affected tests on PRs, the full suite after merge · stands: 2.21
- [ADR-022-box-backend-gvisor-on-the-droplet.md](ADR-022-box-backend-gvisor-on-the-droplet.md) — gVisor on the droplet (superseded 2026-10-27)
- [ADR-023-core-agent-loop-runs-outside-every-box.md](ADR-023-core-agent-loop-runs-outside-every-box.md) — the core loop outside every box · stands: 2.13
- [ADR-024-a-paired-device-runs-only-its-owners-work.md](ADR-024-a-paired-device-runs-only-its-owners-work.md) — a paired device runs only its owner's work · stands: 2.14
- [ADR-025-the-trusted-ledger-and-conversations-live-outside-the-box.md](ADR-025-the-trusted-ledger-and-conversations-live-outside-the-box.md) — the trusted ledger outside the box · stands: 2.15
- [ADR-026-boxes-sleep-when-idle-and-are-admitted-by-size.md](ADR-026-boxes-sleep-when-idle-and-are-admitted-by-size.md) — zero idle tail, admission by size · stands: 2.16
- [ADR-027-a-shared-real-time-relay-is-infrastructure.md](ADR-027-a-shared-real-time-relay-is-infrastructure.md) — a shared real-time relay is infrastructure · stands: 2.17
- [ADR-028-scoped-api-keys-on-mcp-for-agents-without-oauth.md](ADR-028-scoped-api-keys-on-mcp-for-agents-without-oauth.md) — scoped API keys on MCP · stands: 2.18
- [ADR-029-one-app-manifest.md](ADR-029-one-app-manifest.md) — one app manifest · stands: 2.19
- [ADR-030-the-box-holds-only-tools-user-code-and-owner-files.md](ADR-030-the-box-holds-only-tools-user-code-and-owner-files.md) — the box holds only tools, user code and owner files · stands: 2.13
- [ADR-031-subscription-use-follows-each-vendors-sanctioned-paths.md](ADR-031-subscription-use-follows-each-vendors-sanctioned-paths.md) — subscription use follows each vendor's sanctioned paths · stands: 2.20

## Decision logs

Working logs, not ADRs: each records a run's decisions in order, and a later entry or log
amends an earlier one where it says so.

- [untangle-2026-11-wave-1.md](untangle-2026-11-wave-1.md) — the untangle's first redesign of `platform/`: root fixes A1–A7, the bugs found, the spec questions decided
- [untangle-2026-11-wave-2.md](untangle-2026-11-wave-2.md) — the second wave: fixes B1–B26, the lanes and their order, the plan's review (§G)
- [untangle-2026-11-remaining-build.md](untangle-2026-11-remaining-build.md) — the whole remaining build in one order, amending wave 2, with the decisions since (§A–§Q)
