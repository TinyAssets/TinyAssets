/**
 * The doors Worker's fixed facts: its one public origin, the OAuth issuer and resources,
 * the first-party clients, and the environment it binds (`wrangler.jsonc`, held by
 * `guards/config.test.ts`).
 */
import type { OwnerDO } from "../../owner/src/owner-do";

/** The one configured public origin; the Capacitor WebView loads it too. */
export const PUBLIC_HOST = "tinyassets.io";
export const PUBLIC_ORIGIN = `https://${PUBLIC_HOST}`;
/** The authorization server's RFC 8414 issuer: our own, not the identity vendor's. */
export const ISSUER = PUBLIC_ORIGIN;
/** The outside resource: MCP clients' tokens are bound to it. */
export const MCP_RESOURCE = `${PUBLIC_ORIGIN}/mcp`;
/** The first-party resource: the owner's own app's tokens, for `/app/api/*`. */
export const APP_RESOURCE = `${PUBLIC_ORIGIN}/app`;
/** Where the upstream sign-in returns the browser. */
export const UPSTREAM_CALLBACK = `${PUBLIC_ORIGIN}/oauth/upstream/callback`;
/** The first-party app's Client ID Metadata Document, served by this Worker. */
export const WEB_CLIENT = `${PUBLIC_ORIGIN}/oauth/clients/web.json`;
/** Where the first-party app finishes its sign-in. */
export const WEB_REDIRECT = `${PUBLIC_ORIGIN}/app/signed-in`;
/**
 * The first-party clients: the only clients a token for `APP_RESOURCE` is issued to or
 * accepted from. Each is a CIMD URL on our own origin, so the list needs no registry row
 * and no config value.
 */
export const FIRST_PARTY_CLIENTS: readonly string[] = Object.freeze([WEB_CLIENT]);
/**
 * Origins a browser-based MCP client may send to `/mcp`. Empty: a present `Origin` is
 * refused (MCP 2026-07-28 Streamable HTTP), an absent one is allowed.
 */
export const MCP_ORIGINS: readonly string[] = Object.freeze([]);

/** Scopes the authorization server grants. Authority is the owner object's grant rows. */
export const SCOPES: readonly string[] = Object.freeze(["offline_access"]);

/** Token lifetimes (research §1.4): the grant slides while used; the fence never moves. */
export const ACCESS_TOKEN_TTL_S = 3_600;
export const REFRESH_TOKEN_TTL_S = 30 * 86_400;

/**
 * How old an upstream sign-in may be when the doors take it: the object opens a session
 * only for one at most this old, and a `prompt=login` flow is at most ten minutes long.
 */
export const SIGN_IN_MAX_AGE_S = 600;
export const CLOCK_MARGIN_S = 5;

/** The largest body each door reads; past it, `413` before anything is parsed. */
export const AUTHORIZE_BODY_MAX = 4 * 1024;
export const APP_BODY_MAX = 64 * 1024;
export const API_BODY_MAX = 1024 * 1024;

/** `OWNER_ISSUER` until the AuthKit slice sets AuthKit's production issuer. */
export const ISSUER_NOT_CONFIGURED = "not-configured";

/** What `wrangler.jsonc` binds, exactly (the config guard). */
export interface DoorsEnv {
  /** The owner Worker's namespace, by `script_name`; reached only in `binder.ts`. */
  readonly OWNER: DurableObjectNamespace<OwnerDO>;
  /** The OAuth provider's own store (its schema; never ours). */
  readonly OAUTH_KV: KVNamespace;
  /** The owner directory: routing hints and the `control` record (`directory.ts`). */
  readonly DIRECTORY: KVNamespace;
  /** Dynamic client registration, the deprecated fallback: exactly "true" or "false". */
  readonly DCR_ENABLED: string;
  /**
   * The upstream issuer whose subjects are owner ids as they stand (AuthKit's, so today's
   * owners keep theirs); every other issuer's subjects are namespaced (`binder.ts`).
   */
  readonly OWNER_ISSUER: string;
  /**
   * The other upstream issuers whose sign-ins name namespaced owners, as a JSON array:
   * each named by the slice that adds its provider (Sign in with ChatGPT, S10). Empty
   * today. Any issuer neither here nor `OWNER_ISSUER` is refused.
   */
  readonly FOREIGN_ISSUERS: string;
}

const ISSUER_URL = /^https:\/\/[^\s]+$/;

/** The owner issuer, an https URL; unset, every sign-in fails loudly. */
export function ownerIssuer(env: DoorsEnv): string {
  const issuer = env.OWNER_ISSUER;
  if (issuer === ISSUER_NOT_CONFIGURED) {
    throw new Error("OWNER_ISSUER is not configured (tasks 2: AuthKit)");
  }
  if (typeof issuer !== "string" || !ISSUER_URL.test(issuer)) {
    throw new Error("OWNER_ISSUER is an https issuer URL");
  }
  return issuer;
}

/** The listed foreign issuers: distinct https URLs, none of them the owner issuer. */
export function foreignIssuers(env: DoorsEnv): readonly string[] {
  let listed: unknown;
  try {
    listed = JSON.parse(env.FOREIGN_ISSUERS);
  } catch {
    listed = null;
  }
  if (!Array.isArray(listed) || listed.some((i) => typeof i !== "string" || !ISSUER_URL.test(i))
      || new Set(listed).size !== listed.length || listed.includes(env.OWNER_ISSUER)) {
    throw new Error("FOREIGN_ISSUERS is a JSON array of distinct https issuers, " +
                    "never the owner issuer");
  }
  return listed as string[];
}

/** Whether the deprecated DCR fallback is on. Anything but the two spellings is a config bug. */
export function dcrEnabled(env: DoorsEnv): boolean {
  if (env.DCR_ENABLED === "true") {
    return true;
  }
  if (env.DCR_ENABLED === "false") {
    return false;
  }
  throw new Error("DCR_ENABLED is \"true\" or \"false\"");
}

/** Whole seconds, as the owner object counts them. */
export function now(): number {
  return Math.floor(Date.now() / 1000);
}
