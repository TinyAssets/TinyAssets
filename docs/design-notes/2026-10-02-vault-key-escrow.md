# Vault-key escrow: what must survive the loss of the production host

**Status:** proposed (target-architecture S1a.7). Escrow holder is a founder decision, below.
**Inventory measured:** 2026-10-02 on the production droplet (key NAMES only, from
`/etc/tinyassets/{env,agent-interchange.env,request-idempotency.env,app-ingress.env}`) and
`gh secret list` on TinyAssets/TinyAssets.

## The problem

S1a moves platform state off-region (Litestream + backups) so the droplet can be lost. But a
restore is only as good as the keys that make the restored bytes usable. Several keys exist **only**
in `/etc/tinyassets/env` on the droplet. They are not in GitHub secrets or the vault, and they are not
in any backup. Lose the droplet and those keys go with it. The restored data is then intact but
partly unusable: sealed sessions cannot be opened, billing entitlements cannot be verified, and every
push subscription is dead.

The DR drill cannot see this today. It already boots the restored data under a **fresh**
bootstrap env, as the uptime-and-alarms spec requires (`dr-drill.yml`, "fresh environment"). But it
only probes that `/mcp` answers. It never checks that restored sealed sessions, entitlements or push
subscriptions are usable. So a missing key makes data unusable while the drill still goes green.

## Inventory

| Key | Where it lives | Escrowed? | Losing it means | Regenerable? |
|---|---|---|---|---|
| `TINYASSETS_SESSION_SEAL_KEY` | host env | **no** | every sealed session/handle in the restored store is unreadable; all users re-authenticate | new key yes, old data no |
| `TINYASSETS_BILLING_ENTITLEMENT_KEY` | host env | **no** | v2 entitlement claims cannot be verified: paying users look unpaid | **no** (our own signing key, by design) |
| `TINYASSETS_WEBPUSH_VAPID_PRIVATE_KEY` | host env | **no** | every browser push subscription must be re-made | new key yes, subscriptions no |
| `TINYASSETS_APP_INGRESS_HMAC_KEY` | host app-ingress.env | **no** | the app-ingress signature check fails until it is re-provisioned | yes (coordinated with the sender) |
| `TINYASSETS_IDENTITY_FINGERPRINT_KEY` | host env + GitHub secret | yes (GitHub) | identity fingerprints would not match restored rows | no |
| `TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY` | host file + GitHub secret | yes (GitHub; installed by deploy since #4260) | interchange signatures | no |
| `TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY` | host file + GitHub secret | yes (GitHub; set-once by deploy since #4260) | idempotency witnesses | no |
| `TINYASSETS_WIKI_CANARY_TOKEN` | host env + GitHub secret | yes | the canary principal | yes |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | host env | no | billing stops until re-fetched | yes, from the Stripe dashboard |
| `WORKOS_API_KEY` | host env + GitHub secret | yes | login | yes |
| `CLOUDFLARE_TUNNEL_TOKEN` | host env | no | the tunnel connector | yes, from the CF dashboard |
| `TINYASSETS_FCM_SERVICE_ACCOUNT_JSON` | host env | no | Android push | yes, from the Firebase console |
| `GH_TOKEN`, `DO_API_TOKEN` | host env | partly | (being removed from the daemon env by the secret-scope lane) | yes |
| **Backup encryption key** (new, S1a.3) | does not exist yet | n/a | an off-region backup that cannot be decrypted is no backup | **no** |
| **Broker vault key / account KEKs** (S6) | do not exist yet | n/a | the encrypted vault and every per-command-center DEK | **no** |

The bold "no" rows in the last column are the escrow set. Losing one of those is permanent: no
dashboard can re-issue it. Everything else is regenerable, but each still costs an outage while
someone regenerates it, so it belongs in escrow too.

## Proposal

1. **One escrow holder for the whole set, with CI-only read access: GitHub Actions repository
   secrets.** GitHub already holds five of these keys. The deploy installs from it (#4260
   precedent: GitHub is the source of truth and the host gets a set-once copy). The S1b standby
   promotion runs in CI and would read keys from the same place, with no second channel. Every
   host-only key moves there and becomes deploy-installed, the way #4260 does it for the two HMAC
   keys.
2. **A second, offline copy held by the founder** (a password manager entry or a printed sealed
   copy). GitHub secrets are write-only from the UI, so they cannot be read back out to rebuild
   GitHub itself or to survive losing the GitHub org. This copy is only for a total GitHub loss.
3. **The backup encryption key is asymmetric (age).** The droplet holds only the age **recipient**
   (public key), so it can encrypt backups but never decrypt them. The **identity** (private key)
   lives only in escrow and is read by the DR drill and the standby restore in CI. A compromised
   droplet then cannot read old off-region backups, which matters once user data sits in a
   third-party bucket.
4. **The drill proves escrow.** The weekly drill installs the escrowed keys into its fresh env.
   Then, beyond the liveness probe, it opens one restored sealed session, verifies one stored
   entitlement claim, and checks the VAPID public key against a stored subscription. A key missing
   from escrow then fails the drill. That is the executable check that this inventory stays complete,
   so a new host-only key cannot quietly appear.
5. **Rotation is per key and recorded in the deploy path,** like `rotate_request_idempotency_hmac`:
   an explicit dispatch input, never an automatic replace. The data-signing keys (billing
   entitlement, fingerprint, seal) need a versioned verify-old/sign-new window. The billing adapter
   already has one (v1 verified, v2 issued).

## Founder decisions

- **Escrow holder:** GitHub secrets (recommended), or a dedicated secrets manager (1Password
  Secrets Automation, Doppler, ...). The latter is better audited but is a new vendor and a new
  spend line.
- **The offline second copy:** who holds it, and where.

## Not in this note

- The S6 broker key hierarchy (DEK/KEK) is designed in target-architecture §D12. This note only
  requires that its root key lands in the same escrow.
- The secret-scope lane's removal of platform secrets from the daemon env is orthogonal. Escrow
  is about surviving host loss, not about who can read a key at runtime.
