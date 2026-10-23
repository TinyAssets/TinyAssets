LANE: build N1, the one inbound door. Worktree C:\Users\Jonathan\Projects\wf-n1-impl, branch feat/n1-inbound-door (main
eb582d82d08 plus the approved spec branch spec/n1-inbound-door, PR #5228). Implement the spec in
openspec/changes/<the N1 change>/ exactly; its tasks.md is your checklist. Scope for this PR: the one route, the
shared verifier and installation authority, generic per-binding webhooks, and the Slack shared app (fixed extraction
after HMAC, tap-to-install OAuth, per-installation breaker, no-op rows, proof-header stripping, wake reconciliation).
WhatsApp and Teams stay behind their fixture gates: build nothing channel-specific for them. Email is out unless its
gate is satisfied by a fixture you can write offline. Slack's live app registration and secrets are founder/host work:
leave a docs/host-actions.md row and read the platform signing secret from broker custody by name.
Write the tests the spec names first. Open a draft PR to main. Do not deploy.
