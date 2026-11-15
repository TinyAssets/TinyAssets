/**
 * The archive's tests' owner and helpers, shared by `archive-*.test.ts`: a fresh owner at
 * production's ceilings, a few messages short of half the hot ceiling (`seeded`); acked mail
 * and a body at the cap to move; the lanes driven until the archive has nothing due; and
 * archived mail read back by the chain read and by the export's v3 rule.
 */
import { expect, onTestFinished } from "vitest";

import { sqlIn } from "../../ci/workerd";
import { BODY_BYTES_MAX } from "../src/bodies";
import { LEDGER_LIMITS, type Ledger } from "../src/ledger";
import type { Origin } from "../../contract/src/binding";
import { RESULT_CHARS } from "../../contract/src/encoded";
import { MoveTable, parseTrailer, recordRows } from "../src/segments";
import { ballast, type MUSE, type Rig, rig } from "./ledger-support";
import { drainDue } from "./support";

const { maxMessages, maxUnacked } = LEDGER_LIMITS;

/** A third outside agent, beside `MUSE` and `DOT`. */
export const EVE: Origin = Object.freeze({ client: "e".repeat(64), family: "f".repeat(64) });
export const MIB = 1024 * 1024;

export type Seeded = Rig & { readonly ballast: string };

/**
 * A fresh owner at production's ceilings, its ballast agent's
 * `maxUnacked - room` messages hot and unread (`ballast`): the test's own `room + 1`th hot
 * message takes the hot set past half, where the `archive` lane moves acked mail, and `room`
 * more may wait unread. Its storage is let go as the test ends (`letGo`). That is production
 * sizes, so a test that calls it is `large` (`ci/classes.ts`), whatever its time.
 */
export async function seeded(room = 4): Promise<Seeded> {
  const r = await rig();
  const agent = await r.inLedger((_ledger: Ledger, stores) =>
    ballast(stores, room, maxMessages - maxUnacked + room));
  onTestFinished(() => r.letGo());
  return Object.assign(r, { ballast: agent });
}

/**
 * The ballast agent deleted, its hot mail with it, and the lane settled: an export then pages
 * the test's own mail alone, not the ballast's thousands of rows.
 */
export async function unballast(r: Seeded): Promise<void> {
  await r.stub.deleteAgent(r.session(null), { agent_id: r.ballast });
  await settle(r);
}

/**
 * The owner's mail to `room`, read and acked by `reader` at once: acked, so it may move. Each
 * body is `prefix i`, padded to `bytes` when given (a body at the cap seals a segment alone).
 */
export async function sentAndAcked(r: Rig, reader: typeof MUSE, room: string, n: number,
                                   prefix = "m", bytes = 0): Promise<string[]> {
  const ids: string[] = [];
  for (let i = 0; i < n; i++) {
    const posted = await r.post(null, room, { body: `${prefix} ${i}`.padEnd(bytes, "x"),
                                              cmid: `${prefix}${i}` });
    ids.push(posted.message_id);
    const page = await r.read(reader);
    if (page.messages.length > 0) {
      await r.ack(reader, page.messages.at(-1)!.seq);
    }
  }
  return ids;
}

/** A body at the cap, acked by `reader`: past half the hot ceiling, it seals what is before it. */
export async function sealed(r: Rig, reader: typeof MUSE, room: string): Promise<string> {
  return (await sentAndAcked(r, reader, room, 1, "seal", BODY_BYTES_MAX))[0]!;
}

/** Whether the lane has work, as its schedule reads it (`Archive.next`), or a move in flight. */
async function busy(r: Rig): Promise<boolean> {
  const due = await r.inLedger((ledger: Ledger) => ledger.archiveDue()
    || ledger.purgedAgents().length > 0 || ledger.archiveCompactPending());
  return due || (await sqlIn(r.stub, "SELECT 1 FROM archive_moves")).length > 0;
}

/** Drive the object's lanes until the archive has nothing due. */
export async function settle(r: Rig): Promise<void> {
  for (let i = 0; i < 60; i++) {
    await drainDue(r.stub);
    if (!await busy(r)) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("the archive lane did not settle");
}

/** A message's seq, by its id. */
export function seqOf(r: Rig, id: string): Promise<number> {
  return r.inLedger((ledger: Ledger) => ledger.seqOf(id)!);
}

/** Any two segments whose key ranges overlap: none, ever (review H1). */
export async function overlaps(r: Rig): Promise<unknown[]> {
  return sqlIn(r.stub, "SELECT a.segment, b.segment FROM archive_segments a JOIN " +
               "archive_segments b ON a.segment < b.segment AND a.last_key >= b.first_key " +
               "AND a.first_key <= b.last_key");
}

/**
 * The owner's export, paged to its end, and its archived records read by v3's rule: each
 * `archive_segments` row's chunks from the export, each record's rows with its raw body
 * decoded back into its column. Its lines, and the archived records' rows by table.
 */
export async function exportArchived(r: Rig): Promise<{
    lines: Record<string, unknown>[];
    archived: Record<string, Record<string, unknown>>[] }> {
  const lines: Record<string, unknown>[] = [];
  let exportId: string | undefined;
  for (let next: string | null = null, waits = 0; ;) {
    const page = await r.stub.ownerExport(r.session(null), next === null ? { cursor: null }
      : { cursor: next, export_id: exportId! });
    exportId = page.export_id;
    if (page.pending) {
      if (++waits > 400) {
        throw new Error("the export's generation did not go on");
      }
      await drainDue(r.stub);
      next = page.next;
      continue;
    }
    lines.push(...page.lines.map((line: string) => JSON.parse(line)));
    if (page.next === null) {
      break;
    }
    next = page.next;
  }
  const chunkNames = new Set(lines.filter((line) => line["t"] === "chunk")
    .map((line) => line["sha256"]));
  const archived: Record<string, Record<string, unknown>>[] = [];
  for (const fence of lines.filter((line) => line["t"] === "row"
                                   && line["table"] === "archive_segments")) {
    const row = fence["row"] as Record<string, unknown>;
    const shas = (await sqlIn(r.stub, "SELECT sha256 FROM file_content_chunks WHERE " +
                                      "content_id = ? ORDER BY idx", row["content_id"] as string))
      .map((chunk) => chunk["sha256"] as string);
    const parts: Uint8Array[] = [];
    for (const sha of shas) {
      expect(chunkNames.has(sha)).toBe(true);
      const { chunks } = await r.stub.exportChunks(r.session(null), {
        export_id: exportId!, sha256s: [sha] });
      parts.push(Uint8Array.from(Buffer.from(chunks[0]!.data, "base64")));
    }
    const whole = Buffer.concat(parts);
    const at = row["trailer_offset"] as number;
    const trailer = parseTrailer(new TextDecoder().decode(whole.subarray(
      at, at + (row["trailer_bytes"] as number))));
    for (const [, offset, length] of trailer.keys) {
      archived.push(Object.fromEntries(recordRows(whole.subarray(offset, offset + length))));
    }
  }
  return { lines, archived };
}

/**
 * A batch's sources as a move streams them, its records the table's in order (a tail's
 * batch), its bodies inline (no placed body read).
 */
export const hotSources = (ledger: Ledger, table: MoveTable) =>
  async (from: number, to: number) => {
    const keys = Array.from({ length: to - from }, (_, k) => table.key(from + k));
    const parts = ledger.archiveParts(keys);
    return keys.map((key) => {
      const own = parts.get(key)!;
      return [own.head, own.inline!, own.tail];
    });
  };
/** A batch planned into a table of its own, no body placed. */
export const batchOf = (ledger: Ledger, seqs: readonly number[]) => {
  const table = new MoveTable();
  const batch = ledger.archiveBatch(seqs, table, () => {
    throw new Error("no placed body in this batch");
  });
  return batch === null ? null : { ...batch, table };
};
export const noRead = async (): Promise<Uint8Array> => {
  throw new Error("no stored content in this batch");
};

/** Every archived message's body by its id, as the export's reading rule reads it. */
export async function archivedBodies(r: Rig): Promise<Map<string, string>> {
  const { archived } = await exportArchived(r);
  return new Map(archived.map((rows) => [rows["ledger_messages"]!["message_id"] as string,
                                         rows["message_bodies"]!["body"] as string]));
}

/**
 * A chain read, paged by the last message's seq until `id` shows or it ends: its body read
 * whole (a page cuts its first body to fit one answer; the rest reads on by `body_offset`,
 * naming the message), or null. Every page held to one answer.
 */
export async function chained(r: Rig, id: string): Promise<string | null> {
  const read = async (request: Record<string, unknown>) => {
    const page = await r.stub.mailChain(r.bind(null), { message_id: id, limit: 10,
                                                        ...request } as never);
    expect(JSON.stringify(page).length).toBeLessThanOrEqual(RESULT_CHARS);
    return page;
  };
  let after = 0;
  for (let n = 0; n < 40; n++) {
    const page = await read({ after });
    const found = page.messages.find((m) => m.message_id === id) as
      ({ seq: number; message_id: string; body: string | null;
         body_next_offset?: number | null }) | undefined;
    if (found !== undefined) {
      if (found.body === null) {
        return null;
      }
      let text = found.body;
      for (let next = found.body_next_offset ?? null; next !== null;) {
        const on = (await read({ after: found.seq - 1, limit: 1, body_offset: next,
                                 body_message_id: found.message_id })).messages[0] as
          { body: string; body_next_offset?: number | null };
        text += on.body;
        next = on.body_next_offset ?? null;
      }
      return text;
    }
    const last = page.messages.at(-1);
    if (!page.more || last === undefined) {
      return null;
    }
    after = last.seq;
  }
  return null;
}
