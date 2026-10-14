# Host actions and decisions

Work that **only the founder can do** — because it needs an account, a dashboard, a credential, or a
judgment call no agent is authorized to make. Migrated from the `STATUS.md` Work table on
2026-08-25 when the board was retired.

Everything else that was on that board lives in `openspec/changes/` (the queue) or
`docs/concerns/` (unresolved findings). This file exists because those two homes can't hold an item
whose next step is *"the founder logs into Cloudflare."*

**Delete a row when it's done.** Git holds the history.

---

## Production secrets move into a main-only GitHub Environment (2026-10-13)

**Why:** a `workflow_dispatch` on a branch runs that branch's YAML, so `if: github.ref == 'refs/heads/main'` guards protect nothing; repo-level secrets are reachable from any ref, and run 38484527257 scp'd branch code onto the production host as root. Every production job now declares `environment: production` (enforced by `tests/test_workflow_production_environment.py`); the environment itself must exist and own the secrets, which only the repo admin can do.

| Item | Smallest founder-only action |
|---|---|
| GitHub Environment `production` | Repo → Settings → Environments → New environment `production`. Deployment branches and tags: **Selected branches**, add `main` only (optionally add yourself as a required reviewer). Under Environment secrets add `DO_SSH_KEY`, `DO_DROPLET_HOST`, `DO_SSH_USER`, `DIGITALOCEAN_TOKEN`, `DO_API_TOKEN` with their current values. Then delete the five repo-level copies under Settings → Secrets and variables → Actions. Merging first breaks nothing: GitHub auto-creates `production` the first time a job references it, with no branch restriction, and repo-level secrets still resolve inside it. For the same reason the hole stays open until BOTH halves are done, the environment is restricted to `main` AND the repo-level copies are deleted; until then a branch dispatch of an old or edited workflow can still reach the host. The test treats every secret outside its allowlist as production, so move the daemon secrets deploy-prod syncs onto the host (`TINYASSETS_OAUTH_GOOGLE_CLIENT_SECRET`, `TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY`, `TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY`) and dr-drill's `OFFREGION_BACKUP_REMOTE` / `OFFREGION_BACKUP_RCLONE_CONFIG` in the same sitting: a branch can read them the same way. |

---

| One-time core-canary setup (2026-10-10) | Founder-only action |
|---|---|
| Dedicated synthetic owner | Create a separate owner with normal password sign-in, a connected model, and a bounded HTTP connection whose POST endpoint returns `hmac`: lowercase hex HMAC-SHA256 of the submitted UTF-8 `nonce`, using a dedicated random key the model never receives. Install a private code branch taking `nonce` and returning it as `echo`. Route this owner's synthetic patch requests to an intake that records them without creating production issues. Set GitHub secret `TINYASSETS_CORE_CANARY_CONFIG` to JSON containing `email`, `password`, `universe_id`, `connection_id`, `service_path`, `service_hmac_key`, `intake_receiver_id`, and `branch_id`. Reuse the existing health-canary and Pushover secrets. No founder/user data belongs in this account. `deploy-prod` runs `scripts/verify_core_canary_provision.py` before any host change and refuses the deploy when this secret is missing or incomplete. After the swap, `canary_not_provisioned` fails the core-canary step and rolls the new image back. |

## Isolation production refusal acceptance (2026-10-10)

| Item | Smallest founder-only action |
|---|---|
| Second owner for the post-cutover refusal probe | Supply a separately signed-in tester B account/command center. Runbook ?6.5: B?s cell attempts the founder?s tree and vault, then B?s daemon-side read names the founder?s file; both must refuse. Local UID proofs passed, but no production two-account result is claimed. |

## Connector directory submissions (2026-10-10)

Packet: [requirements, form answers, assets and isolated reviewer plan](ops/connector-directory-listings.md).
Engineering readiness gaps are listed there; do not attest they are resolved yet.

| Submission | Smallest founder-only action |
|---|---|
| Meta Muse (first) | Open [Connector Platform](https://muse.ai/platform) with the company account; supply actual legal identity and a separate reviewer identity, accept Meta's terms and submit the packet once its prerequisites pass. Custom-connector access is not required for this submission route. |
| ChatGPT | Open [Plugins](https://platform.openai.com/plugins), verify the publishing identity, supply the domain challenge to engineering and a separate reviewer identity; upload the prepared packet/package, accept terms and submit after review tests pass; publish when approved. |
| Claude | Open [directory management](https://claude.ai/directory/manage) on a paid account; choose MCP connector, supply actual company identity and separate reviewer identity, accept the directory terms and submit the packet after tool tests and policy checks pass. |

Never provide the founder's account, command center or model credentials to reviewers.

---

## Delete the GitHub OAuth App (2026-10-03)

**Why:** `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET` are removed from the
template and withheld from the daemon by `RETIRED_ENV` — nothing read them and no route
serves the callback they described. Deleting them from `/etc/tinyassets/env` itself is
deferred to branch `security/retire-github-oauth-workflow-secret`, so until that lands a
stale assignment may still sit in the host env file (withheld from the daemon, but
present). Two things the code change cannot do:

* **This repo is public, and `docs/ops/day-of-cutover.md` carried an example assignment
  for the secret.** It looks like a placeholder and I did not establish otherwise (I did
  not read the value). Deleting the line does not scrub git history, so if that string was
  ever the real one it is already public.
* An OAuth App that still exists can still be used by whoever holds its secret.

Smallest ask: **GitHub → Settings → Developer settings → OAuth Apps → delete the
TinyAssets app** (or regenerate its secret if you want to keep the registration for the
deferred `/connect` sign-in). Deleting it makes any leaked value worthless and matches
the decision that unused secrets are removed rather than guarded.

Nothing is blocked on this: no code path uses the app, and the renderer withholds both
names from the daemon even if a stale assignment survives on a host
(`deploy/install-tinyassets-env.sh` `RETIRED_ENV`).

---

## Rotate the DigitalOcean API token, then drop it from the box (2026-10-02)

**Why:** an account-wide `DO_API_TOKEN` sat in `/etc/tinyassets/env` and so in the
daemon's process environment and every engine MCP child
(`docs/concerns/2026-10-02-platform-secrets-in-daemon-env.md`). The env split keeps it
out of the daemon from the next deploy on, but the value was exposed for as long as the
daemon ran with it. Nothing on the box reads it; the workflows use the GitHub secret.

1. DigitalOcean → API → Tokens: create a replacement, then revoke the old one.
2. `gh secret set DO_API_TOKEN` with the new value (the workflows need it).
3. On the droplet, after the next deploy: `sudo tinyassets-env delete DO_API_TOKEN`.

Optional, same concern: swap `STRIPE_SECRET_KEY` for a restricted `rk_live_` key scoped
to Checkout, Subscriptions and webhook reads. The daemon must still hold a Stripe key.

---

## Creator revenue share: decide F1–F14 and get counsel on the token questions (2026-10-01)

**Why:** `openspec/changes/creator-revenue-share/` designs paying creators a share of
paid subscriptions in TINY. Phase A (attribution, shadow dollar ledger, metrics, no
money) waits only on your approval and the parameter answers in design §10. Mainnet
payout waits on counsel's answers to design §9: securities and token-promotion optics,
money transmission, creator tax reporting, Stripe, Google Play and Apple policy,
jurisdictions, privacy, unclaimed balances. Nothing pays out until you sign each
monthly batch yourself.

## Decide: do pre-#4287 automations get grandfathered, or does every recurring job hold? (2026-10-03)

Talking to a custom agent works, but its turns still pick up **main's** rules,
review, approval requests and Stop controls — so the agent you addressed is not
the agent whose controls apply. #4343 designs the fix. Two things in it are hard
to reverse, so they are yours before anyone writes code.

**1. The one real judgement call: grandfathering.** Existing recurring
automations carry no record of which agent authored them. The design's safe
default is to hold every future firing until you reconfirm it — and that means
**every ordinary main automation stops too**, which is a lot of stopped work to
restart by hand. The alternative: custom-agent `converse` only existed from
#4287 (merged 2026-10-03 01:41Z), so anything authored before that moment can
only have come from you or `main`; stamp those explicit-`main` and hold only
definitions newer than that. Cheaper and matches clean cutover — but it trusts a
merge timestamp as lineage, which the same design forbids everywhere else, so it
is a deliberate exception rather than an oversight.

> **Ask:** is the #4287 merge time good enough to grandfather everything older as
> `main`? **Yes** → only post-#4287 definitions hold. **No** → every recurring
> definition holds until you reconfirm it, and the implementation must ship the
> hold inert until the owner-vs-engine audience split is proven (otherwise it is
> a self-inflicted outage with no reachable restart).

**2. Approve the shape, once, before implementation.** AGENTS.md wants public
surface, storage shape and authority specced before code. This change adds: a
public `write_graph` payload field (`confirm_agent_provenance`) plus `held` /
`held_reason` / `reconfirmation_required` read fields — a live-connector spec
delta needing a canary `--assert-handles`; a per-launch 256-bit transport
credential with a server-side digest; snapshot columns on runs, turns, the
journal, pending requests and automations plus a launch-binding table; and the
new held state on existing rows. No money, no new provider spend, no widened
permission — each control keeps its current authority and merely selects the
addressed agent instead of always `main`.

> **Ask:** say go on that shape and implementation starts in vertical slices,
> founder-visible one first. Design review is otherwise complete (Claude ADAPT
> folded 2026-10-03; design is docs-only).

## Expose your patch intake as a receiver, so new users can be offered it (2026-09-30)

**Why:** PR #4121 seeds a consent request in every new user's rail — "Let your universe
report problems to TinyAssets" — and approving it connects their universe to your intake.
The platform has to be TOLD which intake to offer: it is your universe's node, owned by
you like any user's, so there is no id in the code. It needs a `receiver_id`, and
production has none yet (`/data/.runs.db` `graph_receivers`: 0 rows, read 2026-09-30).

Your two intakes exist today only as inbound `/mcp/hooks/<token>` webhooks. Those are
anonymous — whatever arrives is attached to nobody's universe, and the sender has to hold
a secret. Native delivery carries the sender's identity and needs no secret at all, which
is why the seeded request has nothing to paste.

**Ask your universe, in the app or the chatbot** (branch `bc19127bde44` is the general
patch-request one, with `what_they_tried` / `what_was_missing_or_broken` / `request_type`):

> Expose the entry step of my patch-request workflow as a receiver any authenticated user
> can send to, and list it so they can find it. Accept `what_they_tried`,
> `what_was_missing_or_broken` and `request_type`. Tell me the receiver id.

It will call `write_graph target="receiver" operation="create"` with `open_to_all: true`
and `discoverable: true`. **Send the lead the `receiver_id` it returns** — that value goes
into `TINYASSETS_PATCH_INTAKE_RECEIVER_ID` in the deploy env, and until it is set the
seeded request does not appear for anyone.

Optional: `TINYASSETS_PATCH_INTAKE_LABEL` changes what the platform calls your intake in
that request. It defaults to `TinyAssets`.

This blocks the Play closed test: the founder asked for patch requests to be live before
testers arrive.

## WorkOS: register the app's new redirect URI (2026-09-30)

**One dashboard field. Sign-in is broken at the new URL until it is set.**

The app moved to `https://tinyassets.io/app`
(your directive, no back-compat). The SPA builds its OAuth `redirect_uri` from the
page it is served at, so AuthKit now receives `https://tinyassets.io/app` — and
AuthKit refuses a redirect URI that is not registered. Nothing in the repo can
register it; this is the single founder action for the move.

1. `dashboard.workos.com` → **Production** environment → **Redirects**.
2. Add `https://tinyassets.io/app` to **Sign-in callback / Redirect URIs**.
3. Remove the old app redirect URI once the new one is saved. Leaving it is
   not dangerous, but it is dead — nothing serves that path any more.
4. Nothing else changes: same origin, same client ID, same MCP resource
   (`https://tinyassets.io/mcp`, untouched by the move).

Expected symptom before you do this: sign-in bounces to AuthKit and comes back with
an `invalid_redirect_uri` / "redirect URI not allowed" error instead of a session.
The app shell itself loads fine either way, so `curl` proof of `/app` passing does
not prove sign-in works.

**Third-party OAuth connections with a PRE-REGISTERED client.** The generic
connection flow's one fixed redirect URI moved too, to
`https://tinyassets.io/app/model-callback/connect`. Connections that dynamically
register a client send the new callback automatically and need nothing. But if a
provider's `client_id` was supplied by hand, that provider's own app settings
still list the old return path and will refuse the exchange — whoever owns
that provider account updates the redirect URI there. Nothing in this repo can
do it, and it is per-connection rather than platform-wide.

**Also, only if Stripe billing is switched on** (it is inert unless
`STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` are set): the Stripe webhook endpoint
is registered as a URL and moved with the app. Repoint it to
`https://tinyassets.io/app/billing/webhook` —
`python scripts/stripe_go_live.py --check --webhook-url https://tinyassets.io/app/billing/webhook`
says whether it needs doing, and `--provision` creates the new endpoint (it prints
the new `whsec_…` once; the old endpoint should then be deleted in the dashboard).
If billing is not switched on, there is nothing to do.

---

## Google Play: finish the closed test and launch

Founder Console snapshot supplied for the 2026-10-11 goal: `alpha` has build 6
(1.0.5, Oct 3), Google Group `testers-community@googlegroups.com`, 12 testers
continuously opted in for 9 days, and no Console notifications.

1. After this PR merges, run **Android release AAB** on `main`:
   `gh workflow run android-release.yml --ref main`. Existing signing and Firebase
   secrets are present; no environment approval is configured for Android.
2. Download `tinyassets-release-aab`; upload **1.0.7 / code 8** to the existing
   **alpha** track **before 2026-10-24 00:00 UTC**, the old sign-in compatibility
   cutoff. Install/test it through Play, including system-browser sign-in, return,
   and a real reply. Keep the same tester group and continuous opt-ins.
3. On/after Console confirms **12 testers for 14 continuous days** (expected
   **Oct 15–16**, not a guaranteed unlock date), click **Apply for production**.
   Fill the marked founder facts in the [draft answers](ops/google-play-launch.md#production-access-questionnaire)
   and paste them. After access approval, submit the tested release to production
   and complete rollout after review; access approval alone does not publish the app.

## Apple: record the review video and resubmit

Build 3 (1.0) remains under the Guideline 2.1 information request; #4594's
system-browser sign-in is merged and live but needs a new native build.

1. After this PR merges, run `gh workflow run ios-release.yml --ref main -f marketing_version=1.0.1 -f upload_to_testflight=true`.
   Leave the build number blank for the workflow run number (must be unused).
   Approve the **app-store** environment for signing and, when requested, upload.
   All six signing/upload secrets exist; no replacement is requested.
2. Attach the processed **1.0.1** build to TestFlight Internal and the App Store
   version for resubmission. With the dedicated reviewer credentials held privately
   in App Store Connect, verify system-browser sign-in returns to TinyAssets,
   the real connection flow works, and a substantive reply arrives. Do not reuse
   build 3 or an interim review-only key for this proof.
3. Record the 2.1 video with the automated physical-iPhone workflow below (no personal
   iPhone needed), check all six shots, attach it with the
   [written answers](ops/app-store-submission-packet.md#guideline-21-response-packet)
   to the unresolved 2.1 request, and resubmit the selected 1.0.1 build only on the
   founder's go. Confirm release settings and the public install page after approval.

### Apple: enable automated physical-iPhone review video

Founder direction 2026-10-11: no personal iPhone is needed. The **iOS App Review
video** workflow (`.github/workflows/ios-review-video.yml`) runs the six-step
Guideline 2.1 packet on one physical AWS iPhone. It first runs the same test against
production on Simulator; that proof alone is not Apple's required device video.

1. Use [AWS advanced account signup](https://docs.aws.amazon.com/accounts/latest/reference/getting-started.html)
   to create a standard AWS account, complete email/phone/payment verification,
   choose the Paid account plan (pay as you go) and Basic support, and enable root
   MFA. Use an administrator identity for the following one-time setup;
   do not create root access keys.
2. Open [Device Farm in Oregon](https://us-west-2.console.aws.amazon.com/devicefarm/home?region=us-west-2).
   Choose **Mobile Device Testing > Projects > Create project**, name it
   `tinyassets-app-review`, and copy its ARN. This enables use of Device Farm;
   select metered testing, not a monthly device slot. All workflow calls use
   `us-west-2`, Device Farm's mobile-testing region.
3. In **IAM > Users > Create user**, name it `tinyassets-review-video`, with no
   console access. Add the inline policy below, substituting your 12-digit account
   ID and the project UUID (the text after `project:` in its ARN). Do not attach
   AdministratorAccess, S3, IAM or any other service permissions.
4. Under that user's **Security credentials > Create access key**, select
   **Application running outside AWS**. Copy the key ID and secret directly into
   GitHub **TinyAssets/TinyAssets > Settings > Environments > app-store > Add
   environment secret** as `AWS_DEVICEFARM_ACCESS_KEY_ID` and
   `AWS_DEVICEFARM_SECRET_ACCESS_KEY`. Add the project ARN as
   `AWS_DEVICEFARM_PROJECT_ARN`. The existing `APP_STORE_REVIEW_USERNAME` and
   `APP_STORE_REVIEW_PASSWORD` remain in that environment; do not put any value in
   source, workflow inputs, chat, screenshots or command arguments.
5. Generate the evidence key locally with the commands below, replace
   `mobile/review-evidence-recipient.pub` with ONLY the public PEM output, and
   commit that public file. The placeholder deliberately blocks the workflow.
   After merge, in **Actions > iOS App Review video > Run workflow**, dispatch
   from **main only** and leave **dry_run=true** first. Keep the app-store
   environment restricted to main and approve its required review. Before merge,
   **iOS build > review_video_compile_only=true** compiles both runners without
   environment access. A successful
   run compiles both runners, builds a signed IPA and completes the production
   Simulator journey without AWS calls. If the reviewer has no working model
   connection, the test fails; arrange the normal user connection first, never an
   interim review-only inference key.
6. Dispatch **iOS App Review video** from **main only** with **dry_run=false** to authorize one metered
   physical run. Download `ios-review-physical-iphone-<run-id>` from its artifacts:
   an encrypted bundle containing MP4, named per-step PNGs, test status, device/iOS
   version and source SHA. Runs
   fail if the journey, splash, recording or screenshot evidence is incomplete.
   Review all six shots, password masking, and the recorded OS against Apple's
   latest-public-iOS request before sending it to the lead. Apple attachment and
   resubmission still require the lead's explicit go.

Optional hardening: set `prevent_self_review` and `can_admins_bypass` only if another available reviewer preserves the founder's approval path; stricter settings can block a sole founder's own dispatches.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "OnlyReviewProjectAndItsChildren",
      "Effect": "Allow",
      "Action": [
        "devicefarm:CreateUpload",
        "devicefarm:GetUpload",
        "devicefarm:DeleteUpload",
        "devicefarm:ScheduleRun",
        "devicefarm:GetRun",
        "devicefarm:StopRun",
        "devicefarm:DeleteRun",
        "devicefarm:ListJobs",
        "devicefarm:ListSuites",
        "devicefarm:ListTests",
        "devicefarm:ListArtifacts",
        "devicefarm:ListRuns",
        "devicefarm:ListUploads"
      ],
      "Resource": [
        "arn:aws:devicefarm:us-west-2:ACCOUNT_ID:project:PROJECT_UUID",
        "arn:aws:devicefarm:us-west-2:ACCOUNT_ID:upload:PROJECT_UUID/*",
        "arn:aws:devicefarm:us-west-2:ACCOUNT_ID:run:PROJECT_UUID/*",
        "arn:aws:devicefarm:us-west-2:ACCOUNT_ID:job:PROJECT_UUID/*",
        "arn:aws:devicefarm:us-west-2:ACCOUNT_ID:suite:PROJECT_UUID/*",
        "arn:aws:devicefarm:us-west-2:ACCOUNT_ID:test:PROJECT_UUID/*"
      ]
    },
    {
      "Sid": "DiscoverPublicIPhones",
      "Effect": "Allow",
      "Action": "devicefarm:ListDevices",
      "Resource": "*",
      "Condition": {"StringEquals": {"aws:RequestedRegion": "us-west-2"}}
    }
  ]
}
```

The separate read-only `ListDevices` statement is required: AWS does not support
resource-level permissions for this action. A literal project-ARN-only policy
cannot discover the newest iPhone/iOS. All mutation permissions above are confined
to this project's resources, and no other AWS service is granted. See the
[AWS service authorization table](https://docs.aws.amazon.com/service-authorization/latest/reference/list_devicefarm.html).
`ListRuns` and `ListUploads` are the only additions to the recording/cleanup
operations: the cancellation recovery sweep needs them to discover leftovers.
The script uses no other AWS API operations; upload/download transfers use the
service's presigned URLs. Device selection does not need a device pool or PassRole.

**Cost, checked 2026-10-11:** [AWS pricing](https://aws.amazon.com/device-farm/pricing/)
lists **$0.17/device-minute** and a limited-time **1,000-minute one-time trial**
(normally 250 minutes). Confirm the trial balance in your new account. One 10-20
minute run is about **$1.70-$3.40** after trial; the 30-minute device timeout is
about **$5.10** in device time, plus any billable service setup time. GitHub macOS
runner usage and the reviewer's model usage are separate. No monthly device slot
is needed. Dry run incurs no AWS usage.

Device Farm re-signs the uploaded current-source app. The artifact records its
SHA; it must not be described as the old build 3 binary. The workflow attempts to delete its
AWS run metadata and uploaded packages after downloading evidence because the
restricted run environment carries reviewer credentials. Keep the GitHub artifact
(the retention is 14 days); failures also retain available evidence. No AWS
account, access keys or paid run is created by the builder.

The repository is public, so **all video/screenshot evidence is encrypted before
upload**, using ephemeral X25519 + HKDF-SHA256 + AES-256-GCM to the committed
recipient public key. No encryption key is derived from the reviewer password.
On the lead's machine, run once (stdout contains only the public PEM):

```powershell
python -m pip install cryptography==46.0.3
python scripts/review_evidence_keygen.py
```

The private key is created with an owner-only ACL at
`%USERPROFILE%\.tinyassets\review-evidence.key`; keygen refuses to overwrite it.
Never commit or upload that private key. Preserve it privately: without it, old
evidence cannot be recovered. Paste only the public output into the recipient
file (plain ASCII/UTF-8). After downloading and unzipping an Actions artifact,
decrypt locally on that same machine:

```powershell
python mobile/scripts/review_evidence.py decrypt review-devicefarm.enc private-review-video
```

Decryption reads the local private key; there is no password prompt.
For Simulator evidence substitute `review-simulator.enc`. Keep decrypted files
private. If the
workflow reports incomplete AWS cleanup, decrypt `cleanup-required.json` and
remove the listed run/uploads in Device Farm after the run has stopped. A forced
runner termination is recovered by the next physical run: before uploading or
sending new credentials, it deletes runs/uploads older than two hours in this
dedicated project, stopping and awaiting unfinished runs first. Do not reuse this
project for other tests. Dry runs make no AWS calls and do not sweep; if no next
physical run is planned, remove leftovers manually. The job allows 75 minutes
for orchestration and cleanup. GitHub has no `cancel-timeout-minutes` setting;
its [cancellation protocol](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation)
can kill a process before cleanup finishes, so extending the job timeout alone
does not guarantee cancellation cleanup.

### AWS: credential vault root key (same account as Device Farm)

Founder decision 2026-10-11: the credential vault's root key is one AWS KMS key
in the AWS account created in step 1 above. That gives one AWS account and one
checklist. There is no offline copy. Design:
`openspec/changes/credential-vault-reshape/design.md` D3. Nothing reads these
values until the cutover PR ships, so do this whenever the account exists.

1. Open [KMS in Oregon](https://us-west-2.console.aws.amazon.com/kms/home?region=us-west-2).
   Choose **Customer managed keys > Create key**:
   - **Symmetric**, **Encrypt and decrypt**, single-Region;
   - alias `tinyassets-vault-root`;
   - key administrator: your administrator identity only;
   - key users: none, because IAM policies grant use (the default key policy
     allows that).

   After creation:
   - open **Key rotation** and enable automatic yearly rotation;
   - copy the key ARN.

   Never schedule deletion. If deletion is ever scheduled, it waits 30 days,
   and it can be cancelled until then.
2. In **IAM > Users > Create user**, name it `tinyassets-vault-broker`, with no
   console access. Add only the inline policy below, with your 12-digit
   account ID and the key ID (the text after `key/` in the ARN). Attach nothing
   else.
3. Under that user's **Security credentials > Create access key**, choose
   **Application running outside AWS**. Paste the values into GitHub
   **TinyAssets/TinyAssets > Settings > Environments > production > Add
   environment secret**:
   - `TINYASSETS_VAULT_AWS_ACCESS_KEY_ID`
   - `TINYASSETS_VAULT_AWS_SECRET_ACCESS_KEY`
   - `TINYASSETS_VAULT_KMS_KEY_ARN` (the key ARN)

   Deploy installs them only for the broker process. Never put them in
   `/etc/tinyassets/env`, chat or a command line.
4. In **IAM > Identity providers > Add provider**, add the GitHub OIDC
   provider if it is not there already:
   - type **OpenID Connect**;
   - provider URL `https://token.actions.githubusercontent.com`;
   - audience `sts.amazonaws.com`.

   Then **IAM > Roles > Create role > Custom trust policy**:
   - paste the trust policy below;
   - name the role `tinyassets-vault-dr-drill`;
   - add the drill permission policy below as its only inline policy.

   Then add GitHub environment `dr-drill`, restricted to main, with secret
   `TINYASSETS_VAULT_DR_ROLE_ARN` set to the role ARN. The DR drill uses this
   role, with `kms:Decrypt` only and no stored key, to prove a restored
   credential opens.

**Broker policy** (`tinyassets-vault-broker`, inline):

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

**DR drill trust policy** (`tinyassets-vault-dr-drill`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"},
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "repo:TinyAssets/TinyAssets:environment:dr-drill"
        }
      }
    }
  ]
}
```

**DR drill permission policy** (inline on that role):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DrillDecryptOnly",
      "Effect": "Allow",
      "Action": "kms:Decrypt",
      "Resource": "arn:aws:kms:us-west-2:ACCOUNT_ID:key/KEY_ID",
      "Condition": {
        "StringEquals": {"kms:EncryptionContext:ta_purpose": "account-kek"}
      }
    }
  ]
}
```

**Rotate the access key** yearly, or at once on any suspicion:
1. Create a second access key on `tinyassets-vault-broker`.
2. Replace the two GitHub secrets.
3. Redeploy.
4. Confirm the broker's KMS health probe is green.
5. Deactivate the old key.
6. Delete it after 24 hours with no use in CloudTrail.

**Emergency stops**, mildest first:
1. **Deactivate the access key, then restart the broker.** All credentialed
   calls stop. This is reversible.
2. **Disable the KMS key.** It stops the drill as well. This is reversible.
3. **Schedule deletion.** After 30 days, every stored credential in every copy
   is unreadable and every user must reconnect.

If AWS is unreachable, live connections keep working from the broker's memory
cache. Others refuse with a retryable error and the uptime alarm fires. No
credential is ever exposed and nothing falls back to plaintext.

**Cost, checked 2026-10-11:** [AWS KMS pricing](https://aws.amazon.com/kms/pricing/)
is $1/key-month, plus $1/month for each of the first two yearly rotations.
Requests cost $0.03 per 10k beyond 20k free a month, and this usage stays
inside the free tier. Expect about **$1-3/month**.


---

## Clear the ACL-locked sandbox temp directories (2026-09-26)

Only an elevated shell can do this one. 68 directories under
`%TEMP%` plus `.codex-test-tmp/` and `.pytest-tmp/` inside the checkout carry
sandbox-token ACLs the interactive user cannot read, list, or delete — not just
cannot delete: `Get-Acl` itself fails. `scripts/dev_hygiene.py` reports them as
`acl_locked_needs_elevation` and deliberately never tries to force them.

```
powershell -ExecutionPolicy Bypass -File scripts/clear_sandbox_temp_dirs.ps1 -Apply
```

Prevention is already in `tests/conftest.py`, which refuses a temp root inside
the repo.

## Decide: make a blocking review verdict a required check (2026-09-26)

A Tier 2 review verdict is posted as a PR comment, and auto-merge doesn't read
comments. On 2026-09-26, #3989 (installer idempotence gate) auto-merged at the
exact head its reviewer had BLOCKED. It carried `infra-change`, which satisfies
`pr-scope-guard` for release-critical files without a receipt. The fixes followed
in #3993. The lead now disables auto-merge on every PR sent to Tier 2 review until
it is approved. That is discipline, not enforcement. The durable option: have
`pr-scope-guard` also require an exact-head `Drain-Review-Verdict: APPROVE` receipt
whenever a PR declares `infra-change` or a Tier 2 title, so a BLOCK holds the PR
the way a failing required check does. That changes a gate file, so it's yours to
approve. Say yes and an agent builds it.

## Delete the platform's model-credential repository secrets (2026-09-24)

The platform has no LLM (AGENTS.md Hard Rule 15), and after the retire-platform-llm-logins
PR nothing reads these. Agents cannot delete repository secrets. In GitHub →
Settings → Secrets and variables → Actions, delete: `CLAUDE_CODE_OAUTH_TOKEN`,
`OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `XAI_API_KEY`,
`WORKFLOW_CODEX_AUTH_JSON_B64`, `WORKFLOW_CLAUDE_CREDENTIALS_JSON_B64`, and the
platform GitHub push map `WORKFLOW_GITHUB_PR_CAPABILITIES`. Also revoke the
underlying keys/tokens at each provider, including the GitHub token inside
`TINYASSETS_GITHUB_PUSH_CAPABILITIES` on the droplet (the deploy scrubs the env
line; the token itself stays valid until revoked). Do it after that PR deploys,
so a rollback never meets a missing secret.

## Replace the backup's broad GitHub token with a backup-only one (2026-09-25)

`GH_TOKEN` in the droplet's `/etc/tinyassets/env` is a live GitHub CLI token (`gho_`, scopes
`gist, repo, workflow`) that the daemon user can read. Only the nightly offsite backup needs it
([concern](concerns/2026-09-25-backup-token-readable-from-container.md)). In GitHub → Settings →
Developer settings → Fine-grained tokens, create a token with **Contents: read and write on
`Jonnyton/tinyassets-backups` only**, and add it as the repository secret `BACKUP_GH_TOKEN`. An agent
then moves the backup to a host-only file and revokes the old token individually (GitHub's credential
revocation API), so your own `gh` login is not affected.

## Delete or uninstall the platform GitHub App (2026-09-24)

The GitHub App token refresher and its host units are removed by the same PR.
Its App was never configured on the droplet (no
`/etc/tinyassets/github-app-token-refresher.env`, no private key; the timer
skipped every run), and no App ID is recorded in the repo, so an agent cannot
name it. In GitHub → Settings → Applications (and Developer settings → GitHub
Apps), uninstall/delete any App installed on `TinyAssets/TinyAssets` for the
community-loop bot identity (Contents + Pull requests write).

## Rotate the production Cloudflare tunnel token (2026-09-24)

The `tinyassets-tunnel` container's start command carries the tunnel token in
plain text (`docker inspect tinyassets-tunnel`). On 2026-09-24 a debugging agent
printed it into a local session transcript. Rotate it in Cloudflare Zero Trust →
Networks → Tunnels, then redeploy the tunnel with the new token, supplied from
the vault rather than as a command-line argument. After rotating, check that
`python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp` is green.
Agents may not change credentials.

## Add Cloudflare Tunnel: Read to the `workflow` API token (2026-09-24)

My Profile → API Tokens → `workflow` → Edit → Add more: **Account ·
Cloudflare Tunnel · Read**. Change nothing else. This lets the hosted
cloud-only preflight read the tunnel's connectors (currently `cf_api_http_401`),
which proves there is exactly one cloud connector and no desktop. It also helps
diagnose the 2026-09-24 app 503s
(`docs/concerns/2026-09-24-app-503-authored-in-front-of-origin.md`, PR #3948).
Claude Code's permission guard refuses scope grants from agents, so the founder
must do this.

## Connect the Claude extension in the free test user's Chrome profile (2026-09-24)

Only the founder's profile is connected. The free-only OpenRouter onboarding
test (capability C5/C1) needs the second profile's extension connected so it
can run as that user. No other founder step is needed; the agent drives the
rest.

## Decide: should a deposit serve the universe by itself?

The deposit spec (`openspec/specs/byo-llm-deposit-surface/spec.md`,
"The result is non-secret and names the serving re-point") says the deposit
**SHALL NOT itself enable serving**. On 2026-09-01 a pasted Codex deposit through the app
left the universe chatting but every run refused with `provider_not_bound`, because the
paste path never followed the hint. #2760 fixes that in the app (the paste path and the
heartbeat call the same `/app/serving/bind` the phone uses); a server-side
"deposit serves when nothing serves" was built, then withdrawn on Codex review because it
contradicts the requirement above.

Your call, because it is a contract change: keep the deposit write-only (every surface
finishes the gesture itself, as now), or change the spec so `connect_llm` serves when
nothing is serving yet. The second is one spec delta plus the withdrawn code; it needs
your approval first. Tiny's view from inside: "registration and selection must not be
separable in a way that leaves me runnable-on-paper but dead in practice."

## Capacity for 1,000 users

### Apply the daemon memory limit — needs a compose sync, not a decision

`deploy/compose.yml` now carries `mem_limit: 4g` + `memswap_limit: 4g` (#2658), but the
droplet's copy at `/opt/tinyassets/compose.yml` is from 2026-08-22 and **is not shipped by
the deploy pipeline** — image updates do not sync it. So the limit is inert and the daemon
is still unbounded.

Much less urgent since the resize: the cgroup's observed peak of 1411.8 MiB was 69% of the
old 2 GB host and is 18% of 8 GB. Left as a follow-up rather than a second outage tonight.
It needs a compose sync plus a daemon recreate (brief blip), which is my job, not yours —
this row exists so it is not forgotten.

### Related, and free: the container has no memory limit

`docker inspect` reports `mem_limit=0`, so the daemon may consume the whole host. With
the run pool already peaking at 1.1 GB of 2 GB, an overshoot OOMs the *host* and takes
`tinyassets-tunnel` with it — a total public outage, where a container limit would have
been a restart (`restart=unless-stopped`). No OOM has happened; the margin is thin. I can
add the limit with the resize, where there is headroom for it to be generous rather than
a new way to fail.

---

## Taking real money

### WorkOS production cut-over — DONE 2026-08-29, nothing left for the founder

Kept as one paragraph so the next reader does not re-derive it: daemon on the production
key (`unassuming-environment-16.authkit.app`, Connect client `client_01M15YZXW7G7X6X1YQ4TG87Q00`,
resource indicator `https://tinyassets.io/mcp`); both founder accounts migrated and verified
live; Google consent screen **In production** (any Google account can sign in); GitHub
PR-writer re-established through production WorkOS Pipes (provider enabled, OAuth app
`TinyAssets WorkOS Pipes` carries both callbacks, connections reconciled for
`u-01kxm1vszd8hwp7em418asq8h9` and `u-tiny`). Record and boundary:
`2026-08-29-codex-subject-migration-boundary.md (archived in git)`. Delete this paragraph on the
next host-actions pass.

---

## Legacy pre-credential data in `/data`

---

## Blocking a proof path

### Decide what the $20 actually buys — blocks live activation

*Found 2026-08-28 while preparing Stripe to go live. Full finding:
`docs/concerns/2026-08-28-the-paid-tier-buys-nothing.md`.*

Nothing outside the billing module reads the subscription tier. There is no metering, no
quota, no paid-only capability. **A user who pays $20/month today receives a flag in a
SQLite table.** Every other blocker is mechanical; this one is a decision.

Billing shipped without the metering half of the plan you approved — that half is PR
**#2598**, still open, and it also carries a stale copy of the billing code that has since
been corrected, so it needs extracting rather than rebasing whole.

**Two questions only you can answer:**

1. **What is the free monthly effect allowance?** You said "materially more than 50."
   Marginal cost is ~$0.12/user/month, so cost is not the constraint — anchoring and abuse
   are. Give me a number and I will land the quota dark, flip it for one universe, and
   live-test it.
2. **Or launch without it** — sell the paid tier as supporting the project rather than as
   capacity. A legitimate choice, but it has to be a chosen one, and the pricing page must
   then say that is what it is.

Until one of those is answered, do not swap in the live key: the checkout would work and
the customer would get nothing for their money.

---

---

### Decide who may sign up — the platform is single-tenant by construction

*2026-08-28, from a cross-family multi-user review. Full finding:
`docs/concerns/2026-08-28-user-code-runs-in-process.md`.*

Stripe is live and provisioned. Four second-user blockers were found and fixed
(#2627 session fixation and world-readable tokens, #2629 source-approval gate, #2630
private-branch run bypass, #2632 write-ACL granting founder tier).

**One is not fixed, because it is not a bug — it is the architecture.** Universe code
runs `exec()` inside the daemon, with `os.environ` and the data dir reachable. So anyone
who can get source approved can read the live Stripe key, every credential vault, every
other user's session token, and can write the database that decides who has paid.

#2629 bounds **who** may approve source (an allowlist, currently just your universe). It
does not bound **what** approved code can do. Encryption would not help: the key lives in
the same process.

**So the decision is yours, and it is not technical:**

1. **Open checkout to the public** — accept that a paying stranger who gets source
   approved owns the deployment. Only sane if the approval allowlist stays exactly one
   universe forever, which makes the paid tier a promise you cannot keep.
2. **Keep checkout closed; subscribe yourself** — everything works, single-founder is the
   threat model it was built for, and it is materially stronger than yesterday.
3. **Build the boundary first** — run universe code in a real OS/container sandbox, or
   move credential custody into a separate process the graph cannot reach. That is the
   work that makes "real users" mean what it sounds like.

I would do (2) now and (3) next. (1) is the one I would not do.

---

### Confirm the free allowance, then I flip metering on

*The `$20` question, now with a concrete proposal rather than an open one. Quota code is
PR #2618, landed dark. Finding:
`docs/concerns/2026-08-28-the-paid-tier-buys-nothing.md`.*

You said the free tier should be "materially more than 50 effects/month". The defaults
already in the code are far more generous than that, and cost says they can be:

| | free | paid ($20/mo) |
|---|---|---|
| effects | **100 / day** (~3,000/mo) | **5,000 / day** |
| window | rolling 24h | rolling 24h |

An *effect* is one thing that reaches the outside world and succeeded. Reads, writes,
edits, retries, and failed attempts cost nothing — that was the bug that started this,
where fifteen failed 401s ate the budget the successful post needed.

Why this generous: marginal cost is ~$0.12/user/month, and the platform supplies no
inference. Cost is not what should constrain the free tier; abuse reaching the outside
world is, which is why effects are metered and runs are not.

**Say "yes" and I will:** flip `TINYASSETS_USAGE_ENFORCEMENT=1` for one universe, live
-test that a real post still goes through and that the cap actually stops the next one,
then roll it out. **Say a different number and I will use that instead** — they are env
vars, not a rebuild.

Nothing is enforced until you answer; the meter is recording either way, which is how we
will know what real usage looks like before the number matters.

---

### Phone app — send the first message and see it answered

*Live and proven on the founder's S24+ via adb (2026-08-22): OpenAI link completes, PONG inside the
sandbox on the founder's own subscription. Shipped as #2466 (FGS vs the Android freezer), #2467
(auto serving binding), #2468 (codex sandbox tmpfs home + systempaths/cap_drop), #2469 (HttpOnly
refresh); APK `android-latest`.*

What is missing is one real message from the founder, answered. Everything up to that is verified.

Owed alongside it: codex refresh-token persistence across a rotating turn, and the deploy step must
install `deploy/compose.yml` -- the droplet was patched in place, so a container recreate loses it.

Recovered 2026-08-27 from PR #2463, which carried it on the retired board and had no other home.

---

### First-class Voice — stop before microphone acceptance after browser speech deploys

**Do not send a credential in chat and do not buy a platform key.** Voice must keep using the same
authenticated home universe and canonical `converse` writer that already answers typed chat. Host
credentials, maintainer accounts, another user's connection, platform-paid usage, and a silent
provider substitute cannot unlock it or count as acceptance.

The 2026-09-04 signed-in acceptance result supersedes the earlier capability receipts: typed chat
worked through the user's connected ChatGPT authority, while Voice asked to connect again and then
reported that the connection was unsupported. The root cause was treating an optional
`tinyassets.voice.v1` WebRTC bridge as a prerequisite for speech. A subscription CLI connection
does not document that bridge, and a ChatGPT subscription must not be interpreted as external
Realtime API entitlement. Asking for another provider or credential is therefore wrong.

The product fix separates conversation authority from speech transport. When the browser or device
exposes speech recognition and synthesis, Voice captures one final transcript, submits it exactly
once through the same authenticated canonical `converse` path as typed chat, renders the exact
reply, and reads that reply aloud. TinyAssets receives recognized text but no microphone bytes.
The browser vendor or device speech service may process recognition remotely and may not work
offline; the disclosure says so before microphone access. A compatible `tinyassets.voice.v1`
bridge remains an optional richer transport, not a reason to reconnect an already-working writer.

After the fix is merged and deployed, open the authenticated app in the exact browser/device that
failed. A working conversation plus supported browser speech must render `Voice` without
`Voice · Connect`; the first tap may show the browser-speech disclosure but must stop before
microphone permission for Jonathan's explicit bounded physical-device proof. An unsupported
browser must name that browser/device limitation while typed chat remains usable. No host file,
developer bypass, new credential, provider switch, or platform-funded usage counts. The proof
order, stop conditions, and evidence packet are in
`docs/ops/realtime-voice-mobile-handoff.md`.

Stop for Jonathan at the rendered disclosure or honest unsupported-browser state before starting
microphone acceptance. The app must derive the current binding; do not ask him to name it or
reconnect it manually. Running the live microphone acceptance and releasing Voice beyond that
bounded proof remain explicit founder decisions.

---

## Credentials and accounts

### Mint the PAT that unblocks the deploy chain

App-merged PRs raise no `push` event, so `build-image` and `deploy-prod` never fire; human-merged
ones do (measured: **#2259 vs #2260**). Mint a fine-grained PAT with **PRs + Contents write**, add it
as an Actions secret, and point `.github/workflows/auto-enroll-merge.yml` at it.

Background: `docs/decisions/ADR-004-merge-attribution-and-the-deploy-gap.md`.
This is the mechanism behind Hard Rule 14 — five PRs merged 2026-07-21 and none reached production.

### Set `Contents: Read and write` on the PAT deposited in the universe's vault

**The whole ask: change one dropdown from Read-only to Read and write.** No new token, no re-paste
— the key already in the vault keeps working.

This is a *different* token from the one above: it is the fine-grained PAT the founder pasted into
their own universe on 2026-08-28 so the agent could open a PR itself. It is live and correctly
scoped to the repo; only the Contents permission is short.

**GitHub itself names the missing permission.** Captured 2026-08-28 19:14 UTC from the raw
effect-evidence map of a live `authenticated_external_call` run (`delivered: true` — the call
reached GitHub and was refused there, not by us):

```
POST /repos/tinyassets/tinyassets/git/refs   ->   403
{"message":"Resource not accessible by personal access token",
 "documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference"}

x-accepted-github-permissions:      contents=write; contents=write,workflows=write
github-authentication-token-expiration: 2026-09-27 03:07:38 UTC
```

`x-accepted-github-permissions` is GitHub stating the requirement outright: **`contents=write`**.
The expiry header proves the token is live and not expired. `GET`s on the same repo succeed —
it reads `main`'s ref and the full `app.html` — which is what proves the repo is selected.
`Pull requests: Read and write` is already set and does **not** cover `POST /git/refs`; branch
creation, and `PUT /contents/...`, are both Contents writes.

Everything on the platform side is already open and was verified the same day: the connection
exists with `POST /git/refs` allowed, effector consent for destination `github` is granted and
unrevoked, and `TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED` is `1` in the running daemon.
(`TINYASSETS_GITHUB_OUTBOUND_VIA_CONNECTION` is also set there but gates no code: GitHub is an
ordinary connection, and the flag was dropped from `apply-daemon-env.yml` on 2026-09-24.) This
one dropdown is the only remaining gate. The same token expires 2026-09-27 03:07 UTC, so a
fresh one with Contents write may be simpler than editing this one. **Since PR #3967 the
reconnect must also declare `git_host: "github.com"`** (the platform no longer maps
`api.github.com` to `github.com`): remove the `github` connection, then answer the universe's
connect ask that carries it. Without it, workspace clone/push goes to `api.github.com` and 403s.

*Blocks:* the founder's standing goal that the universe push a PR end-to-end to deployed.
*Where:* GitHub → Settings → Developer settings → Fine-grained tokens → this token →
Repository permissions → Contents.

### Create the WorkOS native/public client

PKCE S256, no secret, exact variable-port `127.0.0.1` loopback redirect, offline access, plus the
`WORKOS_HOST_BINDING_RESOURCE` audience.
*Depends on:* test-identity landing; #1753 tasks 1.3 / 3.2. *Where:* WorkOS dashboard.

### Register the `TinyAssets` ChatGPT connector as workspace admin

At `https://tinyassets.io/mcp`.
*Depends on:* canonical `/mcp` live and `/mcp-directory*` absent. *Where:* OpenAI workspace admin.

### Supabase infra for the market workflow

Live read access, plus the prod migration-home decision (transport 2.5 / 2.6); a prod-shaped Realtime
env (5.3); an isolated launch-region project (5.4). *Where:* Supabase account.

---

## Approvals and decisions

### Approve an isolated canonical `/mcp` baseline environment + traffic envelope

Provider-free, no maintainer quota, test identities with cleanup, canary-coordinated.
*Depends on:* `harden-production-load-evidence`; `test-identity-and-reset`.

### Authorize the reciprocal public-read / manifest Worker delta edge

One merged front-door body before public-read sync.
*Owner artifact:* `openspec/changes/archive/2026-08-26-reconcile-external-connector-manifests/tasks.md`.
*Depends on:* current owner handoff; public-read task 0.5.

### Review BYO-LLM connect flow slice 1

Round-3 credential-snapshot filesystem fixes verified; **no merge, no deploy** without this review.
*Depends on:* exact-head dual-family review; POSIX/production Codex integration.
*Owner artifacts:* `openspec/changes/archive/2026-10-06-byo-llm-connect-flow/`,
`openspec/changes/archive/2026-08-26-constrain-set-engine-provider-authority/`.

---

## Host-recorded evidence

### Connector tool-selection accuracy — baseline and regression decision

Instrument landed in #1776; **no agent-buildable task remains** (triaged 2026-08-02). The host
records the claude.ai baseline (task 3.1) and the permitted-regression decision (task 3.2).
*Depends on:* ChatGPT connector registration, before its baseline.
*Owner artifact:* `openspec/changes/archive/2026-08-26-connector-tool-selection-accuracy/`.

### Cloud drain activation — dark deploy, cutover, and 24/7 proof

Implementation landed; the dark deploy/canary, single-active cutover, and 24/7 PC-off proof remain.
*Owner artifacts:* `2026-08-03-cloud-drain-epoch2-consumer.md (archived in git)`,
`openspec/changes/archive/2026-08-26-activate-main-universe-spec-drain/tasks.md`.

> **Changed 2026-08-25.** This row carried the dependency *"keep local drain until reviewed cloud
> health; never activate both claimers."* The local drain supervisor was deleted in the harness
> reset (it was an autonomous background worker, which the host's two-provider decision put out of
> scope). There is no longer a second claimer to conflict with — but that also means **there is no
> local fallback** if cloud activation stalls. Decide with that in mind.

---

### X app is Read-only — flip it to Read and Write

**The smallest ask:** in the X developer portal, open the app behind connection
`http_7f4a2d48423c003f5bb31b127468606c` → *User authentication settings* → set
**App permissions** to **Read and write** → then **regenerate the access token
and secret** and re-deposit them. The permission is baked into the token at
issue time, so an existing token keeps `read` access even after the app setting
changes; without the regenerate step this looks unfixed.

**Why it is a host action:** it is a setting in your X account. Nothing in this
repo can change it.

**Reproduced 2026-08-27 through the webapp**, driving `tinyassets.io/app`
as the signed-in founder rather than the MCP — run `948a32670485432a`, same
branch, same result. Two things that run additionally rules out:

- **Not throttling.** `x-rate-limit-remaining: 39999` of `40000`.
- **Not the wrong connection.** The universe enumerated every saved connection:
  `webhook:test`, `x:posting`, and the GitHub PR writer. There is exactly one X
  connection and it is the read-scoped one, so there is no alternative
  credential to try.

X names the fault itself in the response body:

```json
{"detail": "Your client app is not configured with the appropriate oauth1 app permissions for this endpoint.",
 "status": 403, "title": "Forbidden", "type": "https://api.x.com/2/problems/oauth1-permissions"}
```

**Original evidence, 2026-08-27** — run `c2b486ff315045c6`, branch `8ab6516d50c5`
("X Hello World via Codex v2"), production `44c4e205`:

```
status: completed          deliver_post: ran
POST https://api.x.com/2/tweets  ->  403 Forbidden
"Your client app is not configured with the appropriate oauth1 app
 permissions for this endpoint."
x-access-level: read
```

**What this unblocks, and what it proves.** The platform half is done. That run
authenticated, resolved the grant, built the packet, and made a real outbound
POST — the 403 is X refusing the *token's* scope, not TinyAssets failing. The
same branch failed three different ways earlier the same day, all ours and all
now fixed and deployed:

| When | Failure | Fixed by |
|---|---|---|
| 08-25/26 | `permission_denied:provider_not_bound` | #2559 |
| 08-27 am | `provider invocation usage could not be settled` | #2582 |
| 08-27 am | async sub-branch refused, run FAILED before any node | #2586 |

So this row is the last thing between the founder and a posted tweet, and it is
the only one that was never a code problem.

**How to verify after changing it:** re-run branch `8ab6516d50c5`. Expect
`external_write_results.deliver_post.authenticated_external_call.response.status`
to be 201, and `x-access-level` to read `read-write`.

---

## Firebase project for phone notifications (2026-09-30)

**Why:** a universe's "Waiting on you" requests now push to the owner's devices
(`openspec/changes/notify-owner-of-requests`). Browser and desktop push need
nothing from you — web-push keys are self-issued. **Android push needs a Firebase
project only the account owner can create**, and the Android app (1.0.4, the
first build that carries push) reads two secrets that come out of it. Until they
exist, 1.0.4 still builds and runs: the build logs `push DISABLED` and the
"Request notifications" switch in the phone app says notifications aren't set up
yet.

**Steps, one browser session** (an agent can drive 1-3 in your signed-in browser
if you say so; step 4 mints a private key, so it waits for your explicit go):

1. `console.firebase.google.com` -> **Add project**. If the Google Cloud project
   behind `io.tinyassets.app` is already listed, choose **Add Firebase to an
   existing Google Cloud project** rather than creating a second one. Decline
   Google Analytics when asked — the app has none and the Play Data safety /
   Advertising ID answers assume that.
2. In that project -> **Add app -> Android**, package name exactly
   `io.tinyassets.app` (nothing else is needed; skip the SDK steps) ->
   **Download `google-services.json`**.
3. **Project settings -> Cloud Messaging**: confirm *Firebase Cloud Messaging API
   (V1)* shows **Enabled** (it is on by default for new projects). The legacy
   server-key API is not used and stays off.
4. **Project settings -> Service accounts -> Generate new private key** -> keep
   the JSON it downloads.

**Where each file goes** (names are exact; nothing is committed, this repo is
public):

| File | Secret name | Read by | Where it lives |
|---|---|---|---|
| `google-services.json` | `ANDROID_GOOGLE_SERVICES_JSON_B64` (base64 of the file, one line) | the **Android build** — `mobile/scripts/materialize_google_services.py` in `android-release.yml` | GitHub repo secret. For the container build instead: save the file as `~/.tinyassets/android/google-services.json` (it is mounted at `/keys`, or name it with `ANDROID_GOOGLE_SERVICES_JSON_FILE`). |
| service-account JSON | `TINYASSETS_FCM_SERVICE_ACCOUNT_JSON` (the whole document, compacted to ONE line, e.g. `python -c "import json,sys;print(json.dumps(json.load(open(sys.argv[1]))))" key.json`) | the **server** — `tinyassets/notify/fcm.py`, via FCM HTTP v1 | the vault, then a line in `/etc/tinyassets/env` on the droplet (the daemon container's `env_file`); redeploy/recreate to pick it up. Vault only — never a committed file, never a workflow literal. |

`google-services.json` is not a server credential (its API key is restricted to
the app), but it is build input and stays out of git. The service-account JSON
**is** a credential.

**Then:** build 1.0.4 with both in place (the build log should say
`push ENABLED ... project <id>`), sign and upload it the usual way, and turn
notifications on in the phone app's Account page. Proof is a request raised by
your universe arriving on the phone, and tapping it opening that request.

**Play Console, with 1.0.4:** add **Device or other IDs** (the FCM registration
token; optional, functionality only) to the Data safety form — see
`docs/ops/google-play-launch.md` §6. Advertising ID stays **No**.

---


## Verify AuthKit client/family claims before outside-client cutover (K1, 2026-10-06)

Draft PR #4519 contains the #4501 verified-claims probe and scoped authority code.
Run scripts/outside_client_claims_probe.py against real independently connected
clients and a fresh interactive reconnect. Establish the verified resource-token
client claim, credential-family claim and auth_time semantics; no brand, audience,
header or unverified token is acceptable evidence. Keep the probe receipt free of
bearers. Configure TINYASSETS_OUTSIDE_CLIENT_CLAIM and
TINYASSETS_OUTSIDE_FAMILY_CLAIM only from this evidence; explicitly list verified
first-party issuer|client pairs in TINYASSETS_FIRST_PARTY_CLIENTS. Existing outside
clients need owner grants through POST /app/outside-clients before cutover. The
durable OutsideClientAuthority switch defaults denied; TINYASSETS_OUTSIDE_DENY=1
can only deny. Prove independent client revoke and first-party survival live.
This account-only evidence does not block the main-compatible K1 code or wait on U1.
