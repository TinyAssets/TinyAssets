/**
 * The doors' public identity, the facts more than one Worker serves by (`identity.json`, its
 * one copy, which the doors' config, the deploy kit and the plugins read too): the zone; the
 * doors' host and origin; the MCP door's resource; where the upstream sign-in returns the
 * browser; the first-party app's client document and redirect, which the doors serve and the
 * deploy kit's probe signs in with; the first-party resource, where the owner's own app is,
 * whose tokens the doors take for `/app/api/*` and whose pages the owner's object cites a file
 * by (`owner/src/files.ts`); the header every doors answer names its version in; the events
 * E2B's lifecycle hook takes; and how a route of its `routes` takes a URL.
 */
import identity from "../identity.json" with { type: "json" };

// Read as text (a template), so this module holds strings alone, never the shared file
// object (`ci/kit/module-state.ts`).
/**
 * Our zone: the Cloudflare zone, the public site's host, and every name under it, the doors'
 * host and the egress host among them.
 */
export const ZONE = `${identity.zone}`;
/**
 * The doors' host: the zone, or one name under it while the live stage runs (d3 §H). The
 * routes, the issuer and every URL below are on it.
 */
export const HOST = `${identity.host}`;
/** The doors' public origin. */
export const ORIGIN = `https://${HOST}`;
/** Where the upstream sign-in returns the browser: the `redirect_uri` WorkOS lists. */
export const UPSTREAM_CALLBACK = `${ORIGIN}${identity.signin.callback}`;
/** The first-party app's Client ID Metadata Document, served by the doors. */
export const WEB_CLIENT = `${ORIGIN}${identity.first_party.client}`;
/** Where the first-party app finishes its sign-in. */
export const WEB_REDIRECT = `${ORIGIN}${identity.first_party.redirect}`;
/** The MCP door: the outside resource MCP clients' tokens are bound to. */
export const MCP_RESOURCE = `${ORIGIN}${identity.mcp}`;
/** The first-party resource: the owner's own app, and its tokens' audience. */
export const APP_RESOURCE = `${ORIGIN}${identity.first_party.resource}`;
/**
 * The header naming the doors' version that served an answer, its id (`CF_VERSION_METADATA`):
 * Cloudflare ignores a version override for a version outside the deployment and still
 * answers, so the deploy kit's smoke (`deploy/canary.ts --smoke`) reads which one answered.
 */
export const VERSION_HEADER = "x-tinyassets-version";

/**
 * The E2B lifecycle events the doors' hook (`identity.json` `hook`) takes, the two that carry
 * a billed execution: the doors deliver them (`doors/src/e2b-hooks.ts`), and the deploy kit
 * registers the hook for them (`deploy/deploy.ts` `E2B_HOOK`).
 */
export const E2B_HOOK_EVENTS = Object.freeze(["sandbox.lifecycle.paused",
                                              "sandbox.lifecycle.killed"] as const);

/**
 * Whether the Worker route `pattern` takes `url`, as Cloudflare matches one (its routes page):
 * against the whole URL a request carries, its query included, and never its fragment, which
 * no request carries. A pattern ending in `*` takes every URL it begins, any other only
 * itself. Cloudflare refuses an infix `*`, and a leading one in a host, which these patterns
 * never hold, is refused here too. Both are written alike, without a scheme: from the host
 * (the deploy kit's patterns) or from the path (`identity.json`'s).
 */
export function routeTakes(pattern: string, url: string): boolean {
  const star = pattern.indexOf("*");
  if (star !== -1 && star !== pattern.length - 1) {
    throw new Error(`the route ${pattern} holds a * before its end`);
  }
  const sent = url.split("#", 1)[0]!;
  return star === -1 ? sent === pattern : sent.startsWith(pattern.slice(0, -1));
}
