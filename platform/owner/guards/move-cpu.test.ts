/**
 * The CPU of one archive move at the production seal (`LEDGER_LIMITS`; rs_cap §2.2): the
 * tail's batch of acked mail up to a segment's seal, `SEGMENT_MIN_BYTES` of records (always
 * reached before `SEGMENT_MIN_RECORDS` of them, held here too), through the move's pure parts as `owner-do.ts`'s
 * `#archiveMove` runs them: each message's parts (`mailParts`) and its inline body's hash
 * checked, its table (`MoveTable`), its pieces (`pack`) planned (`planSegment`), and every
 * chunk made (`makeChunk`, its sources' parts made again as `archiveParts` makes them)
 * and hashed in the first pass and made again in the second. Held within a tenth of the
 * owner Worker's CPU limit an invocation (`cpu.ts`). Its SQL reads, its uploads and its
 * units are not here: memory is measured in workerd (`limits-audit.md`), and a live move's
 * CPU is not measured.
 */
import { createHash } from "node:crypto";

import { describe, expect, it } from "vitest";

import { LEDGER_LIMITS, type MailParts, mailParts } from "../src/ledger";
import {
  makeChunk, MoveTable, pack, planSegment, SEGMENT_LEAST_BYTES, type Source,
} from "../src/segments";
import { CPU_LIMIT_MS, cpuMs } from "./cpu";

const hex = (n: number, width: number) => n.toString(16).padStart(width, "0");

/**
 * A hot message's three rows, acked, its body `body`, each its narrowest as
 * `archive-mail.test.ts` builds the least mail (a record of the fewest bytes is the most
 * records a seal by bytes holds), but each its own id and chain.
 */
function rows(seq: number, body: string) {
  const id = `msg_${hex(seq, 32)}`;
  const bytes = new TextEncoder().encode(body).byteLength;
  return {
    message: { seq, message_id: id, command_center_id: "", room_id: "dm:x",
               sender_kind: "owner", sender_id: "owner", client_message_id: "c",
               body_sha256: createHash("sha256").update(body).digest("hex"), parent_id: null,
               chain_root: id, chain_depth: 0, created_at: 1, body_bytes: bytes, unacked: 0,
               authority: null, attended: 0, line_bytes: 1 },
    body: { message_id: id, room_id: "dm:x", body, created_at: 1, body_bytes: bytes,
            content_id: null, filer: "owner", meta_bytes: 0 },
    recipients: [{ message_id: id, agent_id: "owner", seq, state: "read", changed_at: 1,
                   unacked: 0 }],
  };
}

/** A record's bytes in its segment: its message's parts and its body. */
function recordOf(own: ReturnType<typeof rows>): number {
  const parts = mailParts(own.message, own.body, own.recipients);
  return parts.head.byteLength + parts.inline!.byteLength + parts.tail.byteLength;
}

describe("an archive move at the production seal", () => {
  const { archiveBytes, archiveRecords } = LEDGER_LIMITS;

  it("is always by its bytes: the least mail's records reach them before its record count",
     () => {
    expect(recordOf(rows(1, "x")) * archiveRecords).toBeGreaterThan(archiveBytes);
  });

  for (const [shape, body] of [["the least mail", "x"],
                               ["mail of 600-byte bodies", "m".repeat(600)]] as const) {
    it(`keeps one move of ${shape} inside a tenth of the CPU limit`, async () => {
      const held = new Map<number, ReturnType<typeof rows>>();
      for (let seq = 1, bytes = 0; held.size < archiveRecords && bytes < archiveBytes; seq++) {
        const own = rows(seq, body);
        held.set(seq, own);
        bytes += recordOf(own);
      }
      const seqs = [...held.keys()];
      /** `archiveParts`: the hot messages' parts, made again from their rows. */
      const partsOf = (keys: readonly number[]) => new Map(keys.map((seq): [number, MailParts] => {
        const own = held.get(seq)!;
        return [seq, mailParts(own.message, own.body, own.recipients)];
      }));
      let chunks = 0;
      let size = 0;
      const ms = await cpuMs(async () => {
        // The batch (`archiveBatch`): each message's parts, its inline body checked, its row.
        const table = new MoveTable();
        for (const [seq, parts] of partsOf(seqs)) {
          const own = held.get(seq)!;
          if (createHash("sha256").update(parts.inline!).digest("hex")
              !== own.message.body_sha256) {
            throw new Error("a body is not its message's");
          }
          table.push(seq, own.message.chain_root, own.message.room_id, own.message.created_at,
                     parts.head.byteLength + own.message.body_bytes + parts.tail.byteLength,
                     MoveTable.INLINE, -1, 0);
        }
        // Its pieces planned, then each chunk made and hashed, and made again to send.
        const all = Array.from({ length: table.count }, (_, n) => n);
        const plans = pack(all, SEGMENT_LEAST_BYTES, (n) => table.length(n)).map((part) => {
          const piece = table.piece(Uint32Array.from(part));
          return { plan: planSegment("mail", piece), at: piece.indices };
        });
        const read = async (): Promise<Uint8Array> => {
          throw new Error("no stored content in a tail's batch of inline bodies");
        };
        for (const pass of [1, 2]) {
          for (const { plan, at } of plans) {
            const sources = async (from: number, to: number) => {
              const keys = Array.from(at.subarray(from, to), (n) => table.key(n));
              const parts = partsOf(keys);
              return keys.map((key): Source[] => {
                const own = parts.get(key)!;
                return [own.head, own.inline!, own.tail];
              });
            };
            for (let n = 0; n < plan.chunks; n++) {
              const made = await makeChunk(plan, n, sources, read);
              if (pass === 1) {
                createHash("sha256").update(made).digest("hex");
                chunks++;
                size += made.byteLength;
              }
            }
          }
        }
      });
      console.log(`one move of ${shape}: ${seqs.length} records of ` +
                  `${recordOf(held.get(1)!)} bytes, ${chunks} chunks, ${size} ` +
                  `bytes, ${Math.round(ms)} ms CPU of ${CPU_LIMIT_MS}`);
      expect(seqs.length).toBeLessThan(archiveRecords);
      expect(size).toBeGreaterThanOrEqual(archiveBytes);
      expect(ms).toBeLessThan(CPU_LIMIT_MS / 10);
    }, 120_000);
  }
});
