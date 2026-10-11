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

What it lacks is a key hierarchy, a durable root key, and encrypted backups.

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
loss means: **losing the root key is a mass reconnect, not data loss**. It is
still an outage, which is why the root is a KMS key with AWS's durability and a
deletion waiting period (D3, D4), but it is not the catastrophe the #4611
description implies.

Rejected: Muse's captured-password injection. It makes TinyAssets a password
store, the highest-value target, and buys little over takeover plus a remembered
session. Owner-connected password managers (1Password agentic autofill, which
needs a per-fill approval on the owner's device) stay a possible later
connection the owner owns. The secret stays in their manager.

### D2. One hierarchy, broker-only

```text
root key (AWS KMS key behind the key seam)            never leaves KMS, never in a data backup
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
  - the root never leaves KMS. A leaked broker credential lets an attacker
    unwrap account KEKs only while that credential is active, and every call
    is logged by CloudTrail. Deactivating the access key stops it at once;
  - a compromised running broker exposes everyone, as Muse authd or Anthropic's
    vault would. That is bounded by keeping the broker small, on its own uid,
    and free of tenant code.
- **This is the D12 shape**, so per-account crypto-shredding and the
  survivor-rewrap order work unchanged.

### D3. Root key: AWS KMS behind a vendor-neutral seam (founder, 2026-10-11)

**Decision.** The root is one AWS KMS symmetric key. It lives in the same AWS
account as Device Farm, in `us-west-2`, the region nearest the sfo3 droplet and
the one that checklist already uses. There is **no app-held master key and no
offline copy**. The founder judged an offline copy to be a risk of its own, so
KMS durability replaces escrow for the root. The key material never leaves
KMS.

**The seam.**
- The broker reaches the root only through `RootKeyProvider`, which has three
  operations:
  - `generate(context)`, which creates a new account KEK;
  - `unwrap(blob, context)`;
  - `wrap(kek, context)`, which rewraps under the current key material.
- The AWS adapter is one module behind that interface. No other code names
  AWS. The sealed-record and wrapped-KEK formats carry a provider id and a key
  reference, so a later move to GCP KMS or OpenBao Transit rewrites only the
  account-KEK wraps (`unwrap` with the old provider, `wrap` with the new one).
  No record is resealed.
- No local provider ships in production. Tests use an in-memory fake that is
  refused when `TINYASSETS_ENV=production`, so there is no second default
  (Fact 8).

**Calls.**
- **`GenerateDataKey` (AES_256)** creates an account KEK. The broker keeps the
  `CiphertextBlob` in its ledger.
- **`Decrypt`** unwraps a KEK when the broker's cache misses.
- **`Encrypt`** rewraps a KEK under the current key material after a rotation.
- **Encryption context.** Every call carries
  `{"ta_purpose": "account-kek", "ta_account": "<opaque account digest>"}`.
  AWS binds the context as AAD, so a wrapped KEK replayed under another account
  fails. CloudTrail logs the context in plaintext, so it holds only an opaque
  digest, never an email or a name
  (<https://docs.aws.amazon.com/kms/latest/developerguide/encrypt_context.html>,
  undated).
- **Volume.** The broker caches unwrapped KEKs in memory only, so it calls KMS
  on start, on a cache miss and on rotation. That is thousands of requests a
  month, inside the 20k free tier.

**Cost.** $1 per key-month, plus $1 a month for each of the first two automatic
rotations. Requests cost $0.03 per 10k beyond 20k free a month
(<https://aws.amazon.com/kms/pricing/>, 2026). That comes to about **$1–3 a
month**.

**The droplet's credential.**
- One IAM user, `tinyassets-vault-broker`, with no console access.
- One access key, created as "Application running outside AWS". DigitalOcean
  has no instance identity that AWS trusts, so a long-lived key is the only
  option.
- The policy allows only the three actions, on the single key, and only with
  the vault's encryption context:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "VaultRootKeyAccountKeksOnly",
      "Effect": "Allow",
      "Action": ["kms:GenerateDataKey", "kms:Decrypt", "kms:Encrypt"],
      "Resource": "arn:aws:kms:us-west-2:ACCOUNT_ID:key/KEY_ID",
      "Condition": {
        "StringEquals": {"kms:EncryptionContext:ta_purpose": "account-kek"},
        "Null": {"kms:EncryptionContext:ta_account": "false"}
      }
    }
  ]
}
```

This policy deliberately grants nothing else:
- no `kms:ReEncrypt*`, `CreateGrant`, `DescribeKey`, `ScheduleKeyDeletion`,
  `DisableKey` or `PutKeyPolicy`;
- no other key and no other AWS service.

The key policy stays at the AWS default. The account root principal enables IAM
policies, and the founder's administrator identity alone administers the key.

**Where the credential lives.**
- **Names:**
  - `TINYASSETS_VAULT_KMS_KEY_ARN`
  - `TINYASSETS_VAULT_AWS_ACCESS_KEY_ID`
  - `TINYASSETS_VAULT_AWS_SECRET_ACCESS_KEY`
  - The standard `AWS_*` names are avoided on purpose, so no other SDK in the
    container picks them up.
- **Path to the host:** the founder puts them in the GitHub `production`
  environment, restricted to main. The deploy installs them into one
  broker-only file, mode `0400` and owned by the broker uid, outside the data
  volume (the #4260 set-once precedent).
- **Who can read them:** the names join `DAEMON_FORBIDDEN_ENV` and the
  engine-child strip list. The deploy's `daemon_env_scoped` check already
  rolls back a daemon process that holds a forbidden name.
- **Recovery:** the credential can be regenerated at any time, unlike the key,
  so it needs no escrow.
- **Optional hardening:** an `aws:SourceIp` condition pinned to a reserved IP.
  It is left out by default because a DR standby on another host would need a
  policy edit first.

**Disaster-recovery principal.**
- The DR drill (D4) must open a restored credential from CI, so it gets a
  separate keyless principal: a role that GitHub OIDC assumes.
- **Trust:** `token.actions.githubusercontent.com`, audience `sts.amazonaws.com`,
  subject `repo:TinyAssets/TinyAssets:environment:dr-drill`.
- **Permissions:** only `kms:Decrypt` on the same key, with the same context
  condition.
- No long-lived secret exists for the drill.

**Rotation.**
- **Key material.** Enable automatic yearly rotation on the KMS key. `Decrypt`
  keeps opening blobs made under older material. A sweep rewraps account KEKs
  under the current material (`Decrypt` then `Encrypt`), so old material is
  never needed in practice.
- **Access key**, yearly or on suspicion:
  1. Create a second access key.
  2. Update the GitHub secret and redeploy, which reinstalls the broker file.
  3. Confirm the broker's KMS health probe is green.
  4. Deactivate the old key.
  5. Delete it after 24 hours with no CloudTrail use.
- **KEKs and DEKs:** as in D5.

**Revocation and kill switches**, from mildest to strongest:
1. **Deactivate the access key.** Every new unwrap fails at once. Cached KEKs
   live until the broker restarts, so restart the broker as well. This is
   reversible.
2. **Disable the KMS key.** This stops every principal, the drill included.
   It is reversible.
3. **Schedule key deletion.** Every credential in every copy becomes
   unreadable after the waiting period, which is a forced platform-wide
   reconnect. Leave the waiting period at the 30-day maximum, so a mistaken
   deletion can be cancelled.

### D4. Durability, outage and restore

**Durability replaces escrow.**
- The root never exists outside KMS, so there is no copy to steal or lose.
- What remains is AWS account loss or deletion of the key. Root MFA, the
  administrator-only key policy and the 30-day deletion window guard against
  both.
- If it still happens, the result is D1's mass reconnect. Nothing
  irreplaceable is lost, because every stored credential can be re-issued by
  its issuer.
- The 2026-10-02 escrow note's "broker vault key / account KEKs" row resolves
  to "AWS KMS, no escrow". Its escrow set otherwise stands.

**AWS unreachable, or the credential refused: fail closed, per connection.**
- KEKs already cached keep working, so live connections continue through a
  short outage.
- A cache miss, or a broker restart during the outage, refuses only the
  affected connections with `key_service_unavailable`. That refusal is
  retryable and is **not** `reconnect_required`: the credential is fine, so
  the owner is not asked to reconnect.
- The broker's KMS health probe raises the existing uptime alarm after
  sustained failure.
- Nothing ever falls back to plaintext, a local key or a newly generated key.

**Backups and the drill.**
- Data backups carry only sealed records and wrapped KEKs, so a stolen
  backup is useless without KMS access.
- The DR drill restores from off-region copies with no production secrets. It
  assumes the OIDC role, then opens **one restored credential and one browser
  session**. A failure fails the drill.

**User-held keys stay rejected as the default.** They break 24/7 automation
while the owner is offline (Direction 8). Muse's unreleased Confidential VM is
the only precedent.

### D5. Rotation of KEKs and DEKs

- **Account KEK rotation** rewraps that account's DEKs.
- **DEK rotation** reseals one command center's records, lazily on write and
  through an explicit sweep.
- Every layer records its key id. A retired key is destroyed only when
  nothing names it.
- **Upstream rotation** is unchanged: OAuth refresh-token rotation is
  single-flight in `credential_refresh.py`.
- **Schedule:** DEKs follow NIST's roughly 2-year use period, and KMS material
  rotates yearly (D3).

### D6. Unopenable means reconnect, per credential

- A record that fails authentication, or that names a key that no longer
  exists, fails loudly for that connection only with `reconnect_required`
  (Fact 8). The existing connect ask is raised for it.
- An unreachable key service is the different, retryable
  `key_service_unavailable` (D4).
- The daemon and every other connection keep working.

### D7. Revocation and erasure

- **Disconnect** calls the issuer's RFC 7009 revocation endpoint when
  discovery advertises one, then deletes the record. When the issuer offers no
  revocation, the owner is told the grant stays valid there until it expires.
  Anthropic and Pipedream do not revoke at all.
- **Browser disconnect** deletes the sealed session.
- **Account erasure** destroys the account's wrapped KEK in the ledger after
  the D12 order. Backups taken after the cutover hold that same wrapped KEK,
  so the D12 tombstone list must refuse to re-import an erased account's KEK
  on restore. Shredding is complete once backup retention has passed.

### D8. Migration: one cutover (Direction 5), including the backups

The cutover runs in one maintenance window, after the KMS key, the IAM
credential and the OIDC role exist, and the broker's KMS health probe is green.

**Live data:**
1. Take a verified full backup in the legacy format, and keep it local to the
   window.
2. The broker generates account KEKs through KMS and creates the DEKs.
3. It seals every legacy record and every raw browser key's state.
4. It opens each sealed record and compares it with the legacy bytes. Any
   mismatch stops the cutover with nothing removed.
5. It removes the legacy files.

The same change deletes:
- the legacy reader and writer;
- the vault-group read path;
- the browser key volume.

There is no dual read and no flag.

**Existing backups: rewrite them (founder, 2026-10-11).** This step is
irreversible, so the order and checks are fixed.

1. **Inventory.** List every place a legacy vault or raw key might be:
   - every retained archive in every tier at `BACKUP_DEST`;
   - noncurrent object versions, if bucket versioning is on;
   - every GitHub release asset in the backups repo;
   - the local snapshot copies `backup.sh` keeps on the droplet.

   Record each archive's name and sha256 in a rewrite manifest.
2. **Fresh baseline.** Take a new backup in the sealed format. Verify it with a
   restore that opens a credential, as in step 4, before any old archive is
   touched.
3. **Canary.** Rewrite the newest legacy archive into a new object with a new
   name, and keep the original:
   - every `.credential-vault.json` is resealed in place by the same migration
     code, so restore value is kept;
   - every raw browser key and its state is resealed;
   - records of accounts erased since the archive was taken are **stripped**,
     not resealed, so a restore cannot bring them back;
   - anything that fails to seal is stripped and listed in the manifest.
4. **Prove the canary.** Restore the rewritten archive into a fresh
   environment through the DR drill path:
   - it must open one credential and one browser session;
   - a scan of the extracted tree must find no file named
     `.credential-vault.json`, no `browser-vault/*.key`, and none of a sample
     of known plaintext secret values. The scan runs inside the broker and
     never prints values.
5. **Then the rest, one archive at a time:**
   1. Rewrite to a new object.
   2. Verify its sha256, that it extracts, and the scan.
   3. Only then delete the original, including its noncurrent versions and its
      GitHub asset.
   4. Write the old and new sha256 to the manifest.
6. **Abort.** Any failure stops the sweep:
   - originals that were not yet replaced stay untouched;
   - already-replaced archives stay replaced, because each was verified before
     its original was deleted;
   - the manifest says exactly where the sweep stopped, and re-running resumes
     from there.

   If the canary fails, nothing has been deleted.
7. **Close-out.** A final scan of the whole destination confirms that no
   legacy name remains. The manifest is the receipt, and it goes in the PR's
   verification evidence.

## 5. PR #4611: closed; reuse its parts

#4611 is closed. Its head `dbe7a4675fc` is still on
`cursor/vault-envelope-encryption-fd9b`. Its shape was replaced because:
- the master key sat in the daemon's environment, and the broker asked the
  daemon to open records;
- one global key meant no per-account shred;
- it had no escrow, and a missing key failed every vault.

The S6 implementation reuses three parts, adapted:
1. **The envelope format** in `tinyassets/vault_envelope.py`: the versioned
   `format`, `version`, `key_id`, `nonce` and `ciphertext` fields. Here it is
   extended with the provider id, the account and command center, and the
   record generation in the AAD.
2. **The migration check:** decrypt and compare before replacing, and replace
   only while the on-disk bytes are still the bytes that were read. A second
   load never rewrites.
3. **The tests** in `tests/test_vault_envelope.py`: migration, idempotence,
   tamper, missing key, rotation and cross-center copy. They are re-pointed at
   the broker key service with the in-memory provider fake.

## 6. Founder decisions (all made 2026-10-11)

1. **Root:** AWS KMS in the Device Farm account (`us-west-2`), behind the seam.
   No app-held key and no offline copy. One least-privilege IAM user for the
   droplet, plus a keyless OIDC role for the DR drill. The setup steps are in
   `docs/host-actions.md`, beside Device Farm.
2. **Existing backups:** rewritten at cutover, following the order in D8.
3. **#4611:** closed. Its envelope format, migration check and tests are
   reused.
