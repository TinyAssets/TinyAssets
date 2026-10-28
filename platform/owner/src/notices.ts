/**
 * The owner's notices: what the agent's runtime reports that no call fails with, kept in
 * the owner's own object for the owner to read (`OwnerDO.noticeList`).
 *
 * pi reports a task its restart limit parked, a task that faulted, a hook or section that
 * threw, the error that failed its Session, and the like through `onReport` (pi-durable
 * README, "Failures and Recovery"); the agent (`agent.ts`) turns each report, and each
 * extension it skipped while opening pi, into one notice. A notice is the report's type and
 * a short detail: its reason, the extension and the task kind, and the error's message cut
 * to `NOTICE_DETAIL` characters. Only the newest `NOTICES_KEPT` stay, so a report that
 * repeats every request can never grow the owner's storage. Each is added in one unit of
 * the object's write gate.
 */
import type { OwnerStorage } from "./gate";

/** The newest notices kept; an older one goes as a new one comes. */
export const NOTICES_KEPT = 100;
/** The characters a notice's detail keeps. */
export const NOTICE_DETAIL = 1024;

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
               type.slice(0, 64), [...detail].slice(0, NOTICE_DETAIL).join(""));
      sql.exec("DELETE FROM owner_notices WHERE seq <= " +
                 "(SELECT seq FROM owner_notices ORDER BY seq DESC LIMIT 1 OFFSET ?)",
               NOTICES_KEPT);
    });
  }

  /**
   * Keep a notice of a standing condition (an extension pi refuses, met at every open)
   * once: none is added while one of the same type and detail is kept, so it never pushes
   * other notices out.
   */
  once(type: string, detail: string, at: number): void {
    const kept = this.#storage.sql.exec(
      "SELECT 1 FROM owner_notices WHERE type = ? AND detail = ? LIMIT 1",
      type.slice(0, 64), [...detail].slice(0, NOTICE_DETAIL).join("")).toArray().length > 0;
    if (!kept) {
      this.add(type, detail, at);
    }
  }

  /** Every kept notice, newest first. */
  list(): Notice[] {
    return this.#storage.sql.exec<{ seq: number; at: number; type: string; detail: string }>(
      "SELECT seq, at, type, detail FROM owner_notices ORDER BY seq DESC").toArray();
  }
}
