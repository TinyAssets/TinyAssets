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
 *   A yes keeps the row as the call's once-approval for `approvalTtlS`, which its one use
 *   deletes; a no deletes it.
 * - **A run's CLI's own tool calls are decided too** (R5 P-hooks): its pre-tool hook, where its
 *   engine's record has one (`schema.ts` `Decide`), asks the decide door of each call it
 *   matches (`runDecide`). A call is each act whose hook kinds name the tool (`acts.json`) or
 *   one of whose rules names it (`tools`), decided as a door's call of that act is, its door
 *   the tool's name and its request the tool's input: every act a rule never lets be first,
 *   then one act asked, so one card and its one yes admit the call however many acts ask.
 *   Which tools the hook matches is the owner's rules' (`hookTools`), for every agent: the
 *   decision holds each rule to its own agent.
 * - **The honest note** (d3 §N5, `ruleNotes`): each rule's places it holds and can't, which
 *   `policyRead` answers beside the rules.
 * - **A yes to a run's call resumes its conversation** (R5 P-resume): the approval writes a
 *   run waiting there (`Runs.resume`) under the held run's authority, on an input naming the
 *   card, so the agent makes the call again and the approval admits it. A main agent's
 *   call is retried when its user says so.
 * - **Generation-bound**: a row asked under another policy generation is neither shown,
 *   answered nor admitted, so a change of the owner's rules decides every held call afresh.
 * - **Bounded and swept**: `held` rows in all, `perClient` of them one outside client's; at
 *   each decision and each answer, rows past their life (`cardTtlS` pending, `approvalTtlS`
 *   approved) and rows of another generation go first.
 * - **Receipted** in a bucket of their own, `action_receipts`, kept to the policy's
 *   `receiptsPerBucket` as its other buckets are, only where a rule decided: `denied`, `held`
 *   (once a card), `admitted`, and the owner's `approved` and `refused`. A call no rule
 *   decides writes nothing.
 *
 * Every change runs in one `transactionSync`; a refusal is thrown once what it receipts is
 * written, in the caller's unit, so the card and its receipt stand.
 */
import { actNamed, type HookKind } from "../../contract/src/acts";
import { canonical } from "../../contract/src/canonical";
import { actDoors } from "../../contract/src/doors";
import { sha256Hex } from "../../contract/src/digest";
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
 * How many calls wait, and how long. A pending card waits 30 days: Claude Code keeps a
 * session that long (`cleanupPeriodDays`), which bounds the resume an answer starts (P-resume).
 * A yes admits its call within a day: a main agent retries when its user says so. 200 rows in
 * all, pending or approved, and 50 of them one outside client's, so no client fills the set
 * the owner's own agents ask in; each card shows at most 8,000 code points of its request.
 */
interface ActionLimits {
  readonly held: number;
  readonly perClient: number;
  readonly cardTtlS: number;
  readonly approvalTtlS: number;
  readonly shownChars: number;
}

export const ACTION_LIMITS: ActionLimits = Object.freeze({
  held: 200,
  perClient: 50,
  cardTtlS: 30 * 86_400,
  approvalTtlS: 86_400,
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
    return { agent: account.run.agent, client: account.origin?.client ?? null,
             run: { id: account.run.id, conversation: account.run.conversation,
                    family: account.origin?.family ?? null } };
  }
  return account.origin === null ? null
    : { agent: null, client: account.origin.client, run: null };
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

/**
 * A pattern of a name part a rule's `tools` matcher writes (`mcp:<server>:<tool>`): each `*`
 * any run of characters, each character but a letter, a digit or `_` any one (a CLI rewrites
 * those in its own names, each its own way), the rest itself. So the pattern holds every name
 * a CLI may give the tool, never fewer.
 */
function partPattern(glob: string): string {
  return [...glob].map((char) => char === "*" ? ".*" : /[A-Za-z0-9_]/.test(char) ? char : ".")
    .join("");
}

/** The patterns of the CLI's tool names that are `act`, as `document`'s rules write it. */
function actTools(document: PolicyDocument, act: string, hook: Hook): string[] {
  const kinds = actNamed(act)?.hook ?? [];
  const named = (document.actions ?? []).filter((rule) => rule.act === act)
    .flatMap((rule) => rule.tools ?? []).map((matcher) => {
      const [, server, tool] = matcher.split(":") as [string, string, string];
      return `${hook.mcp.prefix}${partPattern(server)}${hook.mcp.join}${partPattern(tool)}`;
    });
  return [...kinds.flatMap((kind: HookKind) => (hook.kinds[kind] ?? [])
    .map((name) => name.replaceAll(".", "\\."))), ...named];
}

/** Where a rule holds, and where it can't (`ruleNotes`). */
export interface RuleNote {
  readonly holds_at: readonly string[];
  readonly not_enforced: readonly string[];
}

/**
 * The honest note (d3 §N5) of each of `document`'s rules, in order, from `acts.json` and the
 * engines' records. It holds at `doors` (the platform's own tools and doors that do its act)
 * and at each engine whose CLI's own hook sees it (by its kinds, or the rule's tools). It
 * can't hold where `acts.json` says nothing of ours sees the act (`unseen`), at an engine
 * whose CLI holds no hook for it or one the agent can alter (`alterable`), nor, mid-run, at a
 * hook whose failure lets the call through (`<engine>.fails_open`). Every engine is named: an
 * agent's engine is `auto`, or its own to change, so no rule is held to one.
 */
export function ruleNotes(document: PolicyDocument): RuleNote[] {
  return (document.actions ?? []).map((rule) => {
    const act = actNamed(rule.act)!;
    const holds: string[] = actDoors(rule.act).length > 0 ? ["doors"] : [];
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

/** What a call asks: the door it came to, and its request as asked. */
export interface Asked {
  readonly door: string;
  readonly request: unknown;
}

/** A held call, as the owner is shown it (`actionRequest`). */
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
  readonly expires_at: number;
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

/** The input a run resumed on a yes starts on: the card answered, and the call to make again. */
function resumeInput(card: ActionCard): string {
  return `Your owner said yes to card ${card.card_id} (${card.act} at ${card.door}). Make ` +
    "that same call again now and it goes through, once. If you made it since, do not make " +
    "it again.";
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
    const acts = [...new Set((document.actions ?? []).map((rule) => rule.act))]
      .filter((act) => actTools(document, act, hook).some((pattern) =>
        new RegExp(`^(?:${pattern})$`).test(tool)));
    const nevers = acts.filter((act) => ruleOn(document, act, actor) === "never");
    const asked = acts.find((act) => ruleOn(document, act, actor) === "ask");
    for (const act of [...nevers, ...(asked === undefined ? [] : [asked])]) {
      this.decide(act, actor, { door: tool, request: input }, now);
    }
  }

  /** Rows past their life, and rows asked under another generation, deleted. */
  #sweep(generation: number, now: number): void {
    this.#sql.exec("DELETE FROM action_requests WHERE generation != ? OR (approved_at IS NULL " +
                     "AND filed_at < ?) OR approved_at < ?", generation,
                   now - ACTION_LIMITS.cardTtlS, now - ACTION_LIMITS.approvalTtlS);
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
      this.#sweep(generation, now);
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
      this.#room(actor.client);
      const cardId = "act_" + randomHex(16);
      const shown = [...request];
      const card: ActionCard = {
        card_id: cardId, act, says: actNamed(act)!.says, door: asked.door, agent: actor.agent,
        client: actor.client, conversation,
        request: shown.slice(0, ACTION_LIMITS.shownChars).join(""),
        cut: shown.length > ACTION_LIMITS.shownChars, generation, filed_at: now,
        expires_at: now + ACTION_LIMITS.cardTtlS,
      };
      sql.exec("INSERT INTO action_requests (card_id, digest, call_digest, card, client, " +
                 "generation, filed_at, run, family) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", cardId,
               policyDigest(card), call, canonical(card), actor.client, generation, now,
               actor.run?.id ?? null, actor.run?.family ?? null);
      receipt("held", cardId);
      this.#notices.add("action_request", `${actor.agent ?? "an outside agent"} asks to ` +
                        `${act} at ${asked.door}: card ${cardId}`, now);
      return cardId;
    });
    if (held !== null) {
      throw new Refusal("held_for_owner", `held for your owner's answer: card ${held}. The ` +
                        "owner answers it in the TinyAssets app; once they say yes, make " +
                        "this same call again", { facts: { card_id: held } });
    }
  }

  /** Room for one more held call: `held` in all, `perClient` of them `client`'s. */
  #room(client: string | null): void {
    const sql = this.#sql;
    if (client !== null && sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM action_requests WHERE client = ?", client).one().n
        >= ACTION_LIMITS.perClient) {
      throw new Refusal("refused", "this client's calls held for the owner are at their limit");
    }
    if (sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM action_requests").one().n
        >= ACTION_LIMITS.held) {
      throw new Refusal("refused", "too many calls held for the owner");
    }
  }

  /** The pending card under `cardId`, of the policy's generation and unexpired, or null. */
  #pending(cardId: unknown, generation: number, now: number): Row | null {
    if (typeof cardId !== "string" || !CARD_ID.test(cardId)) {
      return null;
    }
    return this.#sql.exec<Row>(
      "SELECT * FROM action_requests WHERE card_id = ? AND approved_at IS NULL AND " +
        "generation = ? AND filed_at >= ?", cardId, generation, now - ACTION_LIMITS.cardTtlS,
    ).toArray()[0] ?? null;
  }

  /** The pending card under `cardId`, and the digest its answer names, or null. */
  card(cardId: unknown, now: number): { card: ActionCard; digest: string } | null {
    const row = this.#pending(cardId, this.#policy().generation, now);
    return row === null ? null : { card: JSON.parse(row.card) as ActionCard, digest: row.digest };
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
    return this.#answer(session, cardId, shown, now, (row, card) => {
      this.#sql.exec("UPDATE action_requests SET approved_at = ? WHERE card_id = ?", now,
                     row.card_id);
      return { card_id: row.card_id, approved: true, run: row.run === null ? null
        : resume(row.run, row.family, resumeInput(card)) };
    });
  }

  /** The owner's no, in its session, to the pending card shown: deleted, receipted. */
  refuse(session: LiveSession, cardId: unknown, shown: unknown, now: number):
      { card_id: string; approved: false } {
    return this.#answer(session, cardId, shown, now, (row) => {
      this.#sql.exec("DELETE FROM action_requests WHERE card_id = ?", row.card_id);
      return { card_id: row.card_id, approved: false };
    });
  }

  /**
   * The owner's answer, in its session, in one transaction: the pending card with the digest
   * it was shown, answered by `answered`, and receipted.
   */
  #answer<T extends { approved: boolean }>(session: LiveSession, cardId: unknown,
                                           shown: unknown, now: number,
                                           answered: (row: Row, card: ActionCard) => T): T {
    proven(session, "session");
    return this.#storage.transactionSync(() => {
      const { generation } = this.#policy();
      this.#sweep(generation, now);
      const row = this.#pending(cardId, generation, now);
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

export interface ActionCardRequest {
  readonly card_id: string;
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
  /** The pending card the owner is shown, and the digest its answer names. */
  actionRequest(o: ActionsDoorNeeds, { r }: Call<ActionCardRequest>):
      { card: ActionCard; digest: string } {
    const held = o.actions.card(r["card_id"], clock());
    if (held === null) {
      throw new Refusal("refused", "no pending action request for this card");
    }
    return held;
  },
  /**
   * The owner's yes, in its session: the held call admitted once, within a day, and a run's
   * call's conversation resumed (the run that resumes it, or null), its lane synced.
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
  /** The owner's no, in its session: the held call's card gone. */
  actionRefuse(o: ActionsDoorNeeds, { session, r }: Call<ActionAnswerRequest, "session">):
      { card_id: string; approved: false } {
    return o.actions.refuse(session, r["card_id"], r["digest"], clock());
  },
});
