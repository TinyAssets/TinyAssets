/**
 * The sandbox's network, from the owner's own state (gateway research §5.4 steps 1-3; tasks
 * 4 G4). Pure functions: the sandbox slice (8b) sends the network document to E2B, and
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
 */
import { tokenShaped } from "../../contract/src/routing-mac";
import {
  DNS_SERVERS as RESOLVERS, EGRESS_HOST as DOOR_HOST, GENERATION_HEADER, SANDBOX_HEADER,
} from "../../contract/src/egress";
import { checkDestination } from "../../contract/src/policy-document";
import { Refusal } from "../../contract/src/refusal";
import type { NetworkPlan, NetworkRule, Unexpressed } from "../../contract/src/sandbox";

/**
 * The egress host and the pinned resolver as `sandbox/template/build.py` reads them: by their
 * text at this path (`network_constants`), for the template build's checks, which no code
 * imports. The platform's one definition is the contract's (`contract/src/egress.ts`), which
 * the network document below uses; `sandbox/guards/template.test.ts` holds this text to it.
 * Fix A6 moves the build's reads to the runner bundle's own facts, and these two go.
 */
export const EGRESS_HOST = "egress.tinyassets.io";
export const DNS_SERVERS: readonly string[] = Object.freeze(["1.1.1.1"]);

/** What the owner is told of an allowlist: not a data boundary in v1 (above). */
export const BOUNDARY_NOTE = "An egress allowlist keeps the sandbox's tools to the listed " +
  "hosts, but is not a data boundary: the sandbox's DNS resolver is reachable on every port, " +
  "so code that means to send data elsewhere can.";

/** E2B's `ALL_TRAFFIC`. */
export const ALL_TRAFFIC = "0.0.0.0/0";
/** The ports E2B matches a name on: HTTP's `Host` and TLS's SNI. */
const NAMED_PORTS: readonly number[] = Object.freeze([80, 443]);

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
    throw new Refusal("invalid", "invalid egress policy");
  }
  if (!Number.isSafeInteger(generation) || generation < 1) {
    throw new Refusal("invalid", "a policy generation is a whole number from 1");
  }
  if (!tokenShaped(token)) {
    throw new Refusal("invalid", "a sandbox token is its owner, its secret and its MAC, base64url");
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
  allowed.delete(DOOR_HOST);
  const allowOut = mode === "open" ? ["*", DOOR_HOST] : [...[...allowed].sort(), DOOR_HOST];
  const headers: Record<string, string> = {};
  headers[SANDBOX_HEADER] = token;
  headers[GENERATION_HEADER] = String(generation);
  const rules: Record<string, readonly NetworkRule[]> = {};
  rules[DOOR_HOST] = Object.freeze([Object.freeze({
    transform: Object.freeze({ headers: Object.freeze(headers) }) })]);
  return Object.freeze({
    network: Object.freeze({
      allowOut: Object.freeze(allowOut),
      denyOut: Object.freeze([ALL_TRAFFIC]),
      dnsServers: RESOLVERS,
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
