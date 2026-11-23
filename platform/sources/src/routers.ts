/**
 * What a sandbox call's model is priced by, as its source record says (Direction 2: a vendor's
 * billing is record data, never code). A model is priced at its own catalog price, unless its
 * record names it a router (`routers`): its vendor bills each call as the model it routed the
 * call to, whatever the catalog lists the router at (OpenRouter lists `openrouter/auto` at zero).
 * Such a call is reserved at the dearest of the models it may route to, and settled at the
 * model its answer says served it (`gateway/src/meter.ts`, by the reservation's `routed`).
 *
 * A record that names routers says its vendor's zero is no price of its own, so a model its
 * catalog prices at zero that is neither one of its routers nor one of its `credit.free` models
 * has no price known (one that fans a call out to several models, `openrouter/fusion`; a
 * third-party router no vendor page was read for, `typesafe/jev-router`; or one a catalog
 * update added): it is never metered (Fact 8), rather than counted at nothing.
 */
import type { ModelRecord } from "../../contract/src/model-record";

import { matcher } from "./ranking";
import type { SourceRecord } from "./schema";

/** What a call is reserved by: the models whose prices bound it, and whether it is routed. */
export interface PricedBy {
  readonly routed: boolean;
  readonly models: readonly ModelRecord[];
}

const matches = (patterns: readonly string[], id: string) =>
  patterns.some((pattern) => matcher(pattern)(id));

/** Whether the catalog prices `model` at nothing, every rate and tier. */
function zero({ cost }: ModelRecord): boolean {
  return [cost, ...(cost.tiers ?? [])].every((rates) =>
    rates.input === 0 && rates.output === 0 && rates.cacheRead === 0 && rates.cacheWrite === 0);
}

/**
 * What a call of `model`, one of a connection's `models`, on a source of `record` is priced
 * by: a router's, the connection's models it may route to, routed; any other model, itself;
 * null for a router that may route to none of them, or a model priced at zero by a vendor
 * whose zero is no price, which is neither a router nor free.
 */
export function pricedBy(record: SourceRecord | undefined, models: readonly ModelRecord[],
                         model: ModelRecord): PricedBy | null {
  const { id } = model;
  const router = record?.routers?.find((entry) => matches(entry.models, id));
  if (router !== undefined) {
    const to = models.filter((entry) => matches(router.to, entry.id));
    return to.length === 0 ? null : Object.freeze({ routed: true, models: Object.freeze(to) });
  }
  if (record?.routers !== undefined && zero(model) && !matches(record.credit?.free ?? [], id)) {
    return null;
  }
  return Object.freeze({ routed: false, models: Object.freeze([model]) });
}
