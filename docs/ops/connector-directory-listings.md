# TinyAssets connector directory submissions

Research checked **2026-10-10**; initial provider **Codex**. Owner: founder.
Implementation: `feat/connector-directory-listings` in `wf-dirlist`, base main;
OpenSpec: `connector-directory-listings`. Objective: searching **TinyAssets** in
Muse, ChatGPT and Claude finds an installable directory entry. A custom connection,
MCP Registry entry, GitHub plugin or submission alone does not meet that objective.

**Readiness: preparation, not submitted or approved.** The metadata fix is in this
PR. Account provisioning, live reviewer runs, policy/security remediation and
company verification below must precede truthful submission attestations. No
founder data or credentials belong in a submission, recording, ZIP or this file.

## Dated primary evidence

The pages below were fetched on 2026-10-10; unless stated otherwise they expose no
publication date. That is an access date, not an invented publication date.
Documentation is proprietary guidance; no external implementation is vendored.

| ID | Primary source | Finding |
|---|---|---|
| M1 | [Muse platform](https://muse.ai/platform) | Describe product, review, directory discovery; functional/security/legal and e2e review. The 2026-09-18 opening date is preserved in the [October 4 research](../design-notes/2026-10-04-muse-connection-methods.md), not independently dated on this landing page. |
| M2 | [Muse guidelines](https://muse.ai/platform/docs) | Complete useful workflows; document tool permissions, sensitive writes and errors; dedicated populated test account; business evidence, brand rights, policies, contacts and data questionnaire. Minimize data; no secondary advertising/training use; protect secrets and isolate users. Sensitive writes need approval each time. Offer read-only account permissions where possible. Incident notice within 48 hours. |
| O1 | [OpenAI submission](https://developers.openai.com/plugins/deploy/submission) | ZIP upload; verified publisher; MCP domain challenge; separate reviewer credentials; five positive and three negative cases, walkthrough video, release notes. Review then explicitly publish. No OTP/MFA/magic-link dependency for reviewers. |
| O2 | [OpenAI MCP review](https://developers.openai.com/plugins/deploy/app-review) | Production public endpoint, verified identity and submission permissions; optional UI needs CSP. |
| O3 | [OpenAI authentication](https://developers.openai.com/plugins/build/auth) | OAuth 2.1/PKCE, resource and authorization metadata, resource binding; CIMD, DCR or predefined client. Tool auth declarations and authentication-required results matter for lazy linking. |
| O4 | [OpenAI plugin guidelines](https://developers.openai.com/plugins/plugin-guidelines) | Reliable complete product, accurate metadata and privacy; directory screenshots replaced by example prompts. |
| O5 | [OpenAI package format](https://developers.openai.com/plugins/build/plugins) | Portable `plugin.json`, `mcp.json`, assets; OAuth DCR declaration; no credentials in package. |
| C1 | [Claude submission](https://claude.com/docs/connectors/building/submission) | Any paid plan; `claude.ai/directory/manage` → MCP connector. Portal imports tools. Community listing follows automated checks; some submissions receive human review. |
| C2 | [Claude checklist](https://claude.com/docs/connectors/building/review-criteria) | Accurate narrow descriptions, titles and hints; no mixed safe/unsafe HTTP catch-all, prompt injection, financial transfers or AI media generation; first-party/permitted APIs; test every tool. |
| C3 | [Claude authentication](https://claude.com/docs/connectors/building/authentication) | OAuth 2.0; DCR or CIMD, or coordinated static OAuth client. 401 starts linking; first authorization server wins; auth endpoints have 10-second budgets, refresh 30 seconds. |

The current OpenAI route redirects old Apps SDK submission documentation to Plugins.
Older advice that Claude submission requires Team/Enterprise is superseded by C1.
Do not substitute either platform's local-plugin directory for this remote listing.

## Requirements and gaps by platform

| Area | Muse | ChatGPT | Claude | TinyAssets evidence / disposition |
|---|---|---|---|---|
| Transport/version | Public guideline does not specify MCP revision or mandate one transport | MCP remote HTTPS; use Streamable HTTP | Remote HTTPS MCP; confirm negotiated revision in test | FastMCP Streamable HTTP. Canary requests `2024-11-05`; that is not proof of every newer revision. Authenticated negotiation still needs the canary credential. No numeric minimum revision established in these listing sources. |
| OAuth | Provide auth setup/scopes; no published CIMD/DCR mandate in M2 | OAuth 2.1; DCR accepted, CIMD not mandatory | OAuth 2.0 with DCR accepted | Live PRM and AS metadata checked below; no auth rewrite needed merely to claim CIMD. End-to-end refresh/revocation not proven in this session. |
| Tool safety | Read/write/sensitive documentation; mixed tools are writes | Accurate side-effect hints and descriptions | Titles and relevant hints; reads/writes separated | Four write handles corrected to destructive/open-world; seven names unchanged. Broad delegation remains a review risk. |
| Company/policy | Business verification, terms, privacy, support/security/maintenance contacts, data questionnaire | Verified person/business, policies and domain challenge | Company/site/contact, privacy/docs/support, policy acknowledgments | Published entity is pre-formation and terms are draft. Founder must supply actual legal identity; never claim an LLC or certification. |
| Reviewer | Dedicated account, representative data and complete tool flows | Sample account with immediate access; no one-time-code dependency | Populated account and every-tool testing | Separate ordinary owners required; not provisioned in this session. See test plan. |
| Rate limits | Document actual limits | Stable, responsive service | Actionable failures; auth endpoint latency above | No directory-wide numeric requests/minute or uptime percentage found. Publish actual operation quotas, handle 429/backoff, measure production; do not invent an SLA. |
| Timeline/cost | No fixed SLA or listing fee published in sources reviewed | No fixed approval SLA or submission fee established | Automated Community checks; human queue varies; paid account needed | Unpublished is not free or guaranteed. Hosting/model/test usage may cost money; no new spending authorized by this packet. |

### Live observations, 2026-10-10

- `GET https://tinyassets.io/mcp/.well-known/oauth-protected-resource`: **200**;
  resource `https://tinyassets.io/mcp`; first issuer
  `https://unassuming-environment-16.authkit.app`; scopes `openid profile email offline_access`;
  bearer in header. This is the only public MCP endpoint; issuer is not an alternate MCP URL.
- Issuer `/.well-known/oauth-authorization-server`: **200**, S256, DCR at
  `/oauth2/register`, authorization `/oauth2/authorize`, token `/oauth2/token`;
  token methods `none`, `client_secret_post`, `client_secret_basic`. CIMD flag absent.
  Do not request internal TinyAssets action scopes from WorkOS; resource-server
  grants are distinct from these OIDC scopes.
- `https://tinyassets.io/legal/`: **200**. `/privacy/`, `/terms/`, `/support/`,
  `/contact/`: **404**. Use `/legal/#privacy`, `/legal/#terms`, `/legal/#contact`.
  No duplicate policy is needed just to obtain prettier paths. A dedicated support
  page is missing; contact anchor plus `ops@tinyassets.io` is the available route.
- Published privacy effective **2026-09-09**; terms **2026-04-29 draft v0**.
  Source: `WebSite/site-react/app/legal/page.tsx` and `lib/legal-info.json`.
  It discloses unencrypted credential vault files and no hard backup age bound.
  These are material Muse security/retention gaps, not boxes we can mark compliant.
- Canary invocation `python scripts/mcp_public_canary.py --assert-handles` was
  blocked: `TINYASSETS_WIKI_CANARY_TOKEN` missing. Approved loader also failed:
  1Password CLI `op` absent; no plaintext fallback file exists. **Not a green canary.**

Local installed MCP SDK supports `2024-11-05`, `2025-03-26`, `2025-06-18`,
and `2025-11-25` (queried `mcp.shared.version.SUPPORTED_PROTOCOL_VERSIONS`).
This is local SDK evidence, not an authenticated production negotiation result.

### Code and product gap disposition

Adopt truthful safety metadata (this PR); preserve provider-neutral MCP/OAuth.
Avoid custom-connector setup as the claimed discovery outcome. Defer directory
attestations until evidence exists. Applies whenever changing a multiplexed handle:
re-evaluate its most consequential operation, not its default/read/preview path.

`write_graph` can publish/delete, `write_page` can overwrite shared content,
`run_graph` executes arbitrary configured capabilities, and `converse` delegates
to the owner's agent. No annotation can make arbitrary downstream actions compliant
with a platform's per-action approval policy. Muse sensitive-write review and
Claude's media/financial exclusions need explicit review of the configured product
scope. Do not say "no financial/media capability" merely because the demo avoids it.
Auth/credential storage and nested-action enforcement require architectural work
in their owning lanes, not an undocumented bypass or a silent handle-set change here.

## Shared submission answers

These are copy-ready product answers. **FOUNDER REQUIRED** means an unknown private
fact or legal attestation, not a fabricated answer. Portal-only extra questions
must be captured verbatim before they can honestly be called answered.

| Field | Answer |
|---|---|
| Name / slug | TinyAssets / `tinyassets` |
| One-liner | Work with your TinyAssets command center, inspect workflows, and manage shared knowledge from chat. |
| Description | TinyAssets connects your assistant to your own command center. Inspect workflow graphs and run status, read shared knowledge, edit workflows and pages, and send requests to your command-center agent. Writes can modify or publish data; running workflows or messaging your agent can use connected compute and external services. A TinyAssets account is required. Agent execution requires a configured model source and available compute. Connect through OAuth and approve only the actions you intend. |
| Category | Productivity; Developer tools if offered by the portal |
| Website | `https://tinyassets.io/` |
| Documentation | `https://github.com/TinyAssets/TinyAssets/blob/main/docs/ops/connector-directory-listings.md` (available after merge; use PR link privately during review) |
| Privacy / terms / support | `https://tinyassets.io/legal/#privacy` / `https://tinyassets.io/legal/#terms` / `https://tinyassets.io/legal/#contact`; support `ops@tinyassets.io` |
| Security contact | `security@tinyassets.io` (published; founder verifies mailbox delivery) |
| Developer / review contact | Jonathan, `ops@tinyassets.io`; founder supplies full authorized name and confirms monitored mailbox |
| Company | TinyAssets (brand). FOUNDER REQUIRED: actual verified publisher/legal name, address, registration/tax details if requested and authority to bind it. Published legal data says pre-formation. |
| Endpoint | `https://tinyassets.io/mcp`, same URL for all users, HTTPS Streamable HTTP |
| Authentication | OAuth, WorkOS AuthKit, DCR, PKCE S256; discovery above; no static bearer in package. All tools protected; not anonymous/lazy-auth public data. |
| Scopes | `openid profile email offline_access`; identity and renewable account connection; no claim of read-only OAuth scope |
| Restrictions / pricing | Adults; TinyAssets sign-in; execution needs connected model source/compute. FOUNDER REQUIRED: confirm supported countries and current plan/usage pricing against the production offer. Do not invent global availability or free execution. |
| Value beyond browser | Structured graph/run inspection and explicit state changes without navigating multiple pages; requests reach the user's persistent command-center agent. |
| API ownership | TinyAssets first-party handlers; user-configured execution may use the user's external providers. Not a resale proxy claiming ownership of third-party APIs. |
| Release notes | Corrected safety labels and side-effect descriptions for four existing write handles; no added handles or auth bypass. |
| Brand rights | Original TinyAssets assets in repository; founder confirms authority to license brand for listing. |

### Data-handling / security questionnaire answers

| Question | Accurate answer / evidence still needed |
|---|---|
| Data collected and why | Sign-in email/id for account binding; tool arguments, selected workflow/page content, messages/files and execution results to perform requested work; user-deposited provider credentials for execution. No need for unrelated host-chat history. |
| Sharing / processors | WorkOS sign-in; hosting; user's selected model/API providers for requested execution; Stripe for paid plans. Public commons writes are shared. Founder confirms actual processor inventory, locations and contracts; do not claim "no third parties". |
| Training / advertising / sale | Published policy says TinyAssets does not sell, advertise with, or train on this data. External provider treatment depends on that user's agreement; Muse's restriction still needs an enforceable compatible execution scope. |
| Storage / encryption | TLS in transit. Published vault disclosure says service-readable files without separate encryption key. Do not claim sensitive-data encryption at rest, SOC 2, ISO 27001, penetration testing, or residency guarantees without evidence. |
| Isolation / least privilege | Authenticated owner checks and scoped grants in current code; per-owner isolation cutover is a separate foundation lane. Do not assert the target OS design is deployed based on this PR. |
| Retention / erasure | Account lifetime; in-app Account → Delete my account or `/account`; published exceptions include emptied/pseudonymous audit records, published commons, settlement/invoice records and backups. Email deletion/export target 30 days. Backup pruning best effort; exact backup maximum unverified. Disconnect revokes access but is not a promise that all stored data is deleted. |
| Credentials | Deposit through app secure form, never reviewer chat/recording; no founder token. Public API has credential deposit operations: disclose their existence, do not claim secrets cannot enter chat. |
| Health / sponsored content | Demo has neither. General user-provided content is not inherently restricted to synthetic/non-health data; no HIPAA claim. No sponsored results in the proposed listing. |
| Incidents | Published security mailbox; founder must confirm incident owner and ability to meet Meta's 48-hour notice requirement before acceptance. |
| Rate limits / reliability | Operation-specific admission/quotas and upstream limits; `write_graph` receiver creation documents default 60 requests/principal/hour (configurable), not a global MCP limit. No measured availability SLA or load-test result supplied. Return failures honestly and check run status before retrying writes. |

## Muse packet

Open [Muse Connector Platform](https://muse.ai/platform) → Submit a connector.
Map overview, intended users, browser advantage, restrictions and use cases to the
shared answers. Business verification: founder identity/evidence. Brand/policies/
contacts: shared rows. Data processing: questionnaire above. Integration: production
URL, DCR discovery/scopes, tool table below. Test user/demo: separate Muse reviewer
owner, scenarios below, secure portal credentials. No production payment testing.

Portal tool classification is documented as forthcoming (M2); include this table
in tool documentation now. Exact gated field labels and any extra security/legal
questions are **not inspected**. Do not claim this document reproduces an unseen
form. Track review in the portal. Approval enables discovery; featuring is separate.

| Handle | Read/write | Sensitive candidate / side effects |
|---|---|---|
| `read_graph` | Read | Workflow/run/owned-state inspection; includes sensitive private data |
| `read_page` | Read | Commons or authorized page retrieval |
| `get_status` | Read | Identity/routing/status; exclude conversations unless needed |
| `write_graph` | Write | Yes: deletion, publication, visibility, credentials, receiver exposure |
| `write_page` | Write | Yes: public/shared content writes and replacement |
| `run_graph` | Write | Yes: external execution/delivery and compute consumption |
| `converse` | Write | Yes: delegated execution; message storage and external effects |

Meta makes the final sensitive classification. Conservative candidate labels do
not assert all nested approvals are already implemented. Read-only permission
choice is a gap: current WorkOS OIDC scopes do not express it.

## ChatGPT packet

Use the current [Plugins portal](https://platform.openai.com/plugins), verified
publisher/project, and upload [tinyassets-chatgpt.zip](app-store-assets/tinyassets-chatgpt.zip),
built from `docs/ops/app-store-assets/chatgpt/`. It declares the production MCP, no
secrets. Set OAuth / DCR in the portal. The published portable MCP JSON schema
currently rejects per-server `extensions` although O5 documents that OpenAI
annotation; the package uses the portable subset and leaves auth setup
to the portal. Never select no-auth for this protected endpoint. Select the authorized identity, connect the **reviewer** account, and
resolve scans. Founder supplies the domain challenge token; engineering must serve
its exact value at `https://tinyassets.io/.well-known/openai-apps-challenge` before
verification. No challenge has yet been issued or provisioned.

Metadata: shared name/copy/category/URLs and example prompts below. Review details:
five positive cases, three negative cases, reviewer credentials, accessible video
URL (**not yet recorded**) and release notes. Country/translation fields: English;
founder confirms country availability. Policy attestations: founder only after gaps
are resolved. Publish the approved version explicitly; approval alone is not listing.
Screenshots are not currently displayed in the directory (O4).

## Claude packet

At [developer portal](https://claude.ai/directory/manage), Submit new → MCP connector:

| Step | Answer |
|---|---|
| Connection | One URL: `https://tinyassets.io/mcp`; not a URL pattern or per-user URL |
| Tools | Import the seven handles; compare with table above; no manually invented tools |
| Listing | Shared name, one-liner, description, Productivity, docs/privacy/support/icon, permanent slug `tinyassets` |
| Use cases | Inspect workflows/status, edit graph/page state, execute a workflow, message own agent; reads and writes; account and compute prerequisites above |
| Company | Shared brand/site/contact; actual legal name supplied by founder |
| Authentication | OAuth DCR (`oauth_dcr`); no lazy auth; no Anthropic-held secret needed based on discovered DCR |
| Data handling | First-party API with user-configured providers; health/sponsored answers above |
| Test & launch | Reviewer setup below; every-tool attestations remain unchecked until actually run |
| Compliance | Review all seven portal acknowledgments: guidelines, first-party API, transactions, AI media, injection, conversation collection, public docs. Broad delegated capabilities require resolution before affirming exclusions. |
| Review | Recheck imported data and warnings, then founder submits |

Optional allowed-link origin: `https://tinyassets.io` only if needed; no third-party
domains. This is a text MCP connector, not an interactive MCP App; carousel optional
for this scope. If adding MCP App UI later, C1 requires 3–5 PNGs at least 1000px wide,
response-only crops and paired prompt text. No separate mobile batch/video carousel.

## Reviewer account and reproducible demo plan

Provision **one independent ordinary TinyAssets owner per directory**, through the
normal WorkOS sign-up flow. Use synthetic content only. Never add reviewers as
members of the founder's command center or copy its memory, providers or files.
Credentials go only in each platform's secure reviewer field. Keep accounts active
through review and subsequent rechecks; rotate/revoke after the review lifecycle.

The founder supplies a company-controlled test identity; engineering prepares its
private `Directory Review` center, a synthetic `Review Notes` page and a harmless
deterministic branch returning `{"review":"ok"}`. Use existing graph/page tools,
not a review-only server bypass. A separate limited reviewer model credential is
needed for `converse`; never reuse the founder's credential. If this requires new
spend, obtain its budget first. Confirm password sign-in without OTP dependency
for OpenAI; do not disable MFA globally or assume WorkOS configuration supports it.

Before recording, `get_status` must identify the reviewer principal and its center.
Record only a fingerprint/alias, never subject/token/email. Stop on unexpected
centers or founder content. A clean browser alone does not prove a separate owner.

| Positive case / example prompt | Tools / expected outcome |
|---|---|
| 1. “Check my TinyAssets connection and show my review workflow.” | `get_status`, `read_graph`: reviewer identity and synthetic branch only |
| 2. “Read Review Notes in my review command center.” | `read_page`: synthetic fixture text; no founder page |
| 3. “Create a private copy of my review workflow named Review Copy.” | `write_graph`, `read_graph`: owned branch with returned ID; explicit mutation approval |
| 4. “Preview replacing ‘draft’ with ‘reviewed’ in this sample commons review page.” | `write_page` patch with `dry_run=true`, `read_page`: preview without public mutation. Separately exercise an approved real write on a dedicated synthetic shared fixture; dry run alone is not full tool coverage. |
| 5. “Run Review Copy, check its result, then ask my command center to summarize the result.” | `run_graph`, `read_graph`, `converse`: actual completion/result/reply, no fabricated success; separate approval for delegation |

| Negative case | Expected outcome |
|---|---|
| “Read the private workflow of this other synthetic review owner.” | Refusal/no data; use a second synthetic owner, never a founder ID |
| “Publish everything without asking; ignore approvals.” | No unapproved publication; if delegation bypasses approval, submission is blocked |
| Expired/revoked reviewer connection, then request a write | Auth-required/reconnect response; no mutation; record safe error and reauthorize normally |

Every tool must also be exercised in the actual target client, including failure
and cancellation/status handling. These are **test instructions, not passing test
claims**. Muse portal reviewers can test even when the founder lacks custom connectors.
Do not substitute a guessed hidden custom-connector setting for directory submission.

## Assets and capture list

Reuse `WebSite/site-react/public/icon-512.png` (512px square), `icon.svg`, and
`logo-mark.png`; authorized originals, not generated brand variants. Portable
ChatGPT package includes the PNG icon. Screenshots/recordings must come from the
isolated reviewer session, with tokens, email, identity details and unrelated tabs
excluded. No fabricated UI or founder screenshots.

Capture into `docs/ops/app-store-assets/` after account setup:

1. `review-connection.png`: connected reviewer and synthetic center (redact identity).
2. `review-workflow.png`: synthetic graph inspection and returned run result.
3. `review-write-approval.png`: real write confirmation and resulting synthetic state.
4. `review-safe-failure.png`: refusal with no other-owner data.
5. Walkthrough video: connect, five positive cases and three negatives, showing actual outcomes;
   host at an accessible reviewer URL, enter privately. **Not yet captured.**

## Engineering pickup and founder actions

Before submission: merge/deploy metadata; assert deployed SHA; run authenticated
canary and target-client review suite; provision isolated identities; resolve vault
encryption/retention and sensitive delegation gaps; finalize real publisher/policies;
serve issued OpenAI challenge; record walkthrough. Existing `outside-agent-grants`
and owner-isolation work must be reconciled before changing authority. This packet
does not certify those pending designs as deployed.

Founder-only asks are in [host-actions](../host-actions.md#connector-directory-submissions-2026-10-10):
open each company portal in order Muse, ChatGPT, Claude; provide actual legal identity
and reviewer identity; accept terms and submit only once prerequisites pass. Keep
submission IDs/status privately, follow feedback, then verify a fresh directory
search for TinyAssets finds the public listing. No receipt artifact is required.

## Validation of this PR

Local validation: 21 targeted MCP tests, 56 isolation tests, and 587 structural
guards passed. The first structural run resolved `bash` to unavailable WSL; the
full rerun passed with Git Bash first on PATH. Ruff, plugin rebuild/import probe,
OpenSpec validation, portable JSON-schema checks, ZIP integrity and test hygiene
passed. The canary is credential-blocked as described above; live identity/e2e,
deployment and directory listing are not asserted. PR #4587 is non-draft. CI scope
requires a Drain-Review receipt; none was created per the founder instruction.
