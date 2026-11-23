/**
 * An MCP client's held agent registration: filed on the client's own verified call, so the object
 * captured its origin, in the place its grant acts, and applied or declined by the owner by card
 * id and the digest it was shown, as a held policy proposal is.
 */
import { describe, expect, it } from "vitest";

import { GRANT_LIMITS, type Grants } from "../src/grants";
import { clock } from "../src/clock";
import type { Origin } from "../../contract/src/binding";
import { APP_RESOURCE } from "../../contract/src/identity";
import { CARD_LIMITS, type Registrations } from "../src/registrations";
import { DOT, MUSE, rig, type Rig } from "./ledger-support";
import { atClock, failure, inStores } from "./support";

const REQUEST = Object.freeze({ command_center_id: "alpha", display_name: "Muse",
                                delivery_method: "mcp_event", recipe_id: null });
const CONN = Object.freeze({ kind: "mcp", ref: "conn-muse" });

const cards = (r: Rig) => r.table("agent_cards");
/** The pending cards, as the owner is shown them. */
const shown = async (r: Rig) => (await r.stub.registerProposals(r.bind(null))).cards;
const held = async (r: Rig, origin: Origin) => (await r.sql(
  "SELECT agents, held FROM outside_clients WHERE client = ?", origin.client))[0];

/** The `n`th of the many clients, or families, a test fills a bound with. */
const filler = (n: number, pad = "0") => n.toString(16).padStart(64, pad);

/** The object's registrations on its own storage (`storesOn`), in one unit. */
function inCards<R>(r: Rig, act: (cards: Registrations, grants: Grants) => R): Promise<R> {
  return inStores(r.stub, ({ registrations, grants }) => act(registrations, grants));
}

describe("an MCP client's agent registration", () => {
  it("is filed on the client's own call and applied by the owner in session, once",
     async () => {
    const r = await rig();
    const filed = await r.stub.registerPropose(r.bind(MUSE, { source: CONN }), REQUEST);
    expect(filed.card_id).toMatch(/^card_[0-9a-f]{32}$/);
    expect(await held(r, MUSE)).toEqual({ agents: 0, held: 1 });  // the card holds the row
    const [card, ...others] = await shown(r);
    expect([card!.card_id, card!.client, card!.family, card!.source_ref, card!.request,
            card!.digest, others])
      .toEqual([filed.card_id, MUSE.client, MUSE.family, "conn-muse", REQUEST, filed.digest, []]);
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

  it("applies only with the digest shown, in the owner's session, whatever its age",
     async () => {
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
    // A card has no life of its own: years on, it is shown and applied, in a session of then.
    const later = clock() + 10 * 365 * 86_400;
    expect(await atClock(later, async () => {
      const [card] = await shown(r);
      const agent = await r.stub.registerAgent({
        ...r.bind(null), session: (await r.stub.sessionOpen(r.bind(null, {
          authenticated_at: Math.floor(later) }))).session }, {
        card_id: filed.card_id, digest: card!.digest });
      return [card!.digest, agent.outside_client, "expires_at" in card!];
    })).toEqual([filed.digest, MUSE.client, false]);
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
    // A filing is held to the rule its apply is, so the owner is shown only a card that applies.
    for (const [asked, reason] of [
      [{ recipe_id: { nested: 1 } }, "recipe_id is 1 to 128 printable characters"],
      [{ delivery_method: "pigeon" }, "unknown delivery method"],
      [{ display_name: "sk-secret" }, "display_name is required: letters, digits, spaces, " +
                                      "dots, dashes"],
    ] as const) {
      expect((await failure(() => r.stub.registerPropose(r.bind(MUSE), {
        ...REQUEST, ...asked } as never))).message).toBe(reason);
    }
    expect(await cards(r)).toEqual([]);
    const filed = await r.stub.registerPropose(r.bind(MUSE), REQUEST);
    for (const refused of [
      () => r.stub.registerProposals(r.bind(MUSE)),
      () => r.stub.registerDecline(r.bind(MUSE), { card_id: filed.card_id,
                                                   digest: filed.digest }),
    ]) {
      expect((await failure(refused)).message).toMatch(/^only the owner may /);
    }
    await expect(r.sql("UPDATE agent_cards SET client = ?", DOT.client))
      .rejects.toThrow(/never changes/);
  });

  it("replaces the identity's card when filed again, and at a bound lets the oldest go",
     async () => {
    const r = await rig();
    // DOT's card, the oldest of all until DOT's identity files again below and replaces it.
    const dots = await r.stub.registerPropose(r.bind(DOT), REQUEST);
    const first = await r.stub.registerPropose(r.bind(MUSE), REQUEST);
    const second = await r.stub.registerPropose(r.bind(MUSE), { ...REQUEST,
                                                                display_name: "Muse Two" });
    expect((await cards(r)).map((row) => row["card_id"]))
      .toEqual([dots.card_id, second.card_id]);
    expect(first.card_id).not.toBe(second.card_id);
    const out = await inCards(r, (registry, grants) => {
      const stamp = (origin: Origin) => ({ client: origin.client, generation: 1,
                                           authenticated_at: null });
      const tried = (origin: Origin, request: Record<string, unknown> = REQUEST) => {
        try {
          return registry.file(origin, "conn", stamp(origin), request, clock()).card_id;
        } catch (error) {
          return (error as Error).message;
        }
      };
      /** The request whose JSON is `size` characters. */
      const sized = (size: number) => ({ ...REQUEST, display_name: "x".repeat(
        size - JSON.stringify({ ...REQUEST, display_name: "" }).length) });
      // MUSE's other families to its share: the last, past it, lets MUSE's oldest go, not
      // DOT's older one.
      const mine = Array.from({ length: CARD_LIMITS.perClient }, (_, n) =>
        tried({ ...MUSE, family: filler(n, "e") }));
      const pending = registry.list().map((card) => card.card_id);
      const kept = [second.card_id, dots.card_id].map((id) => pending.includes(id));
      const request = [tried(DOT, sized(CARD_LIMITS.maxRequest + 1)),
                       tried(DOT, sized(CARD_LIMITS.maxRequest))];
      // Other clients' cards, each below its share, to the set's bound and one past it,
      // which lets the oldest of all go: MUSE's first family's.
      const others: string[] = [];
      const room = CARD_LIMITS.maxPending - CARD_LIMITS.perClient;
      for (let n = 0; others.length < room; n++) {
        grants.admit(filler(n), "mcp", null, clock());
        for (let k = 0; k < CARD_LIMITS.perClient - 1 && others.length < room; k++) {
          others.push(tried({ client: filler(n), family: filler(k, "e") }));
        }
      }
      return { mine, kept, request, others,
               stranger: tried({ client: "1".repeat(64), family: "2".repeat(64) }) };
    });
    expect(out.kept).toEqual([false, true]);
    const ids = [...out.mine, out.request[1]!, ...out.others];
    expect(ids.every((id) => /^card_[0-9a-f]{32}$/.test(id))).toBe(true);
    expect([out.request[0], out.stranger]).toEqual([
      `a registration is at most ${CARD_LIMITS.maxRequest} characters`,
      "outside client has no grant"]);
    const kept = (await cards(r)).map((row) => row["card_id"]);
    expect(kept).toHaveLength(CARD_LIMITS.maxPending);
    expect(kept.sort()).toEqual(ids.filter((id) => id !== out.mine[0]).sort());
    expect(kept).not.toContain(second.card_id);
    // Each eviction is told, once an asker (both were MUSE's), never naming the request.
    expect((await r.table("owner_notices")).filter((row) => row["type"] ===
      "held_request_evicted").map((row) => row["detail"])).toEqual([
      `an agent registration of ${MUSE.client} was let go to make room for a newer one`]);
  });

  it("holds its client's row, and ends with any change to the client's grant", async () => {
    const r = await rig();
    await r.stub.registerPropose(r.bind(MUSE), REQUEST);
    // The free rows at their bound, each seen after MUSE's: DOT's makes room by the least
    // recently seen of them, never by MUSE's row, which its card holds.
    const free = Array.from({ length: GRANT_LIMITS.maxFreeClients }, (_, n) => filler(n));
    await inStores(r.stub, ({ grants }) => {
      const t = clock() + 1;
      free.forEach((client, n) => grants.admit(client, "mcp", null, t + n));
      grants.admit(DOT.client, "mcp", null, t + free.length);
    });
    expect(await held(r, MUSE)).toEqual({ agents: 0, held: 1 });  // not evicted
    expect(await held(r, { client: free[0]!, family: "" })).toBeUndefined();
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    expect(await cards(r)).toEqual([]);
    expect(await held(r, MUSE)).toEqual({ agents: 0, held: 0 });
  });

  it("keeps a card whatever its age, and files only under the filer's own stamp",
     async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(MUSE));
    await r.stub.outsideAgents(r.bind(DOT));
    const out = await inCards(r, (registry) => {
      const t = clock();
      const by = (origin: Origin) => ({ client: origin.client, generation: 1,
                                        authenticated_at: null });
      registry.file(MUSE, "conn", by(MUSE), REQUEST, t);
      registry.file(DOT, "conn", by(DOT), REQUEST, t + 10 * 365 * 86_400);  // MUSE's stays
      const mismatched = (() => {
        try {
          registry.file(DOT, "conn", by(MUSE), REQUEST, t);
          return "filed";
        } catch (error) {
          return (error as Error).message;
        }
      })();
      return mismatched;
    });
    expect(out).toBe("invalid outside origin");
    expect((await cards(r)).map((row) => row["client"]).sort())
      .toEqual([MUSE.client, DOT.client].sort());
    expect(await held(r, MUSE)).toEqual({ agents: 0, held: 1 });
  });

  it("is counted by a client row recorded after it, as a store change could leave it",
     async () => {
    const r = await rig();
    await r.sql("INSERT INTO agent_cards (card_id, client, family, source_ref, generation, " +
                  "authenticated_at, request, digest, filed_at) VALUES (?, ?, ?, 'conn', 1, " +
                  "NULL, '{}', 'd', ?)", "card_" + "1".repeat(32), MUSE.client, MUSE.family,
                clock());
    await r.stub.outsideAgents(r.bind(MUSE));
    expect(await held(r, MUSE)).toEqual({ agents: 0, held: 1 });
  });

  it("answers the owner app's page of the card, and tells the owner of it there", async () => {
    const r = await rig();
    const filed = await r.stub.registerPropose(r.bind(MUSE), REQUEST);
    const page = `${APP_RESOURCE}#apps/${filed.card_id}`;
    expect(filed.approve_url).toBe(page);
    expect((await r.table("owner_notices")).filter((row) => row["type"] ===
      "agent_registration").map((row) => row["detail"])).toEqual([
      `An app you connected asks to register an agent, for a mailbox and rooms: approve or ` +
      `decline it at ${page}`]);
  });

  it("is declined by the owner only as shown, and then neither applies nor holds its row",
     async () => {
    const r = await rig();
    const { card_id, digest } = await r.stub.registerPropose(r.bind(MUSE), REQUEST);
    for (const asked of [{ card_id, digest: "0".repeat(64) },
                         { card_id: "card_" + "0".repeat(32), digest },
                         { card_id: "nope", digest: "" }]) {
      expect((await failure(() => r.stub.registerDecline(r.bind(null), asked))).message)
        .toBe("registration is not the pending card shown");
    }
    expect(await shown(r)).toHaveLength(1);
    // A no changes nothing the client may do, so it needs no session.
    expect(await r.stub.registerDecline(r.bind(null), { card_id, digest }))
      .toEqual({ card_id, declined: true });
    expect([await shown(r), await held(r, MUSE)]).toEqual([[], { agents: 0, held: 0 }]);
    expect((await failure(() => r.stub.registerAgent(r.session(null), { card_id, digest })))
      .message)
      .toBe("registration is not the pending card shown");
    expect((await r.stub.outsideAgents(r.bind(null))).agents).toEqual([]);
  });

  it("registers where its client's grant acts: a full grant's in main unless it names a " +
     "place, a narrowed grant's in the space its scopes name", async () => {
    const r = await rig();
    const { command_center_id: _named, ...unplaced } = REQUEST;
    const centerOf = async () => (await shown(r))[0]!.request["command_center_id"];
    await r.stub.registerPropose(r.bind(MUSE), unplaced);
    expect(await centerOf()).toBe("main");
    await r.stub.registerPropose(r.bind(MUSE), REQUEST);
    expect(await centerOf()).toBe("alpha");
    /** MUSE's grant narrowed to `spaces`, as the owner narrows it in its session. */
    const narrowed = async (...spaces: string[]) => {
      const { revision } = (await r.stub.grantList(r.bind(null))).clients
        .find((entry) => entry.client === MUSE.client)!;
      await r.stub.grantSet(r.session(null), { client: MUSE.client, source: "mcp",
        mode: "scoped", expected_revision: revision, name: "Muse",
        scopes: spaces.map((universe) => ({ universe, agent: "main",
                                            capability: "read_graph" })) });
    };
    await narrowed("work");
    expect(await cards(r)).toEqual([]);  // the grant change dropped the card
    const filed = await r.stub.registerPropose(r.bind(MUSE), unplaced);
    expect(await centerOf()).toBe("work");
    expect((await failure(() => r.stub.registerPropose(r.bind(MUSE), REQUEST))).message)
      .toBe("this connection's agent registers in a space its grant names: work");
    await narrowed("work", "home");
    expect((await failure(() => r.stub.registerPropose(r.bind(MUSE), unplaced))).message)
      .toBe("this connection's grant names more than one space: name one of work, home as " +
            "command_center_id");
    const named = await r.stub.registerPropose(r.bind(MUSE), { ...unplaced,
                                                               command_center_id: "home" });
    expect(await centerOf()).toBe("home");
    // Its agent acts there: the scoped grant reads its mailbox once the owner applies it.
    expect(filed.card_id).not.toBe(named.card_id);
    const agent = await r.stub.registerAgent(r.session(null), { card_id: named.card_id,
                                                                digest: named.digest });
    expect(agent.command_center_id).toBe("home");
    expect((await r.read(MUSE)).messages).toEqual([]);
  });

  it("is rechecked with standing at its apply", async () => {
    const r = await rig();
    const filed = await r.stub.registerPropose(r.bind(MUSE), REQUEST);
    // A grant change made around the object's doors, so only the apply's recheck sees it.
    await inStores(r.stub, ({ grants }) => {
      grants.revoke(MUSE.client, clock());
    });
    expect((await failure(() => r.stub.registerAgent(r.session(null), {
      card_id: filed.card_id, digest: filed.digest }))).message).toBe("outside client revoked");
    expect((await r.stub.outsideAgents(r.bind(null))).agents).toEqual([]);
    expect(await cards(r)).toHaveLength(1);  // the refused apply rolled back whole
  });
});
