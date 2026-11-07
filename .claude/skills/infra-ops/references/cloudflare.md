
# Cloudflare (infra-ops reference)

Automate Cloudflare account admin using the host's logged-in browser (via `scripts/lead_browser.py`) for one-time dashboard operations, and `scripts/emergency_dns_flip.py` for API-driven operations going forward.

Reuses the shared browser lock pattern — see `references/godaddy.md` for the base lock mechanics.

## When to use

- Minting API tokens (one-time dashboard operation).
- Inspecting DNS records or Worker routes when emergency access needed.

For repeated DNS CRUD + Worker Route CRUD once a token exists, prefer `scripts/emergency_dns_flip.py` over browser automation.

## Dashboard patterns learned 2026-04-19 (Worker deploy + token mint attempt)

### Monaco editor clipboard paste (Worker script paste)

Works reliably via focus + `navigator.clipboard.writeText` + Ctrl+V. See `references/godaddy.md` §Cloudflare Workers — this pattern is proven.

### Radix UI zone dropdown (Worker route binding)

`aria-haspopup="menu"` trigger inside `form#domains_and_routes_form`; options render with `role="menuitem"`. See `references/godaddy.md` §Worker-route-binding.

### Custom Token form: react-select dropdowns (KNOWN HARD)

The `/profile/api-tokens` Custom Token form uses **react-select v5**, NOT native `<select>` or Radix menus.

**DOM fingerprint per dropdown:**
- `<input id="react-select-N-input" role="combobox" disabled tabindex="0">` — hidden input, disabled until menu opens.
- Parent container: `<div class="react-select__control">` — the clickable trigger.
- Sibling: `<div class="react-select__value-container">` — displays current value.
- Menu: `<div data-testid="api_token_permissions_options">` — intercepts pointer events when open.

**Open-and-pick pattern (NOT fully reliable yet — documented for next iteration):**

```python
# From a script attached to CDP Chrome
control = page.locator("#react-select-2-input").locator(
    'xpath=ancestor::div[contains(@class, "react-select__control")][1]'
)
control.click()
# Input becomes active; type filters the menu
page.keyboard.type("Zone", delay=30)
page.keyboard.press("Enter")
```

**Gotchas discovered:**
- `nth(N)` on `[role="combobox"]` includes the sidebar "Quick search" combobox (position 0) — throws off indexing. Always filter by `id^="react-select-"`.
- Clicking `.react-select__control` while a menu is already open triggers `<div data-testid="api_token_permissions_options"> intercepts pointer events`. Press `Escape` first.
- After picking an option, the menu may auto-open the NEXT dropdown. Add `time.sleep(0.5)` and check state.
- Cloudflare's Custom Token page may render a COMPOUND picker (single dropdown with "Zone — DNS — Edit" composite options) OR three separate dropdowns depending on the page version. Inspect DOM before scripting.

### When to stop automating + hand to host

Rule-of-thumb for 1-time dashboard operations:
- If the script draft takes >5 tool calls without a green run → release the browser lock and ask host to finish manually.
- Reserve automation for API-driven REPEATED operations.

Why: Monaco paste + Radix dropdowns cost ~5 tool calls to wire up; react-select can cost 10+ without reliability. Host finishes a form in 30 seconds.

**Once the token exists:** EVERYTHING downstream (DNS CRUD, Worker deploy, Worker route CRUD) automates via API reliably. The dashboard is only a one-time gate.

## API patterns (prefer these)

### DNS record CRUD + Worker Route CRUD
Use `scripts/emergency_dns_flip.py` (shipped in Row N, commit `ff89a85`). Supports upsert/delete DNS records + Worker routes with dry-run default + single-match ambiguity guard + TTL validation.

Required env:
- `CLOUDFLARE_API_TOKEN` — minted via the dashboard flow above.
- `CLOUDFLARE_ZONE_ID` — from zone overview page (not secret; copy from dashboard).

### Worker deploy / rollback
Platform Workers deploy only through the **Platform deploy** workflow (`platform/deploy/checklist.md`). The old proxy (`deploy/cloudflare-worker`) deploys only through `deploy-worker.yml`; after mode `routes`, a hand `wrangler deploy` of it would re-add its `.well-known/oauth-*` routes (`openspec/changes/clean-sheet-platform/tasks.md` §9 R1).

Rollback: `wrangler rollback --version-id <previous>` on the named Worker, or revert on `main` and let its workflow redeploy.

### Zone-level settings
Cloudflare REST API v4 — `GET/PATCH /zones/{zone_id}/settings/{setting_name}`. Direct `requests` calls; no wrapper needed for one-off changes.

## Token scope reference

Minimum scopes for the workflow uptime stack:
- **Zone:DNS:Edit** (scoped to tinyassets.io) — DNS record CRUD for emergency routing + future non-Worker records.
- **Zone:Workers Routes:Edit** (scoped to tinyassets.io) — add/remove apex `/mcp*` Worker binding.
- **Account:Workers Scripts:Edit** — deploy or rollback the Worker code itself.

The platform's deploy kit (`platform/deploy/deploy.ts`, `platform-deploy.yml`) uses the
`CLOUDFLARE_API_TOKEN` repository secret. It needs Account R2, Workers KV Storage, Queues and
Workers Scripts edit, Zone Workers Routes edit, and Zone DNS Edit:
- mode `routes` lists the egress host's DNS records (the API reference accepts DNS Read or
  DNS Write);
- mode `handback` deletes the record the egress custom domain made (DNS Write).

DNS Edit is the scope recorded above. A run has shown the read:
`cloud-only-preflight.yml` run 35704397255 (2026-09-22) read the zone's DNS records with this
secret, and `internal_origin_dns` passed. No run has written DNS with it yet. So nothing is
owed while that record holds. To check without the token's value: the dashboard's My
Profile → API Tokens page lists its permissions (`platform/deploy/checklist.md`).

Token naming: `workflow-<purpose>` (e.g. `workflow-cutover`, `workflow-ci-deploy`).

## Rotation cadence

- API tokens: 180 days (monitored by `.github/workflows/secrets-expiry-check.yml` — opens GitHub issue 30 days before expiry).
- Worker script: no rotation; redeploy on change.
- DNS: no rotation.

## Related references

- `references/godaddy.md` — GoDaddy dashboard + Websites + Marketing + base browser-lock pattern + Monaco/Radix helpers.
- `ui-test` — Chrome DevTools MCP for richer DOM inspection if Playwright selectors fail.
