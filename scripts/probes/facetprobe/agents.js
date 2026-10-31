/**
 * ta-facetprobe-agents: Q7 of the facet probe (facet-probe.md). An Agents SDK root agent
 * (`Root`) hosts a dynamic agent (`PiChild`, a facet) that runs agents' own `PiHarness` on
 * pi-ai's faux provider, so no model credential exists anywhere. Two copies deploy from
 * this one bundle: under the owner Worker's runtime (`disable_ctx_exports`) and with
 * `ctx.exports` on; whichever answers is the minimum config. `PiChild` is not migrated:
 * the SDK makes facet storage through the root.
 *
 * Throwaway. `.github/workflows/facet-probe.yml` deploys and deletes both copies; the
 * `ta-facetprobe-door` Workflow calls `Root.q7()` by RPC. No route, no workers.dev URL.
 */
import { createModels, fauxAssistantMessage, fauxProvider } from "@earendil-works/pi-ai";
import { createRegistry, Harness } from "@earendil-works/pi-durable";
import { Agent } from "agents";
import { PiHarness } from "agents/harness/pi";

const MODEL = Object.freeze({ provider: "probe", id: "faux" });

function errText(error) {
  const text = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return text.slice(0, 500);
}

function fauxModels() {
  const faux = fauxProvider({
    provider: MODEL.provider,
    models: [{ id: MODEL.id, contextWindow: 1_000_000, maxTokens: 100_000 }],
  });
  faux.setResponses(Array.from({ length: 8 }, (_, i) => () => fauxAssistantMessage(`facetprobe answer ${i + 1}`)));
  const models = createModels();
  models.setProvider(faux.provider);
  return models;
}

export class PiChild extends Agent {
  harness = new PiHarness({
    harness: ({ storage, context, onWake }) =>
      Harness.open(storage, { models: fauxModels(), registry: createRegistry(), onWake }, context),
    defaults: { model: MODEL },
  });

  constructor(ctx, env) {
    super(ctx, env);
    this.lifecycle.use(this.harness);
  }

  async ask(text) {
    const answer = await this.harness.prompt(text);
    return { status: answer.status, reason: answer.reason ?? null, text: answer.text ?? null,
             messages: answer.messages.length, size: this.ctx.storage.sql.databaseSize,
             envKeys: Object.keys(this.env ?? {}).sort(), parentPath: this.parentPath,
             exports: typeof this.ctx.exports };
  }
}

/** A dynamic agent with no harness: whether the SDK's facet path works at all, apart from pi. */
export class PlainChild extends Agent {
  hello() {
    return { kind: "PlainChild", size: this.ctx.storage.sql.databaseSize, envKeys: Object.keys(this.env ?? {}).sort(),
             parentPath: this.parentPath };
  }
}

export class Root extends Agent {
  /**
   * A plain child first (the SDK's facet path alone), then the PiHarness child: spawn it,
   * prompt it twice (the second over its reopened Session), delete it, and prompt the
   * fresh one.
   */
  async q7() {
    const out = { exports: typeof this.ctx.exports, facets: typeof this.ctx.facets,
                  envKeys: Object.keys(this.env).sort() };
    const step = async (key, fn) => {
      const at = performance.now();
      try {
        out[key] = { ok: true, value: await fn(), ms: Math.round(performance.now() - at) };
      } catch (error) {
        out[key] = { ok: false, error: errText(error), ms: Math.round(performance.now() - at) };
      }
      return out[key].ok;
    };
    await step("plain", async () => (await this.dynamicAgents.get(PlainChild, "p1")).hello());
    if (await step("spawn", async () => (await this.dynamicAgents.get(PiChild, "t1")) && true)) {
      await step("first", async () => (await this.dynamicAgents.get(PiChild, "t1")).ask("hello"));
      await step("second", async () => (await this.dynamicAgents.get(PiChild, "t1")).ask("again"));
      await step("list", () => this.dynamicAgents.list(PiChild));
      await step("delete", async () => {
        await this.dynamicAgents.delete(PiChild, "t1");
        return { has: this.dynamicAgents.has(PiChild, "t1") };
      });
      await step("afterDelete", async () => (await this.dynamicAgents.get(PiChild, "t1")).ask("fresh"));
    }
    out.rootSize = this.ctx.storage.sql.databaseSize;
    return out;
  }
}

export default {
  fetch() {
    return new Response("not found", { status: 404 });
  },
};
