## Context

`2026-07-22-openspec-full-coverage-audit.md (archived in git)` § *Full-platform target
ownership* names three target groups with no complete active owner, each blocked
on a PLAN position requiring host approval:

1. the collaborative catalog/control plane, after resolving canonical-store,
   private-data, and public-tool-surface conflicts;
2. realtime collaborative editing, node CRUD/discovery/remix/convergence,
   presence, export, and the host/private boundary;
3. data portability, account deletion, succession, and feedback.

The same audit's *Design-truth conflicts that block blind target-spec
transcription* table lists the four blocking positions. All four landed on
`origin/main` 2026-07-25 via the brain-OKF PLAN foldback (#1761). This change is
the successor those rows were waiting for.

Source material is the 2026-04-18 full-platform architecture: §2.2 (realtime
strategy), §15 (node discovery + remix), §16 (collaboration model split), §21
(data portability + deletion), §22 (succession + bus factor), §23 (feedback
channels). PLAN.md's *Reference: Full-Platform Architecture* holds that note as a
target reference **with three host-approved carve-outs** (canonical store scoped
per domain; §17 private-data architecture demoted to research input; the many
standalone RPC/MCP tool names demoted to behavior targets). This change honors
all three carve-outs rather than transcribing the note.

## Goals / Non-Goals

**Goals:**

- Give the three PLAN-gated target groups one complete active OpenSpec owner.
- Apply the three *decided* positions (1A, 1C, 1D) as concrete requirements.
- Apply the *deliberately open* position (1B) by writing portability, deletion,
  and succession custody-agnostically, so the research can close on any of the
  four modes without invalidating these requirements.
- Record an explicit irreducibility call for every standalone RPC the source
  sections named, so the tool-surface carve-out is discharged with evidence
  rather than assertion.
- Name what these positions genuinely do **not** settle, as open questions
  rather than invented positions.

**Non-Goals:**

- Declare any of this behavior shipped. Nothing here is built.
- Close the 1B custody research. This change consumes the openness; it does not
  resolve it, and it does not rank the four modes.
- Rewrite adjacent shipped contracts. Every delta is ADDED into a new capability,
  with one deliberate exception: a target-only MODIFIED delta extends the
  `wiki-commons` typed-filing contract rather than forking a second feedback
  filing mechanism (D9). It reproduces the as-built text it extends and is
  unsyncable until built, like everything else here.
- Choose a realtime substrate beyond what PLAN already fixed (versioned rows +
  broadcast + presence). Per-artifact CRDT escalation stays a separate change.
- Own moderation, tray packaging, node authoring, or real-world handoffs — those
  are `complete-independent-full-platform-targets`. Nor the paid-market,
  data-commons, demand-side, hardware, training, pooled-ownership, or token
  groups — those are `build-forward-platform-capabilities` and its successors.

## Decisions

### D1 — 1A applied: the catalog references commons knowledge, it does not copy it

PLAN's per-domain canonical-store decision makes Postgres canonical for
**catalog, ledger, inbox, and market**, and the OKF bundle canonical for the
**commons**. The catalog is squarely in the first set, so
`collaborative-catalog-and-editing` specifies Postgres-canonical catalog rows
without reopening that decision.

The load-bearing consequence is the seam: a catalog row that *describes* a piece
of commons knowledge is an index entry, not a second canonical copy. Divergence
between an index row and the bundle resolves in favor of the bundle, always.
Without that rule, "Postgres is canonical for the catalog" quietly becomes
"Postgres is canonical for everything the catalog can point at," which is exactly
the global framing the host rejected.

The same decision's brain corollary — a user designs their own brain
organization, OKF is the default and not a mandate — means brain organizations
are themselves discoverable, remixable commons artifacts. They are catalog
subjects, not catalog schema.

### D2 — 1C applied: zero new primitives, and the call is recorded per behavior

Rule 1's irreducibility door is the only way a new top-level primitive ships. The
source sections name eleven standalone RPCs. Each gets a recorded call:

| Behavior target | Named in the architecture as | Irreducible? | Lands as |
|---|---|---|---|
| Node discovery | `discover_nodes` | No — a ranked read over existing artifacts | `read_graph` action |
| Standing similarity interest | `subscribe_similar_in_progress` | No — a durable stored query plus a read | written via `write_graph`, read via `read_graph`; the realtime push is a web transport, not an MCP handle |
| Remix from N parents | `remix_node` | **No — already expressible.** `attribution_edge` is `UNIQUE (parent_id, child_id)`, so one child already carries N parent edges | `write_graph` action; the gap is *atomicity*, not shape |
| Convergence proposal | `propose_convergence` | No | `write_graph` action |
| Convergence ratification | `ratify_convergence` | No as a handle. The *authority boundary* (authenticated, one-per-source, recusal) is platform enforcement; the *policy* (quorum, window) has many plausible shapes and is commons config | `write_graph` action + seeded remixable policy |
| Node update | `update_node` | No | `write_graph` action over the CAS/revision path |
| Comment | `comment` | No — append-only notes already exist | existing unified-notes substrate |
| Export | `export_my_data` | No | `read_graph` action |
| Deletion | `delete_account` | No | `write_graph` action |
| Deletion confirmation | `request_delete_confirmation` | No | `write_graph` action |
| Feedback | `/feedback` | No — `wiki-commons` already ships typed filings with per-kind IDs and dedup | `write_page` typed filing, extending that contract (D9) |

**Result: zero irreducibility findings, zero new advertised handles.** The seven
canonical handles (`read_graph`, `write_graph`, `run_graph`, `read_page`,
`write_page`, `converse`, plus `get_status`) are unchanged, and
`live-mcp-connector-surface` needs no delta from this change.

**This ledger is normative, not prose.** The first review of this change found the
zero-new-handle conclusion argued in design text but carried by only one
capability-scoped requirement, leaving discovery, remix, convergence, export,
deletion, confirmation, succession, and presence without a normative routing
condition. So the ledger above is restated as a **single cross-capability
requirement** — *"Every behavior in this change and its successors routes under
the canonical handle set"*, in `collaborative-catalog-and-editing` — which fixes
the routing per behavior, asserts `tools/list` remains exactly the seven, and is
inherited unchanged by every successor split out of any of the five capabilities.
Each of the eight behaviors additionally carries the no-new-handle condition in
its own requirement, so the invariant survives a successor split that takes only
one capability with it.

**The realtime transport is outside MCP, and that is an already-approved
position, not a new one.** Subscription requests, presence heartbeats, and change
broadcasts ride the non-MCP web transport the architecture's realtime strategy
already fixed. They are named as such in `realtime-collaboration-presence` and in
the cross-capability requirement, precisely so "every target is under the seven
handles or the commons" is not quietly doing work that a fourth transport is
actually doing. That transport adds no handle and authorizes from the same
authenticated subject as the canonical handles.

The remix-from-N row is the one worth stating explicitly, because it is the
strongest new-primitive candidate in the source material and it **fails** the
test on verifiable grounds rather than on judgment: `tinyassets/attribution/
schema.py` already keys `attribution_edge` on `(parent_id, child_id)`, so
set-valued parentage is a shipped capability of the substrate. Multi-parent
lineage does not need a primitive; it needs one transactional write so a partial
failure cannot mint a half-attributed derivative.

**Correction to the first draft of this decision.** That draft also claimed the
gap was making an *existing* per-artifact `credit_share ≤ 1.0` invariant hold
across all N parents at once. That overstated the shipped guarantee, and the
review was right to flag it. What is actually enforced today is per row:
`attribution_credit` carries `CHECK (credit_share >= 0.0 AND credit_share <=
1.0)` and `UNIQUE (artifact_id, actor_id)`, and `tinyassets/api/market.py` clamps
each incoming share into `[0, 1]` before insert. **The aggregate sum across an
artifact's contributors is not enforced anywhere** — it exists as a design
comment at the top of `attribution/schema.py` and as `RemixProvenance.
is_credit_valid`, an advisory helper no write path is obliged to call. So the
implementation gap is *two* things, not one: atomic N-edge writing **and new
aggregate enforcement**. The requirement says so explicitly, and tells an
implementing lane to specify what happens to pre-existing rows that already
violate the bound rather than assuming there are none.

This correction does not change the irreducibility result. Multi-parent lineage
is still expressible on shipped substrate, so remix-from-N still lands as a
`write_graph` action rather than a primitive; the missing aggregate check is a
constraint to add inside that action, not a reason for a new handle.

Rule 1's corollary carries the rest: ranking formulas, convergence quorum policy,
custody-selection guidance, and moderation rubrics all have many plausible
shapes, so they are user-buildable by definition and belong to the commons.

### D3 — 1B applied: custody-agnostic means the platform states what it knows

1B is settled *as an open question*: custody is per-situation and user-chosen
across host machine, private universe brain, vault, and platform-held, and no
lane may treat either the never-store or the platform-store answer as settled.
Waiting for the research to close would leave portability and deletion unowned
indefinitely, so this change is written to be correct under all four modes.

The mechanism is that **custody mode is data, not an assumption**. Every item in
an export or a deletion carries the custody mode it lives under, and the platform
makes exactly two kinds of claim about it:

- **What it holds**, which it can produce or erase directly and assert as fact.
- **What another holder holds**, for which it can only emit a resolvable
  retrieval descriptor (export) or a verifiable deletion obligation (deletion),
  and must report the outcome as *confirmed* or *unconfirmed* — never as done.

This is what stops the two failure modes 1B warns about. An export that assumes
platform-held custody silently drops the user's host-resident data and reports
success. A deletion that assumes platform-held custody reports "deleted" when
bytes survive on a vault or a host. Both are honest under this model and both
are lies under a platform-held assumption.

Rule 4 pre-authorizes the degraded path: "content gated on a host being online
yields a graceful 'no host online' signal." So an offline holder produces a
labelled partial bundle and a resumable deferral, not a failure and not a silent
omission.

Rule 4's standing anti-pattern — "any custody design a user cannot export out
of" — becomes an executable conformance obligation: a custody mode that cannot
satisfy the export contract is non-conforming and cannot ship.

**The manifest itself has custody, and the first draft did not say whose.** The
review caught the real hole: requiring every owned item to carry a custody mode,
requiring export to enumerate *every* item and name its holder, requiring
deletion to issue an obligation to *every* holder, and requiring the unconfirmed
list to stay retrievable *after account deletion* all presuppose two things the
draft never located — an authoritative item/holder manifest, and a post-deletion
way to authenticate to it. Silently placing both on the platform would assume
exactly the platform-held answer 1B forbids assuming; leaving them out makes
"every item" unprovable in host and vault modes.

Resolution, now specified rather than implied:

- **The manifest is assembled, not owned.** The platform holds what it holds,
  plus a *holder registration* per non-platform holder — identity, mode,
  reachability, last-enumeration time. A registration is explicitly **not** an
  inventory of that holder's contents. The manifest for a request is the union of
  the platform's records and each reachable holder's own enumeration, with each
  entry attributed to the holder that asserted it.
- **Coverage is stated per mode.** Which holders answered, which deferred, which
  cannot enumerate. Every "every owned item" guarantee reads as scoped to that
  union plus its coverage statement — so a silent holder degrades the claim
  visibly instead of invisibly.
- **Each mode's resolution is defined**: platform-held enumerates directly; a
  private universe brain enumerates under the owner's authenticated authority; a
  vault enumerates under the owner's key authority and the platform cannot
  enumerate contents without it; a host enumerates when online and defers when
  not. A new mode ships only with its resolution defined.
- **The post-deletion receipt does not depend on the deleted identity.** It is
  issued at confirmation time as a self-contained document the principal keeps
  (readable with no platform call at all) plus a bearer capability that resolves
  current obligation state without resolving, requiring, or revealing the erased
  identity. Its server-side record holds item and holder references and their
  state — not identifying data retained to serve a deleted principal, which would
  reintroduce the retention the deletion just removed.

What stays open is the *evidence standard* for a non-platform discharge, which is
open question 1 below. Locating custody of the manifest does not close that, and
this change does not pretend it does.

### D4 — 1D applied: the split is enforcement vs guidance, and it cuts through every group

Platform code owns boundaries a user must not be able to move. Everything a user
can replace or extend without asking is seeded remixable commons content.

Applied per group: visibility filtering on discovery candidates, derived signal
blocks, presence records, realtime streams, exports, and feedback filings is
**enforcement** — platform code. What to put in a feedback context, which
custody mode fits a workload, which redaction pattern suits a threat model, how
to weight discovery signals, and what convergence quorum is appropriate are
**guidance** — seeded, remixable commons pages, with `_WIKI_CATEGORIES` as the
shipped precedent for seeding a vocabulary without freezing it.

The enforcement half has a live defect class to design against, not just a
principle. STATUS carries a P1 concern that branch get/describe leaks restricted
wiki path, title, and summary through `_related_wiki_pages` — a *derived* block
escaping the visibility predicate that the direct read path honors. Discovery is
that same shape with a much larger surface: provenance chains, related-artifact
lists, negative signals, and active-work counts are all derived blocks that can
name artifacts the caller cannot read. So the requirement is written as filtering
the derived blocks by the same predicate as direct reads, and as leaking neither
metadata nor existence — a suppressed candidate must not be inferable from a
gap in a rank sequence or a count that includes it.

**Two of the first draft's enforcement claims were assertions rather than
checkable requirements, and the review was right about both.**

*Free-text filing bodies.* The draft said the read-visibility predicate applies to
the filing body and guaranteed no unpublishable private content enters it. A read
ACL can resolve structured references, attachments, and platform-derived context;
it cannot prove that arbitrary pasted prose contains nothing private without the
content-classification machinery 1D deliberately leaves to guidance. The hard
boundary is therefore scoped to **structured and platform-derived elements**,
where a predicate can actually resolve the referent. Caller-authored prose gets an
explicit publication confirmation before the filing exists, plus ordinary post-hoc
moderation — and the platform explicitly does **not** claim to have checked it.
Claiming an unenforceable guarantee is worse than stating the real one.

*Timing.* The draft prohibited inference "through timing," which no test
enumerates and no implementation can prove absolutely. That becomes a stated
**noninterference bound with an executable test model**: same query, two corpora
differing only by one restricted artifact, latency distributions indistinguishable
at a documented sample size, statistic, and threshold, with a measured violation
treated as a defect. This also constrains the implementation shape — suppression
work must not scale observably with the number of suppressed candidates. The
other seven leak channels (identifier, path, title, summary, snippet, count,
rank-gap, pagination) stay absolute, because those *are* directly enumerable.

### D5 — Ranking reuses the shipped selector contract instead of inventing one

`shared-goals-and-convergence` already specifies the pattern: a user-buildable
selector Branch rather than a fixed platform weighting formula (DESIGN-008), the
selector must be pure (a Branch carrying node effects or invoking child Branches
is rejected), binding is author-or-capability gated, and unbinding falls back to
a platform default selector. `evaluation-outcomes-and-attribution` says the same
for the quality leaderboard.

Discovery ranking is the same problem, so it reuses that contract rather than
adding a parallel one. The platform ships the signal block, the retrieval, and a
seeded default selector; the ordering policy is a remixable commons artifact.
This satisfies Rule 1's corollary and Rule 2 simultaneously, and it means
discovery inherits the purity guarantee that stops a ranking pass from causing
side effects.

### D6 — Presence is a signal, never an authority

The architecture describes presence-based "soft-lock." Making presence
authoritative would create a denial-of-service surface (hold a presence record,
block a writer) and a fail-open surface (lose the presence record, lose the
lock). Compare-and-swap on the version column is the sole conflict authority;
presence is advisory UX that expires on a heartbeat. This also keeps realtime
degradable: every collaborative operation must complete with the realtime
transport entirely down, which the forever-rule 24/7 posture requires and which
Rule 5 requires for browser-only users on constrained transports.

### D7 — Succession moves operator authority, never user content

§22's SPOF inventory is written from the platform-held era, where "grant the
successor access" implicitly meant access to everything. Under 1B that is a
backdoor: a user who chose vault or host-machine custody specifically to keep
content outside platform reach would find operator succession re-granting it.

So succession is scoped to **operator authority over platform infrastructure** —
registrar, DNS, deployment credentials, org admin, merge rights, treasury
signing, moderation authority. Gaining an operator role SHALL NOT gain access to
user content under any custody mode, and the succession runbook is checked for
that property. Where an operator legitimately needs platform-held user data
(a moderation review of reported content), that access flows through the same
authenticated, audited authority path as any other reviewer, not through
succession.

### D8 — Feedback stays authenticated on the MCP path; anonymity lives on the external path

§23.7 says feedback is not gated by tier and that anonymous readers file equally.
The shipped auth boundary says anonymous read, authenticated write, with pure-
write handles drawing a pre-dispatch 401. These conflict only if the MCP path is
assumed to be the only path.

Resolution: the MCP filing path (`write_page` typed filing) is authenticated like
every other write, and carries a per-invocation `attribute_as` choice so an
authenticated user can still file *pseudonymously*. Genuinely unauthenticated
feedback arrives through the external channels, is merged into the same queue,
and is marked lower-trust for triage and abuse purposes. Nobody is turned away,
and the write boundary is not weakened to achieve it. This is a reconciliation of
two shipped positions, not a new decision.

### D8a — The external tracker stays canonical; the commons filing is a staging record

The first draft of this change also wrote that "the commons filing SHALL remain
the canonical record." That was a **position reversal this lane had no authority
to make**, and the review caught it. The landed architecture §23.1 states that
GitHub Issues is the canonical public bug/feature-request surface, that the
`/feedback` tool opens an Issue, and that external channels route into it —
"GitHub remains the canonical queue." None of the four 2026-07-25 PLAN decisions
touches feedback-record ownership: 1A scopes catalog/ledger/inbox/market against
commons storage, 1B is private custody, 1C is primitives, 1D is
guidance-vs-enforcement. D8 reconciles authenticated MCP writes with anonymous
external intake; it does not license moving canonicity.

So the requirement now specifies the landed position: the external tracker is the
canonical queue, and the platform-side filing is a **durable staging and
provenance record** projected into it as an idempotent receipted outbound effect.
That keeps the property the draft was actually reaching for — a projection failure
must not lose the user's report — without moving canonicity to get it: the staging
record survives, is retryable, and is reported as *pending projection* rather than
as queued.

Whether the commons should ever *become* canonical is a real question (it would
make the commons self-hosting and remove a third-party dependency from the
feedback path), but it is a host decision, not a spec-authoring side effect. It is
recorded as open question 7 below, and the requirement forbids an implementing
lane adopting the reversal without it.

### D9 — Feedback filing extends the shipped typed-filing contract; it does not fork it

The draft specified feedback with its own per-kind filing, dedup, categories,
optional context, and attribution presentation — while declaring no MODIFIED
capability. `wiki-commons` already owns typed filings: `_KIND_ROUTING`, per-kind
`<PREFIX>-NNN` counters, a 0.5-threshold title-plus-body duplicate check, the
required-field rule, and the `file_bug` action. Two active owners for one contract
is how the two drift apart.

This change now carries a **target-only MODIFIED delta against `wiki-commons`**
that extends that one contract: the feedback-only categories get their own
prefixes and counters, feedback categorized as a bug or feature request routes to
the existing BUG and FEAT counters (so a user report and a platform-filed one
share an identifier space and a duplicate check), `attribute_as` is a presentation
choice that does not participate in filing identity, optional caller context is
bounded by the publication requirement in `platform-succession-and-feedback`, and
`component`/`severity` become optional *for feedback-originated kinds only* —
an end user does not know the component — while the four pre-existing kinds keep
requiring them.

That delta reproduces the as-built paragraph and its three scenarios verbatim,
because a MODIFIED requirement replaces its predecessor wholesale on sync. It is
target-only like everything else here and may not be synced until the extension is
built.

## Boundaries with adjacent shipped capabilities

Every delta here is ADDED into a new capability **except one**: `wiki-commons`
carries a target-only MODIFIED delta for the typed-filing extension (D9), because
the alternative was two owners for one filing contract. These adjacencies are
stated so an implementing lane does not silently overwrite as-built truth:

- **`data-commons`** (a delta of `build-forward-platform-capabilities`) owns
  dataset manifests, licences, pricing, gates, contributor settlement, and
  Dataset Forge. Portability *consumes* that generic result: an export carries a
  dataset's own manifest reference and retrieval descriptor and redefines none of
  it. This is an explicit **read and boundary dependency** — a portability
  successor that includes dataset assets must name `data-commons` in its
  dependencies. No delta move is required on the present text.
- **`shared-goals-and-convergence`** ships exact-identifier common-node discovery
  and a fixed server-side archive-consultation heuristic. The target semantic and
  structural discovery surface is *additive*; it does not replace exact-identifier
  matching. If an implementing lane concludes it must, that lane authors the
  MODIFIED delta then.
- **`wiki-commons`** ships dry-run-first, hash-guarded page deletion that protects
  anchors, plus CAS patch/supersede. Account deletion composes over that path
  rather than adding a second deletion mechanism. Its typed-filing contract is the
  one place this change does author a MODIFIED delta (D9); the delta is
  target-only and reproduces the as-built text it extends.
- **`evaluation-outcomes-and-attribution`** ships append-only contribution and
  attribution ledgers. Identity detachment on account deletion must therefore be
  a resolution-time suppression over an authoritative tombstone, not a ledger
  rewrite — an append-only ledger that can be rewritten is not append-only.
- **`knowledge-retrieval-and-memory`** ships OKF v0.1 bundle export over a curated
  wiki source set. That is *commons bundle* export; user-data portability is a
  different subject with a different completeness contract. A portability export
  may reuse the OKF serializer; it may not be mistaken for one.

## Risks / Trade-offs

- [Risk] Custody-agnostic export/deletion is materially harder than platform-held
  and could stall on the open research. → The contract is written so each custody
  mode is a conformance target, not a precondition: a lane may implement
  platform-held conformance first and add modes incrementally, provided it never
  reports non-conforming modes as covered.
- [Risk] "Unconfirmed deletion" is a weaker guarantee than users expect from a
  delete button. → It is the true guarantee under non-platform custody. The
  requirement forces it to be surfaced rather than papered over; what evidence
  standard upgrades unconfirmed to confirmed is an open question below.
- [Risk] Discovery is the largest derived-signal surface in the platform and the
  visibility class it must resist is currently live in a smaller surface. → The
  requirement is written against existence-leak and metadata-leak explicitly, and
  the acceptance obligation includes a negative test per derived block.
- [Risk] Five capabilities in one change is large for a single review. → They
  share exactly one gate (the four PLAN positions) and were blocked as one group.
  Per the umbrella's D1 pattern, each becomes a narrower successor before
  implementation; splitting the *specification* would fragment the shared
  irreducibility ledger and the shared custody model.
- [Risk] Seeded commons defaults (selector, convergence policy) get inherited by
  many users and become de-facto frozen. → Captured as an open question; the
  requirement keeps them replaceable, which is the property 1D actually demands.

## Migration Plan

1. Split each capability into a narrower successor change before implementation,
   per the umbrella's D1 rule. This change records cross-group invariants: the
   irreducibility ledger (D2) — which is normative and inherited unchanged, not
   advisory — the custody model and manifest resolution (D3), the
   enforcement/guidance split with its two scoped acceptance methods (D4), the
   selector contract reuse (D5), the canonical-queue position (D8a), and the
   single-owner filing contract (D9). A successor whose scope includes dataset
   assets also names `data-commons` as a read dependency.
2. Implement catalog and collaboration first — discovery, presence, portability,
   and feedback all read from the catalog.
3. Land visibility enforcement and its negative tests *with* each surface, never
   as follow-up hardening.
4. Prove every surface under the §14 concurrency/load matrix as part of
   implementation.
5. Complete live connector canaries, a rendered chatbot conversation, and
   freshness-stamped post-fix clean-use evidence for every public surface.
6. Sync each capability's delta into `openspec/specs/` only in the lane where its
   implementation and acceptance evidence land, then archive.

Rollback is per capability: keep the surface dark behind its flag, revert the
slice, and restore the preceding schema version by its tested rollback plan.

## Open Questions

These are not covered by the four landed positions. They are recorded, not
answered.

1. **(1B) What evidence upgrades a non-platform deletion from unconfirmed to
   confirmed?** A signed attestation from the holder, destruction of a vault key,
   or nothing verifiable at all for host-machine custody? And which custody modes
   must reach export/deletion conformance before launch versus after? 1B is open
   by design and does not answer either.
2. **(1B + legal) Is resolution-time identity suppression sufficient for
   right-to-be-forgotten over append-only ledgers**, or does it require
   cryptographic erasure (key destruction)? Jurisdiction-dependent; needs the
   same specialist legal review already tracked for forward/training/hardware
   routes.
3. **Deletion × convergence.** What happens when a convergence proposal's
   required ratifier has their identity detached by account deletion — does the
   proposal stall, auto-recuse the seat, or fall to a quorum rule? Neither 1B nor
   1C settles it, and the answer changes whether ratification is a per-identity
   or a per-artifact-owner-set obligation.
4. **Repository topology for exports.** 1A settles canonicity per domain but not
   repo shape: is the commons-bundle git snapshot the same repository as the
   catalog's GitHub export sink, or two (as §16.4 proposed)? Affects clone
   ergonomics and PR-ingest routing, not canonicity.
5. **Governance of widely-inherited commons seeds.** 1D blesses seeding a
   remixable default; it does not say who authors and maintains the default
   discovery selector and convergence policy, or what review applies to changing
   a seed thousands of users inherit by not choosing.
6. **Named succession principals** — the roster, the real-value-cutover human
   co-signer, and the registrar successor. Host/founder action; no provider can
   complete it. Recorded here the same way the umbrella records its counsel gates.
7. **(Host decision) Should the commons filing ever replace the external tracker
   as the canonical feedback queue?** The landed architecture §23.1 makes GitHub
   Issues canonical and routes every channel into it, and this change specifies
   that position (D8a). A commons-canonical queue would make feedback
   self-hosting and drop a third-party dependency from a critical-path surface,
   but it is a change of record ownership on a public surface — host authority,
   not spec-authoring authority. Until it is decided, an implementing lane may
   not adopt the reversal. Smallest concrete ask: *keep GitHub canonical, or
   authorize the commons as canonical with GitHub as the mirror?*
