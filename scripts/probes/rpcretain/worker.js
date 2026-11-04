// The RPC retention probe (rpcretain-probe.yml): does production's 128 MB isolate limit count
// garbage V8 has not collected yet? Throwaway, workers.dev only: the workflow deploys it, runs
// the matrix and deletes it, Durable Object namespace and all. No TinyAssets resource is bound.
//
// GET /burst?form=<form>&mib=<n>, with the per-run token in `x-probe-token`, runs one Durable
// Object call that makes n/4 chunks of 4 MiB and, by form:
//   alloc     - makes and drops each chunk, no RPC;
//   rpcArr    - sends each as a Uint8Array argument to SELF's Store (which drops it);
//   rpcStream - sends each as a type:"bytes" stream to SELF's Store;
//   retain    - keeps every chunk in an array: a real O(size) hold, the control, which must
//               hit exceededMemory near 128 MB to prove the probe can see one.
// It answers {form, mib, objectId, ok, held?, ms?, error?}. A call the runtime resets answers
// ok:false with the runtime's message, and still HTTP 200: the object's outcome is in the
// Durable Object datasets in GraphQL, matched by objectId.
import { DurableObject, WorkerEntrypoint } from "cloudflare:workers";

const CHUNK = 4 * 1024 * 1024;
const FORMS = Object.freeze(["alloc", "rpcArr", "rpcStream", "retain"]);
const MAX_MIB = 2048;

export class Store extends WorkerEntrypoint {
  async put(bytes) { return bytes.byteLength; }
  async putStream(body) {
    const reader = body.getReader();
    let n = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return n;
      n += value.byteLength;
    }
  }
}

const byteStream = (piece) => {
  let sent = 0;
  return new ReadableStream({
    type: "bytes",
    pull(controller) {
      if (sent >= piece.byteLength) { controller.close(); return; }
      controller.enqueue(piece.slice(sent, sent + 65536));
      sent += 65536;
    },
  });
};

export class Prober extends DurableObject {
  async burst(form, mib) {
    const kept = [];
    const t0 = Date.now();
    for (let i = 0; i < (mib * 1024 * 1024) / CHUNK; i++) {
      const piece = new Uint8Array(CHUNK).fill(0x61 + (i % 20));
      if (form === "alloc") await Promise.resolve();
      else if (form === "rpcArr") await this.env.SELF.put(piece);
      else if (form === "rpcStream") await this.env.SELF.putStream(byteStream(piece));
      else if (form === "retain") { kept.push(piece); await Promise.resolve(); }
      else throw new Error(`no form ${form}`);
    }
    return { held: kept.length, ms: Date.now() - t0 };
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!env.TOKEN || request.headers.get("x-probe-token") !== env.TOKEN) {
      return new Response("no", { status: 403 });
    }
    if (url.pathname !== "/burst") return new Response("not found", { status: 404 });
    const form = url.searchParams.get("form");
    const mib = Number(url.searchParams.get("mib"));
    if (!FORMS.includes(form) || !(Number.isInteger(mib) && mib > 0 && mib <= MAX_MIB && mib % 4 === 0)) {
      return new Response("bad request", { status: 400 });
    }
    // A fresh object per run, so no run shares an object's state with an earlier one by name.
    // Its id goes back with the answer, so the driver can match the object's own analytics.
    const id = env.PROBER.idFromName(`${form}-${mib}-${Date.now()}-${Math.random()}`);
    const objectId = id.toString();
    try {
      const r = await env.PROBER.get(id).burst(form, mib);
      return Response.json({ form, mib, objectId, ok: true, ...r });
    } catch (error) {
      return Response.json({ form, mib, objectId, ok: false, error: String(error?.message ?? error) });
    }
  },
};
