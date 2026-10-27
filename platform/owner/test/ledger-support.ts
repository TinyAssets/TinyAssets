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
  const owner = `alice-${crypto.randomUUID()}`;
  const stub: DurableObjectStub<OwnerDO> = env.OWNER.getByName(owner);
  const bind = (origin: Origin | null, extra: Partial<LedgerBinding> = {}): LedgerBinding =>
    ({ owner, caller: owner, origin, ...extra });
  const session = (origin: Origin | null, source: Source = MCP): LedgerBinding =>
    bind(origin, { interactive: true, source });
  const sender = new FakeSender();

  const registered = (origin: Origin, name = "Muse", method = "pull",
                      extra: { max_wakes_per_hour?: number; batch_window_s?: number;
                               command_center_id?: string } = {}) =>
    stub.registerAgent(session(origin), {
      command_center_id: "alpha", display_name: name, delivery_method: method,
      recipe_id: null, ...extra,
    });

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
    /** The ledger on this object's own storage, with some bounds lowered. */
    inLedger: <R>(limits: Partial<LedgerLimits>, act: (ledger: Ledger) => R): Promise<R> =>
      runInDurableObject(stub, (_i: OwnerDO, state) =>
        act(new Ledger(state.storage, { ...LEDGER_LIMITS, ...limits }))),
    /** One alarm pass at `now`, with the scripted doorbell. */
    tick: (now: number = clock()): Promise<number> =>
      runInDurableObject(stub, (_i: OwnerDO, state) =>
        runWakes(new Ledger(state.storage), sender, () => now)),
  };
}

export type Rig = ReturnType<typeof rig>;

/** Receipt kinds other than reads and replies, oldest first. */
export async function wakeReceipts(r: Rig): Promise<string[]> {
  return (await r.table("ledger_receipts")).map((row) => row["kind"] as string)
    .filter((kind) => kind !== "read" && kind !== "reply" && kind !== "ack");
}

/** The usage counters, and the same numbers counted from the rows themselves. */
export async function counters(r: Rig): Promise<[Row, Row]> {
  const [usage] = await r.sql("SELECT agents, messages, body_bytes, settled_wakes " +
                              "FROM ledger_usage");
  const [actual] = await r.sql(
    "SELECT (SELECT COUNT(*) FROM outside_agents) AS agents, " +
      "(SELECT COUNT(*) FROM ledger_messages) AS messages, " +
      "(SELECT COALESCE(SUM(length(CAST(body AS BLOB))), 0) FROM message_bodies) AS body_bytes, " +
      "(SELECT COUNT(*) FROM ledger_wakes WHERE settled_at IS NOT NULL) AS settled_wakes",
  );
  return [usage!, actual!];
}
