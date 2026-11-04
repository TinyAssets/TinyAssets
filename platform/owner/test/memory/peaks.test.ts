/**
 * Each path at its worst, marked for the workerd memory leg (`test/memory-peaks.mjs`), which
 * samples this isolate's live heap and fails a path whose peak passes the grant it declares.
 * Each path runs six times between its marks, after its inputs are made, so what the
 * test itself holds is in the path's starting heap. Run only by that leg (`--project
 * memory`): it waits for the leg's binding, and fails without it.
 */
import { describe, expect, it } from "vitest";

import { API_BODY_MAX } from "../../../doors/src/config";
import { jsonObject } from "../../../doors/src/body";
import { requestGrant } from "../../../doors/src/answers";
import { BODY_BYTES_MAX, READ_GRANT, writeGrant } from "../../src/bodies";
import { ANSWER_GRANT, REQUEST_FACTOR } from "../../../doors/src/answers";
import { boundedBytes } from "../../../doors/src/body";
import { INBOUND_BODY_MAX } from "../../../doors/src/config";
import { decodedChunks, hintsOf, SYNC_BODY_MAX } from "../../../doors/src/egress";
import { GET_GRANT, PUT_GRANT } from "../../../files/src/keys";
import { GENERATION_GRANT } from "../../src/backup";
import { EGRESS_BODY_MAX } from "../../src/network";
import {
  CHUNKS_READ_GRANT, EXPORT_CHUNKS_GRANT, FILE_APPEND_GRANT, FILE_WRITE_GRANT,
} from "../../src/owner-do";
import { BASELINE_BYTES, GUARD_BYTES, isBusy, memoryHeld } from "../../src/memory";
import { owner } from "../generation-support";
import { MUSE, rig } from "../ledger-support";
import { drainDue, ownerObject, sqlIn } from "../support";

const MIB = 1024 * 1024;
const alpha = String.fromCharCode(0x3b1);

/** A request whose body streams as the network sends it, made as it is read. */
function streamed(head: string, piece: string, bytes: number, tail: string): Request {
  const encoder = new TextEncoder();
  const [h, p, t] = [encoder.encode(head), encoder.encode(piece), encoder.encode(tail)];
  const pieces = Math.floor((bytes - h.byteLength - t.byteLength) / p.byteLength);
  let sent = -1;
  return new Request("https://doors/in", { method: "POST", body: new ReadableStream({
    pull(controller) {
      controller.enqueue(sent < 0 ? h : sent < pieces ? p : t);
      if (++sent > pieces) {
        controller.close();
      }
    },
  }), duplex: "half" } as RequestInit);
}

type Mark = (payload: string) => void;

async function marker(): Promise<Mark> {
  for (let n = 0; n < 600; n++) {
    const mark = (globalThis as { __mark?: Mark }).__mark;
    if (mark !== undefined) {
      return mark;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("the memory leg's inspector never attached: run `npm run test:memory`");
}

async function measured(path: string, grant: number, act: () => Promise<unknown>,
                        isolate: "runner" | "files" = "runner", times = 6): Promise<void> {
  const mark = await marker();
  await new Promise((resolve) => setTimeout(resolve, 100));  // a sample at rest first
  mark(JSON.stringify({ mark: "begin", path, grant, isolate }));
  for (let n = 0; n < times; n++) {
    await act();
  }
  await new Promise((resolve) => setTimeout(resolve, 50));
  mark(JSON.stringify({ mark: "end", path }));
}

async function retried<T>(act: () => Promise<T>): Promise<T> {
  for (;;) {
    try {
      return await act();
    } catch (error) {
      if (!isBusy(error) && !(error instanceof Error && error.message.startsWith("busy:"))) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
}

describe("each path's live peak, in workerd", () => {
  it("the isolate at rest: the owner's Worker loaded and one object used", async () => {
    const { stub, as, name } = ownerObject();
    await stub.collectionGet(as(name), { collection: "board", row_id: "none" });
    const mark = await marker();
    await new Promise((resolve) => setTimeout(resolve, 300));
    mark(JSON.stringify({ mark: "rest", baseline: BASELINE_BYTES }));
    await new Promise((resolve) => setTimeout(resolve, 300));
  });

  it("a read's page of an 8 MiB control-character body", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    await r.post(null, `dm:${muse}`, { body: String.fromCharCode(1).repeat(BODY_BYTES_MAX) });
    await measured("read page", READ_GRANT, async () => {
      expect((await r.read(MUSE)).messages[0]).toMatchObject({ truncated: true });
    });
  }, 120_000);

  it("a placed body's write of 8 MiB, two bytes a character", async () => {
    const { stub, as, name } = ownerObject();
    const body = String.fromCharCode(0x3b1).repeat(BODY_BYTES_MAX / 2);
    let n = 0;
    await measured("placed write", writeGrant(BODY_BYTES_MAX), async () => {
      await stub.collectionWrite(as(name), { collection: "board", row_id: `r${n++}`, body,
                                             expected_etag: "", private: false });
    });
  }, 120_000);

  it("a tool call's request body at the door's ceiling, read and parsed", async () => {
    // The body arrives as the network streams it, 32 KiB at a time, made as it is read, so
    // nothing of it is held but by the door: two-byte characters, the worst text a byte.
    const piece = new TextEncoder().encode(String.fromCharCode(0x3b1).repeat(16 * 1024));
    const head = new TextEncoder().encode("{\"collection\":\"board\",\"row_id\":\"x\",\"body\":\"");
    const pieces = Math.floor((API_BODY_MAX - head.byteLength - 2) / piece.byteLength);
    const streamed = () => {
      let sent = -1;
      return new Request("https://doors/api", { method: "POST", body: new ReadableStream({
        pull(controller) {
          controller.enqueue(sent < 0 ? head : sent < pieces ? piece
            : new TextEncoder().encode("\"}"));
          if (++sent > pieces) {
            controller.close();
          }
        },
      }), duplex: "half" } as RequestInit);
    };
    await measured("request body", requestGrant(streamed(), API_BODY_MAX), async () => {
      const args = await jsonObject(streamed(), API_BODY_MAX);
      // The call then sends them: they are held across its await.
      await new Promise((resolve) => setTimeout(resolve, 30));
      expect(Object.keys(args)).toHaveLength(3);
    });
  }, 120_000);

  it("an owner's file writes, appends and chunk reads, in the object and the files Worker",
     async () => {
    const o = await owner();
    const path = "/workspace/big.md";
    const text = alpha.repeat(2 * MIB);  // 4 MiB of two-byte characters
    let etag = "";
    let puts = 0;
    await measured("file write", FILE_WRITE_GRANT, async () => {
      const wrote = await o.stub.fileWrite(o.bind(), { path, expected_etag: null,
                                                       content: text }) as { entry: { etag: string } };
      etag = wrote.entry.etag;
    }, "runner", 3);
    await measured("files put", PUT_GRANT, async () => {
      // A chunk the bucket does not hold yet: one character of it changed each time.
      await o.stub.fileWrite(o.bind(), { path: `/workspace/put-${++puts}.md`, expected_etag: null,
                                         content: `${puts % 10}${text.slice(1)}` });
    }, "files", 3);
    // A file one byte short of a chunk, then a chunk's text after it: the worst append.
    const short = "x".repeat(4 * MIB - 1);
    await measured("file append", FILE_APPEND_GRANT, async () => {
      await o.stub.fileWrite(o.bind(), { path: "/workspace/log.md", expected_etag: null,
                                         content: short });
      await o.stub.fileAppend(o.bind(), { path: "/workspace/log.md", content: text });
    }, "runner", 2);
    void etag;
    const [{ version }] = await sqlIn(o.stub, "SELECT version FROM file_entries WHERE " +
                                              "name = 'log.md'") as [{ version: number }];
    const read = () => o.stub.fileReadChunks(o.bind(), { path: "/workspace/log.md", version,
                                                          first: 0, count: 2 });
    expect((await read()).chunks).toHaveLength(2);
    await measured("chunks read", CHUNKS_READ_GRANT, read);
    await measured("files get", GET_GRANT, read, "files");
  }, 300_000);

  it("a generation's pass over a base past a burst, and an export's chunks", async () => {
    const o = await owner();
    // About 9 MiB of exported rows (notices of 1,000 characters), past one burst's gather.
    await sqlIn(o.stub, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n " +
      "WHERE i < 9000) INSERT INTO owner_notices (at, type, detail) SELECT 0, 'x', " +
      "printf('%.1000c', 'a') FROM n");
    await o.stub.fileWrite(o.bind(), { path: "/workspace/a.bin", expected_etag: null,
                                       content: new Uint8Array(4 * MIB).fill(1) });
    await o.stub.fileWrite(o.bind(), { path: "/workspace/b.bin", expected_etag: null,
                                       content: new Uint8Array(4 * MIB).fill(2) });
    const begun = await o.stub.ownerExport(o.session(), { cursor: null });
    await measured("generation pass", GENERATION_GRANT, async () => {
      for (let n = 0; n < 400; n++) {
        const [row] = await sqlIn(o.stub, "SELECT state FROM owner_backups ORDER BY " +
                                          "started_at DESC LIMIT 1") as [{ state: string }];
        if (row?.state === "done") {
          return;
        }
        await drainDue(o.stub);
      }
      throw new Error("the generation did not finish");
    }, "runner", 1);
    const shas = (await sqlIn(o.stub, "SELECT sha256 FROM file_chunks ORDER BY sha256") as
      { sha256: string }[]).map((row) => row.sha256);
    expect(shas.length).toBeGreaterThanOrEqual(2);
    await measured("export chunks", EXPORT_CHUNKS_GRANT, async () => {
      await o.stub.exportChunks(o.session(), { export_id: begun.export_id,
                                               sha256s: shas.slice(0, 2) });
    });
  }, 300_000);

  it("the doors' egress and inbound bodies at their ceilings", async () => {
    // An inbound event: its bytes held as the call sends them.
    await measured("inbound body", REQUEST_FACTOR * INBOUND_BODY_MAX + ANSWER_GRANT, async () => {
      const bytes = await boundedBytes(streamed("", "e".repeat(32 * 1024), INBOUND_BODY_MAX, ""),
                                       INBOUND_BODY_MAX);
      await new Promise((resolve) => setTimeout(resolve, 30));
      expect(bytes!.byteLength).toBeGreaterThan(INBOUND_BODY_MAX - 64 * 1024);
    }, "runner", 3);
    // A model call's body: read, parsed for its hints, and held until the gateway answers.
    await measured("model call body", REQUEST_FACTOR * EGRESS_BODY_MAX + ANSWER_GRANT,
                   async () => {
      const request = streamed("{\"model\":\"m\",\"messages\":\"", alpha.repeat(16 * 1024),
                               EGRESS_BODY_MAX, "\"}");
      const text = await request.text();
      expect(hintsOf(text).model).toBe("m");
      await new Promise((resolve) => setTimeout(resolve, 30));
      expect(text.length).toBeGreaterThan(0);
    }, "runner", 3);
    // A sync upload: its chunks' base64 streamed in, parsed and decoded, held as the call
    // sends them.
    await measured("sync upload body", REQUEST_FACTOR * SYNC_BODY_MAX + ANSWER_GRANT,
                   async () => {
      const request = streamed('{"intent_id":"i","chunks":[{"sha256":"a","bytes":"',
                               "BwcH".repeat(8 * 1024), SYNC_BODY_MAX, '"}]}');
      const parsed = JSON.parse(await request.text()) as { chunks: unknown };
      const decoded = decodedChunks(parsed.chunks) as { bytes: Uint8Array }[];
      await new Promise((resolve) => setTimeout(resolve, 30));
      expect(decoded[0]!.bytes.byteLength).toBeGreaterThan(8 * MIB);
    }, "runner", 3);
    expect(MIB).toBe(1024 * 1024);
  }, 300_000);

  it("several objects in one isolate writing and reading at once", async () => {
    const owners = Array.from({ length: 4 }, () => ownerObject());
    const bodies = owners.map((_, n) => String.fromCharCode(0x61 + n).repeat(BODY_BYTES_MAX));
    let round = 0;
    // The guard's budget, and what it cannot see: each call's argument, deserialized before
    // its method runs (`memory.ts`), at most one body for each of the eight calls in flight.
    await measured("objects at once",
                   GUARD_BYTES - memoryHeld().reserved + 8 * BODY_BYTES_MAX, async () => {
      round += 1;
      await Promise.all(owners.flatMap(({ stub, as, name }, n) => [
        retried((): Promise<unknown> => stub.collectionWrite(as(name), {
          collection: "board", row_id: `r${round}`,
          body: bodies[n]!, expected_etag: "", private: false })),
        retried(() => stub.collectionPull(as(name), { collection: "board", after: 0, limit: 10 })),
      ]));
    });
    expect(memoryHeld().held).toBe(0);
  }, 300_000);
});
