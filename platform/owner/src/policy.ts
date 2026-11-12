/**
 * Owner policy (N10), in the owner's own object (ADR-032): one document of egress, action
 * rules and the owner's other settings, its generation, the owner's receipts, and the agents'
 * held proposals.
 *
 * Ported from `PolicyStore` in `tinyassets/broker/policy.py` (origin/main 1fd430857):
 * `read`, `write` (with a held proposal's one apply), `receipts`, `receipt_proposal`,
 * `proposal`, `receipt_refusal` and `_validate`, whose rules it keeps. Only the owner,
 * in its own protected session (`sessions.ts`), writes policy; the object checks that
 * session itself before any write here runs.
 *
 * - The document is its `egress` table and the optional tables below
 *   (`policy-document.ts`), validated whole on every write and every read; its digest is
 *   `sha256:` over its canonical JSON and is rechecked on read, so a row changed around this
 *   module fails loudly. Every owner policy change, a per-agent
 *   wake-limit change included (`changed`), bumps the generation, which a write must
 *   name (`expected_generation`), so a change made against a stale view is refused.
 * - A fresh object starts from the default document at generation 1, put there by its
 *   migration: the Python's bootstrap, legacy upgrade and per-owner failure isolation
 *   have no counterpart, since the object is the account.
 * - An agent's proposal is receipted, and with a card id held for the owner, who
 *   applies it at most once through a write that names it. A held card never changes;
 *   pending ones are never evicted, and a full pending set refuses more.
 * - A refused owner-session call leaves a `refusal` receipt with the digest of what was
 *   asked, never the session proof, written after the refused change rolled back.
 * - The optional `actions` table holds the owner's action rules, each on an act (`acts.json`):
 *   ask first, or never. A rule holds where its act is seen, and one nothing sees is refused
 *   (d3 §N). Nothing decides one yet: the decision, its asks and its once-approvals are R5's
 *   later slices (tasks.md §10, R5).
 * - The document's optional `files` table sets the owner's file quotas and history
 *   retention (`FILE_DEFAULTS`, plan §6); what it leaves out keeps the defaults. It is
 *   ours, with no Python counterpart, so the default document leaves it out.
 * - The optional `models` table is the owner's model budget (`MODEL_DEFAULTS`, gateway
 *   research §4): per-run requests, tokens and cost (held per sandbox run at the egress
 *   door, `egress.ts`), and owner-wide and per-source daily and monthly cost ceilings.
 *   The Python's per-turn limits were code constants; here they are policy.
 * - The optional `codemode` table holds one switch: whether code mode scripts may run
 *   (`on`, on unless the owner turns it off; `CODEMODE_DEFAULTS`), their VMs in the
 *   codemode Worker. The MCP door's `code` tool refuses while it is off (slice M).
 * - The optional `tools` table is the owner's own tool profile (`custom`, slice M): the
 *   MCP door's tools a client whose grant names `custom` is served, each a tool of the
 *   table, none twice. Left out, `custom` is every tool (`contract/profiles.json`).
 *
 * The Python closed the owner's public egress tunnels before a write committed and
 * cancelled its live streams after. Here a policy write ends the owner's sandbox egress
 * streams (`egress.ts`): each sandbox request is admitted at open against the current
 * generation, and a write that moves it wakes the `runs` lane (`synced`), which re-applies
 * each live lease's network under it (`Launcher.reapply`); its calls are refused until then.
 *
 * Declared differences from the Python:
 * - An egress destination whose host the WHATWG URL parser reads as an IP address
 *   (`8.8.8.8`, `0x7f000001`, `0x7f.1`, `[::1]`) is refused (`EGRESS_IP_LITERAL`); the
 *   Python accepted a public one. Workers `fetch` shows no resolved address, so the
 *   gateway fetches strictly publicly, E2B filters the sandbox's CIDRs (its live probe is
 *   the sandbox slice's), and the sandbox's network admits names by SNI (G4). A host no
 *   URL can carry (a name ending in a number, a malformed `xn--` label) is invalid. Every
 *   other DNS name the Python accepted is accepted here.
 * - The document is bounded (`maxDocument`), and receipts are kept per bucket
 *   (`receiptsPerBucket`): owner changes, agent proposals and refusals each apart, so
 *   neither an agent nor a refused stranger can push the owner's changes out. The
 *   Python kept every receipt and listed two buckets.
 * - A held proposal applies only as a `policy_proposal` card, which the Python's apply
 *   path checked and its write did not.
 * - One outside client holds at most `pendingPerClient` of the pending proposals, so it
 *   cannot fill the set the owner's agents propose into; the Python had one set.
 * - An entry's values are flat (strings, numbers, booleans, null), as every value the
 *   Python wrote is; it took any JSON there.
 * - The digest is this object's own (numbers as JavaScript prints them), so the cutover
 *   recomputes it for each imported document.
 * - The Python's rule tables (`rules`, `approval_grants`, `operation_kinds`, `review_on`,
 *   `review_off`), its `spend` caps and its egress `ask` mode have no counterpart: each was
 *   keyed to the broker's app connections or read by nothing here (d3 §N6). An action rule
 *   is the `actions` table's.
 *
 * Every change runs in one `transactionSync`, so a refusal leaves nothing behind.
 */
import { FILE_DEFAULTS } from "../../contract/src/file-defaults";
import {
  canonical, CODEMODE_DEFAULTS, DEFAULT_POLICY_DIGEST, type FileSettings, keys, MODEL_DEFAULTS,
  plain, policyDigest, type PolicyDocument, POLICY_LIMITS, type SourceCeilings,
  validate, whole,
} from "../../contract/src/policy-document";
import { Refusal } from "../../contract/src/refusal";
import type { OwnerStorage } from "./gate";
import { profileNames } from "../../contract/src/doors";
import { codePoints } from "../../contract/src/text";
import type {
  Call, Door, OwnerState,
} from "./owner-do";
import { clock } from "./clock";

/**
 * The files settings a document leaves in force: its own, and the defaults for the rest;
 * `max_bytes` is the owner's own lower one, or null for the tier's storage alone.
 */
function fileSettings(document: PolicyDocument):
    Omit<FileSettings, "max_bytes"> & { readonly max_bytes: number | null } {
  return { ...FILE_DEFAULTS, max_bytes: null, ...document.files };
}

/**
 * The files limits the object holds: the document's settings, with `max_bytes` the tier's
 * storage (`storage`, the control record's figure for the owner's tier), or the owner's own
 * where it is lower. Null when the tier's figure is not known here, which refuses every
 * change that adds file bytes (`TIER_UNREAD`): never a figure of ours in its place.
 */
export function fileLimits(document: PolicyDocument, storage: number | null):
    Omit<FileSettings, "max_bytes"> & { readonly max_bytes: number | null } {
  const settings = fileSettings(document);
  return { ...settings, max_bytes: storage === null ? null
    : Math.min(storage, settings.max_bytes ?? storage) };
}

/** The code mode settings a document leaves in force: its own, and the defaults for the rest. */
export function codemodeSettings(document: PolicyDocument): { readonly on: boolean } {
  return { ...CODEMODE_DEFAULTS, ...document.codemode };
}

/** The tools the owner's `custom` profile serves: its own choice, else every tool. */
export function customTools(document: PolicyDocument): readonly string[] {
  return document.tools?.custom ?? profileNames("custom");
}

/** The model settings a document leaves in force. */
interface ModelSettings {
  readonly turn_requests: number | null;
  readonly turn_tokens: number | null;
  readonly turn_cost: number | null;
  readonly daily_cost: number | null;
  readonly monthly_cost: number | null;
  readonly sources: { readonly [source: string]: SourceCeilings };
}

/** The model settings a document leaves in force: its own, and the defaults for the rest. */
export function modelSettings(document: PolicyDocument): ModelSettings {
  const { sources = {}, ...caps } = document.models ?? {};
  return { ...MODEL_DEFAULTS, ...caps, sources };
}

/** A held proposal's change: the JSON object an agent filed. */
export type Change = { readonly [key: string]: string | number | boolean | null | object };

interface PolicyState {
  readonly generation: number;
  readonly digest: string;
  readonly document: PolicyDocument;
}

/** What `read` shows the owner: the Python's `OwnerPolicy.projection()`. */
export interface Projection {
  readonly owner: string;
  readonly generation: number;
  readonly digest: string;
  readonly policy: PolicyDocument;
}

type ReceiptKind = "write" | "wake_limits" | "outside_grant" | "proposal" | "refusal";

interface ReceiptFields {
  readonly session_id?: string | null;
  readonly old_digest?: string | null;
  readonly new_digest?: string | null;
  readonly request_digest?: string | null;
  readonly generation?: number;
  readonly rule?: string | null;
  readonly decision?: string | null;
  readonly outcome?: string | null;
}

export type Receipt = {
  readonly at: number;
  readonly kind: string;
  readonly old_digest: string | null;
  readonly new_digest: string | null;
  readonly request_digest: string | null;
  readonly generation: number;
  readonly rule: string | null;
  readonly decision: string | null;
  readonly outcome: string | null;
};

type Usage = {
  pending_proposals: number;
  applied_proposals: number;
  owner_receipts: number;
  agent_receipts: number;
  refusal_receipts: number;
};

const BUCKETS = Object.freeze(["owner", "agent", "refusal"] as const);
type Bucket = (typeof BUCKETS)[number];

function bucket(kind: ReceiptKind): Bucket {
  return kind === "proposal" ? "agent" : kind === "refusal" ? "refusal" : "owner";
}

/** A card id: 1 to 128 characters, as the Python's. */
function cardId(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && codePoints(value) <= 128;
}

/** `expected_generation`: the policy generation the owner's change was made against. */
function expected(value: unknown): number {
  if (!whole(value) || value < 1) {
    throw new Refusal("invalid", "expected policy generation required");
  }
  return value;
}

export class Policy {
  readonly #storage: OwnerStorage;

  constructor(storage: OwnerStorage) {
    this.#storage = storage;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  #usage(): Usage {
    return this.#sql.exec<Usage>(
      "SELECT pending_proposals, applied_proposals, owner_receipts, agent_receipts, " +
        "refusal_receipts FROM policy_usage",
    ).one();
  }

  /** The policy generation as stored. */
  generation(): number {
    return this.#generation();
  }

  /**
   * The egress generation: moved by a write that changes the egress table, and by nothing
   * else, as a grant's authority generation moves apart from its revision. The sandbox's
   * network is made under it, and egress is admitted only under the current one.
   */
  /** The tools a custom profile serves: the owner's choice, or every tool (`customTools`). */
  customTools(): readonly string[] {
    return customTools(this.read().document);
  }

  /** Whether code mode is on (`codemode.on`, its default on). */
  codemode(): boolean {
    return codemodeSettings(this.read().document).on;
  }

  /** The model policy's settings, its caps among them (`modelSettings`). */
  models(): ModelSettings {
    return modelSettings(this.read().document);
  }

  egressGeneration(): number {
    return this.#sql.exec<{ egress_generation: number }>(
      "SELECT egress_generation FROM owner_policy").one().egress_generation;
  }

  #generation(): number {
    return this.#sql.exec<{ generation: number }>("SELECT generation FROM owner_policy").one()
      .generation;
  }

  #stored(): { generation: number; digest: string; document: string } {
    return this.#sql.exec<{ generation: number; digest: string; document: string }>(
      "SELECT generation, digest, document FROM owner_policy",
    ).one();
  }

  /** The policy as stored, its digest and its document rechecked. */
  read(): PolicyState {
    const row = this.#stored();
    const document: unknown = JSON.parse(row.document);
    if (policyDigest(document) !== row.digest) {
      throw new Error("policy digest mismatch");
    }
    return { generation: row.generation, digest: row.digest,
             document: validate(document) };
  }

  projection(owner: string): Projection {
    const { generation, digest, document } = this.read();
    return { owner, generation, digest, policy: document };
  }

  /** One receipt; each bucket keeps its newest `receiptsPerBucket`. */
  receipt(kind: ReceiptKind, fields: ReceiptFields, now: number): void {
    const sql = this.#sql;
    const into = bucket(kind);
    sql.exec(
      "INSERT INTO policy_receipts (bucket, kind, at, session_id, old_digest, new_digest, " +
        "request_digest, generation, rule, decision, outcome) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      into, kind, now, fields.session_id ?? null, fields.old_digest ?? null,
      fields.new_digest ?? null, fields.request_digest ?? null,
      fields.generation ?? this.#generation(), fields.rule ?? null,
      fields.decision ?? null, fields.outcome ?? null,
    );
    const over = this.#usage()[`${into}_receipts`] - POLICY_LIMITS.receiptsPerBucket;
    if (over > 0) {
      sql.exec("DELETE FROM policy_receipts WHERE seq IN (SELECT seq FROM policy_receipts " +
                 "WHERE bucket = ? ORDER BY seq LIMIT ?)", into, over);
    }
  }

  /** The last `receiptsListed` of each bucket, newest first; never a session id. */
  receipts(): Receipt[] {
    const listed = BUCKETS.flatMap((from) => this.#sql.exec<Receipt & { seq: number }>(
      "SELECT seq, at, kind, old_digest, new_digest, request_digest, generation, rule, " +
        "decision, outcome FROM policy_receipts WHERE bucket = ? ORDER BY seq DESC LIMIT ?",
      from, POLICY_LIMITS.receiptsListed,
    ).toArray());
    return listed.sort((a, b) => b.seq - a.seq).map(({ seq: _seq, ...receipt }) => receipt);
  }

  /**
   * The owner's write; with `proposal`, only as that held card's one apply. The held
   * proposal must be pending, unexpired, a `policy_proposal` of exactly this document
   * and generation with the digest the owner was shown, and is marked applied in the
   * same transaction.
   */
  write(sessionId: string, expectedGeneration: unknown, document: unknown,
        proposal: unknown, now: number): PolicyState {
    validate(document);
    const generation = expected(expectedGeneration);
    if (proposal !== null && (!plain(proposal) || !keys(proposal, ["request_id", "digest"])
                              || !cardId(proposal["request_id"])
                              || !cardId(proposal["digest"]))) {
      throw new Refusal("invalid", "invalid proposal apply");
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const row = this.#stored();
      if (row.generation !== generation) {
        throw new Refusal("refused", "policy generation changed");
      }
      if (proposal !== null) {
        const held = sql.exec<{ digest: string; document: string }>(
          "SELECT digest, document FROM policy_proposals WHERE request_id = ? " +
            "AND applied_at IS NULL AND at > ?",
          proposal["request_id"] as string, now - POLICY_LIMITS.proposalTtlS,
        ).toArray()[0];
        const change: unknown = held === undefined ? {} : JSON.parse(held.document);
        if (held === undefined || held.digest !== proposal["digest"] || !plain(change)
            || change["type"] !== "policy_proposal"
            || canonical(change["policy"]) !== canonical(document)
            || change["expected_generation"] !== generation) {
          throw new Refusal("refused", "proposal is not the pending card shown");
        }
        sql.exec("UPDATE policy_proposals SET applied_at = ? WHERE request_id = ?", now,
                 proposal["request_id"] as string);
      }
      const digest = policyDigest(document);
      // The egress generation moves only when the egress table does: the sandbox's network
      // is stale, and egress refused, only for a real network change (G4).
      const before = JSON.parse(row.document) as { egress?: unknown };
      const moved = canonical(before.egress) !== canonical((document as { egress?: unknown }).egress);
      sql.exec("UPDATE owner_policy SET generation = ?, digest = ?, document = ?, " +
                 "egress_generation = egress_generation + ?",
               generation + 1, digest, canonical(document), moved ? 1 : 0);
      this.receipt("write", { session_id: sessionId, old_digest: row.digest, new_digest: digest,
                              generation: generation + 1, outcome: "committed" }, now);
      return { generation: generation + 1, digest, document: document as PolicyDocument };
    });
  }

  /**
   * The cutover's import of the owner's old policy (`import.ts`), checked by the validator as
   * any write is, and receipted as one: set (true) only while the stored policy is the
   * default; nothing (false) once it is this document already; any other policy refused
   * loudly (`conflict`). Deleted at the cutover with the import doors.
   */
  imported(document: unknown, now: number): boolean {
    validate(document);
    const row = this.#stored();
    if (row.digest === policyDigest(document)) {
      return false;
    }
    if (row.digest !== DEFAULT_POLICY_DIGEST) {
      throw new Refusal("conflict",
                        "conflict: the owner's policy is neither the default nor the " +
                        "imported one");
    }
    this.write("import", row.generation, document, null, now);
    return true;
  }

  /**
   * An owner policy change kept outside the document (an agent's wake limits, stored on
   * its registry row where the ledger reads them): it bumps the generation and is
   * receipted like a write. Run inside the caller's transaction with the change itself.
   */
  changed(sessionId: string, expectedGeneration: unknown, kind: ReceiptKind,
          request: unknown, rule: string, now: number): number {
    const generation = expected(expectedGeneration);
    return this.#storage.transactionSync(() => {
      const row = this.#stored();
      if (row.generation !== generation) {
        throw new Refusal("refused", "policy generation changed");
      }
      this.#sql.exec("UPDATE owner_policy SET generation = ?", generation + 1);
      this.receipt(kind, { session_id: sessionId, old_digest: row.digest,
                           new_digest: row.digest, request_digest: policyDigest(request),
                           generation: generation + 1, rule, decision: "set",
                           outcome: "committed" }, now);
      return generation + 1;
    });
  }

  /**
   * An agent's proposal, receipted; with its card's id, held for the owner. Refiling a
   * held card with another change is refused (the same change again is a no-op);
   * expired proposals and all but the newest applied ones are pruned first.
   */
  propose(change: unknown, requestId: unknown, proposer: string, now: number):
      { request_digest: string; generation: number } {
    if (!plain(change) || codePoints(canonical(change)) > POLICY_LIMITS.maxChange) {
      throw new Refusal("invalid", "invalid policy proposal");
    }
    if (requestId !== null && !cardId(requestId)) {
      throw new Refusal("invalid", "invalid policy proposal");
    }
    const generation = this.#generation();
    const digest = policyDigest(change);
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      if (requestId !== null) {
        sql.exec("DELETE FROM policy_proposals WHERE at < ?", now - POLICY_LIMITS.proposalTtlS);
        const over = this.#usage().applied_proposals - POLICY_LIMITS.appliedProposals;
        if (over > 0) {
          sql.exec("DELETE FROM policy_proposals WHERE request_id IN (SELECT request_id " +
                     "FROM policy_proposals WHERE applied_at IS NOT NULL " +
                     "ORDER BY applied_at LIMIT ?)", over);
        }
        const held = sql.exec<{ digest: string }>(
          "SELECT digest FROM policy_proposals WHERE request_id = ?", requestId,
        ).toArray()[0];
        if (held !== undefined && held.digest !== digest) {
          throw new Refusal("refused", "a proposal is already held for this card");
        }
        if (held === undefined) {
          if (this.#usage().pending_proposals >= POLICY_LIMITS.pendingProposals) {
            throw new Refusal("refused", "too many pending policy proposals");
          }
          const share = sql.exec<{ pending: number }>(
            "SELECT pending FROM proposal_shares WHERE proposer = ?", proposer,
          ).toArray()[0]?.pending ?? 0;
          if (proposer !== "owner" && share >= POLICY_LIMITS.pendingPerClient) {
            throw new Refusal("refused",
                              "this client's pending policy proposals are at their limit");
          }
          sql.exec("INSERT INTO policy_proposals (request_id, digest, document, at, proposer) " +
                     "VALUES (?, ?, ?, ?, ?)", requestId, digest, canonical(change), now,
                   proposer);
        }
      }
      this.receipt("proposal", { request_digest: digest, generation, rule: "owner_policy",
                                 decision: "ask", outcome: "requested" }, now);
      return { request_digest: digest, generation };
    });
  }

  /**
   * In the mint's unit (`OwnerDO.keyMint`): an agent's held key request applied with its
   * key's grant, once (the Python's `_apply_key_request`). The card must be pending,
   * unexpired, an `api_key_request` whose scopes are exactly the grant's, with the digest
   * the owner was shown; it is marked applied here.
   */
  applyKeyRequest(proposal: unknown, scopes: unknown, now: number): void {
    if (!plain(proposal) || !keys(proposal, ["request_id", "digest"])
        || !cardId(proposal["request_id"]) || !cardId(proposal["digest"])) {
      throw new Refusal("invalid", "invalid proposal apply");
    }
    const held = this.#sql.exec<{ digest: string; document: string }>(
      "SELECT digest, document FROM policy_proposals WHERE request_id = ? " +
        "AND applied_at IS NULL AND at > ?",
      proposal["request_id"] as string, now - POLICY_LIMITS.proposalTtlS,
    ).toArray()[0];
    const change: unknown = held === undefined ? {} : JSON.parse(held.document);
    if (held === undefined || held.digest !== proposal["digest"] || !plain(change)
        || change["type"] !== "api_key_request"
        || canonical(change["scopes"]) !== canonical(scopes)) {
      throw new Refusal("refused", "proposal is not the pending card shown");
    }
    this.#sql.exec("UPDATE policy_proposals SET applied_at = ? WHERE request_id = ?", now,
                   proposal["request_id"] as string);
  }

  /** The pending, unexpired proposal held under a card id, and its digest, or null. */
  held(requestId: unknown, now: number): { change: Change; digest: string } | null {
    if (!cardId(requestId)) {
      return null;
    }
    const row = this.#sql.exec<{ digest: string; document: string }>(
      "SELECT digest, document FROM policy_proposals WHERE request_id = ? " +
        "AND applied_at IS NULL AND at > ?", requestId, now - POLICY_LIMITS.proposalTtlS,
    ).toArray()[0];
    return row === undefined ? null
      : { change: JSON.parse(row.document) as Change, digest: row.digest };
  }

  /**
   * `receipt_refusal`: a denied call, by the digest of what was asked, under its rule: an
   * owner-session call's, or an outside client's tool call's (`authority.ts`).
   */
  refusal(action: string, request: unknown, now: number,
          rule = "interactive_owner_session"): void {
    this.#storage.transactionSync(() => this.receipt("refusal", {
      request_digest: policyDigest({ action, request }), rule, decision: "deny",
      outcome: "refused",
    }, now));
  }
}

export interface PolicyWriteRequest {
  readonly expected_generation: number;
  readonly policy: PolicyDocument;
  readonly proposal?: { readonly request_id: string; readonly digest: string };
}
export interface ProposeRequest {
  readonly change: Change;
  readonly request_id?: string;
}
export interface CardRequest {
  readonly request_id: string;
}

// -- THE DOORS: the `policy` rows' handlers (`contract/doors.json`) -------------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands
// the object's state once the row's steps held: the bind, the account, the scope and the
// fields.

/** The `policy` doors' handlers, by door. */
export const policyDoors = Object.freeze({
  policyPropose(o: OwnerState, { account, r }: Call<ProposeRequest>):
      { request_digest: string; generation: number } {
    return o.policy.propose(r["change"], r["request_id"] ?? null,
                              account.origin?.client ?? "owner", clock());
  },
  policyRead(o: OwnerState, { account: { owner } }: Call<undefined>): Projection {
    return o.policy.projection(owner);
  },
  policyReceipts(o: OwnerState, _call: Call<undefined>): { receipts: Receipt[] } {
    return { receipts: o.policy.receipts() };
  },
  async policyWrite(o: OwnerState, door: Door<PolicyWriteRequest>): Promise<Projection> {
    const done = await o.synced(() => door.within(({ account: { owner }, session, r }) => {
      const written = o.policy.write(session, r["expected_generation"], r["policy"],
                                         r["proposal"] ?? null, clock());
      return { owner, generation: written.generation, digest: written.digest,
               policy: written.document };
    }));
    await o.door(() => o.egress.end("policy"));
    return done;
  },
  policyProposal(o: OwnerState, { r }: Call<CardRequest>): { change: Change; digest: string } {
    const held = o.policy.held(r["request_id"], clock());
    if (held === null) {
      throw new Refusal("refused", "no pending proposal for this card");
    }
    return held;
  },
});
