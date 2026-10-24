Fix round on the N4/N8 specs, PR #5239 (worktree C:\Users\Jonathan\Projects\wf-n4-n8-spec). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-n4n8.md
Resolve every finding by deciding, not listing options:
1 HIGH: decide the mechanism that keeps the agent (which runs in the same box with bash) out of the owner's view input
  and private frames: e.g. the view's input path and frame stream live in a separate process/user inside the box the
  agent cannot reach, and private input never enters agent observations; name the test.
2 HIGH: there must be one live view. Extend the existing "protected live owner view" in
  openspec/specs/browser-login-custody/spec.md into N4 (list it as a modified capability) instead of adding a second.
3: N8 decides who holds the pairing credential (broker custody), how the companion's outbound link reaches the box
  (through the router/broker, owner-bound), and how it is revoked.
4: fold or supersede the open companion-local-mcp change so one change owns the desktop companion (S21 one queue).
5: decide the fate of the existing tray host (tinyassets/desktop/tray.py, desktop-app/): becomes the N8 connector,
  or is deleted.
6: add tasks that build the box-view guard and update the N4/N8 registry rows; fix the created dates.
Push to the same branch. Keep it short.
