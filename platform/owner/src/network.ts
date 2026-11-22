/**
 * The sandbox's network, from the owner's own state (gateway research §5.4 steps 1-3). Pure
 * functions: the sandbox Worker sends the network document to E2B, and
 * nothing here reaches E2B. The egress wire it names (the door's host, its headers, the
 * resolver) and the environment a run's model clients reach the door by (`sandboxEnv`) are
 * the contract's (`contract/src/egress.ts`).
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
 *   table does, so a request through a stale network is refused until the new one is
 *   applied: the `runs` lane re-applies each live lease's (`Launcher.reapply`, the sandbox
 *   Worker's `network`). The token is never in the VM; whoever holds the project's E2B API
 *   key can read it back from the sandbox's info (`SandboxNetworkRuleInfo`), so that key is
 *   a platform secret with the token's custody.
 *
 * **The agent's own MCP servers** (`agentServers`) are held to the same rule at each run's
 * admission: one its sandbox's network would never let out is left out of the run, and the
 * owner is told which and why, so no engine is handed a server it cannot reach. A kept server
 * whose URL one of the owner's connectors is sealed for (`connectorsOf`) is reached through the
 * egress door instead (`throughConnectors`), where the gateway puts that connector's key in:
 * the key is bound to its URL by the seal, so it never goes to another host.
 */
import type { AgentMcp, McpServer } from "../../contract/src/agent-definition";
import {
  closedNetwork, connectorUrl, type Egressible, withGeneration,
} from "../../contract/src/egress";
import { allows } from "../../contract/src/egress-scope";
import {
  checkDestination, EGRESS_MODES, type EgressMode,
} from "../../contract/src/policy-document";
import { Refusal } from "../../contract/src/refusal";
import type { NetworkPlan, Unexpressed } from "../../contract/src/sandbox";

/** What the owner is told of an allowlist: not a data boundary in v1 (above). */
export const BOUNDARY_NOTE = "An egress allowlist keeps the sandbox's tools to the listed " +
  "hosts, but is not a data boundary: the sandbox's DNS resolver is reachable on every port, " +
  "so code that means to send data elsewhere can.";

/** The ports E2B matches a name on: HTTP's `Host` and TLS's SNI. */
const NAMED_PORTS: readonly number[] = Object.freeze([80, 443]);

/** The owner policy's egress table, as `policy.ts` validates it. */
export interface EgressPolicy {
  readonly mode: EgressMode;
  readonly destinations: readonly { readonly host: string; readonly port: number }[];
}

/**
 * E2B's network configuration for the owner's egress policy at egress `generation`, its
 * door's transform setting the generation (`withGeneration`) and no egress token: the start
 * that opens a lease adds the lease's (`withEgressToken`), and a re-apply's goes as it is,
 * the sandbox Worker setting the live sandbox's own (`NetworkReapply`), since the object
 * holds only its hash.
 */
export function networkDocument(egress: EgressPolicy, generation: number): NetworkPlan {
  const mode = egress.mode;
  if (!EGRESS_MODES.includes(mode)) {
    throw new Refusal("invalid", "invalid egress policy");
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
  // `open` lets every name out (`*`); either way the door is listed, last (`allowList`).
  const closed = withGeneration(closedNetwork(mode === "open" ? ["*"] : allowed), generation);
  return Object.freeze({
    network: Object.freeze({
      allowOut: Object.freeze(closed.allowOut),
      denyOut: Object.freeze(closed.denyOut),
      dnsServers: Object.freeze(closed.dnsServers),
      rules: Object.freeze(closed.rules),
    }),
    mode,
    unexpressed: Object.freeze(mode === "open" ? [] : unexpressed),
    boundary: false,
    note: BOUNDARY_NOTE,
  });
}

/** An agent's server its runs go without, and why, for the owner's notice. */
export interface DroppedServer {
  readonly name: string;
  readonly host: string;
  readonly why: string;
}

/**
 * The agent's own MCP servers (`mcp.json`, `agentMcp`) its runs take under the owner's egress
 * policy, and those left out. A server started by its command is kept: it runs in the sandbox,
 * whose network holds its traffic as it holds any other. A server reached by its URL is kept
 * when the sandbox's network lets its host out by the rule `networkDocument` makes it by: a
 * name (`checkDestination`, so never an IP address), on 80 or 443, the only ports E2B matches
 * a name on, and in `allowlist` mode a destination's host on one of them.
 */
export function agentServers(mcp: AgentMcp, egress: EgressPolicy):
    { readonly servers: Readonly<Record<string, McpServer>>;
      readonly dropped: readonly DroppedServer[] } {
  const servers: Record<string, McpServer> = {};
  const dropped: DroppedServer[] = [];
  for (const [name, server] of Object.entries(mcp.servers)) {
    const url = "url" in server ? new URL(server.url) : null;
    const why = url === null ? null : serverUnreached(url, egress);
    if (why === null) {
      servers[name] = server;
    } else {
      dropped.push(Object.freeze({ name, host: url!.host, why }));
    }
  }
  return Object.freeze({ servers: Object.freeze(servers), dropped: Object.freeze(dropped) });
}

/**
 * Why the sandbox's network never lets the server at `url` out, or null when it does: the rule
 * a run's servers are kept by at its admission, and a connector's calls at each open
 * (`egress.ts`), so a narrowed policy holds a live run's servers through the egress door too.
 */
export function serverUnreached(url: URL, egress: EgressPolicy): string | null {
  return unreached(url.hostname, url.port === "" ? 443 : Number(url.port), egress);
}

/** Why the sandbox's network never lets `host` out on `port`, or null when it does. */
function unreached(host: string, port: number, egress: EgressPolicy): string | null {
  try {
    checkDestination({ host, port });
  } catch (error) {
    if (!(error instanceof Refusal)) {
      throw error;
    }
    return error.code === "egress_ip_literal" ? "the sandbox's network lets no IP address out"
      : "its host is no name the sandbox's network can let out";
  }
  if (!NAMED_PORTS.includes(port)) {
    return `the sandbox's network lets a name out on ports ${NAMED_PORTS.join(" and ")} alone`;
  }
  if (egress.mode === "allowlist" && !egress.destinations.some((entry) =>
    entry.host === host && NAMED_PORTS.includes(entry.port))) {
    return "its host is not on your egress allowlist";
  }
  return null;
}

/**
 * Each of `servers` reached by a URL one of the owner's active connectors is sealed for (its
 * scope `allows` a `POST` there: its origin, its exact path), by name, bound to the newest
 * such one of `connections` (oldest first): a newer key for a URL is its rotation, and the
 * older one goes unused until the owner disconnects it.
 */
export function connectorsOf(servers: Readonly<Record<string, McpServer>>,
                             connections: readonly Egressible[]):
    Readonly<Record<string, string>> {
  const bound: Record<string, string> = {};
  for (const [name, server] of Object.entries(servers)) {
    const url = "url" in server ? new URL(server.url) : null;
    const newest = url === null ? undefined : connections.findLast(({ egress }) =>
      "kind" in egress && allows(egress.endpoints, "POST", url));
    if (newest !== undefined) {
      bound[name] = newest.connection;
    }
  }
  return Object.freeze(bound);
}

/** `servers`, each one `bound` names reached through the egress door on `run`'s own path. */
export function throughConnectors(servers: Readonly<Record<string, McpServer>>,
                                  bound: Readonly<Record<string, string>>, run: string,
                                  secret: string): Readonly<Record<string, McpServer>> {
  return Object.freeze(Object.fromEntries(Object.entries(servers).map(([name, server]) =>
    [name, bound[name] === undefined || !("url" in server) ? server
      : Object.freeze({ url: connectorUrl(bound[name], run, secret, server.url) })])));
}
