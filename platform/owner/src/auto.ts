/**
 * `auto`, the owner's strongest connected source (Direction 4; agent research §2.2): the
 * runs slice (R) picks a run's source from this order (ADR-034 decision 4), by the ranking it
 * is handed (`sources/src/ranking.ts`: the platform's, or the owner's own file).
 *
 * - A candidate is a key's model on one of the owner's connections, or a plan: an engine
 *   signed in, ranked by the model its record says a run on it is given (`cli.login.model`).
 * - The order: by tier; within a tier, a plan before a key (the owner pays for the plan
 *   already, so a plan in the top tier wins over every metered key); then by entry, newer ids
 *   first within an entry. A model no entry matches is unknown and comes after every tier,
 *   ordered the same way, its keys by the score its source's vendor lists it at (`strength`,
 *   `connections.ts`), highest first, a scored one before an unscored one, then by their
 *   catalog output price, highest first: the order an unranked model has, and `auto` tells
 *   the owner when it runs one (`runs.ts`). So on a key that pays only for free models, all
 *   priced at zero and none ranked, the vendor's strongest comes first, not the first id.
 *   Then the older connection (or sign-in), then the connection, then the model id.
 * - With no connected source, nothing ranks: there is no platform-paid default (a founder
 *   call).
 */
import type { ModelRecord } from "../../contract/src/model-record";
import type { Place, Ranking } from "../../sources/src/ranking";

/**
 * A connection a model call may use, as `Connections.usable` lists it: its models, as
 * `getModels` lists them. Declared here, by what ranks it, so the runs read it without
 * reaching the connections' module (the policy-boundary guard).
 */
export interface Usable {
  readonly connection: string;
  /** Its source record, which its spend is counted and capped under. */
  readonly source: string;
  readonly provider: string;
  readonly display_name: string;
  readonly created_at: number;
  readonly models: readonly ModelRecord[];
  /** Whether its key can pay for a priced model: false, it reported not; null, not known. */
  readonly pays: boolean | null;
  /** Its models' scores, as its source's vendor lists them, higher stronger; by model id. */
  readonly scores: ReadonlyMap<string, number>;
}

/** An engine signed in, as `auto` ranks it. */
export interface Plan {
  readonly engine: string;
  /** The provider its plan covers (`planProvider`). */
  readonly provider: string;
  /** The model its record says a run on it is given (`cli.login.model`). */
  readonly model: string;
  /** When its sign-in last changed: its age among the candidates. */
  readonly since: number;
}

/** One source `auto` may route to. */
export interface Candidate {
  /** A key's connection, or null for a plan. */
  readonly connection: string | null;
  /** A key's source record, or a plan's engine. */
  readonly source: string;
  readonly provider: string;
  readonly model: string;
  /** Where the ranking places its model, or null when no entry does (unknown). */
  readonly place: Place | null;
}

interface Sorted {
  readonly candidate: Candidate;
  /** A key's model's score, as its vendor lists it; null for one unscored, and for a plan. */
  readonly score: number | null;
  /** A key's catalog output price; a plan has none. */
  readonly price: number;
  readonly since: number;
}

function text(a: string | null, b: string | null): number {
  return (a ?? "") < (b ?? "") ? -1 : (a ?? "") > (b ?? "") ? 1 : 0;
}

/** Every connected key's model and every plan, strongest first. */
export function ranked(keys: readonly Usable[], plans: readonly Plan[], ranking: Ranking):
    Candidate[] {
  const all: Sorted[] = [
    ...keys.flatMap((connection) => connection.models.map((model) => ({
      candidate: { connection: connection.connection, source: connection.source,
                   provider: connection.provider, model: model.id,
                   place: ranking.place(connection.provider, model.id) },
      score: connection.scores.get(model.id) ?? null, price: model.cost.output,
      since: connection.created_at }))),
    ...plans.map((plan) => ({
      candidate: { connection: null, source: plan.engine, provider: plan.provider,
                   model: plan.model, place: ranking.place(plan.provider, plan.model) },
      score: null, price: 0, since: plan.since })),
  ];
  const tier = (one: Sorted) => one.candidate.place?.tier ?? Number.MAX_SAFE_INTEGER;
  const entry = (one: Sorted) => one.candidate.place?.entry ?? Number.MAX_SAFE_INTEGER;
  const keyed = (one: Sorted) => one.candidate.connection === null ? 0 : 1;
  const scored = (a: Sorted, b: Sorted) => a.score === b.score ? 0 : a.score === null ? 1
    : b.score === null ? -1 : b.score - a.score;
  all.sort((a, b) => tier(a) - tier(b) || keyed(a) - keyed(b) || entry(a) - entry(b)
    || (a.candidate.place === null ? scored(a, b) || b.price - a.price
      : b.candidate.model.localeCompare(a.candidate.model, "en", { numeric: true }))
    || a.since - b.since
    || text(a.candidate.connection, b.candidate.connection)
    || text(a.candidate.source, b.candidate.source)
    || text(a.candidate.model, b.candidate.model));
  return all.map(({ candidate }) => candidate);
}
