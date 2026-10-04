# Host actions and decisions

Work that **only the founder can do** — because it needs an account, a dashboard, a credential, or a
judgment call no agent is authorized to make. Migrated from the `STATUS.md` Work table on
2026-08-25 when the board was retired.

Everything else that was on that board lives in `openspec/changes/` (the queue) or
`docs/concerns/` (unresolved findings). This file exists because those two homes can't hold an item
whose next step is *"the founder logs into Cloudflare."*

**Delete a row when it's done.** Git holds the history.

---

## Delete the GitHub OAuth App (2026-10-03)

**Why:** `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET` are removed from the
template, the deny-lists and the host env — nothing read them and no route serves the
callback they described. Two things the code change cannot do:

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

## Store launch: four founder steps (2026-09-29)

Both stores are one founder action away from moving. Apple asked for more information
(Guideline 2.1) and has build 3; Play approved build 4 on the closed track. The map is
`docs/ops/mobile-launch-handoff.md`. The four steps are independent, so do them in any
order. Only the Play one is on a 14-day clock, so it goes first.

### Google Play: opt in and recruit the 12 testers

1. On your phone's Google account, open `https://play.google.com/apps/testing/io.tinyassets.app`.
   This is Play's standard opt-in address for the closed (Alpha) track. Play Console →
   **Test and release → Closed testing → Alpha → Testers → Copy link** shows the
   authoritative one. Tap **Become a tester**.
2. Add 15 to 18 people's Google-account emails to the `Founder devices` tester list on
   the same page. Only add people who said yes. The extra 3 to 6 cover drop-offs.
3. Send each of them the opt-in link, using the invitation text under "start the
   12-tester closed test" below.

The clock starts when 12 people are actually opted in, and it runs for 14 days. Tell the
lead the day it starts. The full engagement plan is in that section below.

### Apple: turn on Sign in with Apple

**Why:** the sign-in page offers **Continue with Google**. Apple Guideline 4.8 requires
any app offering Google sign-in to also offer an equivalent private login, and Sign in
with Apple is it. TinyAssets' own email/password does not count while Google is offered
too. Apple has not cited this yet, but a full review would. Sign-in is the hosted WorkOS
page, so this is dashboard setup only: no app rebuild, and build 3 stays as submitted.

You need two browser tabs. In both, stay in the **Production** environment on the WorkOS
side.

1. **WorkOS** (`dashboard.workos.com`, Production) → **Authentication → OAuth providers
   → Sign in with Apple → Enable**. Leave the dialog open. It shows a **Redirect URI**
   and **Outbound email domains**; you paste both into Apple below.
2. **Apple Developer** (`developer.apple.com/account` → Certificates, IDs & Profiles):
   1. Note the **Team ID** shown under your name (top right).
   2. **Identifiers** → `io.tinyassets.app` → tick **Sign in with Apple** (leave it
      as *Enable as a primary App ID*) → **Save**. If Apple warns that profiles will be
      invalidated, accept. Build 3 is already signed and is unaffected. Only a *future*
      iOS build needs its profile regenerated, and the agent will ask when that comes up.
   3. **Identifiers → +** → **Services IDs** → Description `TinyAssets Sign In`,
      Identifier `io.tinyassets.signin` → **Register**. Open it, tick **Sign in with
      Apple → Configure**: Primary App ID `io.tinyassets.app`; Domains and Subdomains
      `api.workos.com`; Return URLs = the WorkOS **Redirect URI** from step 1 →
      **Done → Continue → Save**.
   4. **Keys → +** → Key Name `TinyAssets Sign in with Apple`, tick **Sign in with
      Apple → Configure** → `io.tinyassets.app` → **Save → Continue → Register**.
      Note the **Key ID** and click **Download**. Apple allows only one download.
   5. **Services → Sign in with Apple for Email Communication → Configure → +**. Enter
      the WorkOS **Outbound email domains** from step 1 → **Next → Register**. Without
      this, users who choose *Hide My Email* never receive TinyAssets email.
3. Back in the **WorkOS** dialog, choose **Your app's credentials** and enter: Apple Team
   ID = the value from 2.1, Apple Service ID = `io.tinyassets.signin`, Private Key ID =
   the Key ID from 2.4, Private Key = open the downloaded `AuthKey_<KeyID>.p8` in Notepad
   and paste its whole contents. Toggle **Enable** on and save.
4. **Where the key file goes:** Control Panel → **Credential Manager → Windows
   Credentials → Add a generic credential**. Internet address `TinyAssets Apple SIWA
   key`, user name = the Key ID, password = the whole `.p8` contents. Then delete the
   `.p8` from Downloads and empty the Recycle Bin. Never paste it into chat or commit it.
   The Team ID, Services ID and Key ID are not secret and can be sent to the lead.
5. Tell the lead it's done. The agent runs `python scripts/authkit_login_parity_probe.py`
   (it fails today, exit 1, and passes once Apple is offered). Then it checks one real
   **Continue with Apple** sign-in on `https://tinyassets.io/app`.

### Apple: renew the App Review inference key before 2026-10-10

The dedicated App Review account (`play-review@tinyassets.io`, password in Windows
Credential Manager) answers through a review-only OpenRouter key that **expires
2026-10-10**. If Apple reviews after that date, the reviewer signs in to a universe that
cannot reply, which is a certain rejection. The key must be renewed before resubmission.

1. **OpenRouter** (the account that owns the current review key) → **Keys → Create
   key**. Name `tinyassets-app-review`, credit limit **$5**, expiry at least
   2026-12-31. Copy the key; do not save it anywhere else.
2. In a private browser window, sign in to `https://tinyassets.io/app` as the review
   account. Open **Connect**, choose OpenRouter, paste the key into **Paste only the
   key**, and tap **Connect**.
3. Send one message, for example "What can you help me with?", and confirm a reply
   appears. This also proves the reviewer universe still answers after the September
   prune.
4. Back in OpenRouter, delete the old review key. Tell the lead the new expiry date.

### Apple: record the review video on a physical iPhone

This is Apple's actual ask (Guideline 2.1). Install build 3 from the TestFlight invite
already sent to you, on an iPhone updated to the latest iOS. Record the six steps in
`docs/ops/app-store-submission-packet.md`, "Guideline 2.1 response packet". A simulator
recording is refused. Hand the `.mov` to the lead. The agent attaches it with the written
answers and resubmits only after the lead's explicit go. Do the key renewal above first,
or step 4 of the recording (a real reply) will fail after 2026-10-10. Full history is item
12 under "Apple App Store: enroll" below.

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
`docs/reviews/2026-08-29-codex-subject-migration-boundary.md`. Delete this paragraph on the
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

### Google Play: `WORKOS_API_KEY` reaches the daemon — VERIFIED 2026-09-02, nothing for you

Account deletion removes the user's WorkOS record through the management API, and that
upstream deletion is also what ends sessions on other devices (their refresh handles are
opaque to the daemon). The key is not in the repo or in `deploy/`, so I checked the host:
`/etc/tinyassets/env` carries exactly one `WORKOS_API_KEY=` line, and
`docker exec tinyassets-daemon printenv WORKOS_API_KEY` returns an `sk_`-prefixed value of
the expected length. So a real deletion will remove the sign-in identity, which is what
`/legal` and `/account` promise.

If that ever stops being true, deletion still removes every byte of the user's data and
reports `identity: not_configured`, tells the user, and writes a receipt under
`.account-deletions/` — check it with
`python -c "from tinyassets.account_deletion import pending_deletions; print(pending_deletions('/data'))"`.
Delete this paragraph on the next host-actions pass.

### Apple App Store: enroll — signing and TestFlight cannot start without it

**Standing founder authorization (2026-09-03):** drive all ordinary Apple-required
setup and completion steps for the iPhone Store objective without repeated approval,
including required account/app attestations, API access and least-privilege credentials,
signing/provisioning, builds, verified metadata/privacy answers, screenshots, uploads,
TestFlight validation, App Review submission, and release/publication. Interrupt only
for a new monetary charge, an irreversible destructive action, a material choice Apple
does not require, or personal/legal facts that cannot be established truthfully from
verified project/account evidence. Computer-use actions that policy requires to be
confirmed at action time still follow that higher-level confirmation rule.

**Checked 2026-09-03, not assumed.** The account holder completed Apple's official
creation form, but the final step returned only **"Your account cannot be created at
this time."** There is no field-specific email, phone, birthday, country, or password
validation on the page, and no new Apple email arrived. Apple Account and iCloud Sign-In
were available on Apple's System Status page, so this is not a documented system-wide
outage. The visible evidence cannot distinguish a temporary server-side rejection from
a browser/network-specific rejection; it does not support claiming a phone-reuse,
region, locked-account, or device-limit cause. Apple Support then reported making an
unspecified change on its side and asked for one new creation attempt. After the account
holder retried, the browser reached the signed-in Apple Account **Sign-In & Security**
page and showed two-factor authentication with a trusted phone number. Account creation
is therefore complete. The account holder confirmed that result and closed the completed
Apple Support chat.

Everything autonomous on the iOS build side is staged — the Capacitor platform,
`tinyassets://` URL-scheme patch, native TinyAssets artwork, unsigned compile-check,
manual signed-IPA workflow, opt-in TestFlight upload, and listing/App-Privacy copy.
None of it can produce an installable app without account-owned signing material.

1. **Complete — Apple Account created and verified (2026-09-03).** The signed-in account
   page shows two-factor authentication and a trusted phone number. The account holder
   also accepted the Apple Developer Agreement and declined optional developer-news email.

2. **Complete — enrollment purchased and membership activated (2026-09-03).** The account
   holder completed personal information and Secure Checkout. The signed-in developer portal
   now shows program resources, a Team ID, and a 2027 renewal date. The Apple Developer
   Program License Agreement and Apple Developer Agreement both show accepted on 2026-09-03.
   At that checkpoint no signing assets or App Store Connect API credentials existed.
3. **Complete — explicit App ID and App Store Connect record created (2026-09-03).** Verified in the
   signed-in Apple Developer browser at `/account/resources/identifiers/list`: the
   Identifiers table shows `TinyAssets iOS` / `io.tinyassets.app`. App Store Connect
   Terms of Service V100 (last updated 04 June 2018) was accepted by the founder.
   The founder then confirmed record creation. Apple ID `6808434444`. Product
   metadata are saved. Build 3 is attached to
   the `Internal` TestFlight group, selected for App Store Version 1.0, and its
   en-US **What to Test** and beta app description are saved. The group has manual
   Xcode-build distribution, one Account Holder tester, and a live invitation. The free price
   schedule is confirmed; current submission and availability state is recorded
   in items 10–12 below.
4. **Complete — signing, profile, and CI upload credentials (2026-09-03).** The active
   Apple Distribution certificate expires 2027-09-03 and is paired with an exportable
   private key. The active `TinyAssets App Store 2026` profile is App Store type for
   `io.tinyassets.app`, contains that one certificate, and expires 2027-09-03. The
   encrypted P12 and password, verified profile, and Developer-role `TinyAssets CI Upload`
   App Store Connect API key are present as all six secrets named in §3.
   The protected GitHub environment `app-store` is complete: founder approval is required,
   and only `main` may deploy. You do NOT need a Mac — CI builds on `macos-15`.
   On 2026-09-10 a separate App Manager team key named `TinyAssets Release` replaced
   the protected API key ID/private-key secrets and was backed up in Windows Credential
   Manager. Protected verify-only run `34562826944` proved the replacement key against
   Apple's retained App Review account and contact record. The private key remains outside
   this repository. Receipt:
   `docs/audits/2026-09-10-ios-review-recovery-access.md`.
5. **Complete — Xcode 26 build accepted and processed by TestFlight (2026-09-03).**
   Exact fix revision `6ccb3d24` received a Claude Opus **AGREE** review and landed in
   PR #2798 as `76d795a1`. Every exact-head PR check passed, including `build-ios` and
   `required-tests`. Protected run `33827279907` produced signed build 1.0.0 (3);
   Apple reported no upload errors; App Store Connect matched its delivery UUID and
   now shows Build 3 as **Ready to Submit** under Version 1.0.0. Receipt:
   `docs/audits/2026-09-03-ios-testflight-upload-receipt.md`.
6. **Complete — authenticated TestFlight/App Store metadata (2026-09-03).** The
   founder reauthenticated, after which the beta app description and marketing URL
   were saved, Build 3 was selected for App Store Version 1.0, and the free price
   schedule was confirmed. On 2026-09-10 the release mode was changed to automatic
   after approval. The Account Holder was added to the internal group and Apple now
   shows one tester, one build, and an **Invited** state for Build 3. Receipt:
   `docs/audits/2026-09-03-ios-testflight-preparation-receipt.md`.
   Recovery update: `docs/audits/2026-09-10-ios-review-recovery-access.md`.
7. **Complete — age rating and tested-platform scope (2026-09-03).** The live
   questionnaire is saved at 18+ (19+ in Korea; earlier operating systems show
   17+ with Apple's regional exceptions). Untested Apple Silicon Mac and Apple
   Vision Pro availability are disabled and saved. Storefront availability is
   still unset. Factual App Review Notes describing the pinned Capacitor shell,
   absent commerce/advertising/voice UI, and critical review paths are also saved;
   no reviewer credential or personal contact detail was persisted at that point.
8. **Complete — required screenshots (2026-09-03).** Manual macOS CI run
   `33839432494` built the simulator app from Build 3's exact source, captured and
   validated `1284x2778` iPhone and `2064x2752` iPad images, and both images were
   visually inspected and uploaded. App Store Connect shows `1 of 10 Screenshots`
   in each required set after reload. Receipt:
   `docs/audits/2026-09-03-ios-app-store-screenshot-preflight-receipt.md`.
9. **Complete — reviewer identity and contact block (2026-09-09).** The dedicated
   `play-review@tinyassets.io` password was rotated in WorkOS, saved in Windows
   Credential Manager and the protected `app-store` environment, then verified by
   protected run `34330379943`. Apple retained the matching sign-in requirement,
   reviewer username/password, and all four contact fields. No password or phone value
   is stored in this repository.
10. **Complete — US-first availability and DSA declaration (2026-09-09).** The 27 EU
   storefronts are **Not Available**, automatic future-country availability is off,
   and the United States remains **Available on App Release** within 148 enabled
   storefronts. TinyAssets' app-specific DSA status is saved as non-trader for this
   non-EU initial release.
11. **Complete — live privacy publication (2026-09-09).** PR #3616 landed as
   `caccc05c`; Pages deployment run `34334601760` succeeded; the live
   `https://tinyassets.io/legal/#privacy` page was reloaded and verified; and App
   Store Connect published the four-type privacy disclosure. Review receipt:
   `docs/audits/2026-09-09-ios-privacy-publication-review.md`.
12. **Rejected for information — Apple App Review (2026-09-09 17:56 PDT).** TinyAssets
   iOS 1.0, build 1.0.0 (3), is **Rejected** with submission state **Unresolved Issues**.
   Apple's only cited issue is **Guideline 2.1 - Information Needed - New App Submission**:
   the account's limited review history triggers a latest-iOS physical-device recording
   plus purpose, audience, access, service, regional, and regulated-content answers.
   Submission ID `5c6e4844-2ca2-438c-8aec-a189efb0ebb2`; release is now automatic after
   approval. Do not
   cancel the submission. The reusable response packet is in
   `docs/ops/app-store-submission-packet.md`; receipt:
   `docs/audits/2026-09-09-ios-app-review-submission-receipt.md`.

   **Smallest founder-only action:** accept the live TestFlight invitation for the
   Account Holder on a physical iPhone updated to the latest iOS, install Build 3, and
   capture the six-step recording in the response
   packet, then make the `.mov` available to this task. Apple explicitly requires a
   physical device, so a simulator recording must not be substituted. App Store Connect
   sign-in/2FA is also required whenever the visible session expires. All written answers,
   Notes-field preparation, attachment, and resubmission remain agent-owned, subject to
   the required action-time confirmation before sending the reviewer reply.

   **Prepared 2026-09-10:** the dedicated App Review account is already provisioned with
   a capped, expiring, zero-cost inference connection and completed a rendered production
   turn. Protected read-only run `34522734323` then reverified Apple's retained reviewer
   account/contact block and unchanged `UNRESOLVED_ISSUES` / `REJECTED` state at 12:50 PDT.
   PR #3830 / merge `f497050f6586ae70f41e98f78c412932176a46c7` is deployed and
   verified: Capacitor shells keep Voice hidden and uninitialized, the browser client
   retains Voice, and protected deploy run `34523794549` passed the public canary and
   exact-revision receipt gate. Build 3's defensive microphone usage string remains in
   the binary; the submitted reachable UI and declared review scope remain voice-dark.
   The reviewer needs no provider setup or payment details. The only outstanding founder
   action in this lane is the physical-device recording (and Apple sign-in/2FA when the
   visible App Store Connect session has expired).

   **Prepared 2026-09-10, access recovery:** the App Manager `TinyAssets Release` key
   is protected and live-verified by run `34562826944`; TestFlight retained its feedback,
   privacy, contact, and demo-account details; the Account Holder tester is **Invited**
   with Build 3 attached; and App Store Version 1.0 is `AFTER_APPROVAL`. No beta-review
   submission was started. Receipt:
   `docs/audits/2026-09-10-ios-review-recovery-access.md`.

**Apple preflight evidence (2026-09-09):** **Add for Review** succeeded after
Content Rights, published privacy information, screenshots, reviewer credentials and
contact details, storefront availability, and DSA status were complete. **Submit for
Review** then succeeded and App Store Connect reloaded to **Waiting for Review**.
Apple's 2026-09-09 Guideline 2.1 information request supersedes the earlier wait state.

### Google Play: start the 12-tester closed test — this is the 14-day clock

**This is the long pole for Play, and only you can start it.** A personal developer
account cannot apply for production access until it has run a **closed test with at
least 12 testers opted in continuously for 14 days**. The 14 days are wall-clock:
nothing an agent does shortens them, and the clock does not start until the testers are
actually in. Every other Play item finishes in hours; this one finishes in a fortnight,
so starting it late is what sets the public launch date.

Alongside the Apple enrolment above, this is one of **two clocks worth starting today**.
They run in parallel and neither depends on the other.

What it needs from you:

1. Recruit **15–18 people with Google accounts** so ordinary drop-off cannot take the
   continuously opted-in count below 12. Ask for a clear yes before adding an address;
   do not place personal email addresses in this repository.
2. Play Console → **Test and release → Testing → Closed testing** → create one track.
   Prefer a dedicated Google Group because membership can be maintained without
   rewriting the release; an email list is acceptable if that is simpler.
3. Promote the already phone-verified bundle (or a strictly newer version code), copy
   the opt-in link, and send the invitation below. The 14-day clock begins only after
   at least 12 people have actually opted in, not when invitations are sent.
4. Keep a private tracker with invitee, consent, opt-in confirmed, install confirmed,
   Android/device, three task results, feedback, and opt-out date. Check the Play
   tester count daily and recruit replacements early. Never remove a tester during the
   window unless they ask to leave.

Suggested invitation (send only after the closed-track link exists):

> TinyAssets is running a private 14-day Google Play test. Please open **[opt-in
> link]** while signed into the Google account you gave me, tap **Become a tester**,
> install from Play, and stay opted in through **[end date and timezone]**. During the
> test, please try: (1) launch/sign-in, (2) open Connect and return without exposing a
> credential, and (3) send one ordinary test message if your account is configured.
> Report crashes, stuck screens, sign-in trouble, or confusing copy at **[private
> feedback route]**. Do not enter confidential or regulated information. You may leave
> at any time; tell me so I can replace the test slot.

Engagement plan — Google can reject a production-access application for insufficient
testing even when the count/duration minimum was met:

| When | Operator check | Tester request |
|---|---|---|
| Day 0 | Confirm ≥12 actual opt-ins in Play; save the start timestamp and expected end timestamp. | Opt in and install from Play. |
| Days 1–3 | Triage install/sign-in failures; replace drop-offs before the count falls below 12. | Complete launch/sign-in and one navigation task. |
| Days 4–10 | Review feedback plus Play crashes/ANRs; ship fixes only with a higher version code and re-smoke. | Exercise Connect return/cancel and an ordinary message where configured. |
| Days 11–13 | Confirm ≥12 remain opted in and all launch-blocking defects have dispositions. | Recheck the latest build and submit final feedback. |
| After 14 complete days | Capture the Console eligibility state before applying for production access. | No action unless asked to verify a fix. |

This does not require daily use from every person, but it does require a real,
representative test rather than twelve idle list entries. Google's current rule and
engagement guidance are at
<https://support.google.com/googleplay/android-developer/answer/14151465>.

After the 14 days: apply for production access, which Google reviews separately, and
only then can the app be promoted to Production and be publicly downloadable.

Do not confuse this with the internal-testing track already running — internal testing
does not count toward the requirement, no matter how long it runs.

### Google Play: reusable reviewer account — DONE 2026-09-03

Play Console -> App content -> **Sign in details** (formerly "App access"). Our app is
behind WorkOS AuthKit, so the honest answer to "Is any part of your app restricted?" is
**Yes** — the form's own Yes branch lists "Google Account sign in, and / or SSO", which is
exactly what we use. Google then warns, in the dialog itself:

> "If we can't review your app, you may be prevented from releasing updates, or your app
> may be removed from Google Play. Reviewers are unable to create accounts, **use their own
> existing accounts**, or use free trials to access your app. They are also unable to
> contact you for more information."

**Live identity reconciliation, 2026-09-03:** the WorkOS dashboard admin and sole
TinyAssets team member remains Jonathan Farnsworth, `jonathan.m.farnsworth@gmail.com`,
with the Admin role. Google Play Console is also signed in with that address.

Email + Password is **enabled** in the production WorkOS environment with the
recommended strong policy (10-character minimum, complexity score 3, breached-password
rejection). GoDaddy Email & Office now has the dedicated alias
`play-review@tinyassets.io` on the TinyAssets-controlled `info@tinyassets.io` mailbox;
that mailbox forwards to the founder's primary Gmail and keeps its own copy.

The dedicated AuthKit user **Play Reviewer** is `play-review@tinyassets.io`, WorkOS id
`user_01M1N3BFV6N1V1C9PP1NEWCCHP`. WorkOS shows Verified + Active, Email + Password,
no organization membership, no connected accounts, and sign-in count **2**. The two
password authentications reached the same isolated empty reviewer universe. After the
one-time address verification, the repeat sign-in required no MFA or email challenge;
the intervening Cloudflare human check was browser abuse protection, not an account
second factor. Both sessions were signed out after proof.

Play Console now shows **Sign in details** under Actioned, last edited 2026-09-03, with
the `Play Reviewer` credentials and observed instructions: sign in with Email + Password,
then choose **Skip for now** on the Connect screen to enter the empty reviewer universe.
No organization, integrations, connected accounts, or founder data are attached. The
optional Google/trusted-partner device feedback switch is off. The verified rotated
credential was saved and submitted for review on 2026-09-08. The actual password exists
only in Windows Credential Manager and Play's credential field and is intentionally
absent from this repository.

The mistaken `simkalholdingsllc+tinyassets-play-review@gmail.com` WorkOS user was
permanently deleted only after the correct alias, delivery path, two sign-ins, and Play
save were all proven. It never became the saved Play credential.

WorkOS documents that AuthKit supports Email + Password and that the hosted UI exposes
only the methods enabled in the dashboard
(<https://workos.com/docs/authkit/email-password>,
<https://workos.com/docs/authkit/hosted-ui>). Keep this reviewer as a dedicated password
user; do not attach a founder/personal provider credential. Re-check the saved credential
immediately before every submitted build. Google's reviewer-access requirements are at
<https://support.google.com/googleplay/android-developer/answer/15748846>.

**What it gated:** Sign in details, Target audience (**18 and over**), Data safety,
and the foreground-service declaration were submitted with the 15-change review batch
on 2026-09-08. Play approved and published the batch at 10:35 PM PT. Data safety lists
both password and OAuth account creation. Content
rating remains complete (IARC, submitted 2026-09-02, Everyone / PEGI 3 / USK 0 /
ClassInd L).

**This does not block a real install.** Play's internal testing track explicitly works
"before you've finished setting up your app" — the App content checklist gates *production*
access, not internal testing. The shortest path to the app being installable from Play by a
real person is the upload keystore below, not this section.

### Google Play: upload-keystore secrets — DONE 2026-09-04

Authenticated repository state rechecked 2026-09-08: all four
`ANDROID_UPLOAD_*` secret names are present. Values remain unreadable by design.

The Play developer account exists (identity verified 2026-08-24) and the upload keystore was
generated 2026-09-01 into `~/.tinyassets/android/` on your machine. The release workflow
signs with secrets an agent is not allowed to set (`gh secret set` is denied to it). Run, in
a Git Bash at the repo root:

```bash
D="$HOME/.tinyassets/android"
# `tr -d` is load-bearing: upload-keystore.env has Windows CRLF endings, so a
# plain `. "$D/upload-keystore.env"` puts a trailing carriage return on every
# value. A CR inside the secret makes the signing step fail with
# "Keystore was tampered with, or password was incorrect", which sends you
# hunting a corrupt keystore instead of a line ending. Verified 2026-09-03.
set -a; . <(tr -d '\r' < "$D/upload-keystore.env"); set +a
tr -d '\r\n' < "$D/tinyassets-upload.jks.b64" | gh secret set ANDROID_UPLOAD_KEYSTORE_B64
printf '%s' "$ANDROID_UPLOAD_KEYSTORE_PASSWORD" | gh secret set ANDROID_UPLOAD_KEYSTORE_PASSWORD
printf '%s' "$ANDROID_UPLOAD_KEY_ALIAS"        | gh secret set ANDROID_UPLOAD_KEY_ALIAS
printf '%s' "$ANDROID_UPLOAD_KEY_PASSWORD"     | gh secret set ANDROID_UPLOAD_KEY_PASSWORD
```

To confirm the secrets took, re-run the release workflow: the signing step prints
`upload certificate fingerprint verified` before it signs, and fails closed if the
restored keystore does not carry certificate
`D0:BC:F2:...:B2:11`. I have already verified that the keystore on disk carries
exactly that certificate, so a mismatch after this points at the secret, not the key.

**The alternative that needs no secrets at all.** I can build and sign the bundle
locally in a container (JDK 21, node 22, Android SDK 36) and upload the `.aab` to
the Console by hand — no GitHub secrets involved. That path is already working.
The secrets are still worth setting, because they are what makes *every future
release* one command instead of a manual build.

Then back the keystore up somewhere that is not this laptop.

What remains after the secrets, and who does it (`docs/ops/google-play-launch.md` §11):

| Step | State |
|---|---|
| Play Console: app created, declarations accepted | **done** 2026-09-02 |
| Store listing, graphics, privacy URL, Ads/Government/Financial/Health | **done** |
| Content rating (IARC) | **done** 2026-09-02 — Everyone / PEGI 3 |
| Data safety | **done** 2026-09-03; submitted for review with the 15-change batch on 2026-09-08 |
| Internal-testing tester list | **done** — "Founder devices" attached to the track |
| Build the signed AAB | **done** 2026-09-08 — merged source `ea3f1092`; GitHub workflow run `34297030257` built and signed code `4 (1.0.3)`. Downloaded AAB SHA-256: `d647d073ae62088c8dba0795b883d4449b31c5ab3eb37f2304adde949e352cc3`. |
| Internal-testing release: upload the AAB, roll out | **done** 2026-09-03 11:10 — release `1 (1.0)`, track Active, 3.1 MB |
| Corrected internal release `2 (1.0.1)` | **done** 2026-09-03 21:42 PT — merged source `bf432f1b2dbe`; signed AAB accepted; Play shows Active and Available to internal testers. |
| Verify the loop on a real phone (install from the internal-test link, sign in, chat) | **partially done 2026-09-08** — Google Play installed `3 (1.0.2)` on Samsung S24+ / Android 16 and Android reported `com.android.vending` as installer. WorkOS sign-in succeeded. The corrected debug build proved the provider notification/cancel path. Install Play-signed `4 (1.0.3)` and send one review-safe message to finish the row. |
| Sign in details | **done 2026-09-08** — the dedicated reviewer account and rotated 40-character password were verified end-to-end, transferred directly from Windows Credential Manager, and saved in Play without exposing or persisting the value. |
| Target audience | **done** 2026-09-03 — 18 and over; submitted for review 2026-09-08 |
| Advertising ID declaration | **done 2026-09-03** — saved No after shipped-artifact, exact-candidate merged-manifest, and dependency verification; submitted for review 2026-09-08 |
| Foreground-service declaration + behavior video | **done; submitted 2026-09-08** — the 27.11-second 1080×2340 privacy-redacted candidate has SHA-256 `7b49b48d21ca3a1f57acdce23ed8c5ac0f58b63aab57ea3d4cb5696ed61391f2`. Public URL: `https://github.com/TinyAssets/TinyAssets/releases/download/android-latest/tinyassets-fgs-play-evidence-final.mp4`. Play accepted **Data sync → Network processing → Other** with this link; App content reports no declarations needing attention. |
| Replace the unsafe uploaded conversation screenshot with staged `01-sign-in.png` | **done 2026-09-03** — live draft saved and both retained filenames verified; submitted for review 2026-09-08 |
| Closed test: 12 testers for 14 days, then apply for production access | **you** — Play approved and published signed code `4 (1.0.3)` on 2026-09-08 at 10:35 PM PT. The Alpha track says **Available to selected testers** across all 177 configured regions. The one-member `Founder devices` list is attached, and its invited founder Google account now sees the live opt-in page with **Become a tester**. The founder has not opted in yet. Opt in that account and recruit at least 11 more real Google-account testers; the 14-day clock begins only when 12 remain continuously opted in. |
| Promote to Production → submit for review → **Roll out** | you (final click) |

### Google Play: review and submit the foreground-service declaration

The Android bundle declares a `dataSync` foreground service because **Connect
OpenAI** starts a short-lived local callback listener while subscription OAuth is
open in the external browser. For apps targeting Android 14+, Play requires a
foreground-service declaration. The live form observed 2026-09-08 asks for **Data
sync → Network processing → Other** and one public video link. This is a Console
attestation, so the final truth check and submission are yours.

After installing the next candidate from Play:

1. Review the prepared real-phone video. It shows **Connect OpenAI** entering its
   pending state and the foreground notification appearing immediately; unrelated
   notifications are masked. Android service state and the return-to-app screenshot
   separately confirm that cancellation stops the service. If Play requests a longer
   continuous demonstration, retake it before submitting.
2. In Play Console, open **App content → Foreground service permissions** and use
   the staged wording and video shot list in
   `docs/ops/android-release-verification.md`.
3. Submit only if the recording confirms that exact behavior. If the notification
   remains, the callback cannot be interrupted as described, or the function has
   changed, stop and return the discrepancy to an agent instead of attesting to the
   draft.

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
*Owner artifacts:* `openspec/changes/byo-llm-connect-flow/`,
`openspec/changes/archive/2026-08-26-constrain-set-engine-provider-authority/`.

### Activate hosted-preview publication

`activate-hosted-preview-publication` — a no-prod account plus a fixed Worker; inert host
alias/version; Access anon-deny and reviewer-load proof; then a restricted GitHub environment.
*Where:* Cloudflare + GitHub environment.

---

## Host-recorded evidence

### Connector tool-selection accuracy — baseline and regression decision

Instrument landed in #1776; **no agent-buildable task remains** (triaged 2026-08-02). The host
records the claude.ai baseline (task 3.1) and the permitted-regression decision (task 3.2).
*Depends on:* ChatGPT connector registration, before its baseline.
*Owner artifact:* `openspec/changes/archive/2026-08-26-connector-tool-selection-accuracy/`.

### Cloud drain activation — dark deploy, cutover, and 24/7 proof

Implementation landed; the dark deploy/canary, single-active cutover, and 24/7 PC-off proof remain.
*Owner artifacts:* `docs/audits/2026-08-03-cloud-drain-epoch2-consumer.md`,
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
