## Context

The supplied `b2bcfca0ef` baseline is not remeasured here. Inspection at `26980f01db` confirms these sections and name/description-only skill discovery. Existing ratchets count tool-description and `_HARNESS_HEAD` characters, not full resident tokens.

## Goals / Non-Goals

Keep starter capability, four tools and one existing extension mechanism; add no special starter runtime. Owners can change instructions, persona/soul, skills, rules, extensions and the main agent within existing authority. D6 schemas, D7 memory UI and D10 channels remain separate.

## Decisions

Paths are inside the command center. Skill bodies load on demand; only name, one trigger sentence and path remain resident.

| Hard-coded section | Destination / decision |
|---|---|
| “How I remember” (~685) and open-question recording directive | `skills/starter-memory/SKILL.md`: recall existing facts; immediately persist clear owner-taught facts; read before minimal edits; reject jokes, hypotheses, credentials and unsupported claims; use governed writes for protected files. Seed `AGENTS.md` has one trigger sentence. |
| “How I ask” (~736), including GitHub recipes | `skills/starter-access/SKILL.md`: discover before claiming absence, check existing connections, connect versus extend, raise a real pending request, request the narrowest job-sized grant. GitHub recipes become a linked skill reference loaded only for that service. |
| Unrecorded-lesson nag (~1356) and `extract_learning` (~834) | Delete platform nag and extra model call. Memory skill records in-turn, verifies success and reports failures truthfully; no hidden background replacement. Preserve unresolved source turns at migration. |
| Cross-surface continuity (~1372) | One seed `AGENTS.md` sentence: continue unfinished work from the shared session; history is evidence, not new consent. Keep session transport. |
| Input-method prose (~1387) | Delete repeated prose. Preserve `input_method` as client-reported turn data, never authority; seed `AGENTS.md` says not to infer speech/typing when unknown. |
| Onboarding `_NOTE` (`onboarding_note.py:12`) | `skills/starter-onboarding/SKILL.md`: read identity/responsibility, ask only missing fields, save name/responsibility without replacing existing text, propose owner-set rules and cadence through existing paths. Seed `AGENTS.md` triggers it when setup is incomplete. |
| Founder clock (~441) | `skills/starter-time/SKILL.md`: for time-relative work read the stored account timezone through D6 and current time; ask only when unavailable. Delete per-round clock advice; retain factual message timestamps. Never cache “today” in a seed file. |
| `_HARNESS_HEAD` (~1189) | Seed `AGENTS.md`: workspace location, matching-skill loading/editing, batching independent reads. Tool syntax stays in four tool definitions. Move UI-install and long-running-work recipes to `skills/starter-workspace/SKILL.md` using D6 discovery. |
| Command-center summary / folder inventory (~1260/~1299) | Delete eager summaries. Workspace skill reads files/lists directories and discovers live branches, connections and status through `ta` when needed; do not replace these with stale seed snapshots. |
| Existing default operating text, persona/soul and voice | Seed files own tone, initiative and role; preserve existing persona/soul/voice content. No Python fallback that silently reintroduces default behavior when files are empty or removed. |

Plumbing retains the identity/first-person/honesty line (~773), `_UNTRUSTED_ENVELOPE_RULE` (~88), code-level disclosure checks including `permitted_grounding_files` (~619), and `read/write/edit/bash` definitions. Identity names the selected agent; “main” is a seed choice. Moving text never grants file writes, reveals private material to another tier, or changes owner-only rule mutation.

Seed `AGENTS.md` routes to these skills; the generic index supports customized instructions. Resident manuals would retain the cost; deleting guidance would repeat observed memory/request failures.

## Migration Plan

1. Publish immutable bundle `starter-agent-v1` with manifest version, per-path SHA-256 and predecessor seed hashes. Use the same bundle for new-center provisioning and D10. New centers receive editable files once; startup does not restore removed content.
2. For existing centers, keep an owner-scoped migration receipt outside the agent-writable tree: center ID, version, state (`prepared/active/declined`), original/installed hashes, per-path outcome, owner decision and pending-lesson source references. It is bookkeeping, not prompt policy. Lock per center and use atomic compare-and-create/compare-and-swap with no symlink traversal; unreadable, empty, linked or unknown files are existing content, never “missing.”
3. Prepare an inert bundle plus one visible, version-keyed owner proposal listing additions, collisions and the optional `AGENTS.md` diff. A missing starter skill may be added only by exclusive create. Never replace an existing skill or persona/soul/rules/extension. Never automatically rewrite an existing `AGENTS.md`, even a stock one: only an explicitly accepted diff bound to its current hash may change it. Existing missing `AGENTS.md` also needs acceptance, since deletion may be intentional.
4. Activate only after all five skill routes are reachable and the owner accepts additions, maps collisions to their own skills, or explicitly chooses their own replacement/no starter. A customized `AGENTS.md` needs no amendment when its skill index suffices. An owner-declined starter records that choice and uses their files, without a hidden fallback. Stage additions outside the indexed skills tree until activation, so preparation cannot alter behavior. New files, routing and active receipt commit together at a turn boundary; a crash leaves the old selection active and resumes the same transaction, without duplicate proposals.
5. Before retiring extraction, preserve every unresolved legacy cursor span as an owner-only, verbatim review file with source IDs; do not mark it learned. The memory skill reviews it on first activation, deduplicates against current notes and marks individual sources handled only after a verified write or explicit no-fact decision. A replacement agent's owner receives the same backlog. Retain conversation history and original memory unchanged.
6. Retry never overwrites a changed file or reinstalls a file the owner deleted after activation; the version receipt records deletion/decline. Future versions repeat this offer, never reseed on every turn. Rollback changes the active version and only reverses seed writes whose current hash still matches the installed hash; changed files remain and are reported. Old model extraction is not restarted or replayed during rollback.

Ordering: D6 must first deploy working discovery/invocation for memory, pending requests, connection inspection, rules proposals, automations and account timezone. Verify each recipe and its permission semantics before activating this bundle; do not invent `ta` subcommands here. D6's intermediate release must also prove the old starter can reach these operations through discovery. If it cannot, retain its existing handles until a coordinated per-center four-tool/seed activation; do not remove handles first and leave old prompt recipes stranded. Preparation can land earlier. Keep existing centers on the old release until migrated or explicitly opted out; do not delete the old renderer globally while any existing center still depends on it (including dormant centers). Then remove policy/extraction code and adopt the bundle in D10. This is a rollout boundary, not a permanent legacy mode.

## Risks / Trade-offs and Proof

On-demand reads can add calls: measure full-task cost. Customized instructions can suppress skills: resolve through owner review. Pending migrations can delay removal: report counts, never silently cut over.

Extend `tests/test_converse_turn_cost.py` with pinned-tokenizer measurements of every serialized model request. Record three comparable snapshots: supplied baseline (reproduce on its SHA), D6-only, and D6 plus this slice. Separate core + four schemas, starter instructions/index, owner grounding, history, loaded skill/tool results, and total tokens/calls; neither cache discounts nor D6's schema reduction count as this slice's saving. Acceptance: core + four schemas <1,000 tokens; default starter instructions + index <=1,000 tokens; non-tool resident default overhead at least 50% below D6-only with identical fixtures. These are targets, not measured results; owner-authored content is reported, never trimmed to pass. Lower ratchets to the actual passing counts; retain the tool-description ratchet under D6 ownership.

Live proof on customized tiny, without naming skills: teach a benign fact and recall it across surfaces; request a job needing access and observe the right request without a repeated key ask. On a disposable starter agent in the founder's center, partially complete onboarding, resume elsewhere, and verify missing-field-only questions, persisted answers and governed rules/cadence proposals. Tiny must not restart onboarding. Record skill traces, file diffs, request receipts, tokens/calls and deployed SHA; also prove replacement-main operation and unauthorized-tier refusal. These proofs remain future work.

## Open Questions

No architectural decision is deferred. Founder preference only: keep “Welcome, commander” as editable starter copy, or change it? Default: keep it. Implementation must discover whether D6 exposes account-timezone reads; if absent, that prerequisite belongs to D6 before cutover.
