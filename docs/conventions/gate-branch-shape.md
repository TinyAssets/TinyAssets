# Gate-Branch Shape Convention

**Date:** 2026-04-26
**Author:** navigator
**Status:** Convention proposal. Codifies verdict-shape uniformity for standalone reusable gate branches built by chatbot-authors (Mark's `change_loop_v1` is the first instance).
**Companion task:** #16 (R2 from Task #15 mapping).

---

## 1. Goal

Standalone gate branches (`gate_investigation_v1`, `gate_review_v1`, etc.) need a uniform output shape so:

1. **Conditional-edge routing works generically.** LangGraph's `add_conditional_edges(source, router, path_map)` reads a state field and looks the value up in `path_map`. If every gate emits the same field with values from the same enum, one router function works across all gates.
2. **Multiple chatbots converge.** Mara's chatbot, Devin's chatbot, Priya's chatbot all author gate branches over time. Without convention, each invents its own verdict shape. Convergence on a common shape means downstream consumers (other branches, evaluators, dashboards) only learn ONE shape.
3. **Unifies with the Evaluator type direction.** Per `project_evaluation_layers_unifying_frame` memory: the platform target is one first-class `Evaluator` primitive across fantasy judges + autoresearch metrics + moderation rubrics + real-world outcomes + discovery ranking. Gate branches are evaluators-as-branches. Their output shape MUST be drop-in compatible with the unified type when it lands.

---

## 2. The verdict shape

Every gate branch's terminal node MUST write **two state fields**:

```
verdict:        str    # one of the canonical enum values (§3)
verdict_evidence: dict # structured evidence supporting the verdict (§4)
```

Optionally, gates MAY emit:

```
verdict_score:    float # ∈ [0.0, 1.0], when the gate is rubric-scored
verdict_summary:  str   # human-readable one-liner for chatbot narration
```

The router reads `verdict` (the str field) and looks it up in `path_map`. That's it. Everything else is metadata for the consumer.

### Why a single str field, not a structured object

`add_conditional_edges` path_map keys are `Hashable`; in practice that means `str`. Routing on `verdict.kind` (nested attribute) requires a custom router function that pulls the field, which is fine — but adds a layer chatbots must learn. Single str field is the cheapest contract.

---

## 3. Verdict enum — canonical values

Five canonical verdicts. **Use these exact strings.** Adding a new verdict requires updating this convention + the conditional-edge testing skill (§7).

| Verdict | Meaning | Required fields | Typical next-route |
|---------|---------|----------------|-------------------|
| `"pass"` | Gate accepts the input; downstream proceeds. | none beyond verdict | next phase / END |
| `"send_back"` | Input has fixable issues; route back upstream for revision. | `verdict_evidence.suggestions: list[str]` | upstream node |
| `"reject"` | Input is unfixable; halt this branch. | `verdict_evidence.reason: str` | END (rejected) |
| `"hold"` | Cannot decide yet; need more input or external signal. | `verdict_evidence.waiting_on: str` | hold node / external trigger |
| `"rollback"` | Verdict implies the LAST landed change should be reverted (regression detected). | `verdict_evidence.commit_to_revert: str` AND `verdict_evidence.regression_signal: str` | rollback node |

### Naming notes

- `pass` not `accept`: matches engine `gates/actions.py` enum (`pass`/`fail`/`skip`); reduces vocabulary fan-out across layers.
- `send_back` not `revise`: explicit about routing direction (upstream); domain-agnostic (works for code review, scientific peer review, fiction commit, etc.).
- `reject` not `fail`: gate's job is to make a decision, not to fail; "fail" reads as "the gate itself crashed."
- `hold` is new — Mark surfaced this need. Gate evaluator returning `hold` lets the loop park work without consuming a slot or escalating to user.
- `rollback` is new and surgical-rollback-aware. When the rollback primitive ships (Task #57 design, R1 from #15), gates can emit this directly.

### Mapping from existing fantasy_daemon vocabulary

| fantasy_daemon `verdict` | Convention `verdict` |
|--------------------------|---------------------|
| `accept` | `pass` |
| `revert` | `send_back` (when in-loop) or `rollback` (when post-landing) |
| `revise` | `send_back` |

The fantasy_daemon's existing names (`accept`, `revert`, `revise`) are domain-internal and remain in the `EditorialVerdict` packet for backward compat. New gate branches authored against this convention use the canonical enum directly.

---

## 4. `verdict_evidence` shape

A dict with optional structured fields. The chatbot consumer reads this to narrate the gate's decision to the user.

**Common keys (all optional unless required by §3):**

```python
{
  "reason": str,              # one-line WHY for the verdict
  "suggestions": [str, ...],  # for send_back: what to fix
  "waiting_on": str,          # for hold: what unblocks
  "commit_to_revert": str,    # for rollback: git sha or branch_version_id
  "regression_signal": str,   # for rollback: what changed for the worse
  "rubric_scores": {axis: float, ...},  # for rubric-scored gates
  "evidence_refs": [str, ...],  # URLs / file paths / canon page slugs cited
  "evaluator_branch_id": str, # which evaluator branch produced this (for nested evaluators)
}
```

Chatbots SHOULD reproduce `reason` and `suggestions` directly to the user (visuals-first per `project_chatbot_visuals_first`). They SHOULD NOT invent fields not in this list — extension fields go inside `evidence_refs` or via a custom domain-prefixed key (e.g. `fantasy_daemon.editorial_score: 0.71`).

---

## 4a. Gate requirement metadata

Gate designers may attach requirement metadata to each ladder rung. This is
how Branches know whether they are eligible to plug into a gate, and how a
bounty or bonus knows which evidence unlocks settlement.

Requirement metadata lives on the rung object, next to `rung_key`, `label`, and
`description`:

```yaml
gate_ladder:
  - rung_key: pr_ready
    label: PR ready
    branch_requirements:
      required_output_keys:
        - verdict
        - verdict_evidence
      required_state_fields:
        - request_id
      required_tags:
        - community-change-loop
      required_labels:
        - daemon-request
        - checker:cross-family
      required_evidence_refs:
        - tests
        - observation_plan
      allowed_writer_families:
        - claude
        - codex
      forbid_same_family_checker: true
    bounty_requirements:
      settlement_gate: pr_ready
      minimum_gate_verdict: pass
      required_evidence_refs:
        - pr_url
        - ci_run_url
        - live_observation_url
      free_claim_allowed: true
```

`branch_requirements` answers: "Can this Branch or PR claim this rung?" It is
about branch shape, labels, and evidence. `bounty_requirements` answers: "What
must be true before a paid or bonus settlement can release?" It references the
same rung and evidence vocabulary so gate review and bounty settlement do not
diverge.

The MCP `gates claim` path should validate this metadata once the current
`tinyassets/api/market.py` sweep clears; until then, request labels and PR policy
checks enforce the cloud-visible subset.

Post-2026-06-15 Codex-written patch-loop PRs may use the weaker GPT-5.5-only
checker path defined in
`pages/plans/design-007-post-june-15-gpt-5-5-only-checker-path-for-codex-written-pat.md`
when no qualified Claude-family checker is available. That path must not be
advertised as `checker:cross-family`; it requires explicit weaker-path metadata,
canonical evidence keys, stricter fixture gates, and Claude-family review marked
as preferred but optional advisory.

---

## 5. Worked example — change_loop_v1's investigation gate

A gate branch named `gate_investigation_v1` whose job is "decide whether the bug report has enough information to dispatch a coding attempt."

**State field contract (the gate branch's `output_keys`):**

```python
output_keys = ["verdict", "verdict_evidence", "verdict_summary"]
```

**Terminal node logic (pseudo):**

```python
def gate_investigation_terminal(state):
    bug = state["bug_report"]
    repro = state["minimal_repro"]
    root_cause = state["root_cause_analysis"]

    if not repro:
        return {
            "verdict": "send_back",
            "verdict_evidence": {
                "reason": "no minimal repro case extracted",
                "suggestions": ["call extract_minimal_repro before this gate"],
            },
            "verdict_summary": "Need minimal repro before proceeding to coding.",
        }

    if root_cause["confidence"] < 0.4:
        return {
            "verdict": "hold",
            "verdict_evidence": {
                "reason": f"root-cause confidence {root_cause['confidence']:.2f} below threshold 0.4",
                "waiting_on": "additional bug context (cosign or reproduction by another reporter)",
            },
            "verdict_summary": "Insufficient signal to dispatch a coding attempt yet.",
        }

    return {
        "verdict": "pass",
        "verdict_evidence": {
            "rubric_scores": {
                "repro_quality": 0.85,
                "root_cause_confidence": root_cause["confidence"],
            },
            "evidence_refs": [bug["wiki_page"], repro["test_file"]],
        },
        "verdict_summary": "Investigation complete; ready for coding dispatch.",
    }
```

**Conditional-edge wiring (in the parent `change_loop_v1` branch):**

```python
conditional_edges = [
    {
        "from": "investigation_gate",
        "conditions": {
            "pass": "coding_dispatch",
            "send_back": "investigation_gate",  # retry after upstream fix
            "hold": "park_for_more_signal",
            "reject": "END",
            "rollback": "rollback_handler",
        },
    },
]
```

The router function is generic: `lambda state: state["verdict"]`. One router works for every gate branch in the loop.

---

## 6. Gate branches as Evaluators — forward compatibility

Per `project_evaluation_layers_unifying_frame`: the platform's target is one first-class `Evaluator` primitive that subsumes gate branches, autoresearch metrics, fantasy judges, moderation rubrics, and discovery ranking.

When the unified `Evaluator` type lands (post-#15-R3 wiring), any branch conformant to this convention should adapt without breaking changes:

- `verdict` → maps to `Evaluator.outcome` (str, enum-constrained).
- `verdict_evidence` → maps to `Evaluator.evidence` (dict).
- `verdict_score` → maps to `Evaluator.score` (float ∈ [0, 1]).
- `verdict_summary` → maps to `Evaluator.summary` (str).

Gate-branch authors who follow this convention today are buying forward-compat insurance. New evaluator-typed nodes should adopt the same field names.

---

## 7. Testing convention — conditional-edge testing skill

BUG-019/021/022 (closed by Tier-1 routing fix `c1d8b8b`) surfaced a class of conditional-edge failures: routers return target-name; LangGraph wants `path_map` key; mismatch yields silent KeyError. The conditional-edge testing skill that closed these bugs validates the router-to-path-map contract.

**Once verdict shape is canonical**, the conditional-edge testing skill MAY be extended with a generic gate-branch validator:

> For every branch that emits `verdict` as an `output_key`, assert the conditional-edge consumer's `path_map` keys ⊆ the canonical verdict enum.

That test is generic; it does not need per-gate authoring. It catches the class "you wrote a gate branch but forgot to handle `hold` in the consumer's path_map" before runtime.

**Out of scope for this doc:** writing the test. Surface as a follow-up dev task once gate branches start landing.

---

## 8. What this convention does NOT do

- **Does not define how the gate makes its decision.** That's per-gate (Mark's `gate_investigation_v1` uses LLM rubric scoring; a different gate could use deterministic checks). Convention is about OUTPUT SHAPE only.
- **Does not require all gates to use all five verdicts.** A gate with no rollback semantics simply never emits `"rollback"`. The path_map handles only the verdicts the gate actually emits.
- **Does not replace `EditorialVerdict` packet** in fantasy_daemon. Existing domain code stays as-is; new code adopts canonical enum.
- **Does not standardize every gate input.** Inputs vary per gate. Requirement
  metadata only declares the subset a claiming Branch or bounty settlement must
  satisfy.
- **Does not fully wire runtime authority yet** (who can author a gate? who can
  promote one? when exactly does `gates claim` reject unmet requirements?).
  That's the next runtime enforcement slice after the `market.py` sweep clears.

---

## 9. Adoption path

1. **This doc is canonical** once linked from `docs/conventions.md`. No code changes required.
2. **Mark's existing `change_loop_v1`** is encouraged but not required to migrate to the canonical enum — his branch is functional with whatever verdict strings he chose. Migration is one `patch_branch` call; pre-rollback enums map cleanly per §3 table.
3. **New gate branches** authored after this doc lands SHOULD conform.
4. **Conditional-edge testing skill extension** (§7) lands as a separate dev task when first non-fantasy_daemon gate ships.

---

## 10. References

- `project_evaluation_layers_unifying_frame` — unified Evaluator type direction (memory).
- `project_chatbot_visuals_first` — chatbot reads verdict_evidence + reproduces visually (memory).
- `tinyassets/gates/actions.py:223` — engine-level `eval_verdict` enum (`pass`/`fail`/`skip`); convention's `pass` matches.
- `tinyassets/branches.py` (conditional_edges spec, ~L100-150 of dataclass) — the consumer contract this convention serves.
- `tinyassets/graph_compiler.py:2026-2033` — LangGraph `add_conditional_edges` integration.
- `domains/fantasy_daemon/phases/commit.py:1148` — `EditorialVerdict` packet (existing domain shape; pre-convention).
- `docs/design-notes/2026-04-26-mark-change-loop-status-mapping.md` (deleted 2026-10-06; in git history) — Task #15 mapping; this convention is R2 of that doc.
- `docs/design-notes/2026-04-25-surgical-rollback-proposal.md` (Task #57) — design that adds `rollback` verdict's downstream meaning.
- BUG-019/021/022 (closed by `c1d8b8b`) — the class of conditional-edge failures that motivate the testing-skill extension in §7.
