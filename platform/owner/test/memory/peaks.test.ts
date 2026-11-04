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
import { BASELINE_BYTES, GUARD_BYTES, isBusy, memoryHeld } from "../../src/memory";
import { MUSE, rig } from "../ledger-support";
import { ownerObject } from "../support";

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

async function measured(path: string, grant: number, act: () => Promise<unknown>):
    Promise<void> {
  const mark = await marker();
  await new Promise((resolve) => setTimeout(resolve, 100));  // a sample at rest first
  mark(JSON.stringify({ mark: "begin", path, grant }));
  for (let n = 0; n < 6; n++) {
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
