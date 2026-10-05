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
