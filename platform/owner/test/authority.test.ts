/**
 * Outside-client authority (tasks 5, A2; kept by ADR-034 as a run's authority): a stamp
 * taken only whole; the owner's authority holding everything and none nothing; a client's
 * stamp holding only while its grant stands at the stamped generation; a scoped grant
 * holding each capability where the work acts; the owner's own capabilities never a
 * client's; the admission refusing every check; and the grants' own reads, over the
 * owner's real grant rows, a revoke ending what the stamp could do.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import {
  AGENT_DIR, agentId, Authority, AUTHORITY_CHANGED, authorityOf, NO_AUTHORITY, NOT_GRANTED,
  NOT_OWNER, OWNER_ONLY, refusalCode, type Stamped, stamped, stampOf,
} from "../src/authority";
import { Refused } from "../src/errors";
import { WriteGate } from "../src/gate";
import { Grants, holds } from "../src/grants";
import type { OwnerDO } from "../src/owner-do";
import { Policy } from "../src/policy";
import { MUSE, rig, signedIn } from "./ledger-support";

const STAMP: Stamped = Object.freeze({ client: MUSE.client, generation: 1,
                                       authenticated_at: null, universe: "alpha",
                                       agent: "main" });
const CLIENT = Object.freeze({ by: "client" as const, stamp: STAMP });

/** An authority over grants that answer as `mode`, holding the capabilities `held`. */
function under(mode: string, held: readonly string[] = [],
               admission: () => string | null = () => null): Authority {
  return new Authority({
    grants: { standing: () => ({ mode, holds: (scope) => scope.universe === "alpha"
                                   && scope.agent === "main" && held.includes(scope.capability) }) },
    admission,
  });
}

describe("a stamp", () => {
  it("is taken only whole: a client, its generation, sign-in, universe and agent", () => {
    const good = { client: MUSE.client, generation: 2, authenticated_at: null,
                   universe: "alpha", agent: "main" };
    expect(stamped(good)).toEqual(good);
    expect(stamped(null)).toBeNull();
    for (const bad of [{ ...good, extra: 1 }, { ...good, generation: 0 },
                       { ...good, client: "x" }, { ...good, agent: "*" },
                       { ...good, universe: "" }, { ...good, authenticated_at: -1 }, [], 1]) {
      expect(() => stamped(bad)).toThrow(/a stamp is a client/);
    }
  });

  it("is the work's authority, the owner's none, and read back only as a client's", () => {
    expect(authorityOf(null)).toEqual({ by: "owner" });
    expect(authorityOf(STAMP)).toEqual(CLIENT);
    expect(stampOf(authorityOf(STAMP))).toEqual(STAMP);
    expect([stampOf({ by: "owner" }), stampOf({ by: "none" }), stampOf(undefined)])
      .toEqual([null, null, null]);
  });

  it("names an agent by its directory's name", () => {
    expect(agentId("main")).toBe("main");
    expect(AGENT_DIR.test("a.b-c_d")).toBe(true);
    for (const bad of ["", ".hidden", "a/b", "x".repeat(129), 7]) {
      expect(() => agentId(bad)).toThrow(/an agent id/);
    }
  });

  it("reads a refusal's fixed code", () => {
    expect(refusalCode("owner_suspended: billing")).toBe("owner_suspended");
    expect(refusalCode(NOT_GRANTED)).toBe(NOT_GRANTED);
  });
});

describe("the authority's checks", () => {
  it("hold the owner to everything and none to nothing", () => {
    const authority = under("scoped");
    expect(authority.refusal({ by: "owner" }, OWNER_ONLY)).toBeUndefined();
    expect(authority.refusal({ by: "none" }, [])).toBe(NO_AUTHORITY);
    expect(authority.refusal(undefined, [])).toBe(NO_AUTHORITY);
  });

  it("hold a scoped client to each capability where its work acts, never to the owner's",
     () => {
    const authority = under("scoped", ["read_files"]);
    expect(authority.refusal(CLIENT, [])).toBeUndefined();
    expect(authority.refusal(CLIENT, ["read_files"])).toBeUndefined();
    expect(authority.refusal(CLIENT, ["read_files", "write_files"])).toBe(NOT_GRANTED);
    expect(authority.refusal(CLIENT, OWNER_ONLY)).toBe(NOT_OWNER);
    // Where it acts: another universe, or none, holds nothing.
    expect(authority.refusal({ by: "client", stamp: { ...STAMP, universe: "beta" } },
                             ["read_files"])).toBe(NOT_GRANTED);
    expect(authority.refusal({ by: "client", stamp: { ...STAMP, universe: null } },
                             ["read_files"])).toBe(NOT_GRANTED);
  });

  it("run everything only under a grant that says full", () => {
    expect(["full", "scoped", "revoked", "unknown"].map((mode) =>
      under(mode).refusal(CLIENT, ["read_files"]) ?? "runs"))
      .toEqual(["runs", NOT_GRANTED, NOT_GRANTED, NOT_GRANTED]);
    expect(under("full").refusal(CLIENT, ["run_graph", "write_files"])).toBeUndefined();
    expect(under("full").refusal(CLIENT, OWNER_ONLY)).toBe(NOT_OWNER);
  });

  it("refuse with the fixed reason a stamp whose grant no longer stands, and fail loudly " +
     "when the grants cannot be read", () => {
    const changed = new Authority({ grants: { standing: () => {
      throw new Refused("outside client authority changed");
    } } });
    // A refusal the grants raise is the fixed reason, whatever it said.
    expect(changed.refusal(CLIENT, [])).toBe(AUTHORITY_CHANGED);
    const broken = new Authority({ grants: { standing: () => {
      throw new Error("the grants could not be read");
    } } });
    expect(() => broken.refusal(CLIENT, ["read_files"])).toThrow(/could not be read/);
  });

  it("refuse every check while the owner is not admitted, the owner's own too", () => {
    const authority = under("full", [], () => "owner_suspended: billing");
    expect(authority.refusal({ by: "owner" }, [])).toBe("owner_suspended: billing");
    expect(authority.refusal(CLIENT, [])).toBe("owner_suspended: billing");
  });

  it("hold one of several needs, else the first one's refusal", () => {
    const authority = under("scoped", ["write_files"]);
    expect(() => authority.requireOne(CLIENT, [["read_files"], ["write_files"]])).not.toThrow();
    expect(() => authority.requireOne(CLIENT, [OWNER_ONLY, ["run_graph"]])).toThrow(NOT_OWNER);
    expect(() => authority.requireOne(CLIENT, [])).toThrow(NOT_GRANTED);
  });
});

describe("the authority over the owner's own grants", () => {
  it("holds a scoped client's stamp while its grant stands, and refuses it once revoked",
     async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(MUSE, { authenticated_at: signedIn() }));
    const row = (await r.stub.grantList(r.bind(null))).clients
      .find((client) => client.client === MUSE.client);
    const { generation } = await r.stub.grantSet(r.session(null), {
      client: MUSE.client, source: "mcp", mode: "scoped", name: "",
      scopes: [{ universe: "alpha", agent: "main", capability: "read_files" }],
      expected_revision: row?.revision ?? 0 });
    const stamp: Stamped = { ...STAMP, generation };
    const check = (need: readonly ("read_files" | "write_files")[]) =>
      runInDurableObject(r.stub, (_instance: OwnerDO, state) => {
        const storage = new WriteGate(state.storage).storage;
        const grants = new Grants(storage, new Policy(storage));
        const authority = new Authority({ grants: { standing: (client, at, signed) => {
          const grant = grants.standing(client, at, signed);
          return { mode: grant.mode, holds: (scope) => holds(grant, scope) };
        } } });
        return authority.refusal({ by: "client", stamp }, need) ?? "runs";
      });
    expect(await check(["read_files"])).toBe("runs");
    expect(await check(["write_files"])).toBe(NOT_GRANTED);
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    expect(await check(["read_files"])).toBe(AUTHORITY_CHANGED);
  });
});
