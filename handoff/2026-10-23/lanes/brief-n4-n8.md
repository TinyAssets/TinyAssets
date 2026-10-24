SPEC LANE: N4 (live takeover view of the box's browser and desktop, for passwords, 2FA and CAPTCHAs) and N8 (optional
link to the user's personal computer, Direction 8: every hosted surface works without it). Worktree
C:\Users\Jonathan\Projects\wf-n4-n8-spec, branch spec/n4-n8-view-and-pc-link (from main eb582d82d08). Spec only: one
openspec change per primitive (proposal, design, spec delta, tasks); no product code. Registry rows N4 ("a view of the
owner's box exposed through the interface package"; guard box-view: the view reads only its owner's box) and N8 (an
optional external box connector the owner adds; host-paths stays green) in docs/architecture.md; ADR-022 P4 lists
BoxProvider.view on the owner door only. Place every piece by the target shape (T1-T10): the router only authenticates
and forwards the view stream to the owner's own box; no shared component reads the stream; the user's credentials
typed into the view never leave the box; the PC link is an owner-added connector, not a platform host path. Keep it
short: decisions, not essays. Open a PR.
