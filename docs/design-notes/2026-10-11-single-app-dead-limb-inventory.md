# Single app, and the dead-limb inventory

**Date:** 2026-10-11
**Base:** `65a363b75` on `main`
**Status:** design only. This change adds the document. It does not delete, move, or rewire code.

The product shape in `AGENTS.md` Direction is one agent in the chat, on any device the person already has, plus the Claude and ChatGPT connectors. This note maps how the repo matches that shape today, then lists the older limbs and a prune order that existing CI can check one step at a time.

## How to read the labels

**Evidence** is a path, a test, a git command, or a count taken on this commit. **Inference** is a judgment from that evidence plus Direction. A last-commit date is the last commit that touched the path, which is often a rename or a docs sweep, not proof the feature is alive.

`git ls-files` counts below exclude nothing; they are tracked files. Reference counts used `rg -l` and then dropped `packaging/`, because that tree is a committed copy of `tinyassets/`. A substring search overcounts: `archive/` matching the word "archive" inside a Python file is not an import of the `archive/` directory. Those cases are marked.

Classes:

| Class | Meaning |
|---|---|
| core-keep | On the path the one app uses, or required to build and run that path. |
| superseded | Something else now does this job. Named below. Still referenced, so not deletable yet. |
| idea-to-revive-later | The product idea can return inside the one app. The current module should not be the return vehicle. |
| dead | A content search found no references. Rare. Absence of a search hit is not proof a human never runs the file by path. |

## Target

One core, thin shells.

```text
Web browser  Android (Capacitor)  iOS (Capacitor)  Desktop (Electron)
        \            |                    |                /
         \           +--------------------+               /
          +---- https://tinyassets.io/app (one SPA) -----+
          |         owner door  /app/api  (reads)        |
          |         MCP         /mcp      (actions)      |
          +----------------------+-----------------------+
                                 |
          Claude / ChatGPT connector (MCP only, same /mcp)
                                 |
              one command center per owner (SQLite, files, soul)
              one conversation keyed on the WorkOS principal
              model calls only on that owner's connected provider
```

Direction items this diagram follows: the chat bubble is the product (1); abilities are files, skills, and packages (3); any device, and the connector directories (7); the hosted surfaces work with no owner machine turned on (8). The goals engine and fantasy-as-default are explicitly retired in that same Direction block.

The owner door versus MCP split is already how the SPA is written. `tinyassets/onboarding/app.html` says every read goes to `/app/api` (`tinyassets/owner_door`) and MCP keeps the actions `converse` and `write_graph`, because a model-sized MCP read once hid the request rail (comment at the owner-door section, citing 2026-09-30).

## Surfaces today

| Surface | Where it lives | How it reaches the core | Shared or copied |
|---|---|---|---|
| Web app | `tinyassets/onboarding/` (`app.html`, 10986 lines), mounted by `tinyassets/universe_server.py` at `/app` | Same process as `/mcp`. Flag `TINYASSETS_ONBOARDING_APP` or the mount 404s (comment at the Starlette routes). | This is the app. |
| Android and iOS | `mobile/` (57 tracked files). `capacitor.config.json` sets `server.url` to `https://tinyassets.io/app`. | WebView. Native code is the shell: splash, icons, `tinyassets://auth`. `mobile/README.md` states product logic stays in the web app. | Thin shell. No second chat. |
| Desktop chat | `desktop-app/` (9 tracked files). `src/main.js` loads the same URL. | Electron window. Sign-in returns through `tinyassets-desktop://auth`. | Thin shell. Same SPA. |
| Claude / ChatGPT | Public URL `https://tinyassets.io/mcp`, served by `universe_server.py`. Site constant: `WebSite/site-react/lib/site.ts`. | MCP only. No owner door. Reads are bounded for a model context (the app.html comment above). | Same daemon, narrower door. |
| Marketing site | `WebSite/site-react/` (part of 163 tracked files under `WebSite/`). | Links to `/app`, `/mcp`, and the Play closed test. Deploy is manual: `.github/workflows/deploy-site-react.yml` is `workflow_dispatch` only. | Separate frontend. It does not implement chat. |
| In-process agent tools | `tinyassets/engine_mcp_server.py` (5014 lines) | The command-center agent's own turn, not a user window. `docs/architecture.md` calls it the same handles served locally. | Second copy of the handle surface, beside `universe_server.py` (4435 lines). |
| Host tray | `tinyassets/desktop/` (13 files), `tinyassets_tray.py`, `tinyassets.pyw` | Runs a daemon on a machine. Installers: `.github/workflows/desktop-release.yml`. | A different product from `desktop-app/`. `desktop-app/README.md` says so. Direction item 8 wants the hosted app to work with no owner host online. **Inference:** the tray remains operator/distribution code, not the user app. |
| Local file MCP | `tinyassets/mcp_server.py`, script `tinyassets-mcp` in `pyproject.toml` | Its own FastMCP app. Instructions still say "autonomous workflow daemon" and default the universe to `output/default-universe/`. | Not the hosted connector. **Inference:** superseded by `universe_server.py` for every user surface. Still a console script (19 files mention `tinyassets.mcp_server` outside the module and the mirror, 5 of them tests). |

Continuity across web, phone, desktop, and the connector is a server property: one WorkOS principal, one command center, one conversation. The write-up with file cites is `docs/design-notes/2026-08-22-desktop-app-client-surface.md`. Those line numbers were checked when that note was written; this inventory does not re-verify each line. **Evidence** for the shells loading one URL: `mobile/capacitor.config.json`, `desktop-app/src/main.js`, `WebSite/site-react/lib/site.ts`.

## What is duplicated

| Duplication | Evidence | What a later prune would do |
|---|---|---|
| Plugin runtime mirror | `packaging/` is 768 tracked files. `docs/design-notes/2026-10-01-stop-committing-the-plugin-mirror.md` (status: proposed, not built) says `runtime/tinyassets/` is a byte copy staged by `packaging/claude-plugin/build_plugin.py`, kept honest by `scripts/check_mirror_parity.py`. The note's "542 files" is its own date; the 768 count is `git ls-files packaging` on this commit. | Generate the mirror at bundle time. One PR. The parity check has to move with it. |
| Two MCP servers | `universe_server.py` and `engine_mcp_server.py`. Canonical user handles are named in `universe_server.py` around the "seven handles" comment: `read_graph`, `write_graph`, `run_graph`, `read_page`, `write_page`, `converse`, `get_status`. | Keep both until a single registration serves the connector and the in-process agent. Merging them is a behavior change, not a file move. |
| Older daemon module | `tinyassets/daemon_server.py` is 5261 lines. `rg -l daemon_server` hits 487 files outside `packaging/`, including 76 under `tinyassets/` and 307 tests. `docs/architecture.md` calls it the older substrate still being split into `storage/`. | core-keep until those imports are gone. The name is legacy; the module is not unused. |
| Two spec homes | `openspec/specs`: 87 tracked markdown files. `docs/specs`: 52. `rg docs/specs/` hits `tinyassets/graph_compiler.py`, `tinyassets/api/market.py`, and others. | core-keep `openspec/specs` as the as-built home (`docs/architecture.md` says so). Treat `docs/specs` as superseded only per file, after each inbound cite is updated. |
| Two catalog prototypes | `prototype/workflow-catalog-v0` and `prototype/tinyassets-catalog-v0`, 30 tracked files each, each with its own `.github/workflows/`. | **Inference:** GitHub runs workflows from the repo-root `.github/workflows` (51 files), so the nested workflow files are not this repo's CI. Confirm in the GitHub UI before deleting. The SQL under `prototype/full-platform-v0/` is cited by `tinyassets/authoring/store.py` and `tinyassets/host_pool/__init__.py`. |
| Fantasy, twice | Root `fantasy_daemon/` (119 files, last touch 2026-10-07, commit message is the PLAN.md retirement) and `domains/fantasy_daemon/` (45 files, last touch 2026-10-11 inside the writer-custody fix). `pyproject.toml` registers `fantasy_daemon` and `research_probe` as domain entry points, and packages `fantasy_author`. | See the inventory. Do not delete either tree while entry points and tests import them. |

`app.html` is one file on purpose today. `docs/design-notes/2026-10-01-split-app-html-into-es-modules.md` is the split plan. Until that lands, every shell inherits one blob, which is the right sharing and the wrong edit surface.

## Inventory

Counts are tracked files. "Outside" means `rg -l` hits excluding the tree itself and `packaging/`.

### Core, keep

| Path | Why it stays | Evidence |
|---|---|---|
| `tinyassets/serve.py`, `universe_server.py`, `onboarding/`, `owner_door/`, `agent_loop/`, `providers/`, `boxes/`, `auth/` | The hosted app, the connector, sign-in, the owner door, the agent, and owner-bound models. | `docs/architecture.md` codemap. Shells load `/app`. |
| `tinyassets/engine_mcp_server.py` | The agent's tool door. | Architecture codemap. Large and easy to mistake for a second product. |
| `tinyassets/daemon_server.py` | Still imported across the package and the tests. | 487 `daemon_server` hits outside the mirror. |
| `mobile/`, `desktop-app/` | The device shells. | Config URLs above. Last touch 2026-10-11. |
| `WebSite/site-react/` | The public site in front of the app. | `deploy-site-react.yml` calls itself the production site. |
| `deploy/`, root `.github/workflows/` | How the one deployment and the shells are built. | 64 and 51 tracked files, last touch 2026-10-11. |
| `openspec/specs/` | As-built behavior. | Architecture map. 87 files. |
| `packaging/` until the mirror is generated in CI | Required by the parity gate. | Design note of 2026-10-01. Deleting it fails `scripts/check_mirror_parity.py`. |

### Superseded

The replacement is named. The code is still referenced.

| Limb | Replaced by | Evidence it is still wired | Last git touch |
|---|---|---|---|
| Goals engine as the product (`tinyassets/api/market.py`, 4380 lines; repo-root `goals/`, `branches/`, `nodes/`) | The chat app and the seven canonical handles. | `universe_server.py` imports `goals as _goals_impl`. Comment dated 2026-09-30: legacy fat tools `universe`, `extensions`, `goals`, `gates`, `wiki` are no longer registered; they remain functions the canonical routers call. `tinyassets/catalog/layout.py` `YamlRepoLayout` resolves `goals/<slug>.yaml`, `branches/<slug>.yaml`, `nodes/<branch>/<id>.yaml`. `tinyassets/api/engine_helpers.py` says production `output/`'s parent is that repo root. Tests construct `YamlRepoLayout` on `tmp_path`, not on the committed sample files (`tests/test_storage_phase7_layout.py`). | `market.py` is inside the live tree. `goals/` last commit is the 2026-06-26 naming sweep (`aa30a9c81`), 18 yaml files including `wedding-planner.yaml` and `doomed.yaml`. |
| Fantasy as the default domain (`domains/fantasy_daemon/`, root `fantasy_daemon/`, package `fantasy_author`) | Direction: fantasy is not the default. The app agent is. `tinyassets/__main__.py` still documents a Phase 5 bridge into `fantasy_daemon.__main__.DaemonController`. | Entry point in `pyproject.toml`. `rg fantasy_daemon` : 202 files outside those two trees and the mirror, 79 of them tests, 17 under `tinyassets/`. | Domain tree 2026-10-11 (custody fix, not a feature commit). Root runner 2026-10-07 (PLAN.md retirement). |
| `domains/research_probe/` | Direction item 3: abilities are skills and packages, not a second sample domain. | Entry point. `tinyassets/discovery.py` loads entry points. `tests/test_tinyassets_runtime.py` asserts the domain is discovered and loadable. 21 outside hits, 7 tests. | 2026-06-26 naming sweep. **Inference:** no product change since that sweep. |
| Token / treasury / paid market (`tinyassets/treasury/`, `tinyassets/payments/`, `tinyassets/bid/`, `bids/`) | Not a user surface of the one app. Architecture calls these "test currency only." A 2026-10-02 commit message says the unbuilt paid-market library was deleted (`065ea3ff7`). | `tinyassets/payments/settlement_ledger.py` still imports `tinyassets.treasury.distribution`. Outside hits: treasury 9 (5 tests), payments 19 (8 tests), bid 33 (9 tests). `tinyassets/api/market.py` is the goals/gates/escrow module. `platform` UI under `onboarding/` has no `market` match. | treasury 2026-06-26 rename. payments 2026-10-02 deletion commit. bid 2026-10-11 custody fix. |
| Host pool (`tinyassets/host_pool/`) | One droplet (`deploy/`). Direction item 8. | Imports from outside the package: `tests/test_host_pool_client.py` only, plus the mirror. `tinyassets/platform_secrets.py` says `host_pool` is never imported and lists `SUPABASE_DB_URL` as a template placeholder no code reads. The package still cites `prototype/full-platform-v0/migrations/001_core_tables.sql`. | Not re-dated here. The secrets comment is the evidence. |
| Local workflow MCP (`tinyassets/mcp_server.py`) | Hosted `universe_server.py` at `/mcp`. | Console script in `pyproject.toml`. Module docstring still says workflow daemon. | 2026-10-08, a command-center admission commit. |
| Host tray as the user client (`tinyassets/desktop/`, `tinyassets_tray.py`) | `desktop-app/` for the person, `deploy/` for the server. | Tray files are named by `openspec/specs/desktop-host-runtime/spec.md` and `tests/test_tinyassets_tray_watchdog.py`. `tinyassets/desktop/create_shortcut.py` mentions `tinyassets.pyw`. | desktop package 2026-10-02. |
| `docs/specs/` as a second spec authority | `openspec/specs/`. | 52 files. Still cited from `tinyassets/` (see duplication table). | 2026-10-07. |
| `docs/exec-plans/active/` | OpenSpec changes (`openspec/changes/`, 95 directories that are not `archive/`; `archive/` has 212 directories). | 38 tracked files under `active/`. Newest filename prefix is `2026-07-18` (one file); ten files are prefixed `2026-07-09`. **Inference:** stale relative to OpenSpec, which moved on 2026-10-11. Not a zero-reference claim. | Directory last touch 2026-09-27. |
| Plugin mirror committed in git | Build it in `build_plugin.py` and ship the artifact. | Parity gate. Design note 2026-10-01. | packaging last touch 2026-10-11. |

`openspec/specs/provider-routing/spec.md` still describes fallback chains that end at `ollama-local`, while `tinyassets/providers/router.py`'s docstring says those chains are a catalogue and the platform has no fallback. That is spec drift, recorded so a prune does not "restore" the local model as a platform LLM. **Evidence:** the two files disagree. Fixing the spec is a separate change.

### Idea, revive later, inside the app

| Idea | Where it would fit | What not to revive | Evidence |
|---|---|---|---|
| A long-running author or researcher (fantasy daemon, research probe) | A skill or package the owner edits in the command center (Direction item 3), talking through the same chat. | `domains/*` entry points, the Phase 5 bridge in `tinyassets/__main__.py`, repo-root yaml goals. | Direction text. Domain entry points still load. |
| Multi-step work with a goal and a branch | The agent's own files and `ta`, or a graph the chat compiles, scoped to one command center. | The global goals engine and `api/market.py` escrow. | Direction retires the goals engine. Canonical handles still say `run_graph` / `branch_id`. **Inference:** the graph machinery stays until a chat-only loop replaces those handles. That is separate from renting a model, below. |
| Paying contributors for a published command center | `openspec/changes/creator-revenue-share/` (proposal, 2026-10-01). A share of a subscriber's fee, in `tiny`, for published command-center pieces. | The old paid-market modules. The proposal says #4209 deleted that code and this change does not bring it back. | Proposal text. Different payer and different good from model rental. |
| Model marketplace / capacity sharing | Spare GPU for the same account that owns the machine. Another user's prompts stay off that machine. Detail below. | Cross-user rental of chat turns, a public opt-in that still posts plaintext, and `host_pool` REST, git-backed `bids/`, and flag-off escrow as the implementation. | The pieces exist and do a nearby job. None of them rents an LLM endpoint today. The plaintext HTTP call cannot be the rental wire. |
| Several machines sharing work (host pool, tray heartbeat) | The model-marketplace section, if the shared thing is a model. The hosted cell already runs with the owner's machine off. `openspec/changes/private-network-attach/` is a proposal for reaching a private network, with no product code in that change's proposal. | Supabase `host_pool` and a tray that bids for graph nodes. | `platform_secrets.py` comment. Proposal file exists. |
| Notes and a wiki the agent keeps | Already the direction of pages and soul files inside the command center (`read_page` / `write_page`). | The root `knowledge/` and `pages/` trees as a second wiki. | `knowledge/INDEX.md` is cited from `knowledge/SCHEMA.md`, `WebSite/04-deep-dive-v2-product-truth.md`, and `docs/historical/INDEX.md` (3 files). **Inference:** a scratch wiki, not the app. |
| Ideas inbox | Keep the habit of writing ideas down. The app does not need `ideas/` as a runtime. | Treating `ideas/PIPELINE.md` as a work queue beside `openspec/changes/`. | `scripts/capture_idea.py` and `ideas/INBOX.md` reference the inbox (search hit `ideas/INBOX` in 22 files, many of them inside `ideas/` and old plans). |

#### Model marketplace / capacity sharing

**Idea.** Someone with an unused self-hosted model (Ollama, llama.cpp, vLLM, LM Studio, or any endpoint the generic connect path can call) uses that spare capacity from the hosted app. A cross-user rental, where someone else's command center selects that machine, is the shape people mean by a marketplace. That shape is non-compliant for user prompts. See the hard requirement below.

**Class:** idea-to-revive-later, and only in the compliant shape (the owner's own machine, serving that owner's account). No module in this repo is a marketplace. The nearby pieces are superseded or flag-off, and they should not be the return vehicle.

**What exists (evidence), and what it actually does:**

| Piece | Where | What it is | What it is not |
|---|---|---|---|
| Host registration with a price | `tinyassets/host_pool/registration.py` takes `visibility` (default `"self"`) and `price_floor`. `tinyassets/host_pool/client.py` documents `self\|network\|paid`. | A REST client for a daemon host row. The as-built spec `openspec/specs/daemon-identity-and-host-pool/spec.md` says registration keeps `price_floor`, `max_concurrent`, and `always_active`, and that bid discovery only polls. | Not imported by the running daemon. `tinyassets/platform_secrets.py` says `host_pool` is never imported. Outside the package, only `tests/test_host_pool_client.py` imports it. |
| Paid / public request poll | `tinyassets/host_pool/bid_poller.py` and the same spec: fetch pending requests with `paid` or `public` visibility, 60s poll, forward new ids, do not claim or settle. | A tray notifier. The file's own header says "NOT auto-claim." | Not a model router. |
| Schema for hosts, bids, ledger | `prototype/full-platform-v0/migrations/001_core_tables.sql`: `host_pool.provider` is `local\|claude\|codex\|gemini`, `visibility` is `self\|network\|paid`, `price_floor` is numeric. `requests.inputs_visibility` is `owner-only` or `public`. `bids` reference a `host_id`. `ledger` is append-only. `013_paid_market_workflow.sql` repeats the visibility check. | A Postgres sketch the host-pool client and `tinyassets/authoring/store.py` cite. | Not the store the hosted app uses. The provider check is four names, which is the opposite of a provider-agnostic listing. |
| Node bids on disk | `tinyassets/bid/node_bid.py`: a cross-universe, bid-priced, single-node request as `bids/<id>.yaml`. `openspec/specs/paid-market-economy/spec.md`: claim is a file rename plus a git push. | Graph-node execution between daemons. | Not an LLM hour. A failed push reverts the claim. Settlement "is not race-atomic" (that spec's requirement title). |
| Escrow and the 1% take | `tinyassets/api/market.py` escrow actions require `TINYASSETS_PAID_MARKET=on`. `tinyassets/producers/node_bid.py` and `tinyassets/dispatcher.py` default the flag to `off`. `tinyassets/treasury/distribution.py`: `PLATFORM_TAKE_BP = 100` (1%), half of that to a bounty pool, integer MicroTokens. `tinyassets/payments/settlement_ledger.py` records the take. | Flag-gated test money for node escrow. Reads stay available when the flag is off (`paid-market-economy` spec). | Default off. The SPA has no `market` string under `tinyassets/onboarding/`. |
| Test-currency boundary | `docs/design-notes/2026-04-29-token-naming-and-test-currency.md`, accepted: roadmap touches only `test tiny` on Base Sepolia until a real-currency phase is opened. Public copy must not say the product pays in mainnet `Destiny (tiny)`. What exists, what the site says, and a self-custody payout or platform-spend shape: `docs/design-notes/2026-10-11-tiny-currency-rewards.md`. | The naming and the stop line. | Not a live payout. The legal page's "test tiny rail" sentence overclaims what the code does. |
| Identity hole on the money path | `paid-market-economy` spec, as-built limitation: `_current_actor()` in `tinyassets/api/engine_helpers.py` can fall back to `UNIVERSE_SERVER_USER`, and a host env identity can act for someone else. The spec says the hardened resolver in `tinyassets/api/permissions.py` is not yet the money-path authority, and the default-off flag is the mitigation. | A reason the old path is not safe to switch on. | Not a fixed marketplace. |
| Free-source pooling | `openspec/specs/free-source-pooling/spec.md`: after one of the owner's free sources is exhausted, try another source that owner accepted. "Another owner's source is never discovered or invoked." | Pooling the owner's own keys. | The rule a marketplace would have to replace with an explicit grant, not a fallback. |
| Bring-your-own endpoint | `ApiKeyHttpProvider` plus the two wire dialects. The owner's grant names the HTTPS URL. `tinyassets/providers/owner_binding.py`: the call uses that owner's credential, never the host process, never `ollama-local`. `openspec/specs/provider-routing/spec.md`: a missing model does not borrow another owner's connection; `authority_kind` is explicit (`subscription_snapshot` or `connection_grant`). | How one person connects a model they pay for themselves. | The lender's key stays in the lender's vault. There is no listing, meter, or second-party grant. |
| Command-center revenue share | `openspec/changes/creator-revenue-share/proposal.md` | A cut of a subscriber fee for published command-center pieces. It says the old paid-market code was deleted as dead code (#4209) and this change does not bring it back. | Not model rental. #4209 did not remove `api/market.py`; escrow is still in that file, behind the flag. **Evidence:** the proposal sentence and the file. **Inference:** "deleted" means the unbuilt library the 2026-10-02 commit message names, not every money module. |
| Private reachability | `openspec/changes/private-network-attach/proposal.md` | A proposed owner-network route so a private API can be reached without a public tunnel. Proposal only. | Not metering or payment. |

**How a shared grant would fit the provider router.** **Inference**, and **non-compliant** for another user's prompts. The hard requirement below withdraws it as a product design. It is recorded so a later change does not rebuild it by accident.

The owner connects the model the same way anyone connects a remote endpoint: public HTTPS, a wire the two dialects already speak (`chat_messages` for an OpenAI-compatible server), credential in that owner's vault, broker posts the body. That post is plaintext at the destination. A second account selecting that grant would put the second account's prompt on the first account's machine. That is the call the hard requirement forbids. It also contradicts `free-source-pooling`, which forbids using another owner's source, and it does not match `ollama-local`, which is the platform host's loopback and cannot serve a command center. The compliant use of the same wire is the owner calling their own grant.

Token counts in a chat response are best-effort and may be missing. The 1% take in `treasury/distribution.py` stays dark with the paid-market flag, the actor fallback, and the test-currency stop. None of that metering makes a cross-user body compliant. A model on a private LAN still cannot be dialed: the outbound check refuses non-global addresses. The owner's own machine is reachable today only as a public HTTPS endpoint they connect. `private-network-attach` is the proposal for reaching it without that public port. The old `host_pool.provider` check (`local|claude|codex|gemini`) is a vendor list, and it is not a rental wire.

**Risks.**

| Risk | What the repo already shows | What is still inference |
|---|---|---|
| Abuse | The broker will post to whatever HTTPS host the grant names, and it will not post to loopback, private, or metadata addresses. A grant is the authorization. The paid-market flag defaults off, and money actions are supposed to stick to the authenticated actor, with the env-identity hole recorded in the spec. | A renter who receives the raw key can keep calling after the listing is revoked. A lender who opens a tunnel publishes an endpoint the internet can find. Rate limits, abuse reports, and "this model may not be used for X" are not in the host-pool poller. |
| Privacy of prompts | `ApiKeyHttpProvider` sends the request body to the granted URL. The old SQL sketch defaults `inputs_visibility` to `owner-only`. Free-source pooling refuses another owner's source so this leak does not happen today. | Hard requirement, 2026-10-11: another user's machine must not receive the prompt or the user's data. The destination process sees the body. A disclosure sentence, an opt-in, or `inputs_visibility: public` does not change that. Cross-user rental of turns is non-compliant. |
| Payments | Integer MicroTokens, a 1% take, flag default off, test tiny only until the 2026-04-29 boundary is opened. The paid-market spec says a fractional JSON amount can be truncated by `int(...)`, and node-bid settlement is not race-atomic. Creator revenue share is a different flow and refuses to revive this code. | Charging a card or sending mainnet `tiny` is a new money decision. The founder has to open it. Doing it by flipping `TINYASSETS_PAID_MARKET` reopens the actor fallback. |
| Trust | The host-pool poller does not claim work, so a human still chooses. Model id on a bring-your-own connection is a string the connector typed. Tool support is declared, not probed. | The renter cannot tell from the platform whether the weights match the listing. A hostile model can lie in the completion. There is no reputation store in the pieces above. |
| Uptime | Direction item 8: the hosted app works with the owner's machine off. A listed local model is down when the lender's machine is down. The host-pool heartbeat is a REST `updated_at` loop inside a client the daemon does not import. `owner_binding.py` forbids falling back to the platform's own model when the lender is dark. | A marketplace that auto-fails over to a different lender's model would break the "explicit authority" rule unless the renter accepted that lender too. Spare capacity is not a platform SLA. |

#### Free tier and subscription on a contributor pool

**Correction, from the owner on 2026-10-11.** Free does not mean a lesser product. Same experience, same security, same capabilities on every tier. The only scarce resources are how many agent calls run at once, and how much cloud storage the account uses. A subscription already buys more of those two. It does not buy a safer model, a smarter model, or a private path that free accounts lack.

An earlier draft of this section gave the subscription a higher quality band and a larger daily cap, and treated a weak model as "good enough for free." That draft is withdrawn. It conflicts with this correction and with the founder text already in `usage_policy.py`.

**What concurrency and storage already do (evidence).** `tinyassets/usage_policy.py` is the only tier table. It quotes the founder on 2026-09-30: limits are cloud storage and how many agent calls run at once, and "we dont care what connections they have." The module says nothing a person sees depends on the tier except those two numbers and the upgrade link, and that there are no rate meters.

| | Free default | Paid default | Enforced by |
|---|---|---|---|
| Concurrent agent calls (seats), one pool per account | 3, of which 1 is reserved so chat is not stuck behind background work | 8, same reserve rule | `tinyassets/universe_seats.py`. Over the count, work waits. It is not dropped. |
| Cloud storage, one pool per account | 2 GiB | 20 GiB | The policy module names `tinyassets/storage_accounting.py` as the measurement. That module's own comment says platform bytes are not charged to the person. |

Env overrides exist (`TINYASSETS_FREE_SEATS`, `TINYASSETS_PAID_SEATS`, `TINYASSETS_FREE_STORAGE_GIB`, `TINYASSETS_PAID_STORAGE_GIB`). Checkout is `POST /app/billing/checkout` via `tinyassets/billing/stripe_adapter.py`. **TODO:** whether that checkout is charging live customers was not checked.

The connections people use today are the same shapes on both tiers: their own OpenRouter key (`acquisition_presets.json`; 50 `:free` requests a day recorded in `daily_cap_offers.json`), the other installed key cards (Groq, Google AI Studio, Mistral, Cerebras), a ChatGPT subscription card, the Codex and Claude Code CLIs, or their own HTTPS model server. One account does not call another account's model (`free-source-pooling` spec; `owner_binding.py`).

**Hard requirement, from the owner on 2026-10-11.** A user's prompts and data are never exposed to another user's machine. Any design that delivers plaintext prompts to a third-party host is non-compliant. This binds both tiers. Equal opt-in does not satisfy it. The earlier experiment, where a second account's prompt is posted to a volunteer's model and the screen says so, is withdrawn.

**What the current call exposes (evidence).** `tinyassets/providers/agent_inference.py` encodes the prompt, system text, and wire fields as JSON. `ApiKeyHttpProvider` posts that body to the granted HTTPS URL through the credential-blind proxy. TLS ends at the destination. On a tool turn the later rounds carry tool results, which are the user's pages and files when the tools read them. The proxy keeps the API key out of the application process. The destination receives the body. A search of the inference path found no confidential-compute, enclave, or encrypted-inference step. `tinyassets/platform_runtime_provenance.py` calls cloud admission an accidental-start backstop, never attestation. `tinyassets/storage/learned_models.py` says there is no attestation of model ids, and that an owner's model list is never shared. **TODO:** that was a code search, not a proof that no crypto library elsewhere in the tree could be reused for a different job.

**What can meet the bar.**

User-controlled routing to a destination that account already chose. Evidence: `router.py` leaves the chain empty when the served provider is outside `allowed_providers`, and the call fails closed. `TINYASSETS_PIN_WRITER` is retired. The destinations both tiers can connect today are their own HTTPS model, their own OpenRouter or other key card, or a ChatGPT or Claude subscription. Those calls stay on hardware or a provider that account connected. They do not create a community pool. There is no per-turn sensitivity flag. A classifier that sometimes sends a "safe" turn to a stranger would miss, and a miss is exposure. The compliant rule is that a stranger's machine is never a destination.

Capacity lent only to the same account. The contributor's machine serves command centers that account owns. A collaborator writing inside that owner's command center is already acting on that owner's data (`universe_server.py` treats collaborators as writers on the owner's center; seats and storage are per owner). Those turns may run on the owner's own machine. They may not run on the collaborator's home PC: that PC is another user's machine. `org_id` is copied from a WorkOS claim in `tinyassets/api/permissions.py` and is not a model-routing trust domain. "Team" in this note means that owner's command centers, not a WorkOS org and not a second TinyAssets account. Reaching the owner's machine without a public port is still the unbuilt `private-network-attach` proposal. Today the owner connects a public HTTPS endpoint they control.

A pool that never receives user content. Compliant when the payload is written by the platform and contains no user prompt, history, page, file, or tool result. A fixed health-probe string can be that payload. An agent turn cannot: the turn is the user's message plus whatever the tools read. This kind of pool can check that a machine is up. It cannot answer the user's chat.

Confidential compute or encrypted inference. Meets the bar only when the other machine never holds plaintext: the client verifies a hardware attestation of the exact runner, the prompt is decrypted only inside that enclave, and tool results take the same path. Nothing here implements that. A home Ollama, llama.cpp, vLLM, or LM Studio process sees the HTTP body. A volunteer asserting that their box is a TEE is not attestation. Building it would be a new system (attested confidential GPU, a pinned measurement, a tool channel that does not decrypt on the host). It is not a setting on `ApiKeyHttpProvider`, and a community of unattested home machines cannot offer it.

**A community pool of user turns cannot meet the bar.** The wire is plaintext HTTPS. The same exposure for free and paid is still exposure. A privacy sentence, a shared opt-in switch, and the old SQL `inputs_visibility` value `public` all still deliver the body. A pool limited to tasks the user marks public or non-sensitive still puts those tasks' plaintext on a third-party host, so it is non-compliant with this requirement. Meeting it would take an explicit owner decision to carve that case out of the requirement. This note does not carve it out.

**What the product can offer instead.** Both tiers keep connecting their own model or a provider they hold a key for. Seats and storage stay the only tier differences. A person with a spare GPU uses it for their own account, which is the bring-your-own-model path, not a market. People without a GPU do not borrow one. The platform still has no model of its own (`owner_binding.py`; ADR-006). Credits for hosting other people's turns are not a product under this requirement.

**Smallest next step.** No experiment that posts one account's prompt to another account's machine. The check the code already has is the one to keep: `free-source-pooling` never invokes another owner's source. A later private-attach change is how the owner's own machine becomes reachable without a public port. Encrypted inference stays out until an attested design exists, and that design is spec-first.

**Do not prune these pieces in the same PR that would build own-account reachability.** They are the only map of the old money and host-share behavior. A cross-user grant is a different change, and under this requirement it is not a change to build. Own-account private attach, if it is built, is spec-first because it changes who can be dialed.

### Dead, or as close as a search can show

| Path | Search | Result |
|---|---|---|
| `create-desktop-shortcut.bat` | `rg create-desktop-shortcut` | 0 files. **Evidence.** **Inference:** an old Windows launcher. `start-tinyassets-server.bat` is not in this row: the same search finds `tests/test_integration.py`, `start-tinyassets-server.vbs`, and the shortcut bat. |
| Nested prototype workflows | Path `prototype/*/.github/workflows/` (4 files: validate and ingest, twice). | **Inference:** not the workflows GitHub runs for this repository. Not "zero references" if a doc names them. Do not delete in the same step as `prototype/full-platform-v0/migrations/`, which Python comments cite. |

Nothing else in the survey met "no references." `goal_pool/` at the repo root is one README (`goal_pool/maintenance/README.md`, last commit 2026-04-14). The live producer is `tinyassets/producers/goal_pool.py` (outside hits 63, including `tinyassets/dispatcher.py` and `tinyassets/branch_tasks.py`). The directory is a leftover doc; the producer is superseded-but-wired.

`data/world_rules.lp` plus `tinyassets/constraints/` (4 files, last touch 2026-06-26 rename) is an ASP rules file. `world_rules` appears in `Dockerfile`, `deploy/docker-entrypoint.sh`, and retrieval/evaluation modules (24 outside hits, 10 tests). Not dead. **Inference:** world rules for the fiction daemon, not for the chat app. Revive only as a package the owner attaches.

## Workflows

51 files in `.github/workflows/`. A string scan (file contains `workflow_dispatch:` and does not contain `push:`, `pull_request:`, `schedule:`, or `workflow_run:`) marked 14 as dispatch-only. That scan is **inference**: a comment that mentions `push:` would hide a dispatch-only file, and a `tags:` trigger would look dispatch-only. Two were opened and are dispatch-only on purpose:

- `deploy-site-react.yml` — manual publish of the public site.
- `ios-release.yml` — signed IPA, no automatic App Store submit.

Those 14 are operator tools (resize, restart, DNS, drills, store signing). They are not dead limbs. Deleting one is an ops change with its own proof, not a cleanup commit.

The four workflows under `prototype/` are listed in the dead-or-close table.

## Safe sequence

Each step is its own PR. Each step leaves `python -m scripts.ci_structural_guards` and the tests it touches green. None of these steps is this PR.

1. **Stop adding a second UI.** New behavior goes into `tinyassets/onboarding/` and the canonical handles. Shells stay URL wrappers. Check: a shell diff that adds a chat renderer is out of scope. No file moves. Guard: existing mobile and desktop tests, plus `scripts/mcp_public_canary.py --assert-handles` when handles change.
2. **Generate the plugin mirror in the bundle build.** Follow `docs/design-notes/2026-10-01-stop-committing-the-plugin-mirror.md`. Update `scripts/check_mirror_parity.py` in the same PR so CI compares the build output. Do not delete `tinyassets/` .
3. **Split `app.html` into modules without changing requests.** Follow `docs/design-notes/2026-10-01-split-app-html-into-es-modules.md`. Proof is the existing app tests plus one pass through sign-in, connect, and a chat send. Shells should not change.
4. **Point catalog writes off the git repo.** `engine_helpers._storage_backend` still treats the parent of `output/` as a goals/branches repo. Until that writer targets command-center storage, do not move `goals/`, `branches/`, or `nodes/`. The PR that retargets the writer updates `tests/test_storage_phase7_*.py` and must not assume the committed sample yaml (`wedding-planner.yaml` and the rest) is what production reads. **Inference:** those yaml files look like fixtures; the writer comment is the reason they are not step 1.
5. **Drop `tinyassets/host_pool/` after a startup import check.** Evidence so far: only `tests/test_host_pool_client.py` imports it from outside, and `platform_secrets.py` says the daemon does not import it. The PR deletes the test with the package, and the prototype SQL comment in `authoring/store.py` either gains a one-line schema note in `tinyassets/` or stays until step 8. Re-grep `host_pool` in the same PR before deleting.
6. **Unhook fantasy and research_probe from the default entry points** only when `tests/test_tinyassets_runtime.py` and `tinyassets/__main__.py` no longer require them. Move the interesting behavior into a skill fixture under the command center in a later PR. Deleting `domains/fantasy_daemon/` in the same PR as the chat work will fail discovery tests.
7. **Narrow `api/market.py`.** The registered MCP tools already omit the legacy fat tools (comment in `universe_server.py`). A follow-up stops `read_graph` / `write_graph` from calling goals, gates, and escrow once the app has no caller. Proof is the public handle canary and the market tests, updated in that PR. This is the dangerous step. See risks.
8. **Retire prototype schemas** after steps 5 and 7 no longer cite `prototype/full-platform-v0/migrations/`. Then the two catalog prototypes and their nested workflows can go in one PR. `rg full-platform-v0` outside `prototype/` and the mirror was 21 files on this commit (docs, openspec, 4 tests, 3 under `tinyassets/`, `SUCCESSION.md`).
9. **Archive docs that nothing cites,** one directory at a time, newest citations first: a file under `docs/specs/` or `docs/exec-plans/active/` moves to `docs/historical/` only when `rg` shows no `tinyassets/`, `scripts/`, or `tests/` hit. `docs/concerns/` (239 files) is the open-findings inbox (`docs/architecture.md`). It is not a dead limb; resolving one means deleting that file when the finding is fixed, which is the existing rule.
10. **Naming.** `universe` remains the code and storage word. The open change is `openspec/changes/rename-universe-to-command-center/`. Do that as its own sequence. `tests/test_hard_rename_surfaces.py` already forbids retired Workflow strings on active surfaces. Do not mix the rename into a deletion PR.

`create-desktop-shortcut.bat` can ride along with any of these only after a second search still shows zero hits. It is not worth its own migration.

## Risks

- **Deleting a referenced limb fails CI and can fail production.** `daemon_server.py`, `api/market.py`, and the domain entry points look like leftovers and are on import paths. The class "superseded" means "do not delete yet."
- **The goals writer can touch the git repo.** `engine_helpers.py` documents that production resolves the catalog at the parent of `output/`. Moving `goals/` or `branches/` before retargeting that writer is a data-path change, not a cleanup.
- **Money code is still called.** Settlement and bids are integer MicroTokens wired through `market.py` and `payments/`. Removing them changes escrow and attribution behavior even if the SPA never shows a market. Treat it as a product decision, then a PR with the market tests.
- **Public MCP.** Handle changes need `python scripts/mcp_public_canary.py --assert-handles` after deploy (`AGENTS.md` fact 11). A docs PR does not deploy.
- **Two doors.** The connector never sees the owner door. A prune that moves an action from `converse` / `write_graph` onto `/app/api` only will break Claude and ChatGPT while the app still works.
- **Mirror and rename gates.** A `tinyassets/` edit that forgets the mirror fails parity. A Workflow-era name in an active file fails `test_hard_rename_surfaces.py`. Record dirs listed in that test (`openspec/changes`, `output`, `.cowork-revert-patches`, `.agents`) are allowed to keep old repo slugs.
- **Security invariants stay.** Cloud admission, SSRF, and owner-bound credentials are not cleanup targets. A prune that opens a localhost model path or a host-credential fallback violates Direction and `tinyassets/providers/owner_binding.py`.
- **This inventory can be wrong.** Substring search overcounts. Last-commit dates undercount abandonment when a sweep touches a file. Anything marked inference needs a fresh `rg` in the PR that deletes it. Same finding twice means stop and hand off (`AGENTS.md` loop item 7), not a broader delete.
