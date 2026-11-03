/**
 * The exports open (`owner_exports`, slice P): each named by the id its first page gave,
 * ending `EXPORT_LIFE_S` after it (under `GC_GRACE_S`, so every chunk its pages name is
 * still in the bucket), and pinned to the owner's change clock as it stood then: a later
 * page, once the clock moved, is refused (`EXPORT_CHANGED`). An export holds nothing: no
 * chunk waits for it. At most `EXPORTS_OPEN` are open; a new one past them ends the oldest.
 */
import { randomBytes } from "node:crypto";

import { Invalid, Refused } from "./errors";
import { EXPORT_CHANGED, EXPORT_LIFE_S, EXPORTS_OPEN } from "./export";
import type { OwnerStorage } from "./gate";

const EXPORT_ID = /^[0-9a-f]{32}$/;

/** An open export: its id, when it ends (s), and the change clock it is pinned to. */
export interface OpenExport {
  readonly export_id: string;
  readonly ends_at: number;
  readonly clock: number;
}

function exportId(id: unknown): string {
  if (typeof id !== "string" || !EXPORT_ID.test(id)) {
    throw new Invalid("an export is named by the id its first page gave");
  }
  return id;
}

export class OpenExports {
  readonly #storage: OwnerStorage;

  constructor(storage: OwnerStorage) {
    this.#storage = storage;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /** In a quiet unit: a new export opened at `now` (s), pinned to `clock`. */
  open(now: number, clock: number): OpenExport {
    this.#sql.exec("DELETE FROM owner_exports WHERE ends_at <= ?", now);
    this.#sql.exec("DELETE FROM owner_exports WHERE export_id IN (SELECT export_id FROM " +
                     "owner_exports ORDER BY opened_at DESC LIMIT -1 OFFSET ?)", EXPORTS_OPEN - 1);
    const id = randomBytes(16).toString("hex");
    const ends = now + EXPORT_LIFE_S;
    this.#sql.exec("INSERT INTO owner_exports (export_id, opened_at, ends_at, clock) " +
                     "VALUES (?, ?, ?, ?)", id, now, ends, clock);
    return { export_id: id, ends_at: ends, clock };
  }

  /** The export `id` open at `now`, refused unless it is. A read. */
  named(id: unknown, now: number): OpenExport {
    const row = this.#sql.exec<{ ends_at: number; clock: number }>(
      "SELECT ends_at, clock FROM owner_exports WHERE export_id = ? AND ends_at > ?",
      exportId(id), now).toArray()[0];
    if (row === undefined) {
      throw new Invalid("this export is not open: begin it again");
    }
    return { export_id: id as string, ends_at: row.ends_at, clock: row.clock };
  }

  /**
   * The export `id` as it stands open at `now` for its next page, refused once the owner's
   * change clock is past the one it is pinned to (`EXPORT_CHANGED`). A read.
   */
  standing(id: unknown, now: number, clock: number): OpenExport {
    const open = this.named(id, now);
    if (open.clock !== clock) {
      throw new Refused(EXPORT_CHANGED);
    }
    return open;
  }

  /** In a quiet unit: the export ended, its id no longer naming it. */
  end(id: unknown): boolean {
    return this.#sql.exec("DELETE FROM owner_exports WHERE export_id = ? RETURNING export_id",
                          exportId(id)).toArray().length > 0;
  }
}
