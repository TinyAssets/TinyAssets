/**
 * One owner's trusted state, in that owner's own SQLite-backed Durable Object (ADR-032).
 *
 * Only an edge Worker (the binder) turns a verified credential into an owner and a
 * caller, and it reaches this object by `getByName(owner)`. Every RPC method takes that
 * {@link Binding} as its first, typed argument; nothing in a request body names the
 * owner or the caller. The object serves only the owner it is named by (`ctx.id.name`),
 * refuses when it has no name, and also stores that owner on first use and refuses any
 * other, so a binder bug that reaches the wrong object fails loudly instead of acting
 * there.
 *
 * Its code cannot reach another owner's object: the Worker binds nothing, the runtime
 * hands it no `ctx.exports` (`disable_ctx_exports`), and its source never names
 * `exports` or imports `env` (the config and self-reach guards). The module keeps no
 * mutable state at top level (the module-state guard), since objects of one class can
 * share an isolate.
 */
import { DurableObject } from "cloudflare:workers";

import { Collections, Conflict, type HistoryEntry, type Page, type Row } from "./collections";
import { Invalid, Refused } from "./errors";
import {
  type Agent, type Claim, clock, Ledger, type MailPage, type Origin, outcome, outsideOrigin,
  type Posted, type Source, type Wake,
} from "./ledger";
import { migrate } from "./schema";
import { principal } from "./text";
import { runWakes, type WakeSender, WAKES_PER_ALARM } from "./wakes";

/** Whose object this is, and who is asking: set by the binder from a verified credential. */
export interface Binding {
  readonly owner: string;
  readonly caller: string;
}

/**
 * A ledger call's binding. The caller is the owner's own account; `origin` is the
 * verified outside client it acts through (an outside agent), or null for the owner.
 * The ledger takes every sender and reader from here, never from a request field.
 */
export interface LedgerBinding extends Binding {
  readonly origin: Origin | null;
  /** How the binder verified `origin`: needed to register an agent. */
  readonly source?: Source;
  /** The binder checked the owner's protected interactive session on this request. */
  readonly interactive?: boolean;
}

export type { Origin, Source, WakeSender };

export interface RegisterRequest {
  readonly command_center_id: string;
  readonly display_name: string;
  readonly delivery_method: string;
  readonly recipe_id: string | null;
  readonly max_wakes_per_hour?: number;
  readonly batch_window_s?: number;
}

export interface DisableRequest {
  readonly agent_id: string;
  readonly reason: string;
}

export interface PostRequest {
  readonly room_id: string;
  readonly client_message_id: string;
  readonly body: string;
  readonly parent_id: string | null;
}

export interface ReadRequest {
  readonly limit: number;
  readonly max_bytes?: number;
  readonly pad_bytes?: number;
}

export interface AckRequest {
  readonly through: number;
}

export interface SubscriberRequest {
  readonly command_center_id: string;
  readonly agent_id: string;
}

export interface SettleRequest {
  readonly wake_id: string;
  readonly claim_id: string;
  readonly outcome: unknown;
}

/** The object's environment: no bindings, and never one to its own namespace. */
export type OwnerEnv = Record<never, never>;

export interface WriteRequest {
  readonly collection: string;
  readonly row_id: string;
  readonly body: string;
  readonly expected_etag: string;
  readonly private: boolean;
  readonly history_op?: string;
}

export interface PullRequest {
  readonly collection: string;
  readonly after: number;
  readonly limit: number;
}

export interface KeyRequest {
  readonly collection: string;
  readonly row_id: string;
}

export interface DeleteRequest {
  readonly collection: string;
  readonly row_id: string;
  readonly expected_etag: string;
}

export interface ClearRequest {
  readonly collection: string;
  readonly writer: string | null;
}

/** A lost compare-and-swap is an answer carrying the row as it stands, not a refusal. */
export interface Conflicted {
  readonly conflict: Row | null;
}

/**
 * The request's fields are exactly `required`, plus any of `optional`, and none is
 * `undefined`: a field sent is a value, as the Python door refused `None`.
 */
function fields(request: unknown, required: readonly string[],
                optional: readonly string[] = [],
                refusal = "invalid collection request"): Record<string, unknown> {
  if (typeof request !== "object" || request === null || Array.isArray(request)) {
    throw new Invalid(refusal);
  }
  const entries = Object.entries(request);
  const keys = entries.map(([key]) => key);
  if (!required.every((key) => keys.includes(key))
      || !keys.every((key) => required.includes(key) || optional.includes(key))
      || entries.some(([, value]) => value === undefined)) {
    throw new Invalid(refusal);
  }
  return request as Record<string, unknown>;
}

/** A ledger request's fields: exactly these, never a sender, reader or owner. */
function ledgerFields(request: unknown, required: readonly string[],
                      optional: readonly string[] = []): Record<string, unknown> {
  return fields(request, required, optional, "invalid ledger request");
}

function conflicted<T>(act: () => T): T | Conflicted {
  try {
    return act();
  } catch (error) {
    if (error instanceof Conflict) {
      return { conflict: error.current };
    }
    throw error;
  }
}

export class OwnerDO extends DurableObject<OwnerEnv> {
  readonly #collections: Collections;
  readonly #ledger: Ledger;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    super(ctx, env);
    this.#collections = new Collections(ctx.storage);
    this.#ledger = new Ledger(ctx.storage);
    void ctx.blockConcurrencyWhile(async () => {
      migrate(ctx.storage);
    });
  }

  /**
   * What rings outside agents' doorbells. None until the doorbell slice supplies one;
   * until then the alarm recovers lapsed claims and open wakes stay pending.
   */
  protected wakeSender(): WakeSender | null {
    return null;
  }

  /**
   * The object's one alarm: due wakes, lapsed claims, then rearm at the next due time.
   * There is no global tick; each owner's object schedules only itself.
   */
  override async alarm(): Promise<void> {
    const sender = this.wakeSender();
    await runWakes(this.#ledger, sender, clock, WAKES_PER_ALARM);
    await this.#rearm(sender !== null);
  }

  /** Point the one alarm at the earliest live wake, or clear it when there is none. */
  async #rearm(dueOpen = true): Promise<void> {
    const next = this.#ledger.nextAlarm(clock(), dueOpen);
    if (next === null) {
      await this.ctx.storage.deleteAlarm();
    } else {
      await this.ctx.storage.setAlarm(Math.ceil(next * 1000));
    }
  }

  /**
   * A ledger call's owner, reader and origin. The ledger is its owner's own: the caller
   * must be the owner's account, acting itself (`origin` null) or through a verified
   * outside client.
   */
  #bindLedger(binding: LedgerBinding): { origin: Origin | null; interactive: boolean } {
    const { owner, caller } = this.#bind(binding);
    if (caller !== owner) {
      throw new Refused("the ledger answers its owner's own accounts only");
    }
    if (!("origin" in binding)) {
      throw new Invalid("invalid outside origin");
    }
    const interactive = binding.interactive ?? false;
    if (typeof interactive !== "boolean") {
      throw new Invalid("interactive is a boolean");
    }
    return { origin: outsideOrigin(binding.origin), interactive };
  }

  /** Registering or disabling an agent needs the owner's protected session (S30). */
  #interactive(binding: LedgerBinding): Origin | null {
    const { origin, interactive } = this.#bindLedger(binding);
    if (!interactive) {
      throw new Refused("interactive owner session required");
    }
    return origin;
  }

  /** Wakes are the owner's own doorbell's to claim and settle, never an outside agent's. */
  #doorbell(binding: LedgerBinding): void {
    if (this.#bindLedger(binding).origin !== null) {
      throw new Refused("only the owner's doorbell claims and settles wakes");
    }
  }

  // -- OUTSIDE_AGENTS ---------------------------------------------------------

  outsideAgents(binding: LedgerBinding): { agents: Agent[] } {
    this.#bindLedger(binding);
    return { agents: this.#ledger.agents() };
  }

  async registerAgent(binding: LedgerBinding, request: RegisterRequest): Promise<Agent> {
    const origin = this.#interactive(binding);
    const r = ledgerFields(request, ["command_center_id", "display_name", "delivery_method",
                                     "recipe_id"], ["max_wakes_per_hour", "batch_window_s"]);
    const agent = this.#ledger.register(origin, binding.source, r as unknown as RegisterRequest);
    await this.#rearm();
    return agent;
  }

  async disableAgent(binding: LedgerBinding, request: DisableRequest): Promise<Agent> {
    this.#interactive(binding);
    const r = ledgerFields(request, ["agent_id", "reason"]);
    const agent = this.#ledger.disable(r["agent_id"], r["reason"]);
    await this.#rearm();
    return agent;
  }

  // -- ROOM_POST and MAILBOX_RECEIPT -------------------------------------------

  async roomPost(binding: LedgerBinding, request: PostRequest): Promise<Posted> {
    const { origin } = this.#bindLedger(binding);
    const r = ledgerFields(request, ["room_id", "client_message_id", "body", "parent_id"]);
    const posted = this.#ledger.post(origin, r["room_id"], r["client_message_id"], r["body"],
                                     r["parent_id"]);
    await this.#rearm();
    return posted;
  }

  mailboxRead(binding: LedgerBinding, request: ReadRequest): MailPage {
    const { origin } = this.#bindLedger(binding);
    const r = ledgerFields(request, ["limit"], ["max_bytes", "pad_bytes"]);
    return this.#ledger.read(origin, r["limit"], r["max_bytes"] ?? null, r["pad_bytes"] ?? 0);
  }

  mailboxAck(binding: LedgerBinding, request: AckRequest): { reader: string; through: number } {
    const { origin } = this.#bindLedger(binding);
    const r = ledgerFields(request, ["through"]);
    return this.#ledger.ack(origin, r["through"]);
  }

  /** W4: a `mailbox.message` subscribe must name the caller's own ringing agent. */
  mailboxSubscriber(binding: LedgerBinding, request: SubscriberRequest): { agent_id: string } {
    const { origin } = this.#bindLedger(binding);
    const r = ledgerFields(request, ["command_center_id", "agent_id"]);
    this.#ledger.subscriber(origin, r["command_center_id"], r["agent_id"]);
    return { agent_id: r["agent_id"] as string };
  }

  // -- wakes ------------------------------------------------------------------

  dueWakes(binding: LedgerBinding, request: { limit: number }): { wakes: Wake[] } {
    this.#doorbell(binding);
    const r = ledgerFields(request, ["limit"]);
    const limit = r["limit"];
    if (typeof limit !== "number" || !Number.isSafeInteger(limit) || limit < 1
        || limit > WAKES_PER_ALARM) {
      throw new Invalid(`limit is 1 to ${WAKES_PER_ALARM}`);
    }
    return { wakes: this.#ledger.dueWakes(clock(), limit) };
  }

  /**
   * Claim one due wake under a lease. A claim over RPC is marked may-have-sent at once:
   * the object cannot see when the caller's first byte leaves, so a lapsed one is
   * `unknown` and never replayed.
   */
  async claimWake(binding: LedgerBinding, request: { wake_id: string }): Promise<Claim> {
    this.#doorbell(binding);
    const r = ledgerFields(request, ["wake_id"]);
    const claim = this.#ledger.claim(r["wake_id"], clock(), true);
    await this.#rearm();
    return claim;
  }

  /** Settle a claimed wake from the doorbell's answer; only the claim's holder can. */
  async settleWake(binding: LedgerBinding, request: SettleRequest): Promise<{ settled: boolean }> {
    this.#doorbell(binding);
    const r = ledgerFields(request, ["wake_id", "claim_id", "outcome"]);
    const settled = this.#ledger.settle(r["wake_id"], r["claim_id"], outcome(r["outcome"]),
                                        clock());
    await this.#rearm();
    return { settled };
  }

  /**
   * The owner this object serves: the name it was reached by, and every other one
   * refused. The stored owner, fixed at first use, is a second check of the same.
   */
  #bind(binding: Binding): { owner: string; caller: string } {
    if (typeof binding !== "object" || binding === null) {
      throw new Invalid("invalid owner principal");
    }
    const owner = principal(binding.owner);
    const caller = principal(binding.caller);
    const named = this.ctx.id.name;
    if (named === undefined) {
      throw new Refused("this object is not named by an owner");
    }
    if (named !== owner) {
      throw new Refused("this object belongs to another owner");
    }
    const sql = this.ctx.storage.sql;
    const stored = sql.exec<{ owner: string }>("SELECT owner FROM object_owner").toArray()[0];
    if (stored === undefined) {
      sql.exec("INSERT INTO object_owner (only, owner) VALUES (1, ?)", owner);
    } else if (stored.owner !== owner) {
      throw new Refused("this object belongs to another owner");
    }
    return { owner, caller };
  }

  #ownerOnly(owner: string, caller: string, action: string): void {
    if (caller !== owner) {
      throw new Refused(`only the owner may ${action} collection rows`);
    }
  }

  collectionWrite(binding: Binding, request: WriteRequest): { row: Row } | Conflicted {
    const { owner, caller } = this.#bind(binding);
    const r = fields(request, ["collection", "row_id", "body", "expected_etag", "private"],
                     ["history_op"]);
    return conflicted(() => ({
      row: this.#collections.write(owner, caller, r["collection"] as string,
                                   r["row_id"] as string, r["body"], r["expected_etag"],
                                   r["private"], "history_op" in r ? r["history_op"] : "write"),
    }));
  }

  collectionPull(binding: Binding, request: PullRequest): Page {
    const { caller } = this.#bind(binding);
    const r = fields(request, ["collection", "after", "limit"]);
    return this.#collections.pull(caller, r["collection"] as string, r["after"], r["limit"]);
  }

  collectionGet(binding: Binding, request: KeyRequest): { row: Row | null } {
    const { caller } = this.#bind(binding);
    const r = fields(request, ["collection", "row_id"]);
    return this.#collections.get(caller, r["collection"] as string, r["row_id"] as string);
  }

  collectionHistory(binding: Binding, request: KeyRequest): { entries: HistoryEntry[] } {
    const { caller } = this.#bind(binding);
    const r = fields(request, ["collection", "row_id"]);
    return this.#collections.history(caller, r["collection"] as string, r["row_id"] as string);
  }

  collectionDelete(binding: Binding, request: DeleteRequest): { deleted: number } | Conflicted {
    const { owner, caller } = this.#bind(binding);
    this.#ownerOnly(owner, caller, "delete");
    const r = fields(request, ["collection", "row_id", "expected_etag"]);
    return conflicted(() => this.#collections.delete(
      caller, r["collection"] as string, r["row_id"] as string, r["expected_etag"],
    ));
  }

  collectionClear(binding: Binding, request: ClearRequest): { deleted: number } {
    const { owner, caller } = this.#bind(binding);
    this.#ownerOnly(owner, caller, "clear");
    const r = fields(request, ["collection", "writer"]);
    return this.#collections.clear(caller, r["collection"] as string, r["writer"]);
  }
}
