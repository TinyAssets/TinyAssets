/**
 * One owner's trusted state, in that owner's own SQLite-backed Durable Object (ADR-032).
 *
 * Only an edge Worker (the binder) turns a verified credential into an owner and a
 * caller, and it reaches this object by `getByName(owner)`. Every RPC method takes that
 * {@link Binding} as its first, typed argument; nothing in a request body names the
 * owner or the caller. The object serves only the owner it is named by (`ctx.id.name`),
 * refuses when it has no name, and also stores that owner on first use and refuses any
 * other, so a binder bug that reaches the wrong object fails loudly instead of acting
 * there.
 *
 * What the binder verified, the object takes as typed binding fields: the outside
 * client a call comes through (`origin`, `source`, `authenticated_at`) and the owner's
 * session cookie (`session`). What it decides from them, it decides itself, from its
 * own rows: every outside call, at every door, is admitted against that client's grant
 * and held to its scopes (`grants.ts`: `read_graph`, `write_graph`, `propose_policy`,
 * `read_files`, `write_files`), and every widening checks the owner's live session row
 * (`sessions.ts`), a refused one leaving a refusal receipt in the policy receipts
 * (`policy.ts`). An outside client's call never carries the owner's session, with one
 * pinned exception: the owner's browser registering an agent for an API key it holds
 * (`registerAgent`), whose origin the object re-derives from the key id. An MCP client's
 * agent registers through a card the client files on its own verified call
 * (`registerPropose`, `registrations.ts`), which the owner applies in session by its id.
 * Every other door refuses the pair.
 *
 * Its code cannot reach another owner's object: the Worker binds only `FILES`, the files
 * Worker, which itself binds no Durable Object and no service; the runtime hands it no
 * `ctx.exports` (`disable_ctx_exports`); and its source never names `exports` or imports
 * `env` (the config and self-reach guards). The owner's file bytes reach the bucket only
 * through `FILES`, under the owner's namespace, which only this object holds
 * (`files.ts`). The module keeps no mutable state at top level (the module-state
 * guard), since objects of one class can share an isolate.
 *
 * Every public method is an RPC any binding can call, so the class has no public method
 * but its doors (`RPC_METHODS` in the tests pins the list). Its timed work is the one
 * schedule's (`schedule.ts`): lanes on the Agents SDK Lifecycle job queue, which owns the
 * object's single physical alarm, as PiHarness's wake job will once it is installed. The
 * object is a plain Durable Object that holds a Lifecycle, never the SDK's `Agent`, and
 * installs none of Lifecycle's runtime handlers: `alarm` alone hands the runtime's alarm to
 * it, so no `fetch` or WebSocket handler joins the surface. Claiming and settling wakes is
 * the `wakes` lane's own, in process, with the doorbell's {@link WakeSender}; file
 * housekeeping (expired uploads and temporary directories, history prunes, garbage chunk
 * deletes) is the `files` lane's. No door reaches either.
 */
import { Lifecycle } from "agents/lifecycle";
import { DurableObject } from "cloudflare:workers";

import { Collections, Conflict, type HistoryEntry, type Page, type Row } from "./collections";
import { Invalid, Refused } from "./errors";
import {
  type Change as FileChange, CHUNK_BYTES, type ChunkRef, type ChunkStore, type Content,
  type Entry,
  FileConflict, Files, type Lease, type SyncOp, type SyncOutcome, type Usage,
} from "./files";
import { type Capability, type Client, type Grant, Grants, holds, type Scope } from "./grants";
import {
  type Agent, clock, Ledger, type MailPage, type Origin, outsideOrigin, type Posted,
  registrant, type Source,
} from "./ledger";
import {
  type Change, fileSettings, Policy, type PolicyDocument, type Projection, type Receipt,
} from "./policy";
import { type Card, Registrations } from "./registrations";
import { Schedule } from "./schedule";
import { runWork, Scheduled, type WorkRunner } from "./scheduled";
import { migrate } from "./schema";
import { Sessions } from "./sessions";
import { principal } from "./text";
import { runWakes, type WakeSender, WAKES_PER_ALARM } from "./wakes";

/**
 * Whose object this is, who is asking and through what: set by the binder from a
 * verified credential, the one binding every door takes. `caller` is the owner's own
 * account, or, at the collection doors alone, another owner visiting (origin null).
 * `origin` is the verified outside client the owner's account acts through (an outside
 * agent), or null. The object takes every sender and reader from here, never from a
 * request field.
 */
export interface Binding {
  readonly owner: string;
  readonly caller: string;
  readonly origin: Origin | null;
  /** How the binder verified `origin`: an MCP client or an API key. Every outside call. */
  readonly source?: Source;
  /**
   * When the credential was signed in, in whole seconds: an outside client's, which a
   * revoke fences, or the owner's interactive sign-in, which opens a session.
   */
  readonly authenticated_at?: number;
  /** The owner's protected-session cookie, as the browser presented it. */
  readonly session?: string;
}

export type { Origin, Source, WakeSender };

export interface RegisterRequest {
  readonly command_center_id: string;
  readonly display_name: string;
  readonly delivery_method: string;
  readonly recipe_id: string | null;
}

export interface DisableRequest {
  readonly agent_id: string;
  readonly reason: string;
}

export interface AgentRequest {
  readonly agent_id: string;
}

export interface WakeLimitsRequest {
  readonly agent_id: string;
  readonly max_wakes_per_hour: number;
  readonly batch_window_s: number;
  readonly expected_generation: number;
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

/** The owner's apply of a held registration: the card and the digest it was shown. */
export interface CardApply {
  readonly card_id: string;
  readonly digest: string;
}

export interface RegistrationCardRequest {
  readonly card_id: string;
}

export interface GrantRequest {
  readonly client: string;
  readonly source: string;
  readonly mode: string;
  readonly scopes: Scope[];
  readonly expected_generation: number;
  readonly name: string;
}

export interface ClientRequest {
  readonly client: string;
}

export interface PostRequest {
  readonly room_id: string;
  readonly client_message_id: string;
  readonly body: string;
  readonly parent_id: string | null;
}

export interface ReadRequest {
  readonly limit: number;
  readonly max_bytes?: number;
  readonly pad_bytes?: number;
}

export interface AckRequest {
  readonly through: number;
}

export interface SubscriberRequest {
  readonly command_center_id: string;
  readonly agent_id: string;
}

/**
 * The object's environment: one service binding, `FILES`, to the files Worker, which
 * binds no Durable Object and no service (the config guard checks both configs), and
 * never a binding to its own namespace.
 */
export interface OwnerEnv {
  readonly FILES: ChunkStore;
}

export interface FileRequest {
  readonly path: string;
}

export interface FileListRequest {
  readonly dir: string;
  readonly after?: string;
  readonly limit: number;
}

export interface FileReadRequest {
  readonly path: string;
  readonly etag?: string;
}

export interface FileChunksRequest {
  readonly path: string;
  readonly version: number;
  readonly first: number;
  readonly count: number;
}

export interface FileWriteRequest {
  readonly path: string;
  readonly expected_etag: string | null;
  readonly content: Uint8Array;
  readonly mode?: number;
}

export interface UploadBeginRequest {
  readonly chunks: ChunkRef[];
}

export interface UploadChunksRequest {
  readonly intent_id: string;
  readonly chunks: { sha256: string; bytes: Uint8Array }[];
}

export interface UploadCommitRequest {
  readonly intent_id: string;
  readonly path: string;
  readonly expected_etag: string | null;
  readonly size: number;
  readonly chunks: string[];
  readonly mode?: number;
}

export interface IntentRequest {
  readonly intent_id: string;
}

export interface MkdirRequest {
  readonly path: string;
  readonly expected_etag: string | null;
}

export interface RemoveRequest {
  readonly path: string;
  readonly expected_etag: string | null;
  readonly recursive: boolean;
}

export interface RenameRequest {
  readonly from: string;
  readonly to: string;
  readonly expected_etag: string | null;
  readonly replace: boolean;
}

export interface FileHistoryRequest {
  readonly path: string;
  readonly before?: number;
  readonly limit: number;
}

export interface VersionRequest {
  readonly path: string;
  readonly version: number;
}

export interface RestoreRequest extends VersionRequest {
  readonly expected_etag: string | null;
}

export interface ChangesRequest {
  readonly after: number;
  readonly limit: number;
}

export interface LeaseRequest {
  readonly lease_id: string;
  readonly epoch: number;
}

export interface SyncCommitRequest extends LeaseRequest {
  readonly intent_id: string | null;
  readonly ops: SyncOp[];
}

/** A lost file compare-and-swap: answered with the entry as it stands. */
export interface FileConflicted {
  readonly conflict: Entry | null;
}

export interface WriteRequest {
  readonly collection: string;
  readonly row_id: string;
  readonly body: string;
  readonly expected_etag: string;
  readonly private: boolean;
  readonly history_op?: string;
}

export interface PullRequest {
  readonly collection: string;
  readonly after: number;
  readonly limit: number;
}

export interface KeyRequest {
  readonly collection: string;
  readonly row_id: string;
}

export interface DeleteRequest {
  readonly collection: string;
  readonly row_id: string;
  readonly expected_etag: string;
}

export interface ClearRequest {
  readonly collection: string;
  readonly writer: string | null;
}

/** A lost compare-and-swap is an answer carrying the row as it stands, not a refusal. */
export interface Conflicted {
  readonly conflict: Row | null;
}

/** What a call is bound to: its owner and caller, its outside client and that client's grant. */
interface Account {
  readonly owner: string;
  readonly caller: string;
  readonly origin: Origin | null;
  readonly grant: Grant | null;
}

/**
 * Who else an account door serves. `visitors`: another owner may call, as itself (the
 * collection doors). `registering`: an outside origin may come with the owner's session
 * (the owner's browser registering an agent for that client).
 */
interface Serves {
  readonly visitors?: boolean;
  readonly registering?: boolean;
}

export const OUTSIDE_SESSION = "an outside client's call never carries the owner's session";
export const MCP_REGISTERS_BY_CARD = "an MCP client's agent registers through the card it files";

/**
 * The request's fields are exactly `required`, plus any of `optional`, and none is
 * `undefined`: a field sent is a value, as the Python door refused `None`.
 */
function fields(request: unknown, required: readonly string[],
                optional: readonly string[] = [],
                refusal = "invalid collection request"): Record<string, unknown> {
  if (typeof request !== "object" || request === null || Array.isArray(request)) {
    throw new Invalid(refusal);
  }
  const entries = Object.entries(request);
  const keys = entries.map(([key]) => key);
  if (!required.every((key) => keys.includes(key))
      || !keys.every((key) => required.includes(key) || optional.includes(key))
      || entries.some(([, value]) => value === undefined)) {
    throw new Invalid(refusal);
  }
  return request as Record<string, unknown>;
}

/** A ledger request's fields: exactly these, never a sender, reader or owner. */
function ledgerFields(request: unknown, required: readonly string[],
                      optional: readonly string[] = []): Record<string, unknown> {
  return fields(request, required, optional, "invalid ledger request");
}

function policyFields(request: unknown, required: readonly string[],
                      optional: readonly string[] = []): Record<string, unknown> {
  return fields(request, required, optional, "invalid policy request");
}

function conflicted<T>(act: () => T): T | Conflicted {
  try {
    return act();
  } catch (error) {
    if (error instanceof Conflict) {
      return { conflict: error.current };
    }
    throw error;
  }
}

function fileFields(request: unknown, required: readonly string[],
                    optional: readonly string[] = []): Record<string, unknown> {
  return fields(request, required, optional, "invalid file request");
}

async function fileConflicted<T>(act: () => T | Promise<T>): Promise<T | FileConflicted> {
  try {
    return await act();
  } catch (error) {
    if (error instanceof FileConflict) {
      return { conflict: error.current };
    }
    throw error;
  }
}

/** Who a file change is by, in its history: the owner, or the outside client it came through. */
function fileWriter(account: Account): string {
  return account.origin === null ? "owner" : `client:${account.origin.client}`;
}

function flag(value: unknown, what: string): boolean {
  if (typeof value !== "boolean") {
    throw new Invalid(`${what} is true or false`);
  }
  return value;
}

export class OwnerDO extends DurableObject<OwnerEnv> {
  readonly #collections: Collections;
  readonly #policy: Policy;
  readonly #grants: Grants;
  readonly #sessions: Sessions;
  readonly #ledger: Ledger;
  readonly #files: Files;
  readonly #work: Scheduled;
  readonly #registrations: Registrations;
  readonly #schedule: Schedule;
  readonly #lifecycle: Lifecycle<OwnerEnv>;
  /**
   * What rings outside agents' doorbells, called in process by the `wakes` lane. None
   * until the doorbell slice (tasks 7) constructs one here; until then the lane only
   * recovers lapsed claims, and open wakes and revokes stay pending without a job.
   */
  readonly #sender: WakeSender | null = null;
  /**
   * What runs due scheduled turns, called in process by the `work` lane. None until the
   * agent (tasks 5) constructs one; until then the lane only recovers lapsed claims, and
   * pending work stays held without a job.
   */
  readonly #runner: WorkRunner | null = null;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    super(ctx, env);
    this.#collections = new Collections(ctx.storage);
    this.#policy = new Policy(ctx.storage);
    this.#grants = new Grants(ctx.storage, this.#policy);
    this.#sessions = new Sessions(ctx.storage);
    this.#ledger = new Ledger(ctx.storage, this.#grants);
    this.#files = new Files(ctx.storage, env.FILES,
                            () => fileSettings(this.#policy.read().document));
    this.#work = new Scheduled(ctx.storage, this.#grants);
    this.#registrations = new Registrations(ctx.storage, this.#grants);
    // The lanes of the one schedule. There is no global tick; each owner's object
    // schedules only itself.
    this.#schedule = new Schedule({
      // Lapsed claims, then due wakes and revokes; open rows count only with a doorbell.
      wakes: { next: (now) => this.#ledger.nextDue(now, this.#sender !== null),
               run: () => runWakes(this.#ledger, this.#sender, clock, WAKES_PER_ALARM) },
      files: { next: (now) => this.#files.nextAlarm(now), run: (now) => this.#files.collect(now) },
      // Stamped scheduled work: lapsed claims, then, with a runner, due work rechecked
      // against the authority it was made under.
      work: { next: (now) => this.#work.nextDue(now, this.#runner !== null),
              run: () => runWork(this.#work, this.#runner, clock) },
    }, clock);
    this.#lifecycle = new Lifecycle<OwnerEnv>(this).use(this.#schedule);
    void ctx.blockConcurrencyWhile(async () => {
      migrate(ctx.storage);
      // The schedule as stored, for this code's lanes and sender: a deploy that adds a
      // doorbell gives the wakes held while there was none their job.
      await this.#schedule.sync();
    });
  }

  /** The runtime's alarm, which Lifecycle owns: it drives the due jobs, then re-arms. */
  override async alarm(): Promise<void> {
    await this.#lifecycle.alarm();
  }

  /**
   * An account call's owner, origin and grant. The account is its owner's own: the
   * caller must be the owner's account, acting itself (`origin` null) or through a
   * verified outside client, which is admitted here against its grant (P0-3) before any
   * door acts. A refusal here leaves no receipt: the call never reached the account.
   */
  #account(binding: Binding, serves: Serves = {}): Account {
    const { owner, caller } = this.#bind(binding);
    if (!("origin" in binding)) {
      throw new Invalid("invalid outside origin");
    }
    const origin = outsideOrigin(binding.origin);
    if (caller !== owner && !(serves.visitors === true && origin === null)) {
      throw new Refused("the account answers its owner's own callers only");
    }
    const signedIn = binding.authenticated_at ?? null;
    if (signedIn !== null && (typeof signedIn !== "number" || !Number.isSafeInteger(signedIn)
                              || signedIn < 0)) {
      throw new Invalid("authenticated_at is a time in whole seconds");
    }
    if (origin === null) {
      return { owner, caller, origin, grant: null };
    }
    // The owner's cookie rides only the owner's own browser requests (tasks 2); an
    // outside client's call carrying it is a binder bug or a replay, refused before any
    // grant is recorded. Only an API key's registration carries both: the binder checked
    // the owner holds the key, and the object re-derives its origin from the key id.
    if (binding.session !== undefined && serves.registering !== true) {
      throw new Refused(OUTSIDE_SESSION);
    }
    const source = registrant(binding.source, origin);
    if (binding.session !== undefined && source.kind !== "api_key") {
      throw new Refused(MCP_REGISTERS_BY_CARD);
    }
    return { owner, caller, origin,
             grant: this.#grants.admit(origin.client, source.kind, signedIn, clock()) };
  }

  /** The owner itself, on its own account, never an outside client. */
  #owner(binding: Binding, action: string): Account {
    const account = this.#account(binding);
    if (account.origin !== null) {
      throw new Refused(`only the owner may ${action}`);
    }
    return account;
  }

  /**
   * A widening: the owner's live session, checked against the object's own session row
   * (S30, N10). `owner` names what only the owner itself, never an outside client, may
   * do in it. A refusal after the account is bound leaves a refusal receipt of what was
   * asked, written once the refused change has rolled back.
   */
  #widening<T>(binding: Binding, action: string, request: unknown, owner: string | null,
               act: (account: Account, sessionId: string) => T, serves: Serves = {}): T {
    const account = this.#account(binding, serves);
    try {
      // An outside client holds no owner session, so what only the owner may do is
      // refused as that before the session is looked for.
      if (owner !== null && account.origin !== null) {
        throw new Refused(`only the owner may ${owner}`);
      }
      return act(account, this.#sessions.live(binding.session, clock()));
    } catch (error) {
      if (error instanceof Refused || error instanceof Invalid) {
        try {
          this.#policy.refusal(action, { request, origin: account.origin }, clock());
        } catch {
          console.error("owner-session refusal receipt unavailable");  // refuse regardless
        }
      }
      throw error;
    }
  }

  /**
   * A scoped outside client reaches its own mailbox only with `capability` granted, as
   * the `main` agent, in `center` (default: the command center its one enabled agent is
   * registered in). `full` and the owner pass.
   */
  #scope(account: Account, capability: Capability, center?: unknown): void {
    if (account.grant?.mode !== "scoped") {
      return;
    }
    let universe = center;
    if (universe === undefined) {
      const mine = this.#ledger.agents(account.origin)
        .filter((agent) => agent.disabled_reason === null);
      if (mine.length !== 1) {
        throw new Refused("this outside identity is not one enabled agent");
      }
      universe = mine[0]!.command_center_id;
    }
    if (typeof universe !== "string"
        || !holds(account.grant, { universe, agent: "main", capability })) {
      throw new Refused("outside client capability not granted");
    }
  }

  // -- SESSIONS: the owner's protected interactive sessions ---------------------

  /** A verified interactive sign-in opens a session; its cookie is returned once. */
  sessionOpen(binding: Binding): { session: string; expires_at: number } {
    this.#owner(binding, "open its session");
    return this.#sessions.open(binding.authenticated_at, clock());
  }

  sessionExtend(binding: Binding): { expires_at: number } {
    this.#owner(binding, "renew its session");
    return this.#sessions.extend(binding.session, clock());
  }

  sessionClose(binding: Binding): { closed: number } {
    this.#owner(binding, "sign out");
    return this.#sessions.close(binding.session);
  }

  /** Every session signed out: account revocation and deletion (`revoke_owner`). */
  sessionCloseAll(binding: Binding): { closed: number } {
    this.#owner(binding, "sign out");
    return this.#sessions.closeAll();
  }

  // -- OWNER_POLICY ------------------------------------------------------------

  policyRead(binding: Binding): Projection {
    const { owner } = this.#owner(binding, "read its policy");
    return this.#policy.projection(owner);
  }

  policyReceipts(binding: Binding): { receipts: Receipt[] } {
    this.#owner(binding, "read its policy");
    return { receipts: this.#policy.receipts() };
  }

  /** The owner's write, in its session; with `proposal`, a held card's one apply. */
  policyWrite(binding: Binding, request: PolicyWriteRequest): Projection {
    return this.#widening(binding, "write", request, "write its policy", ({ owner }, id) => {
      const r = policyFields(request, ["expected_generation", "policy"], ["proposal"]);
      const written = this.#policy.write(id, r["expected_generation"], r["policy"],
                                         r["proposal"] ?? null, clock());
      return { owner, generation: written.generation, digest: written.digest,
               policy: written.document };
    });
  }

  /**
   * An agent's proposal, the owner's or an admitted outside client's; never applied. A
   * scoped client proposes only with `propose_policy` granted where its agent is.
   */
  policyPropose(binding: Binding, request: ProposeRequest):
      { request_digest: string; generation: number } {
    const account = this.#account(binding);
    const r = policyFields(request, ["change"], ["request_id"]);
    this.#scope(account, "propose_policy");
    return this.#policy.propose(r["change"], r["request_id"] ?? null,
                                account.origin?.client ?? "owner", clock());
  }

  /** The pending card the owner is shown, and the digest its apply must name. */
  policyProposal(binding: Binding, request: CardRequest):
      { change: Change; digest: string } {
    this.#owner(binding, "read its policy");
    const r = policyFields(request, ["request_id"]);
    const held = this.#policy.held(r["request_id"], clock());
    if (held === null) {
      throw new Refused("no pending proposal for this card");
    }
    return held;
  }

  /**
   * Owner policy (W3): an agent's hourly wake cap and batch window, kept on its registry
   * row where the ledger reads them, and changed like any policy write: in the owner's
   * session, against the policy generation, receipted.
   */
  policyWakeLimits(binding: Binding, request: WakeLimitsRequest):
      { agent: Agent; generation: number } {
    return this.#widening(binding, "wake_limits", request, "change its agents' policy",
                          (_account, id) => {
      const r = policyFields(request, ["agent_id", "max_wakes_per_hour", "batch_window_s",
                                       "expected_generation"]);
      return this.ctx.storage.transactionSync(() => {
        const agent = this.#ledger.setWakeLimits(r["agent_id"], r["max_wakes_per_hour"],
                                                 r["batch_window_s"]);
        const limits = { agent_id: agent.agent_id, max_wakes_per_hour: agent.max_wakes_per_hour,
                         batch_window_s: agent.batch_window_s };
        const generation = this.#policy.changed(id, r["expected_generation"], "wake_limits",
                                                limits, agent.agent_id, clock());
        return { agent, generation };
      });
    });
  }

  // -- OUTSIDE_CLIENTS: grants (P0-3) ---------------------------------------------

  grantList(binding: Binding): { clients: Client[] } {
    this.#owner(binding, "read its outside clients");
    return { clients: this.#grants.list() };
  }

  /** The owner's widening, in its session; the client's agents' wakes end with it. */
  async grantSet(binding: Binding, request: GrantRequest):
      Promise<{ client: string; mode: string; scopes: Scope[]; generation: number }> {
    const set = this.#widening(binding, "outside_set", request,
                               "change an outside client's grant", (_account, id) => {
      const r = policyFields(request, ["client", "source", "mode", "scopes",
                                       "expected_generation", "name"]);
      return this.ctx.storage.transactionSync(() => {
        const changed = this.#grants.set(id, r as unknown as GrantRequest, clock());
        this.#endClient(changed.client);
        return changed;
      });
    });
    await this.#schedule.sync();
    return set;
  }

  /** A narrowing the owner's account makes without a session, as a key's revoke does. */
  async grantRevoke(binding: Binding, request: ClientRequest):
      Promise<{ client: string; revoked: boolean; generation: number }> {
    this.#owner(binding, "revoke an outside client's grant");
    const r = policyFields(request, ["client"]);
    const revoked = this.ctx.storage.transactionSync(() => {
      const result = this.#grants.revoke(r["client"], clock());
      this.#endClient(result.client);
      return result;
    });
    await this.#schedule.sync();
    return revoked;
  }

  /**
   * Forget a revoked grant no pre-revoke credential can use (slice 3's review L1): the
   * binder calls it for an API key once the directory has deleted the key (tasks 2); an
   * MCP client's goes once its revoke is older than `MCP_SIGN_IN_LIFETIME_S`. A narrowing,
   * so the owner's account makes it without a session.
   */
  grantForget(binding: Binding, request: ClientRequest): { client: string; forgotten: boolean } {
    this.#owner(binding, "forget an outside client's grant");
    const r = policyFields(request, ["client"]);
    return this.#grants.forget(r["client"], clock());
  }

  /**
   * A grant change ends what its client set going (P0-3's M3): its agents' open wakes and
   * callbacks, its pending scheduled work and its held registrations, in the caller's
   * transaction.
   */
  #endClient(client: string): void {
    this.#ledger.suppressClient(client, clock());
    this.#work.endClient(client, clock());
    this.#registrations.endClient(client);
  }

  // -- OUTSIDE_AGENTS ---------------------------------------------------------

  /** The owner sees every agent; an outside caller only its own identity's. */
  outsideAgents(binding: Binding): { agents: Agent[] } {
    const { origin } = this.#account(binding);
    return { agents: this.#ledger.agents(origin) };
  }

  /**
   * Registering an agent needs the owner's protected session (S30), in one of two forms.
   * The owner itself applies an MCP client's held registration (`CardApply`): the card
   * the client filed on its own verified call, which captured its origin. Or the owner's
   * browser registers an agent for an API key it holds, naming the key's identity
   * (`origin`, re-derived from the key id): the one door an outside origin reaches
   * together with the owner's session.
   */
  async registerAgent(binding: Binding, request: RegisterRequest | CardApply): Promise<Agent> {
    const agent = this.#widening(binding, "OUTSIDE_AGENTS.register", request, null,
                                 ({ origin }) => {
      if (origin === null) {
        const r = ledgerFields(request, ["card_id", "digest"]);
        return this.ctx.storage.transactionSync(() => {
          const card = this.#registrations.take(r["card_id"], r["digest"], clock());
          return this.#ledger.register(card.origin, card.source,
                                       card.request as unknown as RegisterRequest);
        });
      }
      const r = ledgerFields(request, ["command_center_id", "display_name", "delivery_method",
                                       "recipe_id"]);
      return this.#ledger.register(origin, binding.source, r as unknown as RegisterRequest);
    }, { registering: true });
    await this.#schedule.sync();
    return agent;
  }

  /**
   * An MCP client files the registration of its agent on its own verified call, so the
   * object captures the origin the binder verified; the owner applies it in session
   * (`registerAgent`). Never applied here. Filing again replaces the identity's card.
   */
  registerPropose(binding: Binding, request: RegisterRequest):
      { card_id: string; digest: string; expires_at: number } {
    const account = this.#account(binding);
    if (account.origin === null || account.grant === null) {
      throw new Refused("only an outside client files its own agent's registration");
    }
    if (registrant(binding.source, account.origin).kind !== "mcp") {
      throw new Refused("an API key's agent registers from the owner's browser with the key");
    }
    const r = ledgerFields(request, ["command_center_id", "display_name", "delivery_method",
                                     "recipe_id"]);
    return this.#registrations.file(account.origin, (binding.source as Source).ref, {
      client: account.origin.client, generation: account.grant.generation,
      authenticated_at: binding.authenticated_at ?? null }, r, clock());
  }

  /** The pending registration card the owner is shown, and the digest its apply names. */
  registerProposal(binding: Binding, request: RegistrationCardRequest): Card {
    this.#owner(binding, "read its agents' registrations");
    const r = ledgerFields(request, ["card_id"]);
    const card = this.#registrations.card(r["card_id"], clock());
    if (card === null) {
      throw new Refused("no pending registration for this card");
    }
    return card;
  }

  /**
   * The owner disables any agent in its session. An outside client cannot reach it: it
   * holds no owner session, and its call never carries one.
   */
  async disableAgent(binding: Binding, request: DisableRequest): Promise<Agent> {
    const agent = this.#widening(binding, "OUTSIDE_AGENTS.disable", request, null,
                                 ({ origin }) => {
      const r = ledgerFields(request, ["agent_id", "reason"]);
      return this.#ledger.disable(r["agent_id"], r["reason"], origin);
    });
    await this.#schedule.sync();
    return agent;
  }

  /** Only the owner itself, in its session, deletes an agent. */
  async deleteAgent(binding: Binding, request: AgentRequest):
      Promise<{ agent_id: string; messages: number }> {
    const deleted = this.#widening(binding, "OUTSIDE_AGENTS.delete", request,
                                   "change its agents' policy", () => {
      const r = ledgerFields(request, ["agent_id"]);
      return this.#ledger.remove(r["agent_id"]);
    });
    await this.#schedule.sync();
    return deleted;
  }

  // -- ROOM_POST and MAILBOX_RECEIPT -------------------------------------------

  async roomPost(binding: Binding, request: PostRequest): Promise<Posted> {
    const account = this.#account(binding);
    const r = ledgerFields(request, ["room_id", "client_message_id", "body", "parent_id"]);
    this.#scope(account, "write_graph");
    const posted = this.#ledger.post(account.origin, r["room_id"], r["client_message_id"],
                                     r["body"], r["parent_id"]);
    await this.#schedule.sync();
    return posted;
  }

  mailboxRead(binding: Binding, request: ReadRequest): MailPage {
    const account = this.#account(binding);
    const r = ledgerFields(request, ["limit"], ["max_bytes", "pad_bytes"]);
    this.#scope(account, "read_graph");
    return this.#ledger.read(account.origin, r["limit"], r["max_bytes"] ?? null,
                             r["pad_bytes"] ?? 0);
  }

  mailboxAck(binding: Binding, request: AckRequest): { reader: string; through: number } {
    const account = this.#account(binding);
    const r = ledgerFields(request, ["through"]);
    this.#scope(account, "write_graph");
    return this.#ledger.ack(account.origin, r["through"]);
  }

  /**
   * W4: a `mailbox.message` subscribe must name the caller's own ringing agent, and a
   * scoped client needs `read_graph` in that command center. The answer's `generation`
   * tags the callback the subscribe door stores, so a revoke made before it never
   * erases it.
   */
  mailboxSubscriber(binding: Binding, request: SubscriberRequest):
      { agent_id: string; generation: number } {
    const account = this.#account(binding);
    const r = ledgerFields(request, ["command_center_id", "agent_id"]);
    this.#scope(account, "read_graph", r["command_center_id"]);
    const generation = this.#ledger.subscriber(account.origin, r["command_center_id"],
                                               r["agent_id"]);
    return { agent_id: r["agent_id"] as string, generation };
  }

  /**
   * The owner this object serves: the name it was reached by, and every other one
   * refused. The stored owner, fixed at first use, is a second check of the same.
   */
  #bind(binding: Binding): { owner: string; caller: string } {
    if (typeof binding !== "object" || binding === null) {
      throw new Invalid("invalid owner principal");
    }
    const owner = principal(binding.owner);
    const caller = principal(binding.caller);
    const named = this.ctx.id.name;
    if (named === undefined) {
      throw new Refused("this object is not named by an owner");
    }
    if (named !== owner) {
      throw new Refused("this object belongs to another owner");
    }
    const sql = this.ctx.storage.sql;
    const stored = sql.exec<{ owner: string }>("SELECT owner FROM object_owner").toArray()[0];
    if (stored === undefined) {
      sql.exec("INSERT INTO object_owner (only, owner) VALUES (1, ?)", owner);
    } else if (stored.owner !== owner) {
      throw new Refused("this object belongs to another owner");
    }
    return { owner, caller };
  }

  #ownerOnly(owner: string, caller: string, action: string): void {
    if (caller !== owner) {
      throw new Refused(`only the owner may ${action} collection rows`);
    }
  }

  /**
   * A collection door's caller: the owner, a visiting owner, or an outside client the
   * owner's account acts through, admitted against its grant and held to `capability`.
   */
  #collector(binding: Binding, capability: Capability): { owner: string; caller: string } {
    const account = this.#account(binding, { visitors: true });
    this.#scope(account, capability);
    return account;
  }

  collectionWrite(binding: Binding, request: WriteRequest): { row: Row } | Conflicted {
    const { owner, caller } = this.#collector(binding, "write_graph");
    const r = fields(request, ["collection", "row_id", "body", "expected_etag", "private"],
                     ["history_op"]);
    return conflicted(() => ({
      row: this.#collections.write(owner, caller, r["collection"] as string,
                                   r["row_id"] as string, r["body"], r["expected_etag"],
                                   r["private"], "history_op" in r ? r["history_op"] : "write"),
    }));
  }

  collectionPull(binding: Binding, request: PullRequest): Page {
    const { caller } = this.#collector(binding, "read_graph");
    const r = fields(request, ["collection", "after", "limit"]);
    return this.#collections.pull(caller, r["collection"] as string, r["after"], r["limit"]);
  }

  collectionGet(binding: Binding, request: KeyRequest): { row: Row | null } {
    const { caller } = this.#collector(binding, "read_graph");
    const r = fields(request, ["collection", "row_id"]);
    return this.#collections.get(caller, r["collection"] as string, r["row_id"] as string);
  }

  collectionHistory(binding: Binding, request: KeyRequest): { entries: HistoryEntry[] } {
    const { caller } = this.#collector(binding, "read_graph");
    const r = fields(request, ["collection", "row_id"]);
    return this.#collections.history(caller, r["collection"] as string, r["row_id"] as string);
  }

  collectionDelete(binding: Binding, request: DeleteRequest): { deleted: number } | Conflicted {
    const { owner, caller } = this.#collector(binding, "write_graph");
    this.#ownerOnly(owner, caller, "delete");
    const r = fields(request, ["collection", "row_id", "expected_etag"]);
    return conflicted(() => this.#collections.delete(
      caller, r["collection"] as string, r["row_id"] as string, r["expected_etag"],
    ));
  }

  collectionClear(binding: Binding, request: ClearRequest): { deleted: number } {
    const { owner, caller } = this.#collector(binding, "write_graph");
    this.#ownerOnly(owner, caller, "clear");
    const r = fields(request, ["collection", "writer"]);
    return this.#collections.clear(caller, r["collection"] as string, r["writer"]);
  }

  // -- FILES: the owner's file tree (plan §6) --------------------------------------

  /**
   * A file door's account: the owner's own, or an outside client admitted against its
   * grant and held to `read_files` or `write_files`. No visiting owner reaches another's
   * files.
   */
  #filer(binding: Binding, capability: "read_files" | "write_files"): Account {
    const account = this.#account(binding);
    this.#scope(account, capability);
    return account;
  }

  fileStat(binding: Binding, request: FileRequest): { entry: Entry | null } {
    this.#filer(binding, "read_files");
    const r = fileFields(request, ["path"]);
    return { entry: this.#files.stat(r["path"]) };
  }

  fileList(binding: Binding, request: FileListRequest):
      { entries: Entry[]; next: string | null } {
    this.#filer(binding, "read_files");
    const r = fileFields(request, ["dir", "limit"], ["after"]);
    return this.#files.list(r["dir"], r["after"] ?? null, r["limit"]);
  }

  /**
   * A file's entry and content: its bytes when small, else its chunk list for
   * `fileReadChunks`. With `etag` still current, only the entry (`not_modified`).
   */
  fileRead(binding: Binding, request: FileReadRequest):
      { entry: Entry; not_modified?: true; inline?: Uint8Array; chunks?: ChunkRef[] } {
    this.#filer(binding, "read_files");
    const r = fileFields(request, ["path"], ["etag"]);
    const content: Content = this.#files.open(r["path"]);
    if (r["etag"] !== undefined && r["etag"] === content.entry.etag) {
      return { entry: content.entry, not_modified: true };
    }
    const { content_id: _id, ...shown } = content;
    return shown;
  }

  /** Up to six of a kept version's chunks, from `first`, so a read pins its version. */
  async fileReadChunks(binding: Binding, request: FileChunksRequest):
      Promise<{ chunks: Uint8Array[] }> {
    this.#filer(binding, "read_files");
    const r = fileFields(request, ["path", "version", "first", "count"]);
    const { chunks = [] } = this.#files.openVersion(r["path"], r["version"]);
    const first = r["first"];
    const many = r["count"];
    if (typeof first !== "number" || typeof many !== "number" || !Number.isSafeInteger(first)
        || !Number.isSafeInteger(many) || first < 0 || many < 1 || many > 6
        || first + many > chunks.length) {
      throw new Invalid("first and count name 1 to 6 of the version's chunks");
    }
    return { chunks: await this.#files.fetch(chunks.slice(first, first + many)
      .map(({ sha256 }) => ({ sha256 }))) };
  }

  /** Write a whole file of at most one chunk (4 MiB); larger ones go by upload. */
  async fileWrite(binding: Binding, request: FileWriteRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    const account = this.#filer(binding, "write_files");
    const r = fileFields(request, ["path", "expected_etag", "content"], ["mode"]);
    const content = r["content"];
    if (!(content instanceof Uint8Array) || content.byteLength > CHUNK_BYTES) {
      throw new Invalid("a written file is bytes, at most 4 MiB; larger ones are uploaded");
    }
    const written = await fileConflicted(async () => ({
      entry: await this.#files.write(r["path"], content, {
        expected: r["expected_etag"], mode: r["mode"], writer: fileWriter(account) }),
    }));
    await this.#schedule.sync();
    return written;
  }

  /** Begin an upload: the chunks it will commit, and the ones whose bytes to send. */
  async fileUploadBegin(binding: Binding, request: UploadBeginRequest):
      Promise<{ intent_id: string; missing: string[]; expires_at: number }> {
    this.#filer(binding, "write_files");
    const r = fileFields(request, ["chunks"]);
    const begun = this.#files.begin(r["chunks"]);
    await this.#schedule.sync();
    return begun;
  }

  /** Send an upload's chunk bytes, at most six chunks a call; R2 checks each hash. */
  async fileUploadChunks(binding: Binding, request: UploadChunksRequest):
      Promise<{ created: number }> {
    this.#filer(binding, "write_files");
    const r = fileFields(request, ["intent_id", "chunks"]);
    return this.#files.upload(r["intent_id"], r["chunks"]);
  }

  async fileUploadCommit(binding: Binding, request: UploadCommitRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    const account = this.#filer(binding, "write_files");
    const r = fileFields(request, ["intent_id", "path", "expected_etag", "size", "chunks"],
                         ["mode"]);
    const committed = await fileConflicted(() => ({
      entry: this.#files.commit(r["intent_id"], r["path"], {
        expected: r["expected_etag"], size: r["size"], chunks: r["chunks"], mode: r["mode"],
        writer: fileWriter(account) }),
    }));
    await this.#schedule.sync();
    return committed;
  }

  async fileUploadAbort(binding: Binding, request: IntentRequest): Promise<{ aborted: boolean }> {
    this.#filer(binding, "write_files");
    const r = fileFields(request, ["intent_id"]);
    const aborted = this.#files.abort(r["intent_id"]);
    await this.#schedule.sync();
    return aborted;
  }

  async fileMkdir(binding: Binding, request: MkdirRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    const account = this.#filer(binding, "write_files");
    const r = fileFields(request, ["path", "expected_etag"]);
    const made = await fileConflicted(() => ({
      entry: this.#files.mkdir(r["path"], { expected: r["expected_etag"],
                                            writer: fileWriter(account) }),
    }));
    await this.#schedule.sync();
    return made;
  }

  async fileRemove(binding: Binding, request: RemoveRequest):
      Promise<{ removed: number } | FileConflicted> {
    const account = this.#filer(binding, "write_files");
    const r = fileFields(request, ["path", "expected_etag", "recursive"]);
    const removed = await fileConflicted(() => this.#files.remove(r["path"], {
      expected: r["expected_etag"], recursive: flag(r["recursive"], "recursive"),
      writer: fileWriter(account) }));
    await this.#schedule.sync();
    return removed;
  }

  async fileRename(binding: Binding, request: RenameRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    const account = this.#filer(binding, "write_files");
    const r = fileFields(request, ["from", "to", "expected_etag", "replace"]);
    const renamed = await fileConflicted(() => ({
      entry: this.#files.rename(r["from"], r["to"], {
        expected: r["expected_etag"], replace: flag(r["replace"], "replace"),
        writer: fileWriter(account) }),
    }));
    await this.#schedule.sync();
    return renamed;
  }

  fileHistory(binding: Binding, request: FileHistoryRequest):
      { changes: FileChange[]; next: number | null } {
    this.#filer(binding, "read_files");
    const r = fileFields(request, ["path", "limit"], ["before"]);
    return this.#files.history(r["path"], r["before"] ?? null, r["limit"]);
  }

  async fileRestore(binding: Binding, request: RestoreRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    const account = this.#filer(binding, "write_files");
    const r = fileFields(request, ["path", "version", "expected_etag"]);
    const restored = await fileConflicted(() => ({
      entry: this.#files.restore(r["path"], r["version"], {
        expected: r["expected_etag"], writer: fileWriter(account) }),
    }));
    await this.#schedule.sync();
    return restored;
  }

  /** Drop a kept version's content: the owner's own act, never an outside client's. */
  async fileRedact(binding: Binding, request: VersionRequest): Promise<{ redacted: number }> {
    this.#owner(binding, "redact its file history");
    const r = fileFields(request, ["path", "version"]);
    const redacted = this.#files.redact(r["path"], r["version"]);
    await this.#schedule.sync();
    return redacted;
  }

  fileChanges(binding: Binding, request: ChangesRequest):
      { changes: FileChange[]; cursor: number; reset: boolean } {
    this.#filer(binding, "read_files");
    const r = fileFields(request, ["after", "limit"]);
    return this.#files.changes(r["after"], r["limit"]);
  }

  fileUsage(binding: Binding): Usage {
    this.#filer(binding, "read_files");
    return this.#files.usage();
  }

  // The sync lease is the owner's own: the sandbox's sync agent reaches it through the
  // binder with a lease token (tasks 2), as the owner, never as an outside client.

  fileLeaseAcquire(binding: Binding, request: { holder: string }): Lease {
    this.#owner(binding, "hold the file sync lease");
    const r = fileFields(request, ["holder"]);
    return this.#files.leaseAcquire(r["holder"]);
  }

  fileLeaseRenew(binding: Binding, request: LeaseRequest): Lease {
    this.#owner(binding, "hold the file sync lease");
    const r = fileFields(request, ["lease_id", "epoch"]);
    return this.#files.leaseRenew(r["lease_id"], r["epoch"]);
  }

  fileLeaseRelease(binding: Binding, request: LeaseRequest): { released: boolean } {
    this.#owner(binding, "hold the file sync lease");
    const r = fileFields(request, ["lease_id", "epoch"]);
    return this.#files.leaseRelease(r["lease_id"], r["epoch"]);
  }

  async fileSyncCommit(binding: Binding, request: SyncCommitRequest):
      Promise<{ outcomes: SyncOutcome[] }> {
    this.#owner(binding, "hold the file sync lease");
    const r = fileFields(request, ["lease_id", "epoch", "intent_id", "ops"]);
    const committed = this.#files.syncCommit({ lease_id: r["lease_id"], epoch: r["epoch"],
                                               intent_id: r["intent_id"], ops: r["ops"] });
    await this.#schedule.sync();
    return committed;
  }
}
