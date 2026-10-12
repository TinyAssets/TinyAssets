# Previewing & approving the site

This is the production source for `tinyassets.io`. These preview paths do not
change the live site. A push to `main` that changes site source
(`WebSite/site-react/**`, `WebSite/design-system/**`, `WebSite/shared/**`)
or `deploy-site-react.yml` runs that workflow. A manual run with
`confirm: deploy` is the rollback. See `../DEPLOY.md`.

## 1. Local hot-reload

```bash
cd WebSite/design-system && npm ci && npm run build   # first time, and after token edits
cd ../site-react
npm ci             # first time
npm run dev        # → http://localhost:3000
```

The dev server proxies `/mcp` → `https://tinyassets.io/mcp` server-side (no
CORS), but public site code supplies no bearer and does not use that route.
The commons list shows its labelled checked-in snapshot; live readings require
a signed-in connector.

## 2. Production-shaped static preview

```bash
npm run preview    # next build + serves the real static export at http://localhost:4322
```

No `/mcp` exists on localhost. The protected surfaces render the same labelled
snapshot and signed-in state as the production-shaped public site.

## 3. Tests and the rendered sweep

```bash
npm test                                  # node --test scripts/*.test.mjs
npm run build && python scripts/sweep.py  # every route + alias, phone + desktop
node scripts/snapshot-public.mjs          # refresh lib/mcp-snapshot.json from the live endpoint
```

`scripts/` holds:

- `canonical-mcp-contract.test.mjs`, `public-boundary.test.mjs`: the connector
  contract and the boundary the pages must keep (no public browser MCP request,
  snapshot provenance retained, no operator status in a browser).
- `preview-worker-security.test.mjs`, `validate-preview-*.mjs` (+ tests): the
  hosted-preview trust boundary used by `preview-worker.yml`,
  `preview-worker-deploy.yml` and `preview-security.yml`.
- `snapshot-public.mjs`: the snapshot baker (public universe list only).
- `sweep.py`: the Playwright sweep (Python `playwright`).

## 4. Isolated hosted PR preview (after activation)

After the bootstrap is on `main` and the dedicated preview account/environment
is provisioned, each eligible same-repository pull request can get an isolated
URL of the form:

`https://p<pr-base36>-r<run-base36>-a<attempt-base36>-tiny-site-react-preview.<preview-account-subdomain>.workers.dev`

Treat everything rendered at that URL as untrusted review input. The trusted
Worker runs before every asset lookup. It canonicalizes case, escapes, slash
forms, and dot segments, returns no-store `503` for every MCP-equivalent path
and the `/.well-known/oauth-*`, `/.well-known/openid-*`, and
`/.well-known/mcp*` discovery namespaces, and fails closed when a segment
becomes empty after normalization or a path cannot otherwise be safely
canonicalized. Only other canonical paths fall through to static assets.
Sanitized artifacts reject literal-percent path components so every accepted
path is serveable under the same decoding policy. Preview JavaScript therefore
cannot acquire a same-origin bridge to production data.

An independent `preview-security` workflow checks the trust-boundary contract
on every pull request and `main` push. The publication path then has four
isolated authorities:

1. `preview-worker.yml` runs pull-request-controlled installs, tests, and builds
   with a read-only repository token/permission, no deployment secrets, and no
   persisted checkout credential. It uploads only the static `out/` tree.
2. The trusted default-branch `preview-worker-deploy.yml` validates the exact
   workflow, repository, open pull request, current head, and exact artifact ID
   plus bounded metadata without loading an environment or secret. It copies
   only bounded regular static files into a clean tree, records a deterministic
   manifest, and transfers both without claiming an independently verified
   archive digest.
3. A fresh protected-environment job regenerates the manifest from its
   independently revalidated tree, requires a byte-identical match, then hashes
   those regenerated manifest bytes for the published provenance receipt. It
   supplies the Worker program and configuration from its exact trusted commit,
   installs lockfile-pinned deployment tooling without lifecycle scripts,
   rechecks the current pull-request head, and uploads an undeployed Worker
   version under a never-reused alias derived from the PR, run, and attempt IDs.
   A trusted parser rejects ambiguous or malformed Wrangler receipts.
4. A separate least-privilege job rechecks the head before posting the
   provider-generated immutable version URL, alias URL, full SHA, run/attempt,
   exact source artifact ID, verified sanitized-manifest SHA-256, and Cloudflare
   version ID. An API-reported artifact digest is not treated as verified byte
   provenance.

The credentialed upload targets the GitHub environment `react-preview`. It must
not receive credentials until the following isolated infrastructure exists:

- a dedicated Cloudflare **preview account** with no production Workers, routes,
  domains, data, or credentials; enable its `workers.dev` subdomain and create
  the fixed `tiny-site-react-preview` Worker as a one-time host action because
  the workflow deliberately cannot provision or auto-create targets;
- enable Cloudflare Access for this Worker's base `workers.dev` hostname and
  its alias/version Preview URL hostnames, and allow only named reviewers or
  the approved organization; do not configure `Everyone`, `Bypass`, or
  public-path exceptions. These URLs are otherwise public, and arbitrary
  pull-request JavaScript is not an acceptable public hosting surface;
- with a host-held preview-account credential, upload one inert trusted
  bootstrap version under a unique alias without pull-request bytes or a
  GitHub credential, then prove anonymous denial and authorized-reviewer
  loading on the real base, bootstrap alias, and version hostnames;
- environment secret `CLOUDFLARE_PREVIEW_API_TOKEN`, containing a new token with
  only Workers Scripts write permission in that preview account, configured
  only after the Access proof is independently accepted;
- environment variable `CLOUDFLARE_PREVIEW_ACCOUNT_ID`, containing that preview
  account's ID.

Cloudflare Workers write permission is account-scoped, so a token in the
production account is not sufficiently isolated even when the workflow fixes
the Worker name. Never copy or reuse the production token. Restrict the
environment to `main`, require review, and disallow administrator bypass where
the repository plan supports those controls.

Before activation, a qualifying pull request may cause GitHub to materialize an
empty, unprotected `react-preview` environment record. The upload still fails
closed because both preview credential values must be non-empty. Activation
must create or harden that environment record and accept Access proof before
adding either credential.

`workflow_run` uses the workflow definition already present on the default
branch. Fork pull requests may build and test without secrets, but they never
enter credentialed publication. Same-repository pull requests are rechecked
immediately before credential use to confirm their current head still matches
the validated build.

Each successfully published eligible build uses a never-reused alias and a
provider-generated immutable version URL, so a stale upload cannot change the
bytes behind earlier review evidence. PR close/merge and one-day GitHub
artifact expiry do **not** delete Cloudflare versions or their version URLs.
For incident response, remove the GitHub environment secret and disable
Preview URLs or delete the dedicated Worker/account.

## The approval loop

1. A change lands on a branch / PR (made by you or by an agent).
2. Reviewers use the local preview, the sweep screenshots, and (once activated)
   the isolated hosted preview URL, treated as untrusted input.
3. On approval, merge to `main`. A merge that changes site source, or the
   deploy workflow, publishes through `deploy-site-react.yml`.
4. Complete the public canary and rendered-browser checks in `../DEPLOY.md`.
   Roll back by running that workflow with `confirm: deploy` on the last good
   revision.
