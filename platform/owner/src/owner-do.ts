/**
 * One owner's trusted state, in that owner's own SQLite-backed Durable Object (ADR-032).
 *
 * Only an edge Worker (the binder) turns a verified credential into an owner and a
 * caller, and it reaches this object by `getByName(owner)`. Every RPC method takes that
 * {@link Binding} as its first, typed argument; nothing in a request body names the
 * owner or the caller. The object stores its owner on first use and refuses any other,
 * so a binder bug that reaches the wrong object fails loudly instead of acting there.
 *
 * This class has no binding to its own namespace (the `wrangler.jsonc` guard), so its
 * code cannot reach another owner's object, and the module keeps no mutable state at
 * top level (the module-state guard), since objects of one class can share an isolate.
 */
import { DurableObject } from "cloudflare:workers";

import { Collections, Conflict, type HistoryEntry, type Page, type Row } from "./collections";
import { Invalid, Refused } from "./errors";
import { migrate } from "./schema";
import { principal } from "./text";

/** Whose object this is, and who is asking: set by the binder from a verified credential. */
export interface Binding {
  readonly owner: string;
  readonly caller: string;
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

/** The request's fields are exactly `required`, plus any of `optional`. */
function fields(request: unknown, required: readonly string[],
                optional: readonly string[] = []): Record<string, unknown> {
  if (typeof request !== "object" || request === null || Array.isArray(request)) {
    throw new Invalid("invalid collection request");
  }
  const keys = Object.keys(request);
  if (!required.every((key) => keys.includes(key))
      || !keys.every((key) => required.includes(key) || optional.includes(key))) {
    throw new Invalid("invalid collection request");
  }
  return request as Record<string, unknown>;
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

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    super(ctx, env);
    this.#collections = new Collections(ctx.storage);
    void ctx.blockConcurrencyWhile(async () => {
      migrate(ctx.storage);
    });
  }

  /** The owner this object serves: stored on first use, and every other one refused. */
  #bind(binding: Binding): { owner: string; caller: string } {
    if (typeof binding !== "object" || binding === null) {
      throw new Invalid("invalid owner principal");
    }
    const owner = principal(binding.owner);
    const caller = principal(binding.caller);
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
