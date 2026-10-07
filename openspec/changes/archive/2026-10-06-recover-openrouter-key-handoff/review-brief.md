# Independent shape review request

Review `proposal.md`, `design.md`, delta spec and tasks. No manual ingress
runtime has been written. UI-only branch work is incomplete recovery and not
offered for release. Owner: Codex cloud_runtime_mvp; reviewer: Claude Fable,
dispatched only by lead.

Please return AGREE / DISAGREE_EVIDENCE with a code citation /
DISAGREE_CONCERN, distinguishing basic MVP safety blockers from later hardening.

1. Is exact `{preset_id,key}` on existing authenticated same-origin
   `/mcp/app/model-connect/deposit_key` the smallest safe acquisition boundary,
   reusing `model_bootstrap.complete_bootstrap` and existing unanswered
   `bind_model_access` approval without generic inference or paid expansion?
2. Resolve the concrete inherited account-deletion/vault-write race described
   in design. Is coordinating `delete_account` with existing per-universe
   exclusive provider admission and checking tombstone/owner under the vault
   write lock sufficient and minimally scoped? Name additional mutation fences
   needed after discovery, avoiding deadlocking nested admission locks.
3. Validate direct secure-input mode, clear-before-await, login generation,
   non-replay and user-directed existing `resume` recovery semantics.

Supporting independent Codex inspection is not cross-family approval. Lead
owns the production browser and deploy. Another builder owns durable PKCE
pending state in `hosted_model_auth.py`; the original proposal had no edits to it.

## Fresh final-review supplement after required CI failure

The original literal preset comparison added one provider-specific runtime
string and failed two ratchet tests. Do not reuse prior approval as approval of
the correction. Assess authority equivalence explicitly: existing bundled
acquisition data now sets `manual_key_entry: true` only for the intended preset;
`load_preset(require_manual_key=True)` refuses all absent/false/non-boolean
opt-ins, validates the installed protocol/HTTPS endpoints and compiles/checks
matching owner-filtered bearer discovery before home creation. Requests cannot
supply opt-in metadata or arbitrary endpoints. Future installed presets without
explicit trusted opt-in remain refused. Free-only bootstrap and explicit user
approval are unchanged. No ratchet baseline or quarantine edit is proposed.

The final candidate composes origin/main cc9c0373's deployed durable PKCE state.
This correction now owns a17-line acquisition-loader delta in
`hosted_model_auth.py`; all durable begin/take/exchange functions are preserved.
Review the composition rather than the obsolete pre-durability snapshot.
Trusted metadata changes the existing preset digest; deploy only outside a
live consent ceremony. Windows408passed1skip and Linux226passed0skip include
the ratchet, manual policy, persistence, bootstrap and shared deletion proofs.
Exact command/evidence lives in the committed handoff review document.
