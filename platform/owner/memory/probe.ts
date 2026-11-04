/**
 * The workerd memory leg's probe (`leg.mjs`, ADR-037): the owner's chunk-wise paths, run by
 * the platform's own code, in an owner isolate (`Holder`, a Durable Object) against a chunk
 * store in another isolate (`Store`, as the files Worker), so each chunk crosses an RPC as
 * in production.
 *
 * **Checkpoints.** At each point a path holds what it holds (a chunk read, a chunk made, a
 * chunk put, a window read), it awaits the leg's driver (`#checkpoint`), which collects this
 * isolate (`/gc`, a swept full collection) and reads its heap and backing stores while the
 * path is suspended, then lets it go on: so a sample is the path's live set at that point,
 * with no worker code between the collection and the reading (rpcretain_research.md §4: a
 * free-running sampler reads garbage not yet swept).
 *
 * Paths (each `GET /run?path=<path>&mib=<size>&n=<objects>`):
 * - `edit`: a file of `mib` MiB stored a chunk at a time, then one occurrence across its
 *   first seam replaced by longer text (`edit.ts`, every later chunk rewritten), under one
 *   slot (`holds.ts`);
 * - `read`: a body of `mib` MiB stored, then one page's window read from its middle by
 *   verified ranges (`bodies.ts`' `windowReads`), under one slot;
 * - `hoard`: the control: a chunk made and kept at each checkpoint, so its live set grows
 *   with its size; a leg that cannot see it grow proves nothing;
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
  readonly DRIVER: Fetcher;
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

  async drop(): Promise<void> {
    STORED.clear();
  }
}

const STORED = new Map<string, Uint8Array>();

export class Holder extends DurableObject<Env> {
  /** The path suspended while the driver collects this isolate and reads it. */
  async #checkpoint(): Promise<void> {
    await (await this.env.DRIVER.fetch("http://driver/checkpoint")).text();
  }

  /** A file of `bytes` bytes of `a`, a marker across its first seam, put a chunk at a time. */
  async #store(bytes: number): Promise<{ size: number;
                                         chunks: { sha256: string; size: number }[] }> {
    const chunks: { sha256: string; size: number }[] = [];
    const mark = new TextEncoder().encode("<<MARK>>");
    for (let at = 0; at < bytes; at += CHUNK_BYTES) {
      const piece = new Uint8Array(Math.min(CHUNK_BYTES, bytes - at)).fill(0x61);
      if (at === 0 && piece.byteLength === CHUNK_BYTES) {
        piece.set(mark.subarray(0, 3), CHUNK_BYTES - 3);
      }
      if (at === CHUNK_BYTES) {
        piece.set(mark.subarray(3), 0);
      }
      const sha256 = sha(piece);
      chunks.push({ sha256, size: piece.byteLength });
      await this.env.STORE.put(sha256, piece);
      await this.#checkpoint();
    }
    return { size: bytes, chunks };
  }

  async edit(mib: number): Promise<number> {
    const file = await this.#store(mib * 1024 * 1024);
    return slotted(ownerSlots(), 1, async () => {
      const plan = await planEdit({
        ...file, read: async (index) => {
          const read = (await this.env.STORE.get([{ sha256: file.chunks[index]!.sha256 }]))[0]!;
          await this.#checkpoint();
          return read;
        },
      }, "<<MARK>>", "<<MARK, edited and longer>>", CHUNK_BYTES);
      if ("refused" in plan) {
        throw new Error(`refused: ${JSON.stringify(plan.refused)}`);
      }
      const kept = new Set(file.chunks.map((chunk) => chunk.sha256));
      for (const [index, sha256] of plan.hashes.entries()) {
        if (!kept.has(sha256)) {
          const made = await plan.make(index);
          await this.#checkpoint();
          await this.env.STORE.put(sha256, made);
          await this.#checkpoint();
        }
      }
      return plan.size;
    });
  }

  async read(mib: number): Promise<number> {
    const file = await this.#store(mib * 1024 * 1024);
    return slotted(ownerSlots(), 1, async () => {
      const offset = Math.floor(file.size / 2);
      const parts = await this.env.STORE.get(windowReads(
        file.chunks.map((chunk) => chunk.sha256), file.size, offset, offset + PAGE_BYTES + 3));
      await this.#checkpoint();
      return parts.reduce((sum, part) => sum + part.byteLength, 0);
    });
  }

  /** The control: every chunk made is kept, so its live set grows with its size. */
  async hoard(mib: number): Promise<number> {
    const kept: Uint8Array[] = [];
    for (let at = 0; at < mib * 1024 * 1024; at += CHUNK_BYTES) {
      kept.push(new Uint8Array(CHUNK_BYTES).fill(0x61));
      await this.#checkpoint();
    }
    return kept.length;
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/gc") {
      // A full collection, then a moment for the backing stores it let go to be swept off
      // the main thread, then another, so what is left is the live set.
      gc();
      await scheduler.wait(5);
      gc();
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
      const holder = env.HOLDER.get(env.HOLDER.idFromName(`${path}-${mib}-${n}-${index}`));
      return path === "edit" ? holder.edit(mib) : path === "read" ? holder.read(mib)
        : holder.hoard(mib);
    }));
    return Response.json(done);
  },
};
