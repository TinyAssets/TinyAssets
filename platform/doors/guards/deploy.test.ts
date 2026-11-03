/**
 * The deploy kit (`platform/deploy/deploy.ts`, slice L), built and never run here: it names
 * every Worker, binding, namespace and secret the platform's configs declare; the config it
 * deploys is the checked-in one with only the namespace ids, the doors' upstream and (with
 * `--routes`) the routes filled; it holds the owner issuer to its live discovery exactly;
 * it registers the WorkOS redirect URI only when missing and never deletes one; and it sets
 * the routing key on the doors and the owner together or not at all.
 */
import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../owner/guards/sources";
import {
  BUCKET, CALLBACK, configOf, DARK_CONTROL, deployedConfig, EGRESS_DOMAIN, NAMESPACES,
  registerRedirect, ROUTES, SECRETS, secretPlan, upstreamFrom, WORKERS,
} from "../../deploy/deploy";
import { parseControl } from "../src/directory";

type Json = Record<string, unknown>;

const present = WORKERS.filter((worker) => existsSync(join(PLATFORM, worker, "wrangler.jsonc")));
const UPSTREAM = { issuer: "https://auth.test",
                   record: { client_id: "c", authorization_endpoint: "https://auth.test/a",
                             token_endpoint: "https://auth.test/t",
                             jwks_uri: "https://auth.test/j",
                             user_delete: "https://api.workos.com/user_management/users/{sub}" } };
const IDS = Object.fromEntries(Object.keys(NAMESPACES).map((binding) => [binding, `id-${binding}`]));

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
      const { kv_namespaces: kv, main, vars, $schema: _schema, ...rest } = checked as Json;
      const { kv_namespaces: deployedKv, main: deployedMain, vars: deployedVars, ...others } =
        deployed;
      expect([worker, others]).toEqual([worker, rest]);
      expect(String(deployedMain)).toBe(join(PLATFORM, worker, String(main)));
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

  it("holds the owner issuer to its live discovery exactly", async () => {
    const discovery = (body: Json) => async () => Response.json(body);
    const good = { issuer: "https://auth.test", authorization_endpoint: "https://auth.test/a",
                   token_endpoint: "https://auth.test/t", jwks_uri: "https://auth.test/j",
                   claims_supported: ["sub", "auth_time"] };
    expect((await upstreamFrom("https://auth.test", "c", discovery(good))).record)
      .toEqual(UPSTREAM.record);
    await expect(upstreamFrom("https://auth.test", "c",
                              discovery({ ...good, issuer: "https://auth.test/" })))
      .rejects.toThrow(/not OWNER_ISSUER/);
    await expect(upstreamFrom("https://auth.test", "c",
                              discovery({ ...good, claims_supported: ["sub"] })))
      .rejects.toThrow(/auth_time/);
    await expect(upstreamFrom("https://auth.test", "", discovery(good)))
      .rejects.toThrow(/WORKOS_CLIENT_ID/);
    await expect(upstreamFrom("http://auth.test", "c", discovery(good))).rejects.toThrow();
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
    const none = secretPlan({ doors: [], owner: [], gateway: [], sandbox: [] }, new Set());
    expect(none.filter(({ name }) => name === "ROUTING_MAC_KEY").map(({ worker }) => worker)
      .sort()).toEqual(["doors", "owner"]);
    const both = secretPlan({ doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY"],
                              owner: ["ROUTING_MAC_KEY"], gateway: ["GATEWAY_KEYRING"],
                              sandbox: ["E2B_API_KEY"] }, new Set());
    expect(both).toEqual([{ worker: "doors", name: "INBOUND_SLACK_SIGNING_SECRET" }]);
    expect(() => secretPlan({ doors: ["ROUTING_MAC_KEY"], owner: [] }, new Set()))
      .toThrow(/--rotate/);
    expect(secretPlan({ doors: ["ROUTING_MAC_KEY"], owner: [] }, new Set(["ROUTING_MAC_KEY"]))
      .filter(({ name }) => name === "ROUTING_MAC_KEY")).toHaveLength(2);
  });
});
