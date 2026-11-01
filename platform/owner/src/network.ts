/**
 * The sandbox's network and its model clients' environment, from the owner's own state
 * (gateway research §5.4 steps 1-3; tasks 4 G4). Pure functions: the sandbox slice (8b)
 * sends the network document to E2B and sets the environment in the sandbox, and nothing
 * here reaches E2B.
 *
 * **The network document** (`networkDocument`) is E2B's network configuration, in the field
 * names E2B documents for `Sandbox.create({ network })` and `updateNetwork`:
 * `allowOut`, `denyOut`, `dnsServers` and `rules` (each host's `[{ transform: { headers } }]`).
 * Sources: E2B's "Internet access" page (docs.e2b.dev/network/internet-access, cited by
 * gateway research §5.1 as E2B-net) and the `SandboxNetworkOpts`, `SandboxNetworkUpdate`,
 * `SandboxNetworkRule` and `SandboxNetworkTransform` types of the `e2b` SDK 2.62.0
 * (`dist/index.d.ts`), whose comments are those pages' text.
 * - `denyOut` is all traffic (`0.0.0.0/0`, the SDK's `ALL_TRAFFIC`), and `allowOut`, which
 *   E2B says takes precedence over `denyOut`, lists what may leave: in `open` mode `*`,
 *   every name by its HTTP `Host` or TLS SNI (so an IP-literal HTTPS request stays
 *   blocked: E2B matches domains on ports 80 and 443 only); in `allowlist` mode the policy's
 *   destinations; and in every mode `egress.tinyassets.io`, the egress door, so the
 *   sandbox's model clients always reach their providers through us.
 * - `ask` mode is an allowlist plus a refusal receipt: nothing can ask the owner from
 *   inside E2B's filter, so what is not listed is refused, and the object receipts that
 *   once each time the network is applied (`receipt`).
 * - A destination E2B cannot express is refused here, never widened: a port other than 80
 *   or 443 (E2B filters other ports by CIDR only). A name on 80 or 443 opens both, since
 *   E2B matches a name on either; that is the one way the network is wider than the
 *   policy's word, and it is declared here.
 * - Every destination is checked again by the policy's own rule (`checkDestination`), so
 *   an IP literal is refused here too (`EGRESS_IP_LITERAL`), whatever wrote the document.
 * - `dnsServers` is pinned to one resolver (`DNS_SERVERS`), not E2B's default of all
 *   traffic to 8.8.8.8 whenever a domain is allowed. E2B's own note: it opens the firewall
 *   only, and the sandbox's `/etc/resolv.conf` must name the same server (8b's template).
 * - **Allowlist mode is not a data boundary in v1.** E2B allows *all* traffic to a DNS
 *   server, on every port (its `dnsServers` text), and its rules name no ports
 *   (`allowOut`/`denyOut` take CIDRs, IPs and names only), so the pinned resolver cannot be
 *   held to port 53: code in the sandbox can tunnel data through DNS, or speak DoH (HTTPS
 *   to an IP) to it, whatever the list says. The list keeps honest tools to the owner's
 *   hosts; it does not stop a sandbox that means to send data elsewhere (`BOUNDARY_NOTE`).
 *   An empty `dnsServers` with names pinned in the template's `/etc/hosts` would close it
 *   for a fixed list; that, and whether E2B's proxy honours the VM's `/etc/hosts` or DNS
 *   for a transformed host (a redirect would carry the egress token elsewhere), are 8b's
 *   live probe.
 * - `rules` holds one transform, on `egress.tinyassets.io` only: E2B's egress proxy sets
 *   the sandbox's egress token (`x-ta-sandbox`) and the egress generation this document was
 *   made under (`x-ta-network-generation`) on every HTTPS request to the door, outside the
 *   VM, replacing any header of the same names the sandbox sent. The door admits a request
 *   only under the current egress generation, which moves only when the policy's egress
 *   table does, so a request through a stale network is refused until the sandbox slice
 *   applies the new one. The token is never in the VM; whoever holds the project's E2B API
 *   key can read it back from the sandbox's info (`SandboxNetworkRuleInfo`), so that key is
 *   a platform secret with the token's custody.
 *
 * **The environment** (`sandboxEnv`): for each of the owner's active connections, a base
 * URL on the egress door and a placeholder key, `ta_ph_<connection>_<random>`, which
 * carries no secret: the door ignores it, the gateway strips it with every other inbound
 * credential (`forward.ts`) and injects the sealed one. The names follow one rule, never a
 * provider's own code: `<PROVIDER>_BASE_URL` and `<PROVIDER>_API_KEY` (the provider id in
 * upper case, `-` as `_`: `ANTHROPIC_BASE_URL`, `OPENAI_API_KEY`, `GROQ_API_KEY`) for the
 * oldest active connection of each provider, and `TA_<CONNECTION>_BASE_URL` and
 * `TA_<CONNECTION>_API_KEY` for every one. A base URL is the door, the connection, then the
 * path of the sealed base URL: `https://egress.tinyassets.io/c/<connection>/` for
 * Anthropic's, `…/c/<connection>/v1` for OpenAI's, so a client appends its own API path.
 */
import type { EgressScope } from "../../gateway/src/credential";

import { tokenShaped } from "../../doors/src/routing-mac";

import { Invalid, Refused } from "./errors";

/**
 * The gateway as the egress door binds it (`EGRESS`, its `EgressGateway` entrypoint): one
 * sandbox model call forwarded, metered, under the sealed endpoints of the blob the owner's
 * object handed out for it; its metered outcome comes as one JSON line on `outcome`, and
 * closing `stop` aborts it. Every refusal is a fixed-code answer; the provider's own
 * answers pass through.
 */
export interface EgressService {
  forward(owner: string, connection: string, version: number, blob: string, request: Request,
          metering: { readonly max_output_tokens: number; readonly input_bytes: number } | null,
          stop: ReadableStream<Uint8Array>):
    Promise<{ response: Response; outcome: ReadableStream<Uint8Array> }>;
}

/**
 * The refusal of an IP-literal egress destination. Workers `fetch` shows no resolved
 * address, so the gateway fetches strictly publicly, E2B filters the sandbox's CIDRs (8b's
 * live probe confirms private addresses are unreachable), and a destination is a DNS name,
 * matched by SNI.
 */
export const EGRESS_IP_LITERAL = "egress_ip_literal: an egress destination is a DNS name, " +
  "never an IP literal";
/** `normalize_destination`'s DNS label: underscores allowed, hyphens interior. */
const LABEL = /^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$/;
/** A host the URL parser turned into an IPv4 address. */
const IPV4 = /^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+$/;

/**
 * One egress destination of the owner's policy (`policy.ts` validates each by this):
 * `normalize_destination(host, port) == entry`, for a hostname: lowercase ASCII DNS labels
 * with no trailing dot, as the Python's IDNA fast path leaves them, and a port. An IP
 * literal, in any spelling the URL parser reads as an address, is refused
 * (`EGRESS_IP_LITERAL`); a host no URL can carry (a name ending in a number, a malformed
 * `xn--` label) is invalid.
 */
export function checkDestination(entry: unknown): void {
  if (typeof entry !== "object" || entry === null || Array.isArray(entry)
      || Object.keys(entry).sort().join() !== "host,port") {
    throw new Invalid("invalid egress destination");
  }
  const { host, port } = entry as Record<string, unknown>;
  if (typeof host !== "string" || typeof port !== "number" || !Number.isSafeInteger(port)
      || port < 1 || port > 65_535) {
    throw new Invalid("invalid egress destination");
  }
  if (host.includes(":") || host.startsWith("[")) {
    throw new Refused(EGRESS_IP_LITERAL);
  }
  if (!host || host.length > 253 || !host.split(".").every((label) => LABEL.test(label))) {
    throw new Invalid("invalid egress destination");
  }
  // The host as a URL reads it: any spelling of an IPv4 address (decimal, hex, octal,
  // short forms) comes back dotted, and a name ending in a number is no host at all.
  let parsed: string;
  try {
    parsed = new URL(`https://${host}/`).hostname;
  } catch {
    throw new Invalid("invalid egress destination");
  }
  if (IPV4.test(parsed)) {
    throw new Refused(EGRESS_IP_LITERAL);
  }
}

/**
 * Every refusal the egress door and the owner's object answer a sandbox request with, each
 * a fixed code (`egress.ts`, `doors/src/egress.ts`).
 */
export const EGRESS_CODES = Object.freeze([
  "egress_token_missing", "egress_token_unknown", "egress_token_expired",
  "egress_lease_closed", "egress_policy_stale", "egress_network_stale",
  "egress_connection_refused", "egress_endpoint_refused", "egress_unmetered",
  "egress_length_required", "egress_rate", "egress_spend_refused",
] as const);
export type EgressCode = typeof EGRESS_CODES[number];

/** How the egress door saw a stream end. */
export const OUTCOMES: readonly string[] = Object.freeze(["complete", "aborted", "failed",
                                                          "deadline", "ended"]);
export type Outcome = "complete" | "aborted" | "failed" | "deadline" | "ended";

/**
 * The longest one sandbox request may stream, in seconds: the gateway's own forward
 * deadline (`FORWARD_DEADLINE_MS`, a test holds the two equal), which the door keeps too,
 * so a stream the gateway never ends is still settled.
 */
export const EGRESS_DEADLINE_S = 300;

/** The egress door's host: always allowed, the one host the transform is set on. */
export const EGRESS_HOST = "egress.tinyassets.io";
export const EGRESS_ORIGIN = `https://${EGRESS_HOST}`;
/** The header the transform sets to the sandbox's egress token. */
export const SANDBOX_HEADER = "x-ta-sandbox";
/** The header the transform sets to the egress generation its network was made under. */
export const GENERATION_HEADER = "x-ta-network-generation";
/** What the owner is told of an allowlist: not a data boundary in v1 (above). */
export const BOUNDARY_NOTE = "An egress allowlist keeps the sandbox's tools to the listed " +
  "hosts, but is not a data boundary: the sandbox's DNS resolver is reachable on every port, " +
  "so code that means to send data elsewhere can.";

/** E2B's `ALL_TRAFFIC`. */
export const ALL_TRAFFIC = "0.0.0.0/0";
/**
 * The one resolver the sandbox may reach. Which resolver is best is unverified (research
 * §5.4.1); 8b's live probe checks that the sandbox resolves through it and nothing else.
 */
export const DNS_SERVERS: readonly string[] = Object.freeze(["1.1.1.1"]);
/** The ports E2B matches a name on: HTTP's `Host` and TLS's SNI. */
const NAMED_PORTS: readonly number[] = Object.freeze([80, 443]);
const CONNECTION = /^c[0-9a-f]{24}$/;
/** The egress door's path segment before a connection id: `/c/<connection>/...`. */
const CONNECTION_SEGMENT = "c";

export type EgressMode = "open" | "allowlist" | "ask";

/** E2B's `SandboxNetworkRule`, its static transform. */
export interface NetworkRule {
  readonly transform: { readonly headers: Readonly<Record<string, string>> };
}

/** E2B's network configuration, as `Sandbox.create({ network })` and `updateNetwork` take it. */
export interface NetworkDocument {
  readonly allowOut: readonly string[];
  readonly denyOut: readonly string[];
  readonly dnsServers: readonly string[];
  readonly rules: Readonly<Record<string, readonly NetworkRule[]>>;
}

/** A destination the document could not express, and why. */
export interface Unexpressed {
  readonly host: string;
  readonly port: number;
  readonly reason: "port";
}

/** What the sandbox slice sends, and what the object receipts when it is applied. */
export interface NetworkPlan {
  readonly network: NetworkDocument;
  readonly mode: EgressMode;
  /** The policy generation the document was made under. */
  readonly generation: number;
  /** Destinations refused because E2B cannot filter them by name. */
  readonly unexpressed: readonly Unexpressed[];
  /** `ask` mode: what is not listed is refused, with a refusal receipt at each apply. */
  readonly receipt: boolean;
  /** Whether the network is a data boundary: never, in v1 (`BOUNDARY_NOTE`). */
  readonly boundary: false;
  readonly note: string;
}

/** The owner policy's egress table, as `policy.ts` validates it. */
export interface EgressPolicy {
  readonly mode: string;
  readonly destinations: readonly { readonly host: string; readonly port: number }[];
}

/**
 * E2B's network configuration for the owner's egress policy at egress `generation`, with
 * `token`, the sandbox's egress token, in the egress door's transform.
 */
export function networkDocument(egress: EgressPolicy, generation: number, token: string):
    NetworkPlan {
  const mode = egress.mode;
  if (mode !== "open" && mode !== "allowlist" && mode !== "ask") {
    throw new Invalid("invalid egress policy");
  }
  if (!Number.isSafeInteger(generation) || generation < 1) {
    throw new Invalid("a policy generation is a whole number from 1");
  }
  if (!tokenShaped(token)) {
    throw new Invalid("a sandbox token is its owner, its secret and its MAC, base64url");
  }
  const allowed = new Set<string>();
  const unexpressed: Unexpressed[] = [];
  for (const entry of egress.destinations) {
    checkDestination(entry);  // an IP literal is refused here as in the policy itself
    if (!NAMED_PORTS.includes(entry.port)) {
      unexpressed.push(Object.freeze({ host: entry.host, port: entry.port, reason: "port" }));
    } else if (mode !== "open") {
      allowed.add(entry.host);
    }
  }
  allowed.delete(EGRESS_HOST);
  const allowOut = mode === "open" ? ["*", EGRESS_HOST] : [...[...allowed].sort(), EGRESS_HOST];
  const headers: Record<string, string> = {};
  headers[SANDBOX_HEADER] = token;
  headers[GENERATION_HEADER] = String(generation);
  const rules: Record<string, readonly NetworkRule[]> = {};
  rules[EGRESS_HOST] = Object.freeze([Object.freeze({
    transform: Object.freeze({ headers: Object.freeze(headers) }) })]);
  return Object.freeze({
    network: Object.freeze({
      allowOut: Object.freeze(allowOut),
      denyOut: Object.freeze([ALL_TRAFFIC]),
      dnsServers: DNS_SERVERS,
      rules: Object.freeze(rules),
    }),
    mode,
    generation,
    unexpressed: Object.freeze(mode === "open" ? [] : unexpressed),
    receipt: mode === "ask",
    boundary: false,
    note: BOUNDARY_NOTE,
  });
}


/** A connection the sandbox may reach: its id, its provider and where it may be forwarded. */
export interface Egressible {
  readonly connection: string;
  readonly provider: string;
  readonly egress: EgressScope;
}

/** The environment-variable stem a name makes: upper case, every other character `_`. */
function stem(name: string): string {
  return name.toUpperCase().replace(/[^A-Z0-9]/g, "_");
}

/**
 * The provider CLIs whose variables the one rule does not name, as data: for a provider,
 * each CLI's base-URL and key variables, and whether its base URL is the door and the
 * connection alone (`bare`: its SDK appends the API version itself). Gemini CLI reads
 * `GOOGLE_GEMINI_BASE_URL` and `GEMINI_API_KEY`, its `@google/genai` client appending
 * `/v1beta`. Claude Code (`ANTHROPIC_*`), Codex and Qwen Code (`OPENAI_*`) and Mistral's
 * `MISTRAL_API_KEY` are the rule's own names.
 */
const CLI_ENV: Readonly<Record<string, readonly {
  readonly base_url: string; readonly api_key: string; readonly bare: boolean }[]>> =
  Object.freeze({
    google: Object.freeze([Object.freeze({ base_url: "GOOGLE_GEMINI_BASE_URL",
                                           api_key: "GEMINI_API_KEY", bare: true })]),
  });

/**
 * The base URL a connection's clients use: the door, the connection, the sealed path (or,
 * with no scope, the door and the connection alone).
 */
export function egressBaseUrl(connection: string, egress: EgressScope | null): string {
  if (!CONNECTION.test(connection)) {
    throw new Invalid("invalid connection");
  }
  const path = egress === null ? "/" : new URL(egress.base_url).pathname;
  return `${EGRESS_ORIGIN}/${CONNECTION_SEGMENT}/${connection}${
    path === "/" ? "/" : path.replace(/\/+$/, "")}`;
}

/**
 * The sandbox's model-client environment for `connections` (oldest first, as the object
 * lists them): each one's base URL on the door and a placeholder key, `random()` making
 * each placeholder's random part (hex). A placeholder carries no secret.
 */
export function sandboxEnv(connections: readonly Egressible[], random: () => string):
    Readonly<Record<string, string>> {
  const env: Record<string, string> = {};
  const named = new Set<string>();
  for (const { connection, provider, egress } of connections) {
    const base = egressBaseUrl(connection, egress);
    const part = random();
    if (!/^[0-9a-f]{16,64}$/.test(part)) {
      throw new Error("a placeholder's random part is 16 to 64 hex");
    }
    const placeholder = `ta_ph_${connection}_${part}`;
    env[`TA_${stem(connection)}_BASE_URL`] = base;
    env[`TA_${stem(connection)}_API_KEY`] = placeholder;
    const name = stem(provider);
    if (!named.has(name)) {
      named.add(name);
      env[`${name}_BASE_URL`] = base;
      env[`${name}_API_KEY`] = placeholder;
      for (const names of CLI_ENV[provider] ?? []) {
        env[names.base_url] = names.bare ? egressBaseUrl(connection, null) : base;
        env[names.api_key] = placeholder;
      }
    }
  }
  return Object.freeze(env);
}
