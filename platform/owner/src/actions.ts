/**
 * The owner's action rules decided (R5 slice 2; d3 §N2, §N4; `r5_research.md` §3, §5.2): the
 * one decision, the calls it holds for the owner, the owner's answers and their receipts.
 *
 * - **The decision** (`decide`) is of an agent's call: a run's (its agent, and the client that
 *   started it, if one did) or an outside client's (`decidedActor`). The owner itself is never
 *   decided: the rules are the owner's on its agents. The rules on the call's act that hold
 *   for its actor are read from the policy at the call (`ruleOn`: a rule naming an `agent`
 *   holds for that agent's runs alone), and the strictest wins: `never` refuses
 *   (`action_never`), `ask` holds the call for the owner (`held_for_owner`, the card's id in
 *   the refusal's facts) unless the owner said yes to this very call, and no rule lets it be.
 * - **A held call is one row of `action_requests`**: its card, what the owner is shown (the
 *   act, the door, the actor, its conversation and the request as asked, cut to
 *   `shownChars`), the card's digest, which the owner's answer names, and the call's, which a
 *   yes admits once: the door, the request as asked (its bytes by their digest), the actor
 *   and, for a run's call, its conversation. The same call held again answers the same card.
 *   A yes keeps the row as the call's once-approval, which its one use deletes; a no deletes
 *   it. Neither a card nor a yes has a life of its own (Fact 15): each waits as a held
 *   approval waits in its conversation on Claude, ChatGPT or an agents SDK's run state.
 * - **The owner answers a card on its app's page** (`waitingPage`), which the refusal hands
 *   the agent to give its user and the `action_request` notice names: the app lists the
 *   pending cards (`list`) with every other card waiting on the owner. A yes is a widening,
 *   in the owner's session; a no deletes a card and widens nothing, so it needs none.
 * - **A run's CLI's own tool calls are decided too** (R5 P-hooks): its pre-tool hook, where its
 *   engine's record has one (`schema.ts` `Decide`), asks the decide door of each call it
 *   matches (`runDecide`). A call is each act whose hook kinds name the tool (`acts.json`) or
 *   one of whose rules names it (`tools`), decided as a door's call of that act is, its door
 *   the tool's name and its request the tool's input: every act a rule never lets be first,
 *   then one act asked, so one card and its one yes admit the call however many acts ask.
 *   Which tools the hook matches is the owner's rules' (`hookTools`), for every agent: the
 *   decision holds each rule to its own agent.
 * - **So are the commands a run's own definition starts** (`decideCommand`): its `mcp.json`
 *   servers' and its `hooks.json` hooks', which no tool call starts, decided at the run's
 *   admission as a tool call of their kind (`COMMAND_KIND`) is; the admission leaves out each
 *   one a rule holds.
 * - **The honest note** (d3 §N5, `ruleNotes`): each rule's places it holds and can't, which
 *   `policyRead` answers beside the rules.
 * - **A yes to a run's call resumes its conversation** (R5 P-resume): the approval writes a
 *   run waiting there (`Runs.resume`) under the held run's authority, on an input naming the
 *   card, so the agent makes the call again and the approval admits it. A main agent's
 *   call is retried when its user says so.
 * - **Generation-bound**: a row asked under another policy generation is neither shown,
 *   answered nor admitted, so a change of the owner's rules decides every held call afresh.
 * - **Bounded and swept**: `held` rows in all, `perClient` of them one outside client's; a
 *   call held at a bound lets the oldest row go to make its room (the client's own at its
 *   share, else the oldest of its class's: an outside client's never pushes out the owner's
 *   agents'), refused only when its class has none. At each decision and each answer, rows
 *   of another generation go first.
 * - **Receipted** in a bucket of their own, `action_receipts`, kept to the policy's
 *   `receiptsPerBucket` as its other buckets are, only where a rule decided: `denied`, `held`
 *   (once a card), `admitted`, and the owner's `approved` and `refused`. A call no rule
 *   decides writes nothing.
 *
 * Every change runs in one `transactionSync`; a refusal is thrown once what it receipts is
 * written, in the caller's unit, so the card and its receipt stand.
 */
import { actNamed, COMMAND_KIND, toolPatterns } from "../../contract/src/acts";
import type { Origin } from "../../contract/src/binding";
import { canonical } from "../../contract/src/canonical";
import { actDoors } from "../../contract/src/doors";
import { sha256Hex } from "../../contract/src/digest";
import { waitingPage } from "../../contract/src/identity";
import { randomHex } from "../../contract/src/ids";
import {
  type ActionRule, policyDigest, type PolicyDocument, POLICY_LIMITS,
} from "../../contract/src/policy-document";
import { Refusal, refusalOf } from "../../contract/src/refusal";
import { served, servedRecord } from "../../sources/src/served";
import type { Decide } from "../../sources/src/schema";
import { clock } from "./clock";
import { type Account, type Call, type LiveSession, proven } from "./door";
import type { OwnerStorage } from "./gate";
import type { Notices } from "./notices";

/**
 * How many calls wait: 200 rows in all, pending or approved, and 50 of them one outside
 * client's, so no client's asks push out the set the owner's own agents ask in; each card
 * shows at most 8,000 code points of its request.
 */
interface ActionLimits {
  readonly held: number;
  readonly perClient: number;
  readonly shownChars: number;
}

export const ACTION_LIMITS: ActionLimits = Object.freeze({
  held: 200,
  perClient: 50,
  shownChars: 8_000,
});

/** Who a decided call is: one of the owner's agents' runs, or an outside client. */
export interface Actor {
  /** The agent a run acts as; null for an outside client's own call. */
  readonly agent: string | null;
  /** The outside client the call is under: its own, or the one that started the run. */
  readonly client: string | null;
  /**
   * A run's call: the run, its conversation (which the call's approval is bound to and
   * resumes) and its origin's family (what the resume runs under); null for an outside
   * client's own call.
   */
  readonly run: { readonly id: string; readonly conversation: string;
                  readonly family: string | null } | null;
}

/**
 * The actor of `account`'s call, or null for the owner itself and for another owner visiting
 * (origin null, not a run): neither is one of the owner's agents.
 */
export function decidedActor(account: Account): Actor | null {
  if (account.run !== null) {
    return runActor(account.run, account.origin);
  }
  return account.origin === null ? null
    : { agent: null, client: account.origin.client, run: null };
}

/** A run, as its calls are decided: its id, its agent and its conversation. */
interface DecidedRun {
  readonly id: string;
  readonly agent: string;
  readonly conversation: string;
}

/** The actor of `run`'s calls: its agent, under the client `origin` names when one started it. */
function runActor(run: DecidedRun, origin: Origin | null): Actor {
  return { agent: run.agent, client: origin?.client ?? null,
           run: { id: run.id, conversation: run.conversation, family: origin?.family ?? null } };
}

/** The strictest of `document`'s rules on `act` that hold for `actor`, or null for none. */
export function ruleOn(document: PolicyDocument, act: string, actor: Actor):
    ActionRule["do"] | null {
  const holding = (document.actions ?? []).filter((rule) => rule.act === act
    && (rule.agent === undefined || rule.agent === actor.agent));
  return holding.some((rule) => rule.do === "never") ? "never"
    : holding.length > 0 ? "ask" : null;
}

/** A CLI's own pre-tool hook, as its engine's record has it. */
type Hook = Exclude<Decide, { readonly unsupported: string }>;

/** The hook of `engine`'s record, or null for one that has none. */
export function hookOf(engine: string | null): Hook | null {
  const decide = engine === null ? undefined : servedRecord(engine)?.cli.decide;
  return decide === undefined || "unsupported" in decide ? null : decide;
}

/** The patterns of the CLI's tool names that are `act`, as `document`'s rules write it. */
function actTools(document: PolicyDocument, act: string, hook: Hook): string[] {
  return toolPatterns(hook, actNamed(act)?.hook ?? [], (document.actions ?? [])
    .filter((rule) => rule.act === act).flatMap((rule) => rule.tools ?? []));
}

/** Where a rule holds, and where it can't (`ruleNotes`). */
export interface RuleNote {
  readonly holds_at: readonly string[];
  readonly not_enforced: readonly string[];
}

/**
 * The honest note (d3 §N5) of each of `document`'s rules, in order, from `acts.json` and the
 * engines' records. It holds at `doors` (the platform's own tools and doors that do its act),
 * at `definitions` (the commands the agent's own definition starts, decided at each run's
 * admission, whatever its engine, where its act is of `COMMAND_KIND`) and at each engine whose
 * CLI's own hook sees it (by its kinds, or the rule's tools). It
 * can't hold where `acts.json` says nothing of ours sees the act (`unseen`), at an engine
 * whose CLI holds no hook for it or one the agent can alter (`alterable`), nor, mid-run, at a
 * hook whose failure lets the call through (`<engine>.fails_open`). Every engine is named: an
 * agent's engine is `auto`, or its own to change, so no rule is held to one.
 */
export function ruleNotes(document: PolicyDocument): RuleNote[] {
  return (document.actions ?? []).map((rule) => {
    const act = actNamed(rule.act)!;
    const holds: string[] = [...actDoors(rule.act).length > 0 ? ["doors"] : [],
                             ...act.hook.includes(COMMAND_KIND) ? ["definitions"] : []];
    const not: string[] = [...act.unseen];
    if (act.hook.length > 0 || rule.tools !== undefined) {
      for (const engine of [...served().keys()].sort()) {
        const hook = hookOf(engine);
        if (hook === null || hook.alterable !== undefined || (rule.tools === undefined
            && !act.hook.some((kind) => hook.kinds[kind] !== undefined))) {
          not.push(engine);
          continue;
        }
        holds.push(engine);
        if (hook.fails === "open") {
          not.push(`${engine}.fails_open`);
        }
      }
    }
    return { holds_at: holds, not_enforced: not };
  });
}

/**
 * The owner's note on its agents' own hooks (`hooks.json`), the honest note's neighbour: where
 * each runs, and where it can't be held to what it says. It runs at each engine whose record
 * renders it, in the root-owned layer the engine's decide hook is in, through the runner's
 * dispatcher (`definition.hooks`). It never runs where a main agent calls the doors
 * (`main_agent`), nor at an engine with no such layer; at an engine whose CLI runs it where the
 * agent can change what it does (`alterable`, Codex's login shell) it runs all the same, but is
 * not held; and, mid-run, a dispatcher that cannot start lets the call through where the engine
 * `fails` open (`<engine>.fails_open`). Every engine is named, as for a rule.
 */
export function hookNote(): RuleNote {
  const holds: string[] = [];
  const not: string[] = ["main_agent"];
  for (const engine of [...served().keys()].sort()) {
    const hooks = servedRecord(engine)!.cli.definition.hooks;
    if ("unsupported" in hooks || hooks.alterable !== undefined) {
      not.push(engine);
      continue;
    }
    holds.push(engine);
    if (hooks.fails === "open") {
      not.push(`${engine}.fails_open`);
    }
  }
  return { holds_at: holds, not_enforced: not };
}

/** What a call asks: the door it came to, and its request as asked. */
export interface Asked {
  readonly door: string;
  readonly request: unknown;
}

/** A held call, as the owner is shown it (`actionRequests`). */
export interface ActionCard {
  readonly card_id: string;
  readonly act: string;
  /** The act's words (`acts.json`). */
  readonly says: string;
  readonly door: string;
  readonly agent: string | null;
  readonly client: string | null;
  /** The run's conversation the call was asked in, which a yes resumes; null for a client's. */
  readonly conversation: string | null;
  /** The request as asked, canonical JSON, its bytes as their text; `cut` past `shownChars`. */
  readonly request: string;
  readonly cut: boolean;
  readonly generation: number;
  readonly filed_at: number;
}

/** A decision a rule made, or the owner's answer to a held call, as `policyReceipts` lists it. */
export type ActionReceipt = {
  readonly at: number;
  readonly act: string;
  readonly decision: ActionRule["do"];
  readonly outcome: "denied" | "held" | "admitted" | "approved" | "refused";
  readonly call_digest: string;
  readonly card_id: string | null;
  readonly agent: string | null;
  readonly client: string | null;
  readonly generation: number;
};

/** The policy as the decision reads it: its generation and its document. */
type PolicyRead = () => { readonly generation: number; readonly document: PolicyDocument };

const CARD_ID = /^act_[0-9a-f]{32}$/;

/** The pending cards' rows: not yet answered yes, asked under the generation bound. */
const PENDING = "FROM action_requests WHERE approved_at IS NULL AND generation = ?";

/**
 * `value` as JSON holds it, each of its bytes (`Uint8Array`, a file's content) as `bytes`
 * makes them: their text for the card, their digest for the call's own digest.
 */
function asJson(value: unknown, bytes: (value: Uint8Array) => string): unknown {
  if (value instanceof Uint8Array) {
    return bytes(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => asJson(item, bytes));
  }
  return typeof value === "object" && value !== null
    ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, asJson(item, bytes)]))
    : value;
}

type Row = {
  card_id: string; digest: string; call_digest: string; card: string; client: string | null;
  generation: number; filed_at: number; approved_at: number | null;
  run: string | null; family: string | null;
};

/**
 * The input a run resumed on a yes starts on: the card answered, and the call to make again.
 * A command of the agent's own definition held at its admission (`decideCommand`) is no call
 * the agent makes: the resumed run's admission admits it.
 */
function resumeInput(card: ActionCard): string {
  return `Your owner said yes to card ${card.card_id} (${card.act} at ${card.door}), once. ` +
    "If it was a call of yours, make that same call again now and it goes through; if you " +
    "made it since, do not make it again. If it was a command of your own definition, it " +
    "runs in this run.";
}

export class Actions {
  readonly #storage: OwnerStorage;
  readonly #policy: PolicyRead;
  readonly #notices: Notices;

  constructor(storage: OwnerStorage, policy: PolicyRead, notices: Notices) {
    this.#storage = storage;
    this.#policy = policy;
    this.#notices = notices;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /** Whether any rule names `act`: only then is a call of it decided at all. */
  ruled(act: string): boolean {
    return (this.#policy().document.actions ?? []).some((rule) => rule.act === act);
  }

  /**
   * The patterns of the CLI's tool names `hook` matches for the owner's rules, every act a
   * rule names: none when no rule is on an act its hook sees.
   */
  hookTools(hook: Hook): { tools: string[]; generation: number } {
    const { document, generation } = this.#policy();
    const acts = new Set((document.actions ?? []).map((rule) => rule.act));
    return { tools: [...new Set([...acts].flatMap((act) => actTools(document, act, hook)))].sort(),
             generation };
  }

  /**
   * In the caller's unit: a run's tool call at its CLI's hook decided, as each act it is
   * (`actTools`): every act a rule never lets be first, then the first act asked alone, so a
   * call two asked acts name files one card, whose one yes admits it.
   */
  decideTool(actor: Actor, hook: Hook, tool: string, input: unknown, now: number): void {
    const { document } = this.#policy();
    this.#decideAs(document, (act) => actTools(document, act, hook).some((pattern) =>
      new RegExp(`^(?:${pattern})$`).test(tool)), actor, { door: tool, request: input }, now);
  }

  /**
   * In the caller's unit, at `run`'s admission: a command the run's own definition starts
   * (`asked`: the definition's file as its door, the server or hook as its request), decided
   * as the run's call (`origin`, the client that started it, if one did) of each act of
   * `COMMAND_KIND`, as `decideTool` decides a tool call of that kind. No tool call starts it,
   * so no hook would see it.
   */
  decideCommand(run: DecidedRun, origin: Origin | null, asked: Asked, now: number): void {
    const { document } = this.#policy();
    this.#decideAs(document, (act) => actNamed(act)?.hook.includes(COMMAND_KIND) === true,
                   runActor(run, origin), asked, now);
  }

  /**
   * `asked` decided as each act a rule names that `is` takes: every act a rule never lets be
   * first, then the first act asked alone, so a call two asked acts name files one card.
   */
  #decideAs(document: PolicyDocument, is: (act: string) => boolean, actor: Actor,
            asked: Asked, now: number): void {
    const acts = [...new Set((document.actions ?? []).map((rule) => rule.act))].filter(is);
    const nevers = acts.filter((act) => ruleOn(document, act, actor) === "never");
    const first = acts.find((act) => ruleOn(document, act, actor) === "ask");
    for (const act of [...nevers, ...(first === undefined ? [] : [first])]) {
      this.decide(act, actor, asked, now);
    }
  }

  /** Rows asked under another generation, deleted. */
  #sweep(generation: number): void {
    this.#sql.exec("DELETE FROM action_requests WHERE generation != ?", generation);
  }

  /** One receipt; the bucket keeps its newest `receiptsPerBucket`. */
  #receipt(receipt: ActionReceipt): void {
    const sql = this.#sql;
    sql.exec("INSERT INTO action_receipts (at, act, decision, outcome, call_digest, card_id, " +
               "agent, client, generation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", receipt.at,
             receipt.act, receipt.decision, receipt.outcome, receipt.call_digest,
             receipt.card_id, receipt.agent, receipt.client, receipt.generation);
    const over = sql.exec<{ n: number }>("SELECT action_receipts AS n FROM policy_usage").one().n
      - POLICY_LIMITS.receiptsPerBucket;
    if (over > 0) {
      sql.exec("DELETE FROM action_receipts WHERE seq IN (SELECT seq FROM action_receipts " +
                 "ORDER BY seq LIMIT ?)", over);
    }
  }

  /** The last `receiptsListed`, newest first. */
  receipts(): ActionReceipt[] {
    return this.#sql.exec<ActionReceipt>(
      "SELECT at, act, decision, outcome, call_digest, card_id, agent, client, generation " +
        "FROM action_receipts ORDER BY seq DESC LIMIT ?", POLICY_LIMITS.receiptsListed,
    ).toArray();
  }

  /**
   * In the caller's unit: `actor`'s call of `act` decided. Returns when it may be done;
   * throws `action_never` when a rule never lets it be, and `held_for_owner` when it waits for
   * the owner's yes, its card filed (once), receipted and noticed.
   */
  decide(act: string, actor: Actor, asked: Asked, now: number): void {
    const { generation, document } = this.#policy();
    const rule = ruleOn(document, act, actor);
    if (rule === null) {
      return;
    }
    const request = canonical(asJson(asked.request, (bytes) => new TextDecoder().decode(bytes)));
    const conversation = actor.run?.conversation ?? null;
    const call = policyDigest({
      door: asked.door, agent: actor.agent, client: actor.client, conversation,
      request: asJson(asked.request, (bytes) => `sha256:${sha256Hex(bytes)}`) });
    const receipt = (outcome: ActionReceipt["outcome"], cardId: string | null) => this.#receipt({
      at: now, act, decision: rule, outcome, call_digest: call, card_id: cardId,
      agent: actor.agent, client: actor.client, generation });
    if (rule === "never") {
      this.#storage.transactionSync(() => receipt("denied", null));
      throw new Refusal("action_never",
                        `the owner's rule never lets an agent do this (${act})`);
    }
    const sql = this.#sql;
    const held = this.#storage.transactionSync((): string | null => {
      this.#sweep(generation);
      const row = sql.exec<Row>("SELECT * FROM action_requests WHERE call_digest = ?", call)
        .toArray()[0];
      if (row !== undefined && row.approved_at !== null) {
        sql.exec("DELETE FROM action_requests WHERE card_id = ?", row.card_id);
        receipt("admitted", row.card_id);
        return null;
      }
      if (row !== undefined) {
        return row.card_id;
      }
      this.#room(actor.client, now);
      const cardId = "act_" + randomHex(16);
      const shown = [...request];
      const card: ActionCard = {
        card_id: cardId, act, says: actNamed(act)!.says, door: asked.door, agent: actor.agent,
        client: actor.client, conversation,
        request: shown.slice(0, ACTION_LIMITS.shownChars).join(""),
        cut: shown.length > ACTION_LIMITS.shownChars, generation, filed_at: now,
      };
      sql.exec("INSERT INTO action_requests (card_id, digest, call_digest, card, client, " +
                 "generation, filed_at, run, family) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", cardId,
               policyDigest(card), call, canonical(card), actor.client, generation, now,
               actor.run?.id ?? null, actor.run?.family ?? null);
      receipt("held", cardId);
      this.#notices.add("action_request", `${actor.agent ?? "an outside agent"} asks to ` +
                        `${act} at ${asked.door}: allow or deny it at ${waitingPage(cardId)}`,
                        now);
      return cardId;
    });
    if (held !== null) {
      throw new Refusal("held_for_owner", `held for your owner's answer: card ${held}. Your ` +
                        `owner answers it at ${waitingPage(held)}: give them that link. Once ` +
                        "they say yes, make this same call again", { facts: { card_id: held } });
    }
  }

  /**
   * Room for one more held call: at `perClient` of `client`'s, its oldest row goes; else at
   * `held` in all, the oldest of its asker's class (outside clients', or the owner's own
   * agents'), so an outside client's ask never pushes out the owner's; with none, refused.
   * The owner is told whose call went (`held_request_evicted`), once while that notice stands.
   */
  #room(client: string | null, now: number): void {
    const sql = this.#sql;
    const own = client !== null && sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM action_requests WHERE client = ?", client).one().n
        >= ACTION_LIMITS.perClient;
    if (!own && sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM action_requests").one().n
        < ACTION_LIMITS.held) {
      return;
    }
    const gone = sql.exec<{ client: string | null; agent: string | null }>(
      "DELETE FROM action_requests WHERE card_id = (SELECT card_id FROM action_requests WHERE " +
        `${own ? "client = ?" : `client IS ${client === null ? "" : "NOT "}NULL`} ORDER BY ` +
        "filed_at, rowid LIMIT 1) RETURNING client, json_extract(card, '$.agent') AS agent",
      ...(own ? [client] : [])).toArray()[0];
    if (gone === undefined) {
      throw new Refusal("refused", "too many calls held for the owner");
    }
    this.#notices.once("held_request_evicted", `a held call of ${gone.agent ?? gone.client} ` +
                       "was let go to make room for a newer one", now);
  }

  /** The pending card under `cardId`, of the policy's generation, or null. */
  #pending(cardId: unknown, generation: number): Row | null {
    if (typeof cardId !== "string" || !CARD_ID.test(cardId)) {
      return null;
    }
    return this.#sql.exec<Row>(`SELECT * ${PENDING} AND card_id = ?`, generation, cardId)
      .toArray()[0] ?? null;
  }

  /** The pending cards of the policy's generation, oldest first, each with its answer's digest. */
  list(): (ActionCard & { readonly digest: string })[] {
    return this.#sql.exec<Pick<Row, "card" | "digest">>(
      `SELECT card, digest ${PENDING} ORDER BY filed_at, rowid`, this.#policy().generation,
    ).toArray().map((row) => ({ ...JSON.parse(row.card) as ActionCard, digest: row.digest }));
  }

  /**
   * The owner's yes, in its session, to the pending card with the digest it was shown: kept
   * as the call's once-approval, receipted, and, for a run's call, its conversation resumed
   * (`resume`, `Runs.resume`, given the run it was held in, that run's origin's family and
   * the input to start on, in the same transaction: a resume refused writes no yes); the
   * resumed run's id, or null for none.
   */
  approve(session: LiveSession, cardId: unknown, shown: unknown, now: number,
          resume: (run: string, family: string | null, prompt: string) => string | null):
      { card_id: string; approved: true; run: string | null } {
    proven(session, "session");
    return this.#answer(cardId, shown, now, (row, card) => {
      this.#sql.exec("UPDATE action_requests SET approved_at = ? WHERE card_id = ?", now,
                     row.card_id);
      return { card_id: row.card_id, approved: true, run: row.run === null ? null
        : resume(row.run, row.family, resumeInput(card)) };
    });
  }

  /** The owner's no to the pending card shown: deleted, receipted. */
  refuse(cardId: unknown, shown: unknown, now: number): { card_id: string; approved: false } {
    return this.#answer(cardId, shown, now, (row) => {
      this.#sql.exec("DELETE FROM action_requests WHERE card_id = ?", row.card_id);
      return { card_id: row.card_id, approved: false };
    });
  }

  /**
   * The owner's answer, in one transaction: the pending card with the digest it was shown,
   * answered by `answered`, and receipted.
   */
  #answer<T extends { approved: boolean }>(cardId: unknown, shown: unknown, now: number,
                                           answered: (row: Row, card: ActionCard) => T): T {
    return this.#storage.transactionSync(() => {
      const { generation } = this.#policy();
      this.#sweep(generation);
      const row = this.#pending(cardId, generation);
      if (row === null || typeof shown !== "string" || row.digest !== shown) {
        throw new Refusal("refused", "action request is not the pending card shown");
      }
      const card = JSON.parse(row.card) as ActionCard;
      const answer = answered(row, card);
      this.#receipt({ at: now, act: card.act, decision: "ask",
                      outcome: answer.approved ? "approved" : "refused",
                      call_digest: row.call_digest, card_id: row.card_id, agent: card.agent,
                      client: card.client, generation });
      return answer;
    });
  }
}

export interface ActionAnswerRequest {
  readonly card_id: string;
  readonly digest: string;
}

// -- THE DOORS: the `actions` rows' handlers (`owner/doors.json`) --------------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#call`) hands what its
// doors reach (`ActionsDoorNeeds`) once the row's steps held: the bind, the account, the
// scope and the fields.

/** What a run's hook helper asks: the tools its hook matches, or one call it saw. */
export interface DecideRequest {
  readonly check?: true;
  readonly tool_name?: string;
  readonly tool_input?: unknown;
}

/** A tool's name, as a CLI hands its hook one. */
const TOOL = /^[A-Za-z0-9_.:-]{1,256}$/;

/** What the `actions` doors reach: the held calls, and the runs a yes resumes. */
export interface ActionsDoorNeeds {
  readonly actions: Actions;
  readonly runs: { resume(run: string, family: string | null, prompt: string, now: number):
                     string | null;
                   engineOf(run: string): string | null };
}

/** The `actions` doors' handlers, by door. */
export const actionsDoors = Object.freeze({
  /** The pending held calls the owner is shown, oldest first, each with its digest. */
  actionRequests(o: ActionsDoorNeeds, _call: Call<undefined>):
      { cards: (ActionCard & { readonly digest: string })[] } {
    return { cards: o.actions.list() };
  },
  /**
   * The owner's yes, in its session: the held call admitted once, whenever it is made, and a
   * run's call's conversation resumed (the run that resumes it, or null), its lane synced.
   */
  actionApprove(o: ActionsDoorNeeds, { session, r }: Call<ActionAnswerRequest, "session">):
      { card_id: string; approved: true; run: string | null } {
    const now = clock();
    return o.actions.approve(session, r["card_id"], r["digest"], now,
                             (run, family, prompt) => o.runs.resume(run, family, prompt, now));
  },
  /**
   * The decide door, which a run's CLI's hook asks (`sandbox/runner/decide.ts`, or the CLI
   * itself for an `http` hook): with `check`, the patterns of the tool names its CLI's hook
   * matches for the owner's rules (none for an engine with no hook) and their generation; else
   * the tool call its hook saw, decided (`decideTool`). The helper reads a refusal as a door's;
   * an `http` hook is answered in its CLI's own words (`hook`): an empty object lets the call
   * be, and the record's `deny`, its `{reason}` the refusal's message, refuses it.
   */
  runDecide(o: ActionsDoorNeeds, { account, r }: Call<DecideRequest>):
      { tools: string[]; generation: number } | { allow: true } | { hook: object } {
    const actor = decidedActor(account);
    if (account.run === null || actor === null) {
      throw new Refusal("refused", "only a run's own hook asks its decide door");
    }
    const hook = hookOf(o.runs.engineOf(account.run.id));
    if (r["check"] === true) {
      return hook === null ? { tools: [], generation: 0 } : o.actions.hookTools(hook);
    }
    const tool = r["tool_name"];
    if (typeof tool !== "string" || !TOOL.test(tool)) {
      throw new Refusal("invalid", "invalid decide request");
    }
    const deny = hook !== null && "http" in hook.runs ? hook.runs.http.deny : null;
    try {
      if (hook !== null) {
        o.actions.decideTool(actor, hook, tool, r["tool_input"] ?? null, clock());
      }
    } catch (error) {
      const refusal = refusalOf(error);
      if (deny === null || (refusal?.code !== "action_never"
                            && refusal?.code !== "held_for_owner")) {
        throw error;
      }
      return { hook: JSON.parse(deny.replace("{reason}",
        JSON.stringify(refusal.message).slice(1, -1))) as object };
    }
    return deny === null ? { allow: true } : { hook: {} };
  },
  /** The owner's no, a narrowing that needs no session: the held call's card gone. */
  actionRefuse(o: ActionsDoorNeeds, { r }: Call<ActionAnswerRequest>):
      { card_id: string; approved: false } {
    return o.actions.refuse(r["card_id"], r["digest"], clock());
  },
});
