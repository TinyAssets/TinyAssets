## Why

Audit L9 found thirteen jargon-heavy seed files before a new owner has taught the agent anything. Founder direction (2026-10-04) calls for three readable files: Soul, Memory and Identity, with no loss of existing user content.

## What Changes

- **BREAKING:** replace the thirteen-file personal baseline with `soul.md`, `MEMORY.md` and `identity.md`; stop requiring empty companion files for first contact or soul parsing.
- Extend the existing D10 `starter-seed-lifecycle` contract with this content profile, using its manifest, receipt, conditional writes and Undo. Do not create another migration mechanism.
- Preserve every legacy companion file and every customized core file; only receipt-proven untouched core templates qualify for automatic replacement.
- Create optional learned documents and soul-edit history when used, and keep the starter and its main agent editable.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `starter-seed-lifecycle`: additive clean personal-file profile and conservative legacy adoption. This capability is proposed in the existing same-named change, not yet in as-built specs; sync that prerequisite first, then this change's ADDED requirements.

## Impact

Planning owner: Codex. Branch: `spec/muse-pi-gap-proposals`, based on `origin/main` at `9e96ff95959499cd2ec6fda9e0761b9d498240dd`. Draft only; no product code, deployment or PR. Future implementation has its own worktree and PR.

Expected consumers: `universe_bundle.py`, `first_contact.py`, `universe_soul.py`, `branches.py`, `api/universe.py`, memory/history and command-center layout classification. D10 owns transaction machinery; `starter-agent-out-of-plumbing` owns renderer cutover; D7 owns the Your agent editor and learning retirement. This change supplies their clean content profile and compatibility acceptance, not duplicate implementations. Do not edit `tinyassets/onboarding/app.html` in this planning lane.
