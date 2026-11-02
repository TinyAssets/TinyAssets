/**
 * The owner's notices: what the owner's work reports that no call fails with, kept in the
 * owner's own object for the owner to read (`OwnerDO.noticeList`): today a connection's
 * setup check that failed, or a provider revoke that failed or is unsupported
 * (`connections.ts`); the runs slice (R) adds a run's failures (a sign-in expired, a
 * vendor's rate limit or billing error, a sync refused). A notice is a type and a short
 * detail cut to `NOTICE_DETAIL` characters. Only the newest `NOTICES_KEPT` stay, so a
 * report that repeats can never grow the owner's storage. Each is added in one unit of the
 * object's write gate.
 */
import type { OwnerStorage } from "./gate";

/** The newest notices kept; an older one goes as a new one comes. */
export const NOTICES_KEPT = 100;
/** The characters a notice's detail keeps. */
export const NOTICE_DETAIL = 1024;

/** A notice's type and detail as kept: each cut to what its column holds. */
function kept(type: string, detail: string): [string, string] {
  return [type.slice(0, 64), [...detail].slice(0, NOTICE_DETAIL).join("")];
}

export interface Notice {
  readonly seq: number;
  readonly at: number;
  readonly type: string;
  readonly detail: string;
}

export class Notices {
  readonly #storage: OwnerStorage;

  constructor(storage: OwnerStorage) {
    this.#storage = storage;
  }

  /** Keep one notice, and drop what falls past the newest `NOTICES_KEPT`. */
  add(type: string, detail: string, at: number): void {
    const sql = this.#storage.sql;
    this.#storage.transactionSync(() => {
      sql.exec("INSERT INTO owner_notices (at, type, detail) VALUES (?, ?, ?)", at,
               ...kept(type, detail));
      sql.exec("DELETE FROM owner_notices WHERE seq <= " +
                 "(SELECT seq FROM owner_notices ORDER BY seq DESC LIMIT 1 OFFSET ?)",
               NOTICES_KEPT);
    });
  }

  /** Every kept notice, newest first. */
  list(): Notice[] {
    return this.#storage.sql.exec<{ seq: number; at: number; type: string; detail: string }>(
      "SELECT seq, at, type, detail FROM owner_notices ORDER BY seq DESC").toArray();
  }
}
