/**
 * The doors Worker's fixed facts: its one public origin, the OAuth issuer and resources,
 * the first-party clients, and the environment it binds (`wrangler.jsonc`, held by
 * `guards/config.test.ts`).
 *
 * The public identity is `identity.json`, its one copy, which the deploy kit
 * (`deploy/facts.ts`) and the plugins (`plugins/build.mjs`) read too: `host`, the public
 * origin's; `egress`, the sandbox's door, a custom domain of this Worker; `routes`, the
 * route patterns the doors take at the cutover, each from the origin; the MCP door
 * (`mcp`); where a sign-in starts and where the upstream returns it (`signin`); the doors'
 * own app's client document, redirect and resource (`first_party`); E2B's lifecycle
 * webhook (`hook`); and the control record's key (`control_key`). The contract's `ZONE`
 * and `EGRESS_HOST` (`contract/src/egress.ts`) are held to `host` and `egress` by
 * `guards/identity.test.ts`: the runner the sandbox template ships is built from that file,
 * so it changes only with a new template pair.
 */
import identity from "./identity.json" with { type: "json" };
import type { Source } from "../../contract/src/egress-scope";
import type { ChunkPut } from "../../files/src/index";
import type { Verified } from "../../gateway/src/forward";
import type { Sealed } from "../../gateway/src/keyring";
import type { Exchanged } from "../../gateway/src/oauth";
import type { OwnerDO } from "../../owner/src/owner-do";

/**
 * The gateway as the egress door binds it (`EGRESS`, its `EgressGateway` entrypoint): one
 * sandbox model call forwarded, metered, under the sealed endpoints of the blob the owner's
 * object handed out for it, its body streamed; a model call's reservation made through
 * `reserve` (the door's callback, a stub over RPC) once the gateway's one parse read the
 * body's model and tier, null for a free call; its metered outcome comes as one JSON line on
 * `outcome`, and closing `stop` aborts it. Every refusal is a fixed-code answer; the
 * provider's own answers pass through.
 */
export interface EgressService {
  forward(owner: string, connection: string, version: number, blob: string, request: Request,
          reserve: ((call: { readonly model: string; readonly tier: string | null }) =>
            Promise<{ readonly metering: { readonly max_output_tokens: number;
                                           readonly input_bytes: number;
                                           readonly model: string;
                                           readonly tier: string | null } }
                    | { readonly refused: string }>) | null,
          stop: ReadableStream<Uint8Array>):
    Promise<{ response: Response; outcome: ReadableStream<Uint8Array> }>;
}

// Each fact of the identity is read as text (a template), so this module holds strings
// alone, never the shared file object (`owner/guards/module-state.test.ts`).
/** The one configured public origin; the Capacitor WebView loads it too. */
export const PUBLIC_HOST = `${identity.host}`;
export const PUBLIC_ORIGIN = `https://${PUBLIC_HOST}`;
/** The authorization server's RFC 8414 issuer: our own, not the identity vendor's. */
export const ISSUER = PUBLIC_ORIGIN;
/** The MCP door's path, and the outside resource MCP clients' tokens are bound to. */
export const MCP_PATH = `${identity.mcp}`;
export const MCP_RESOURCE = `${PUBLIC_ORIGIN}${MCP_PATH}`;
/** The first-party resource: the owner's own app's tokens, for `/app/api/*`. */
export const APP_RESOURCE = `${PUBLIC_ORIGIN}${identity.first_party.resource}`;
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

/** `OWNER_ISSUER` until the AuthKit slice sets AuthKit's production issuer. */
export const ISSUER_NOT_CONFIGURED = "not-configured";
/** `UPSTREAM` until the deploy fills it from AuthKit's live discovery (`deploy/`). */
export const UPSTREAM_NOT_CONFIGURED = "not-configured";

/** The inbound door's path: `/hooks/inbound/<token>`, one segment (`inbound.ts`). */
export const INBOUND_PATH = "/hooks/inbound/";
/**
 * An inbound event's body: the platform's one request ceiling, the same as a tool call's
 * (`API_BODY_MAX`; slice LIM raises it to its 16 MiB), never a ceiling of its own.
 */
export const INBOUND_BODY_MAX = API_BODY_MAX;

/**
 * The erasure queue (`erase.ts`), the one the owner Worker's `ERASURES` producer sends to:
 * the queue handler takes every other batch as the sandbox's ring (`index.ts`), so
 * `guards/config.test.ts` holds this name to the configs.
 */
export const ERASURE_QUEUE = "tinyassets-erasure";

/** The files Worker's chunk entrypoint, as the file door's bytes reach it (`file-bytes.ts`). */
export interface FilesService {
  putChunks(ns: string, key: string, chunks: ChunkPut[]): Promise<{ created: number }>;
}

/**
 * The gateway as the doors bind it: its `ConnectGateway` entrypoint, which serves only
 * the connect steps (no model call, no egress forwarding, no keyring). Its answers are
 * the gateway's own types; every failure is one of its fixed codes.
 */
export interface ConnectService {
  seal(owner: string, connection: string, version: number, key: string, record: Source):
    Promise<Sealed>;
  exchange(owner: string, connection: string, version: number,
           grant: { code: string; verifier: string; redirect_uri: string }, record: Source):
    Promise<Exchanged>;
  verify(owner: string, connection: string, version: number, blob: string): Promise<Verified>;
  /** A wake token the owner pasted, sealed for its subscription (slice W). */
  sealDoorbell(owner: string, subscription: string, version: number,
               input: { record: string; url: string; secret: string }): Promise<Sealed>;
  /** An MCP Events callback, verified by a signed challenge, then sealed (slice W). */
  verifyDoorbell(owner: string, subscription: string, version: number,
                 input: { record: string; url: string; secret: string }):
    Promise<{ readonly verified: true; readonly sealed: Sealed }
            | { readonly verified: false; readonly reason: string }>;
}

/** What `wrangler.jsonc` binds, exactly (the config guard). */
export interface DoorsEnv {
  /** The owner Worker's namespace, by `script_name`; reached only in `binder.ts`. */
  readonly OWNER: DurableObjectNamespace<OwnerDO>;
  /** The gateway's connect entrypoint; reached only in `connect.ts`. */
  readonly GATEWAY: ConnectService;
  /** The gateway's egress entrypoint, which serves only `forward`; reached only in `egress.ts`. */
  readonly EGRESS: EgressService;
  /**
   * The routing key sandbox tokens are MAC'd under (`routing-mac.ts`), a Worker secret the
   * owner Worker holds too: the egress door verifies a token by it before any stub call.
   */
  readonly ROUTING_MAC_KEY: string;
  /**
   * E2B's lifecycle webhook's signature secret (`e2b-hooks.ts`), a Worker secret slice L's
   * deploy kit sets and registers with E2B; unset, every delivery is refused `503`.
   */
  readonly E2B_WEBHOOK_SECRET?: string;
  /** The OAuth provider's own store (its schema; never ours). */
  readonly OAUTH_KV: KVNamespace;
  /** The owner directory: routing hints (`directory.ts`). */
  readonly DIRECTORY: KVNamespace;
  /** The platform's control record alone (`c:control`), the owner Worker's too. */
  readonly CONTROL: KVNamespace;
  /** The owner app's own files (`app/`), served only through `pages.ts`. */
  readonly ASSETS: Fetcher;
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
  /**
   * The owner issuer's sign-in endpoints, client and token issuer, as JSON (`oidc.ts`'s
   * `UpstreamConfig`): `not-configured` until the deploy writes it (`upstreamFrom`: User
   * Management's endpoints, and the JWKS from the issuer's live discovery).
   */
  readonly UPSTREAM: string;
  /**
   * The upstream's client secret (WorkOS's API key: the code exchange and the identity's
   * deletion need it), set in the Worker's secrets by the deploy kit from the production
   * environment's secret store; absent until then, and every sign-in fails loudly.
   */
  readonly WORKOS_API_KEY?: string;
  /** The files Worker: only `file-bytes.ts` reaches it, the binder streaming an upload. */
  readonly FILES: FilesService;
}

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
