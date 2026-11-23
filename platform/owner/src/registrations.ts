/**
 * An MCP client's held agent registration, mirroring a held policy
 * proposal (`policy.ts`).
 *
 * Registering an agent is the owner's act, in its protected session, but the identity an
 * MCP agent registers as is its client's verified origin, which only the client's own
 * verified call carries: the owner's browser has no source for it. So the client files a
 * card through its own call (`file`), and the object captures the origin the binder
 * verified, the call's source reference and the grant authority it was admitted under.
 * The filing is held to the rule every registration is (`Ledger.registration`), in the place
 * the client's grant acts (`centerOf`), so the owner is shown only a card that applies. Its
 * answer names the owner app's page of the card (`approveUrl`), which the client hands its
 * user, and the owner is told of it there too (`agent_registration`). The owner reads the
 * cards (`list`) and applies one in session by its id and the digest it was shown (`take`,
 * then `Ledger.register`, in one transaction), or declines it (`decline`). An API key's
 * agent still registers from the owner's browser with the key, whose origin the object
 * re-derives from the key id.
 *
 * - One pending card per outside identity: filing again replaces it. A card waits until
 *   the owner applies or declines it, its client's grant changes, or the room needs its
 *   place (below): it has no life of its own (Fact 15).
 * - A card is stamped like scheduled work: `take` rechecks its client's grant with
 *   `standing`, and a grant change drops the client's cards in its own transaction
 *   (`endClient`). A pending card holds its client's grant row (the schema's `held`).
 * - A card never changes (the schema refuses an update), and is gone once applied.
 * - Bounded: `maxPending` cards (a counter the schema's triggers keep), `perClient` of
 *   them one outside client's (counted on its index), each request at most
 *   `maxRequest` characters. A filing at a bound lets the oldest card go to make its
 *   room: the client's own at its share, else the oldest of all (every card is an outside
 *   client's), the owner told whose (`held_request_evicted`). Nothing is refused for room,
 *   and at most `maxPending` grant rows are held.
 */
import { sha256Hex } from "../../contract/src/digest";
import { HEX64, randomHex } from "../../contract/src/ids";
import { appPage } from "../../contract/src/identity";
import { Refusal } from "../../contract/src/refusal";
import { CARD_REQUEST_MAX } from "../../contract/src/limits";
import type { OwnerStorage } from "./gate";
import { codePoints } from "../../contract/src/text";
import { type Call, type LiveSession, proven } from "./door";
import type { Source } from "../../contract/src/binding";
import type { StandingGrant } from "./authority";
import { clock } from "./clock";
import type { Notices } from "./notices";

/** The outside authority a held card was filed under: its client, generation and sign-in. */
export interface Stamp {
  readonly client: string;
  readonly generation: number;
  readonly authenticated_at: number | null;
}

/** What `standing` answers; the object's grants (`grants.ts`). */
interface StandingReader {
  standing(client: unknown, generation: unknown, authenticatedAt: number | null): unknown;
}

interface CardLimits {
  readonly maxPending: number;
  readonly perClient: number;
  readonly maxRequest: number;
}

export const CARD_LIMITS: CardLimits = Object.freeze({
  maxPending: 200, perClient: 10, maxRequest: CARD_REQUEST_MAX,
});

/**
 * Where a full grant's agent registers when its client names no place: its grant holds
 * everywhere, so the name only keeps one identity's agent one row across its filings.
 */
export const HOME_CENTER = "main";

/** What a registration asks for: the fields of every registration's request. */
type Asked = "command_center_id" | "display_name" | "delivery_method" | "recipe_id";

/** A registration request's flat fields, as the door took them. */
export type Fields = Readonly<Record<string, string | number | boolean | null>>;

const CARD_ID = /^card_[0-9a-f]{32}$/;

/** A held card, as the owner is shown it. */
export interface Card {
  readonly card_id: string;
  readonly client: string;
  readonly family: string;
  readonly source_ref: string;
  readonly request: Fields;
  readonly digest: string;
  readonly filed_at: number;
}

/** What an applied card registers: the captured identity, source and request. */
export interface Taken {
  readonly origin: { readonly client: string; readonly family: string };
  readonly source: { readonly kind: "mcp"; readonly ref: string };
  readonly request: Fields;
}

type Row = {
  card_id: string; client: string; family: string; source_ref: string; generation: number;
  authenticated_at: number | null; request: string; digest: string; filed_at: number;
};

/** The owner app's page of a held card, where the owner applies or declines it. */
export function approveUrl(cardId: string): string {
  return appPage("apps", cardId);
}

/**
 * Where a client's agent registers (its `command_center_id`): a scoped client acts where its
 * agent is (`OwnerDO.#universe`), so a narrowed grant's agent registers in the space its
 * scopes name, the one `named` among several; a full grant holds everywhere, so its agent
 * registers where `named` says, else in `HOME_CENTER`.
 */
export function centerOf(grant: StandingGrant, named: unknown): unknown {
  if (grant.mode !== "scoped") {
    return named ?? HOME_CENTER;
  }
  const spaces = [...new Set(grant.scopes.map((scope) => scope.universe))];
  if (named === undefined && spaces.length === 1) {
    return spaces[0];
  }
  if (named === undefined) {
    throw new Refusal("invalid", "this connection's grant names more than one space: name " +
                      `one of ${spaces.join(", ")} as command_center_id`);
  }
  if (!spaces.includes(named as string)) {
    throw new Refusal("refused", "this connection's agent registers in a space its grant " +
                      `names: ${spaces.join(", ")}`);
  }
  return named;
}

export class Registrations {
  readonly #storage: OwnerStorage;
  readonly #grants: StandingReader;
  readonly #notices: Pick<Notices, "add" | "once">;

  constructor(storage: OwnerStorage, grants: StandingReader,
              notices: Pick<Notices, "add" | "once">) {
    this.#storage = storage;
    this.#grants = grants;
    this.#notices = notices;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  #shown(row: Row): Card {
    return { card_id: row.card_id, client: row.client, family: row.family,
             source_ref: row.source_ref,
             request: JSON.parse(row.request) as Fields, digest: row.digest,
             filed_at: row.filed_at };
  }

  /** The pending card under `cardId` as stored, or undefined. */
  #row(cardId: unknown): Row | undefined {
    return typeof cardId === "string" && CARD_ID.test(cardId)
      ? this.#sql.exec<Row>("SELECT * FROM agent_cards WHERE card_id = ?", cardId).toArray()[0]
      : undefined;
  }

  /** The pending card `cardId` names, if `shown` is the digest it was shown with. */
  #pending(cardId: unknown, shown: unknown): Row {
    const row = this.#row(cardId);
    if (row === undefined || typeof shown !== "string" || row.digest !== shown) {
      throw new Refusal("refused", "registration is not the pending card shown");
    }
    return row;
  }

  /**
   * File a card from the client's own verified call: `origin` as the binder verified it,
   * `sourceRef` its source's reference, `by` the grant authority it was admitted under. The
   * owner is told where to approve it.
   */
  file(origin: { client: string; family: string }, sourceRef: string, by: Stamp,
       request: Readonly<Record<string, unknown>>, now: number):
      { card_id: string; digest: string } {
    if (!HEX64.test(origin.client) || !HEX64.test(origin.family) || by.client !== origin.client) {
      throw new Refusal("invalid", "invalid outside origin");
    }
    if (Object.values(request).some((value) => value !== null && typeof value === "object")) {
      throw new Refusal("invalid", "a registration's fields are flat values");
    }
    const body = JSON.stringify(request);
    if (codePoints(body) > CARD_LIMITS.maxRequest) {
      throw new Refusal("invalid",
                        `a registration is at most ${CARD_LIMITS.maxRequest} characters`);
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      this.#grants.standing(by.client, by.generation, by.authenticated_at);
      sql.exec("DELETE FROM agent_cards WHERE client = ? AND family = ?", origin.client,
               origin.family);
      const mine = sql.exec<{ n: number }>(
        "SELECT COUNT(*) AS n FROM agent_cards WHERE client = ?", origin.client).one().n;
      const own = mine >= CARD_LIMITS.perClient;
      if (own || sql.exec<{ pending_cards: number }>("SELECT pending_cards FROM policy_usage")
                   .one().pending_cards >= CARD_LIMITS.maxPending) {
        const gone = sql.exec<{ client: string }>(
          "DELETE FROM agent_cards WHERE card_id = (SELECT card_id FROM agent_cards " +
            `${own ? "WHERE client = ? " : ""}ORDER BY filed_at, rowid LIMIT 1) RETURNING client`,
          ...(own ? [origin.client] : [])).one();
        this.#notices.once("held_request_evicted", `an agent registration of ${gone.client} ` +
                           "was let go to make room for a newer one", now);
      }
      const cardId = "card_" + randomHex(16);
      const shown = sha256Hex(JSON.stringify([cardId, origin.client, origin.family, sourceRef,
                                           body]));
      sql.exec("INSERT INTO agent_cards (card_id, client, family, source_ref, generation, " +
                 "authenticated_at, request, digest, filed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               cardId, origin.client, origin.family, sourceRef, by.generation,
               by.authenticated_at, body, shown, now);
      this.#notices.add("agent_registration", "An app you connected asks to register an " +
                        "agent, for a mailbox and rooms: approve or decline it at " +
                        approveUrl(cardId), now);
      return { card_id: cardId, digest: shown };
    });
  }

  /** The pending cards, oldest first. */
  list(): Card[] {
    return this.#sql.exec<Row>("SELECT * FROM agent_cards ORDER BY filed_at, rowid").toArray()
      .map((row) => this.#shown(row));
  }

  /**
   * The owner's apply, in its session: the pending card with the digest the owner was
   * shown, whose client's authority still stands, consumed. The caller registers what it
   * returns in the same transaction, so a refused registration keeps the card.
   */
  take(session: LiveSession, cardId: unknown, shown: unknown): Taken {
    proven(session, "session");
    const row = this.#pending(cardId, shown);
    this.#grants.standing(row.client, row.generation, row.authenticated_at);
    this.#sql.exec("DELETE FROM agent_cards WHERE card_id = ?", row.card_id);
    return { origin: { client: row.client, family: row.family },
             source: { kind: "mcp", ref: row.source_ref },
             request: JSON.parse(row.request) as Fields };
  }

  /** The owner's no: the pending card with the digest the owner was shown, gone; its id. */
  decline(cardId: unknown, shown: unknown): string {
    const { card_id: declined } = this.#pending(cardId, shown);
    this.#sql.exec("DELETE FROM agent_cards WHERE card_id = ?", declined);
    return declined;
  }

  /** A changed or revoked grant drops its client's cards; how many. */
  endClient(client: string): number {
    return this.#sql.exec("DELETE FROM agent_cards WHERE client = ? RETURNING card_id", client)
      .toArray().length;
  }
}

export interface RegistrationAnswerRequest {
  readonly card_id: string;
  readonly digest: string;
}

// -- THE DOORS: the `registrations` rows' handlers (`owner/doors.json`) ---------------------------

/** What the `registrations` doors reach: the held cards, and the rule a registration holds. */
export interface RegistrationsDoorNeeds {
  readonly registrations: Registrations;
  readonly ledger: { registration(request: Readonly<Record<Asked, unknown>>): Fields };
}

export const registrationsDoors = Object.freeze({
  /**
   * An MCP client files the registration of its agent on its own verified call, so the
   * object captures the origin the binder verified, in the place its grant acts; the owner
   * applies it in session (`registerAgent`) at the page its answer names. Never applied
   * here. Filing again replaces the identity's card.
   */
  registerPropose(o: RegistrationsDoorNeeds,
                  { account, r, binding }: Call<Readonly<Record<string, unknown>>>):
      { card_id: string; digest: string; approve_url: string } {
    const grant = account.grant!;
    const request = o.ledger.registration({
      command_center_id: centerOf(grant, r["command_center_id"]),
      display_name: r["display_name"], delivery_method: r["delivery_method"],
      recipe_id: r["recipe_id"] });
    const filed = o.registrations.file(account.origin!, (binding.source as Source).ref, {
      client: account.origin!.client, generation: grant.generation,
      authenticated_at: binding.authenticated_at ?? null }, request, clock());
    return { ...filed, approve_url: approveUrl(filed.card_id) };
  },
  /**
   * The one card under `card_id`, for the doors' version before (`dropped`), which still
   * names it until the stage serves none.
   */
  registerProposal(o: RegistrationsDoorNeeds, { r }: Call<{ readonly card_id: string }>):
      Card {
    const card = o.registrations.list().find((each) => each.card_id === r["card_id"]);
    if (card === undefined) {
      throw new Refusal("refused", "no pending registration for this card");
    }
    return card;
  },
  /** The pending registration cards the owner is shown, oldest first, each with its digest. */
  registerProposals(o: RegistrationsDoorNeeds, _call: Call<undefined>): { cards: Card[] } {
    return { cards: o.registrations.list() };
  },
  /** The owner's no to a pending card, by its id and the digest it was shown. */
  registerDecline(o: RegistrationsDoorNeeds, { r }: Call<RegistrationAnswerRequest>):
      { card_id: string; declined: true } {
    return { card_id: o.registrations.decline(r["card_id"], r["digest"]), declined: true };
  },
});
