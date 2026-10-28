/**
 * Which pi-ai provider serves a sealed credential (research §2.3 step 2, §2.4).
 *
 * The gateway holds no provider code: a credential's sealed `upstream.provider` names a
 * pi-ai built-in provider, whose factory brings its catalog (base URLs, model metadata,
 * prices) and its adapter. `custom` is a declared endpoint: a pi-ai `createProvider` over
 * the sealed models with pi-ai's own adapter for the sealed API. Either way the request
 * goes to the sealed `base_url`, never the catalog's.
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
import { createProvider } from "@earendil-works/pi-ai/models";
import { builtinProviders } from "@earendil-works/pi-ai/providers/all";

import { SUPPORTED_APIS, type Upstream } from "./credential";
import { Failure } from "./failure";

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

/** pi-ai's lazy adapters a declared endpoint may speak, by API id. */
const CUSTOM_APIS: Readonly<Record<string, () => ProviderStreams>> = Object.freeze({
  "anthropic-messages": anthropicMessagesApi,
  "google-generative-ai": googleGenerativeAIApi,
  "openai-completions": openAICompletionsApi,
  "openai-responses": openAIResponsesApi,
});

function customProvider(upstream: Upstream): Provider {
  const api = Object.hasOwn(CUSTOM_APIS, upstream.api) ? CUSTOM_APIS[upstream.api] : undefined;
  if (api === undefined || upstream.models === undefined) {
    throw new Failure("unsupported_source");
  }
  const models: Model<Api>[] = upstream.models.map((model) => ({
    id: model.id,
    name: model.name,
    api: upstream.api,
    provider: "custom",
    baseUrl: upstream.base_url,
    reasoning: model.reasoning,
    input: [...model.input],
    cost: { ...model.cost },
    contextWindow: model.contextWindow,
    maxTokens: model.maxTokens,
  }));
  return createProvider({
    id: "custom",
    name: "Declared endpoint",
    baseUrl: upstream.base_url,
    auth: { apiKey: { name: "Sealed credential",
                      resolve: async ({ credential }) =>
                        credential?.key ? { auth: { apiKey: credential.key } } : undefined } },
    models,
    api: api(),
  });
}

/**
 * The provider for a sealed upstream; `unsupported_source` when pi-ai has none, or when
 * its API is not one the gateway runs.
 */
export function providerFor(catalog: Catalog, upstream: Upstream): Provider {
  if (!SUPPORTED_APIS.includes(upstream.api)) {
    throw new Failure("unsupported_source");
  }
  if (upstream.provider === "custom") {
    return customProvider(upstream);
  }
  const provider = catalog.provider(upstream.provider);
  if (provider === undefined) {
    throw new Failure("unsupported_source");
  }
  return provider;
}

/** The model ids a sealed upstream serves: its provider's catalog, or its declared list. */
export function modelIds(catalog: Catalog, upstream: Upstream): string[] {
  return providerFor(catalog, upstream).getModels()
    .filter((model) => model.api === upstream.api).map((model) => model.id);
}
