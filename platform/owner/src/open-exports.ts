/**
 * The exports open (`owner_exports`, slice P): each named by the id its first page gave,
 * reading one generation (`backup.ts`) and pinning it from deletion for `EXPORT_LIFE_S`
 * (under `GC_GRACE_S`, so every chunk it names is still in the bucket). At most
 * `EXPORTS_OPEN` are open; a new one past them ends the oldest.
 */
import { randomBytes } from "node:crypto";

import { Invalid } from "./errors";
import { EXPORT_LIFE_S, EXPORTS_OPEN } from "./export";
import type { OwnerStorage } from "./gate";

const EXPORT_ID = /^[0-9a-f]{32}$/;

/** An open export: its id, when (s) it ends, and the generation it reads. */
export interface OpenExport {
  readonly export_id: string;
  readonly ends_at: number;
  readonly gen: string;
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

  /** In a quiet unit: a new export of generation `gen` opened at `now` (s). */
  open(now: number, gen: string): OpenExport {
    this.#sql.exec("DELETE FROM owner_exports WHERE ends_at <= ?", now);
    this.#sql.exec("DELETE FROM owner_exports WHERE export_id IN (SELECT export_id FROM " +
                     "owner_exports ORDER BY opened_at DESC LIMIT -1 OFFSET ?)", EXPORTS_OPEN - 1);
    const id = randomBytes(16).toString("hex");
    const ends = now + EXPORT_LIFE_S;
    this.#sql.exec("INSERT INTO owner_exports (export_id, opened_at, ends_at, gen) VALUES " +
                     "(?, ?, ?, ?)", id, now, ends, gen);
    return { export_id: id, ends_at: ends, gen };
  }

  /** The export `id` open at `now`, refused unless it is. A read. */
  named(id: unknown, now: number): OpenExport {
    const row = this.#sql.exec<{ ends_at: number; gen: string }>(
      "SELECT ends_at, gen FROM owner_exports WHERE export_id = ? AND ends_at > ?",
      exportId(id), now).toArray()[0];
    if (row === undefined) {
      throw new Invalid("this export is not open: begin it again");
    }
    return { export_id: id as string, ends_at: row.ends_at, gen: row.gen };
  }

  /** In a quiet unit: the export ended, its id no longer naming it. */
  end(id: unknown): boolean {
    return this.#sql.exec("DELETE FROM owner_exports WHERE export_id = ? RETURNING export_id",
                          exportId(id)).toArray().length > 0;
  }
}
