/**
 * The starter's tests' support. Every owner in the `seeded` project carries the package this
 * code carries (`carried()`, no alias), as production seeds it:
 * - `settledOwner`: an owner whose own seed has landed, for the tests of updates, which pass
 *   a package of their own over its storage at a version past the seed (`SEED + n`, so a
 *   starter bump leaves them as they are);
 * - `failedOwner`: an owner whose own first seed failed part way, so its own files may come
 *   before the seed's retry lands.
 */
import { env } from "cloudflare:workers";

import { Notices } from "../../src/notices";
import { Starter } from "../../src/starter";
import { carried, type StarterFile, type StarterPackage } from "../../src/starter-package";
import { rig } from "../ledger-support";
import { drainDue, filesOn, inGate, sqlIn } from "../support";

/** The version every owner here is seeded at: a test's package is `SEED + n`. */
export const SEED = carried().version;

export const AGENTS = "/workspace/agents/main";
export const SKILLS = "/workspace/.agents/skills";

export const file = (path: string, value: string, extra: Partial<StarterFile> = {}):
  StarterFile => ({ path, class: "managed", text: value, ...extra });
export const agentJson = (scopes: string[], engine = "auto") =>
  JSON.stringify({ engine, scopes });

export type Rig = Awaited<ReturnType<typeof rig>>;

/** A file's whole text through `file_read`'s door, or null when there is none. */
export async function readText(r: Rig, path: string): Promise<string | null> {
  const { entry } = await r.stub.fileStat(r.bind(null), { path });
  return entry === null ? null
    : (await r.stub.fileText(r.bind(null), { path, max_bytes: 4_194_304 })).text;
}

/** The starter's receipts, by path. */
export async function receipts(r: Rig) {
  return Object.fromEntries((await r.stub.starterReceipts(r.bind(null))).receipts
    .map((row) => [row.path, row]));
}

/** The details of the owner's notices of `type`. */
export async function notices(r: Rig, type: string): Promise<string[]> {
  return (await r.stub.noticeList(r.bind(null))).notices
    .filter((notice) => notice.type === type).map((notice) => notice.detail);
}

/** The version the owner's starter last reconciled whole. */
export async function versionOf(r: Rig): Promise<number> {
  return (await r.stub.starterReceipts(r.bind(null))).version;
}

/** The object's lanes run until its own seed has landed. */
export async function settled(r: Rig): Promise<void> {
  for (let tries = 0; tries < 100; tries++) {
    if (await versionOf(r) === SEED) {
      return;
    }
    await drainDue(r.stub);
  }
  throw new Error("the owner's seed did not land");
}

/** A fresh owner whose own seed has landed. */
export async function settledOwner(): Promise<Rig> {
  const r = await rig();
  await settled(r);
  return r;
}

/**
 * A fresh owner whose own first seed failed part way: its first upload held at the FILES gate,
 * the uploads after it refused there (`times` of them) by the files plane's own refusal, the
 * hold let go, and the lanes run until the owner is told. Until its retry is due, the owner's
 * doors no longer wait for the seed, so the owner's own files may come first (production's
 * one way to a file of the owner's there before its seed lands).
 */
export async function failedOwner(times = 1): Promise<Rig> {
  const arm = () => env.FILES_GATE.arm({ method: "putChunks", hold: "before" });
  await arm();
  const r = await rig();
  // The arming names no owner (the seed itself mints the namespace the gate names calls by):
  // a call of another owner's that meets it is let go, the arming made again first.
  let first: number | null = null;
  let ns: string | null = null;
  for (let tries = 0; tries < 200 && first === null; tries++) {
    ns = (await sqlIn(r.stub, "SELECT ns FROM file_space"))[0]?.["ns"] as string | null ?? null;
    for (const held of await env.FILES_GATE.held()) {
      if (ns !== null && held.name === ns) {
        first = held.id;
      } else if (ns !== null) {
        await arm();
        await env.FILES_GATE.release(held.id);
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  if (first === null || ns === null) {
    throw new Error("the owner's seed made no upload");
  }
  await env.FILES_GATE.arm({ method: "putChunks", name: ns, times,
                             refuse: { code: "internal", message: SEED_REFUSED } });
  await env.FILES_GATE.release(first);
  for (let tries = 0; tries < 100 && (await notices(r, "starter_failed")).length === 0;
       tries++) {
    await drainDue(r.stub);
  }
  return r;
}

/** What the files plane answers a failed seed's upload in `failedOwner`. */
export const SEED_REFUSED = "internal: the files plane failed (the test's)";

/** A starter of `pkg` over `r`'s object, as its lane builds one, for `act` to drive. */
export function starterOn(r: Rig, pkg: StarterPackage, now: number) {
  return <T>(act: (starter: Starter) => Promise<T>) =>
    inGate(r.stub, async (_instance, _state, gate) => {
      const notes = new Notices(gate.storage);
      return act(new Starter({
        storage: gate.storage, files: filesOn(gate.storage, gate.write), write: gate.write,
        admitted: () => true, ready: () => true, clock: () => now,
        notice: (type, detail) => notes.add(type, detail, now),
      }, pkg));
    });
}

/** One pass of `pkg` over `r`'s object, as its lane runs it, at `now`. */
export function pass(r: Rig, pkg: StarterPackage, now = Date.now() / 1000): Promise<void> {
  return starterOn(r, pkg, now)((starter) => starter.run(now));
}

/** When the lane would next run `pkg` on `r`'s object, at `now`. */
export function nextOf(r: Rig, pkg: StarterPackage, now: number): Promise<number | null> {
  return starterOn(r, pkg, now)(async (starter) => starter.next(now));
}
