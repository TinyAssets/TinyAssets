LANE: S28 credential-vault reshape on post-C6 broker custody (tracker item 8.14; Shape registry S28). Worktree
C:\Users\Jonathan\Projects\wf-vault-kms, branch conv/s28-vault-kms (from main eb582d82d08).
Closed PR #4949 sealed credentials under a KMS key hierarchy but was built on the pre-C6 daemon vault
(credential_vault.py, role launchers). Read it (gh pr diff 4949) and openspec/changes/credential-vault-reshape if it
exists. SPEC FIRST (authority and custody): redo it on today's broker custody (broker/vault.py,
broker/subscription_custody.py, the HTTP credential store): every credential the broker holds is sealed under one
broker-only key hierarchy with a KMS root, envelope keys per owner, rotation, and backup/restore that never exposes
plaintext; nothing reaches a box. Write the spec delta and tasks, then implement only what needs no new cloud account
or spending (KMS account setup is a founder/host action: add a docs/host-actions.md row and keep a local root for
tests). Open a draft PR.
