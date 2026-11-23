/**
 * A source's routers (`src/routers.ts`): each one its catalog lists at a price of zero and
 * routes to models it lists; what a call of each model of a source that names routers is
 * priced by; and a record's routers checked whole.
 */
import { builtinProviders } from "@earendil-works/pi-ai/providers/all";
import { describe, expect, it } from "vitest";

import type { ModelRecord } from "../../contract/src/model-record";

import { bundledRecords } from "../src/catalog";
import { sourceRecord } from "../src/check";
import { matcher } from "../src/ranking";
import { pricedBy } from "../src/routers";
import { SourceRecordError } from "../src/schema";

const records = bundledRecords().map(sourceRecord);
const routing = records.filter((record) => record.routers !== undefined);

/** The chat models pi-ai's catalog lists for `provider` on `api`, as a connection holds them. */
function listed(provider: string, api: string): ModelRecord[] {
  return builtinProviders().find((entry) => entry.id === provider)!.getModels()
    .filter((model) => model.api === api) as unknown as ModelRecord[];
}

const zero = ({ cost }: ModelRecord) =>
  cost.input + cost.output + cost.cacheRead + cost.cacheWrite === 0;

describe("a source's routers", () => {
  it("are models its catalog lists at zero, each routing to models it lists", () => {
    expect(routing.map((record) => record.id)).toEqual(["openrouter_user_models_v1"]);
    for (const record of routing) {
      const models = listed(record.upstream!.provider, record.upstream!.api);
      for (const router of record.routers!) {
        for (const pattern of router.models) {
          const matched = models.filter((model) => matcher(pattern)(model.id));
          expect(matched.length, pattern).toBeGreaterThan(0);
          expect(matched.filter((model) => !zero(model)).map((model) => model.id), pattern)
            .toEqual([]);
        }
        for (const pattern of router.to) {
          expect(models.some((model) => matcher(pattern)(model.id)), pattern).toBe(true);
        }
      }
    }
  });

  it("price a router's call by what it may route to, a free model and a priced one by " +
     "itself, and no other model the vendor lists at zero", () => {
    const record = routing.find((entry) => entry.id === "openrouter_user_models_v1")!;
    const models = listed("openrouter", "openai-completions");
    const of = (id: string) => models.find((model) => model.id === id)!;
    const by = (id: string, among = models) => pricedBy(record, among, of(id));
    const priced = models.filter((model) => !zero(model));
    const free = models.filter((model) => model.id.endsWith(":free"));
    expect(by("openrouter/auto")).toEqual({ routed: true, models });
    expect(by("auto")).toEqual({ routed: true, models });
    // A router to free models alone costs nothing whatever serves it: settled as named alone.
    expect(by("openrouter/free")).toEqual({ routed: false, models: free });
    expect(by(free[0]!.id)).toEqual({ routed: false, models: [free[0]] });
    expect(by(priced[0]!.id)).toEqual({ routed: false, models: [priced[0]] });
    // Every model listed at zero but neither routed nor free: no price is known for it.
    expect(models.filter((model) => by(model.id) === null).map((model) => model.id))
      .toEqual(["openrouter/fusion", "typesafe/jev-router"]);
    // A router that may route to none of the connection's models is not priced either.
    expect(by("openrouter/free", models.filter((model) => !model.id.endsWith(":free"))))
      .toBeNull();
    // A source naming no routers prices a model at zero as free.
    const { routers: _routers, ...plain } = record;
    expect(pricedBy(plain, models, of("openrouter/fusion"))).toMatchObject({ routed: false });
  });

  it("are checked whole: model patterns, where each routes, and the vendor's page", () => {
    const openrouter = bundledRecords().find((record) =>
      (record as { id: string }).id === "openrouter_user_models_v1") as Record<string, unknown>;
    const router = { models: ["x/auto"], to: ["*"], cite: "https://openrouter.ai/docs" };
    const at = (routers: unknown) => sourceRecord({ ...openrouter, routers });
    expect(at([router]).routers).toEqual([router]);
    for (const bad of [[], "x", [{ ...router, models: [] }], [{ ...router, to: ["*", "*"] }],
                       [{ ...router, models: ["a b"] }], [{ ...router, cite: "http://x.test" }],
                       [{ ...router, extra: 1 }], Array.from({ length: 9 }, () => router)]) {
      expect(() => at(bad), JSON.stringify(bad)).toThrow(SourceRecordError);
    }
  });
});
