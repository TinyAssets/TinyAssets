import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";

import {
  clock, Ledger, LEDGER_LIMITS, type LedgerLimits, type Origin, type Outcome, type Source,
  type WakeTarget,
} from "../src/ledger";
import type { LedgerBinding, OwnerDO } from "../src/owner-do";
import { runWakes, type WakeSender } from "../src/wakes";

export const MUSE: Origin = Object.freeze({ client: "a".repeat(64), family: "b".repeat(64) });
export const DOT: Origin = Object.freeze({ client: "c".repeat(64), family: "d".repeat(64) });
export const MCP: Source = Object.freeze({ kind: "mcp", ref: "conn-1" });

/** The `n`th distinct outside identity, for tests that need many agents. */
export function origin(n: number): Origin {
  const hex = n.toString(16).padStart(8, "0");
  return { client: hex.repeat(8), family: "f".repeat(56) + hex };
}

type Row = Record<string, SqlStorageValue>;

/** A doorbell that answers from a script: an outcome, or `crash` to die mid-send. */
export class FakeSender implements WakeSender {
  readonly sent: WakeTarget[] = [];
  readonly revoked: string[] = [];
  readonly answers: (Outcome | "crash")[] = [];

  async send(target: WakeTarget, mark: () => void): Promise<Outcome> {
    mark();
    this.sent.push(target);
    const answer = this.answers.shift() ?? { state: "sent", status: 202 };
    if (answer === "crash") {
      throw new Error("the object died after the request left");
    }
    return answer;
  }

  async revoke(agentId: string): Promise<void> {
    this.revoked.push(agentId);
  }
}

export interface Post {
  body?: string;
  cmid?: string;
  parent?: string | null;
}

/**
 * One owner's object, by that owner's own name, and the calls a binder would make on it.
 * Each test gets a fresh owner, so no test sees another's ledger.
 */
export function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub: DurableObjectStub<OwnerDO> = env.OWNER.getByName(owner);
  const bind = (origin: Origin | null, extra: Partial<LedgerBinding> = {}): LedgerBinding =>
    ({ owner, caller: owner, origin, ...extra });
  const session = (origin: Origin | null, source: Source = MCP): LedgerBinding =>
    bind(origin, { interactive: true, source });
  const sender = new FakeSender();

  /**
   * An agent registered from `origin` in the owner's session, then given any wake limits
   * by the owner's own policy setter (a registration never carries them).
   */
  const registered = async (origin: Origin, name = "Muse", method = "pull",
                            extra: { max_wakes_per_hour?: number; batch_window_s?: number;
                                     command_center_id?: string } = {}) => {
    const { max_wakes_per_hour: perHour, batch_window_s: batch, ...request } = extra;
    const agent = await stub.registerAgent(session(origin), {
      command_center_id: "alpha", display_name: name, delivery_method: method,
      recipe_id: null, ...request,
    });
    if (perHour === undefined && batch === undefined) {
      return agent;
    }
    return stub.setWakeLimits(session(null), {
      agent_id: agent.agent_id, max_wakes_per_hour: perHour ?? agent.max_wakes_per_hour,
      batch_window_s: batch ?? agent.batch_window_s,
    });
  };

  return {
    owner, stub, bind, session, sender,
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
    /** A whole table, in insertion order. */
    table: (name: string): Promise<Row[]> => runInDurableObject(stub, (_i: OwnerDO, state) =>
      state.storage.sql.exec<Row>(`SELECT * FROM ${name} ORDER BY rowid`).toArray()),
    sql: (statement: string, ...params: SqlStorageValue[]): Promise<Row[]> =>
      runInDurableObject(stub, (_i: OwnerDO, state) =>
        state.storage.sql.exec<Row>(statement, ...params).toArray()),
    /** The ledger on this object's own storage, with some bounds changed. */
    inLedger: <R>(limits: Partial<LedgerLimits>, act: (ledger: Ledger) => R): Promise<R> =>
      runInDurableObject(stub, (_i: OwnerDO, state) =>
        act(new Ledger(state.storage, { ...LEDGER_LIMITS, ...limits }))),
    /** One alarm pass at `now`, with the scripted doorbell. */
    tick: (now: number = clock()): Promise<number> =>
      runInDurableObject(stub, (_i: OwnerDO, state) =>
        runWakes(new Ledger(state.storage), sender, () => now)),
    /** The object's alarm time in seconds, or null when it is not set. */
    alarmAt: (): Promise<number | null> => runInDurableObject(stub, async (_i: OwnerDO, state) => {
      const at = await state.storage.getAlarm();
      return at === null ? null : at / 1000;
    }),
  };
}

export type Rig = ReturnType<typeof rig>;

/** Receipt kinds other than reads, replies and acks, oldest first. */
export async function wakeReceipts(r: Rig): Promise<string[]> {
  return (await r.table("ledger_receipts")).map((row) => row["kind"] as string)
    .filter((kind) => kind !== "read" && kind !== "reply" && kind !== "ack");
}

/** Every counter the ledger's bounds read, and the same numbers counted from the rows. */
export async function counters(r: Rig): Promise<[Row, Row]> {
  const [usage] = await r.sql(
    "SELECT agents, enabled_agents, messages, body_bytes, settled_wakes, " +
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
