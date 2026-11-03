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
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../owner/guards/sources";
import {
  BUCKET, CALLBACK, type CfAnswer, configOf, DARK_CONTROL, deployedConfig, EGRESS_DOMAIN,
  DOORS, handover, kvPresent, listed, NAMESPACES, namespacesOf, PUBLIC_INPUTS, registerRedirect,
  ROUTES, type Route, routesOf, runHandover, SECRET_INPUTS, zoneOf,
  SECRETS, secretPlan, secretsOf, templateReady, upstreamFrom, versionOf, WORKERS,
} from "../../deploy/deploy";
import { parseControl } from "../src/directory";
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
                            "tinyassets.io/hooks/inbound/*", "tinyassets.io/app/api/*",
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
    const both = secretPlan({ doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY"],
                              owner: ["ROUTING_MAC_KEY"], gateway: ["GATEWAY_KEYRING"],
                              sandbox: ["E2B_API_KEY"] }, new Set(), NONE);
    expect(both).toEqual([{ worker: "doors", name: "INBOUND_SLACK_SIGNING_SECRET" }]);
    expect(() => secretPlan({ doors: ["ROUTING_MAC_KEY"], owner: [] }, new Set(), NONE))
      .toThrow(/--rotate/);
    expect(secretPlan({ doors: ["ROUTING_MAC_KEY"], owner: [] }, new Set(["ROUTING_MAC_KEY"]),
                      NONE).filter(({ name }) => name === "ROUTING_MAC_KEY")).toHaveLength(2);
  });

  it("never generates or replaces a held keyring, and rotates one only by adding a key",
     () => {
    const held = { doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY", "INBOUND_SLACK_SIGNING_SECRET"],
                   owner: ["ROUTING_MAC_KEY"], gateway: ["GATEWAY_KEYRING"],
                   sandbox: ["E2B_API_KEY"] };
    const values = (ring: string) => (name: string) =>
      name === "GATEWAY_KEYRING" ? ring : undefined;
    expect(secretPlan(held, new Set(), values(RING))).toEqual([]);
    expect(() => secretPlan(held, new Set(["GATEWAY_KEYRING"]), values(RING)))
      .toThrow(/adding a key/);
    expect(() => secretPlan(held, new Set(["GATEWAY_KEYRING"]),
                            values(JSON.stringify({ active: "k3", keys: { k1: "a", k2: "b" } }))))
      .toThrow(/active key among its keys/);
    expect(secretPlan(held, new Set(["GATEWAY_KEYRING"]), values(ROTATED)))
      .toEqual([{ worker: "gateway", name: "GATEWAY_KEYRING" }]);
    const kit = readFileSync(join(PLATFORM, "deploy", "deploy.ts"), "utf8");
    // The kit never makes a secret's value in a deploy: only keyring-new and keyring-add,
    // which write a local file for the founder to store.
    expect(kit.match(/randomBytes\(/g)).toHaveLength(2);
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
        return ok([{ id: "z" }]);
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
    const zone = await zoneOf(fake);
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
    for (const path of PROBES.slice(0, 9)) {
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
    await expect(routesOf(api({}), "z")).rejects.toThrow(/could not be read/);
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
    const plan = workflow.slice(workflow.indexOf("\n  plan:\n"), workflow.indexOf("\n  apply:\n"));
    const apply = workflow.slice(workflow.indexOf("\n  apply:\n"));
    // The plan holds no production secret, so it needs no environment and runs first.
    expect(plan).not.toMatch(/environment:/);
    expect(plan).toContain("deploy.ts");
    expect(plan).toContain("args=(--public)");
    expect(plan).not.toMatch(/--yes/);
    for (const name of SECRET_INPUTS) {
      expect(plan).not.toContain(name);
    }
    for (const name of PUBLIC_INPUTS) {
      expect(plan).toMatch(new RegExp(`${name}: \\$\\{\\{ (secrets|vars)\\.${name} \\}\\}`));
    }
    // The apply job: after the plan, in the repo's main-only environment
    // (tests/test_workflow_production_environment.py), with every input the kit reads.
    expect(apply).toMatch(/^    needs: plan$/m);
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
