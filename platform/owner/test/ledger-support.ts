import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";

import { isAdmitted } from "../src/admission";
import { type Adopt, Bodies } from "../src/bodies";
import { Collections } from "../src/collections";
import { WriteGate } from "../src/gate";
import { Grants } from "../src/grants";
import { Ledger, LEDGER_LIMITS, type Outcome, type WakeTarget } from "../src/ledger";
import { apiKeyOrigin } from "../../contract/src/binding";
import type { Refusal } from "../../contract/src/refusal";
import { clock } from "../src/clock";
import type { Binding, Origin, Source } from "../../contract/src/binding";
import type { OwnerDO } from "../src/owner-do";
import { Policy } from "../src/policy";
import { subscriptionId } from "../../contract/src/wake-kinds";
import { CLOSED, runWakes, type WakeSender } from "../src/wakes";
import { alarmOf, collectionParts, EVERY_SIGNIN, filesOn, laneAt, TIERS } from "./support";

export const MUSE: Origin = Object.freeze({ client: "a".repeat(64), family: "b".repeat(64) });
export const DOT: Origin = Object.freeze({ client: "c".repeat(64), family: "d".repeat(64) });
export const MCP: Source = Object.freeze({ kind: "mcp", ref: "conn-1" });
/** A callback an agent's client subscribes (MCP Events): a public HTTPS URL, a `whsec_` key. */
export const CALLBACK = "https://doorbell.test/tinyassets/events";
export const WHSEC = "whsec_" + btoa(String.fromCharCode(...Array.from({ length: 32 },
                                                                       (_, n) => n + 1)));
/** The MCP Events event the subscribing kind lists. */
export const EVENT = "tinyassets.changed";

/**
 * An MCP Events subscription as the MCP door hands it on, its blob a stand-in (the tests that
 * send seal a real one through the gateway): its id derived as the door derives it.
 */
export function subscription(commandCenter = "alpha",
                             extra: { board?: boolean; rooms?: string[]; blob?: string;
                                      url?: string; expires_at?: number | null;
                                      client?: string } = {}) {
  const args = { command_center_id: commandCenter, board: extra.board ?? false,
                 rooms: extra.rooms ?? [] };
  return {
    subscription_id: subscriptionId(extra.client ?? MUSE.client, extra.url ?? CALLBACK, EVENT,
                                    args),
    name: EVENT, arguments: args, blob: extra.blob ?? "v1.stand-in", kid: "k1", version: 1,
    expires_at: extra.expires_at ?? null,
  };
}

/** The same subscription, as the ledger's `subscribe` takes it. */
export function ledgerSubscription(commandCenter = "alpha", client = MUSE.client) {
  const door = subscription(commandCenter, { client });
  return { subscription_id: door.subscription_id, command_center_id: commandCenter,
           board: false, rooms: [], blob: door.blob, kid: door.kid, version: 1,
           expires_at: null };
}

/** The `n`th distinct outside identity, for tests that need many agents. */
export function origin(n: number): Origin {
  const hex = n.toString(16).padStart(8, "0");
  return { client: hex.repeat(8), family: "f".repeat(56) + hex };
}

type Row = Record<string, SqlStorageValue>;

/** A doorbell that answers from a script: an outcome, or `crash` to die mid-send. */
export class FakeSender implements WakeSender {
  /** Whether the kill switch is closed: `open()` answers its negation. */
  closed = false;
  readonly sent: WakeTarget[] = [];
  readonly answers: (Outcome | "crash")[] = [];

  async open(): Promise<boolean> {
    return !this.closed;
  }

  async send(target: WakeTarget, mark: () => Promise<void>): Promise<Outcome> {
    await mark();
    this.sent.push(target);
    const answer = this.answers.shift() ?? { state: "sent", status: 202 };
    if (answer === "crash") {
      throw new Error("the object died after the request left");
    }
    return answer;
  }
}

export interface Post {
  body?: string;
  cmid?: string;
  parent?: string | null;
}

/**
 * The ledger's parts on an object's own storage, a copy of the object's wiring (`OwnerDO`): its
 * grants, the owner's admission, the collections' change cursor, and its files' adoption of
 * a placed body and their one wall (`filesOn`'s, with no tier named). It goes when the tests
 * reach the ledger only by the object's doors (T1c).
 */
export type LedgerParts = [storage: DurableObjectStorage, grants: Grants,
                           admitted: () => boolean, changes: () => number, adopt: Adopt,
                           wall: (needed: number) => Refusal];
export function ledgerParts(storage: DurableObjectStorage): LedgerParts {
  const gate = new WriteGate(storage);
  const files = filesOn(gate.storage, gate.write);
  const collections = new Collections(...collectionParts(storage));
  return [storage, new Grants(storage, new Policy(storage)),
          () => isAdmitted(storage), () => collections.clock(),
          new Bodies(files, gate.storage, gate.write).adopt,
          (needed: number) => files.fullRefusal(needed)];
}

/**
 * The ledger on an object's own storage, built as the object builds it (`ledgerParts`). A
 * test that registers on it directly first admits the agent's outside client, as the
 * object's door admits every outside call before any act: an MCP client is recorded `full`.
 */
export function ledgerOn(storage: DurableObjectStorage): Ledger {
  const parts = ledgerParts(storage);
  const grants = parts[1];
  return new (class extends Ledger {
    override register(origin: Origin | null, source: unknown,
                      request: Parameters<Ledger["register"]>[2], now?: number) {
      if (origin !== null) {
        grants.admit(origin.client, (source as Source).kind, null, clock());
      }
      return super.register(origin, source, request, now);
    }
  })(...parts);
}

/** An outside agent of the test's own, its mail the ballast a ceiling is filled with. */
export const BALLAST: Origin = origin(0xba11);

/**
 * At production's ceilings, room for `unread` more unread messages and `hot` more hot ones:
 * the rest is a ballast agent's mail (`BALLAST`), its acked part first, so a test meets each
 * ceiling, and the archive lane's half of the hot one, where a small one would. The
 * ballast agent's id.
 */
export function ballast(ledger: Ledger, unread: number, hot: number): string {
  const { maxMessages, maxUnacked } = LEDGER_LIMITS;
  const agent = ledger.register(BALLAST, MCP, { command_center_id: "alpha",
                                                display_name: "Ballast",
                                                delivery_method: "pull", recipe_id: null })
    .agent_id;
  const room = `dm:${agent}`;
  const acked = maxMessages - hot - (maxUnacked - unread);
  let last = 0;
  for (let n = 0; n < acked; n++) {
    last = ledger.seqOf(ledger.post(null, room, `ballast-a${n}`, "x", null).message_id)!;
    if (n % 1_000 === 999 || n === acked - 1) {
      ledger.read(BALLAST, 1);
      ledger.ack(BALLAST, last);
    }
  }
  for (let n = 0; n < maxUnacked - unread; n++) {
    ledger.post(null, room, `ballast-u${n}`, "x", null);
  }
  return agent;
}

/** Now, in the whole seconds a binder reports a sign-in at. */
export function signedIn(): number {
  return Math.floor(clock());
}

/**
 * One owner's object, by that owner's own name, and the calls a binder would make on it.
 * Each test gets a fresh owner, so no test sees another's ledger (on `namespace`, the
 * production object's unless a test names one of its classes). The owner has signed in
 * once, so `session` carries a live protected-session cookie the object minted.
 */
export async function rig(namespace: DurableObjectNamespace<OwnerDO> = env.OWNER) {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub: DurableObjectStub<OwnerDO> = namespace.getByName(owner);
  /** A binder's binding; an outside origin comes through an MCP client unless named. */
  const bind = (origin: Origin | null, extra: Partial<Binding> = {}): Binding =>
    ({ owner, caller: owner, origin, ...(origin === null ? {} : { source: MCP }), tiers: TIERS,
       ...("sandbox" in extra ? {} : { signins: EVERY_SIGNIN }), ...extra });
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
      expected_revision: 0, name: "",
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
    /** The agent's own client subscribes (its blob a stand-in); the subscription's id. */
    subscribe: async (origin: Origin, commandCenter = "alpha",
                      extra: Parameters<typeof subscription>[1] = {}) => {
      const stored = await stub.wakeSubscribe(bind(origin), subscription(
        commandCenter, { client: origin.client, ...extra }));
      return stored.subscription_id;
    },
    /** A whole table, in insertion order. */
    table: (name: string): Promise<Row[]> => runInDurableObject(stub, (_i: OwnerDO, state) =>
      state.storage.sql.exec<Row>(`SELECT * FROM ${name} ORDER BY rowid`).toArray()),
    sql: (statement: string, ...params: SqlStorageValue[]): Promise<Row[]> =>
      runInDurableObject(stub, (_i: OwnerDO, state) =>
        state.storage.sql.exec<Row>(statement, ...params).toArray()),
    /** The ledger on this object's own storage. */
    inLedger: <R>(act: (ledger: Ledger) => R): Promise<R> =>
      runInDurableObject(stub, (_i: OwnerDO, state) => act(ledgerOn(state.storage))),
    /**
     * The object's storage let go, after a test that filled it to a ceiling: a filled object
     * is tens of MB on disk, kept until the test file ends (`inFilled`'s reason).
     */
    letGo: (): Promise<void> => runInDurableObject(stub, (_i: OwnerDO, state) =>
      state.storage.deleteAll()),
    /** One alarm pass at `now`, with the scripted doorbell. */
    tick: (now: number = clock()): Promise<number | typeof CLOSED> =>
      runInDurableObject(stub, (_i: OwnerDO, state) =>
        runWakes(ledgerOn(state.storage), sender, () => now,
                 new WriteGate(state.storage).write)),
    /** The object's physical alarm in seconds, as Lifecycle armed it, or null. */
    alarmAt: (): Promise<number | null> => alarmOf(stub),
    /** The `wakes` lane's Lifecycle job due time in seconds, or null when it has none. */
    wakesAt: (): Promise<number | null> => laneAt(stub, "wakes"),
    /** The nightly manifest's job (`backup.ts`): the alarm every owner's object keeps. */
    nightAt: (): Promise<number | null> => laneAt(stub, "manifest"),
  };
}

export type Rig = Awaited<ReturnType<typeof rig>>;

/** Every counter the ledger's bounds read, and the same numbers counted from the rows. */
export async function counters(r: Rig): Promise<[Row, Row]> {
  const [usage] = await r.sql(
    "SELECT agents, enabled_agents, messages, settled_wakes, unacked, acked_bytes, " +
      "(SELECT json_group_object(reader, unacked) FROM " +
      "(SELECT * FROM ledger_readers WHERE unacked > 0 ORDER BY reader)) AS mailboxes, " +
      "(SELECT json_group_object(agent_id, unacked_to_owner) " +
      "FROM (SELECT * FROM outside_agents WHERE unacked_to_owner > 0 ORDER BY agent_id)) " +
      "AS shares, " +
      "(SELECT json_group_object(message_id, unacked) FROM " +
      "(SELECT * FROM ledger_messages WHERE unacked > 0 ORDER BY message_id)) AS held " +
      "FROM ledger_usage",
  );
  const [actual] = await r.sql(
    "SELECT (SELECT COUNT(*) FROM outside_agents) + (SELECT COUNT(*) FROM native_agents) " +
      "AS agents, (SELECT COUNT(*) FROM outside_agents WHERE disabled_reason IS NULL) + " +
      "(SELECT COUNT(*) FROM native_agents WHERE retired_at IS NULL) AS enabled_agents, " +
      "(SELECT COUNT(*) FROM ledger_messages) AS messages, " +
      "(SELECT COUNT(*) FROM ledger_messages WHERE unacked > 0) AS unacked, " +
      "(SELECT coalesce(SUM(line_bytes), 0) FROM ledger_messages WHERE unacked = 0) " +
      "AS acked_bytes, " +
      "(SELECT COUNT(*) FROM ledger_wakes WHERE settled_at IS NOT NULL) AS settled_wakes, " +
      "(SELECT json_group_object(agent_id, n) FROM (SELECT r.agent_id, " +
      "COUNT(*) AS n FROM ledger_recipients r JOIN ledger_messages m " +
      "USING (message_id) WHERE r.unacked = 1 GROUP BY r.agent_id ORDER BY r.agent_id)) " +
      "AS mailboxes, " +
      "(SELECT json_group_object(sender_id, n) FROM (SELECT sender_id, " +
      "COUNT(*) AS n FROM ledger_messages WHERE sender_kind = 'outside' " +
      "AND message_id IN (SELECT message_id FROM ledger_recipients WHERE unacked = 1) " +
      "GROUP BY sender_id ORDER BY sender_id)) AS shares, " +
      "(SELECT json_group_object(message_id, n) FROM (SELECT message_id, COUNT(*) AS n " +
      "FROM ledger_recipients WHERE unacked = 1 GROUP BY message_id ORDER BY message_id)) " +
      "AS held",
  );
  return [usage!, actual!];
}
