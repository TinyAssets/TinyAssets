/**
 * The Worker kit's gate and auxiliary Workers, run (`ci/gate.ts`, `ci/workers.ts`
 * `auxiliary`): the owner Worker, bundled from its real entry, serves its doors by
 * `scriptName` with no test class of its own; a gate in front of the files Worker and one in
 * front of the owner forward every call as it is, hold one until it is let go, change one's
 * bytes in flight, and pass a refusal, injected or real, through unchanged.
 */
import { env } from "cloudflare:workers";
import { afterEach, describe, expect, it } from "vitest";

import { PLATFORM_CALLER } from "../../contract/src/binding";
import type { ChunkStore } from "../../contract/src/files";
import type { OwnerDoors } from "../../contract/src/owner";
import { refusalOf } from "../../contract/src/refusal";
import { HELD_WAIT_MS, TIERS } from "../fixtures";
import type { GateControl } from "../gate";

/** A binding called by a name its type does not have. */
type Untyped = Record<string, () => Promise<unknown>>;
type Owner = DurableObjectStub<OwnerDoors>;
interface Env {
  readonly OWNER: DurableObjectNamespace<OwnerDoors>;
  readonly REAL_OWNER: DurableObjectNamespace<OwnerDoors>;
  readonly FILES: ChunkStore;
  readonly REAL_FILES: ChunkStore;
  readonly FILES_GATE: GateControl;
  readonly OWNER_GATE: GateControl;
}
const e = env as unknown as Env;

// A gate's state is module state, which outlives a test: what one test armed, held or
// recorded is gone before the next.
afterEach(async () => {
  await e.FILES_GATE.disarm();
  await e.OWNER_GATE.disarm();
});

/**
 * What a call ended with: its answer, or its error's name, message and own fields, less the
 * ones the runtime adds at each hop it crosses (`remote`, the `durableObjectId` it left).
 */
async function outcome(call: () => Promise<unknown>): Promise<unknown> {
  try {
    return { answered: await call() };
  } catch (error) {
    const { name, message, stack: _stack, remote: _remote, durableObjectId: _id, ...own } =
      error as Error & Record<string, unknown>;
    return { name, message, ...own };
  }
}

async function sha256(bytes: Uint8Array): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** A new namespace's name, as the files Worker takes one. */
const fresh = () => crypto.randomUUID().replaceAll("-", "");

/** A fresh namespace in the files Worker, its key, and one chunk stored there. */
async function space() {
  const ns = fresh();
  const { key } = await e.REAL_FILES.mintKey(ns);
  const bytes = new TextEncoder().encode(`chunk of ${ns}`);
  const hash = await sha256(bytes);
  await e.REAL_FILES.putChunks(ns, key, [{ sha256: hash, bytes }]);
  return { ns, key, bytes, hash };
}

/** Whether `promise` has settled yet. */
function watched(promise: Promise<unknown>): { settled: () => boolean } {
  let settled = false;
  promise.then(() => { settled = true; }, () => { settled = true; });
  return { settled: () => settled };
}

/** A new owner, admitted by the platform's door, and its own binding. */
async function owner(stub: (name: string) => Owner) {
  const name = `owner-${crypto.randomUUID()}`;
  await stub(name).admissionSet({ owner: name, caller: PLATFORM_CALLER, origin: null },
                                { admit: true });
  return { name, base: { owner: name, caller: name, origin: null, tiers: TIERS } };
}
const gated = (name: string): Owner => e.OWNER.getByName(name);
const direct = (name: string): Owner => e.REAL_OWNER.getByName(name);

describe("the files gate", () => {
  it("forwards each call as it is, and records what crossed with its bytes counted", async () => {
    const s = await space();
    await e.FILES_GATE.seen();
    const read = [{ sha256: s.hash, verify: true as const }];
    expect(await e.FILES.getChunks(s.ns, s.key, read))
      .toEqual(await e.REAL_FILES.getChunks(s.ns, s.key, read));
    expect(await e.FILES_GATE.seen()).toEqual([
      { method: "getChunks", name: null, args: [s.ns, s.key, read] }]);
    const bytes = new TextEncoder().encode("another");
    await e.FILES.putChunks(s.ns, s.key, [{ sha256: await sha256(bytes), bytes }]);
    expect(await e.FILES_GATE.seen()).toEqual([{ method: "putChunks", name: null, args: [
      s.ns, s.key, [{ sha256: await sha256(bytes), bytes: { bytes: bytes.length } }]] }]);
  });

  it("holds a read before it is sent, and after its answer, until it is released", async () => {
    const s = await space();
    for (const at of ["before", "after"] as const) {
      await e.FILES_GATE.arm({ method: "getChunks", hold: at });
      const reading = e.FILES.getChunks(s.ns, s.key, [{ sha256: s.hash, verify: true }]);
      const read = watched(reading);
      const held = await e.FILES_GATE.held(HELD_WAIT_MS);
      expect(held).toEqual([{ id: expect.any(Number), method: "getChunks", name: null, at }]);
      await e.FILES.mintKey(fresh());
      expect(read.settled()).toBe(false);
      await e.FILES_GATE.release(held[0]!.id);
      expect(await reading).toEqual([s.bytes]);
      expect(await e.FILES_GATE.held()).toEqual([]);
    }
    // Armed once, it acts once.
    expect(await e.FILES.getChunks(s.ns, s.key, [{ sha256: s.hash, verify: true }]))
      .toEqual([s.bytes]);
    expect(await outcome(() => e.FILES_GATE.release(1_000_000)))
      .toMatchObject({ message: "no call 1000000 is held at the gate" });
  });

  it("passes an injected refusal through unchanged, as many times as armed", async () => {
    const s = await space();
    const refusal = { code: "busy", message: "busy: the files Worker is full", retryAfterS: 3,
                      facts: { held: 2 } };
    await e.FILES_GATE.arm({ method: "getChunks", refuse: refusal, times: 2 });
    const read = () => e.FILES.getChunks(s.ns, s.key, [{ sha256: s.hash, verify: true }]);
    expect(await outcome(read)).toEqual({ name: "Refusal", ...refusal });
    // The contract reads it as the refusal it was armed with.
    const refused = refusalOf(await read().catch((error: unknown) => error));
    expect(refused === null ? null : { name: refused.name, code: refused.code,
                                       message: refused.message,
                                       retryAfterS: refused.retryAfterS, facts: refused.facts })
      .toEqual({ name: "Refusal", ...refusal });
    expect(await read()).toEqual([s.bytes]);
  });

  it("passes a real refusal through as it crosses without the gate", async () => {
    const s = await space();
    const empty = (files: ChunkStore) => outcome(() => files.putChunks(s.ns, s.key, []));
    const real = await empty(e.REAL_FILES);
    expect(real).toMatchObject({ name: "Refusal", code: "invalid" });
    expect(await empty(e.FILES)).toEqual(real);
  });

  it("changes a call's bytes in flight, and the real Worker refuses them", async () => {
    const s = await space();
    const bytes = new TextEncoder().encode("sent as it was hashed");
    const put = [{ sha256: await sha256(bytes), bytes }];
    await e.FILES_GATE.arm({ method: "putChunks", corrupt: true });
    expect(await outcome(() => e.FILES.putChunks(s.ns, s.key, put))).toMatchObject({
      name: "Refusal", code: "invalid", message: "the chunk's bytes do not match its sha256" });
    expect(await e.FILES.putChunks(s.ns, s.key, put)).toEqual({ created: 1 });
  });

  it("refuses an arming of a method its interface does not have", async () => {
    expect(await outcome(() => e.FILES_GATE.arm({ method: "notAMethod", hold: "before" })))
      .toMatchObject({ message: "the gate has no method notAMethod" });
    expect(await outcome(() => (e.FILES as unknown as Untyped)["notAMethod"]!()))
      .toMatchObject({ message: expect.stringMatching(/does not implement the method/) });
  });

  it("refuses an arming that would never act", async () => {
    const never = [{ method: "getChunks" }, { method: "getChunks", hold: "during" },
                   { method: "getChunks", hold: "before", corrupt: true },
                   { method: "getChunks", name: "an-object", hold: "before" }];
    for (const arm of never) {
      expect(await outcome(() => e.FILES_GATE.arm(arm as never)))
        .toMatchObject({ message: expect.stringMatching(/^an arming holds|never act$/) });
    }
    expect(await e.FILES_GATE.held()).toEqual([]);
  });

  it("is as it started once disarmed: no arming, no held call, an empty record", async () => {
    const s = await space();
    await e.FILES_GATE.arm({ method: "getChunks", hold: "before" });
    const reading = e.FILES.getChunks(s.ns, s.key, [{ sha256: s.hash, verify: true }]);
    expect(await e.FILES_GATE.held(HELD_WAIT_MS)).toHaveLength(1);
    await e.FILES_GATE.arm({ method: "mintKey", refuse: { code: "busy", message: "busy" } });
    await e.FILES_GATE.disarm();
    expect(await reading).toEqual([s.bytes]);
    expect([await e.FILES_GATE.held(), await e.FILES_GATE.seen()]).toEqual([[], []]);
    expect(await e.FILES.mintKey(fresh())).toHaveProperty("key");
  });
});

describe("the owner Worker as an auxiliary, behind the owner gate", () => {
  it("serves its doors from its real entry, bound by scriptName", async () => {
    const o = await owner(direct);
    await direct(o.name).fileWrite(o.base, { path: "/workspace/notes/a.md", expected_etag: "",
                                             content: "one" });
    const read = await direct(o.name).fileRead(o.base, { path: "/workspace/notes/a.md" });
    expect(new TextDecoder().decode(read.inline)).toBe("one");
    expect(await gated(o.name).fileRead(o.base, { path: "/workspace/notes/a.md" })).toEqual(read);
  });

  it("passes the owner's real refusal through as it crosses without the gate", async () => {
    const name = `owner-${crypto.randomUUID()}`;
    const base = { owner: name, caller: name, origin: null, tiers: TIERS };
    const read = (stub: (name: string) => Owner) => outcome(() => stub(name).policyRead(base));
    const real = await read(direct);
    expect(real).toMatchObject({ name: "Refusal", code: "owner_unadmitted" });
    expect(await read(gated)).toEqual(real);
  });

  it("refuses one owner's door once as armed, and records each owner's doors", async () => {
    const [a, b] = [await owner(gated), await owner(gated)];
    await e.OWNER_GATE.seen();
    const refusal = { code: "owner_suspended", message: "owner_suspended: held for review" };
    await e.OWNER_GATE.arm({ method: "policyRead", name: a.name, refuse: refusal });
    const read = (o: typeof a) => outcome(() => gated(o.name).policyRead(o.base));
    const others = await read(b);
    expect(others).toHaveProperty("answered");
    expect(await read(a)).toEqual({ name: "Refusal", ...refusal });
    expect(await read(a)).toMatchObject({ answered: { owner: a.name } });
    expect(await e.OWNER_GATE.seen(a.name)).toEqual([
      { method: "policyRead", name: a.name, args: [a.base] },
      { method: "policyRead", name: a.name, args: [a.base] }]);
    expect((await e.OWNER_GATE.seen(b.name)).map((call) => call.method)).toEqual(["policyRead"]);
    expect(await outcome(() => (gated(a.name) as unknown as Untyped)["notADoor"]!()))
      .toMatchObject({ message: expect.stringMatching(/does not implement the method/) });
  });

  it("holds the owner's own files call at the files gate its FILES binding reaches", async () => {
    const o = await owner(direct);
    // A small file is kept inline, so the first write mints the owner's key and puts nothing;
    // the write past the inline size below puts its chunk.
    await direct(o.name).fileWrite(o.base, { path: "/workspace/notes/small.md",
                                             expected_etag: "", content: "small" });
    await e.FILES_GATE.seen();
    await e.FILES_GATE.arm({ method: "putChunks", hold: "before" });
    const writing = direct(o.name).fileWrite(o.base, { path: "/workspace/notes/big.md",
                                                       expected_etag: "",
                                                       content: "x".repeat(8_192) });
    const write = watched(writing);
    const held = await e.FILES_GATE.held(HELD_WAIT_MS);
    expect(held.map((call) => call.method)).toEqual(["putChunks"]);
    await direct(o.name).policyRead(o.base);
    expect(write.settled()).toBe(false);
    await e.FILES_GATE.release();
    await writing;
    const read = await direct(o.name).fileRead(o.base, { path: "/workspace/notes/big.md" });
    expect(read.entry).toMatchObject({ size: 8_192 });
    expect((await e.FILES_GATE.seen()).map((call) => call.method)).toContain("putChunks");
  });
});
