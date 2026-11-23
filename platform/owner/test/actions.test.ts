/**
 * The owner's action rules decided (R5 P-decide; d3 §N2, §N4): at a door that does an act, an
 * agent's call (a run's, or an outside client's) is held to the owner's rules on that act:
 * `never` refuses it, `ask` holds it for the owner, whose yes admits that one call once, and
 * no rule lets it be. The owner's own calls are never decided. Every decision a rule made is
 * receipted; the held calls are bounded, bound to the policy's generation, and swept.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Binding } from "../../contract/src/binding";
import { waitingPage } from "../../contract/src/identity";
import { QUEUE_ROWS } from "../../contract/src/limits";
import { DEFAULT_POLICY_JSON, POLICY_LIMITS } from "../../contract/src/policy-document";
import { ACTION_LIMITS, type ActionCard } from "../src/actions";
import { clock } from "../src/clock";
import type { OwnerDO } from "../src/owner-do";
import { QUEUE_FULL } from "../src/runs";
import { gated } from "./gateway-support";
import { DOT, MCP, MUSE, registerOn, rig, signedIn, dm } from "./ledger-support";
import { ALL_SCOPES, OPEN_RECORD, ownerRun, recorder, runBinding, runnerBinding, startOf }
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
    `held for your owner's answer: card ${held.facts?.card_id}. Your owner answers it at ` +
      `${waitingPage(held.facts!.card_id!)}: give them that link. Once they say yes, make ` +
      "this same call again"]);
  return held.facts!.card_id!;
}

/** The pending card under `card` as the owner's list shows it, or undefined once none waits. */
async function listed(o: Pick<Owner, "stub" | "owner">, card: string) {
  return (await o.stub.actionRequests(o.owner)).cards.find((each) => each.card_id === card);
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
      .toContain("an outside agent asks to workspace.files at fileMkdir: allow or deny it at " +
                 waitingPage(card));
    const shown = (await listed(o, card))!;
    expect(shown).toMatchObject({
      card_id: card, act: "workspace.files", says: "Changing the owner's files.",
      door: "fileMkdir", agent: null, client: MUSE.client,
      request: '{"expected_etag":null,"path":"/workspace/held"}', cut: false,
    } satisfies Partial<ActionCard>);
    // A client's own call resumes no run of the owner's: its client retries it.
    expect(await r.stub.actionApprove(o.session, { card_id: card, digest: shown.digest }))
      .toEqual({ card_id: card, approved: true, run: null });
    expect(await listed(o, card)).toBeUndefined();
    // The yes is that client's call alone: another client's same call is held for its own.
    expect(await heldCard(() => mkdir(r.stub, r.bind(DOT), "/workspace/held"))).not.toBe(card);
    await mkdir(r.stub, r.bind(MUSE), "/workspace/held");
    // Once: the same call again is held anew.
    expect(await heldCard(() => mkdir(r.stub, r.bind(MUSE), "/workspace/held")))
      .not.toBe(card);
    expect(await outcomes(o)).toEqual(["held", "admitted", "held", "approved", "held"]);
  });

  it("is answered only as the pending card shown, a yes in the owner's session, under the " +
     "policy it was asked under", async () => {
    const { r, o } = await ledgerOwner();
    await ruled(o, [{ act: "workspace.files", do: "ask" }]);
    const ask = () => heldCard(() => mkdir(r.stub, r.bind(MUSE), "/workspace/asked"));
    const card = await ask();
    const { digest } = (await listed(o, card))!;
    const wrong = { card_id: card, digest: "sha256:" + "0".repeat(64) };
    for (const [binding, answer, reason] of [
      [o.session, wrong, "action request is not the pending card shown"],
      [o.session, { card_id: "act_" + "0".repeat(32), digest },
        "action request is not the pending card shown"],
      [o.owner, { card_id: card, digest }, "interactive owner session required"],
      [r.bind(MUSE), { card_id: card, digest },
        "only the owner may answer its agents' held calls"],
    ] as const) {
      expect((await failure(() => r.stub.actionApprove(binding, answer))).message).toBe(reason);
    }
    for (const [binding, answer, reason] of [
      [o.owner, wrong, "action request is not the pending card shown"],
      [r.bind(MUSE), { card_id: card, digest },
        "only the owner may answer its agents' held calls"],
    ] as const) {
      expect((await failure(() => r.stub.actionRefuse(binding, answer))).message).toBe(reason);
    }
    expect((await failure(() => r.stub.actionRequests(r.bind(MUSE)))).message)
      .toBe("only the owner may read its agents' held calls");
    // The owner's no, a narrowing, needs no session: the card goes, and the call asked again
    // is held anew.
    expect(await r.stub.actionRefuse(o.owner, { card_id: card, digest }))
      .toEqual({ card_id: card, approved: false });
    const again = await ask();
    expect(again).not.toBe(card);
    // The rules written again: the card asked under the old ones is neither shown nor
    // answered, and the call is decided afresh.
    const shown = (await listed(o, again))!;
    await ruled(o, [{ act: "workspace.files", do: "ask" }]);
    expect(await listed(o, again)).toBeUndefined();
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
    expect((await listed(o, card))!)
      .toMatchObject({ act: "share", door: "collectionWrite" });
  });
});

describe("the held calls", () => {
  it("wait whatever their age, and at perClient a client's or held in all the oldest goes",
     async () => {
    const { r, o } = await ledgerOwner();
    await ruled(o, [{ act: "workspace.files", do: "ask" }]);
    const dots = await heldCard(() => mkdir(r.stub, r.bind(DOT), "/workspace/dot"));
    const muses: string[] = [];
    for (let n = 0; n < ACTION_LIMITS.perClient; n++) {
      muses.push(await heldCard(() => mkdir(r.stub, r.bind(MUSE), `/workspace/p-${n}`)));
    }
    // Past MUSE's share, its own oldest goes, never another's older one.
    const more = await heldCard(() => mkdir(r.stub, r.bind(MUSE), "/workspace/one-more"));
    expect(await listed(o, muses[0]!)).toBeUndefined();
    expect((await listed(o, dots))!.card_id).toBe(dots);
    // The rest of the set, by the owner's agents' runs, to its bound and one past it, which
    // lets their own oldest go, never an outside client's.
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
      return Array.from({ length: runs + 1 }, (_, n) => asked(n));
    })).toEqual(Array(runs + 1).fill("held for your owner's answer"));
    expect(await r.table("action_requests")).toHaveLength(ACTION_LIMITS.held);
    expect((await listed(o, dots))!.card_id).toBe(dots);
    // An outside client's call at the full set lets the oldest outside one go: DOT's.
    await heldCard(() => mkdir(r.stub, r.bind(DOT), "/workspace/dot-2"));
    expect(await listed(o, dots)).toBeUndefined();
    // Each eviction is told, once an asker, naming the asker and never the call.
    const told = (await r.table("owner_notices"))
      .filter((row) => row["type"] === "held_request_evicted").map((row) => row["detail"]);
    expect([told.length, told.some((detail) => String(detail).includes("workspace"))])
      .toEqual([3, false]);
    // A card and a yes have no life of their own: years on, the card is shown and answered,
    // and the yes admits its call once.
    const later = clock() + 10 * 365 * 86_400;
    await atClock(later, async () => {
      expect(await heldCard(() => mkdir(r.stub, r.bind(DOT), "/workspace/dot"))).not.toBe(dots);
      const shown = (await listed(o, more))!;
      expect("expires_at" in shown).toBe(false);
      await r.stub.actionApprove({ ...o.session, session: (await r.stub.sessionOpen({
        ...o.owner, authenticated_at: Math.floor(later) })).session },
                                 { card_id: more, digest: shown.digest });
    });
    await atClock(later + 10 * 365 * 86_400, async () => {
      await mkdir(r.stub, r.bind(MUSE), "/workspace/one-more");
      expect(await heldCard(() => mkdir(r.stub, r.bind(MUSE), "/workspace/one-more")))
        .not.toBe(more);
    });
  });

  it("refuse an outside client's call when the full set holds only the owner's agents'",
     async () => {
    const { r, o } = await ledgerOwner();
    await ruled(o, [{ act: "workspace.files", do: "ask" }]);
    await inStores(r.stub, ({ actions }) => {
      for (let n = 0; n < ACTION_LIMITS.held; n++) {
        expect(() => actions.decide("workspace.files", { agent: "main", client: null, run: null },
                                    { door: "fileMkdir", request: { n } }, clock()))
          .toThrow("held for your owner's answer");
      }
    });
    expect((await failure(() => mkdir(r.stub, r.bind(MUSE), "/workspace/m"))).message)
      .toBe("too many calls held for the owner");
    expect(await r.table("action_requests")).toHaveLength(ACTION_LIMITS.held);
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
    await post(dm(main), "to-owner");
    const card = await heldCard(() => post(dm(main, muse), "to-muse"));
    expect((await listed(o, card))!)
      .toMatchObject({ act: "people.message", door: "roomPost", agent: "main", client: null });
  });
});

describe("a run's CLI's own tool call, at its hook's decide door (R5 P-hooks)", () => {
  /** The owner's run of `main` on `engine`, and its decide door's binding. */
  async function hooked(engine = "claude-code") {
    const r = await gated();
    const o = { stub: r.stub, owner: r.bind(), session: r.session() };
    const { start } = await ownerRun(r.stub, r.bind(), {
      engine, credential: "vendor_login_in_sandbox", scopes: ALL_SCOPES });
    return { r, o, run: runBinding(r, start) };
  }

  it("names the tools the hook matches for the owner's rules, as its CLI names them", async () => {
    const { r, o, run } = await hooked();
    expect(await r.stub.runDecide(run, { check: true })).toEqual({ tools: [], generation: 1 });
    await ruled(o, [{ act: "workspace.shell", do: "ask" },
                    { act: "people.message", do: "never", agent: "other",
                      tools: ["mcp:gmail:send_*", "mcp:my-mail:post.v2"] }]);
    // Every rule's tools, any agent's: the decision holds each rule to its own agent.
    expect(await r.stub.runDecide(run, { check: true })).toEqual({ tools: [
      "Bash", "Monitor", "PowerShell", "mcp__gmail__send_.*", "mcp__my.mail__post.v2"].sort(),
      generation: 2 });
    const gemini = await hooked("gemini");
    await ruled(gemini.o, [{ act: "people.message", do: "ask", tools: ["mcp:gmail:send_*"] }]);
    expect(await gemini.r.stub.runDecide(gemini.run, { check: true }))
      .toEqual({ tools: ["mcp_gmail_send_.*"], generation: 2 });
  });

  it("decides the call as each act it is, every never first, at the tool as its door",
     async () => {
    const { r, o, run } = await hooked("gemini");
    const call = (tool_name: string, tool_input: unknown) =>
      r.stub.runDecide(run, { tool_name, tool_input });
    expect(await call("run_shell_command", { command: "ls" })).toEqual({ allow: true });
    await ruled(o, [{ act: "workspace.shell", do: "ask" },
                    { act: "people.message", do: "never", tools: ["mcp:gmail:send_*"] }]);
    const card = await heldCard(() => call("run_shell_command", { command: "curl x.test" }));
    expect((await listed(o, card))!).toMatchObject({
      act: "workspace.shell", door: "run_shell_command", agent: "main",
      request: JSON.stringify({ command: "curl x.test" }) });
    expect((await failure(() => call("mcp_gmail_send_mail", { to: "a@b.c" }))).code)
      .toBe("action_never");
    expect(await call("read_file", { path: "/workspace/a" })).toEqual({ allow: true });
    expect(await call("mcp_gmail_read", {})).toEqual({ allow: true });
    // A tool two rules name is both acts: the never refuses it, and no card is filed.
    await ruled(o, [{ act: "share", do: "ask", tools: ["mcp:board:post"] },
                    { act: "people.message", do: "never", tools: ["mcp:board:post"] }]);
    expect((await failure(() => call("mcp_board_post", {}))).code).toBe("action_never");
    expect(await outcomes(o)).toEqual(["denied", "denied", "held"]);
  });

  it("files one card for a call two asked acts name, whose one yes admits it", async () => {
    const { r, o, run } = await hooked("gemini");
    const call = () => r.stub.runDecide(run, { tool_name: "mcp_board_post", tool_input: {} });
    await ruled(o, [{ act: "share", do: "ask", tools: ["mcp:board:post"] },
                    { act: "people.message", do: "ask", tools: ["mcp:board:post"] }]);
    const card = await heldCard(call);
    expect(await heldCard(call)).toBe(card);
    const { digest } = (await listed(o, card))!;
    await r.stub.actionApprove(o.session, { card_id: card, digest });
    expect(await call()).toEqual({ allow: true });
    expect(await outcomes(o)).toEqual(["admitted", "approved", "held"]);
  });

  it("answers Claude Code's own post in its own words: {} lets be, its deny refuses",
     async () => {
    const { r, o, run } = await hooked();
    const call = (tool_name: string) => r.stub.runDecide(run, { tool_name, tool_input: {} });
    expect(await call("Bash")).toEqual({ hook: {} });
    await ruled(o, [{ act: "workspace.shell", do: "never" },
                    { act: "workspace.files", do: "ask" }]);
    const denied = (reason: string | RegExp) => ({ hook: { hookSpecificOutput: {
      hookEventName: "PreToolUse", permissionDecision: "deny",
      permissionDecisionReason: typeof reason === "string" ? reason
        : expect.stringMatching(reason) } } });
    expect(await call("Bash"))
      .toEqual(denied("the owner's rule never lets an agent do this (workspace.shell)"));
    expect(await call("Write")).toEqual(denied(/^held for your owner's answer: card act_/));
    expect(await call("Read")).toEqual({ hook: {} });
  });

  it("is a run's alone, and a call with a tool's name", async () => {
    const { r, run } = await hooked();
    const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
    for (const binding of [r.bind(), client]) {
      expect((await failure(() => r.stub.runDecide(binding, { check: true }))).message)
        .toBe("only a run's own hook asks its decide door");
    }
    for (const asked of [{}, { tool_name: "" }, { tool_name: 7 }, { tool_name: "a b" }]) {
      expect((await failure(() => r.stub.runDecide(run, asked as never))).code)
        .toBe("invalid");
    }
  });
});

describe("the honest note (d3 §N5)", () => {
  it("says beside each rule where it holds and where it can't", async () => {
    const { o } = await ledgerOwner();
    await ruled(o, [{ act: "workspace.shell", do: "ask" },
                    { act: "people.message", do: "never", tools: ["mcp:gmail:send_*"] },
                    { act: "workspace.workflows", do: "ask" }]);
    // Codex's hook is written, but the agent can alter what it answers (its record says how).
    const unheld = ["main_agent", "sandbox_programs", "codex", "gemini.fails_open"];
    expect((await o.stub.policyRead(o.owner)).notes).toEqual([
      // A shell rule holds over the commands the agent's own definition starts too, at its
      // run's admission, whatever its engine, Codex's included.
      { holds_at: ["definitions", "claude-code", "gemini", "pi"], not_enforced: unheld },
      { holds_at: ["doors", "claude-code", "gemini", "pi"], not_enforced: unheld },
      { holds_at: ["doors"], not_enforced: [] },
    ]);
  });

  it("says where the agents' own hooks run, and where they are not held (D5)", async () => {
    const { o } = await ledgerOwner();
    // Codex runs its dispatcher through a login shell the agent writes; where an engine's
    // dispatcher cannot start, the call goes on; a main agent runs none.
    expect((await o.stub.policyRead(o.owner)).hooks).toEqual({
      holds_at: ["claude-code", "gemini", "pi"],
      not_enforced: ["main_agent", "codex", "gemini.fails_open"] });
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
    const { digest } = (await listed(o, card))!;
    return { r, o, run, card, digest };
  }

  it("is bound to its conversation, which the owner's yes resumes, the call then admitted once",
     async () => {
    const { r, o, run, card, digest } = await asking();
    expect((await listed(o, card))!.conversation)
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
      `Your owner said yes to card ${card} (workspace.files at fileMkdir), once. If it was a ` +
        "call of yours, make that same call again now and it goes through; if you made it " +
        "since, do not make it again. If it was a command of your own definition, it runs in " +
        "this run."]);
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
    expect(((await listed(o, card))!).digest).toBe(digest);
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
    const asked = await r.stub.agentSubmit({ ...client, control: OPEN_RECORD },
                                           { message: "hi", client_send_id: "c-1" });
    const start = await startOf(r.stub, asked.run_id);
    await ruled(o, [{ act: "workspace.files", do: "ask" }]);
    const card = await heldCard(() => mkdir(r.stub, runBinding(r, start), "/workspace/c"));
    await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
    const { digest } = (await listed(o, card))!;
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
