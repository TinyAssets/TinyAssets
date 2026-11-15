/**
 * A key rotation's driver and its retirement (`deploy/operator.ts` `rotate`, `deploy.ts`
 * `retirement`, `offsite.ts` `firstNightAfter`), against a fake account whose doors answer
 * each batch of operator messages, a fake GitHub of the copy's nights, and a fake clock: the
 * record written after every page and gone on from after a crash; an owner left owing asked
 * first by the next run; an answer under another kid stopping the run with nothing of its page
 * recorded; and each of the retirement's gates refusing until it holds. The modes' wiring is
 * `deploy.test.ts`'s; the owner's answers are `owner/test`'s.
 */
import { describe, expect, it } from "vitest";

import { keyring } from "../../ci/fixtures";
import { DISPATCH } from "../../contract/src/canary";
import { parseRing, type Ring } from "../../contract/src/envelope";
import { OWNER_ENTRY } from "../../contract/src/ids";
import {
  answerKey, answerOf, macHolds, type OperatorMessage, operatorMessage,
} from "../../contract/src/operator-message";
import { QUEUES } from "../../contract/src/queue-names";
import { TOKEN } from "../actions";
import { Cloudflare } from "../cloudflare";
import { retirement, type Retiring } from "../deploy";
import { OPERATOR_NAMES } from "../facts";
import { firstNightAfter, KEEP_DAYS, NIGHTLY } from "../offsite";
import {
  keyHashes, type Pass, rotate, type RotationRecord, rotationKey, RUN_MS,
} from "../operator";

const ACCOUNT = "a".repeat(32);
const OPERATOR = parseRing(keyring("k1"))!;
const STORED = parseRing(keyring("k2", ["k1", "k2"]))!;
const KEY = rotationKey("FILES_KEYRING", "k2");
const START = new Date("2026-11-15T12:00:00.000Z");
const owner = (n: number) => `user_${String(n).padStart(4, "0")}`;

/** What the fake doors answer one owner's message, or null for no answer. */
type Doors = (message: OperatorMessage) => Parameters<typeof answerOf>[2] | null;

/**
 * A fake account: the directory's `o:` records of `owners`, the control namespace, the operator
 * queue whose doors answer each message of a batch at once (`doors`), and the operator bucket;
 * a clock each sleep moves on; and a switch that fails every answer's read (`failing`).
 */
function account(owners: readonly string[], doors: Doors) {
  const kv = new Map<string, string>();
  const batches: { op: string; owners: string[] }[] = [];
  const answers = new Map<string, unknown>();
  const puts: RotationRecord[] = [];
  const state = { failing: false };
  let at = START.getTime();
  const json = (body: unknown, status = 200) => Response.json(body, { status });
  const cloudflare = new Cloudflare("cf-token", async (url, init) => {
    const path = url.slice("https://api.cloudflare.com/client/v4".length);
    const method = init?.method ?? "GET";
    if (path.startsWith(`/accounts/${ACCOUNT}/storage/kv/namespaces/directory/keys?`)) {
      expect(new URL(url).searchParams.get("prefix")).toBe(OWNER_ENTRY);
      // KV's order is its own: the driver sorts.
      return json({ success: true, result: [...owners].reverse()
        .map((name) => ({ name: `${OWNER_ENTRY}${name}` })) });
    }
    const value = /^\/accounts\/a+\/storage\/kv\/namespaces\/control\/values\/(.+)$/.exec(path);
    if (value !== null) {
      const key = decodeURIComponent(value[1]!);
      if (method === "PUT") {
        kv.set(key, String(init!.body));
        puts.push(JSON.parse(String(init!.body)) as RotationRecord);
        return json({ success: true });
      }
      const held = kv.get(key);
      return held === undefined ? json({ success: false, errors: [{ code: 10009 }] }, 404)
        : new Response(held);
    }
    if (path === `/accounts/${ACCOUNT}/queues`) {
      return json({ success: true, result: [{ queue_id: "q-op", queue_name: QUEUES.OPERATOR }] });
    }
    if (path === `/accounts/${ACCOUNT}/queues/q-op/messages/batch` && method === "POST") {
      const sent = (JSON.parse(String(init!.body)) as
        { messages: { body: unknown; content_type: string }[] }).messages;
      expect(sent.length).toBeLessThanOrEqual(100);
      const messages = sent.map((each) => {
        expect(each.content_type).toBe("json");
        return operatorMessage(each.body);
      });
      for (const message of messages) {
        expect(await macHolds(OPERATOR, message)).toBe(true);
        const outcome = doors(message);
        if (outcome !== null) {
          answers.set(answerKey(message.ask), answerOf(message, Math.floor(at / 1000), outcome));
        }
      }
      batches.push({ op: messages[0]!.op, owners: messages.map((message) => message.owner) });
      return json({ success: true, result: null });
    }
    const object = `/accounts/${ACCOUNT}/r2/buckets/${OPERATOR_NAMES.bucket}/objects/`;
    if (path.startsWith(object) && method === "GET") {
      if (state.failing) {
        return json({ success: false, errors: [{ code: 10001 }] }, 500);
      }
      const answer = answers.get(path.slice(object.length));
      return answer === undefined ? json({ success: false, errors: [{ code: 10007 }] }, 404)
        : json(answer);
    }
    throw new Error(`the fake account has no ${method} ${path}`);
  });
  const said: string[] = [];
  const sweep = (stored: Ring = STORED) => rotate({
    cloudflare, account: ACCOUNT, operator: OPERATOR, keyring: "FILES_KEYRING",
    op: "owner.rewrap", stored, directory: "directory", control: "control",
    waiting: { now: () => new Date(at), sleep: async (ms) => { at += ms; } },
    say: (...lines) => { said.push(...lines); } });
  const record = () => JSON.parse(kv.get(KEY) ?? "null") as RotationRecord | null;
  const asked = (op = "owner.rewrap") => batches.filter((batch) => batch.op === op)
    .flatMap((batch) => batch.owners);
  return { sweep, record, batches, puts, state, said, asked, kv,
           tick: (ms: number) => { at += ms; } };
}

const swept = (left = 0, kid: string | null = "k2"): Doors => () =>
  ({ result: { rewrapped: true, roots: 2, left, kid } });

describe("a rotation's sweep of the directory", () => {
  it("asks every owner in pages of 100, writing the record after each, and sweeps the pass",
     async () => {
    const owners = Array.from({ length: 250 }, (_, n) => owner(n));
    const a = account(owners, swept());
    const record = await a.sweep();
    expect(a.batches.map((batch) => batch.owners.length)).toEqual([100, 100, 50]);
    expect(a.asked()).toEqual(owners);
    // A page's record names its last owner, before the next page is asked.
    expect(a.puts.map((put) => put.passes[0]!.through)).toEqual([owner(99), owner(199),
                                                                  owner(249)]);
    expect(a.puts.map((put) => put.passes[0]!.swept === null)).toEqual([true, true, false]);
    expect(record).toEqual(a.record());
    expect(record).toMatchObject({ kind: "tinyassets-rotation", keyring: "FILES_KEYRING",
                                   active: "k2", keys: keyHashes(STORED) });
    const pass = record.passes[0]!;
    expect(pass).toEqual({ began: Math.floor(START.getTime() / 1000), through: owner(249),
                           owing: [], swept: pass.swept });
    expect(pass.swept).toBeGreaterThan(pass.began!);
    // The record holds each key's hash, never a key.
    expect(JSON.stringify(record)).not.toContain(Buffer.from(STORED.keys.get("k2")!)
      .toString("base64"));
    expect(a.said.at(-1)).toMatch(/^pass 1 swept every owner to left 0, the last at /);
    // Swept, a run again begins a second pass.
    await a.sweep();
    expect(a.record()!.passes).toHaveLength(2);
    expect(a.said).toContain("begin pass 2 of FILES_KEYRING's rotation to k2");
  });

  it("goes on after a crash from the last page recorded whole, asking the page cut short again",
     async () => {
    const owners = Array.from({ length: 250 }, (_, n) => owner(n));
    let reads = 0;
    const a = account(owners, (message) => {
      if (message.owner === owner(150) && reads++ === 0) {
        a.state.failing = true;  // the run dies reading page 2's answers
      }
      return { result: { rewrapped: true, roots: 1, left: 0, kid: "k2" } };
    });
    await expect(a.sweep()).rejects.toThrow(/failed \(500/);
    expect(a.record()!.passes).toEqual([expect.objectContaining({ through: owner(99),
                                                                  swept: null })]);
    a.state.failing = false;
    const before = a.asked().length;
    await a.sweep();
    // No owner of page 1 asked again; page 2 asked whole, then page 3.
    expect(a.asked().slice(before)).toEqual(owners.slice(100));
    expect(a.record()!.passes).toHaveLength(1);
    expect(a.record()!.passes[0]!.swept).not.toBeNull();
  });

  it("leaves a held owner owing, asked again at the run's end and first by the next run, and " +
     "sweeps only once none owes", async () => {
    const owners = Array.from({ length: 120 }, (_, n) => owner(n));
    const held = owner(5);
    const restore = { step: "rows" as string | null };
    const a = account(owners, (message) => {
      if (message.op === "owner.state") {
        return { result: { state: "pending", reasons: [], tier: "free", restore: restore.step,
                           runs: 0, stored_bytes: 0 } };
      }
      if (message.owner === owner(7)) {
        return { refused: { code: "owner_erased", message: "the owner is erased" } };
      }
      return { result: { rewrapped: false, roots: 0, left: message.owner === held
        && restore.step !== null ? 2 : 0, kid: "k2" } };
    });
    await a.sweep();
    // Asked in page 1, then once more after the directory; its restore step named.
    expect(a.asked().filter((each) => each === held)).toHaveLength(2);
    expect(a.asked("owner.state")).toEqual([held]);
    expect(a.said).toContain(`${held} still owes (left 2): restore step rows`);
    // An erased owner owes nothing.
    expect(a.record()!.passes).toEqual([{ began: expect.any(Number), through: owner(119),
                                          owing: [held], swept: null }]);
    restore.step = null;
    const before = a.batches.length;
    await a.sweep();
    expect(a.batches.slice(before)).toEqual([{ op: "owner.rewrap", owners: [held] }]);
    expect(a.record()!.passes).toEqual([expect.objectContaining({ owing: [],
                                                                  swept: expect.any(Number) })]);
  });

  it("stops at an answer under a kid other than the stored ring's active one, recording " +
     "nothing of its page", async () => {
    const owners = Array.from({ length: 150 }, (_, n) => owner(n));
    const a = account(owners, (message) => ({ result: { rewrapped: false, roots: 1, left: 0,
      kid: message.owner === owner(120) ? "k1" : "k2" } }));
    await expect(a.sweep()).rejects.toThrow(
      `the doors answered owner.rewrap for ${owner(120)} under k1, not the stored ring's ` +
      "active kid k2: the deployed ring is not the stored one");
    expect(a.puts).toHaveLength(1);
    expect(a.record()!.passes[0]!.through).toBe(owner(99));
  });

  it("refuses a record begun with another ring under the same active kid", async () => {
    const a = account([owner(1)], swept());
    await a.sweep();
    const other = parseRing(keyring("k2", ["k0", "k2"]))!;
    await expect(a.sweep(other)).rejects.toThrow(
      `the rotation record ${KEY} was begun with another ring under k2 (kids k1, k2)`);
  });

  it("leaves an owner no answer reaches owing, and stops at a page's end past its time, the " +
     "next run going on from there", async () => {
    const owners = Array.from({ length: 300 }, (_, n) => owner(n));
    const a = account(owners, (message) => {
      if (message.owner === owner(150) && message.op === "owner.rewrap") {
        a.tick(RUN_MS);  // page 2 outlasts the run's time
      }
      return message.owner === owner(3) ? null
        : { result: { rewrapped: false, roots: 0, left: 0, kid: null } };
    });
    const record = await a.sweep();
    expect(record.passes[0]).toMatchObject({ through: owner(199), owing: [owner(3)], swept: null });
    expect(a.said.at(-1)).toBe("stopped after 20 minutes, the record saying where: run again " +
                               "to go on");
    const before = a.batches.length;
    await a.sweep();
    expect(a.batches.slice(before).map((batch) => [batch.op, batch.owners.length,
                                                   batch.owners[0]])).toEqual([
      ["owner.rewrap", 1, owner(3)], ["owner.rewrap", 100, owner(200)],
      ["owner.rewrap", 1, owner(3)], ["owner.state", 1, owner(3)]]);
    expect(a.said).toContain(`${owner(3)} still owes (no answer within 120 s (the dead-letter ` +
                             `queue ${OPERATOR_NAMES.dead_letter_queue} may hold it)): ` +
                             "owner.state unanswered");
  });
});

/** A rotation record of FILES_KEYRING to k2 with `passes`. */
const recordOf = (passes: Pass[]): RotationRecord => ({
  kind: "tinyassets-rotation", keyring: "FILES_KEYRING", active: "k2", keys: keyHashes(STORED),
  passes });
const RETIRED = parseRing(JSON.stringify({ active: "k2", keys: { k2: Buffer.from(
  STORED.keys.get("k2")!).toString("base64") } }))!;
const s = (iso: string) => Date.parse(iso) / 1000;
const DAY = 86_400_000;
/** The first copy night after pass 1, and its gate: `KEEP_DAYS` + 1 days on. */
const T = new Date("2026-11-16T05:58:00.000Z");
const GATE = new Date(T.getTime() + (KEEP_DAYS + 1) * DAY);
const PASS_1: Pass = { began: s("2026-11-15T12:00:00Z"), through: "user_z", owing: [],
                       swept: s("2026-11-15T13:00:00Z") };
const after = (from: Date, ms: number) => Math.floor((from.getTime() + ms) / 1000);
const PASS_2: Pass = { began: after(GATE, 60_000), through: "user_z", owing: [],
                       swept: after(GATE, 3600_000) };
const PASSED = { state: "passed", line: "a night last passed" } as const;

function retiring(change: Partial<Retiring> = {}): Retiring {
  return { keyring: "FILES_KEYRING", copied: true, stored: RETIRED,
           record: recordOf([PASS_1, PASS_2]),
           copy: { watch: PASSED, night: { first: T, ever: true } },
           now: new Date(GATE.getTime() + 2 * 3600_000), ...change };
}

describe("a keyring's retirement", () => {
  it("holds once the latest pass swept after the copy's versions under the old kids expired, " +
     "and names its words", () => {
    expect(KEEP_DAYS).toBe(22);
    expect(retirement(retiring())).toEqual({ kids: ["k1"], words: "retire-FILES_KEYRING-k1",
      lines: [`pass 2 of ${KEY} swept every owner to left 0 at ${
        new Date(PASS_2.swept! * 1000).toISOString()}`,
              `the off-account copy's versions under k1 expired at ${GATE.toISOString()}, ` +
              "before pass 2 began"] });
    // A keyring the copy never holds: one pass is enough.
    expect(retirement(retiring({ keyring: "GATEWAY_KEYRING", copied: false, copy: null,
      record: { ...recordOf([PASS_1]), keyring: "GATEWAY_KEYRING" } })).words)
      .toBe("retire-GATEWAY_KEYRING-k1");
    // A copy that never passed a night holds nothing under k1.
    expect(retirement(retiring({ record: recordOf([PASS_1]),
      copy: { watch: { state: "waiting", line: "" }, night: { first: null, ever: false } } }))
      .lines.at(-1)).toBe("the off-account copy has never passed a night, so it holds no blob " +
                          "under k1");
  });

  it.each([
    ["no record", { record: undefined }, `holds no rotation record ${KEY}`],
    ["the full ring stored", { stored: STORED },
     "the stored FILES_KEYRING is not the rotation record's ring less k1"],
    ["another key kept under k2", { stored: parseRing(keyring("k2"))! },
     "the stored FILES_KEYRING is not the rotation record's ring less k1"],
    ["no pass swept", { record: recordOf([{ ...PASS_1, swept: null, owing: ["user_a"] }]) },
     "pass 1 of deploy:rotation:FILES_KEYRING:k2 has not swept every owner to left 0"],
    ["the latest pass owing", { record: recordOf([PASS_1, { ...PASS_2, swept: null }]) },
     "pass 2 of deploy:rotation:FILES_KEYRING:k2 has not swept"],
    ["no copy night after pass 1", { record: recordOf([PASS_1]),
                                     copy: { watch: PASSED, night: { first: null, ever: true } } },
     "no night of the off-account copy has passed since pass 1 swept at 2026-11-15T13:00:00.000Z"],
    ["a minute before the gate", { now: new Date(GATE.getTime() - 60_000) },
     `the off-account copy's versions under k1 expire by ${GATE.toISOString()}`],
    ["pass 2 begun before the gate", { record: recordOf([PASS_1, {
      ...PASS_2, began: after(GATE, -60_000) }]) },
     "pass 2 of deploy:rotation:FILES_KEYRING:k2 began at"],
    ["one pass alone", { record: recordOf([PASS_1]) }, "pass 1 of deploy:rotation:FILES_KEYRING:k2 began at"],
    ["the copy overdue", { copy: { watch: { state: "overdue", line: "no night since" },
                                   night: { first: T, ever: true } } },
     "the off-account copy is overdue (no night since)"],
  ] as [string, Partial<Retiring>, string][])("refuses with %s", (_what, change, refusal) => {
    expect(() => retirement(retiring(change))).toThrow(refusal);
  });
});

describe("the copy's first night after a pass", () => {
  /** A GitHub of the copy's completed nights, newest first: when each was made, and its copy. */
  const reach = (nights: { at: string; copy: string }[]) => ({
    env: { [TOKEN]: "gh-token" },
    fetch: async (url: string) => {
      const repository = `https://api.github.com/repos/${DISPATCH.repository}`;
      const at = new URL(url);
      if (url.startsWith(`${repository}/actions/workflows/${NIGHTLY.workflow}/runs?`)) {
        return Response.json({ workflow_runs: at.searchParams.get("page") !== "1" ? []
          : nights.map((night, id) => ({ id, created_at: night.at })) });
      }
      const jobs = /\/actions\/runs\/(\d+)\/jobs$/.exec(at.pathname);
      const night = nights[Number(jobs?.[1])]!;
      return Response.json({ jobs: [
        { name: NIGHTLY.settings, conclusion: "success", completed_at: night.at },
        { name: NIGHTLY.copy, conclusion: night.copy,
          completed_at: new Date(Date.parse(night.at) + 20 * 60_000).toISOString() }] });
    },
  });

  it("is the copy's end of the first night made after it that passed", async () => {
    const nights = [{ at: "2026-11-18T05:37:00Z", copy: "success" },
                    { at: "2026-11-17T05:37:00Z", copy: "success" },
                    { at: "2026-11-16T05:37:00Z", copy: "failure" },
                    { at: "2026-11-15T05:37:00Z", copy: "success" }];
    expect(await firstNightAfter(reach(nights), new Date("2026-11-15T13:00:00Z")))
      .toEqual({ first: new Date("2026-11-17T05:57:00Z"), ever: true });
    expect(await firstNightAfter(reach(nights), new Date("2026-11-18T06:00:00Z")))
      .toEqual({ first: null, ever: true });
    expect(await firstNightAfter(reach([{ at: "2026-11-18T05:37:00Z", copy: "skipped" }]),
                                 new Date("2026-11-15T13:00:00Z")))
      .toEqual({ first: null, ever: false });
  });
});
