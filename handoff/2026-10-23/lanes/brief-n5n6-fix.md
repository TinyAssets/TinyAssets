Fix round on the N5/N6 specs, PR #5229 (worktree C:\Users\Jonathan\Projects\wf-n5n6-spec, branch
spec/n5-n6-egress-undo). Review findings (DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-n5n6.md

FOUNDER DECISIONS (2026-10-23), record them in each design with the date:
- N6: undo points are the automatic sleep snapshots plus owner-requested checkpoints ("save a restore point"). No
  per-turn or per-session checkpoints, no pause between messages. Show it fits the C10 storage budget and the 5 GiB
  free tier with the existing seven-snapshot retention.
- N5: owners can still restrict their box (allowlist or ask-before-internet); open stays the default, consistent with
  the 2026-10-20 open-public-web decision. Destinations the owner connected (a destination with a bound credential:
  their model sources and connectors) always satisfy allowlist and ask, without provider-specific code. Approving an
  ask writes a standing allowlist entry through the owner's protected policy write (one writer, no new one-use grant
  store).

Also resolve: N6 restore authority (name the exact path an owner-confirmed restore takes to the broker, through the
N10 protected owner-session route, so an agent in the box cannot forge it); undo states plainly what happens to the
current state (publish the pre-restore sleep snapshot first so undo is itself undoable, or say it is discarded and put
that in the confirmation text; prefer the first); interactive turns waiting across a restore; no turn-checkpoint pin
in shared lifecycle code. Cite the decision record for N5 surviving C7.
Push to the same branch. Keep it short.
