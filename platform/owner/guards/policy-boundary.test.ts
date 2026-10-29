/**
 * N10 and P0-3 policy-writers: owner policy, grants and sessions are written through
 * the object's doors only.
 *
 * Ported from `tests/test_policy_writers.py` (origin/main 1fd430857, and its P0-3 form on
 * `p0-3-outside-grants`). There, only the broker's policy store wrote policy and grants
 * and only the router's owner channel submitted a widening. Here, only the object
 * imports the session and grant modules (so only its doors, which check the owner's
 * session themselves, can call a widening), and each policy table is named only by the
 * module that keeps it and the schema. The stamped schedule's work and the held agent
 * registrations are held to the same: only the object (and the registrations, for the
 * stamp's type) import them. So are the model connections and their spend: only the
 * object builds them, the provider and `auto` read their types and calls, and their
 * blob, flow, reservation and spend tables are their modules' alone. The lists only
 * shrink.
 */
import { describe, expect, it } from "vitest";

import { workerSources } from "./sources";

const OWNER_DO = "owner/src/owner-do.ts";
const SCHEMA = "owner/src/schema.ts";
const MODULES: Readonly<Record<string, readonly string[]>> = Object.freeze({
  policy: Object.freeze([OWNER_DO, "owner/src/grants.ts", SCHEMA]),
  grants: Object.freeze([OWNER_DO]),
  sessions: Object.freeze([OWNER_DO]),
  scheduled: Object.freeze([OWNER_DO, "owner/src/registrations.ts"]),
  registrations: Object.freeze([OWNER_DO]),
  connections: Object.freeze([OWNER_DO, "owner/src/auto.ts", "owner/src/gateway-provider.ts"]),
  usage: Object.freeze([OWNER_DO, "owner/src/gateway-provider.ts"]),
});
const TABLES: Readonly<Record<string, readonly string[]>> = Object.freeze({
  owner_sessions: Object.freeze(["owner/src/sessions.ts", SCHEMA]),
  owner_policy: Object.freeze(["owner/src/policy.ts", SCHEMA]),
  policy_receipts: Object.freeze(["owner/src/policy.ts", SCHEMA]),
  policy_proposals: Object.freeze(["owner/src/policy.ts", SCHEMA]),
  proposal_shares: Object.freeze(["owner/src/policy.ts", SCHEMA]),
  outside_clients: Object.freeze(["owner/src/grants.ts", SCHEMA]),
  grant_fences: Object.freeze(["owner/src/grants.ts", SCHEMA]),
  policy_usage: Object.freeze(["owner/src/grants.ts", "owner/src/policy.ts",
                               "owner/src/registrations.ts", "owner/src/scheduled.ts",
                               "owner/src/sessions.ts", SCHEMA]),
  scheduled_work: Object.freeze(["owner/src/scheduled.ts", SCHEMA]),
  agent_cards: Object.freeze(["owner/src/registrations.ts", SCHEMA]),
  // The `connections` table's name is a plain word the sources use, so its sealed blobs
  // and flows are what is held to their module.
  connection_secrets: Object.freeze(["owner/src/connections.ts", SCHEMA]),
  connection_flows: Object.freeze(["owner/src/connections.ts", SCHEMA]),
  model_reservations: Object.freeze(["owner/src/usage.ts", SCHEMA]),
  model_spend: Object.freeze(["owner/src/usage.ts", SCHEMA]),
});

/**
 * Tables no source names at all: Lifecycle's (`cf_agents_*`, its job queue among them) and
 * pi's session store (`pi_*`). The SDK alone reads and writes them.
 */
const RESERVED: readonly RegExp[] = Object.freeze([/\bcf_agents_\w*/, /\bpi_[a-z]\w*/]);

function importer(module: string): RegExp {
  return new RegExp(`(?:\\bfrom\\s*|\\bimport\\s*\\(?\\s*)["'][^"']*/${module}(?:\\.[cm]?[jt]s)?["']`);
}

/** Who imports each policy module, and who names each policy table, but the module itself. */
export function reach(sources: Map<string, string>): Record<string, string[]> {
  const found: Record<string, string[]> = {};
  for (const [path, source] of [...sources].sort(([a], [b]) => (a < b ? -1 : 1))) {
    for (const module of Object.keys(MODULES)) {
      if (path !== `owner/src/${module}.ts` && importer(module).test(source)) {
        (found[module] ??= []).push(path);
      }
    }
    for (const table of Object.keys(TABLES)) {
      if (new RegExp(`\\b${table}\\b`).test(source)) {
        (found[table] ??= []).push(path);
      }
    }
    if (RESERVED.some((reserved) => reserved.test(source))) {
      (found["reserved"] ??= []).push(path);
    }
  }
  return found;
}

describe("policy-boundary guard", () => {
  it("lets only the object reach the session and grant modules, and each table its own",
     () => {
    const expected = Object.fromEntries([...Object.entries(MODULES), ...Object.entries(TABLES)]
      .map(([name, paths]) => [name, [...paths].sort()]));
    expect(reach(workerSources())).toEqual(expected);
  });

  it.each([
    ['import { Sessions } from "./sessions";', "sessions"],
    ["import type { Grant } from './grants.ts';", "grants"],
    ['const policy = await import("./policy");', "policy"],
    ['sql.exec("UPDATE owner_policy SET generation = 9");', "owner_policy"],
    ['sql.exec("DELETE FROM outside_clients");', "outside_clients"],
    ['sql.exec("DELETE FROM cf_agents_jobs");', "reserved"],
    ['sql.exec("UPDATE cf_agents_jobs SET time = 0");', "reserved"],
    ['sql.exec("SELECT * FROM pi_entries");', "reserved"],
  ])("sees %s", (line, name) => {
    expect(reach(new Map([["owner/src/probe.ts", line]]))[name]).toEqual(["owner/src/probe.ts"]);
  });
});
