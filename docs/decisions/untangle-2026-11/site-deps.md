# site-deps as built (2026-11-14)

The public site's production dependencies (`WebSite/site-react`) had 1 critical and 3 high
advisories (`docs/concerns/2026-07-27-public-site-privacy-deps-ci.md`, part 2). They now have
none, at any severity, and a check keeps it so.

## 1. What the audit found, and what reaches the shipped site

`npm audit --omit=dev` at `origin/main` `7c15bddb0` reported four packages. The site ships as a
static export (`next.config.mjs`: `output: "export"`) published to GitHub Pages. No Next.js
server, image optimiser, middleware, Server Action or React Server Components endpoint runs in
production. postcss, nanoid and source-map-js run only inside `next build`, on the site's and the
design system's own CSS.

| Package | Severity | Advisories (fixed in) | Reaches the shipped site? |
|---|---|---|---|
| `next` 14.2.15 | critical | GHSA-f82v-jwr5-mffw (14.2.25): "bypass authorization checks … if the authorization check occurs in middleware" | No: the site has no middleware, and an export has none at runtime. |
| | critical | GHSA-p293-qw3h-jr36 (15.5.24): RCE "when the server is hosted on machines using a Windows filesystem" | No: no server. |
| | critical | GHSA-2xp9-vwfh-vxw4 (15.5.24), GHSA-2464-fhr4-w9xw (15.5.28): RCE in "Image Optimization" through `sharp`/`libheif` | No: `images.unoptimized`, and no optimiser runs on Pages. |
| | high | GHSA-mwv6, -5j59, -h25m, -q4gf, -8h8q (14.2.34 to 15.5.16): DoS from "a malicious HTTP request … to any App Router endpoint" or "Server Function endpoint" | No: no endpoint; the HTML is prerendered. |
| | high | GHSA-m99w, -p3m5 (15.5.21, 15.5.28): DoS "with at least one Server Action"; GHSA-89xv (15.5.21): SSRF "when a Server Action forwards or redirects" | No: the site has no Server Action. |
| | high | GHSA-c4j6 (15.5.16): SSRF via "the built-in Node.js server" WebSocket upgrades | No: no server. |
| | high | GHSA-36qx (15.5.16): "Pages Router with `i18n`" and middleware | No: App Router, no i18n, no middleware. |
| | high | GHSA-p9j2 (15.5.21): `rewrites()`/`redirects()` with a request-controlled destination | No: the one rewrite is dev-only (`/mcp`, fixed host). |
| | moderate, low | 18 more: image optimiser, cache poisoning, middleware redirects, dev-server origin check, CSP-nonce and `beforeInteractive` XSS | No: server-only, or features the site does not use (no `next/script`, no nonces). The dev-server one (GHSA-3h52, low) reaches a developer running `npm run dev`. |
| `postcss` 8.4.31 (pinned by `next`) | high | GHSA-6g55 (8.5.12), GHSA-r28c (8.5.18): "An attacker who controls the CSS input" can make the build read local files through `sourceMappingURL` | No: build-time only, and every CSS input is first-party. |
| `nanoid` 3.3.15 | high | GHSA-28wg (3.3.16), GHSA-2v37 (3.3.18): an infinite loop when "an application … passes an unvalidated, attacker-controlled" size | No: build-time, called by postcss with fixed sizes. |
| `source-map-js` 1.2.1 | high | GHSA-68fv (1.2.2): an event-loop DoS from an attacker-supplied indexed source map | No: build-time, first-party maps. |

So none of the four reached a visitor. They are fixed anyway, for two reasons. The build and
`npm run dev` run them on contributors' machines and CI. And the check in §4 can then be a
plain audit with no allow-list: an allow-list of "doesn't reach an export" is a copy of this
table that drifts.

## 2. The versions

- **`next` 14.2.15 → 15.5.28.** No 14.x release clears the criticals: 14.2.35, the last, still
  carries GHSA-p293, -2xp9 and -2464 and nine highs, which were fixed only on 15.5 and 16. 15.5.28
  is the smallest release that clears every `next` advisory.
- **16.x was tried and rejected.** 16.4.1 audits clean with no override, but it builds with
  Turbopack by default. Turbopack refuses `@tiny/design-system`, a `file:../design-system` link
  outside the project root ("Can't resolve '@tiny/design-system/styles.css'"). Taking 16 would
  need `turbopack.root` or `--webpack`, a different bundler and CSS minifier, removing `next lint`,
  and Node 20.9 or later. That is a bundler migration, not a security fix.
- **`postcss` is overridden to 8.5.23 under `next` only** (`overrides.next.postcss`). Every 15.5
  release pins postcss at exactly 8.4.31, so no `next` 15 clears GHSA-6g55 and -r28c by itself.
  8.5.23 is the version `next` 16 itself pins, and it also clears the moderate GHSA-fxqj. The
  exported CSS is byte-identical with and without the override. It is the one value outside a
  package's own declaration. It goes when the site moves to a `next` whose own postcss pin is
  8.5.23 or later. `npm ls postcss` prints it as `overridden`, so it is visible there.
- **`nanoid` 3.3.20 and `source-map-js` 1.2.2** come from refreshing the lockfile with
  `npm audit fix`, inside postcss's own ranges.
- **`react` and `react-dom` stay at 18.3.1.** The upgrade guide sets React 19 as the minimum
  for the App Router, but the App Router renders with the copy of React that `next` vendors, not
  the installed one. The export bundles React `18.3.0-canary-14898b6a9` before and
  `19.2.0-canary-0bdb9206` after. The installed 18.3.1 reaches no shipped byte, so bumping it
  changes nothing and was left.
- **The rest of the 15 upgrade guide does not apply here.** The site uses no `params`,
  `searchParams`, `cookies()`, `headers()`, server `fetch`, `next/font`, `next/image`,
  `next/script`, route handler or Server Action. Every `next.config.mjs` key it uses
  (`distDir`, `trailingSlash`, `images.unoptimized`, `transpilePackages`, `basePath`,
  `assetPrefix`, the dev `rewrites`, `skipTrailingSlashRedirect`) is unchanged in 15.
- **Two dead scripts were removed.** `start` (`next start`) cannot serve an `output: "export"`
  build, and `lint` (`next lint`) has no ESLint installed and is deprecated in 15.5. Nothing
  calls either one.
- `next` 15 adds `sharp` as an optional dependency. The site never calls it (`images.unoptimized`).

## 3. The export, before and after

Both trees were built from the same checkout (design system built once) and compared with
hashed names normalised:

- **HTML:** all 27 HTML files have the same visible text, links and image attributes. One head
  change on every page: `<link rel="manifest">` loses `crossorigin="use-credentials"`, because
  `next` 15 no longer adds it by default. The manifest is same-origin and Pages sets no
  cookies, so the fetch is the same. Explained; not fixed.
- **CSS:** the content-hashed stylesheet differs only in colour notation. `next` 14's minifier
  rewrote the source's `rgba(242, 239, 230, 0.14)` as `hsla(45,32%,93%,.14)`, and `next` 15
  keeps the authored `rgba(...)`. Screenshots differ by at most 2 of 255 in a channel, on
  borders. The new output is the source token exactly.
- **JS and `.txt` (RSC payloads):** framework runtime only (React 19 canary, shared first-load
  87 kB → 103 kB). Page chunks shrank.
- `npm test` passes 41 of 41, and `scripts/sweep.py` is clean before and after (errs 0, warns 0,
  no overflow, every alias lands). `npm run dev` serves `/` and `/fine-print/`.

## 4. The guard

No workflow audited the site, and none runs the site on a pull request at all:
`deploy-site-react` runs only on `main`. The new `.github/workflows/site-audit.yml` runs
`npm audit --omit=dev --audit-level=high --package-lock-only`:

- **on a pull request that changes the site's `package.json` or `package-lock.json`** (or the
  workflow itself). Only a dependency change can bring in a vulnerable version, so an advisory
  published upstream never turns an unrelated pull request red;
- **weekly, and on dispatch.** A failing run opens one issue (found by its title), or comments
  on the open one. The first clean run closes it. Only this job gets `issues: write`, and it never
  runs on a pull request. That job runs one at a time (`concurrency`), so a dispatch during the
  weekly run cannot race it into a second issue.

It is not a step in `deploy-site-react`. There, a newly published advisory in an unreachable
build tool would block a copy fix from shipping. The weekly issue covers the same exposure
without blocking.

Proof: the check exits 1 on `origin/main`'s lockfile and 0 on this branch's.

## 5. Left

- `WebSite/design-system` dev dependencies (`storybook` and its tree) and
  `site-react/preview-deploy-tools` (`wrangler`) still carry advisories. They are dev tools that
  ship nothing, and they fall outside "production dependencies" and this check.
- The concern's part 3 (repository-level secrets readable by a same-repo pull request) stays open
  in the concern file.
