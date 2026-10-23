Review the N3 (realtime two-way voice and video) and N9 (model_call returns media) specs in PR #5240 (worktree
C:\Users\Jonathan\Projects\wf-n3-n9-spec; `git diff origin/main...HEAD`). Read-only.
Yardstick: openspec/changes/owner-daemon-in-box/research.md and T1-T10 in its design.md; AGENTS.md Direction 2 (no
provider-specific code) and 4; registry rows N3, N9, S1 (one model call), S3 (one loop) in docs/architecture.md.
Check: realtime is a streaming mode of the one model call and the one loop, not a second loop; the realtime transport
goes through the broker as the one egress with credentials only in the broker; media bytes are owner data in the box
and never pass through or persist in shared components beyond transit; a receipt per call including streamed and
media calls; spending and caps apply to realtime minutes and media; capabilities come from each source's declared or
discovered contract, with no provider names in code; the existing browser voice path is replaced, not kept beside it;
nothing over-built; no decision left open. Report findings with file:line and severity, then exactly one line:
VERDICT: AGREE or VERDICT: DISAGREE_EVIDENCE. No sub-agents, no peer_agent.py. Budget about 15 minutes.
