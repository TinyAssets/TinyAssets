/**
 * The owner's holds of large data (`holds.ts`, ADR-037): a hold takes the isolate's units
 * only while its bytes are in hand, weighted by what it holds, so a co-tenant's small read
 * never waits behind edits (review M1); and a ranged read is verified against its whole
 * chunk end to end, a tampered chunk failing loudly (review L3).
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { chunkName, unwrap } from "../../files/src/datakey";
import { chunkKey } from "../../files/src/keys";
import { planEdit, type Source } from "../src/edit";
import {
  EDIT_MAKE_UNITS, OWNER_POOL_UNITS, OWNER_RESERVE_UNITS, ownerSlots,
} from "../src/holds";
import { owner } from "./generation-support";
import { MUSE, rig } from "./ledger-support";
import { failure, inFiles, sqlIn } from "./support";

const MIB = 1024 * 1024;

describe("the owner's holds", () => {
  it("never keep a co-tenant's small read waiting behind edits", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    await r.post(null, `dm:${muse}`, { body: "x".repeat(3000) });  // placed: past 2,048 bytes
    const pool = ownerSlots();
    // Edits' making steps hold every unit but the reserve, and another waits for room.
    const steps = Math.floor((OWNER_POOL_UNITS - OWNER_RESERVE_UNITS) / EDIT_MAKE_UNITS);
    const held = await Promise.all(Array.from({ length: steps }, () =>
      pool.acquire(EDIT_MAKE_UNITS)));
    const rest = OWNER_POOL_UNITS - OWNER_RESERVE_UNITS - steps * EDIT_MAKE_UNITS;
    if (rest > 0) {
      held.push(await pool.acquire(rest));
    }
    const waiting = pool.acquire(EDIT_MAKE_UNITS);
    try {
      const started = Date.now();
      expect((await r.read(MUSE)).messages[0]!.body).toBe("x".repeat(3000));
      expect(Date.now() - started).toBeLessThan(2_000);
      expect(pool.state().waiting).toBe(1);
    } finally {
      for (const slot of held) {
        slot.release();
      }
      (await waiting).release();
    }
  });

  it("takes an edit's units a step at a time, never across the edit", async () => {
    const o = await owner();
    const path = "/workspace/steps.md";
    await o.stub.fileWrite(o.bind(), { path, expected_etag: null,
                                       content: "<<MARK>>" + "b".repeat(4 * MIB - 8) });
    for (const fill of ["c", "d"]) {
      await o.stub.fileAppend(o.bind(), { path, content: fill.repeat(4 * MIB) });
    }
    const etag = (await o.stub.fileStat(o.bind(), { path })).entry!.etag;
    const pool = ownerSlots();
    pool.resetPeak();
    expect(await o.stub.fileEdit(o.bind(), { path, old_text: "<<MARK>>",
                                             new_text: "<<MARK, longer>>", expected_etag: etag }))
      .toHaveProperty("entry");
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
    expect(pool.state().peak).toBeLessThanOrEqual(EDIT_MAKE_UNITS);
  }, 120_000);

  it("reads each source chunk of an edit inside a step of its own", async () => {
    const chunk = 16;
    const bytes = new TextEncoder().encode("ab".repeat(40) + "MARK" + "cd".repeat(40));
    const chunks: { size: number; bytes: Uint8Array; sha256: string }[] = [];
    for (let at = 0; at < bytes.byteLength; at += chunk) {
      const piece = bytes.slice(at, at + chunk);
      const digest = await crypto.subtle.digest("SHA-256", piece);
      chunks.push({ size: piece.byteLength, bytes: piece,
                    sha256: [...new Uint8Array(digest)]
                      .map((b) => b.toString(16).padStart(2, "0")).join("") });
    }
    let depth = 0;
    let steps = 0;
    const source: Source = {
      size: bytes.byteLength, chunks,
      read: async (index) => {
        expect(depth).toBe(1);
        return chunks[index]!.bytes.slice();
      },
    };
    const plan = await planEdit(source, "MARK", "DONE, longer", chunk, async (work) => {
      depth += 1;
      steps += 1;
      try {
        return await work();
      } finally {
        depth -= 1;
      }
    });
    expect(plan).toHaveProperty("hashes");
    // The scan reads every chunk once, the hashes every chunk from the change on once.
    const changed = Math.floor(80 / chunk);
    expect(steps).toBe(chunks.length + (chunks.length - changed));
  });

  it("fails a ranged read loudly when its chunk's bytes are changed in R2", async () => {
    const o = await owner();
    const path = "/workspace/tampered.md";
    const content = "0123456789".repeat(1000);
    await o.stub.fileWrite(o.bind(), { path, expected_etag: null, content });
    expect((await o.stub.fileText(o.bind(), { path, offset: 5000, max_bytes: 10 })).text)
      .toBe("0123456789");
    const [row] = await sqlIn(o.stub, "SELECT c.sha256 FROM file_entries e JOIN " +
      "file_content_chunks c ON c.content_id = e.content_id WHERE e.name = ?", "tampered.md");
    const space = await inFiles(o.stub, {}, (files) => files.space());
    const name = await chunkName(await unwrap(env.FILES_TEST_RING, space.ns, space.key),
                                 row!["sha256"] as string);
    await env.BLOBS.put(chunkKey(space.ns, name), new TextEncoder().encode(
      content.replace("0123456789", "0123456780")));
    expect((await failure(() => o.stub.fileText(o.bind(), { path, offset: 5000,
                                                              max_bytes: 10 }))).message)
      .toMatch(/a chunk's bytes no longer match its hash/);
  });
});
