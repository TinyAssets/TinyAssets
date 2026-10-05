# Cross-family review and disposition

Claude reviewed the implementation through `.agents/skills/peer-agents/SKILL.md`
with `scripts/peer_agent.py claude`, read-only, on 2026-10-04. One round;
verdict ADAPT. The review found no custody/owner isolation or cost-cap issues.

- **AGREE:** connect acceptance exceptions must not escape after deposit. The
  completion path now returns `model_source_acceptance_failed`, leaves the request
  pending and reports actual serving state. A real legacy assignment test proves it.
- **AGREE:** activation failure after rebind must not silently disable the old
  setup. Recovery rebinds prior membership and enables it with assignment, revision
  and last-update fences. Tests prove restoration and refusal to overwrite a newer
  owner disable. A disable changes `updated_at`, not revision; both binding APIs
  now accept that additional fence for composed server-owned operations.
- **DISAGREE_EVIDENCE (nonblocking status suggestion):** the existing
  `unchanged/already_powered` result describes preservation of the serving root.
  Retained that compatibility contract and its named test. Actual accepted
  membership is asserted directly on the resulting assignment; a failed addition
  now returns an explicit error rather than that success result.

The reviewer ran `tests/test_unify_connection_uses.py` (30 passed before the
recovery regressions were added). No second review round was commissioned.

## Round 2 repair disposition (2026-10-05)

The supplied review `review-g7model.md` approved the prior head, but source CI
job 111621098967 supplied regression evidence in PR comment 5988421056.

- **AGREE:** discarding `matching[1:]` for a provider-only pin incorrectly reduced
  the existing foreground allowance from six to three. Keep same-source eligible
  alternatives for provider-only pins; exact model pins still forbid substitution.
  The original allowance test is unchanged, with added exhaustion coverage for
  provider-only, `model`, and `model_id` pins.
- **AGREE:** legacy acceptance failures contradicted the design's compatibility
  promise. Keep the fail-loudly behavior, correct the design, and tell owners to
  confirm explicit access and ensure exactly one owned serving agent before retry.
  Returning success before membership acceptance would misrepresent authority.

This repair changes candidate selection and error copy, not authority grants or
gate files. It uses the supplied cross-family review and CI evidence; no additional
review round is required by the floor-only review scope.
