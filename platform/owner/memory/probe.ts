/**
 * The workerd memory leg's probe (`leg.mjs`, ADR-037): the owner's chunk-wise paths, run by
 * the platform's own code, in an owner isolate (`Holder`, a Durable Object) against a chunk
 * store in another isolate (`Store`, as the files Worker), so each call crosses an RPC as in
 * production. `/gc` collects this isolate, so a sample read after it is the live set.
 *
 * Paths (each `GET /run?path=<path>&mib=<size>&n=<objects>`):
 * - `edit`: a file of `mib` MiB stored a chunk at a time, then one occurrence at a chunk's
 *   seam replaced by longer text (`edit.ts`), under one slot (`holds.ts`);
 * - `read`: a body of `mib` MiB stored, then one page's window read from its middle by
 *   verified ranges (`bodies.ts`' `windowReads`), under one slot;
 * - `n` of them at once, each in its own object of the one isolate.
 */
import { createHash } from "node:crypto";

import { DurableObject, WorkerEntrypoint } from "cloudflare:workers";

import { windowReads } from "../src/bodies";
import { planEdit } from "../src/edit";
import { ownerSlots } from "../src/holds";
import { CHUNK_BYTES } from "../src/manifest";
import { PAGE_BYTES } from "../src/pages";
import { slotted } from "../src/slots";

declare function gc(): void;

interface Env {
  readonly HOLDER: DurableObjectNamespace<Holder>;
  readonly STORE: Service<Store>;
}

const sha = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");

/** The chunk store, as the files Worker answers: whole chunks, or verified ranges. */
export class Store extends WorkerEntrypoint {
  async put(sha256: string, bytes: Uint8Array): Promise<void> {
    STORED.set(sha256, bytes);
  }

  async get(reads: { sha256: string; offset?: number; length?: number }[]):
      Promise<Uint8Array[]> {
    return reads.map((read) => {
      const bytes = STORED.get(read.sha256)!;
      if (sha(bytes) !== read.sha256) {
        throw new Error("a chunk's bytes no longer match its hash");
      }
      return read.offset === undefined ? bytes.slice()
        : bytes.slice(read.offset, read.offset + read.length!);
    });
  }

  async putStream(sha256: string, body: ReadableStream<Uint8Array>, size: number):
      Promise<void> {
    const bytes = new Uint8Array(size);
    let at = 0;
    const reader = body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      bytes.set(value, at);
      at += value.byteLength;
    }
    STORED.set(sha256, bytes);
  }

  async drop(): Promise<void> {
    STORED.clear();
  }

  collect(): boolean {
    gc();
    return true;
  }
}

const STORED = new Map<string, Uint8Array>();

/** A file of `bytes` bytes of `a`, `marker` at the end of its first chunk, put a chunk at a time. */
async function storeFile(store: Service<Store>, bytes: number, marker: string):
    Promise<{ size: number; chunks: { sha256: string; size: number }[] }> {
  const chunks: { sha256: string; size: number }[] = [];
  const mark = new TextEncoder().encode(marker);
  for (let at = 0; at < bytes; at += CHUNK_BYTES) {
    const piece = new Uint8Array(Math.min(CHUNK_BYTES, bytes - at)).fill(0x61);
    if (at === 0 && piece.byteLength === CHUNK_BYTES) {
      piece.set(mark.subarray(0, 3), CHUNK_BYTES - 3);  // across the first seam
    }
    if (at === CHUNK_BYTES) {
      piece.set(mark.subarray(3), 0);
    }
    const sha256 = sha(piece);
    chunks.push({ sha256, size: piece.byteLength });
    const sent = store.put(sha256, piece) as Promise<void> & { [Symbol.dispose]?: () => void };
    await sent;
    sent[Symbol.dispose]?.();
  }
  return { size: bytes, chunks };
}

export class Holder extends DurableObject<Env> {
  async edit(mib: number): Promise<number> {
    const file = await storeFile(this.env.STORE, mib * 1024 * 1024, "<<MARK>>");
    return slotted(ownerSlots(), 1, async () => {
      const plan = await planEdit({
        ...file, read: async (index) => (await this.env.STORE.get([
          { sha256: file.chunks[index]!.sha256 }]))[0]!,
      }, "<<MARK>>", "<<MARK, edited and longer>>", CHUNK_BYTES);
      if ("refused" in plan) {
        throw new Error(`refused: ${JSON.stringify(plan.refused)}`);
      }
      const kept = new Set(file.chunks.map((chunk) => chunk.sha256));
      for (const [index, sha256] of plan.hashes.entries()) {
        if (!kept.has(sha256)) {
          await this.env.STORE.put(sha256, await plan.make(index));
        }
      }
      return plan.size;
    });
  }

  async store(mib: number): Promise<number> {
    return (await storeFile(this.env.STORE, mib * 1024 * 1024, "<<MARK>>")).size;
  }

  /** Chunks made and dropped one at a time, with no RPC: the method's own control. */
  async alloc(mib: number): Promise<number> {
    let total = 0;
    for (let at = 0; at < mib * 1024 * 1024; at += CHUNK_BYTES) {
      const piece = new Uint8Array(CHUNK_BYTES).fill(0x61);
      total += sha(piece).length;
      await scheduler.wait(20);
    }
    return total;
  }

  /** The same chunks put as byte streams: what a call holds when its bytes are a stream. */
  async streamStore(mib: number): Promise<number> {
    const size = mib * 1024 * 1024;
    for (let at = 0; at < size; at += CHUNK_BYTES) {
      const piece = new Uint8Array(Math.min(CHUNK_BYTES, size - at)).fill(0x61);
      let sent = 0;
      const body = new ReadableStream<Uint8Array>({
        type: "bytes",
        pull(controller) {
          if (sent >= piece.byteLength) {
            controller.close();
            return;
          }
          controller.enqueue(piece.slice(sent, sent + 65536));
          sent += 65536;
        },
      });
      await this.env.STORE.putStream(sha(piece), body, piece.byteLength);
    }
    return size;
  }

  async read(mib: number): Promise<number> {
    const file = await storeFile(this.env.STORE, mib * 1024 * 1024, "<<MARK>>");
    return slotted(ownerSlots(), 1, async () => {
      const offset = Math.floor(file.size / 2);
      const parts = await this.env.STORE.get(windowReads(
        file.chunks.map((chunk) => chunk.sha256), file.size, offset, offset + PAGE_BYTES + 3));
      const bytes = new Uint8Array(parts.reduce((sum, part) => sum + part.byteLength, 0));
      let at = 0;
      for (const part of parts) {
        bytes.set(part, at);
        at += part.byteLength;
      }
      return new TextDecoder().decode(bytes).length;
    });
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/gc") {
      // A full collection, then a moment for the backing stores it let go to be swept (V8
      // frees an ArrayBuffer's backing store after the collection, off the main thread),
      // then another, so what is left is the live set.
      gc();
      await scheduler.wait(5);
      gc();
      await env.STORE.collect();
      return new Response("gc");
    }
    if (url.pathname === "/drop") {
      await env.STORE.drop();
      return new Response("dropped");
    }
    const path = url.searchParams.get("path")!;
    const mib = Number(url.searchParams.get("mib"));
    const n = Number(url.searchParams.get("n") ?? 1);
    const done = await Promise.all(Array.from({ length: n }, (_, index) => {
      const holder = env.HOLDER.get(env.HOLDER.idFromName(`${path}-${mib}-${index}`));
      return path === "edit" ? holder.edit(mib) : path === "store" ? holder.store(mib)
        : path === "streamstore" ? holder.streamStore(mib) : path === "alloc"
          ? holder.alloc(mib) : holder.read(mib);
    }));
    return Response.json(done);
  },
};
