/**
 * `auto`, the `gateway` provider's virtual model (Direction 4; agent research §2.2): at
 * each request it is the owner's strongest connected model, chosen in the object.
 *
 * - **Ranking** is data (`model-ranking.json`): the earliest entry a model matches ranks it,
 *   newer ids first within an entry; a model no entry matches comes after, by its catalog
 *   output price, highest first; then the older connection, then the id.
 * - **Stickiness**, pi's documented practice for a virtual model (pi-coding-agent
 *   "Virtual Models": `previous` for a continuation, `failed` for a retry), read from the
 *   request's own transcript, where every answer names the physical model that gave it
 *   (`<connection>/<model>`), so nothing is kept in memory and an eviction loses nothing:
 *   - a continuation (the newest message is a tool result) stays on the model that gave
 *     the turn's newest answer, while it is still connected, keeping prompt caches and
 *     thinking signatures valid;
 *   - a retry after an answer that failed before any output (pi's durable retry appends
 *     the failed answer) goes to the next-ranked model after the one that failed (from
 *     the last, round to the strongest), since a model call has no side effect; one that
 *     failed after output stays on its model, and so does one that failed for its
 *     context's length (`context_length_exceeded`), which pi retries once compacted, so
 *     the compaction is what makes it fit;
 *   - a user's message routes afresh, to the strongest.
 * - **Metadata mirroring**: `auto` is listed with the strongest model's whole record, so
 *   pi's compaction thresholds and the reservation's estimate are the target's. A call
 *   `auto` routes elsewhere (a continuation kept on an earlier target after the owner
 *   connected a stronger one, or a retry moved down the ranking) is estimated, priced and
 *   clamped by the target's own record, and held to the target's own compaction threshold
 *   (`gateway-provider.ts`): pi keys `auto`'s thresholds by `auto` alone, so that one call
 *   is refused as an overflow, and pi compacts and retries it.
 * - **Compaction thresholds** are each model's own (`compactionFor`), from its context
 *   window: pi's defaults are sized for a window of 256K tokens or more, and a smaller one
 *   gets them in proportion.
 * - With no connected model, `auto` is not listed, so pi settles the turn
 *   `unanswered: no_model`, loudly. There is no platform-paid default (a founder call).
 */
import type { Message } from "@earendil-works/pi-ai";
import { type CompactionPolicy, DEFAULT_COMPACTION_POLICY } from "@earendil-works/pi-durable";

import type { ModelInfo, Usable } from "./connections";
import ranking from "./model-ranking.json" with { type: "json" };

/**
 * The code of a context too long for its model: the gateway's (`failure.ts`), and the
 * phrase pi-ai's overflow detection matches (`isContextOverflow`), so pi compacts and
 * retries once.
 */
export const CONTEXT_OVERFLOW = "context_length_exceeded";

/** The compaction thresholds pi's overrides set for one model. */
export type Thresholds = Required<Pick<CompactionPolicy,
  "reserveTokens" | "keepRecentTokens" | "backgroundTokens">>;

/**
 * pi's compaction thresholds for a model, from its own context window (pi audit F6): pi's
 * defaults (`DEFAULT_COMPACTION_POLICY`: 16,384 tokens kept free for the answer, 20,000
 * recent ones kept verbatim, a background compaction 32,768 below the blocking threshold)
 * hold for a window of 256K tokens or more; a smaller window keeps an eighth of itself
 * free, a quarter verbatim and starts in the background an eighth lower, as pi's own
 * example sizes a 32K model (4,096, 8,000 and 4,096).
 */
export function compactionFor(model: Pick<ModelInfo, "contextWindow">): Thresholds {
  const window = model.contextWindow;
  return Object.freeze({
    reserveTokens: Math.min(DEFAULT_COMPACTION_POLICY.reserveTokens, Math.floor(window / 8)),
    keepRecentTokens: Math.min(DEFAULT_COMPACTION_POLICY.keepRecentTokens,
                               Math.floor(window / 4)),
    backgroundTokens: Math.min(DEFAULT_COMPACTION_POLICY.backgroundTokens,
                               Math.floor(window / 8)),
  });
}

/** The context size above which pi blocks a request on `model` for a compaction. */
export function blockingThreshold(model: Pick<ModelInfo, "contextWindow">): number {
  return model.contextWindow - compactionFor(model).reserveTokens;
}

/** One connected model `auto` may route to. */
export interface Candidate {
  readonly connection: string;
  readonly source: string;
  readonly provider: string;
  readonly display_name: string;
  readonly created_at: number;
  readonly model: ModelInfo;
}

/** The physical model id of a candidate, as pi lists it and every answer names it. */
export function physicalId(candidate: Pick<Candidate, "connection" | "model">): string {
  return `${candidate.connection}/${candidate.model.id}`;
}

interface Rank {
  readonly provider: string;
  readonly model: string;
}

/** The ranking's entries, checked: a malformed file ranks nothing, loudly. */
function ranks(): readonly Rank[] {
  const file = ranking as { version?: unknown; ranks?: unknown };
  const entries = file.ranks;
  if (file.version !== 1 || !Array.isArray(entries)
      || !entries.every((entry: unknown) => typeof entry === "object" && entry !== null
        && typeof (entry as Rank).provider === "string"
        && typeof (entry as Rank).model === "string")) {
    throw new Error("the model ranking file is malformed");
  }
  return entries as readonly Rank[];
}

/** A pattern whose `*` matches any run of characters, and nothing else is special. */
function glob(pattern: string, value: string): boolean {
  const source = pattern.split("*").map((part) => part.replace(/[.+?^${}()|[\]\\]/g, "\\$&"))
    .join(".*");
  return new RegExp(`^${source}$`).test(value);
}

/** The index of the first entry `candidate` matches, or the entries' count for none. */
export function rankOf(candidate: Pick<Candidate, "provider" | "model">,
                       entries: readonly Rank[] = ranks()): number {
  const id = candidate.model.id;
  const tail = id.slice(id.lastIndexOf("/") + 1);
  const index = entries.findIndex((entry) =>
    (entry.provider === "*" || entry.provider === candidate.provider)
    && (glob(entry.model, id) || glob(entry.model, tail)));
  return index === -1 ? entries.length : index;
}

/** Every connected model, strongest first. */
export function ranked(usable: readonly Usable[]): Candidate[] {
  const entries = ranks();
  const all = usable.flatMap((connection) => connection.models.map((model) => ({
    connection: connection.connection, source: connection.source,
    provider: connection.provider, display_name: connection.display_name,
    created_at: connection.created_at, model,
  })));
  const keyed = all.map((candidate) => ({ candidate, rank: rankOf(candidate, entries) }));
  keyed.sort((a, b) => a.rank - b.rank
    || (a.rank === entries.length ? b.candidate.model.cost.output - a.candidate.model.cost.output
      : b.candidate.model.id.localeCompare(a.candidate.model.id, "en", { numeric: true }))
    || a.candidate.created_at - b.candidate.created_at
    || (a.candidate.connection < b.candidate.connection ? -1
      : a.candidate.connection > b.candidate.connection ? 1 : 0)
    || (a.candidate.model.id < b.candidate.model.id ? -1 : 1));
  return keyed.map(({ candidate }) => candidate);
}

/** The model `auto` routes this request to, or none when nothing is connected. */
export function route(candidates: readonly Candidate[],
                      messages: readonly Message[]): Candidate | undefined {
  const strongest = candidates[0];
  const said = messages.filter((message) => message.role !== "system");
  const last = said.at(-1);
  const named = (model: string) => candidates.find((candidate) => physicalId(candidate) === model);
  if (last?.role === "assistant" && last.stopReason === "error") {
    const failed = named(last.model);
    if (failed === undefined) {
      return strongest;
    }
    if (last.content.length > 0 || last.errorMessage === CONTEXT_OVERFLOW) {
      return failed;
    }
    // The next-ranked after the one that failed, round to the strongest after the last.
    return candidates[candidates.indexOf(failed) + 1] ?? strongest;
  }
  if (last?.role === "toolResult") {
    const previous = said.findLast((message) => message.role === "assistant");
    const sticky = previous?.role === "assistant" ? named(previous.model) : undefined;
    return sticky ?? strongest;
  }
  return strongest;
}
