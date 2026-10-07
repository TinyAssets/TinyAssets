## Why

A live second-user signup landed on the provider workspace with a starter key
instead of finishing TinyAssets authorization. A subsequent explicitly approved
OAuth ceremony returned an authorization error. The existing generic paste box
only deposits a credential; it cannot finish model activation for this user.

## What Changes

- Explain signup/dashboard recovery honestly without promising automatic return.
- Add explicit manual-key acquisition to the existing authenticated app model
  connection route, reusing its installed preset and complete_bootstrap pipeline.
- Reuse the existing eligible-free-model discovery and unanswered model-access
  approval. Never enable a provider merely because a key was pasted.
- Preserve current owner/home, empty-setup, secret-redaction and no-replay rules.
- Coordinate, but do not duplicate, the separately owned OAuth callback repair.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `onboarding-connection-progress`: secure manual acquisition can prepare a
  free-model confirmation without an existing inference provider or OAuth callback.

## Impact

Owner: Codex cloud_runtime_mvp. Branch: codex/openrouter-handoff-clarity.
One PR for the intentional onboarding handoff/recovery correction. The user/lead
explicitly prioritizes this live signup failure over the completed browser-RAM
architecture task; no unrelated feature work is admitted here.

Expected files: tinyassets/onboarding/model_connect.py, app.html, focused route/
controller tests, plugin mirror and brand receipt. No new database, public MCP
handle, credential store, model-access policy, grant type or automatic approval.
The additive app API operation is authority/secret-ingress work and is gated by
independent Fable shape review before runtime implementation. Root owns review,
release and the real user's browser; no user credential enters agent chat/tools.

The current UI-only commit is incomplete recovery, not a shipped solution.

## Research pickup (September 18)

Lead's primary-source report is `docs/audits/2026-09-18-openrouter-onboarding-implications.md`,
currently in the primary documentation worktree at
`C:/Users/Jonathan/.codex/worktrees/0a7f/TinyAssets`. Fold its independent Claude
research review and any required adaptations into this existing lane; do not
open a new implementation lane. OAuth PKCE remains the primary normal path;
manual key entry is recovery only. The report found documented `key_label`,
but no documented signup-skip/forced-continuation control in the sources it
reviewed. This is not proof such a control cannot exist.

New key-label or waiting/new-tab UX derived from that research is held for its
independent research-review supplement. That supplement does not block fixes
for the measured signup handoff, incomplete manual activation or lost pending
authorization; their existing shape and release gates still apply. Do not
invent a public `limit` query parameter, add public analytics, or promise
control over the provider authorization page's app heading. Management-key
and Stripe provisioning are outside this free-only, user-owned browser flow.
