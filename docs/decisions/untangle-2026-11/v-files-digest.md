# B7 Files content digests as built (2026-11-18)

Files uses the contract's SHA-256 for chunk verification, the digest supplied to the bucket for sealed bytes, and manifest references. Each hashes the same bytes and compares or emits lowercase hex. The HMAC that conceals chunk names and the authenticated encryption under each owner's key retain their distinct purposes.

Files pins its Workers compatibility date to `2026-10-23`. Cloudflare enabled Node compatibility by default for dates starting `2026-08-04`, including `node:crypto`. The current Files configuration therefore already supports `contract/src/digest.ts`. Wrangler 4.154.0 bundled the actual Files entry point in an offline dry run using its existing configuration. The generated module imports `createHash` from `node:crypto`. [Cloudflare compatibility change](https://developers.cloudflare.com/changelog/post/2026-08-04-nodejs-compat-default/)
