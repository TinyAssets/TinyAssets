/**
 * The owner's action rules decided (R5 P-decide; d3 §N2, §N4): at a door that does an act, an
 * agent's call (a run's, or an outside client's) is held to the owner's rules on that act:
 * `never` refuses it, `ask` holds it for the owner, whose yes admits that one call once, and
 * no rule lets it be. The owner's own calls are never decided. Every decision a rule made is
 * receipted; the held calls are bounded, bound to the policy's generation, and swept.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Binding } from "../../contract/src/binding";
import { QUEUE_ROWS } from "../../contract/src/limits";
import { DEFAULT_POLICY_JSON, POLICY_LIMITS } from "../../contract/src/policy-document";
import { ACTION_LIMITS, type ActionCard } from "../src/actions";
import { clock } from "../src/clock";
import type { OwnerDO } from "../src/owner-do";
import { QUEUE_FULL } from "../src/runs";
import { gated } from "./gateway-support";
import { DOT, MCP, MUSE, registerOn, rig, signedIn } from "./ledger-support";
import { ALL_SCOPES, ownerRun, recorder, runBinding, runnerBinding, startOf, TIERS }
  from "./runs-support";
import { atClock, drainDue, failure, inStores } from "./support";

afterEach(() => {
  vi.useRealTimers();
});

type Stub = DurableObjectStub<OwnerDO>;
/** What sets an owner's rules: its object, its own binding and its session's. */
type Owner = { readonly stub: Stub; readonly owner: Binding; readonly session: Binding };

/** The owner's action rules set to `actions`, in its session. */
async function ruled(o: Owner, actions: readonly object[]): Promise<void> {
  const { generation } = await o.stub.policyRead(o.owner);
  await o.stub.policyWrite(o.session, { expected_generation: generation,
                                        policy: { ...JSON.parse(DEFAULT_POLICY_JSON), actions } });
}

/** A directory made by `binding`'s caller: an act of `workspace.files`. */
function mkdir(stub: Stub, binding: Binding, path: string) {
  return stub.fileMkdir(binding, { path, expected_etag: null });
}

/** The card `call` was held under, from its refusal's facts. */
async function heldCard(call: () => Promise<unknown>): Promise<string> {
  const held = await failure(call) as Error & { code?: string; facts?: { card_id?: string } };
  expect([held.code, held.message]).toEqual(["held_for_owner",
    `held for your owner's answer: card ${held.facts?.card_id}. The owner answers it in the ` +
      "TinyAssets app; once they say yes, make this same call again"]);
  return held.facts!.card_id!;
}

async function ledgerOwner() {
  const r = await rig();
  return { r, o: { stub: r.stub, owner: r.bind(null), session: r.session(null) } };
}

/** The outcomes of the owner's action receipts, newest first. */
async function outcomes(o: Owner): Promise<string[]> {
  return (await o.stub.policyReceipts(o.owner)).actions.map((receipt) => receipt.outcome);
}

describe("an agent's call of an act", () => {
  it("is let be while no rule names its act, and the owner's own call is never decided",
     async () => {
    const { r, o } = await ledgerOwner();
    await mkdir(r.stub, r.bind(MUSE), "/workspace/free");
    await ruled(o, [{ act: "workspace.files", do: "never" }]);
    await mkdir(r.stub, o.owner, "/workspace/owners");
    const never = await failure(() => mkdir(r.stub, r.bind(MUSE), "/workspace/never"));
    expect([never.code, never.message]).toEqual(["action_never",
      "the owner's rule never lets an agent do this (workspace.files)"]);
    const [receipt] = (await r.stub.policyReceipts(o.owner)).actions;
    expect(receipt).toMatchObject({ act: "workspace.files", decision: "never",
                                    outcome: "denied", card_id: null, agent: null,
                                    client: MUSE.client });
  });

  it("is held for the owner's yes, which admits that call once", async () => {
    const { r, o } = await ledgerOwner();
    await ruled(o, [{ act: "workspace.files", do: "ask" }]);
    const card = await heldCard(() => mkdir(r.stub, r.bind(MUSE), "/workspace/held"));
    // Asked again, the same card: nothing new is filed.
    expect(await heldCard(() => mkdir(r.stub, r.bind(MUSE), "/workspace/held"))).toBe(card);
    expect((await r.stub.noticeList(o.owner)).notices.map((notice) => notice.detail))
      .toContain(`an outside agent asks to workspace.files at fileMkdir: card ${card}`);
    const shown = await r.stub.actionRequest(o.owner, { card_id: card });
    expect(shown.card).toMatchObject({
      card_id: card, act: "workspace.files", says: "Changing the owner's files.",
      door: "fileMkdir", agent: null, client: MUSE.client,
      request: '{"expected_etag":null,"path":"/workspace/held"}', cut: false,
    } satisfies Partial<ActionCard>);
    // A client's own call resumes no run of the owner's: its client retries it.
    expect(await r.stub.actionApprove(o.session, { card_id: card, digest: shown.digest }))
      .toEqual({ card_id: card, approved: true, run: null });
    expect((await failure(() => r.stub.actionRequest(o.owner, { card_id: card }))).message)
      .toBe("no pending action request for this card");
    // The yes is that client's call alone: another client's same call is held for its own.
    expect(await heldCard(() => mkdir(r.stub, r.bind(DOT), "/workspace/held"))).not.toBe(card);
    await mkdir(r.stub, r.bind(MUSE), "/workspace/held");
    // Once: the same call again is held anew.
    expect(await heldCard(() => mkdir(r.stub, r.bind(MUSE), "/workspace/held")))
      .not.toBe(card);
    expect(await outcomes(o)).toEqual(["held", "admitted", "held", "approved", "held"]);
  });

  it("is answered only as the pending card shown, in the owner's session, under the policy " +
     "it was asked under", async () => {
    const { r, o } = await ledgerOwner();
    await ruled(o, [{ act: "workspace.files", do: "ask" }]);
    const ask = () => heldCard(() => mkdir(r.stub, r.bind(MUSE), "/workspace/asked"));
    const card = await ask();
    const { digest } = await r.stub.actionRequest(o.owner, { card_id: card });
    for (const [binding, answer, reason] of [
      [o.session, { card_id: card, digest: "sha256:" + "0".repeat(64) },
        "action request is not the pending card shown"],
      [o.session, { card_id: "act_" + "0".repeat(32), digest },
        "action request is not the pending card shown"],
      [o.owner, { card_id: card, digest }, "interactive owner session required"],
      [r.bind(MUSE), { card_id: card, digest },
        "only the owner may answer its agents' held calls"],
    ] as const) {
      expect((await failure(() => r.stub.actionApprove(binding, answer))).message).toBe(reason);
    }
    // The owner's no: the card goes, and the call asked again is held anew.
    expect(await r.stub.actionRefuse(o.session, { card_id: card, digest }))
      .toEqual({ card_id: card, approved: false });
    const again = await ask();
    expect(again).not.toBe(card);
    // The rules written again: the card asked under the old ones is neither shown nor
    // answered, and the call is decided afresh.
    const shown = await r.stub.actionRequest(o.owner, { card_id: again });
    await ruled(o, [{ act: "workspace.files", do: "ask" }]);
    expect((await failure(() => r.stub.actionRequest(o.owner, { card_id: again }))).message)
      .toBe("no pending action request for this card");
    expect((await failure(() => r.stub.actionApprove(o.session, { card_id: again,
                                                                  digest: shown.digest })))
      .message).toBe("action request is not the pending card shown");
    expect(await ask()).not.toBe(again);
    expect(await outcomes(o)).toEqual(["held", "held", "refused", "held"]);
  });

  it("is a share only as a public row, and held so", async () => {
    const { r, o } = await ledgerOwner();
    await ruled(o, [{ act: "share", do: "ask" }]);
    const row = (row_id: string, isPrivate: boolean) => r.stub.collectionWrite(r.bind(MUSE), {
      collection: "board", row_id, body: "{}", expected_etag: "", private: isPrivate });
    await row("mine", true);
    const card = await heldCard(() => row("public", false));
    expect((await r.stub.actionRequest(o.owner, { card_id: card })).card)
      .toMatchObject({ act: "share", door: "collectionWrite" });
  });
});

describe("the held calls", () => {
  it("are at most perClient a client's and held in all, each living cardTtlS, a yes " +
     "approvalTtlS", async () => {
    const { r, o } = await ledgerOwner();
    await ruled(o, [{ act: "workspace.files", do: "ask" }]);
    const muses: string[] = [];
    for (let n = 0; n < ACTION_LIMITS.perClient; n++) {
      muses.push(await heldCard(() => mkdir(r.stub, r.bind(MUSE), `/workspace/p-${n}`)));
    }
    expect((await failure(() => mkdir(r.stub, r.bind(MUSE), "/workspace/one-more"))).message)
      .toBe("this client's calls held for the owner are at their limit");
    const dots = await heldCard(() => mkdir(r.stub, r.bind(DOT), "/workspace/dot"));
    // The rest of the set, by the owner's agents' runs, to its bound.
    const runs = ACTION_LIMITS.held - ACTION_LIMITS.perClient - 1;
    expect(await inStores(r.stub, ({ actions }) => {
      const asked = (n: number) => {
        try {
          actions.decide("workspace.files", { agent: "main", client: null, run: null },
                         { door: "fileMkdir", request: { n } }, clock());
          return "allowed";
        } catch (error) {
          return (error as Error).message.split(":")[0];
        }
      };
      return [...Array.from({ length: runs }, (_, n) => asked(n)), asked(runs)];
    })).toEqual([...Array(runs).fill("held for your owner's answer"),
                 "too many calls held for the owner"]);
    // Past a card's life, every pending card is swept at the next decision.
    const later = clock() + ACTION_LIMITS.cardTtlS + 1;
    await atClock(later, async () => {
      const fresh = await heldCard(() => mkdir(r.stub, r.bind(DOT), "/workspace/dot"));
      expect(fresh).not.toBe(dots);
      expect((await failure(() => r.stub.actionRequest(o.owner, { card_id: muses[0]! })))
        .message).toBe("no pending action request for this card");
      await heldCard(() => mkdir(r.stub, r.bind(MUSE), "/workspace/one-more"));
      const shown = await r.stub.actionRequest(o.owner, { card_id: fresh });
      // A yes past its life admits nothing: the call is held anew.
      await r.stub.actionApprove({ ...o.session, session: (await r.stub.sessionOpen({
        ...o.owner, authenticated_at: Math.floor(later) })).session },
                                 { card_id: fresh, digest: shown.digest });
    });
    await atClock(later + ACTION_LIMITS.approvalTtlS + 1, async () => {
      await heldCard(() => mkdir(r.stub, r.bind(DOT), "/workspace/dot"));
    });
  });

  it("keep their receipts to the newest receiptsPerBucket, and list the last receiptsListed",
     async () => {
    const { r, o } = await ledgerOwner();
    await ruled(o, [{ act: "workspace.files", do: "never" }]);
    await inStores(r.stub, ({ actions }) => {
      for (let n = 0; n <= POLICY_LIMITS.receiptsPerBucket; n++) {
        try {
          actions.decide("workspace.files", { agent: null, client: MUSE.client, run: null },
                         { door: "fileMkdir", request: { n } }, clock());
        } catch {
          // each call refused `action_never`, receipted
        }
      }
    });
    expect(await r.sql("SELECT COUNT(*) AS rows, MIN(seq) AS oldest FROM action_receipts"))
      .toEqual([{ rows: POLICY_LIMITS.receiptsPerBucket, oldest: 2 }]);
    expect((await r.stub.policyReceipts(o.owner)).actions.length)
      .toBe(POLICY_LIMITS.receiptsListed);
  });
});

describe("a run's call", () => {
  it("is held to a rule naming its agent, and a post is a message only to an outside agent",
     async () => {
    const r = await gated();
    const o = { stub: r.stub, owner: r.bind(), session: r.session() };
    const { start } = await ownerRun(r.stub, r.bind(), {
      engine: "claude-code", credential: "vendor_login_in_sandbox", scopes: ALL_SCOPES });
    const run = runBinding(r, start);
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    // A rule on another agent holds neither this run nor an outside client.
    await ruled(o, [{ act: "workspace.files", do: "never", agent: "other" }]);
    await mkdir(r.stub, run, "/workspace/by-main");
    await mkdir(r.stub, client, "/workspace/by-muse");
    await ruled(o, [{ act: "workspace.files", do: "never", agent: "main" }]);
    expect((await failure(() => mkdir(r.stub, run, "/workspace/main-again"))).code)
      .toBe("action_never");
    await mkdir(r.stub, client, "/workspace/muse-again");
    // A message to another party: the run's post to an outside agent, never to the owner.
    const muse = await inStores(r.stub, (stores) => registerOn(stores, MUSE, {
      command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
      recipe_id: null }).agent_id);
    const main = (await r.stub.outsideAgents(r.bind())).owner_agents
      .find((agent) => agent.agent === "main")!.agent_id;
    await ruled(o, [{ act: "people.message", do: "ask", agent: "main" }]);
    const post = (room: string, cmid: string) => r.stub.roomPost(run, {
      room_id: room, client_message_id: cmid, body: "hello", parent_id: null });
    await post(`dm:${main}`, "to-owner");
    const card = await heldCard(() => post(`dm:${[main, muse].sort().join("+")}`, "to-muse"));
    expect((await r.stub.actionRequest(o.owner, { card_id: card })).card)
      .toMatchObject({ act: "people.message", door: "roomPost", agent: "main", client: null });
  });
});

describe("a run's held call (R5 P-resume)", () => {
  /** The owner's run of `main` on Claude Code, its file changes asked first, held and ended. */
  async function asking() {
    const r = await gated();
    const o = { stub: r.stub, owner: r.bind(), session: r.session() };
    const { run, start } = await ownerRun(r.stub, r.bind(), {
      engine: "claude-code", credential: "vendor_login_in_sandbox", scopes: ALL_SCOPES });
    await ruled(o, [{ act: "workspace.files", do: "ask", agent: "main" }]);
    const card = await heldCard(() => mkdir(r.stub, runBinding(r, start), "/workspace/held"));
    // The run goes on and ends; its engine's session is the conversation's.
    await r.stub.runEvents(runnerBinding(r, start), { frames: [{ seq: 1, at: 1, event: {
      type: "system", subtype: "init", session_id: "sess-held" } }], end: { exit_code: 0 } });
    const { digest } = await r.stub.actionRequest(o.owner, { card_id: card });
    return { r, o, run, card, digest };
  }

  it("is bound to its conversation, which the owner's yes resumes, the call then admitted once",
     async () => {
    const { r, o, run, card, digest } = await asking();
    expect((await r.stub.actionRequest(o.owner, { card_id: card })).card.conversation)
      .toBe(run.conversation_id);
    const approved = await r.stub.actionApprove(o.session, { card_id: card, digest });
    expect(approved).toEqual({ card_id: card, approved: true, run: expect.any(String) });
    // The same call from another of the agent's conversations is not the one approved.
    const other = await ownerRun(r.stub, r.bind());
    expect(await heldCard(() => mkdir(r.stub, runBinding(r, other.start), "/workspace/held")))
      .not.toBe(card);
    await drainDue(r.stub);
    const resumed = await startOf(r.stub, approved.run!);
    expect([resumed.conversation, resumed.session_id, resumed.agent, resumed.prompt]).toEqual([
      run.conversation_id, "sess-held", "main",
      `Your owner said yes to card ${card} (workspace.files at fileMkdir). Make that same ` +
        "call again now and it goes through, once. If you made it since, do not make it again."]);
    await mkdir(r.stub, runBinding(r, resumed), "/workspace/held");
    expect(await heldCard(() => mkdir(r.stub, runBinding(r, resumed), "/workspace/held")))
      .not.toBe(card);
    expect(await outcomes(o)).toEqual(["held", "admitted", "held", "approved", "held"]);
  });

  it("writes no yes when its resume is refused for the inputs' queue", async () => {
    const { r, o, card, digest } = await asking();
    await r.sql("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n " +
                  `WHERE i < ${QUEUE_ROWS}) INSERT INTO run_inputs (run, prompt, bytes) ` +
                  "SELECT 'run_' || printf('%032x', i), 'x', 1 FROM n");
    expect((await failure(() => r.stub.actionApprove(o.session, { card_id: card, digest })))
      .message).toBe(QUEUE_FULL);
    // The card is still pending, and its call still held, not admitted.
    expect((await r.stub.actionRequest(o.owner, { card_id: card })).digest).toBe(digest);
    expect(await outcomes(o)).toEqual(["held"]);
  });

  it("resumes nothing on the owner's no", async () => {
    const { r, o, card, digest } = await asking();
    const starts = (await recorder(r.stub)).starts.length;
    expect(await r.stub.actionRefuse(o.session, { card_id: card, digest }))
      .toEqual({ card_id: card, approved: false });
    await drainDue(r.stub);
    expect((await recorder(r.stub)).starts).toHaveLength(starts);
    expect((await r.stub.runList(o.owner, {})).runs.map((listed) => listed.state))
      .toEqual(["succeeded"]);
  });

  it("is resumed under the run's own authority: an outside client's run as that client's",
     async () => {
    const r = await gated();
    const o = { stub: r.stub, owner: r.bind(), session: r.session() };
    await ownerRun(r.stub, r.bind(), {
      engine: "claude-code", credential: "vendor_login_in_sandbox", scopes: ALL_SCOPES });
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    await r.stub.outsideAgents(client);
    const { revision } = (await r.stub.grantList(r.bind())).clients
      .find((entry) => entry.client === MUSE.client)!;
    await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "scoped",
      scopes: ["converse", "write_files"].map((capability) => ({
        universe: "alpha", agent: "main", capability })),
      expected_revision: revision, name: "Muse" });
    const { card_id: registration, digest: shown } = await r.stub.registerPropose(client, {
      command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
      recipe_id: null });
    await r.stub.registerAgent(r.session(), { card_id: registration, digest: shown });
    const asked = await r.stub.agentSubmit({ ...client, tiers: TIERS },
                                           { message: "hi", client_send_id: "c-1" });
    const start = await startOf(r.stub, asked.run_id);
    await ruled(o, [{ act: "workspace.files", do: "ask" }]);
    const card = await heldCard(() => mkdir(r.stub, runBinding(r, start), "/workspace/c"));
    await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
    const { digest } = await r.stub.actionRequest(o.owner, { card_id: card });
    const { run } = await r.stub.actionApprove(o.session, { card_id: card, digest });
    await drainDue(r.stub);
    const resumed = await startOf(r.stub, run!);
    expect([resumed.authority, resumed.conversation, resumed.unattended])
      .toEqual([MUSE.client, start.conversation, start.unattended]);
    expect(await r.stub.agentStatus(client, { run_id: run! }))
      .toMatchObject({ conversation_id: start.conversation, initiator: "client" });
    await mkdir(r.stub, runBinding(r, resumed), "/workspace/c");
  });
});
