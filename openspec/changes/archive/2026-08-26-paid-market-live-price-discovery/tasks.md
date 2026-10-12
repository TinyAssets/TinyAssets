## Premise verification — 2026-07-25

Reconciled on the user-fixed base `92d730bc` against landed #1737 and the
in-flight `codex/osx-market-workflow` transport lane. `live` means
independently buildable while dark; `blocked-*` work stays unchecked.

| Task | Classification | Evidence / boundary |
|---|---|---|
| 1.3 | built | Wave 2 is unlanded at 15/37; wallet/chain, R2-1 receipts, S14/B36, tenant, and domain owners are not live. |
| 2.1 | unblocked -> built (scope corrected 2026-07-25) | #1679 landed (`01e7ced7`) and 1.5's re-review returned APPROVE. Strict canonical-byte verification applies to descriptor bytes; demand is typed structured input normalized before matching/identity, not a raw-byte decoder. Quote-bound scope provenance was repaired through 3.1 — see the 2.1 evidence block. |
| 2.2 | unblocked -> built (scope corrected 2026-07-25) | Same #1679 landing. `descriptors.py` derives `descriptor_id` from strict descriptor bytes and `market_class_id` from normalized typed demand; it does not claim byte-canonical demand ingestion. `capability_id` stayed purged and payload/credential/price/routing/reservation/execution remain absent. |
| 2.3 | built | Opaque descriptor ids, injected issuer verification, exact canonical bytes, mutation refusal, recomputed totals, and conserved pure capacity values are covered. Atomic persistence remains in 5.1. |
| 2.4 | built | Exact landed-total and quote validation is pure and transport-independent. |
| 2.5 | built; finding B closed (2026-07-25) | Facet/substitutability coverage landed with the #1679 grammar. Fourteen manipulation controls carry mutation probes that go red when forced open; no self/linked-party fee exemption is encoded. Price and delivered quantity are settlement evidence, the canonical fee is schedule-derived, quote attributes are re-read from signed bytes, and all identity partitions now share one exact capped-weight total. Inconsistent cross-partition constraints fail closed. |
| 3.1 | built (join repaired 2026-07-25) | Fail-closed observation joins and independently fresh price fields use fixture receipts only. The join was reopened after Codex's money review: it agreed three receipt copies and then accepted every other price-index input from its caller. Identity/scope now derive from the quote and the price from the settled gross. |
| 3.2 | built | The field-fresh oracle is differential-tested against landed canonical settlement/index primitives. |
| 3.3 | built | The credential-blind read-only protocol cannot execute or return an executable route. |
| 3.4 | built | Two fake adapters cover complete, partial, malformed, stale, timeout, and independent-failure cases without live calls. |
| 3.5 | stale-inverted → built (hardened 2026-07-25) | Account- and principal-root self-trades stay excluded while every positive-gross settlement retains the canonical fee — now the amount its bound `fee_schedule_version` derives, not merely a positive number. Pair/buyer/seller-root dampening, infeasible-cap equal weighting, and composite-only clamping are covered, plus settlement-identity dampening and settlement-uniqueness against split-account and replayed volume. |
| 4.1 | built | Explicit free/BYOC/paid mandate selection creates no provider or money effect. |
| 4.2 | built | Pure eligibility/ranking returns evidence only and owns no reservation. |
| 4.3 | built | #1737 landed the immutable private receipt, ACL, retention/legal-hold, replay, commitment, and aggregate-projection contract with focused tests. |
| 4.4 | built | Boundary/mutation tests prove the result grants no execution, credential, capacity, or money authority. |
| 5.1 | blocked-transport-landing | Waits on `market_workflow`, `market_realtime`, `market_delivery`, and `013_paid_market_workflow.sql` in `codex/osx-market-workflow`. |
| 5.2 | blocked-transport-landing | Same residual `codex/osx-market-workflow` transport-file gate as 5.1. |
| 5.3 | built | Pure dark order policy composes the landed bucket and settlement oracles; no durable lock or transport was added. |
| 5.4 | built | Pure allow/refuse policy keeps unsupported instruments dark without registration. |
| 5.5 | split-host/legal (code built; artifact pending) | #1737 landed the fail-closed gate/tests; no current specialist legal-review artifact exists. |
| 6.1 | blocked-boundary | Public reads require tenant/privacy, boundary, load/security, canary, chatbot, and host activation gates. |
| 6.2 | blocked-tenant | Full handoff proof also needs R2-1, Wave 2, domain capacity, and boundary owners. |
| 6.3 | blocked-S14/B36 | Requires landed transport/public surfaces, tenant owner, and a reviewed capacity harness; draft PR #1695 is planning-only. |
| 6.4 | blocked-S14/B36 | Depends on 6.3 environment evidence and independent security/concurrency review. |
| 6.5 | blocked-host | No advertisement, live rollout, canary, chatbot proof, or organic-use evidence is authorized. |
| 7.1 | built | Refusal/default-dark definitions are explicit and no workflow, deployment registration, migration, or feature enablement was added. |
| 7.2 | blocked-transport-landing | Sync/archive would falsely claim the blocked public, executable, load, legal, and rollout work is complete. |

## 1. Contract ownership and review gates

- [x] 1.1 Release `paid-market-price-index-and-forwards` from the Wave 2 transaction/migration change and record this change as the build-forward umbrella’s narrow live-price successor.
- [x] 1.2 Obtain opposite-provider review of the proposal, design, full delta, and compute/LLM/task/fabrication research now archived by PR #1648 against TinyAssets context. Claude Sonnet approved the corrected source mapping on 2026-07-22; the verdict is recorded in `2026-07-22-paid-market-live-price-source-review.md (archived in git)`.
- [x] 1.3 Confirm the Wave 2 logical-accounting transaction owner, required wallet/chain-effect successor from `docs/design-notes/2026-04-18-full-platform-architecture.md` §18.6, outbound boundary authority/receipts successor, tenant identity/isolation, R2-1 provider authority/credential-class receipts, distributed execution, and each domain capacity/evidence owner have landed before their dependent adapter, public, executable, settlement, or paid-observation tasks; otherwise keep those tasks blocked and dark. Premise audit above records every dependent task and leaves all gated behavior dark.
- [x] 1.4 Run `openspec validate paid-market-live-price-discovery --strict`, full strict validation, requirement/scenario counts, and `git diff --check` after every contract amendment.
- [x] 1.5 (done 2026-07-25: Claude Opus 5 round-1 ADAPT -> six adaptations folded -> round-2 APPROVE; verdict held by the fable-fleet lead session and summarized in the PR #1679 comment thread) Obtain opposite-provider re-review of the amended V1 descriptor grammar, one-profile supply identity, four closed lane schemas, schema-owned comparison direction, separately derived public market-class identity, observation-scope provenance, demand/private-scope separation, error privacy, and digest contracts before starting tasks 2.1 or 2.2.

## 2. Pure descriptors, quotes, and evaluation

- [x] 2.1 Add failing unit/property tests for the descriptor's exact bounded ASCII canonical grammar; normalized structured descriptor construction versus strict descriptor canonical-byte verification; deterministic error precedence and safe paths; golden domain-separated descriptor and market-class `sha256:` identities; one atomic correlated and independently supportable supply profile per descriptor; all four closed lane schemas; immutable validator-revision attestation; schema-owned range and required-set-subset comparison; unsupported versions/revisions; overlapping compatible supply mapping to one normalized public market class; extra supply headroom not changing that class; typed demand canonicalization before public identity; demand/private values staying outside public identity; quote-bound observation-scope provenance; and hard substitutability mismatches. This task does not claim a raw demand-byte decoder.
  - Evidence (2026-07-25; `tests/test_paid_market_descriptors.py` 77 passed,
    `tests/test_paid_market_scope_provenance.py` 39 passed). In
    `tests/test_paid_market_descriptors.py`: golden domain-separated identities
    re-derived from hand-written envelopes plus pinned literals `:146-260`;
    caller `profile_id`/`descriptor_id`/`profiles`/`direction`/inclusivity refused
    `:271-318`; four closed lane schemas `:385-400`; bounded ASCII grammar, sets,
    ranges, identifiers, integers `:407-484`; fail-closed policy defaults `:491-535`;
    schema-owned direction and required-subset comparison `:542-619`; injected
    validator attestation / unavailable / unsupported-revision / revision-mismatch /
    refusal `:626-686`; decoder-vs-constructor split with `not_canonical` emitted only
    by the decoder `:697-716`, plus byte-length, non-ASCII, duplicate-key, depth, and
    NaN gates `:744-786`, and structure-before-validator precedence `:788-796`;
    headroom collapsing to one market class, equivalent demand order/duplicate/
    whitespace/case/NFKC variants collapsing before identity, unnormalizable
    shape/type/non-ASCII/bounds failing closed, and private demand staying outside
    public identity `:803-963`.
  - Quote-bound observation-scope provenance — corrected 2026-07-25 after Codex's
    money review found the earlier claim premature. The prior evidence pointed at
    two *disconnected* facts (a v2 signature covers scope; an independently
    assembled observation carries canonical bytes) and proved no join between them,
    so a quote/observation mismatch could not go red. Closing it required repairing
    task 3.1's observation-join surface, which this lane did rather than deferring:
    `join_paid_observation` now *derives* descriptor, market class, scope revision,
    and scope bytes from the `ValidatedQuote` instead of accepting them. Proof in
    `tests/test_paid_market_scope_provenance.py`: the observation's bytes are the
    signature-covered bytes, re-parsed out of `canonical_bytes` `:513`; a tampered
    scope never validates so no observation exists at all `:540`; a v1 quote —
    whose signature never spanned a scope binding — is refused `quote_scope_unsigned`
    `:555`; and a descriptor / currency / fee-version mismatch fails closed `:564`.
    The control is mutation-probed at
    `tests/test_paid_market_manipulation_mutation.py:477`.
- [x] 2.2 Implement pure immutable descriptor and public market-class projection values outside provider/domain execution code with an explicitly injected per-call validator that attests one immutable profile-schema revision and no mutable registry; derive rather than accept both ids; keep private demand commitments tenant-keyed and keep payloads, credentials, prices, routing, reservation, and execution absent.
  - Evidence (2026-07-25): `tinyassets/paid_market/descriptors.py` is a pure value
    module with no provider/domain execution import. `construct_descriptor:235`
    derives `descriptor_id`; `project_market_class:647` separately derives
    `market_class_id` from demand — both are derived, never accepted, and the closed
    `_ENVELOPE_FIELDS:135` refuses a caller-supplied `descriptor_id`. The per-call
    injected validator attests one immutable content-addressed revision with no
    process-global or mutable registry (`_check_validator:405`), and receives a deep
    copy so it cannot rewrite the hashed profile. `capability_id` is absent from
    `tinyassets/paid_market/` entirely, so the retired supply-identity field is not
    reintroduced; the surviving repo-wide uses are the unrelated pre-existing
    `host_pool` bid-matching field and the `public.capabilities` Postgres column,
    neither of which this lane touches. Payload, credential, price, routing,
    reservation, and execution fields
    have no home in either envelope; private demand equality stays on the existing
    tenant-keyed HMAC commitments in `tinyassets/paid_market/routing.py:209-223`.
    Quote-bound scope provenance lands at schema v2 in
    `tinyassets/paid_market/quotes.py:186-206` with the trusted projector in
    `tinyassets/paid_market/scope.py`.
  - Strictness correction (2026-07-25): demand enters this pure API as a typed
    mapping, not raw bytes. `_normalized_demand` constructs one bounded ASCII form
    before compatibility or market identity: NFKC/case/edge-whitespace variants
    normalize, semantic sets sort and de-duplicate, member order is irrelevant,
    and malformed shapes/types/identifiers fail closed. Both matching and
    `project_market_class` consume only that normalized form.
- [x] 2.3 Add failing tests for indicative versus native firm authority; versioned domain-separated canonical bytes; unknown-field refusal; server-recomputed totals; complete signed-field coverage; enrolled/revoked issuer keys; tenant/demand/descriptor/terms/fee/nonce/expiry/offer binding; and conserved single/partial capacity consumption.
- [x] 2.4 Implement pure deterministic quote validation and landed monetary normalization for inference, training, task, and fabrication with one settlement currency, exact canonical fee version, priced-component coverage, explicit service attributes/objective weights, optional separately approved FX binding, and exact integer/rational arithmetic.
- [x] 2.5 Add mutation/property tests proving nominal unit price, stale fields, unsupported facets, or a changed descriptor cannot alter eligibility or silently substitute supply.
  - Evidence (2026-07-25; `tests/test_paid_market_manipulation_mutation.py` 97 passed).
    Fourteen mutation probes each force one control open and assert the guard goes red:
    index eligibility / self-trade / linked-party / unknown-linkage
    (`_index_eligible`, `:250`), per-principal influence cap (`_capped_scales`,
    `:296`), settlement-derived unit price (`_require_settlement_derived_price`,
    `:331`), settlement-identity dampening (`_settlement_identity_scale`, `:408`),
    settlement uniqueness (`_require_unique_settlements`, `:429`), quote binding
    (`_require_quote_binding`, `:477`), canonical fee positivity
    (`_require_canonical_fee`, `:536`), canonical fee schedule
    (`_fee_matches_schedule`, `:559`), raw native-truth isolation (`_raw_vwap_field`,
    `:644`), composite ceiling clamp (`_composite_field`, `:660`), and the
    substitutability gate (`descriptors._compare`, `:736`), and joint cross-partition
    settlement weights (`_joint_capped_weights`, finding-B probe). Properties: no nominal
    price clears any non-price rejection across 7 prices x 7 defects; a changed
    descriptor is a different supply identity and is never silently substituted;
    stale fields never become executable and a fresh field never refreshes a stale
    one. Fee-on-every-settlement holds identically for self-trade, linked-party, and
    arm's-length — no self/linked-party fee exemption is encoded anywhere.
  - Corrected 2026-07-25 (Codex money review, two rounds): the earlier six probes
    proved a *weight* cap only, and `unit_price_micros` was an unbounded caller
    value — a positive fixed weight times an unbounded price is still unbounded.
    Round 1 bound `unit_price * quantity == gross_micros`; the round-2 re-review
    refuted that as insufficient, because bounding the *product* does not bound the
    *price* while `quantity` is still caller-supplied. Both factors are now
    settlement evidence (`gross_micros` and `delivered_quantity`), the parties moved
    into `SettlementBinding`, and the join re-reads every quote identity field out
    of the signed `canonical_bytes` rather than trusting the public
    `ValidatedQuote` dataclass. Beyond the monkeypatch probes, a source-mutant run
    removed each new control from `price_surface.py` directly: all 21 new-control
    tests went red and no other test did.
  - **CLOSED — cross-partition cap composition.** `_raw_vwap_field` now sends the
    pair, buyer-root, seller-root, requester, and host-owner partitions to one exact
    rational joint solver. Every group is constrained against the same final total;
    the solver maximizes retained settled volume without exceeding the original
    observation quantity. Mutually inconsistent partition constraints have no
    positive solution and fail closed with `joint_influence_cap_infeasible`.
    The pinned two-partition counterexample is now a green refusal test, extended
    across 2/3/5 partitions, with a mutation probe that forces the joint solver open
    and proves split volume would otherwise be admitted.

## 3. Price surfaces and reference adapters

- [x] 3.1 Add failing tests for per-descriptor raw-VWAP/native-ask/external-ceiling/composite-index fields, independent timestamps/TTLs/sample counts/owner counts, null versus zero, valid all-in ceiling clamp-and-flag, incomplete/stale never-clamp behavior, confidence flags, and fail-closed paid-observation joins across tenant/universe identity, fence-bound accepted-result identity, parties, currency/token/chain, gross/net/fee amounts, and the finality/reorg status defined by `docs/design-notes/2026-04-18-full-platform-architecture.md` §18.6.
  - Repaired 2026-07-25 (Codex money review reopened this task rather than letting
    2.1 defer its gap into an already-checked box). The join verified equality among
    three copies of `SettlementBinding` and then accepted every remaining price-index
    input from its caller. `join_paid_observation` now derives identity and scope
    from the settlement's `ValidatedQuote`, takes parties from the binding, and
    admits a declared unit price only when it exactly reconstructs the settled gross
    (`tinyassets/paid_market/price_surface.py`, `_require_quote_binding` /
    `_require_settlement_derived_price` / `_index_eligible`).
    `PaidObservation` retains `descriptor_id` and `quote_id`, and
    `PriceSurface.observation_descriptor_ids` keeps each source's exact descriptor id
    as aggregate evidence — the scenario the earlier shape could not satisfy.
  - Deliberate fail-closed trade-off, recorded rather than silent: a settlement whose
    `gross_micros` does not divide exactly by the delivered `quantity` raises
    `unit_price_not_settlement_derived` instead of rounding into the index. Integer
    micros only; no float and no floor-drift in the money path.
- [x] 3.2 Implement the pure field-fresh aggregation oracle and differential-test it against canonical paid-market settlement/index primitives.
- [x] 3.3 Define the read-only credential-blind reference-adapter boundary and add contract tests proving it cannot execute, reserve, claim, settle, access secrets, or return an executable route.
- [x] 3.4 Add at least two fake external reference adapters and fault tests for timeout, malformed units/currency, incompatible terms, omitted tax/egress/region/minimum/discount components, partial staleness, independent failure, and partial-reference labeling; use no live credential, quota, or paid API.
- [x] 3.5 Add economic-principal-root manipulation tests for split offers, counterparties, workforce/OAuth/seller accounts, reversed pair direction, unknown linkage, exact same-owner ingestion exclusion without a settlement-fee waiver, recorded canonical fees for every positive-gross settlement including linked-party trades, concentration caps, raw-native-price immutability, composite-only ceiling clamp behavior, and low-confidence thin markets.

## 4. Deterministic economic routing

- [x] 4.1 Add failing tests proving the requester chooses free, BYOC, or paid fulfillment and that unavailable free/BYOC work never creates a paid lock or maintainer/provider call.
- [x] 4.2 Implement pure verified-eligibility/hard-constraint filtering and versioned landed-monetary ranking with stable tie-breaks, explicit service-attribute handling, single-currency/FX rules, complete rejection reasons, cap/fee-version enforcement, and no capacity or money reservation side effect.
- [x] 4.3 Add tenant-private reproducible evaluation receipts with opaque tenant-keyed commitments, owner/admin/auditor ACLs, non-enumerable ids, candidate versions/freshness, reason codes, monetary/service breakdown, retention/hold/deletion/export policy, and aggregate-only public projection.
  - Evidence (verified 2026-07-25; #1737, `b57a7836`): `tinyassets/paid_market/routing.py:85-126,188-325` defines immutable retention/receipt/replay/public-projection shapes, tenant-keyed HMAC receipt IDs and commitments, candidate evidence, owner/admin/auditor ACLs, retention/deletion/legal-hold enforcement, replay, and aggregate-only projection. `tests/test_paid_market_routing.py:295-342,345-380,383-413` proves non-enumerability and ACL isolation, replay/public privacy, and fail-closed retention/legal hold.
- [x] 4.4 Mutation-test that quote ranking cannot authorize provider credentials, alter provider fallback chains, skip domain-native execution fences, or treat ranking as reservation/acceptance/invoice/settlement.

## 5. Native firm capacity and forwards

- [ ] 5.1 After prerequisites land, add failing integration tests for signed short-lived native firm quotes, domain-created tenant/demand/quote/descriptor/quantity/expiry/fence capacity grants, one atomic consumption winner, partial-consumption conservation, offer-version CAS, expiry/cancellation, and no double-sell under response loss.
  - Residual gate (2026-07-25): the transport workflow files `market_workflow`, `market_realtime`, `market_delivery`, and `013_paid_market_workflow.sql` remain in flight on branch `codex/osx-market-workflow`; this integration-test task waits on that lane.
- [ ] 5.2 Implement native firm-quote publication and the default-off selection handoff only: the domain owner creates/fences capacity, `paid-market-economy` records logical budget reservation/accounting intent, and the successor defined by `docs/design-notes/2026-04-18-full-platform-architecture.md` §18.6 owns wallet/chain effects; discovery evaluates and revalidates but creates none of those authorities. Require that successor's matching verified receipt before a paid settlement becomes a price observation. Add no external-provider execution.
  - Residual gate (2026-07-25): implementation waits on the in-flight transport workflow files `market_workflow`, `market_realtime`, `market_delivery`, and `013_paid_market_workflow.sql` on branch `codex/osx-market-workflow`.
- [x] 5.3 Add failing forward tests for exact 8-hour/day/week buckets, ≤28-day horizon, 1M/10M/100M sizes, batch-only initial class, immutable id, authenticated monotone lifecycle, collateral-before-executable, spot collateral-free, deterministic best ask, exact pro-rata demand-relative settlement, threshold-only slashing, buyer compensation, and no-show behavior.
- [x] 5.4 Implement only physically delivered native spot/forward instruments; reject cash settlement, secondary transfer, leverage/netting, proprietary-model resale, and F3 swarm execution.
- [ ] 5.5 Before any forward, training, or hardware route is advertised or enabled in a jurisdiction, obtain and bind a current specialist legal-review artifact covering the CFTC facts-and-circumstances forward-contract-exclusion test, applicable commodities/derivatives/securities/consumer/money-transmission rules, and the narrower knowledge-based BIS/export-control triggers; add `tests/test_paid_market_jurisdiction_gates.py` proving missing/stale review keeps the route dark and no automated label is presented as legal approval.
  - Code half landed (verified 2026-07-25; #1737, `b57a7836`): `tinyassets/paid_market/instruments.py:41-65,126-174` defines `LegalReview` and a fail-closed `jurisdiction_gate`; `tests/test_paid_market_jurisdiction_gates.py:31-98` covers missing, stale, mismatched, incomplete, and automated review while denying any legal-approval label.
  - Host half remains: obtain and bind the current specialist legal-review artifact with the required jurisdiction-specific forward, commodities/derivatives/securities/consumer/money-transmission, and BIS/export-control analysis. The parent task stays unchecked until that artifact exists.

## 6. Public, security, concurrency, and uptime proof

- [ ] 6.1 Add unauthenticated CDN-cacheable aggregate quote/model/curve-equivalent reads with 60-second TTL, explicit limits, and primary-text units, currency, landed total, component coverage, executability, freshness, source class, confidence, and caveats; revalidate tenant/issuer/eligibility/fee/offer/capacity authority outside the cache before handoff.
- [ ] 6.2 Run authorization/privacy tests proving composite tenant keys on private quote/receipt/cache/capacity/idempotency/settlement handoffs; cross-tenant non-enumerability; revocation-invalidated caches; provider contract/subprocessor/locality/retention/attestation eligibility; no credential/payload leakage; no ambient maintainer authority; and no executable route without BYOC or an accepted market lease.
- [ ] 6.3 Run capability-sharded load tests for at least 200 concurrent ranking refreshes, 500 daemon offers, 1,000 requests over five minutes, adapter failures, hot-class reads, tenant fairness, and bounded no-host behavior with no poll-all loop.
- [ ] 6.4 Record environment, exact commands, p50/p95/p99, cache hit/staleness, CPU/pool occupancy, failure counts, duplicate locks, and starvation/leakage results; obtain independent security/concurrency/diff review.
- [ ] 6.5 Before advertisement, pass live canaries and a real rendered chatbot quote conversation; after rollout, record freshness-stamped organic clean-user evidence or leave a STATUS watch item.

## 7. Foldback

- [x] 7.1 Keep external execution/BYOK resale, proprietary instruments, F3 swarm, cash/secondary instruments, and automatic paid fallback explicitly dark in code, docs, and deployment configuration.
- [ ] 7.2 Sync the implemented requirements into canonical `paid-market-price-index-and-forwards`, validate idempotently, archive the change, and retire its STATUS row in the landing commit.
