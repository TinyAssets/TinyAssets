/**
 * The deploy kit (`platform/deploy/deploy.ts`, slice L), built and never run here: it names
 * every Worker, binding, namespace and secret the platform's configs declare; the config it
 * deploys is the checked-in one with only the namespace ids, the doors' upstream and (with
 * `--routes`) the routes filled; it holds the owner issuer to its live discovery exactly;
 * it registers the WorkOS redirect URI only when missing and never deletes one; and it sets
 * the routing key on the doors and the owner together or not at all.
 *
 * Slice L's review (H2, research 3b and 3c): a failed lookup is never "absent"; a held
 * keyring is never replaced, and is rotated only by adding a key; the control record is
 * written only when the API answers that it is absent; an absent discovery field is unknown,
 * and unknown stops the kit; the kit runs from an approved manual workflow whose secrets come
 * from the environment's store.
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../owner/guards/sources";
import {
  BUCKET, CALLBACK, type CfAnswer, configOf, DARK_CONTROL, deployedConfig, EGRESS_DOMAIN,
  DOORS, E2B_HOOK, handover, keyring, kvPresent, lifecycleHook, listed, lockRefusal, NAMESPACES, namespacesOf, PUBLIC_INPUTS, registerRedirect,
  pairCheck, pairTag, ROUTES, type Route, routesOf, runHandover, SECRET_INPUTS, shippedRunner,
  zoneOf,
  SECRETS, secretPlan, secretsOf, templateReady, upstreamFrom, versionOf, WORKERS,
} from "../../deploy/deploy";
import { parseControl } from "../src/directory";
import { E2B_HOOK_PATH } from "../src/e2b-hooks";
import { upstreamConfig } from "../src/oidc";

type Json = Record<string, unknown>;

const present = WORKERS.filter((worker) => existsSync(join(PLATFORM, worker, "wrangler.jsonc")));
const UPSTREAM = { issuer: "https://auth.test",
                   record: { client_id: "c", authorization_endpoint: "https://auth.test/a",
                             token_endpoint: "https://auth.test/t",
                             jwks_uri: "https://auth.test/j",
                             user_delete: "https://api.workos.com/user_management/users/{sub}" } };
const IDS = Object.fromEntries(Object.keys(NAMESPACES).map((binding) => [binding, `id-${binding}`]));
const NONE = () => undefined;
const RING = JSON.stringify({ active: "k1", keys: { k1: "a" } });
const ROTATED = JSON.stringify({ active: "k2", keys: { k1: "a", k2: "b" } });

/** A Cloudflare API that answers each path with the answer `answers` names, or a 500. */
function api(answers: Record<string, CfAnswer | Error>) {
  const calls: string[] = [];
  const call = async (path: string, init?: RequestInit): Promise<CfAnswer> => {
    calls.push(`${init?.method ?? "GET"} ${path}`);
    const answer = answers[path];
    if (answer instanceof Error) {
      throw answer;
    }
    return answer ?? { status: 500, body: { success: false, errors: [{ code: 10000 }] } };
  };
  return Object.assign(call, { calls });
}

const ok = (result: unknown): CfAnswer => ({ status: 200, body: { success: true, result } });

describe("the deploy kit", () => {
  it("names every Worker the platform holds, each after the Workers it binds", () => {
    const configs = readdirSync(PLATFORM, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && existsSync(join(PLATFORM, entry.name,
                                                                "wrangler.jsonc")))
      .map((entry) => entry.name);
    expect(configs.filter((worker) => !WORKERS.includes(worker))).toEqual([]);
    for (const worker of present) {
      const services = (configOf(worker)["services"] ?? []) as { service: string }[];
      for (const { service } of services) {
        const target = service.replace(/^tinyassets-/, "");
        if (present.includes(target)) {
          expect([worker, WORKERS.indexOf(target) < WORKERS.indexOf(worker)])
            .toEqual([worker, true]);
        }
      }
    }
  });

  it("names every namespace, bucket and secret the configs declare", () => {
    for (const worker of present) {
      const config = configOf(worker);
      for (const kv of (config["kv_namespaces"] ?? []) as { binding: string }[]) {
        expect(Object.keys(NAMESPACES)).toContain(kv.binding);
      }
      for (const bucket of (config["r2_buckets"] ?? []) as { bucket_name: string }[]) {
        expect(bucket.bucket_name).toBe(BUCKET);
      }
      const required = ((config["secrets"] as { required?: string[] } | undefined)?.required
                        ?? []);
      expect([worker, required.filter((name) => !(SECRETS[worker] ?? []).includes(name))])
        .toEqual([worker, []]);
    }
    expect(SECRETS["sandbox"]).toEqual(["E2B_API_KEY"]);
    expect(Object.values(NAMESPACES)).toHaveLength(3);
  });

  it("deploys the checked-in config with only its ids, the upstream and the routes filled", () => {
    for (const worker of present) {
      const checked = configOf(worker);
      const deployed = deployedConfig(worker, IDS, UPSTREAM, false);
      const { kv_namespaces: kv, main, vars, assets, $schema: _schema, ...rest } =
        checked as Json;
      const { kv_namespaces: deployedKv, main: deployedMain, vars: deployedVars,
              assets: deployedAssets, ...others } = deployed;
      expect([worker, others]).toEqual([worker, rest]);
      expect(String(deployedMain)).toBe(join(PLATFORM, worker, String(main)));
      // Written outside the Worker's folder: the app files' directory is absolute too.
      expect(deployedAssets).toEqual(assets === undefined ? undefined : {
        ...(assets as Json), directory: join(PLATFORM, worker, String((assets as Json)["directory"])) });
      expect(deployedKv).toEqual(((kv ?? undefined) as { binding: string }[] | undefined)
        ?.map((namespace) => ({ ...namespace, id: IDS[namespace.binding] })));
      expect(deployed["routes"]).toBeUndefined();
      if (worker === "doors") {
        expect(deployedVars).toEqual({ ...(vars as Json), OWNER_ISSUER: UPSTREAM.issuer,
                                       UPSTREAM: JSON.stringify(UPSTREAM.record) });
      } else {
        expect(deployedVars).toEqual(vars);
      }
    }
    const routed = deployedConfig("doors", IDS, UPSTREAM, true)["routes"];
    expect(routed).toEqual([...ROUTES.map((pattern) => ({ pattern, zone_name: "tinyassets.io" })),
                            { pattern: EGRESS_DOMAIN, custom_domain: true }]);
    expect(ROUTES).toEqual(["tinyassets.io/mcp*", "tinyassets.io/api/*",
                            "tinyassets.io/hooks/inbound/*", "tinyassets.io/hooks/e2b/*",
                            "tinyassets.io/app/api/*",
                            "tinyassets.io/authorize*", "tinyassets.io/oauth/*",
                            "tinyassets.io/.well-known/oauth-*"]);
  });

  it("writes a control record its gates read, every outside door closed", () => {
    const control = parseControl(DARK_CONTROL);
    expect(control).toMatchObject({ outside: "off", ordinary: "canary", canary: [],
                                    inbound: false });
    expect(control?.tiers).not.toBeNull();
  });

  const discovery = (body: Json) => async () => Response.json(body);
  const good = { issuer: "https://auth.test", authorization_endpoint: "https://auth.test/a",
                 token_endpoint: "https://auth.test/t", jwks_uri: "https://auth.test/j",
                 token_endpoint_auth_methods_supported: ["client_secret_post"],
                 claims_supported: ["sub", "auth_time"] };

  it("holds the owner issuer to its live discovery exactly, in a record the doors accept",
     async () => {
    const made = await upstreamFrom("https://auth.test", "c", undefined, discovery(good));
    expect(made.record).toEqual(UPSTREAM.record);
    expect(() => upstreamConfig(JSON.stringify(made.record))).not.toThrow();
    await expect(upstreamFrom("https://auth.test", "c", undefined,
                              discovery({ ...good, issuer: "https://auth.test/" })))
      .rejects.toThrow(/not OWNER_ISSUER/);
    await expect(upstreamFrom("https://auth.test", "", undefined, discovery(good)))
      .rejects.toThrow(/WORKOS_CLIENT_ID/);
    await expect(upstreamFrom("http://auth.test", "c", undefined, discovery(good)))
      .rejects.toThrow();
    await expect(upstreamFrom("https://auth.test", "c", undefined, async () =>
      new Response("down", { status: 503 }))).rejects.toThrow(/503/);
  });

  it("stops on an absent discovery field: unknown never passes (research 3b)", async () => {
    const without = (field: string) => {
      const body: Json = { ...good };
      delete body[field];
      return discovery(body);
    };
    for (const field of ["claims_supported", "token_endpoint_auth_methods_supported",
                         "token_endpoint", "jwks_uri", "authorization_endpoint"]) {
      await expect(upstreamFrom("https://auth.test", "c", undefined, without(field)))
        .rejects.toThrow(/unknown/);
    }
    await expect(upstreamFrom("https://auth.test", "c", undefined,
                              discovery({ ...good, claims_supported: ["sub"] })))
      .rejects.toThrow(/auth_time/);
    await expect(upstreamFrom("https://auth.test", "c", undefined,
      discovery({ ...good, token_endpoint_auth_methods_supported: ["private_key_jwt"] })))
      .rejects.toThrow(/client_secret_post/);
  });

  it("settles auth_time by one real token's claims before the discovery's list", async () => {
    const quiet = { ...good, claims_supported: undefined };
    const claims = (body: Json) => JSON.stringify(body);
    expect((await upstreamFrom("https://auth.test", "c",
                               claims({ iss: "https://auth.test", sub: "user_1",
                                        auth_time: 1_700_000_000 }), discovery(quiet))).issuer)
      .toBe("https://auth.test");
    await expect(upstreamFrom("https://auth.test", "c",
                              claims({ iss: "https://auth.test", sub: "user_1" }),
                              discovery(good))).rejects.toThrow(/no auth_time/);
    await expect(upstreamFrom("https://auth.test", "c",
                              claims({ iss: "https://other.test", auth_time: 1 }),
                              discovery(good))).rejects.toThrow(/iss/);
    await expect(upstreamFrom("https://auth.test", "c", "not json", discovery(good)))
      .rejects.toThrow(/JSON/);
  });

  it("registers the WorkOS redirect URI only when missing, and never deletes one", async () => {
    const calls: string[] = [];
    const workos = (uris: string[]) => async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      return init?.method === "POST" ? Response.json({ uri: CALLBACK }, { status: 201 })
        : Response.json({ data: uris.map((uri) => ({ uri })) });
    };
    expect(await registerRedirect("k", true, workos([CALLBACK, "https://other.test/cb"])))
      .toBe("present");
    expect(await registerRedirect("k", false, workos(["https://other.test/cb"]))).toBe("missing");
    expect(await registerRedirect("k", true, workos(["https://other.test/cb"])))
      .toBe("registered");
    expect(calls.filter((call) => call.startsWith("POST"))).toEqual([
      "POST https://api.workos.com/user_management/redirect_uris"]);
    expect(calls.some((call) => call.startsWith("DELETE"))).toBe(false);
  });

  it("sets the routing key on the doors and the owner together, or not at all", () => {
    const none = secretPlan({ doors: [], owner: [], gateway: [], sandbox: [] }, new Set(), NONE);
    expect(none.filter(({ name }) => name === "ROUTING_MAC_KEY").map(({ worker }) => worker)
      .sort()).toEqual(["doors", "owner"]);
    const both = secretPlan({ doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY", "E2B_WEBHOOK_SECRET"],
                              owner: ["ROUTING_MAC_KEY"], gateway: ["GATEWAY_KEYRING"],
                              sandbox: ["E2B_API_KEY"], files: ["FILES_KEYRING"] },
                            new Set(), NONE);
    expect(both).toEqual([{ worker: "doors", name: "INBOUND_SLACK_SIGNING_SECRET" }]);
    expect(() => secretPlan({ doors: ["ROUTING_MAC_KEY"], owner: [] }, new Set(), NONE))
      .toThrow(/--rotate/);
    expect(secretPlan({ doors: ["ROUTING_MAC_KEY"], owner: [] }, new Set(["ROUTING_MAC_KEY"]),
                      NONE).filter(({ name }) => name === "ROUTING_MAC_KEY")).toHaveLength(2);
  });

  it("never generates or replaces a held keyring, and rotates one only by adding a key",
     () => {
    const held = { doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY", "E2B_WEBHOOK_SECRET",
                           "INBOUND_SLACK_SIGNING_SECRET"],
                   owner: ["ROUTING_MAC_KEY"], gateway: ["GATEWAY_KEYRING"],
                   sandbox: ["E2B_API_KEY"], files: ["FILES_KEYRING"] };
    const values = (ring: string) => (name: string) =>
      name === "GATEWAY_KEYRING" || name === "FILES_KEYRING" ? ring : undefined;
    expect(secretPlan(held, new Set(), values(RING))).toEqual([]);
    expect(() => secretPlan(held, new Set(["GATEWAY_KEYRING"]), values(RING)))
      .toThrow(/adding a key/);
    expect(() => secretPlan(held, new Set(["GATEWAY_KEYRING"]),
                            values(JSON.stringify({ active: "k3", keys: { k1: "a", k2: "b" } }))))
      .toThrow(/active key among its keys/);
    expect(secretPlan(held, new Set(["GATEWAY_KEYRING"]), values(ROTATED)))
      .toEqual([{ worker: "gateway", name: "GATEWAY_KEYRING" }]);
    // The files Worker's data keys' ring, by the same rule (slice P).
    expect(() => secretPlan(held, new Set(["FILES_KEYRING"]), values(RING)))
      .toThrow(/FILES_KEYRING holds/);
    expect(secretPlan(held, new Set(["FILES_KEYRING"]), values(ROTATED)))
      .toEqual([{ worker: "files", name: "FILES_KEYRING" }]);
    const kit = readFileSync(join(PLATFORM, "deploy", "deploy.ts"), "utf8");
    // The kit never makes a secret's value in a deploy: only keyring-new and keyring-add,
    // which write a local file for the founder to store.
    expect(kit.match(/randomBytes\(/g)).toHaveLength(2);
  });

  it("refuses to deploy the owner Worker unless the migration lock pins every migration it " +
     "ships, in order (review M5-2)", () => {
    const code = ["a".repeat(64), "b".repeat(64)];
    const first = { at: "2026-11-04T00:00:00.000Z", commit: "abc123" };
    expect(lockRefusal({ first_deploy: first, deployed: code }, code)).toBeNull();
    // Unmarked: the first deploy pins first.
    expect(lockRefusal({ first_deploy: null, deployed: [] }, code)).toMatch(/no first deploy/);
    // A migration the lock does not pin, one it pins that is gone, one changed, or reordered.
    for (const deployed of [[code[0]], [...code, "c".repeat(64)], [code[0], "c".repeat(64)],
                            [code[1], code[0]]]) {
      expect(lockRefusal({ first_deploy: first, deployed }, code)).toMatch(/PIN_MIGRATIONS/);
    }
    expect(lockRefusal({ deployed: code }, code)).toMatch(/out of shape/);
    expect(lockRefusal(null, code)).toMatch(/out of shape/);
    // The kit checks it before any change, on the lock the commit holds.
    const kit = readFileSync(join(PLATFORM, "deploy", "deploy.ts"), "utf8");
    const main = kit.slice(kit.indexOf("async function main()"));
    expect(main.indexOf("lockRefusal(")).toBeGreaterThan(-1);
    expect(main.indexOf("lockRefusal(")).toBeLessThan(main.indexOf("deployWorker("));
    expect(main.indexOf("lockRefusal(")).toBeLessThan(main.indexOf("wrangler(["));
  });

  it("never reads a failed secret listing as no secrets", async () => {
    const path = "/accounts/a/workers/scripts/tinyassets-doors/secrets";
    expect(await secretsOf(api({ [path]: ok([{ name: "WORKOS_API_KEY", type: "secret_text" }]) }),
                           "a", "tinyassets-doors")).toEqual(["WORKOS_API_KEY"]);
    expect(await secretsOf(api({ [path]: { status: 404, body: { success: false,
                                                                errors: [{ code: 10007 }] } } }),
                           "a", "tinyassets-doors")).toBeNull();
    for (const answer of [
      { status: 500, body: {} }, { status: 403, body: { success: false, errors: [{ code: 10000 }] } },
      { status: 404, body: { success: false, errors: [{ code: 7003 }] } },
      { status: 200, body: { success: false } }, { status: 200, body: { success: true } },
    ] as CfAnswer[]) {
      await expect(secretsOf(api({ [path]: answer }), "a", "tinyassets-doors"))
        .rejects.toThrow(/failed/);
    }
    await expect(secretsOf(api({ [path]: new Error("network down") }), "a", "tinyassets-doors"))
      .rejects.toThrow(/network down/);
  });

  it("calls the control record absent only when the API answers it is", async () => {
    const path = "/accounts/a/storage/kv/namespaces/n/values/c%3Acontrol";
    expect(await kvPresent(api({ [path]: { status: 200, body: {} } }), "a", "n", "c:control"))
      .toBe(true);
    expect(await kvPresent(api({ [path]: { status: 404, body: { success: false,
                                                                errors: [{ code: 10009 }] } } }),
                           "a", "n", "c:control")).toBe(false);
    for (const answer of [{ status: 500, body: {} }, { status: 404, body: {} },
                          { status: 401, body: { errors: [{ code: 10000 }] } }] as CfAnswer[]) {
      await expect(kvPresent(api({ [path]: answer }), "a", "n", "c:control"))
        .rejects.toThrow(/failed/);
    }
  });

  it("never reads a failed namespace, bucket or queue listing as absent", async () => {
    const page = (n: number) => `/accounts/a/storage/kv/namespaces?per_page=100&page=${n}`;
    const full = Array.from({ length: 100 }, (_, i) => ({ id: `i${i}`, title: `t${i}` }));
    const found = await namespacesOf(api({ [page(1)]: ok(full),
                                           [page(2)]: ok([{ id: "x", title: "tinyassets-control" }]) }),
                                     "a");
    expect(found.get("tinyassets-control")).toBe("x");
    await expect(namespacesOf(api({ [page(1)]: ok(full) }), "a")).rejects.toThrow(/failed/);
    expect(listed("tinyassets-files-old\nother", "tinyassets-files")).toBe(false);
    expect(listed("name:           tinyassets-files\n", "tinyassets-files")).toBe(true);
    expect(listed("│ q1 │ tinyassets-sandbox-ring │ 0 │", "tinyassets-sandbox-ring")).toBe(true);
    const kit = readFileSync(join(PLATFORM, "deploy", "deploy.ts"), "utf8");
    // wrangler's runner throws on any non-zero exit, so a failed listing stops the kit.
    expect(kit).toMatch(/if \(result\.status !== 0\) \{\s*\/\/[^\n]*\n\s*throw new Error/);
  });

  /** The live proxy's routes (`deploy/cloudflare-worker/wrangler.toml`), and others. */
  const LIVE: readonly Route[] = [
    { id: "r1", pattern: "tinyassets.io/mcp*", script: "tinyassets-mcp-proxy" },
    { id: "r2", pattern: "tinyassets.io/app*", script: "tinyassets-mcp-proxy" },
    { id: "r3", pattern: "tinyassets.io/api*", script: "tinyassets-mcp-proxy" },
    { id: "r4", pattern: "tinyassets.io/.well-known/oauth-protected-resource*",
      script: "tinyassets-mcp-proxy" },
    { id: "r5", pattern: "tinyassets.io/.well-known/oauth-authorization-server*",
      script: "tinyassets-mcp-proxy" },
    { id: "r6", pattern: "tinyassets.io/site*", script: "site" },
  ];
  /** Paths the doors' routes cover, and two they do not. */
  const PROBES = ["tinyassets.io/mcp", "tinyassets.io/mcp?x=1", "tinyassets.io/api/tools/a",
                  "tinyassets.io/.well-known/oauth-protected-resource/mcp",
                  "tinyassets.io/.well-known/oauth-authorization-server",
                  "tinyassets.io/app/api/keys", "tinyassets.io/oauth/token",
                  "tinyassets.io/authorize", "tinyassets.io/hooks/inbound/x",
                  "tinyassets.io/hooks/e2b/lifecycle",
                  "tinyassets.io/app", "tinyassets.io/site/x"];

  /** The Worker Cloudflare sends `path` to: the most specific matching pattern's. */
  function servedBy(routes: Iterable<Route>, path: string): string | undefined {
    let best: Route | undefined;
    for (const route of routes) {
      const wild = route.pattern.endsWith("*");
      const stem = wild ? route.pattern.slice(0, -1) : route.pattern;
      if ((wild ? path.startsWith(stem) : path === stem)
          && (best === undefined || stem.length > best.pattern.replace(/\*$/, "").length)) {
        best = route;
      }
    }
    return best?.script;
  }

  /** A fake of Cloudflare's routes API over `start`, checking every path after each change. */
  function zoneApi(start: readonly Route[], fail?: string) {
    const table = new Map(start.map((route) => [route.id, { ...route }]));
    const before = new Map(PROBES.map((path) => [path, servedBy(table.values(), path)]));
    const calls: string[] = [];
    let next = 100;
    const call = async (path: string, init?: RequestInit): Promise<CfAnswer> => {
      const method = init?.method ?? "GET";
      calls.push(`${method} ${path}`);
      if (path === "/zones?name=tinyassets.io") {
        return ok([{ id: "z", account: { id: "a".repeat(32) } }]);
      }
      if (method === "GET" && path === "/zones/z/workers/routes") {
        return ok([...table.values()]);
      }
      if (fail !== undefined && `${method} ${path}` === fail) {
        return { status: 500, body: { success: false, errors: [{ code: 10000 }] } };
      }
      const body = JSON.parse(String(init?.body ?? "{}")) as { pattern?: string; script?: string };
      const id = path.split("/").at(-1)!;
      if (method === "POST" && path === "/zones/z/workers/routes") {
        table.set(`n${next}`, { id: `n${next++}`, pattern: body.pattern!, script: body.script! });
      } else if (method === "PUT" && table.has(id)) {
        table.set(id, { id, pattern: body.pattern!, script: body.script! });
      } else if (method === "DELETE" && table.has(id)) {
        table.delete(id);
      } else {
        return { status: 404, body: { success: false, errors: [{ code: 10020 }] } };
      }
      for (const [probe, server] of before) {
        // A path served before the handover is served after every one of its changes.
        if (server !== undefined) {
          expect([probe, servedBy(table.values(), probe)]).not.toEqual([probe, undefined]);
        }
      }
      return ok({});
    };
    return Object.assign(call, { calls, table, before });
  }

  it("hands each route from the old proxy to the doors one at a time, no path unserved " +
     "(review D3)", async () => {
    const fake = zoneApi(LIVE);
    const { id: zone, account } = await zoneOf(fake);
    expect(account).toBe("a".repeat(32));
    const steps = handover(await routesOf(fake, zone), DOORS, ROUTES);
    // Identical patterns move in place; a narrower old one is deleted after the doors' wider
    // one exists; a wider old one (app*, api*) keeps only the paths the doors do not cover.
    expect(steps.filter((step) => step.method === "PUT").map((step) => step.pattern))
      .toEqual(["tinyassets.io/mcp*"]);
    expect(steps.filter((step) => step.method === "DELETE").map((step) => step.pattern))
      .toEqual(["tinyassets.io/.well-known/oauth-protected-resource*",
                "tinyassets.io/.well-known/oauth-authorization-server*"]);
    const added = steps.findIndex((step) => step.pattern === "tinyassets.io/.well-known/oauth-*");
    expect(steps.findIndex((step) => step.method === "DELETE")).toBeGreaterThan(added);
    await runHandover(fake, zone, DOORS, steps);
    for (const path of PROBES.slice(0, 10)) {
      expect([path, servedBy(fake.table.values(), path)]).toEqual([path, DOORS]);
    }
    expect(servedBy(fake.table.values(), "tinyassets.io/app")).toBe("tinyassets-mcp-proxy");
    expect(servedBy(fake.table.values(), "tinyassets.io/site/x")).toBe("site");
    // Run again, it changes nothing.
    expect(handover([...fake.table.values()], DOORS, ROUTES)).toEqual([]);
  });

  it("stops the handover at a refused change, saying what is done", async () => {
    const fake = zoneApi(LIVE, "PUT /zones/z/workers/routes/r1");
    const steps = handover(await routesOf(fake, "z"), DOORS, ROUTES);
    await expect(runHandover(fake, "z", DOORS, steps)).rejects.toThrow(/the steps before it/);
    expect(fake.calls.filter((call) => call.startsWith("DELETE"))).toEqual([]);
    await expect(zoneOf(api({}))).rejects.toThrow(/could not be read/);
    // The account is the zone's own: one zone, with its account, or the kit stops.
    const zones = (result: unknown) => api({ "/zones?name=tinyassets.io": ok(result) });
    await expect(zoneOf(zones([{ id: "z" }]))).rejects.toThrow(/could not be read/);
    await expect(zoneOf(zones([{ id: "z", account: { id: "a".repeat(32) } },
                               { id: "y", account: { id: "b".repeat(32) } }])))
      .rejects.toThrow(/2 found/);
    await expect(zoneOf(zones([{ id: "z", account: { id: "../x" } }])))
      .rejects.toThrow(/could not be read/);
    await expect(routesOf(api({}), "z")).rejects.toThrow(/could not be read/);
  });

  it("takes the keyring the founder's setup makes in memory, in keyring-new's own shape",
     () => {
    // scripts/platform_setup.py's maker, through a two-line driver: Python's ring is the one
    // stored, and the apply job's keyring() is the check it must pass.
    const repo = join(PLATFORM, "..");
    const made = execFileSync("python3", ["-I", "-c",
      "import sys; sys.path.insert(0, sys.argv[1]); " +
      "from scripts.platform_setup import make_keyring; sys.stdout.write(make_keyring())",
      repo], { encoding: "utf8" });
    const ring = keyring(made, "FILES_KEYRING");
    expect([ring.active, Object.keys(ring.keys)]).toEqual(["k1", ["k1"]]);
    expect(Buffer.from(ring.keys["k1"]!, "base64").byteLength).toBe(32);
    // The kit's own maker writes the same shape.
    const out = mkdtempSync(join(tmpdir(), "keyring-"));
    try {
      execFileSync(process.execPath, ["--experimental-strip-types",
                                      join(PLATFORM, "deploy", "deploy.ts"), "keyring-new",
                                      join(out, "ring.json")], { stdio: "pipe" });
      const theirs = keyring(readFileSync(join(out, "ring.json"), "utf8"));
      expect([theirs.active, Object.keys(theirs.keys)])
        .toEqual([ring.active, Object.keys(ring.keys)]);
      expect(Buffer.from(theirs.keys["k1"]!, "base64").byteLength).toBe(32);
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });

  it("registers E2B's lifecycle webhook disabled in dark, sets its secret on every apply, " +
     "and enables it only with the routes", async () => {
    /** A fake of E2B's webhooks API: its hooks, and every call as `METHOD path body`. */
    const e2b = (start: Json[], fail?: string) => {
      const hooks = start.map((hook) => ({ ...hook }));
      const calls: string[] = [];
      const fetcher = async (url: string, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        const path = url.replace("https://api.e2b.app", "");
        calls.push(`${method} ${path}${init?.body ? ` ${String(init.body)}` : ""}`);
        expect((init?.headers as Record<string, string>)["X-API-Key"]).toBe("k");
        if (fail === method) {
          return new Response("", { status: 500 });
        }
        if (method === "GET") {
          return Response.json(hooks);
        }
        const body = JSON.parse(String(init!.body)) as Json;
        if (method === "POST") {
          hooks.push({ id: "w1", ...body });
          return Response.json({ id: "w1" }, { status: 201 });
        }
        const hook = hooks.find((each) => `/events/webhooks/${each["id"]}` === path)!;
        Object.assign(hook, body);
        return Response.json(hook);
      };
      return { hooks, calls, fetcher };
    };
    // dark, none held: registered disabled, with the stored secret, for the two events.
    const dark = e2b([{ id: "x", url: "https://elsewhere.test/hook", enabled: true }]);
    expect(await lifecycleHook("k", "s1", true, false, dark.fetcher))
      .toBe("registered, disabled");
    expect(dark.hooks[1]).toEqual({ id: "w1", name: E2B_HOOK.name, url: E2B_HOOK.url,
                                    events: ["sandbox.lifecycle.paused",
                                             "sandbox.lifecycle.killed"],
                                    enabled: false, signatureSecret: "s1" });
    // dark again: the secret set again, never enabled, the other team hook untouched.
    expect(await lifecycleHook("k", "s2", true, false, dark.fetcher))
      .toBe("its secret set again, disabled");
    expect(dark.calls.at(-1)).toBe('PATCH /events/webhooks/w1 {"signatureSecret":"s2"}');
    expect(dark.hooks[0]).toEqual({ id: "x", url: "https://elsewhere.test/hook", enabled: true });
    // routes: enabled, after the handover; and a routes run later changes only the secret.
    expect(await lifecycleHook("k", "s2", true, true, dark.fetcher))
      .toBe("its secret set again, enabled");
    expect(dark.calls.at(-1))
      .toBe('PATCH /events/webhooks/w1 {"signatureSecret":"s2","enabled":true}');
    expect(await lifecycleHook("k", "s2", true, true, dark.fetcher))
      .toBe("its secret set again, enabled");
    expect(dark.calls.at(-1)).toBe('PATCH /events/webhooks/w1 {"signatureSecret":"s2"}');
    // dark after the cutover never turns it off.
    await lifecycleHook("k", "s2", true, false, dark.fetcher);
    expect(dark.hooks[1]!["enabled"]).toBe(true);
    // routes with none held: registered enabled.
    const routed = e2b([]);
    expect(await lifecycleHook("k", "s", true, true, routed.fetcher)).toBe("registered, enabled");
    // Without --yes, only the listing is read.
    const planned = e2b([]);
    expect(await lifecycleHook("k", "s", false, false, planned.fetcher))
      .toBe("missing: registered, disabled");
    expect(planned.calls).toEqual(["GET /events/webhooks"]);
    // A failed listing or write stops the kit; two hooks for the URL are refused.
    await expect(lifecycleHook("k", "s", true, false, e2b([], "GET").fetcher))
      .rejects.toThrow(/listed its webhooks with 500/);
    await expect(lifecycleHook("k", "s", true, false, e2b([], "POST").fetcher))
      .rejects.toThrow(/refused the webhook with 500/);
    await expect(lifecycleHook("k", "s", true, false,
                               e2b([{ id: "a", url: E2B_HOOK.url }], "PATCH").fetcher))
      .rejects.toThrow(/refused the webhook's change/);
    await expect(lifecycleHook("k", "s", true, false,
                               e2b([{ id: "a", url: E2B_HOOK.url },
                                    { id: "b", url: E2B_HOOK.url }]).fetcher))
      .rejects.toThrow(/holds 2 webhooks/);
    // The hook's path is the doors' own, and a route hands it to them.
    expect(E2B_HOOK.url).toBe(`https://tinyassets.io${E2B_HOOK_PATH}`);
    expect(ROUTES.some((pattern) => pattern.endsWith("*")
      && `tinyassets.io${E2B_HOOK_PATH}`.startsWith(pattern.slice(0, -1)))).toBe(true);
  });

  it("checks the sandbox's E2B template has a ready build, and stops on a failed lookup",
     async () => {
    const e2b = (alias: number, exists: unknown, tag = 200) => async (url: string) =>
      url.includes("/aliases/") ? Response.json({ templateID: "t1" }, { status: alias })
        : Response.json({ exists }, { status: tag });
    expect(await templateReady("tinyassets-sandbox:v1", "k", e2b(200, true))).toBe(true);
    expect(await templateReady("tinyassets-sandbox:v1", "k", e2b(200, false))).toBe(false);
    expect(await templateReady("tinyassets-sandbox:v1", "k", e2b(404, true))).toBe(false);
    await expect(templateReady("tinyassets-sandbox:v1", "k", e2b(500, true))).rejects.toThrow();
    await expect(templateReady("tinyassets-sandbox:v1", "k", e2b(200, true, 503)))
      .rejects.toThrow();
    await expect(templateReady("tinyassets-sandbox", "k", e2b(200, true))).rejects.toThrow();
  });

  it("names the pair it deploys by the template and the runner the sandbox Worker ships",
     () => {
    const runner = shippedRunner();
    expect(runner).toMatch(/^[0-9a-f]{64}$/);
    expect(shippedRunner()).toBe(runner);  // the same bytes every build
    expect(pairTag("tinyassets-sandbox:s0123456789abcdef", runner))
      .toBe(`tinyassets-sandbox:s0123456789abcdef-r${runner.slice(0, 16)}`);
    expect(() => pairTag("tinyassets-sandbox:s1", "runner")).toThrow(/SHA-256/);
    // build.py marks a passed pair with the same tag.
    const script = readFileSync(join(PLATFORM, "sandbox", "template", "build.py"), "utf8");
    expect(script).toContain('return f"{tag_}-r{runner_sha[:16]}"');
  }, 60_000);

  it("runs the pair check with the E2B key alone, and stops on a failure", async () => {
    const saved = { ...process.env };
    Object.assign(process.env, { E2B_API_KEY: "e2b_key", CLOUDFLARE_API_TOKEN: "cf",
                                 GATEWAY_KEYRING: "ring", WORKOS_API_KEY: "workos" });
    try {
      let tagged = false;
      const e2b = async (url: string) => url.includes("/aliases/")
        ? Response.json({ templateID: "t1" }) : Response.json({ exists: tagged });
      const calls: { argv: readonly string[]; env: Record<string, string> }[] = [];
      const spawn = ((command: string, argv: readonly string[],
                      options: { env: Record<string, string> }) => {
        calls.push({ argv: [command, ...argv], env: options.env });
        tagged = true;
        return { status: 0 };
      }) as unknown as Parameters<typeof pairCheck>[0];
      await pairCheck(spawn, e2b);
      expect(calls).toHaveLength(1);
      expect(calls[0]!.argv.slice(-1)).toEqual(["--check-pair"]);
      expect(Object.keys(calls[0]!.env).sort()).toEqual(["E2B_API_KEY", "HOME", "PATH"]);
      expect(calls[0]!.env["E2B_API_KEY"]).toBe("e2b_key");
      // Already passed: nothing is run.
      await pairCheck(spawn, e2b);
      expect(calls).toHaveLength(1);
      // A failed check, or one that tagged nothing, stops the kit.
      tagged = false;
      const failed = (() => ({ status: 1 })) as unknown as Parameters<typeof pairCheck>[0];
      await expect(pairCheck(failed, e2b)).rejects.toThrow(/failed its standing checks/);
      const silent = (() => ({ status: 0 })) as unknown as Parameters<typeof pairCheck>[0];
      await expect(pairCheck(silent, e2b)).rejects.toThrow(/failed its standing checks/);
    } finally {
      process.env = saved;
    }
  }, 60_000);

  it("deploys a later version without touching routes, and names its version", () => {
    expect(versionOf("Uploaded tinyassets-owner\nWorker Version ID: " +
                     "0f7e3a3c-1b2d-4e5f-8a9b-0c1d2e3f4a5b\n"))
      .toBe("0f7e3a3c-1b2d-4e5f-8a9b-0c1d2e3f4a5b");
    expect(() => versionOf("Uploaded tinyassets-owner")).toThrow(/Version ID/);
  });

  it("plans in a job of its own, then applies in the production environment after it " +
     "(review D1, D2)", () => {
    const workflow = readFileSync(join(PLATFORM, "..", ".github", "workflows",
                                       "platform-deploy.yml"), "utf8");
    expect(workflow).toMatch(/^on:\s*\n\s+workflow_dispatch:/m);
    expect(workflow).not.toMatch(/^\s+(push|pull_request|schedule):/m);
    expect(workflow).not.toMatch(/ssh|droplet|\/etc\/tinyassets/i);
    const plan = workflow.slice(workflow.indexOf("\n  plan:\n"), workflow.indexOf("\n  pair:\n"));
    const pair = workflow.slice(workflow.indexOf("\n  pair:\n"), workflow.indexOf("\n  apply:\n"));
    const apply = workflow.slice(workflow.indexOf("\n  apply:\n"));
    // The pair check: after the plan, before the apply, in the production environment with
    // the E2B key alone (in its one step), its SDK from the hashed lock, its timeout past the
    // three throwaway sandboxes' own (900 + 1800 + 300 s, `build.py`).
    expect(pair).toMatch(/^    needs: plan$/m);
    expect(pair).toMatch(/^    environment: production$/m);
    expect(pair).toContain("deploy.ts --pair");
    expect(pair).toContain("--require-hashes -r platform/sandbox/template/requirements.txt");
    expect([...pair.matchAll(/secrets\.([A-Z0-9_]+)/g)].map((found) => found[1]))
      .toEqual(["E2B_API_KEY"]);
    expect(pair).not.toMatch(/^    env:/m);
    expect(Number(/timeout-minutes: (\d+)/.exec(pair)![1]) * 60).toBeGreaterThan(900 + 1800 + 300);
    const script = readFileSync(join(PLATFORM, "sandbox", "template", "build.py"), "utf8");
    for (const seconds of [900, 1800, 300]) {
      expect(script).toContain(`timeout=${seconds}`);
    }
    expect(apply).toMatch(/^    needs: \[plan, pair\]$/m);
    expect(apply).toContain("needs.pair.result == 'success'");
    expect(apply).not.toMatch(/pip install|python/);
    // The plan holds no production secret, so it needs no environment and runs first.
    expect(plan).not.toMatch(/environment:/);
    expect(plan).toContain("deploy.ts");
    expect(plan).toContain("args=(--public)");
    expect(plan).not.toMatch(/--yes/);
    for (const name of SECRET_INPUTS) {
      expect(plan).not.toContain(name);
    }
    // The account is read from the zone (`zoneOf`), never held as an input.
    expect(PUBLIC_INPUTS).not.toContain("CLOUDFLARE_ACCOUNT_ID");
    expect(workflow).not.toContain("CLOUDFLARE_ACCOUNT_ID");
    for (const name of PUBLIC_INPUTS) {
      expect(plan).toMatch(new RegExp(`${name}: \\$\\{\\{ (secrets|vars)\\.${name} \\}\\}`));
    }
    // The apply job: after the plan, in the repo's main-only environment
    // (tests/test_workflow_production_environment.py), with every input the kit reads.
    expect(apply).toMatch(/^    environment: production$/m);
    for (const name of [...PUBLIC_INPUTS, ...SECRET_INPUTS]) {
      expect(apply).toMatch(new RegExp(`${name}: \\$\\{\\{ (secrets|vars)\\.${name} \\}\\}`));
    }
    for (const name of SECRET_INPUTS) {
      expect(apply).toContain(`${name}: \${{ secrets.${name} }}`);
    }
    // The handles canary's own bearer (scripts/_canary_common.py), so Fact 11's canary runs.
    const canary = /CANARY_TOKEN_ENV = "([A-Z_]+)"/.exec(readFileSync(
      join(PLATFORM, "..", "scripts", "_canary_common.py"), "utf8"))![1]!;
    expect(apply).toContain(`${canary}: \${{ secrets.${canary} }}`);
    const kit = readFileSync(join(PLATFORM, "deploy", "deploy.ts"), "utf8");
    expect(kit).not.toMatch(/\/etc\/tinyassets/);
    expect(kit).toContain("mcp_public_canary.py");
  });
});
