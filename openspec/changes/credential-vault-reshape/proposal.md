# Credential vault: research and reshape

**Founder, 2026-10-11:** "for the vault encryption, yeah im not sure the correct
thing we should be doing with user credentials. this likely needs a research and
reshape lane for the vault module to be more like how Grok bot or Meta Muse does
it." And: "build towards the correct shape now ... do not patch a known bad
shape."

## Why

Owners' credentials are stored in `.credential-vault.json` in each command
center. They are cleartext or base64, and file mode is their only protection.
The nightly full-tier backup tars them offsite as they are. Browser sessions use
a second, separate scheme: raw per-owner keys that are never backed up, so a
restore signs everyone out.

PR #4611 (now closed) added one master key, kept in the daemon's environment,
with no escrow. That patched the known-bad shape instead of building the S6
shape that target-architecture already chose: the broker as sole key holder,
per-command-center data keys and account KEKs, and a root that survives the
host.

The research in `design.md` compares Meta Muse, Grok, ChatGPT, Claude, Manus,
Comet, Gemini and the integration platforms against OWASP and NIST. It
confirms the half TinyAssets already has right:
- credentials are applied at egress and never shown to the agent;
- takeover is used for sign-in;
- no passwords are stored.

It also names the half it lacks: a key hierarchy, a durable root key, and
encrypted backups.

## What changes

- **What is stored.** The vault holds only delegations an issuer can revoke:
  browser sessions, OAuth tokens, pasted keys and subscription bundles. It
  never holds a website password, TOTP seed, recovery code or passkey.
- **One key hierarchy, broker-only.** Root key, then account KEK, then
  command-center DEK, then AES-256-GCM records bound by AAD to account, center,
  record and generation. Browser custody joins the same hierarchy. The daemon,
  the loop and the boxes never hold keys or plaintext.
- **The root key is an AWS KMS key behind a vendor-neutral seam** (founder,
  2026-10-11).
  - No app-held master key and no offline copy; KMS durability replaces
    escrow.
  - The droplet gets one least-privilege IAM credential, broker-only; the DR
    drill uses a keyless GitHub OIDC role.
  - If AWS is unreachable, uncached connections fail closed with a retryable
    reason and an alarm. There is no plaintext fallback.
  - The DR drill must open a restored credential and a restored session.
- **Faults and lifecycle.**
  - An unopenable record means reconnect for that one connection; it never
    takes down the whole vault.
  - Rotation rewraps layers.
  - Disconnect revokes at the issuer when it can (RFC 7009).
  - Erasure crypto-shreds the account KEK.
- **One cutover migration.** The legacy reader, the vault-group path and the
  raw browser key volume are deleted in the same change.
- **Existing backups are rewritten at cutover**, canary first, so no readable
  credential remains (founder, 2026-10-11).
- **PR #4611 is closed.** Its envelope format, migration check and tests are
  reused.

## Capabilities

- `credential-vault`: ADDED requirements and REMOVED the as-built "filesystem
  permissions only" requirement.
- `browser-login-custody`: MODIFIED "Separate browser encryption keys from data
  backups".

## Impact

- **Code:**
  - `tinyassets/credential_vault.py`
  - `tinyassets/broker/` (new key service; `browser_vault.py`, `server.py`,
    `account_erasure.py`)
  - `tinyassets/connection_oauth/tokens.py`
  - `tinyassets/credential_refresh.py`
  - the deposit paths in `tinyassets/api/` and `tinyassets/onboarding/`
- **Deploy:**
  - `deploy/backup.sh`
  - `deploy/compose.yml` (the browser key volume is retired)
  - `dr-drill.yml`
  - broker-only install of the KMS credential in the deploy workflow
  - the founder's AWS checklist in `docs/host-actions.md`
- **Authority:** floor-class (credential custody). One cross-family review
  round at implementation time.
- **Founder decisions:** all made on 2026-10-11 (`design.md` section 6).
- **Cost:** AWS KMS, about $1–3 a month.
