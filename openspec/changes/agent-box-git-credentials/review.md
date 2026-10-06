# L11 cross-family review, 2026-10-05

One Claude review via `scripts/peer_agent.py claude --timeout 900`, exit 0,
verdict **ADAPT**. Review scope: the L11 authority/transport implementation,
not unrelated main and parent merges. The reviewer also ran the synthetic
Linux test file while this lane was correcting its host-side workspace path.

- **AGREE — finish the synthetic proof.** Corrected the workspace path to
  `universe_tools.WORKSPACE_DIR`; the bash clone/push/fetch had already passed.
  Binary comparison and the complete workspace secret scan now execute.
- **AGREE — prove in-flight authority changes.** Added upload and download
  credit-starvation tests for revoke, scope change and generation fencing.
  Upload waits also check the resource snapshot, not just grant existence.
- **AGREE — git setup must not disable local bash.** Missing identity, owner
  admission, broker, proxy or an unambiguous grant now yields no authenticated
  routes and a fixed diagnostic, while local commands still run. The real
  broker catalog setup path and each refusal have regression coverage.
- **AGREE — release the upload condition before credit dispatch.** A credit
  callback waits for the broker event loop, whose DATA handler needs that
  condition. The callback now runs outside it; a concurrent DATA test proves it.
- The temporary exception-location diagnostic observed during review was
  removed. No upstream exception text is returned or logged by the git route.

No second review was requested. The verdict remains ADAPT with the findings
addressed; this is not represented as a later APPROVE. Deployment and a real-user
app pass remain outside this draft's evidence.

## Continuation review (2026-10-05)

One new cross-family round for the continuation, Claude via peer-agents,
read-only, exit 0 after 74 seconds: **VERDICT: ADAPT**. Scope was the consent
fixture and canonical `/cc` provider contract, not a second review of the
previous streaming implementation.

- **AGREE:** unknown_after_restore is not an execution completion. The executor
  now holds with box_tool_outcome_unknown for that receipt or any non-integer
  exit code. Regression cases include an unknown marker accompanied by a
  misleading zero exit code. No new operation id is issued.
- **DISAGREE_EVIDENCE with the proposed skip:** the real LocalBoxProvider test
  requires Linux, and the reviewer observed its expected NotImplementedError
  on Windows (3 other tests passed). The owner explicitly requires the Linux
  oracle and says never skip tests. Keep the real integration test unskipped;
  Windows is not its acceptance venue. This is not a claim of Windows support.
- Reviewer confirmed no change weakens the per-role switch or credential
  boundary and no collision with launcher/gVisor files. No second round.

The verdict remains ADAPT with the correctness finding addressed and the
platform-test recommendation explicitly declined under the owner's constraint.
