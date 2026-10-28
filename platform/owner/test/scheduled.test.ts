/**
 * Stamped scheduled work, the `work` lane (tasks 3.6, slice 3's review N3): made and run
 * only while the outside authority it was stamped with stands, holding its client's grant
 * row meanwhile, and never run again once it may have run.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { GRANT_LIMITS, Grants } from "../src/grants";
import { clock, type Origin } from "../src/ledger";
import type { OwnerDO } from "../src/owner-do";
import { Policy } from "../src/policy";
import {
  runWork, Scheduled, type Stamp, WORK_LEASE_S, WORK_LIMITS, type WorkLimits, type WorkRunner,
  type WorkTarget,
} from "../src/scheduled";
import { MUSE, rig, type Rig } from "./ledger-support";
import { laneAt } from "./support";

const CHATGPT: Origin = Object.freeze({ client: "1".repeat(64), family: "2".repeat(64) });
const TURN = Object.freeze({ prompt: "check the board" });

/** A runner over a script: each run's answer, `crash` to die mid-run, `early` before mark. */
class ScriptedRunner implements WorkRunner {
  readonly ran: WorkTarget[] = [];
  readonly answers: ("done" | "failed" | "crash" | "early")[] = [];

  async run(target: WorkTarget, mark: () => void) {
    const answer = this.answers.shift() ?? "done";
    if (answer === "early") {
      throw new Error("died before the work began");
    }
    mark();
    this.ran.push(target);
    if (answer === "crash") {
      throw new Error("died after the work began");
    }
    return { state: answer };
  }
}

interface Parts {
  readonly grants: Grants;
  readonly work: Scheduled;
  readonly state: DurableObjectState;
}

/** The object's own grants and scheduled work, with some bounds changed. */
function inWork<R>(r: Rig, act: (parts: Parts) => R | Promise<R>,
                   limits: Partial<WorkLimits> = {},
                   grantLimits: Partial<typeof GRANT_LIMITS> = {}): Promise<R> {
  return runInDurableObject(r.stub, (_i: OwnerDO, state) => {
    const grants = new Grants(state.storage, new Policy(state.storage),
                              { ...GRANT_LIMITS, ...grantLimits });
    return act({ grants, state, work: new Scheduled(state.storage, grants,
                                                    { ...WORK_LIMITS, ...limits }) });
  });
}

/** The client's authority as its last admitted call had it. */
async function admitted(r: Rig, origin: Origin, signedIn: number | null = null): Promise<Stamp> {
  await r.stub.outsideAgents(r.bind(origin));
  const row = (await r.stub.grantList(r.bind(null))).clients
    .find((c) => c.client === origin.client)!;
  return { client: origin.client, generation: row.generation, authenticated_at: signedIn };
}

const states = (r: Rig) => r.sql("SELECT state, attempts, marked FROM scheduled_work " +
                                 "ORDER BY created_at, work_id");
const held = async (r: Rig, origin: Origin) => (await r.sql(
  "SELECT held FROM outside_clients WHERE client = ?", origin.client))[0]?.["held"];

/** Every counter the work's bounds read, and the same numbers counted from the rows. */
async function counters(r: Rig) {
  const [kept] = await r.sql("SELECT open_work, settled_work, (SELECT json_group_object(" +
    "client, held) FROM (SELECT * FROM outside_clients ORDER BY client)) AS held " +
    "FROM policy_usage");
  const [counted] = await r.sql(
    "SELECT (SELECT COUNT(*) FROM scheduled_work WHERE settled_at IS NULL) AS open_work, " +
      "(SELECT COUNT(*) FROM scheduled_work WHERE settled_at IS NOT NULL) AS settled_work, " +
      "(SELECT json_group_object(client, n) FROM (SELECT c.client, (SELECT COUNT(*) FROM " +
      "scheduled_work w WHERE w.client = c.client AND w.settled_at IS NULL) + (SELECT " +
      "COUNT(*) FROM agent_cards a WHERE a.client = c.client) AS n FROM outside_clients c " +
      "ORDER BY c.client)) AS held");
  return [kept, counted];
}

describe("stamped scheduled work", () => {
  it("is made only under authority that stands, or the owner's own", async () => {
    const r = await rig();
    const stamp = await admitted(r, CHATGPT);
    const out = await inWork(r, ({ work }) => {
      const tried = (made: unknown) => {
        try {
          return work.add("turn", clock() + 60, TURN, made, clock()).work_id.slice(0, 5);
        } catch (error) {
          return (error as Error).message;
        }
      };
      return [tried(stamp), tried(null), tried({ ...stamp, generation: 2 }),
              tried({ ...stamp, client: "9".repeat(64) }), tried({ ...stamp, extra: 1 }),
              tried({ ...stamp, generation: "1" })];
    });
    expect(out).toEqual(["work_", "work_", "outside client authority changed",
                         "outside client has no grant",
                         "a stamp is a client, its grant generation and its sign-in time",
                         "a stamp is a client, its grant generation and its sign-in time"]);
    expect(await held(r, CHATGPT)).toBe(1);
  });

  it("holds its client's row, so free-row eviction never makes it 'no grant' (N3)",
     async () => {
    const r = await rig();
    const stamp = await admitted(r, CHATGPT);
    const runner = new ScriptedRunner();
    await inWork(r, ({ work }) => work.add("turn", clock() - 1, TURN, stamp, clock()));
    expect((await r.stub.grantList(r.bind(null))).clients[0]!.held).toBe(1);
    await inWork(r, ({ grants }) => {
      grants.admit(MUSE.client, "mcp", null, clock());  // one free row only: CHATGPT's stays
    }, {}, { maxFreeClients: 1 });
    expect((await r.stub.grantList(r.bind(null))).clients.map((c) => c.client).sort())
      .toEqual([CHATGPT.client, MUSE.client].sort());
    await expect(r.sql("DELETE FROM outside_clients WHERE client = ?", CHATGPT.client))
      .rejects.toThrow(/never deleted/);
    expect(await inWork(r, ({ work }) => runWork(work, runner, clock))).toBe(1);
    expect(runner.ran.map((t) => [t.kind, t.payload, t.stamp])).toEqual([["turn", TURN, stamp]]);
    expect(await states(r)).toEqual([{ state: "done", attempts: 1, marked: 1 }]);
    expect(await held(r, CHATGPT)).toBe(0);  // settled: the row is free again
    const [kept, counted] = await counters(r);
    expect(kept).toEqual(counted);
  });

  it("is rechecked with standing before it runs, and refused once the grant moved",
     async () => {
    const r = await rig();
    const stamp = await admitted(r, CHATGPT, 100);
    const runner = new ScriptedRunner();
    await inWork(r, ({ work }) => {
      work.add("turn", clock() - 1, TURN, stamp, clock());
      work.add("turn", clock() - 1, TURN, null, clock());  // the owner's own: no stamp
    });
    // A grant change made around the object's doors, so only the run's recheck sees it.
    await inWork(r, ({ grants }) => grants.revoke(CHATGPT.client, clock()));
    expect(await inWork(r, ({ work }) => runWork(work, runner, clock))).toBe(1);
    expect(runner.ran.map((t) => t.stamp)).toEqual([null]);
    expect((await states(r)).map((row) => row["state"]).sort()).toEqual(["done", "refused"]);
    expect(await held(r, CHATGPT)).toBe(0);
  });

  it("of a client is ended by the client's grant change, in the change's transaction",
     async () => {
    const r = await rig();
    const stamp = await admitted(r, CHATGPT);
    await inWork(r, ({ work }) => work.add("turn", clock() + 600, TURN, stamp, clock()));
    await r.stub.grantRevoke(r.bind(null), { client: CHATGPT.client });
    expect(await states(r)).toEqual([{ state: "refused", attempts: 0, marked: 0 }]);
    expect(await held(r, CHATGPT)).toBe(0);
    const [kept, counted] = await counters(r);
    expect(kept).toEqual(counted);
  });

  it("is never run again once it may have run; an unmarked lapse runs again, a few times",
     async () => {
    const r = await rig();
    const runner = new ScriptedRunner();
    runner.answers.push("crash", "early", "early", "early");
    const t = clock();
    await inWork(r, ({ work }) => {
      work.add("turn", t - 10, { n: 1 }, null, t);
    });
    await inWork(r, ({ work }) => runWork(work, runner, () => t));
    expect(await states(r)).toEqual([{ state: "running", attempts: 1, marked: 1 }]);
    // Past its lease: marked, so unknown, and never handed to the runner again.
    await inWork(r, ({ work }) => runWork(work, runner, () => t + WORK_LEASE_S));
    expect(await states(r)).toEqual([{ state: "unknown", attempts: 1, marked: 1 }]);
    expect(runner.ran).toHaveLength(1);
    // A run that died before its mark is due again, until it has used its attempts.
    await inWork(r, ({ work }) => work.add("turn", t - 10, { n: 2 }, null, t + 1));
    for (let pass = 1; pass <= WORK_LIMITS.attempts; pass++) {
      const at = t + pass * (WORK_LEASE_S + 1);
      await inWork(r, ({ work }) => runWork(work, runner, () => at));
    }
    const last = t + (WORK_LIMITS.attempts + 1) * (WORK_LEASE_S + 1);
    await inWork(r, ({ work }) => runWork(work, runner, () => last));
    expect((await states(r))[1]).toEqual({ state: "failed", attempts: WORK_LIMITS.attempts,
                                           marked: 0 });
    expect(runner.ran).toHaveLength(1);
  });

  it("settles only from the claim that holds it", async () => {
    const r = await rig();
    const out = await inWork(r, ({ work }) => {
      const t = clock();
      const { work_id: id } = work.add("turn", t - 1, TURN, null, t);
      const claim = work.claim(id, t);
      if (claim.outcome !== "claimed") {
        throw new Error("expected a claim");
      }
      return [work.settle(id, "claim_x", { state: "done" }, t),
              work.mark(id, "claim_x"),
              work.settle(id, claim.target.claim_id, { state: "failed", reason: "boom" }, t),
              work.settle(id, claim.target.claim_id, { state: "done" }, t),
              (() => {
                try {
                  return work.settle(id, claim.target.claim_id, { state: "sent" }, t);
                } catch (error) {
                  return (error as Error).message;
                }
              })()];
    });
    expect(out).toEqual([false, false, true, false, "invalid work answer"]);
  });

  it("is bounded per owner and per client, and keeps its stamp", async () => {
    const r = await rig();
    const stamp = await admitted(r, CHATGPT);
    const out = await inWork(r, ({ work }) => {
      const tried = (made: Stamp | null, payload: unknown = TURN) => {
        try {
          work.add("turn", clock() + 60, payload, made, clock());
          return "added";
        } catch (error) {
          return (error as Error).message;
        }
      };
      return [tried(stamp), tried(stamp), tried(stamp), tried(null), tried(null), tried(null),
              tried(null, "x".repeat(WORK_LIMITS.maxPayload)), tried(null, () => 1)];
    }, { maxOpen: 4, perClient: 2 });
    const payload = "a work payload is JSON of at most 16384 characters";
    expect(out).toEqual(["added", "added", "this client's scheduled work is at its limit",
                         "added", "added", "the owner's scheduled work is at its limit",
                         payload, payload]);
    await expect(r.sql("UPDATE scheduled_work SET generation = 9"))
      .rejects.toThrow(/keeps the authority it was made under/);
    const [kept, counted] = await counters(r);
    expect(kept).toEqual(counted);
  });

  it("keeps settled rows for their retention, and at most the newest", async () => {
    const r = await rig();
    const runner = new ScriptedRunner();
    const t = clock();
    await inWork(r, async ({ work }) => {
      for (let n = 0; n < 3; n++) {
        work.add("turn", t - 1, { n }, null, t);
      }
      await runWork(work, runner, () => t);
      work.add("turn", t - 1, { n: 3 }, null, t);
      await runWork(work, runner, () => t + WORK_LIMITS.settledRetentionS + 1);
    }, { maxSettled: 2 });
    expect((await r.sql("SELECT payload FROM scheduled_work")).map((row) => row["payload"]))
      .toEqual(['{"n":3}']);
  });
});

describe("the work lane", () => {
  it("has no job for pending work without a runner, and one for a claim's lease",
     async () => {
    const r = await rig();
    await inWork(r, ({ work }) => work.add("turn", clock() - 1, TURN, null, clock()));
    await r.stub.grantRevoke(r.bind(null), { client: CHATGPT.client });  // any change syncs
    expect(await laneAt(r.stub, "work")).toBeNull();
    const t = clock();
    await inWork(r, ({ work }) => {
      const [id] = work.dueOpen(t, 1);
      expect(work.claim(id!, t).outcome).toBe("claimed");
    });
    await r.stub.grantRevoke(r.bind(null), { client: CHATGPT.client });
    expect(await laneAt(r.stub, "work")).toBe(Math.ceil((t + WORK_LEASE_S) * 1000) / 1000);
    expect(await inWork(r, ({ work }) => [work.nextDue(t, false), work.nextDue(t, true),
                                          work.nextDue(t + WORK_LEASE_S + 5, false)]))
      .toEqual([t + WORK_LEASE_S, t + WORK_LEASE_S, t + WORK_LEASE_S + 5]);
  });
});

describe("the schedule's own queries", () => {
  it("read by index, never by scanning a table", async () => {
    const r = await rig();
    const plans = await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      const plan = (query: string, ...params: SqlStorageValue[]) => state.storage.sql
        .exec<{ detail: string }>(`EXPLAIN QUERY PLAN ${query}`, ...params).toArray()
        .map((row) => row.detail).join("; ");
      const client = "1".repeat(64);
      return [
        plan("SELECT work_id FROM scheduled_work WHERE state = 'pending' AND due <= ? " +
               "ORDER BY due LIMIT ?", 1, 1),
        plan("SELECT work_id FROM scheduled_work WHERE state = 'running' AND due <= ? " +
               "ORDER BY due LIMIT ?", 1, 1),
        plan("SELECT MIN(due) AS due FROM scheduled_work WHERE state = 'running'"),
        plan("SELECT MIN(due) AS due FROM scheduled_work WHERE state = 'pending'"),
        plan("SELECT COUNT(*) AS n FROM scheduled_work WHERE client = ? AND settled_at IS NULL",
             client),
        plan("UPDATE scheduled_work SET state = 'refused' WHERE client = ? AND " +
               "state = 'pending' AND settled_at IS NULL", client),
        plan("DELETE FROM scheduled_work WHERE settled_at IS NOT NULL AND settled_at < ?", 1),
        plan("SELECT work_id FROM scheduled_work WHERE settled_at IS NOT NULL " +
               "ORDER BY settled_at LIMIT ?", 1),
        plan("SELECT client FROM outside_clients WHERE mode = 'revoked' AND agents = 0 " +
               "AND held = 0 AND revoked_at <= ? AND source = 'mcp' ORDER BY revoked_at " +
               "LIMIT ?", 1, 1),
        plan("DELETE FROM agent_cards WHERE filed_at < ?", 1),
        plan("DELETE FROM agent_cards WHERE client = ? AND family = ?", client, client),
        plan("SELECT COUNT(*) AS n FROM agent_cards WHERE client = ?", client),
        plan("SELECT * FROM agent_cards WHERE card_id = ? AND filed_at >= ?", "card_x", 1),
      ];
    });
    for (const plan of plans) {
      expect(plan).not.toMatch(/SCAN (?!.*USING)/);
    }
  });
});
