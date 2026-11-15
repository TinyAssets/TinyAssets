/**
 * Which pi-ai provider serves a sealed credential (research §2.3 step 2, §2.4).
 *
 * The gateway holds no provider code: a credential's sealed `upstream.provider` names a
 * pi-ai built-in provider, whose factory brings its catalog (base URLs, model metadata,
 * prices) and its adapter. A declared endpoint (sealed with its own models) is a pi-ai
 * `createProvider` over the sealed models with pi-ai's own adapter for the sealed API.
 * Either way the request goes to the sealed `base_url`, never the catalog's.
 *
 * Auth never comes from the environment: the collection the gateway streams through is
 * given an auth context with no variables and no files, so the opened credential is the
 * only credential any adapter sees.
 */
import type { Api, AuthContext, Model, Provider, ProviderStreams } from "@earendil-works/pi-ai";
import { anthropicMessagesApi } from "@earendil-works/pi-ai/api/anthropic-messages.lazy";
import { googleGenerativeAIApi } from "@earendil-works/pi-ai/api/google-generative-ai.lazy";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import { openAIResponsesApi } from "@earendil-works/pi-ai/api/openai-responses.lazy";
import { createProvider, isModelType } from "@earendil-works/pi-ai/models";
import { builtinProviders } from "@earendil-works/pi-ai/providers/all";

import { type DeclaredApi, declaredApi } from "../../contract/src/api-rules";
import type { Upstream } from "../../contract/src/egress-scope";
import { Refusal } from "../../contract/src/refusal";
import type { ModelRecord } from "../../contract/src/model-record";

/** The providers the gateway can stream through, by pi-ai provider id. */
export interface Catalog {
  provider(id: string): Provider | undefined;
}

/**
 * pi-ai's built-in providers, constructed once per catalog: each call makes its own
 * catalog, so nothing is kept between calls or shared between owners.
 */
export function builtinCatalog(): Catalog {
  let providers: readonly Provider[] | undefined;
  return {
    provider: (id) => (providers ??= builtinProviders()).find((entry) => entry.id === id),
  };
}


/** No environment variables and no files: the opened credential is the only auth. */
export const NO_AMBIENT: AuthContext = Object.freeze({
  env: async (): Promise<string | undefined> => undefined,
  fileExists: async (): Promise<boolean> => false,
});

/**
 * pi-ai's lazy adapter for each API a declared endpoint may speak, keyed by the contract's
 * table (`api-rules.ts` `DeclaredApi`): exactly the APIs marked `declared` there.
 */
const DECLARED = Object.freeze({
  "anthropic-messages": anthropicMessagesApi,
  "google-generative-ai": googleGenerativeAIApi,
  "openai-completions": openAICompletionsApi,
  "openai-responses": openAIResponsesApi,
} satisfies Record<DeclaredApi, () => ProviderStreams>);

/** A declared endpoint's sealed `models`, on pi-ai's adapter for its API; or none. */
function declaredProvider(upstream: Upstream, declared: readonly ModelRecord[]):
    Provider | undefined {
  const { api, provider: id, base_url: baseUrl } = upstream;
  if (!declaredApi(api)) {
    return undefined;
  }
  const models: Model<Api>[] = declared.map((model) => ({
    ...structuredClone(model) as Omit<Model<Api>, "api" | "provider" | "baseUrl">,
    api, provider: id, baseUrl,
  }));
  return createProvider({
    id,
    name: "Declared endpoint",
    baseUrl,
    auth: { apiKey: { name: "Sealed credential",
                      resolve: async ({ credential }) =>
                        credential?.key ? { auth: { apiKey: credential.key } } : undefined } },
    models,
    api: DECLARED[api](),
  });
}

/**
 * The provider for an opened upstream (whose API `upstream` checked against the contract's
 * table): its declared models when it was sealed with them, else pi-ai's built-in
 * provider; `unsupported_source` when pi-ai has none.
 */
export function providerFor(catalog: Catalog, upstream: Upstream): Provider {
  const provider = upstream.models === undefined ? catalog.provider(upstream.provider)
    : declaredProvider(upstream, upstream.models);
  if (provider === undefined) {
    throw new Refusal("unsupported_source");
  }
  return provider;
}

/**
 * The record pi-ai's catalog holds for `model` (`contract/src/model-record.ts`): only the
 * kept fields, each as pi-ai has it.
 */
export function recordOf(model: Model<Api>): ModelRecord {
  const { id, name, reasoning, thinkingLevelMap, promptCache, input, inputLimits,
          contextWindow, maxTokens, cost } = model;
  return Object.freeze({
    id, name, reasoning,
    ...(thinkingLevelMap === undefined ? {} : { thinkingLevelMap }),
    ...(promptCache === undefined ? {} : { promptCache }),
    input: input.filter((kind) => kind === "text" || kind === "image"),
    ...(inputLimits === undefined ? {} : { inputLimits }),
    contextWindow, maxTokens, cost,
  });
}

/**
 * The models a sealed upstream serves, each with its whole capability record
 * (`recordOf`): its provider's catalog, or its declared list. The owner object
 * loads no catalog of its own, so this is where it learns them, at connect time: it lists,
 * prices (pi-ai's `calculateCost`, tiers included), compacts and offers thinking levels by
 * them.
 */
export function modelInfos(catalog: Catalog, upstream: Upstream): ModelRecord[] {
  return providerFor(catalog, upstream).getModels()
    .filter((model): model is Model<Api> =>
      model.api === upstream.api && isModelType(model, "chat"))
    .map(recordOf);
}
