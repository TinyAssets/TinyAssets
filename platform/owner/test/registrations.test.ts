/**
 * An MCP client's held agent registration (slice 3's review L2): filed on the client's
 * own verified call, so the object captured its origin, and applied by the owner in its
 * session by card id and the digest it was shown, as a held policy proposal is.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { GRANT_LIMITS, Grants } from "../src/grants";
import { clock, type Origin } from "../src/ledger";
import type { OwnerDO } from "../src/owner-do";
import { Policy } from "../src/policy";
import { CARD_LIMITS, CARD_TTL_S, type CardLimits, Registrations } from "../src/registrations";
import { DOT, MUSE, rig, type Rig } from "./ledger-support";
import { failure } from "./support";

const REQUEST = Object.freeze({ command_center_id: "alpha", display_name: "Muse",
                                delivery_method: "mcp_event", recipe_id: null });
const CONN = Object.freeze({ kind: "mcp", ref: "conn-muse" });

const cards = (r: Rig) => r.table("agent_cards");
const held = async (r: Rig, origin: Origin) => (await r.sql(
  "SELECT agents, held FROM outside_clients WHERE client = ?", origin.client))[0];

/** The object's registrations on its own storage, with some bounds changed. */
function inCards<R>(r: Rig, limits: Partial<CardLimits>,
                    act: (cards: Registrations, grants: Grants) => R): Promise<R> {
  return runInDurableObject(r.stub, (_i: OwnerDO, state) => {
    const grants = new Grants(state.storage, new Policy(state.storage));
    return act(new Registrations(state.storage, grants, { ...CARD_LIMITS, ...limits }), grants);
  });
}

describe("an MCP client's agent registration", () => {
  it("is filed on the client's own call and applied by the owner in session, once",
     async () => {
    const r = await rig();
    const filed = await r.stub.registerPropose(r.bind(MUSE, { source: CONN }), REQUEST);
    expect(filed.card_id).toMatch(/^card_[0-9a-f]{32}$/);
    expect(await held(r, MUSE)).toEqual({ agents: 0, held: 1 });  // the card holds the row
    const card = await r.stub.registerProposal(r.bind(null), { card_id: filed.card_id });
    expect([card.client, card.family, card.source_ref, card.request, card.digest])
      .toEqual([MUSE.client, MUSE.family, "conn-muse", REQUEST, filed.digest]);
    const agent = await r.stub.registerAgent(r.session(null), { card_id: filed.card_id,
                                                                digest: filed.digest });
    expect([agent.outside_client, agent.outside_family, agent.source, agent.source_ref,
            agent.delivery_method]).toEqual([MUSE.client, MUSE.family, "mcp", "conn-muse",
                                             "mcp_event"]);
    expect(await cards(r)).toEqual([]);
    expect(await held(r, MUSE)).toEqual({ agents: 1, held: 0 });
    const again = await failure(() => r.stub.registerAgent(r.session(null), {
      card_id: filed.card_id, digest: filed.digest }));
    expect(again.message).toBe("registration is not the pending card shown");
  });

  it("applies only with the digest shown, unexpired, in the owner's session", async () => {
    const r = await rig();
    const filed = await r.stub.registerPropose(r.bind(MUSE), REQUEST);
    for (const [binding, apply, reason] of [
      [r.bind(null), filed, "interactive owner session required"],
      [r.session(null), { ...filed, digest: "0".repeat(64) },
        "registration is not the pending card shown"],
      [r.session(null), { ...filed, card_id: "card_" + "0".repeat(32) },
        "registration is not the pending card shown"],
      [r.session(null), { ...filed, card_id: "nope" },
        "registration is not the pending card shown"],
    ] as const) {
      const refused = await failure(() => r.stub.registerAgent(binding, {
        card_id: apply.card_id, digest: apply.digest }));
      expect(refused.message).toBe(reason);
    }
    expect((await r.stub.outsideAgents(r.bind(null))).agents).toEqual([]);
    // Past its life, a card is neither shown nor applied.
    expect(await inCards(r, {}, (registry, _grants) => {
      const later = clock() + CARD_TTL_S + 1;
      try {
        registry.take(filed.card_id, filed.digest, later);
        return "applied";
      } catch (error) {
        return [(error as Error).message, registry.card(filed.card_id, later)];
      }
    })).toEqual(["registration is not the pending card shown", null]);
  });

  it("is filed only by an MCP client, shown only to the owner, and never changes",
     async () => {
    const r = await rig();
    const { origin, source } = await r.keyed("0123456789abcdef");
    expect((await failure(() => r.stub.registerPropose(r.bind(origin, { source }), REQUEST)))
      .message).toBe("an API key's agent registers from the owner's browser with the key");
    expect((await failure(() => r.stub.registerPropose(r.bind(null), REQUEST))).message)
      .toBe("only an outside client files its own agent's registration");
    expect((await failure(() => r.stub.registerPropose(r.bind(MUSE), {
      ...REQUEST, extra: 1 } as never))).message).toBe("invalid ledger request");
    expect((await failure(() => r.stub.registerPropose(r.bind(MUSE), {
      ...REQUEST, recipe_id: { nested: 1 } } as never))).message)
      .toBe("a registration's fields are flat values");
    const filed = await r.stub.registerPropose(r.bind(MUSE), REQUEST);
    expect((await failure(() => r.stub.registerProposal(r.bind(MUSE), {
      card_id: filed.card_id }))).message)
      .toBe("only the owner may read its agents' registrations");
    await expect(r.sql("UPDATE agent_cards SET client = ?", DOT.client))
      .rejects.toThrow(/never changes/);
  });

  it("replaces the identity's card when filed again, and is bounded", async () => {
    const r = await rig();
    const first = await r.stub.registerPropose(r.bind(MUSE), REQUEST);
    const second = await r.stub.registerPropose(r.bind(MUSE), { ...REQUEST,
                                                                display_name: "Muse Two" });
    expect((await cards(r)).map((row) => row["card_id"])).toEqual([second.card_id]);
    expect(first.card_id).not.toBe(second.card_id);
    await r.stub.outsideAgents(r.bind(DOT));
    const out = await inCards(r, { perClient: 1, maxPending: 2, maxRequest: 120 }, (registry) => {
      const stamp = (origin: Origin) => ({ client: origin.client, generation: 1,
                                           authenticated_at: null });
      const tried = (origin: Origin, request: Record<string, unknown> = REQUEST) => {
        try {
          return registry.file(origin, "conn", stamp(origin), request, clock()).card_id.slice(0, 5);
        } catch (error) {
          return (error as Error).message;
        }
      };
      return [tried({ ...MUSE, family: "e".repeat(64) }), tried(DOT),
              tried(DOT, { ...REQUEST, display_name: "x".repeat(200) }),
              tried({ client: "1".repeat(64), family: "2".repeat(64) })];
    });
    expect(out).toEqual(["this client's pending registrations are at their limit", "card_",
                         "a registration is at most 120 characters",
                         "outside client has no grant"]);
  });

  it("holds its client's row, and ends with any change to the client's grant", async () => {
    const r = await rig();
    await r.stub.registerPropose(r.bind(MUSE), REQUEST);
    await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      new Grants(state.storage, new Policy(state.storage), { ...GRANT_LIMITS, maxFreeClients: 1 })
        .admit(DOT.client, "mcp", null, clock());
    });
    expect(await held(r, MUSE)).toEqual({ agents: 0, held: 1 });  // not evicted
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    expect(await cards(r)).toEqual([]);
    expect(await held(r, MUSE)).toEqual({ agents: 0, held: 0 });
  });

  it("is rechecked with standing at its apply", async () => {
    const r = await rig();
    const filed = await r.stub.registerPropose(r.bind(MUSE), REQUEST);
    // A grant change made around the object's doors, so only the apply's recheck sees it.
    await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      new Grants(state.storage, new Policy(state.storage)).revoke(MUSE.client, clock());
    });
    expect((await failure(() => r.stub.registerAgent(r.session(null), {
      card_id: filed.card_id, digest: filed.digest }))).message).toBe("outside client revoked");
    expect((await r.stub.outsideAgents(r.bind(null))).agents).toEqual([]);
    expect(await cards(r)).toHaveLength(1);  // the refused apply rolled back whole
  });
});
