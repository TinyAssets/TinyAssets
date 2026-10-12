> **Target-only change — sync/archive guard.** Every requirement in this change
> describes *intended future behavior*. **Nothing in sections 2-6 is built on
> `origin/main`.** `openspec archive` syncs a change's delta specs into
> `openspec/specs/` as a side effect, and `openspec/specs/` is as-built truth —
> so archiving this change while its capabilities are unbuilt would write five
> fictional capabilities into canonical truth. **No delta here may be synced and
> this change may not be archived until the corresponding implementation and its
> named acceptance evidence have landed.** Sections 1 and 7 are this lane's own
> authoring and foldback work and are the only sections it may check.
>
> The guard covers the `wiki-commons` MODIFIED delta too. That delta reproduces
> the as-built typed-filing paragraph verbatim and adds unbuilt feedback
> extensions; syncing it early would write the extension into as-built truth and
> would also overwrite a live requirement with a partly-fictional one.
>
> **Count as of 2026-07-25: 58 tasks, 9 complete** (all eight of section 1, plus
> 7.1 after the opposite-provider review was folded in). The 45 tasks in sections
> 2-6 and the four remaining in section 7 are open and may not be checked by
> annotation, delegation, or host-gating — only by landed code plus its named
> acceptance evidence.

## 1. Author the change against the landed PLAN positions

- [x] 1.1 Verify the four blocking PLAN positions actually landed before authoring against them.
  - Verified 2026-07-25 against `origin/main` `PLAN.md`: **1A** — *Design Decisions* "Canonical store is per-domain, not one store for everything (host-approved 2026-07-25)" plus "A user's brain organization is theirs to design (host-approved 2026-07-25)"; **1B** — Scoping Rule 4 "Private-data custody is an OPEN RESEARCH QUESTION (host-approved 2026-07-25 — reopened, previously stated here as settled)" plus the matching *Open Tensions* entry; **1C** — Scoping Rule 1 "Irreducibility finding — the only door a new top-level primitive comes through (host-approved 2026-07-25)"; **1D** — Scoping Rule 3 "Guidance is community-built; the platform owns enforcement boundaries only (host-approved 2026-07-25)". *Reference: Full-Platform Architecture* carries the three matching carve-outs.
- [x] 1.2 Scope the change to the target groups with no active owner and fence it against the two sibling target changes.
  - `2026-07-22-openspec-full-coverage-audit.md (archived in git)` § *Full-platform target ownership* names three unowned PLAN-gated groups. `complete-independent-full-platform-targets` owns moderation, tray packaging, node authoring/autoresearch, and real-world handoffs; `build-forward-platform-capabilities` and its successors own boundary, data, demand, hardware, training, pooled-ownership, and token. Neither claims catalog/collaboration, discovery/remix, presence, or portability/deletion/succession/feedback. No file overlap: this lane writes only `openspec/changes/complete-plan-gated-platform-targets/`.
- [x] 1.3 Apply 1A: specify Postgres-canonical catalog rows and the index-not-copy seam against OKF-canonical commons knowledge, with bundle-wins divergence resolution.
- [x] 1.4 Apply 1C: record an irreducibility call for all eleven standalone RPCs named across architecture §§15, 16, 21, 22, and 23 (`design.md` § D2), and carry it as one **normative cross-capability requirement** inherited unchanged by every successor rather than as design prose. **Result: zero new top-level primitives, zero new advertised handles.**
  - The strongest candidate, remix-from-N, fails the test on verifiable grounds rather than judgment: `tinyassets/attribution/schema.py` keys `attribution_edge` on `UNIQUE (parent_id, child_id)`, so one child already carries N parent edges. Set-valued parentage is shipped substrate; the gap is atomicity of the N-edge write **plus new aggregate credit enforcement**. Corrected 2026-07-25 after opposite-provider review: the aggregate `credit_share ≤ 1.0` bound is **not** an existing invariant. `attribution_credit` enforces `CHECK (credit_share >= 0.0 AND credit_share <= 1.0)` per row and `UNIQUE (artifact_id, actor_id)`; the aggregate sum is enforced nowhere — it lives only in a schema-module design comment and the advisory `RemixProvenance.is_credit_valid` helper, and `tinyassets/api/market.py:922` clamps only the individual share. The result is unchanged: still a requirement, not a primitive.
  - The realtime subscription/heartbeat/broadcast path is identified as the **already-approved non-MCP web transport**, not a new handle and not a twelfth RPC, so the "everything is under the seven handles or the commons" formulation does not silently rest on an unnamed fourth transport.
- [x] 1.5 Apply 1B: write portability, deletion, and succession custody-agnostic across all four modes, with custody recorded per item and the platform claiming only what it holds.
  - Includes the succession corollary (`design.md` § D7): gaining an operator role grants no access to user content in any custody mode. Under the platform-held assumption §22's SPOF inventory would have made operator succession a backdoor into vault- and host-custody content.
- [x] 1.6 Apply 1D: split enforcement from guidance in every group — visibility filtering on projections, discovery candidates and derived blocks, presence, streams, exports, and filings is platform code; selection/redaction/weighting/quorum guidance is seeded remixable commons content.
- [x] 1.7 Reuse the shipped user-buildable selector contract for discovery ranking instead of inventing a parallel one, including its purity rejection and default-selector fallback.
  - `openspec/specs/shared-goals-and-convergence/spec.md:75` (DESIGN-008) and `evaluation-outcomes-and-attribution` § *quality leaderboard*.
- [x] 1.8 Record what the four positions do **not** settle as open questions rather than inventing positions (`design.md` § *Open Questions*, six entries).

## 2. Collaborative catalog and editing

- [ ] 2.1 Implement the Postgres-canonical catalog with commons entries as rebuildable index rows, bundle-wins divergence resolution, and brain organizations as catalog subjects rather than catalog schema.
- [ ] 2.2 Implement compare-and-swap versioned writes with monotonic per-artifact versions and refusals that return current version plus a change description.
- [ ] 2.3 Implement immutable revision append, arbitrary-revision restore recorded forward as a new revision, and refusal of any attempt to edit or remove a revision record.
- [ ] 2.4 Implement content-class-bound collaboration models, refusing commons-path writes to platform code with the fork-and-PR path named.
- [ ] 2.5 Implement derived one-way external export plus a round-trip import that re-enters the ordinary authenticated write path with no version, authority, visibility, or moderation bypass and no resurrection of withheld content.
- [ ] 2.6 Implement visibility enforcement across every catalog projection including derived fields and counts, with authority resolved from the authenticated subject and no environment fallback.
- [ ] 2.7 Route every behavior as an action under an existing canonical handle per the cross-capability handle invariant, and add an invariant test asserting `tools/list` advertises exactly the seven canonical handles. The test SHALL cover this change's behaviors as a set, not one capability's, so a successor split cannot drop it.
- [ ] 2.8 Prove catalog behavior under the §14 concurrency/load matrix: concurrent same-artifact writers, revision-append contention, import/direct-write races, and visibility enforcement under load.

## 3. Node discovery and remix

- [ ] 3.1 Implement the single-call composite signal contract with absent-not-defaulted signals and a stable query identifier.
- [ ] 3.2 Implement selector-delegated ranking with the seeded default selector, bind-time purity rejection, and unbind fallback.
- [ ] 3.3 Implement visibility filtering across candidates and every derived block, with negative tests per block for identifier, path, title, summary, snippet, count, rank-gap, and pagination leakage.
  - This is the same defect class as the live `_related_wiki_pages` branch-describe leak on the STATUS board — a derived block escaping the predicate the direct read honors — on a much larger surface. The negative tests are the requirement, not follow-up hardening.
- [ ] 3.4 Implement the timing noninterference acceptance method the leak requirement now states: a repeatable harness that runs one fixed query against two corpora differing only by the presence of one restricted matching artifact, at a documented sample size, and compares latency distributions under a documented statistic and significance threshold. Record the bound and its parameters with the implementation; treat a measured violation as a defect in the surface. Added 2026-07-25 after opposite-provider review — the first draft prohibited timing inference absolutely with no acceptance method, which is not checkable.
- [ ] 3.5 Implement commons-equal ranking with no platform-origin weighting outside the selector, and labelled cross-domain matches.
- [ ] 3.6 Implement atomic remix-from-N over the existing `attribution_edge` substrate: all-or-nothing edge and credit writes, cycle rejection, and idempotent retry on a derivation identity.
  - **Also introduce aggregate credit enforcement** — a transactional check that recorded shares for one artifact sum to ≤ 1.0 across all contributors. This is new enforcement, not a preserved invariant: the store enforces only the per-row `[0, 1]` range and `UNIQUE (artifact_id, actor_id)`. Specify the treatment of pre-existing rows that already violate the aggregate bound rather than assuming there are none, and test the aggregate refusal directly so it cannot pass vacuously on the per-row constraint.
- [ ] 3.7 Implement propose-then-ratify convergence with authenticated append-only ratifications, proposer recusal, supersede-not-delete, walkable superseded lineage, and seeded remixable policy values that cannot configure away the enforcement properties.
- [ ] 3.8 Implement standing similarity interest as a durable owner-bound stored query read back through the ordinary read path, with owner-visibility-scoped matching, revocation, expiry, and cost scaling in outstanding queries rather than queries×changes.
- [ ] 3.9 Prove discovery under the §14 matrix: read-dominant load at the stated multiple, concurrent remix on shared parents, concurrent ratification of one proposal, and stored-query fan-out.

## 4. Realtime collaboration and presence

- [ ] 4.1 Implement presence as an advisory expiring per-artifact signal that never grants, denies, delays, or reserves a write.
- [ ] 4.2 Implement heartbeat expiry with a bounded margin and no requirement for explicit release, with no presence inheritance across artifacts, sessions, or principals.
- [ ] 4.3 Implement versioned-row broadcast carrying artifact and resulting version, introducing no convergent-replication substrate.
- [ ] 4.4 Implement delivery-time visibility evaluation, existence-non-disclosure on unauthorized subscription, delivery stop on authority revocation, and collaborator-identity filtering. Carry subscription, heartbeat, and broadcast on the already-approved non-MCP web transport, authorizing from the same authenticated subject as the canonical handles, and assert in test that no subscription, presence, or delivery handle is advertised.
- [ ] 4.5 Prove full collaborative function with the realtime transport entirely down, and loss-free duplicate-free recovery on reconnection.
- [ ] 4.6 Implement subscription-bounded fan-out with documented per-connection subscription and rate bounds, explicit bounded refusals, and shed/coalesce that never degrades commit latency or success.
- [ ] 4.7 Prove realtime under the §14 matrix at the stated multiple of projected load, including hot-artifact fan-out and mass reconnection.

## 5. Data portability and deletion

- [ ] 5.1 Implement per-item custody-mode recording with unknown-fails-safe handling, and verify no portability or deletion path assumes either the platform-held or the never-store position.
- [ ] 5.2 Implement the custody-mode-scoped manifest itself — the piece the first draft left unlocated. The platform's records cover the items it holds plus a **holder registration** (identity, mode, reachability, last-enumeration time) per non-platform holder; a registration is not an inventory of that holder's contents. Assemble each manifest at request time as the union of platform records and each reachable holder's own enumeration, attribute every entry to the holder that asserted it, and state coverage per custody mode (answered / deferred / unenumerable). Define the enumeration resolution for each of the four modes, refuse to offer a mode with no defined resolution, and test that the platform cannot synthesize a vault-mode inventory without the owner's key authority.
  - Where an enumerated item is a `data-commons` dataset asset, consume that capability's manifest, licence, and retrieval contract as a read dependency; do not restate dataset licensing, pricing, gating, or contributor provenance.
- [ ] 5.3 Implement export enumeration across all owned categories with enclose-or-descriptor per item, an explicit completeness statement, partial labelling, and no silent omission. Read every "every owned item" guarantee as scoped to the assembled manifest plus its coverage statement, never as a global claim over a mode whose holder did not answer.
- [ ] 5.4 Implement per-custody-mode export conformance reporting, and refuse to offer any mode that cannot satisfy enumeration and retrieval by its owner.
- [ ] 5.5 Implement graceful resumable deferral for unreachable holders, distinguishable from permanent unavailability, delivering the remainder rather than withholding it.
- [ ] 5.6 Implement cross-principal export privacy across whole items, derived material, manifests, and counts, with no elevated-role reach.
- [ ] 5.7 Implement deletion as direct bounded erasure of platform-held content with per-item erasure records, plus issued deletion obligations to other holders with confirmed/unconfirmed reporting that never counts an issued obligation as deletion. Scope obligations and discharge records per holder and per custody mode, with no aggregate claim that outruns the per-holder records.
- [ ] 5.8 Implement the post-deletion receipt path that does not depend on the deleted account identity: a self-contained receipt document issued at confirmation listing each item, holder, mode, and confirmed/unconfirmed state and readable with no platform call, plus a bearer capability that resolves current obligation state without resolving, requiring, or revealing the erased identity. The capability's server-side record carries only item and holder references and their state — no retained identifying data for the deleted principal — and expires under a stated policy without invalidating the document. Added 2026-07-25 after opposite-provider review: the first draft required the unconfirmed list to stay retrievable by a principal whose identity it had just erased.
- [ ] 5.9 Implement wiki-orphan survival: contributions remain readable and anonymous, lineage resolves to an anonymous ancestor, and no derivative is cascaded.
  - Composes over the shipped `wiki-commons` dry-run-first hash-guarded page deletion path; do not add a second deletion mechanism.
- [ ] 5.10 Implement identity detachment as an authoritative marker consulted at resolution time over append-only ledgers, honored by every resolving surface including regenerated external mirrors, with non-honoring surfaces withholding the identifier rather than leaking it.
- [ ] 5.11 Implement initiator-bound expiring confirmation, refusal of third-party initiation and of replay, and pre-confirmation disclosure of irreversibility, export-first, and possible unconfirmed items.
- [ ] 5.12 Prove portability and deletion under the §14 matrix: concurrent export and deletion for one principal, holder-offline and holder-recovery paths, deletion racing an in-flight write, and detachment consistency across concurrent resolutions.

## 6. Platform succession and feedback

- [ ] 6.1 Implement the machine-readable successor roster and its automated completeness/staleness check, reporting without blocking unrelated work.
- [ ] 6.2 Implement and verify the operator-authority boundary: succession grants no content access in any custody mode, and out-of-reach custody stays out of reach even to a successor holding every operator role.
- [ ] 6.3 Implement phase-split executable bus-factor gates reporting per-condition results, with real-value conditions unsatisfiable by an automated or simulated participant and not evaluated as launch blockers.
- [ ] 6.4 **Host/founder action — no provider can complete this.** Record the named succession principals: the operator roster holders, the real-value-cutover human co-signer(s), and the registrar successor. Recorded here the same way the umbrella records its counsel gates; 6.3's real-value gate stays unmet until this lands.
- [ ] 6.5 Author the succession runbook covering all seven required areas, including redeploy-from-nothing without any operator's personal machine, and implement the role/secret/roster staleness divergence check.
- [ ] 6.6 Implement typed authenticated feedback filings by **extending the `wiki-commons` typed-filing contract** per this change's MODIFIED delta — not by adding a second filing mechanism. Feedback-only categories get their own prefixes and counters; bug and feature-request feedback reuses the existing BUG and FEAT counters and the existing 0.5-threshold duplicate check; `attribute_as` is presentation-only, stays outside filing identity, and retains the authenticated binding for abuse control; `component`/`severity` become optional for feedback-originated kinds only. Add an invariant test asserting no feedback handle is advertised and the filing routes under `write_page`.
- [ ] 6.7 Implement publish-authorization enforcement scoped to **structured and platform-derived elements** — references, attachments, derived context and summaries — with explicit named refusal rather than silent stripping. Do **not** implement content classification over caller-authored prose; instead require an explicit publication confirmation stating the filing will be publicly readable, keep prose subject to post-hoc moderation, and make no claim that prose was checked. Seed the feedback guidance as replaceable commons content, not platform policy code. Scoped 2026-07-25 after opposite-provider review: a read ACL cannot prove arbitrary prose is free of private material, and 1D leaves classification to guidance.
- [ ] 6.8 Implement projection into the **canonical external queue** as an idempotent receipted outbound effect under the existing effect-authority and receipt contract, recording the receipt and external identifier against the filing, retrying without duplication, reporting an unprojected filing as pending projection rather than queued, and admitting unauthenticated external inbound at lower trust. The platform-side filing is a durable staging and provenance record, not the canonical queue.
  - Corrected 2026-07-25 after opposite-provider review. The first draft declared the commons filing canonical, reversing the landed architecture §23.1 position ("GitHub remains the canonical queue") with no PLAN decision authorizing it. Do not adopt the reversal without the host decision recorded as `design.md` open question 7.
- [ ] 6.9 Prove the feedback path under the §14 matrix: concurrent duplicate submissions, mirror retry under uncertain outcomes, and burst intake.

## 7. Foldback

- [ ] 7.1 Validate the change strictly, obtain an opposite-provider review, and **incorporate its findings, before PR or merge.**
  - Gate wording corrected 2026-07-25. It previously read "before pushing" and was checked while the branch had already been pushed and the verdict was still pending — an irreversible gate recorded as met before its evidence existed. The branch being pushed is not the risk boundary here; PR/merge is.
  - Evidence: `openspec validate complete-plan-gated-platform-targets --strict` passes; full-tree `openspec validate --all --strict` result recorded in `LANE_REPORT.md`. Codex cross-family verdict **`adapt`** returned 2026-07-25 with nine findings (seven substantive), all folded into this change in the same commit that re-checks this task; the per-finding disposition is in `LANE_REPORT.md` § *Codex verdict + fold*.
  - Not claimed: the folded text has **not** been re-reviewed. That confirming pass is task 7.2 and is open.
- [ ] 7.2 Obtain a confirming opposite-provider pass on the folded text before PR or merge. The `adapt` verdict was returned against the pre-fold change; the position reversal, custody-manifest closure, filing-ownership reconciliation, and scoped acceptance methods folded in response are unreviewed.
- [ ] 7.3 Split each capability into a narrower successor change before its implementation begins, per the umbrella's D1 rule, carrying forward the irreducibility ledger (D2, normative and inherited unchanged), the custody model and manifest resolution (D3), the enforcement/guidance split with its two scoped acceptance methods (D4), the selector contract reuse (D5), the canonical-queue position (D8a), and the single-owner filing contract (D9). A successor whose scope includes dataset assets names `data-commons` as a read dependency.
- [ ] 7.4 Complete live connector canaries, a rendered chatbot conversation, and freshness-stamped post-fix clean-use evidence for every public surface before treating it as implemented.
- [ ] 7.5 **Terminal task.** Sync each capability's delta into `openspec/specs/` only in the lane where its implementation and acceptance evidence land, then archive. As of 2026-07-25 zero of the five capabilities has any implementation on `origin/main`, so nothing may be synced and this change stays active. Archiving now would write five unbuilt capabilities into canonical as-built truth, and would additionally overwrite the live `wiki-commons` typed-filing requirement with a partly-unbuilt extension.
