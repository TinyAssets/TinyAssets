/**
 * The sandbox's network document and its model clients' environment (tasks 4 G4): pure
 * functions of the owner's state, tested exhaustively.
 */
import { describe, expect, it } from "vitest";

import { Invalid, Refused } from "../src/errors";
import {
  ALL_TRAFFIC, DNS_SERVERS, EGRESS_HOST, EGRESS_IP_LITERAL, egressBaseUrl, GENERATION_HEADER,
  networkDocument, SANDBOX_HEADER, sandboxEnv,
} from "../src/network";

const TOKEN = "T".repeat(43);
const transform = (generation: number) => ({ [EGRESS_HOST]: [{ transform: { headers: {
  [SANDBOX_HEADER]: TOKEN, [GENERATION_HEADER]: String(generation) } } }] });

function thrown(act: () => unknown): Error {
  try {
    act();
  } catch (error) {
    return error as Error;
  }
  throw new Error("expected a throw");
}

const DESTINATIONS = [
  { host: "pypi.org", port: 443 }, { host: "files.pythonhosted.org", port: 443 },
  { host: "example.com", port: 80 }, { host: "pypi.org", port: 80 },
  { host: "db.example.com", port: 5432 }, { host: EGRESS_HOST, port: 443 },
];

describe("the network document", () => {
  it("in open mode allows every name by SNI and the door, and denies all else", () => {
    const plan = networkDocument({ mode: "open", destinations: DESTINATIONS }, 3, TOKEN);
    expect(plan).toEqual({
      network: { allowOut: ["*", EGRESS_HOST], denyOut: [ALL_TRAFFIC], dnsServers: ["1.1.1.1"],
                 rules: transform(3) },
      mode: "open", generation: 3, unexpressed: [], receipt: false });
    expect(DNS_SERVERS).toEqual(["1.1.1.1"]);
    expect(ALL_TRAFFIC).toBe("0.0.0.0/0");
  });

  it("in allowlist mode allows the policy's names and the door, once each, in order", () => {
    const plan = networkDocument({ mode: "allowlist", destinations: DESTINATIONS }, 1, TOKEN);
    expect(plan.network).toEqual({
      allowOut: ["example.com", "files.pythonhosted.org", "pypi.org", EGRESS_HOST],
      denyOut: [ALL_TRAFFIC], dnsServers: ["1.1.1.1"], rules: transform(1) });
    // A port E2B cannot filter by name is refused, never widened to the name.
    expect(plan.unexpressed).toEqual([{ host: "db.example.com", port: 5432, reason: "port" }]);
    expect(plan.network.allowOut).not.toContain("db.example.com");
    expect(plan.receipt).toBe(false);
  });

  it("in ask mode is an allowlist and a refusal receipt", () => {
    const allowlist = networkDocument({ mode: "allowlist", destinations: DESTINATIONS }, 2, TOKEN);
    const ask = networkDocument({ mode: "ask", destinations: DESTINATIONS }, 2, TOKEN);
    expect(ask.network).toEqual(allowlist.network);
    expect(ask.unexpressed).toEqual(allowlist.unexpressed);
    expect([ask.mode, ask.receipt]).toEqual(["ask", true]);
  });

  it.each(["open", "allowlist", "ask"])("always allows egress.tinyassets.io, last, in %s mode",
                                        (mode) => {
    for (const destinations of [[], DESTINATIONS, [{ host: EGRESS_HOST, port: 443 }]]) {
      const { network } = networkDocument({ mode, destinations }, 1, TOKEN);
      expect(network.allowOut.at(-1)).toBe(EGRESS_HOST);
      expect(network.allowOut.filter((host) => host === EGRESS_HOST)).toHaveLength(1);
      expect(network.denyOut).toEqual([ALL_TRAFFIC]);
      expect(Object.keys(network.rules)).toEqual([EGRESS_HOST]);
    }
    expect(networkDocument({ mode, destinations: [] }, 1, TOKEN).network.allowOut)
      .toEqual(mode === "open" ? ["*", EGRESS_HOST] : [EGRESS_HOST]);
  });

  it.each(["open", "allowlist", "ask"])("refuses an IP literal in any spelling, in %s mode",
                                        (mode) => {
    for (const host of ["8.8.8.8", "203.0.113.7", "127.1", "0x7f000001", "0177.0.0.1",
                        "[::1]", "::1", "2001:db8::1", "12345"]) {
      const error = thrown(() => networkDocument({ mode, destinations: [{ host, port: 443 }] },
                                                 1, TOKEN));
      expect([error.name, error.message]).toEqual([Refused.name, EGRESS_IP_LITERAL]);
    }
  });

  it("refuses what is not a policy: a mode, a destination, a generation or a token", () => {
    for (const [egress, generation, token] of [
      [{ mode: "closed", destinations: [] }, 1, TOKEN],
      [{ mode: "allowlist", destinations: [{ host: "Example.com", port: 443 }] }, 1, TOKEN],
      [{ mode: "allowlist", destinations: [{ host: "example.com", port: 0 }] }, 1, TOKEN],
      [{ mode: "allowlist", destinations: [{ host: "example.com" }] }, 1, TOKEN],
      [{ mode: "allowlist", destinations: [{ host: "example.com", port: 443, x: 1 }] }, 1, TOKEN],
      [{ mode: "open", destinations: [] }, 0, TOKEN],
      [{ mode: "open", destinations: [] }, 1.5, TOKEN],
      [{ mode: "open", destinations: [] }, 1, "short"],
      [{ mode: "open", destinations: [] }, 1, `${"T".repeat(42)}=`],
    ] as const) {
      expect(thrown(() => networkDocument(egress as never, generation, token)).name)
        .toBe(Invalid.name);
    }
  });

  it("carries the token and generation in the door's transform only", () => {
    const { network } = networkDocument({ mode: "allowlist", destinations: DESTINATIONS }, 9,
                                        TOKEN);
    expect(network.rules).toEqual(transform(9));
    expect(JSON.stringify({ ...network, rules: {} })).not.toContain(TOKEN);
  });
});

const ANTHROPIC = { base_url: "https://api.anthropic.com",
                    endpoints: [{ origin: "https://api.anthropic.com", paths: ["/v1/messages"],
                                  methods: ["POST"] }] };
const OPENAI = { base_url: "https://api.openai.com/v1",
                 endpoints: [{ origin: "https://api.openai.com", paths: ["/v1/responses"],
                               methods: ["POST"] }] };
const GROQ = { base_url: "https://api.groq.com/openai/v1/",
               endpoints: [{ origin: "https://api.groq.com", paths: ["/openai/v1/chat/completions"],
                             methods: ["POST"] }] };
const C1 = `c${"1".repeat(24)}`;
const C2 = `c${"2".repeat(24)}`;
const C3 = `c${"3".repeat(24)}`;
const C4 = `c${"4".repeat(24)}`;

describe("the sandbox's model clients' environment", () => {
  it("gives each connection a base URL on the door and a placeholder key", () => {
    let n = 0;
    const random = () => (++n).toString(16).padStart(32, "0");
    const env = sandboxEnv([
      { connection: C1, provider: "anthropic", egress: ANTHROPIC },
      { connection: C2, provider: "openai", egress: OPENAI },
      { connection: C3, provider: "anthropic", egress: ANTHROPIC },
      { connection: C4, provider: "google-vertex", egress: GROQ },
    ], random);
    expect(env).toEqual({
      ANTHROPIC_BASE_URL: `https://egress.tinyassets.io/c/${C1}/`,
      ANTHROPIC_API_KEY: `ta_ph_${C1}_${"0".repeat(31)}1`,
      [`TA_${C1.toUpperCase()}_BASE_URL`]: `https://egress.tinyassets.io/c/${C1}/`,
      [`TA_${C1.toUpperCase()}_API_KEY`]: `ta_ph_${C1}_${"0".repeat(31)}1`,
      OPENAI_BASE_URL: `https://egress.tinyassets.io/c/${C2}/v1`,
      OPENAI_API_KEY: `ta_ph_${C2}_${"0".repeat(31)}2`,
      [`TA_${C2.toUpperCase()}_BASE_URL`]: `https://egress.tinyassets.io/c/${C2}/v1`,
      [`TA_${C2.toUpperCase()}_API_KEY`]: `ta_ph_${C2}_${"0".repeat(31)}2`,
      // The oldest connection of a provider has its names; every one has its own.
      [`TA_${C3.toUpperCase()}_BASE_URL`]: `https://egress.tinyassets.io/c/${C3}/`,
      [`TA_${C3.toUpperCase()}_API_KEY`]: `ta_ph_${C3}_${"0".repeat(31)}3`,
      GOOGLE_VERTEX_BASE_URL: `https://egress.tinyassets.io/c/${C4}/openai/v1`,
      GOOGLE_VERTEX_API_KEY: `ta_ph_${C4}_${"0".repeat(31)}4`,
      [`TA_${C4.toUpperCase()}_BASE_URL`]: `https://egress.tinyassets.io/c/${C4}/openai/v1`,
      [`TA_${C4.toUpperCase()}_API_KEY`]: `ta_ph_${C4}_${"0".repeat(31)}4`,
    });
    expect(sandboxEnv([], random)).toEqual({});
  });

  it("refuses a connection id or a placeholder that is not one", () => {
    expect(() => egressBaseUrl("c1", ANTHROPIC)).toThrow("invalid connection");
    expect(() => sandboxEnv([{ connection: C1, provider: "openai", egress: OPENAI }],
                            () => "not hex")).toThrow();
    expect(egressBaseUrl(C1, GROQ)).toBe(`https://egress.tinyassets.io/c/${C1}/openai/v1`);
  });
});
