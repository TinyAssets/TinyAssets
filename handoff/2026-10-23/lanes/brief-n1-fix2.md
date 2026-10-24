Second fix round on the N1 inbound-door spec, PR #5228 (worktree C:\Users\Jonathan\Projects\wf-n1-spec, branch
spec/n1-inbound-door). Re-review findings:
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-rereview-n1.md

FOUNDER DECISION (2026-10-23), record it in design.md with the date: one shared TinyAssets app per channel (Slack,
Teams, WhatsApp and similar), installed by the owner with a tap through OAuth; owners do not create their own
developer apps. Consequence for the shape: the shared door may read exactly ONE identity field per event (the
workspace/tenant/account id the channel's data profile names, e.g. Slack team_id) and map it to one owner's box through
an installation record written at install time (a positive identity->box mapping, T5, exactly like sign-in). It reads
nothing else in the body: no message text, no routing to agents, no replies; the raw request is forwarded to the box.
The channel's signing secret for the shared app is platform-held in broker custody; verification stays as decided
(pure verifier code; secret lookup broker-side). The per-binding callback option stays for generic webhooks and
channels without a shared app. Describe install, uninstall (mapping removed, box notified), one workspace connected to
two owners (refuse; first owner keeps it unless they disconnect), and the identity-field extraction as data in the
profile (JSONPath-like selector), not provider code.

Also fix the re-review's new findings: key rotation while the box sleeps (connector refreshes keys on a timed wake
within the provider's rotation lead time; unknown key returns a retryable status; add the rotation-while-asleep test
case); state that the router runs the same pure verifier for no-secret profiles and only the secret lookup is in the
broker; list HMAC signature header encodings/prefixes as profile data.
Push to the same branch. Keep it short.
