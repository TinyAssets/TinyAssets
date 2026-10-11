# Design: credential vault reshape

Researched 2026-10-11 (web search and fetch). `[OLD]` marks a source dated
before 2026-04-11. "Undated" means the page shows no date and was read on
2026-10-11. "Secondary" means the claim comes from reporting about the vendor's
own page, not the page itself. Inference is labelled as such.

## 1. What we have today (verified in the repo on 2026-10-11)

- **The vault.** `tinyassets/credential_vault.py` writes `.credential-vault.json`
  into each command center. The secrets are cleartext or base64. The only
  protection is file mode: `0640`, owned by the vault group, so the daemon
  writes and the broker reads (spec `credential-vault`, "As-Built Storage
  Protection Is Filesystem Permissions Only"). OAuth bundles (`access_token`,
  `refresh_token`, `token_url`, `client_id`) sit in the same file under
  `vault://http/<name>` (`tinyassets/connection_oauth/tokens.py`).
- **Browser custody is a second key scheme.** `tinyassets/broker/browser_vault.py`
  seals each owner's browser state with AES-GCM under a raw 32-byte key per
  owner. The keys are stored in `/var/lib/ta-broker/browser-vault` on a volume
  that is never backed up, so losing that volume means everyone signs in again
  (spec `browser-login-custody`).
- **Backups carry the credentials in the clear.** `deploy/backup.sh` tars the
  whole data volume into the full tier and excludes only the browser keys. Every
  `.credential-vault.json` therefore reaches `BACKUP_DEST` (DigitalOcean Spaces
  in sfo3, per concern `2026-10-02-backups-share-the-droplets-region.md`) in
  recoverable form, and stays there for the retention window: 7 daily, 4 weekly
  and 6 monthly archives. This is the largest real exposure today, larger than
  the file on the live disk.
- **Inbound keys are already right.** `/api/v1` keys (PR #4602) and OAuth
  tokens the platform issues are stored as SHA-256 hashes and are never
  recoverable. This change does not touch them.
- **The plan already exists.** target-architecture D12 and slice S6 call for a
  broker that is the sole key holder, per-command-center DEKs wrapped by account
  KEKs, escrow, and migration of the plaintext vault. The 2026-10-02 escrow note
  (`docs/design-notes/2026-10-02-vault-key-escrow.md`) lists the "broker vault
  key / account KEKs" as unbuilt escrow items. This change is S6's credential
  half, specified.

## 2. How the agent products do it

**Meta Muse.** Sources: Meta's engineering post
<https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse>
(2026-09-08), and `docs/design-notes/2026-10-04-muse-connection-methods.md`.
- Each user gets a dedicated VM. A host-side service, `hatch-authd`, holds the
  OAuth tokens. The agent cell only ever sees **surrogate tokens**.
- **Sentinel** authorizes each concrete request, then swaps in the real
  credential at egress. Each worker has a credential allowlist.
- Meta says the tokens are stored "in your VM, not in centralized Meta
  infrastructure".
- **Passwords:** the user types them into a native capture UI that sends them
  straight to authd. They are injected into the page when needed, and the agent
  is paused while that happens. 1Password support is "coming".
- **Encryption:** no KMS or HSM details. A "Muse Confidential VM" encrypted
  with a user-held key is "coming soon" or in limited testing. Until then Meta
  can read the VM.
- **Revocation:** Settings > Connectors > disconnect. Approvals are scoped:
  one-time, session, task, time-bounded or perpetual.

**xAI Grok.**
- Connectors (Gmail, Calendar, Drive) use OAuth "connect once"
  (<https://docs.x.ai/grok/connectors>, undated). Token storage is not
  published.
- Grok Bot (beta, about 2026-08). Secondary source:
  <https://openclawdatabase.com/grok-bot/security/>, updated 2026-09-19.
  - One persistent cloud VM per account. Browser logins persist on its disk,
    and **every Bot on the account shares them**.
  - Passwords, 2FA and CAPTCHA go through user takeover.
  - Connector tokens are held server-side, not on the VM. Backups are
    encrypted daily.
  - Inference: the account-wide shared session is the anti-pattern. One
    injected Bot can ride every login.

**OpenAI (ChatGPT agent, cloud browser, Atlas).**
- The agent pauses and the user **takes over** to sign in. Cookies persist
  across sessions "like a regular browser". Users can sign out per site or clear
  all browser data (<https://help.openai.com/en/articles/11752874>, undated,
  read via snippet).
- Cloud-browser sign-in (about 2026-08): Settings > Cloud browser > Browser
  data, with per-site permissions, and the model never sees typed credentials
  (secondary:
  <https://www.notebookcheck.net/ChatGPT-can-now-sign-in-to-your-accounts-and-stay-signed-in.1379126.0.html>).
- Atlas agent mode cannot use saved passwords or autofill
  (<https://simonwillison.net/2025/Oct/21/introducing-chatgpt-atlas/>, `[OLD]`).
- No public statement on how connector tokens are stored.

**Anthropic Claude.**
- Remote connector OAuth tokens sit in "Anthropic's token vault, encrypted at
  rest". Human access is restricted and audit-logged
  (<https://claude.com/docs/connectors/policies>, undated).
- Claude Desktop keeps a managed remote server's token encrypted on the device
  (<https://claude.com/docs/third-party/claude-desktop/mcp-sign-in>, undated).
- Disconnecting deletes Anthropic's copy but does **not** revoke at the
  provider (support article 11596036, dated 2026-08-06, seen via snippet).
- Claude in Chrome runs in the user's own browser and sessions. Anthropic warns
  that injection could "read your credentials or take actions within your
  logged-in sessions"
  (<https://support.claude.com/en/articles/12902428-use-claude-in-chrome-safely>,
  undated).
- 1Password for Claude (beta, 2026-07): every fill needs Touch ID and is scoped
  to the task. Passwords and TOTP never reach Claude or Anthropic
  (<https://www.helpnetsecurity.com/2026/07/17/1password-anthropic-claude-integration/>).

**Manus.**
- With consent, Manus saves encrypted cookies and local storage on its servers
  and loads them into each new sandbox. They last until the site expires them.
- "Manus doesn't store your passwords." The user takes over for login, CAPTCHA
  and MFA.
- Settings > Cloud Browser > Logged-in Accounts can clear one session or all of
  them.
- Sources: <https://manus.im/help/cloud-browser> (undated, about 2025-11,
  `[OLD]`) and <https://help.manus.im/en/articles/11711226> (2025-12-17,
  `[OLD]`).

**Perplexity Comet.**
- Passwords and cards stay in a local OS vault, not on Perplexity's servers
  (secondary: <https://www.gend.co/blog/perplexity-comet-security-day-one>).
- Two incidents rode authorized sessions rather than stealing secrets:
  - Brave's prompt-injection exfiltration of an email and an OTP
    (<https://brave.com/blog/comet-prompt-injection/>, 2025-08-20, `[OLD]`).
  - LayerX "CometJacking"
    (<https://thehackernews.com/2025/10/cometjacking-one-click-can-turn.html>,
    `[OLD]`).

**Google Gemini in Chrome.**
- With permission it signs in through Google Password Manager. Google says it
  does not share passwords with Gemini.
- The remote browser pauses at sign-in until the user takes over
  (<https://support.google.com/chrome/answer/16821166>, undated).

**Integration platforms**, the closest analogue to a hosted token store:
- **Nango:** AES-256-GCM. On Cloud the data key is wrapped by AWS KMS.
  Self-hosted uses one env key, `NANGO_ENCRYPTION_KEY`, that cannot be changed
  once set. That is the #4611 shape
  (<https://www.nango.dev/docs/guides/platform/security>, undated).
- **Composio:** a per-project key wrapped by KMS. It still lost about 5k API
  keys and 5k GitHub tokens in May 2026, through an employee's OAuth token
  (<https://composio.dev/blog/composio-may-2026-security-incident>, 2026-05-21).
- **Pipedream:** a KMS key rotated yearly. Deleting a grant does not revoke it
  at the provider (<https://pipedream.com/docs/privacy-and-security/>,
  undated).
- **Salesloft Drift (2025-08, `[OLD]`):** stolen refresh tokens exposed more
  than 700 Salesforce orgs
  (<https://thehackernews.com/2025/08/salesloft-oauth-breach-via-drift-ai.html>).
  It is the reference case for one central token store.

**What the industry pattern is** (inference from the above):
1. **No product gives passwords to the model.**
   - Most have the user type them during takeover: OpenAI, Manus, Grok Bot, and
     Gemini in the remote browser.
   - Others fill them out of the model's sight: Meta authd, 1Password, Google
     Password Manager.
   - Only Meta keeps a captured password.
2. **The session cookie is what actually holds access.**
   - Cloud browsers persist encrypted cookies server-side until the site
     expires them, with per-site and clear-all controls.
   - None of them publishes its key management.
3. **OAuth tokens sit in the vendor's cloud**, encrypted at rest.
   - No vendor publishes per-user keys.
   - Meta is the only one to separate tokens per user (a per-user VM plus
     surrogates).
   - Only Meta claims confidential computing with user-held keys, and that is
     unreleased.
4. **Disconnect usually deletes without revoking.** Anthropic and Pipedream say
   so. Revoking at the provider is a differentiator, not a norm.
5. **Breaches are runtime or operator compromise, or injection that rides live
   grants.** At-rest encryption does not stop any of them (Composio, Drift,
   Comet). Encryption bounds data-only theft, meaning backups and disks. Scoped
   grants and egress gates bound the rest.

TinyAssets already matches the strong half of this pattern:
- the credential-blind broker applies credentials at egress, like Sentinel;
- takeover handles sign-in;
- there is no password storage.

What it lacks is a key hierarchy, escrow, and encrypted backups.

## 3. Best practice

- **OWASP Secrets Management**
  (<https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html>,
  living, undated):
  - use envelope encryption;
  - keep keys apart from data;
  - encrypt backups;
  - back up keys separately (cold storage plus a break-glass copy);
  - rotate by writing with the new key while old keys still read.
- **OWASP Cryptographic Storage**
  (<https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html>,
  undated):
  - AES-256-GCM;
  - a DEK wrapped by a KEK that is stored separately;
  - keep retired keys so old backups still open.
- **RFC 9700, OAuth Security BCP** (<https://www.rfc-editor.org/rfc/rfc9700.html>,
  2025-01, `[OLD]`, still the current BCP):
  - a public client's refresh tokens must be rotated or sender-constrained;
  - the platform's sign-ins are public PKCE clients, so rotation, which is
    already implemented, is required;
  - DPoP (RFC 9449, `[OLD]`) is where the issuer supports it.
- **NIST SP 800-57 Pt 1 Rev 5** (2020, `[OLD]`): a symmetric data key encrypts
  for at most about 2 years.
- **NIST SP 800-38D** (`[OLD]`): with random 96-bit nonces, a GCM key is good
  for at most 2^32 encryptions. Per-command-center DEKs stay far below that.
- **AWS KMS encryption context** (<https://docs.aws.amazon.com/kms/latest/developerguide/encrypt_context.html>,
  undated): bind the tenant id as AAD so a ciphertext under the wrong tenant
  fails. The context is logged in plaintext, so it must hold no secrets.
- **Google Tink** (<https://developers.google.com/tink/key-management-overview>,
  updated 2026-08-11): keysets are wrapped by a KMS KEK with associated data;
  never store a plaintext keyset; rotate every 30 to 90 days.
- **OpenBao** (<https://openbao.org/docs/secrets/transit/>, 2.6.x;
  <https://openbao.org/docs/concepts/seal/>, undated):
  - Transit derives a key per tenant and rewraps by version.
  - If the seal key is lost, the cluster "cannot be recovered, even from
    backups".
  - CVE-2026-55776 affects 2.5.x derived asymmetric keys.
- **Crypto-shredding:** destroy one user's wrapped key so every backup copy
  becomes unreadable. Key backups must not outlive the deletion promise
  (<https://sota.io/blog/chatto-crypto-shredding-gdpr-article-17-right-to-erasure-self-hosted-implementation-2026>,
  2026).
- **Device-bound session cookies (DBSC):**
  - GA for Google accounts on Windows Chrome from 2026-05-28
    (<https://workspaceupdates.googleblog.com/2026/05/prevent-account-takeovers-with-DBSC-now-generally-available-in-the-Chrome-browser-for-Windows.html>);
  - open to sites in Chrome 145 (<https://developer.chrome.com/blog/dbsc-windows-announcement>).
  - Cookies exported from a user's own Chrome stop refreshing elsewhere, so
    signing in inside the owner-custodied browser, which the direction already
    calls for, is the shape that keeps working.
- **Slack** (<https://docs.slack.dev/authentication/best-practices-for-security>,
  undated): delete a departed user's tokens from production **and backups**.

## 4. Decisions

### D1. Store delegations, never site passwords

What the vault may hold:
- remembered browser sessions;
- OAuth tokens;
- pasted keys and developer-app secrets;
- model subscription bundles.

It never holds website passwords, TOTP seeds, recovery codes or passkey private
keys:
- the owner types those in the protected live view;
- or their own password manager fills them there, the way the browser-login
  spec already describes.

Everything stored is then re-obtainable from its issuer. That changes what key
loss means: **losing the vault key is a mass reconnect, not data loss**. It is
still an outage, so escrow is required (D4), but it is not the catastrophe the
#4611 description implies.

Rejected: Muse's captured-password injection. It makes TinyAssets a password
store, the highest-value target, and buys little over takeover plus a remembered
session. Owner-connected password managers (1Password agentic autofill, which
needs a per-fill approval on the owner's device) stay a possible later
connection the owner owns. The secret stays in their manager.

### D2. One hierarchy, broker-only

```text
root key (provider seam: local file | cloud KMS)       escrowed, never in a data backup
  -> account KEK (one per account, wrapped by root)    stored wrapped in the broker ledger
     -> command-center DEK (wrapped by account KEK)    stored wrapped beside the center's records
        -> sealed record: AES-256-GCM, AAD = account | command center | record id | generation
```

- **Only the broker uid unwraps.** The daemon hands deposits to the broker and
  no longer reads or writes credential files. The vault group read path goes
  away.
- **Browser custody moves into the same hierarchy.** Its raw per-owner keys
  become DEK-sealed state. That gives one scheme, and sessions survive a
  restore, which they do not today.
- **Blast radius:**
  - a leaked DEK exposes one command center;
  - a leaked account KEK exposes one account;
  - a leaked root together with the data exposes everyone, which is why the
    root sits in a separate place with separate escrow;
  - a compromised running broker exposes everyone, as Muse authd or Anthropic's
    vault would. That is bounded by keeping the broker small, on its own uid,
    and free of tenant code.
- **This is the D12 shape**, so per-account crypto-shredding and the
  survivor-rewrap order work unchanged.

### D3. Root key provider: a seam, local by default

| Option | Cost (2026) | What it buys | What it costs |
|---|---|---|---|
| **Local root** (broker-only file outside the data volume, escrowed) | $0 | No new vendor or dependency. Works with zero hosts online. | Root bytes exist on the host. Theft of the host disk plus the root volume exposes everyone. |
| **AWS KMS**, one symmetric key | $1/key-month, plus $1/month for each of the first two rotations; $0.03 per 10k requests, 20k free (<https://aws.amazon.com/kms/pricing/>, 2026) | The root is never exportable; every unwrap is audit-logged; decrypt can be cut off in an incident. | DigitalOcean has no KMS (only a key/value Secrets Manager with no published encryption: <https://docs.digitalocean.com/reference/doctl/reference/secrets/>, 2026-10-09), so the droplet needs a long-lived AWS credential. A host compromise can still call unwrap while it holds that credential. A new vendor and spend line; the broker cannot start if AWS cannot be reached. |
| **GCP Cloud KMS** | about $0.06 per software key version per month; $0.03 per 10k operations (<https://cloud.google.com/kms/pricing>, read via snippet) | Same as AWS | Same as AWS |
| **OpenBao Transit** on a separate small VM | $0 licence, plus about $6+/month for a VM and the operations work | Open source and vendor-neutral | Another always-on service, which works against Direction 8, plus Shamir unseal operations |
| HCP Vault, Infisical | about $450+/month (HCP, third-party figure); $20 per identity per month (Infisical) | Managed | Too expensive at this scale |

**Recommendation:**
- Ship the seam with the **local provider as the default now**. It costs
  nothing and is vendor-neutral.
- Usage is tiny. The broker unwraps account KEKs, caches them in memory, and
  calls the root only on start and on a KEK miss. That keeps any KMS bill at
  about $1 to $3 a month.
- Moving to a KMS later rewrites only the account-KEK wraps (D2), so this
  choice does not block the reshape. **This is a founder decision** (section 6).

### D4. Escrow and restore

- The root goes into the escrow already proposed in the 2026-10-02 note: GitHub
  Actions secrets, CI-readable, plus an offline founder copy.
- Data backups carry only sealed records and wrapped keys. That also closes the
  cleartext-in-backups exposure for every backup taken after the cutover.
- The DR drill restores with the escrowed root and **opens one restored
  credential and one browser session**, so missing escrow fails the drill. It
  no longer passes green.
- User-held keys (passkey PRF and the like) are rejected as the default. A user
  who is offline could not run 24/7 automations (Direction 8), and losing the
  key would lose the credentials. The only vendor exploring user-held keys is
  Muse (Confidential VM, unreleased). That remains a possible later per-connection
  opt-in.

### D5. Rotation

- **Root and KEK rotation** rewrap the layer below. This is cheap, and no
  record is re-encrypted.
- **DEK rotation** reseals one command center's records. It runs lazily on
  write and by an explicit sweep.
- Every layer records its key id, and a retired key is destroyed only when
  nothing names it.
- **Upstream rotation** is unchanged: OAuth refresh-token rotation is
  single-flight in `credential_refresh.py`.
- Schedule: rotate the root yearly or on suspicion. DEKs follow NIST's
  roughly 2-year use period.

### D6. Unopenable means reconnect, per credential

A record that will not open fails loudly for that connection only, with a named
`reconnect_required` reason, and raises the existing connect ask (Fact 8). The
daemon and every other connection keep working. This replaces the #4611
behaviour, where a missing key makes every vault read raise.

### D7. Revocation and erasure

- **Disconnect** first calls the issuer's RFC 7009 revocation endpoint when
  discovery advertises one, then deletes the record. When the issuer offers
  none, the owner is told the grant stays valid at the issuer until it expires.
  This goes beyond the industry norm (Anthropic and Pipedream do not revoke).
- **Browser disconnect** deletes the sealed session.
- **Account erasure** destroys the account KEK after the D12 order, so old
  backups of that owner's credentials become unreadable.
- Escrow holds only the root, never account KEKs, so shredding works.
- **Incident kill switch:** destroying the root and rotating escrow forces a
  platform-wide reconnect. That is the honest worst-case lever, because with no
  shared platform apps there is no single client to revoke.

### D8. Migration: one cutover (Direction 5)

The cutover runs in a short maintenance window, after a verified full backup:
1. The broker generates the root, the account KEKs and the DEKs.
2. It seals every legacy record and every browser key's state.
3. It opens each sealed record and compares it with the legacy bytes.
4. Only then does it remove the legacy files.

The same change deletes:
- the legacy JSON reader and writer;
- the vault-group read path;
- the separate browser key volume.

There is no dual-read period and no feature flag.

**Pre-cutover backups still hold cleartext** until retention prunes them, up to
six months for monthly archives. Rewriting retained archives destroys backup
history, so that is a founder decision (section 6).

## 5. PR #4611: replace, salvage parts

#4611 (`cursor/vault-envelope-encryption-fd9b`, draft) seals each vault file
with a random per-write data key, wrapped by `TINYASSETS_VAULT_MASTER_KEY`.

What is wrong with its shape:
1. **The master key lives in the daemon's environment, and the broker asks the
   daemon to open records (`broker/vault_open.py`).** That inverts S6, where
   the broker is the sole key holder. The daemon is the process that takes
   tenant input, and its environment is readable by same-uid engine children
   (concerns `2026-10-02-platform-secrets-in-daemon-env.md` and
   `2026-10-04-engine-mcp-shares-daemon-uid`).
2. **One global key, no account KEK.** Per-account crypto-shredding and the D12
   survivor rewrap cannot work, because a backup's ciphertext is opened by the
   global key forever.
3. **No escrow, and a missing key fails every vault.** The fault is all-or-
   nothing instead of per credential (D6), and the DR drill cannot prove it.
4. **The AAD binds only the directory name**, not the account, the record or
   its generation.
5. **It leaves browser custody on a second scheme** and keeps the daemon on the
   read and write path. It is a patch to the known-bad shape.

Worth salvaging into the S6 implementation:
- the versioned envelope fields;
- compare-before-replace migration with an on-disk byte check;
- its tamper, cross-center, rotation and idempotence tests.

**Action:** close #4611 as superseded by this change. Do **not** set
`TINYASSETS_VAULT_MASTER_KEY` on the host.

## 6. Founder decisions

1. **Root provider.** Local root plus escrow ($0, recommended now), or a cloud
   KMS (AWS about $1 to $3 a month, GCP under $1 a month). A KMS means a new
   vendor and a long-lived cloud credential on the droplet.
2. **Escrow holder for the root.** GitHub Actions secrets plus an offline copy,
   as recommended in the 2026-10-02 note, or a paid secrets manager.
3. **Pre-cutover backups that hold cleartext credentials.** Either rewrite the
   retained archives without vault files (irreversible), or let them age out
   over up to six months while access to the bucket stays restricted.
