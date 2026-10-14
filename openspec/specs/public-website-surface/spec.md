# public-website-surface Specification

## Purpose
Define the public website's route set, its message, the live-versus-snapshot provenance of what it shows, the browser's public read boundary, the operational and plan copy, the brand mark, and indexing. As built 2026-09-02 (`WebSite/site-react`, Next.js static export on `@tiny/design-system`). The per-PR Cloudflare preview worker was removed on 2026-10-14; it never published because its preview credentials were never set.
## Requirements
### Requirement: The Public Site Ships As One Static Multi-Route Application

The website source under `WebSite/site-react` SHALL build with Next.js static export (`output: "export"`, trailing slashes) and expose exactly the public routes `/`, `/start`, `/connect`, `/build`, `/commons`, `/developers`, `/fine-print`, `/legal`, `/token`, and `/account`. Retired routes (`/host`, `/soul`, `/graph`, `/notebook`, `/goals`, `/goal`, `/catalog`, `/patterns`, `/wiki`, `/alliance`, `/contribute`, `/loop`, `/patch-loop`, `/proof`, `/status`, `/economy`) SHALL remain soft-landing aliases that name their destination, link to it, and follow after a short delay, and SHALL be marked `noindex`. There is no second site tree: the Svelte rollback source and its deploy workflow were retired on 2026-09-02, and rollback means redeploying an earlier revision. The generated static assets SHALL include the canonical hostname (`CNAME`), crawler policy, sitemap, brand marks, web manifest, `llms.txt`, and the `.well-known/openai-apps-challenge` file committed with the site.

#### Scenario: A retired route is visited

- **WHEN** a visitor opens `/proof/`, `/status/`, `/host/`, or any other retired route
- **THEN** the page says where the content went, links there, and lands on the destination without the visitor acting

#### Scenario: Static production build is requested

- **WHEN** `npm run build` runs in `WebSite/site-react`
- **THEN** Next.js emits `out/<route>/index.html` for every public route and alias plus the static assets, without a website application server

### Requirement: Every Page Serves One Positioning And One Job

The site SHALL present TinyAssets as one app on any device: the visitor's own agents, a model the visitor brings, and setups that can be copied. Each route SHALL have one job: `/` states that positioning on the first screen and links to join, how it works, status, creators, source, and fine print; `/start` gets a person into early access on any surface; `/connect` shows how to add the hosted `https://tinyassets.io/mcp` server to Claude, ChatGPT, Cursor and Codex with AuthKit sign-in; `/build` explains the primitives (connection, graph, code node, workspace, automation, brain) and honest failure; `/commons` shows public shapes to remix; `/developers` covers open source, the MCP endpoint and handles, and specs; `/fine-print` carries operational truth, plans, and boundaries; `/legal` carries terms, privacy, and disclosures; `/token` publishes the currency token's contract addresses from `lib/token-info.json` and does not promise value or sell the token; `/account` says accounts live in the app and how to delete one. Copy SHALL NOT advertise a platform-supplied model, a paid work market, a DAO, KYC, or an in-app wallet, host-run fleets, per-channel integration lists, the retired `Workflow` name, a global goals engine, engagement metrics, user counts, or "powered by" a model vendor. Public contact mail SHALL be info@tinyassets.io.

#### Scenario: The home page proof

- **WHEN** a visitor reads the home page
- **THEN** the first screen says it is one app, the visitor's own agents, a model the visitor brings, and a setup that can be copied
- **AND** the primary action is joining early access
- **AND** the receipt of pull request #2728 stays on `/build`, not as the home-page headline

#### Scenario: Subscription copy stays truthful

- **WHEN** any page describes connecting a subscription
- **THEN** ChatGPT/Codex is described as one tap and Claude as the person pasting their own setup token into the deposit form
- **AND** no page presents a "Connect Claude" OAuth control

### Requirement: The Site Names TinyAssets Only

The public site SHALL name the operator as TinyAssets and SHALL NOT publish a separate legal-entity name, a trade-name phrase, or a doing-business-as phrase. `brand`, `governing_law`, and `contact` SHALL stay in `WebSite/site-react/lib/legal-info.json`. `/legal` (page and metadata), the footer brand, the schema.org Organization, WebSite, and SoftwareApplication names, the Open Graph site name, and `SITE.contact` SHALL read this record. A site test that scans only `WebSite/site-react` SHALL fail when `Simkal`, `Holdings LLC`, `trade name of`, `doing business as`, or a `{legal_name}` placeholder appears there.

#### Scenario: A visitor reads the terms

- **WHEN** a visitor opens `/legal`
- **THEN** the terms say the agreement is between them and TinyAssets
- **AND** the page names the governing law and the contact address from `lib/legal-info.json`
- **AND** the page does not name another legal entity

### Requirement: Plans Are Described In Words, In One Place, And Do Not Overstate Enforcement

The site SHALL describe plans in the `Plans` section of `/fine-print` (anchor `#plans`). Free and any later paid tier SHALL be the same app and the same security. The only differences the copy may name are simultaneous runs and storage. The section SHALL NOT print a price, a run cap, or a storage cap until the owner publishes those numbers; unfinished numbers SHALL be a visible owner-decision note, not a guessed figure. Because enforcement is not refusing work today, the section SHALL say usage is metered and the gate is not switched on. It SHALL NOT describe hitting a limit as a thing that currently happens, and SHALL NOT say that no upgrade control exists. The only upgrade entry is the app at `https://tinyassets.io/app?upgrade=1`. Home (`/`) and `/start` SHALL link to that entry without restating a price. No public text asset (`llms.txt`, `robots.txt`) states a price.

#### Scenario: A visitor looks for pricing

- **WHEN** a visitor opens `/fine-print/#plans`
- **THEN** they read that free and paid are the same app and the same security, that the only named differences are simultaneous runs and storage, that no price or cap is printed, and that the refusal gate is not switched on, with a link to plans in the app
- **AND** unfinished numbers are an owner-decision note rather than a guessed figure
- **AND** they do not read a daily limit or a claim that there is no upgrade control

#### Scenario: Home and start link to plans

- **WHEN** a visitor reads the home page or `/start`
- **THEN** each page links to `https://tinyassets.io/app?upgrade=1`
- **AND** neither page states a price

#### Scenario: A grounding crawler reads the text assets

- **WHEN** an AI-grounding crawler reads `/llms.txt`
- **THEN** it finds that joining is free during early access and a pointer to `/fine-print/#plans`
- **AND** it does not find a price or a daily-limit claim, so the two cannot drift apart

### Requirement: Public Views Distinguish Live Reads From The Checked-In Snapshot

The site SHALL carry a checked-in public snapshot (`lib/mcp-snapshot.json`: the
public universe list with `fetched_at`) and SHALL label it as a snapshot with
its date wherever it is shown. Public browser code SHALL NOT attempt a live MCP
read because it has no connector bearer. It SHALL state that live discovery
needs a signed-in connector and MUST NOT relabel snapshot data as live. The
snapshot SHALL be refreshed only by an authenticated `scripts/snapshot-public.mjs`
run, which reads the public projection and fails closed if completeness cannot
be proven.

#### Scenario: A visitor opens commons

- **WHEN** an unsigned browser renders `/commons`
- **THEN** it shows snapshot rows labelled with the snapshot date and the signed-in-connector requirement
- **AND** it makes no MCP request and offers no unauthenticated MCP refresh control

#### Scenario: No public universes exist

- **WHEN** the checked-in snapshot contains no discoverable universes
- **THEN** the page states that there are no public universes in this snapshot and that every universe starts private

#### Scenario: A snapshot record is not explicitly discoverable

- **WHEN** the checked-in snapshot holds a record whose `visibility` is missing, `private`, or any value other than `public`/`metadata_only`
- **THEN** it is dropped before render by `lib/discoverable.js`, rather than shown as public
- **AND** one bad record does not blank the list

#### Scenario: The public list is raw rather than curated

- **WHEN** the snapshot reports working or housekeeping universes as publicly discoverable
- **THEN** `/commons` shows them and says the list is what the endpoint reported rather than a curated gallery
- **AND** the site does not filter them out, which would make its own "what is public" claim false

### Requirement: Browser MCP Reads Use The Public Connector Contract And Only The Public Projection

The public browser SHALL NOT initialize an MCP session or issue any request to
`/mcp`. `lib/live.ts` SHALL expose only `fetchPublicUniverses`, which refuses
with a bounded signed-in-connector message, and `fetchVitals`, which returns an
explicit `authRequired` state without a network call. Live MCP reads SHALL be
performed only by an authenticated connector or account surface. The client
MUST NOT call `get_status`, request goals or runs, embed a bearer, default a
missing visibility to public, or surface untrusted endpoint detail.
`scripts/public-boundary.test.mjs` and `scripts/canonical-mcp-contract.test.mjs`
SHALL enforce this in `npm test`.

#### Scenario: A public browser loads the site

- **WHEN** any public route renders without an authenticated connector context
- **THEN** no MCP session or tool call is created
- **AND** no credential is embedded in or recovered by the static JavaScript

#### Scenario: Code attempts public universe refresh

- **WHEN** public browser code calls `fetchPublicUniverses`
- **THEN** it receives the bounded signed-in-connector refusal without a network request

### Requirement: Reachability And Activity Stay Distinct Operational Truths

The `/fine-print` reachability strip SHALL report that live readings require a
signed-in connector. It MUST NOT describe the protected endpoint as
unreachable, MUST NOT infer an executing run or public activity from snapshot
data, and SHALL NOT offer an unsigned `Refresh MCP` control.

#### Scenario: Public browser opens fine print

- **WHEN** the browser has no connector bearer
- **THEN** the strip reports "sign-in required" for live readings
- **AND** it makes no reachability request and asserts no activity state

### Requirement: Start, Surfaces, And Availability Copy Are Truthful

`/start` SHALL present three steps (sign in; connect a subscription; say what real thing to finish) and the four surfaces with their real addresses: the web app at `https://tinyassets.io/app`, the chatbot connector URL `https://tinyassets.io/mcp` for Claude.ai and ChatGPT, the Android app as Google Play's closed-test opt-in page `https://play.google.com/apps/testing/io.tinyassets.app` (with the `android-latest` debug APK at most a developer link, labelled as a separate install that never receives Play updates), and the desktop app as unsigned builds from `desktop-app/` in the repository. `/fine-print` SHALL state plainly what does not exist: no platform model, no list of integrations, no signed desktop installer and no public Play listing yet, no paid work market. `/developers` SHALL name `https://tinyassets.io/mcp` as the only public endpoint and list the seven canonical handles (`converse`, `read_graph`, `write_graph`, `run_graph`, `read_page`, `write_page`, `get_status`) with the source-install path (Python 3.11+, clone, editable install, `tinyassets-mcp` / `tinyassets-cli`).

#### Scenario: Visitor wants the Android app

- **WHEN** a visitor follows "Get the Android app" or reads `llms.txt`
- **THEN** the install path is Google Play's closed-test opt-in for `io.tinyassets.app`, the copy says only invited accounts can join, and the debug APK (which installs as `io.tinyassets.app.debug`) is never presented as the way to install the app

#### Scenario: Visitor asks for a desktop installer

- **WHEN** a visitor reads the desktop surface on `/start` or the fine print
- **THEN** the site says builds are unsigned and come from the repository and links the desktop source, and does not present an installer download

#### Scenario: A mobile-store reviewer reads privacy and account-deletion copy

- **WHEN** a reviewer opens `/legal#app-data` or `/account`
- **THEN** the policy names iOS alongside web, Android, and desktop as a client of the same hosted service
- **AND** the account page says the same in-app deletion path is available from iOS

### Requirement: The Mark Has One Source And Appears On Every Surface

The brand mark (a circular badge: Mount Baker seen from the south, a wolf howling on the snowfield, a pale moon and a spiral galaxy in the night sky) SHALL be defined once as the `EMBLEM` layer list in `tinyassets/desktop/icon_gen.py` -- with the mountain profile traced from a photograph of the real skyline rather than drawn by eye -- and exported by `WebSite/brand/render_marks.py` to the site icons (`favicon.ico`, `icon.svg`, `apple-touch-icon.png`, `icon-192.png`, `icon-512.png`, `logo-mark.png`, `tinyassets-mark.png`), the repository brand assets under `assets/`, the desktop app build resources, the Windows tray icon, the Android launcher and splash density set, and the Play listing graphics; `WebSite/brand/render_og.py` SHALL render the OG card with the site's fonts. The site's inline React mark (`components/TinyAssetsMark.tsx`) SHALL be **generated** by the same exporter from the same constants rather than hand-maintained, so a geometry change cannot leave the web mark behind. The served web app SHALL carry the same mark as its favicon and brand glyph.

#### Scenario: The mark changes

- **WHEN** the `EMBLEM` layer list or the palette in `icon_gen.py` changes and the exporters run
- **THEN** every listed surface receives the new mark from the same description, and no exported raster or inline copy is hand-edited
- **AND** the boundary test fails if the site's inline component carries a path `icon_gen.py` does not describe, so the traced mountain profile cannot be quietly redrawn by eye

### Requirement: Public And Private Indexing Boundaries Are Declared

The site's crawler policy SHALL allow public pages to search and AI-grounding crawlers while disallowing `/account`, `/auth/`, `/editor/`, and `/admin/` from indexing, and `/account` SHALL also carry `noindex` metadata. Because a crawler obeys only its most specific matching group and does not fall back to `*` (RFC 9309), every named agent and the wildcard SHALL share one group carrying both the allow and the exclusions; naming an agent in its own group would silently drop the exclusions for exactly that crawler. The sitemap SHALL contain only the seven intended public routes and SHALL use the canonical `https://tinyassets.io` origin with trailing slashes. These declarations are advisory web metadata and MUST NOT be treated as authentication or access control for private application surfaces.

#### Scenario: Crawler requests policy

- **WHEN** a crawler reads `/robots.txt`
- **THEN** it receives an allow policy for the public site, explicit exclusions for private route prefixes, and the canonical sitemap location

### Requirement: The Public Site Has One Publisher

The public site SHALL publish only through `.github/workflows/deploy-site-react.yml` when site source lands on `main`. Pull requests SHALL be reviewed with the local preview commands in `WebSite/PREVIEW.md`. There is no per-PR Cloudflare preview Worker, preview Wrangler config, or preview-worker workflow.

#### Scenario: A pull request changes the site

- **WHEN** a pull request changes site source
- **THEN** reviewers use the local preview in `WebSite/PREVIEW.md`
- **AND** the site publishes when that change lands on `main` through `deploy-site-react.yml`
