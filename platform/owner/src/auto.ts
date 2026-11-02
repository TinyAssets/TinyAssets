/**
 * `auto`, the owner's strongest connected model (Direction 4; agent research §2.2), as
 * the object ranks its connections: the runs slice (R) picks a run's source from it,
 * ranking the sources that have an engine (ADR-034 decision 4).
 *
 * - **Ranking** is data (`model-ranking.json`): the earliest entry a model matches ranks it,
 *   newer ids first within an entry; a model no entry matches comes after, by its catalog
 *   output price, highest first; then the older connection, then the id.
 * - With no connected model, nothing ranks: there is no platform-paid default (a founder
 *   call).
 */
import type { ModelInfo, Usable } from "./connections";
import ranking from "./model-ranking.json" with { type: "json" };

/** One connected model `auto` may route to. */
export interface Candidate {
  readonly connection: string;
  readonly source: string;
  readonly provider: string;
  readonly display_name: string;
  readonly created_at: number;
  readonly model: ModelInfo;
}

/** The physical model id of a candidate, as a connection lists it. */
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
