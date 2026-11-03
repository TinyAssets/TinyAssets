/**
 * The exports open (`owner_exports`, slice P): each one holds every chunk from deletion until
 * it ends or its hold lapses (`EXPORT_HOLD_S`), so every hash its manifest names can be
 * fetched by the owner (`export.ts`). At most `EXPORTS_OPEN` are open; a new one past them
 * ends the oldest.
 */
import { randomBytes } from "node:crypto";

import { Invalid } from "./errors";
import { EXPORT_HOLD_S, EXPORTS_OPEN } from "./export";
import type { OwnerStorage } from "./gate";

const EXPORT_ID = /^[0-9a-f]{32}$/;

export class ExportHolds {
  readonly #storage: OwnerStorage;

  constructor(storage: OwnerStorage) {
    this.#storage = storage;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /** In a unit: a new export opened, holding chunks from `now` (s); its id and hold's end. */
  open(now: number): { export_id: string; held_until: number } {
    this.#sql.exec("DELETE FROM owner_exports WHERE held_until <= ?", now);
    this.#sql.exec("DELETE FROM owner_exports WHERE export_id IN (SELECT export_id FROM " +
                     "owner_exports ORDER BY opened_at DESC LIMIT -1 OFFSET ?)", EXPORTS_OPEN - 1);
    const id = randomBytes(16).toString("hex");
    const until = now + EXPORT_HOLD_S;
    this.#sql.exec("INSERT INTO owner_exports (export_id, opened_at, held_until) VALUES (?, ?, ?)",
                   id, now, until);
    return { export_id: id, held_until: until };
  }

  /** The export `id` as it stands open at `now`, refused unless it is. A read. */
  standing(id: unknown, now: number): { export_id: string; held_until: number } {
    if (typeof id !== "string" || !EXPORT_ID.test(id)) {
      throw new Invalid("an export is named by the id its first page gave");
    }
    const row = this.#sql.exec<{ held_until: number }>(
      "SELECT held_until FROM owner_exports WHERE export_id = ? AND held_until > ?", id, now)
      .toArray()[0];
    if (row === undefined) {
      throw new Invalid("this export is not open: begin it again");
    }
    return { export_id: id, held_until: row.held_until };
  }

  /** In a unit: the export ended, its hold let go. */
  end(id: unknown): boolean {
    if (typeof id !== "string" || !EXPORT_ID.test(id)) {
      throw new Invalid("an export is named by the id its first page gave");
    }
    return this.#sql.exec("DELETE FROM owner_exports WHERE export_id = ? RETURNING export_id",
                          id).toArray().length > 0;
  }

  /** Until when (s) an open export holds the chunks, or null for none open. A read. */
  heldUntil(now: number): number | null {
    return this.#sql.exec<{ until: number | null }>(
      "SELECT MAX(held_until) AS until FROM owner_exports WHERE held_until > ?", now).one().until;
  }
}
