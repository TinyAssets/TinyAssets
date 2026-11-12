/**
 * The sandbox's network document and its model clients' environment (tasks 4 G4): pure
 * functions of the owner's state, tested exhaustively.
 */
import { describe, expect, it } from "vitest";

import { Refusal } from "../../contract/src/refusal";
import {
  KEY_MIN_BYTES, LEASE_LIVED, mintToken, tokenHash, verifyToken,
} from "../../contract/src/routing-mac";

import { bundledRecords, bundledSources } from "../../sources/src/catalog";
import { sourceRecord } from "../../sources/src/check";
import { runEnv } from "../../sources/src/run-env";
import { EGRESS_IP_LITERAL } from "../../contract/src/policy-document";
import { BOUNDARY_NOTE, networkDocument } from "../src/network";
import {
  ALL_TRAFFIC, DNS_SERVERS, EGRESS_HOST, egressBaseUrl, egressTokenOf, GENERATION_HEADER,
  generationOf, SANDBOX_HEADER, sandboxEnv, withEgressToken,
} from "../../contract/src/egress";

const TOKEN = `${btoa("owner-1").replace(/=+$/, "")}.2000000000.${"T".repeat(43)}.${
  "M".repeat(43)}`;
/** A routing key, as a deploy generates one: 32 bytes, base64url. */
const KEY = Buffer.alloc(32, 7).toString("base64url");
const transform = (generation: number) => ({ [EGRESS_HOST]: [{ transform: { headers: {
  [GENERATION_HEADER]: String(generation) } } }] });

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
    const plan = networkDocument({ mode: "open", destinations: DESTINATIONS }, 3);
    expect(plan).toEqual({
      network: { allowOut: ["*", EGRESS_HOST], denyOut: [ALL_TRAFFIC], dnsServers: ["1.1.1.1"],
                 rules: transform(3) },
      mode: "open", generation: 3, unexpressed: [], receipt: false, boundary: false,
      note: BOUNDARY_NOTE });
    expect(DNS_SERVERS).toEqual(["1.1.1.1"]);
    expect(ALL_TRAFFIC).toBe("0.0.0.0/0");
  });

  it("in allowlist mode allows the policy's names and the door, once each, in order", () => {
    const plan = networkDocument({ mode: "allowlist", destinations: DESTINATIONS }, 1);
    expect(plan.network).toEqual({
      allowOut: ["example.com", "files.pythonhosted.org", "pypi.org", EGRESS_HOST],
      denyOut: [ALL_TRAFFIC], dnsServers: ["1.1.1.1"], rules: transform(1) });
    // A port E2B cannot filter by name is refused, never widened to the name.
    expect(plan.unexpressed).toEqual([{ host: "db.example.com", port: 5432, reason: "port" }]);
    expect(plan.network.allowOut).not.toContain("db.example.com");
    expect(plan.receipt).toBe(false);
  });

  it("in ask mode is an allowlist and a refusal receipt", () => {
    const allowlist = networkDocument({ mode: "allowlist", destinations: DESTINATIONS }, 2);
    const ask = networkDocument({ mode: "ask", destinations: DESTINATIONS }, 2);
    expect(ask.network).toEqual(allowlist.network);
    expect(ask.unexpressed).toEqual(allowlist.unexpressed);
    expect([ask.mode, ask.receipt]).toEqual(["ask", true]);
  });

  it.each(["open", "allowlist", "ask"])("always allows egress.tinyassets.io, last, in %s mode",
                                        (mode) => {
    for (const destinations of [[], DESTINATIONS, [{ host: EGRESS_HOST, port: 443 }]]) {
      const { network } = networkDocument({ mode, destinations }, 1);
      expect(network.allowOut.at(-1)).toBe(EGRESS_HOST);
      expect(network.allowOut.filter((host) => host === EGRESS_HOST)).toHaveLength(1);
      expect(network.denyOut).toEqual([ALL_TRAFFIC]);
      expect(Object.keys(network.rules)).toEqual([EGRESS_HOST]);
    }
    expect(networkDocument({ mode, destinations: [] }, 1).network.allowOut)
      .toEqual(mode === "open" ? ["*", EGRESS_HOST] : [EGRESS_HOST]);
  });

  it.each(["open", "allowlist", "ask"])("refuses an IP literal in any spelling, in %s mode",
                                        (mode) => {
    for (const host of ["8.8.8.8", "203.0.113.7", "127.1", "0x7f000001", "0177.0.0.1",
                        "[::1]", "::1", "2001:db8::1", "12345"]) {
      const error = thrown(() => networkDocument({ mode, destinations: [{ host, port: 443 }] },
                                                 1));
      expect([(error as Refusal).code, error.message])
        .toEqual(["egress_ip_literal", EGRESS_IP_LITERAL]);
    }
  });

  it("refuses what is not a policy: a mode, a destination or a generation", () => {
    for (const [egress, generation] of [
      [{ mode: "closed", destinations: [] }, 1],
      [{ mode: "allowlist", destinations: [{ host: "Example.com", port: 443 }] }, 1],
      [{ mode: "allowlist", destinations: [{ host: "example.com", port: 0 }] }, 1],
      [{ mode: "allowlist", destinations: [{ host: "example.com" }] }, 1],
      [{ mode: "allowlist", destinations: [{ host: "example.com", port: 443, x: 1 }] }, 1],
      [{ mode: "open", destinations: [] }, 0],
      [{ mode: "open", destinations: [] }, 1.5],
    ] as const) {
      expect((thrown(() => networkDocument(egress as never, generation)) as Refusal)
        .invalid).toBe(true);
    }
  });

  it("carries the generation, and the token once the opener adds it, in the door's " +
     "transform only", () => {
    const { network } = networkDocument({ mode: "allowlist", destinations: DESTINATIONS }, 9);
    expect(network.rules).toEqual(transform(9));
    expect([generationOf(network), egressTokenOf(network)]).toEqual([9, null]);
    const opened = withEgressToken(network, TOKEN);
    expect(opened.rules).toEqual({ [EGRESS_HOST]: [{ transform: { headers: {
      [GENERATION_HEADER]: "9", [SANDBOX_HEADER]: TOKEN } } }] });
    expect(egressTokenOf(opened)).toBe(TOKEN);
    expect({ ...opened, rules: {} }).toEqual({ ...network, rules: {} });
  });
});

const ANTHROPIC = { api: "anthropic-messages", base_url: "https://api.anthropic.com",
                    endpoints: [{ origin: "https://api.anthropic.com", paths: ["/v1/messages"],
                                  methods: ["POST"] }], meter: null };
const OPENAI = { api: "openai-responses", base_url: "https://api.openai.com/v1",
                 endpoints: [{ origin: "https://api.openai.com", paths: ["/v1/responses"],
                               methods: ["POST"] }], meter: null };
const GROQ = { api: "openai-completions", base_url: "https://api.groq.com/openai/v1/",
               endpoints: [{ origin: "https://api.groq.com", paths: ["/openai/v1/chat/completions"],
                             methods: ["POST"] }], meter: null };
const C1 = `c${"1".repeat(24)}`;
const C2 = `c${"2".repeat(24)}`;
const C3 = `c${"3".repeat(24)}`;
const C4 = `c${"4".repeat(24)}`;
const C5 = `c${"5".repeat(24)}`;
const GOOGLE = { api: "google-generative-ai", base_url: "https://generativelanguage.googleapis.com/v1beta",
                 endpoints: [{ origin: "https://generativelanguage.googleapis.com",
                               paths: ["/v1beta/models/*"], methods: ["POST"] }], meter: null };

describe("a sandbox token", () => {
  const EXPIRES = 2_000_000_000;

  it("names its owner and expiry, carries the routing key's MAC, and keeps only its " +
     "secret's hash", () => {
    const owner = "user_01HZ";
    const minted = mintToken(KEY, "egress", owner, EXPIRES);
    const [routing, expires, secret, mac] = minted.token.split(".");
    expect(expires).toBe(String(EXPIRES));
    expect(secret).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(mac).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(routing).toBe(Buffer.from(owner).toString("base64url"));
    expect(verifyToken(KEY, "egress", minted.token))
      .toEqual({ owner, expires: EXPIRES, hash: minted.hash });
    expect(minted.hash).toBe(tokenHash(secret!));
    expect(mintToken(KEY, "egress", owner, EXPIRES).hash).not.toBe(minted.hash);
    for (const bad of [1.5, -1, 99_999_999_999_999]) {
      expect(() => mintToken(KEY, "egress", owner, bad)).toThrow(/expiry/);
    }
    // A lease's token has no expiry of its own (its lease's); a socket ticket always has.
    expect(verifyToken(KEY, "egress", mintToken(KEY, "egress", owner, LEASE_LIVED).token))
      .toMatchObject({ owner, expires: 0 });
    expect(() => mintToken(KEY, "socket", owner, LEASE_LIVED)).toThrow(/expiry/);
  });

  it("is refused forged, altered (its expiry too), of the other kind or under another key",
     () => {
    const minted = mintToken(KEY, "egress", "user_01HZ", EXPIRES);
    const [routing, expires, secret, mac] = minted.token.split(".") as
      [string, string, string, string];
    const flip = (part: string) => `${part.slice(0, -2)}${part.at(-2) === "A" ? "B" : "A"}${
      part.at(-1)}`;
    const other = Buffer.from("user_02XY").toString("base64url");
    const otherKey = Buffer.alloc(32, 9).toString("base64url");
    const later = String(EXPIRES + 86_400);
    for (const bad of ["", secret, `${routing}.${secret}.${mac}`,
                       `${routing}.${expires}.${secret}.${mac}x`, `.${expires}.${secret}.${mac}`,
                       `${routing}.${expires}..${mac}`, `${routing}=.${expires}.${secret}.${mac}`,
                       `${other}.${expires}.${secret}.${mac}`,
                       `${flip(routing)}.${expires}.${secret}.${mac}`,
                       `${routing}.${later}.${secret}.${mac}`,
                       `${routing}.0${expires}.${secret}.${mac}`,
                       `${routing}.${expires}.${flip(secret)}.${mac}`,
                       `${routing}.${expires}.${secret}.${flip(mac)}`,
                       `${routing}.${expires}.${secret}.${"A".repeat(43)}`]) {
      expect(verifyToken(KEY, "egress", bad)).toBeNull();
    }
    expect(verifyToken(KEY, "sync", minted.token)).toBeNull();
    expect(verifyToken(otherKey, "egress", minted.token)).toBeNull();
    // Another spelling of the same MAC bytes (the last character's two spare bits set).
    const last = mac.at(-1)!;
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    const respelled = alphabet[alphabet.indexOf(last) ^ 1]!;
    expect(Buffer.from(`${mac.slice(0, -1)}${respelled}`, "base64url"))
      .toEqual(Buffer.from(mac, "base64url"));
    expect(verifyToken(KEY, "egress", `${routing}.${expires}.${secret}.${mac.slice(0, -1)}${
      respelled}`)).toBeNull();
    // A forger who picks a routing part, an expiry and a secret still needs the key's MAC.
    const forged = mintToken(otherKey, "egress", "user_02XY", EXPIRES).token;
    expect(verifyToken(KEY, "egress", forged)).toBeNull();
  });

  it("takes a routing key of 32 bytes, and none shorter", () => {
    expect(KEY_MIN_BYTES).toBe(32);
    const thirtyTwo = Buffer.alloc(32, 3).toString("base64url");
    const minted = mintToken(thirtyTwo, "egress", "user_01HZ", EXPIRES);
    expect(verifyToken(thirtyTwo, "egress", minted.token)).not.toBeNull();
    for (const short of [1, 16, 31]) {
      expect(() => mintToken(Buffer.alloc(short, 3).toString("base64url"), "egress",
                             "user_01HZ", EXPIRES)).toThrow(/ROUTING_MAC_KEY/);
    }
  });

  it("fails loudly on a Worker without a usable routing key", () => {
    for (const key of [undefined, "", "short", Buffer.alloc(31).toString("base64url"),
                       `${KEY}=`]) {
      expect(() => mintToken(key, "egress", "user_01HZ", EXPIRES)).toThrow(/ROUTING_MAC_KEY/);
      expect(() => verifyToken(key, "egress", TOKEN)).toThrow(/ROUTING_MAC_KEY/);
    }
  });
});

/** `engine`'s run on `credential`, on `connection` when it is a key's. */
const run = (engine: string, credential: "vendor_login_in_sandbox" | "api_key",
             connection: string | null = null) =>
  ({ ...runEnv(bundledSources(), engine, credential), connection, run: R, secret: S });

/** The run the environments are made for: its path is in every base URL. */
const R = `run_${"0".repeat(32)}`;
/** Its own secret, beside it in every base URL: only the run's CLI holds them. */
const S = "5e".repeat(32);
/** The run's path, with its secret. */
const RS = `${R}/${S}`;

/** Every connection an owner could hold, one per API-key source, oldest first. */
const KEYS = bundledRecords().map(sourceRecord)
  .filter((record) => record.connect.includes("api_key"));
const EVERY = KEYS.map((key, index) => ({
  connection: `c${String(index + 1).padStart(24, "0")}`, provider: key.upstream!.provider,
  egress: { api: key.upstream!.api, base_url: key.upstream!.base_url,
            endpoints: key.endpoints!, meter: null } }));

describe("the sandbox's model clients' environment", () => {
  it("gives a run on the vendor's own sign-in no key and no base URL, for every CLI", () => {
    const engines = bundledRecords().map(sourceRecord)
      .filter((record) => record.cli?.credential.includes("vendor_login_in_sandbox"));
    expect(engines.map((record) => record.id).sort())
      .toEqual(["claude-code", "codex", "gemini", "kimi"]);
    // Every API-key variable any record names, and the rule's for every provider.
    const keyNames = bundledRecords().map(sourceRecord).flatMap((record) =>
      Object.values(record.cli?.api_key_env ?? {}).flatMap((vars) =>
        [vars.api_key, ...(vars.base_url === null ? [] : [vars.base_url])]));
    for (const record of engines) {
      // However many keys the owner holds, a signed-in run carries none of them.
      const env = sandboxEnv(EVERY, () => "ab".repeat(16),
                             run(record.id, "vendor_login_in_sandbox"));
      expect(env, record.id).toEqual({});
      const forbidden = record.cli!.per_credential!.vendor_login_in_sandbox!.forbid_env!;
      for (const name of [...forbidden, ...keyNames]) {
        expect(env, `${record.id}: ${name}`).not.toHaveProperty(name);
      }
      expect(Object.keys(env).filter((name) => /_(?:API_KEY|BASE_URL)$/.test(name))).toEqual([]);
    }
    const claude = sandboxEnv(EVERY, () => "ab".repeat(16),
                              run("claude-code", "vendor_login_in_sandbox"));
    expect(claude["ANTHROPIC_API_KEY"]).toBeUndefined();
    expect(claude["ANTHROPIC_BASE_URL"]).toBeUndefined();
    // A signed-in run is on no connection.
    expect(() => sandboxEnv(EVERY, () => "ab".repeat(16),
                            run("claude-code", "vendor_login_in_sandbox", EVERY[0]!.connection)))
      .toThrow(Refusal);
  });

  it("gives an API-key run its one connection, under the rule's names and the engine's own",
     () => {
    let checked = 0;
    for (const [index, key] of KEYS.entries()) {
      const own = EVERY[index]!;
      for (const engine of key.engines!) {
        const made = run(engine, "api_key", own.connection);
        const env = sandboxEnv(EVERY, () => "ab".repeat(16), made);
        // The secret in the path, or as the header the record names (never both).
        const inPath = made.secret_header === undefined ? S : undefined;
        if (made.secret_header !== undefined) {
          expect(env[made.secret_header.env]).toBe(`x-ta-run: ${S}`);
          expect(JSON.stringify(env).split(S).length).toBe(2);
        }
        const placeholder = `ta_ph_${own.connection}_${"ab".repeat(16)}`;
        const vars = sourceRecord(bundledRecords().find((record) =>
          (record as { id: string }).id === engine)).cli!.api_key_env![key.upstream!.provider]!;
        expect(env[vars.api_key], `${key.id} → ${engine}`).toBe(placeholder);
        if (vars.base_url !== null) {
          expect(env[vars.base_url], `${key.id} → ${engine}`).toBe(vars.base === "connection"
            ? egressBaseUrl(own.connection, null, R, inPath)
            : egressBaseUrl(own.connection, own.egress, R, inPath));
        }
        // No other connection's placeholder, nor its base URL, is in the run's environment.
        for (const other of EVERY.filter((entry) => entry !== own)) {
          expect(JSON.stringify(env), `${key.id} → ${engine}`).not.toContain(other.connection);
        }
        checked += 1;
      }
    }
    expect(checked).toBeGreaterThanOrEqual(10);
  });

  it("names a key's variables by the rule, and an engine's by its record", () => {
    const at = (n: number) => `c${String(n).padStart(24, "0")}`;
    const random = () => "0".repeat(31) + "1";
    expect(sandboxEnv([{ connection: C2, provider: "openai", egress: OPENAI }], random,
                      run("codex", "api_key", C2))).toEqual({
      OPENAI_BASE_URL: `https://egress.tinyassets.io/r/${RS}/c/${C2}/v1`,
      OPENAI_API_KEY: `ta_ph_${C2}_${"0".repeat(31)}1`,
      // Codex's own key variable, from its record.
      CODEX_API_KEY: `ta_ph_${C2}_${"0".repeat(31)}1`,
      [`TA_${C2.toUpperCase()}_BASE_URL`]: `https://egress.tinyassets.io/r/${RS}/c/${C2}/v1`,
      [`TA_${C2.toUpperCase()}_API_KEY`]: `ta_ph_${C2}_${"0".repeat(31)}1`,
    });
    expect(sandboxEnv([{ connection: C5, provider: "google", egress: GOOGLE }], random,
                      run("gemini", "api_key", C5))).toEqual({
      GOOGLE_BASE_URL: `https://egress.tinyassets.io/r/${RS}/c/${C5}/v1beta`,
      GOOGLE_API_KEY: `ta_ph_${C5}_${"0".repeat(31)}1`,
      // Gemini CLI's own names, its base URL the door and the connection alone.
      GOOGLE_GEMINI_BASE_URL: `https://egress.tinyassets.io/r/${RS}/c/${C5}/`,
      GEMINI_API_KEY: `ta_ph_${C5}_${"0".repeat(31)}1`,
      [`TA_${C5.toUpperCase()}_BASE_URL`]: `https://egress.tinyassets.io/r/${RS}/c/${C5}/v1beta`,
      [`TA_${C5.toUpperCase()}_API_KEY`]: `ta_ph_${C5}_${"0".repeat(31)}1`,
    });
    // A connection the owner does not hold, or a provider the engine runs no key of.
    expect(() => sandboxEnv([{ connection: C1, provider: "anthropic", egress: ANTHROPIC }],
                            random, run("claude-code", "api_key", at(99)))).toThrow(Refusal);
    expect(() => sandboxEnv([{ connection: C1, provider: "anthropic", egress: ANTHROPIC }],
                            random, run("codex", "api_key", C1))).toThrow(Refusal);
    // Two names for one variable that disagree, or a name the credential forbids, fail.
    expect(() => sandboxEnv([{ connection: C2, provider: "openai", egress: OPENAI }], random,
                            { run: R, secret: S, credential: "api_key", connection: C2,
                              forbid_env: [],
                              api_key_env: { openai: { base_url: "OPENAI_BASE_URL",
                                                       api_key: "K", base: "connection" } } }))
      .toThrow(/two values/);
    expect(() => sandboxEnv([{ connection: C2, provider: "openai", egress: OPENAI }], random,
                            { ...run("codex", "api_key", C2), forbid_env: ["CODEX_API_KEY"] }))
      .toThrow(/forbids CODEX_API_KEY/);
    expect(() => sandboxEnv([{ connection: C2, provider: "openai", egress: OPENAI }], random,
                            { ...run("codex", "api_key", C2), forbid_env: ["CODEX_*"] }))
      .toThrow(/forbids CODEX_API_KEY/);
  });

  it("never meets a record's own variable: the checker refuses a record setting any name " +
     "a key's environment sets (slice E's owed case)", () => {
    // Every name `sandboxEnv` could set for an engine's run: the rule's for each provider
    // its record runs a key of, the connection's, and the record's own key variables.
    let refused = 0;
    for (const value of bundledRecords()) {
      const record = sourceRecord(value);
      const cli = record.cli;
      if (cli === undefined) {
        continue;
      }
      const names = Object.entries(cli.api_key_env ?? {}).flatMap(([provider, vars]) => [
        `${provider.toUpperCase().replace(/[^A-Z0-9]/g, "_")}_BASE_URL`,
        `${provider.toUpperCase().replace(/[^A-Z0-9]/g, "_")}_API_KEY`,
        vars.api_key, ...(vars.base_url === null ? [] : [vars.base_url])]);
      names.push(`TA_${C1.toUpperCase()}_BASE_URL`, `TA_${C1.toUpperCase()}_API_KEY`);
      for (const name of names) {
        for (const where of ["env", "home_env"] as const) {
          const planted = JSON.parse(JSON.stringify(value)) as { cli: Record<string, unknown> };
          planted.cli[where] = { ...(planted.cli[where] as object | undefined),
                                 [name]: where === "env" ? "x" : "{home}/x" };
          expect(() => sourceRecord(planted), `${record.id} ${where} ${name}`)
            .toThrow(/credential|forbids/);
          refused += 1;
        }
      }
    }
    expect(refused).toBeGreaterThan(40);
  });

  it("refuses a connection id or a placeholder that is not one", () => {
    expect(() => egressBaseUrl("c1", ANTHROPIC, R, S)).toThrow("invalid connection");
    expect(() => egressBaseUrl(C1, ANTHROPIC, "run_x", S)).toThrow("invalid run");
    expect(() => egressBaseUrl(C1, ANTHROPIC, R, "5e")).toThrow("invalid run secret");
    expect(() => sandboxEnv([{ connection: C1, provider: "openai", egress: OPENAI }],
                            () => "not hex", run("codex", "api_key", C1))).toThrow();
    expect(egressBaseUrl(C1, GROQ, R, S)).toBe(`https://egress.tinyassets.io/r/${RS}/c/${C1}/openai/v1`);
  });

  it("takes the run's secret as a header only where a record says so, and only so", () => {
    const records = bundledRecords() as { id: string; cli?: Record<string, unknown> }[];
    const claude = records.find((record) => record.id === "claude-code")!;
    const codex = records.find((record) => record.id === "codex")!;
    expect(sourceRecord(claude).cli!.run_secret_env)
      .toEqual({ env: "ANTHROPIC_CUSTOM_HEADERS", value: "x-ta-run: {run_secret}" });
    expect(sourceRecord(claude).cli!.mcp).toMatchObject({
      template: expect.stringContaining("\"x-ta-run\": \"{run_secret}\"") });
    // A vendor-login run's environment carries no header: it makes no keyed model call.
    expect(runEnv(bundledSources(), "claude-code", "vendor_login_in_sandbox").secret_header)
      .toBeUndefined();
    const planted = (record: { cli?: Record<string, unknown> }, cli: Record<string, unknown>) =>
      () => sourceRecord({ ...JSON.parse(JSON.stringify(record)),
                           cli: { ...JSON.parse(JSON.stringify(record.cli)), ...cli } });
    expect(planted(codex, { run_secret_env: { env: "X_HEADERS", value: "x-ta-run: {run_secret}" } }))
      .not.toThrow();
    expect(planted(codex, { run_secret_env: { env: "X_HEADERS", value: "{run_secret}" } }))
      .toThrow(/run_secret_env/);
    expect(planted(codex, { unattended_argv: ["--x={run_secret}"] })).toThrow(/run_secret/);
    // A record that runs on no key takes no header variable.
    const kimi = records.find((record) => record.id === "kimi")!;
    expect(planted(kimi, { run_secret_env: { env: "X_HEADERS", value: "x-ta-run: {run_secret}" } }))
      .toThrow(/run_secret_env/);
  });
});
