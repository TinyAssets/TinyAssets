import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";

import { Grants } from "../src/grants";
import {
  apiKeyOrigin, clock, Ledger, LEDGER_LIMITS, type LedgerLimits, type Origin, type Outcome,
  type Source, type WakeTarget,
} from "../src/ledger";
import type { Binding, OwnerDO } from "../src/owner-do";
import { Policy } from "../src/policy";
import { runWakes, type WakeSender } from "../src/wakes";
import { alarmOf, laneAt } from "./support";

export const MUSE: Origin = Object.freeze({ client: "a".repeat(64), family: "b".repeat(64) });
export const DOT: Origin = Object.freeze({ client: "c".repeat(64), family: "d".repeat(64) });
export const MCP: Source = Object.freeze({ kind: "mcp", ref: "conn-1" });

/** The `n`th distinct outside identity, for tests that need many agents. */
export function origin(n: number): Origin {
  const hex = n.toString(16).padStart(8, "0");
  return { client: hex.repeat(8), family: "f".repeat(56) + hex };
}

type Row = Record<string, SqlStorageValue>;

/**
 * A doorbell that answers from a script: an outcome, or `crash` to die mid-send. It
 * keeps each agent's callbacks by the subscription generation they were tagged with, and
 * a revoke erases only those at or before its generation, as the contract says.
 */
export class FakeSender implements WakeSender {
  readonly sent: WakeTarget[] = [];
  readonly revoked: [string, number][] = [];
  readonly answers: (Outcome | "crash")[] = [];
  readonly callbacks = new Map<string, Set<number>>();

  /** The subscribe door storing a callback tagged with its generation. */
  subscribed(agentId: string, generation: number): void {
    this.callbacks.set(agentId, (this.callbacks.get(agentId) ?? new Set()).add(generation));
  }

  async send(target: WakeTarget, mark: () => void): Promise<Outcome> {
    mark();
    this.sent.push(target);
    const answer = this.answers.shift() ?? { state: "sent", status: 202 };
    if (answer === "crash") {
      throw new Error("the object died after the request left");
    }
    return answer;
  }

  async revoke(agentId: string, generation: number): Promise<void> {
    this.revoked.push([agentId, generation]);
    const live = [...(this.callbacks.get(agentId) ?? [])].filter((g) => g > generation);
    this.callbacks.set(agentId, new Set(live));
  }
}

export interface Post {
  body?: string;
  cmid?: string;
  parent?: string | null;
}

/**
 * The ledger on an object's own storage, reading that object's own grants. A test that
 * registers on it directly first admits the agent's outside client, as the object's door
 * admits every outside call before any act: an MCP client is recorded `full`.
 */
export function ledgerOn(storage: DurableObjectStorage,
                         limits: Partial<LedgerLimits> = {}): Ledger {
  const grants = new Grants(storage, new Policy(storage));
  return new (class extends Ledger {
    override register(origin: Origin | null, source: unknown,
                      request: Parameters<Ledger["register"]>[2], now?: number) {
      if (origin !== null) {
        grants.admit(origin.client, (source as Source).kind, null, clock());
      }
      return super.register(origin, source, request, now);
    }
  })(storage, grants, { ...LEDGER_LIMITS, ...limits });
}

/** Now, in the whole seconds a binder reports a sign-in at. */
export function signedIn(): number {
  return Math.floor(clock());
}

/**
 * One owner's object, by that owner's own name, and the calls a binder would make on it.
 * Each test gets a fresh owner, so no test sees another's ledger. The owner has signed in
 * once, so `session` carries a live protected-session cookie the object minted.
 */
export async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub: DurableObjectStub<OwnerDO> = env.OWNER.getByName(owner);
  /** A binder's binding; an outside origin comes through an MCP client unless named. */
  const bind = (origin: Origin | null, extra: Partial<Binding> = {}): Binding =>
    ({ owner, caller: owner, origin, ...(origin === null ? {} : { source: MCP }), ...extra });
  const { session: cookie } = await stub.sessionOpen(bind(null, { authenticated_at: signedIn() }));
  const session = (origin: Origin | null, source: Source = MCP): Binding =>
    bind(origin, { session: cookie, source });
  const sender = new FakeSender();

  /**
   * An MCP agent registered from `origin`: its client files the card on its own call, and
   * the owner applies it in its session. Then given any wake limits by the owner's own
   * policy setter (a registration never carries them).
   */
  const registered = async (origin: Origin, name = "Muse", method = "pull",
                            extra: { max_wakes_per_hour?: number; batch_window_s?: number;
                                     command_center_id?: string } = {}) => {
    const { max_wakes_per_hour: perHour, batch_window_s: batch, ...request } = extra;
    const { card_id, digest } = await stub.registerPropose(bind(origin), {
      command_center_id: "alpha", display_name: name, delivery_method: method,
      recipe_id: null, ...request,
    });
    const agent = await stub.registerAgent(session(null), { card_id, digest });
    if (perHour === undefined && batch === undefined) {
      return agent;
    }
    const { generation } = await stub.policyRead(bind(null));
    return (await stub.policyWakeLimits(session(null), {
      agent_id: agent.agent_id, max_wakes_per_hour: perHour ?? agent.max_wakes_per_hour,
      batch_window_s: batch ?? agent.batch_window_s, expected_generation: generation,
    })).agent;
  };

  /** An API key's grant, as the directory's mint writes it with the key: full. */
  const keyed = async (keyId: string) => {
    const origin = apiKeyOrigin(keyId);
    await stub.grantSet(session(null), {
      client: origin.client, source: "api_key", mode: "full", scopes: [],
      expected_generation: 0, name: "",
    });
    return { origin, source: { kind: "api_key", ref: keyId } as const };
  };

  return {
    owner, stub, bind, session, cookie, sender, keyed,
    registered,
    register: async (...args: Parameters<typeof registered>) =>
      (await registered(...args)).agent_id,
    post: (origin: Origin | null, room: string, p: Post = {}) => stub.roomPost(bind(origin), {
      room_id: room, client_message_id: p.cmid ?? "c1", body: p.body ?? "hello",
      parent_id: p.parent ?? null,
    }),
    read: (origin: Origin | null = null, limit = 50, budget: { max_bytes?: number;
                                                               pad_bytes?: number } = {}) =>
      stub.mailboxRead(bind(origin), { limit, ...budget }),
    ack: (origin: Origin | null, through: number) =>
      stub.mailboxAck(bind(origin), { through }),
    /** The agent's own client subscribes to its mailbox; the doorbell keeps the callback. */
    subscribe: async (origin: Origin, agentId: string, commandCenter = "alpha") => {
      const { generation } = await stub.mailboxSubscriber(
        bind(origin), { command_center_id: commandCenter, agent_id: agentId });
      sender.subscribed(agentId, generation);
      return generation;
    },
    /** A whole table, in insertion order. */
    table: (name: string): Promise<Row[]> => runInDurableObject(stub, (_i: OwnerDO, state) =>
      state.storage.sql.exec<Row>(`SELECT * FROM ${name} ORDER BY rowid`).toArray()),
    sql: (statement: string, ...params: SqlStorageValue[]): Promise<Row[]> =>
      runInDurableObject(stub, (_i: OwnerDO, state) =>
        state.storage.sql.exec<Row>(statement, ...params).toArray()),
    /** The ledger on this object's own storage, with some bounds changed. */
    inLedger: <R>(limits: Partial<LedgerLimits>, act: (ledger: Ledger) => R): Promise<R> =>
      runInDurableObject(stub, (_i: OwnerDO, state) => act(ledgerOn(state.storage, limits))),
    /** One alarm pass at `now`, with the scripted doorbell. */
    tick: (now: number = clock()): Promise<number> =>
      runInDurableObject(stub, (_i: OwnerDO, state) =>
        runWakes(ledgerOn(state.storage), sender, () => now)),
    /** The object's physical alarm in seconds, as Lifecycle armed it, or null. */
    alarmAt: (): Promise<number | null> => alarmOf(stub),
    /** The `wakes` lane's Lifecycle job due time in seconds, or null when it has none. */
    wakesAt: (): Promise<number | null> => laneAt(stub, "wakes"),
  };
}

export type Rig = Awaited<ReturnType<typeof rig>>;

/** Receipt kinds other than reads, replies and acks, oldest first. */
export async function wakeReceipts(r: Rig): Promise<string[]> {
  return (await r.table("ledger_receipts")).map((row) => row["kind"] as string)
    .filter((kind) => kind !== "read" && kind !== "reply" && kind !== "ack");
}

/** Every counter the ledger's bounds read, and the same numbers counted from the rows. */
export async function counters(r: Rig): Promise<[Row, Row]> {
  const [usage] = await r.sql(
    "SELECT agents, enabled_agents, messages, body_bytes, settled_wakes, tombstones, " +
      "(SELECT json_group_object(reader, unacked || '/' || unacked_bytes) FROM " +
      "(SELECT * FROM ledger_readers WHERE unacked > 0 ORDER BY reader)) AS mailboxes, " +
      "(SELECT json_group_object(agent_id, unacked_to_owner || '/' || unacked_to_owner_bytes) " +
      "FROM (SELECT * FROM outside_agents WHERE unacked_to_owner > 0 ORDER BY agent_id)) " +
      "AS shares, " +
      "(SELECT json_group_object(message_id, unacked) FROM " +
      "(SELECT * FROM ledger_messages WHERE unacked > 0 ORDER BY message_id)) AS held " +
      "FROM ledger_usage",
  );
  const [actual] = await r.sql(
    "SELECT (SELECT COUNT(*) FROM outside_agents) AS agents, " +
      "(SELECT COUNT(*) FROM outside_agents WHERE disabled_reason IS NULL) AS enabled_agents, " +
      "(SELECT COUNT(*) FROM ledger_messages) AS messages, " +
      "(SELECT COALESCE(SUM(length(CAST(body AS BLOB))), 0) FROM message_bodies) AS body_bytes, " +
      "(SELECT COUNT(*) FROM ledger_wakes WHERE settled_at IS NOT NULL) AS settled_wakes, " +
      "(SELECT COUNT(*) FROM outside_agents WHERE deleted_at IS NOT NULL) AS tombstones, " +
      "(SELECT json_group_object(agent_id, n || '/' || b) FROM (SELECT r.agent_id, " +
      "COUNT(*) AS n, SUM(m.body_bytes) AS b FROM ledger_recipients r JOIN ledger_messages m " +
      "USING (message_id) WHERE r.unacked = 1 GROUP BY r.agent_id ORDER BY r.agent_id)) " +
      "AS mailboxes, " +
      "(SELECT json_group_object(sender_id, n || '/' || b) FROM (SELECT sender_id, " +
      "COUNT(*) AS n, SUM(body_bytes) AS b FROM ledger_messages WHERE sender_kind = 'outside' " +
      "AND message_id IN (SELECT message_id FROM ledger_recipients WHERE unacked = 1) " +
      "GROUP BY sender_id ORDER BY sender_id)) AS shares, " +
      "(SELECT json_group_object(message_id, n) FROM (SELECT message_id, COUNT(*) AS n " +
      "FROM ledger_recipients WHERE unacked = 1 GROUP BY message_id ORDER BY message_id)) " +
      "AS held",
  );
  return [usage!, actual!];
}
