# DESIGN-007: Post-June-15 GPT-5.5-only checker path for Codex-written patch-loop PRs

Status: proposed
Filed: 2026-05-21
Issue: #984
Request kind: project-design

## Context

The live community patch loop currently treats machine-authored code as
merge-eligible only when writer and checker families differ:

- `writer:claude` requires `checker:codex`.
- `writer:codex` requires `checker:claude`.

That is the right default while both hosted families are available. It becomes
too strong for the post-2026-06-15 hosted-loop target if the available
subscription-backed executable path is GPT-5.5 / Codex only. Requiring a
Claude-family checker would turn a capacity preference into a merge blocker and
would push the loop toward external paid API usage.

## Decision

For Codex-written community patch-loop PRs opened on or after 2026-06-15, a
GPT-5.5-only checker path may satisfy merge evidence when no qualified
Claude-family checker is available. This path is explicitly weaker than
cross-family review and must be labelled as such.

Claude-family review remains preferred. When a volunteer or host-provided
Claude-family daemon is available, it should run as advisory or as the stronger
cross-family gate. Its absence must not block the GPT-5.5-only hosted loop from
reaching merge evidence after the cutoff date.

## Required metadata

PRs using this path must make the weaker path visible in labels or equivalent
gate metadata:

- `writer:codex`
- `checker:gpt-5.5`
- `checker:same-family-weaker`
- `checker:claude-advisory-optional`

If repository labels have not been created yet, the PR body or gate-evidence
packet must carry the same four facts verbatim. The old `checker:claude` label
must not be fabricated just to satisfy pre-cutoff tooling.

## Required evidence keys

The checker packet must use canonical evidence keys so humans, policy checks,
and future daemons can distinguish this path from ordinary self-review:

```yaml
checker_path: gpt-5.5-only
writer_family: codex
checker_family: codex
checker_model: gpt-5.5
claude_review: unavailable_optional_advisory
source_read_checklist:
  - request_artifact
  - changed_files
  - relevant_plan_sections
  - relevant_tests
schema_drift_tests:
  - workflow_policy_labels
  - gate_requirement_metadata
fixture_gates:
  - focused_tests
  - policy_check
  - stale_base_check
  - scope_split_check
```

## Merge-safety conditions

A GPT-5.5-only checker pass is merge evidence only when all of these are true:

1. The PR is Codex-written and opened on or after 2026-06-15.
2. The writer run and checker run are separate invocations where the harness can
   provide separation. If the harness cannot separate invocations, the PR must
   state that limitation.
3. The checker reads source and tests directly, not only the writer summary.
4. Focused tests and policy checks named by the PR pass after the checker run.
5. Schema-drift checks cover writer/checker labels and gate requirement
   metadata affected by the change.
6. The PR body names Claude-family review as preferred but optional advisory.
7. Bounty settlement, if any, references the gate ladder's
   `bounty_requirements`; this checker path does not create a payout shortcut.

## Non-goals

- Do not weaken pre-2026-06-15 PRs or retroactively relabel PR #976.
- Do not treat same-family GPT-5.5 checking as equivalent to cross-family
  review.
- Do not add API-key billing, paid Claude execution, bounty settlement, or
  wallet behavior.
- Do not make this a generic same-family exception for Claude-written PRs.

## Follow-up implementation hooks

When the cutoff policy is activated, update the policy surfaces together:

- `.github/workflows/daemon-request-policy.yml`
- `gate-branch-shape.md (archived in git)`

The compiled CI writer path was retired on 2026-06-25 and no longer
participates in this cutoff policy.

The activation patch should add tests that prove pre-cutoff Codex PRs still
require Claude-family checking while post-cutoff GPT-5.5-only PRs require the
weaker-path metadata and stricter evidence keys.
