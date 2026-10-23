LANE: finish N1, the one inbound door. Worktree C:\Users\Jonathan\Projects\wf-n1-impl, branch feat/n1-inbound-door
(draft PR #5235), now merged with the approved one-durable-send branch conv/c11-durable-send (PR #5237). Your previous
round left these undone (see the PR body): the route still uses the old direct dispatcher; receipt-keyed durable
session.start, route cutover, per-installation breaker, wake reconciliation, migration and the remaining spec proofs.
Durable admission now exists (#5237): use it so an inbound event is acknowledged only after it is durably admitted as a
session.start input keyed by its receipt (no loss, no duplicate session on redelivery). Cut the route over to the one
door and delete the old direct dispatcher in this PR. Implement the breaker, the authenticated no-op rows, wake
reconciliation against the owner-filtered routing snapshot, and the spec's remaining tests, following
openspec/changes/<the N1 change>/tasks.md as the checklist. WhatsApp, Teams and email stay behind their fixture gates.
Mark tasks done only when their test passes. Push to the same branch.
