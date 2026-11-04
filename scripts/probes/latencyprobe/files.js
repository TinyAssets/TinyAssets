/**
 * ta-latencyprobe-files: the files Worker's stand-in for the latency probe
 * (openspec/changes/clean-sheet-platform/latency-probe.md). Throwaway.
 *
 * The owner object reaches R2 only through the files Worker (`FILES`, a service binding), so
 * option B is timed over that hop: these RPC methods are the bucket calls, nothing more.
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import { traceColo } from "./trace.js";

export default class Files extends WorkerEntrypoint {
  async put(key, bytes, create) {
    const done = await this.env.BUCKET.put(key, bytes, create
      ? { onlyIf: new Headers({ "if-none-match": "*" }) } : undefined);
    return done !== null;
  }

  async get(key) {
    const object = await this.env.BUCKET.get(key);
    if (object === null) {
      throw new Error(`no object ${key}`);
    }
    return (await object.arrayBuffer()).byteLength;
  }

  async list(prefix, limit) {
    return (await this.env.BUCKET.list({ prefix, limit })).objects.length;
  }

  where() {
    return traceColo();
  }

  fetch() {
    return new Response("not found", { status: 404 });
  }
}
