/**
 * The doors Worker's fixed facts: its one public origin, the OAuth issuer and resources,
 * the first-party clients, and the environment it binds (`wrangler.jsonc`, held by
 * `guards/config.test.ts`).
 *
 * The public identity is the contract's `identity.json`, its one copy, which the deploy kit
 * (`deploy/facts.ts`) and the plugins (`plugins/build.mjs`) read too: `host`, the public
 * origin's; `egress`, the sandbox's door, a custom domain of this Worker; `routes`, the
 * route patterns the doors take at the cutover, each from the origin; the MCP door
 * (`mcp`); where a sign-in starts and where the upstream returns it (`signin`); the doors'
 * own app's client document, redirect and resource (`first_party`); E2B's lifecycle
 * webhook (`hook`). The contract's `ZONE` and `EGRESS_HOST` (`contract/src/egress.ts`) read
 * `host` and `egress`, and the runner the sandbox template ships is built from that module, so
 * a change to either takes a new template pair.
 */
import identity from "../../contract/identity.json" with { type: "json" };

import type { DoorsEnv } from "./env";

// Each fact of the identity is read as text (a template), so this module holds strings
// alone, never the shared file object (`ci/kit/module-state.ts`).
/** The one configured public origin; the Capacitor WebView loads it too. */
export const PUBLIC_HOST = `${identity.host}`;
export const PUBLIC_ORIGIN = `https://${PUBLIC_HOST}`;
/** The authorization server's RFC 8414 issuer: our own, not the identity vendor's. */
export const ISSUER = PUBLIC_ORIGIN;
/** The MCP door's path, and the outside resource MCP clients' tokens are bound to. */
export const MCP_PATH = `${identity.mcp}`;
export const MCP_RESOURCE = `${PUBLIC_ORIGIN}${MCP_PATH}`;
/** Where a sign-in starts. */
export const AUTHORIZE_PATH = `${identity.signin.authorize}`;
/** Where the upstream sign-in returns the browser. */
export const UPSTREAM_CALLBACK_PATH = `${identity.signin.callback}`;
export const UPSTREAM_CALLBACK = `${PUBLIC_ORIGIN}${UPSTREAM_CALLBACK_PATH}`;
/** E2B's sandbox lifecycle webhook's path (`e2b-hooks.ts`). */
export const E2B_HOOK_PATH = `${identity.hook}`;
/**
 * Where a model source's OAuth flow returns the browser (`connect.ts`). A key-exchange
 * source, which sends no `state`, returns to it with the flow's state as one more path
 * segment.
 */
export const CONNECT_CALLBACK_PATH = "/oauth/connect/callback";
export const CONNECT_CALLBACK = `${PUBLIC_ORIGIN}${CONNECT_CALLBACK_PATH}`;
/**
 * The owner app's page that finishes a connect: the callback hands it the code and
 * state in the URL fragment, which no server sees, and the page posts them back with the
 * owner's token and session (the callback's navigation carries neither).
 */
export const CONNECTED_PAGE = `${PUBLIC_ORIGIN}/app/connected`;
/** The first-party app's Client ID Metadata Document, served by this Worker. */
export const WEB_CLIENT = `${PUBLIC_ORIGIN}${identity.first_party.client}`;
/** Where the first-party app finishes its sign-in. */
export const WEB_REDIRECT = `${PUBLIC_ORIGIN}${identity.first_party.redirect}`;
/**
 * The first-party clients: the only clients a token for `APP_RESOURCE` is issued to or
 * accepted from. Each is a CIMD URL on our own origin, so the list needs no registry row
 * and no config value.
 */
export const FIRST_PARTY_CLIENTS: readonly string[] = Object.freeze([WEB_CLIENT]);
/** Scopes the authorization server grants. Authority is the owner object's grant rows. */
export const SCOPES: readonly string[] = Object.freeze(["offline_access"]);

/** Token lifetimes (research §1.4): the grant slides while used; the fence never moves. */
export const ACCESS_TOKEN_TTL_S = 3_600;
export const REFRESH_TOKEN_TTL_S = 30 * 86_400;

/**
 * How old an upstream sign-in may be when the doors take it: the object opens a session
 * only for one at most this old, and a forced fresh sign-in is at most ten minutes long.
 */
export const SIGN_IN_MAX_AGE_S = 600;
export const CLOCK_MARGIN_S = 5;

/** The largest body each door reads; past it, `413` before anything is parsed. */
/** The consent form: its handle and decision, a few short fields. */
export const AUTHORIZE_BODY_MAX = 4 * 1024;
/**
 * A tool call's arguments: the request body's one ceiling (`EGRESS_BODY_MAX`, 16 MiB, the
 * isolate's 128 MB), which carries a body of the most bytes (`BODY_BYTES_MAX`, half of it)
 * with room for its JSON escapes; a test holds the three.
 */
export const API_BODY_MAX = 16 * 1024 * 1024;
/** The owner surface's requests (a run's input among them): as a tool call's. */
export const APP_BODY_MAX = API_BODY_MAX;

/**
 * `OWNER_ISSUER` and `UPSTREAM` until the deploy writes them: AuthKit's production issuer,
 * and its endpoints from AuthKit's live discovery (`deploy/`).
 */
export const NOT_CONFIGURED = "not-configured";

/** The inbound door's path: `/hooks/inbound/<token>`, one segment (`inbound.ts`). */
export const INBOUND_PATH = "/hooks/inbound/";
/**
 * An inbound event's body: the platform's one request ceiling, the same as a tool call's
 * (`API_BODY_MAX`, 16 MiB), never a ceiling of its own.
 */
export const INBOUND_BODY_MAX = API_BODY_MAX;

/**
 * The erasure queue (`erase.ts`), the one the owner Worker's `ERASURES` producer sends to:
 * the queue handler takes a batch of neither it nor the import queue as the sandbox's ring
 * (`index.ts`), so `guards/config.test.ts` holds this name to the configs.
 */
export const ERASURE_QUEUE = "tinyassets-erasure";

/**
 * A secret a data record names (a shared app's signing secret, `shared-apps.json`), or
 * null when the Worker holds none: its door then refuses loudly, never a guess.
 */
export function namedSecret(env: DoorsEnv, name: string): string | null {
  const value = (env as unknown as Record<string, unknown>)[name];
  return typeof value === "string" && value !== "" ? value : null;
}

const ISSUER_URL = /^https:\/\/[^\s]+$/;

/** The owner issuer, an https URL; unset, every sign-in fails loudly. */
export function ownerIssuer(env: DoorsEnv): string {
  const issuer = env.OWNER_ISSUER;
  if (issuer === NOT_CONFIGURED) {
    throw new Error("OWNER_ISSUER is not configured");
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
