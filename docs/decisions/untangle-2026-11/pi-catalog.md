# pi-catalog as built (2026-11-24)

`untangle/pi-catalog`, owed by `probe-model.md` (#5852). Directions 2 and 4. A live bug: the
sandbox image's pi is 1.4.0, and pi resolves a run's model by its own catalog, which is pi-ai
1.4.0's. The gateway lists, ranks and prices every connection's models by pi-ai 1.6.0
(#5834). On OpenRouter, pi-ai 1.4.0 lacks `anthropic/claude-fable-5.2`, and it lists the
`~anthropic/*-latest` aliases on `openai-completions` only; 1.6.0 lists them on
`anthropic-messages`. `auto` ranks those models first, and since #5842 and #5845 a run that
names no model gets the strongest pickable one. So every real pi run on an OpenRouter
connection sealed with the Messages API fails today.

## Research (Fact 14), before the design

### How pi picks a run's model and its API

Read from both tarballs (`@earendil-works/pi-coding-agent` 1.4.0 and 1.6.0, `dist/core`):

- **Its catalog is pi-ai's own module.** `model-runtime.js` builds its providers from
  `@earendil-works/pi-ai/providers/all`. That is the module the gateway's `builtinCatalog()`
  lists by (`gateway/src/catalog.ts`). pi-coding-agent X depends on pi-ai X exactly, and the
  two are published together (1.6.0: 11:05 and 11:09 on 2026-11-20).
- **No network catalog in a run.** pi can overlay a newer catalog from pi.dev, and narrows
  OpenRouter's to the key's own models. Both are written only by a network refresh. Only the
  `rpc` and interactive modes start one (`main.js`). `--mode json`, the record's mode,
  refreshes with `allowNetwork: false` (`agent-session-services.js`). So a run's catalog is its
  bundled pi-ai's and nothing else, and no switch is needed for it.
- **`--provider P --model M` on an id the catalog lacks** (`model-resolver.js`
  `resolveCliModel`):
  - first a fuzzy match: any of P's models whose id or name contains M, so another model can
    run silently;
  - else `buildFallbackModel`: P's default model (`moonshotai/kimi-k2.6`, on
    `openai-completions`) with M's id. On the Messages API's base it calls
    `/api/chat/completions`, which the door refuses.

  The same code is in 1.6.0. An id the catalog holds resolves exactly, before either, and
  takes that catalog's API. So an id alone is not enough: 1.4.0 resolves
  `~anthropic/claude-fable-latest` exactly, to its `openai-completions` entry, and calls
  `/chat/completions` on the Messages API's base just the same.
- **models.json** (`model-config.js`, `provider-composer.js`):
  - a provider's `baseUrl` alone re-routes its built-in models, which keep their records.
    That is all the record's file sets today (`{"providers": {"{provider}": {"baseUrl": …}}}`).
  - a `models` entry whose id matches a built-in **replaces it whole**
    (`applyModelsJson`/`modelFromJson`). Its `compat` is the provider's and the entry's only;
    missing fields take pi's defaults (128000 context, 16384 output, no reasoning).
  - `modelOverrides` merges into a built-in, and ignores an id the catalog lacks.

### Should the engine be handed the run's model record instead?

The question was whether a run should carry its model's record (API, base, id) from the
gateway's catalog, so the engine's own catalog never matters. That would mean writing it as a
`models` entry. Rejected, for three reasons:

- **The record the owner holds is not the whole model.** `contract/src/model-record.ts` keeps
  no `compat`: it shapes the upstream request, and the owner reads none of it. A `models` entry
  replaces the built-in model, so handing that record would drop the built-in's `compat`. In
  pi-ai 1.6.0, 413 of OpenRouter's 417 chat models carry one, all 18 of Anthropic's and all
  47 of OpenAI's. Claude Fable's is `forceAdaptiveThinking: true`, on OpenRouter as on
  Anthropic. The gateway passes a sandbox call through raw (`forward.ts`), so pi's own encoding
  is what the provider receives. A Fable run would then send the thinking shape Fable refuses.
- **Carrying `compat` too** would put request-shaping fields into every connection's stored
  record, the doors' answers and a declared endpoint's input. That would be a second copy of
  the catalog, sent with every run, which is a copy of values.
- **pi's catalog already is the platform's,** at one version. pi resolves a model through the
  same pi-ai module the gateway lists by, so the same version is one source by construction.
  Every model the owner can hand a pi run is then one pi resolves exactly. It has the same API,
  `compat` and limits, and neither the fuzzy match nor the fallback can happen.

The other engines are not in this class. Claude Code, Codex and Gemini CLI each run on their
vendor's one provider (`api_key_env`), on one API. They send the id they are given, and carry
no pi-ai.

### What moves pi

- **The pin:** `sources/records/pi.json` `cli.install` (version, integrity, `lock_sha256`),
  and its lock, `sources/locks/pi/{package.json,package-lock.json}`. `sources.test.ts` holds
  the lock to the record.
- **The image:** `sandbox/template/image.ts` stages each engine's lock. Its tag is the digest
  of the build's inputs, so a new lock moves the tag. The image installs from that lock.
- **The pin of the image:** `sandbox/wrangler.jsonc` `E2B_TEMPLATE`.
  - The deploy kit refuses a tree whose image is not the pin (`deploy.ts` `treeImage`).
  - `template.test.ts` passes a tree whose new image is not built yet. It fails only a pin
    with no passing record, or a passing image left unpinned.
  - So after this PR every deploy waits for the image's build and pin, #5852's among them.
    That is intended: no gateway on pi-ai 1.6.0 reaches the stage beside an image of another
    version.
- **Building it** is `.github/workflows/sandbox-template.yml`, dispatched on `main` with
  `mode: build` (founder-approved, `production` environment). It builds the image when E2B has
  no ready build of its tag. Then it runs every standing check on throwaway sandboxes: the
  sign-ins, each keyed route (two turns, the second resumed) and the matcher. Only when all pass
  does it record the pair. The run's `passed-json` artifact and the new pin then land as one PR,
  as `template-pin.md` did.
- **What the stage picks up:** a sandbox is made from the pin only when its authority has none
  (`sandbox/src/control.ts` `#ensure`). A running or paused one is resumed whatever template it
  was made from, and a paused, idle sandbox is never retired. So a new pin reaches every new
  sandbox, and not an owner's existing one. That is a hidden link of its own: no row names its
  template. Owed below as its own lane; it needs its own research.

### Is pi 1.6.0 the same engine to the record?

Read against the record, every field the platform uses:

- **The same in both:**
  - the flags `--mode json`, `--session`, `--provider`, `--model`, `--skill`, `--no-approve`,
    `--tools`, `--no-extensions`, `-e` and `builtin:` extensions;
  - `PI_CODING_AGENT_DIR` and `PI_SKIP_VERSION_CHECK`;
  - the JSON stream's events and fields (`docs/json.md` is the same file);
  - `docs/security.md` (the same file);
  - the extension events the hooks and decide templates use.
- **Documented in 1.6.0's `docs/extensions.md`:** the fields those templates read
  (`ctx.sessionManager.getSessionId()`, `event.toolName`, `event.input`, `event.text`,
  `event.reason`, `{ block, reason }`, `{ action: "handled" | "continue" }`, a
  `before_agent_start` message with `customType` and `display: false`). 1.4.0's shorter page
  did not document most of them, so the record now cites 1.6.0.
- **The changelog's breaking changes** (1.5.0, 1.5.1, 1.6.0) concern extension UI components,
  grep's `readFile`, tool-argument validation and an empty `tools` field. The record uses none
  of them.
- **Anthropic message threads** (1.5.1) are on by default only for `api.anthropic.com`. A run's
  base is the egress door, so they are off.
- **Node:** pi 1.6.0 needs `>=22.19.0`; the image's Node is v22.22.0.
- **Install scripts:** the lock still holds two packages with one (`@google/genai`,
  `protobufjs`), so `lifecycle_scripts` stays true.
- **The catalog lock:** the new lock's pi-ai 1.6.0 has the platform lock's integrity, byte for
  byte.

## The design

- **pi moves to 1.6.0:** its lock is regenerated from the registry, and the record pins it. The
  two cites now name 1.6.0's pages, read today.
- **The link is held, not hoped for.** A sources guard requires something of every engine lock
  that carries a package the gateway runs on (`gateway/package.json` `dependencies`, which is
  pi-ai and nothing else). Each copy of that package in the lock must be the platform's own,
  with the same version and integrity. No record field and no engine name is needed for it.
  - A pi-ai bump without pi fails CI, and so does a pi bump without pi-ai.
  - The bump that passes moves the image's tag, so its deploy waits for the build and the pin.
  - The pi-ai watch (`model-catalog.md`'s owed `untangle/pi-ai-watch`) therefore proposes the
    two together.
- **An id names one API.** pi resolves the run's model by its id, and takes that catalog entry's
  API. The owner also finds a run's model by id (`launch.ts` `#runApi`) and hands the base of
  that model's API. The two agree only while each id is on one API in its provider. In pi-ai
  1.6.0 that holds on all eight providers pi keys (anthropic 18 chat models, openai 47, google
  26, openrouter 417, xai 4, groq 7, cerebras 2, mistral 34). The same guard holds it, ids
  compared without case, as pi matches them.

### Against #5852's plan

#5852's route probe names one model per engine, provider and sealed API. The model is the one
the platform ranking places first among the gateway catalog's pickable models on that API, and
`newerFirst` breaks ties. Each model is from the gateway's pi-ai, so with one pi-ai it is in
pi's catalog, on that API. Checked for its nine pi steps against pi-ai 1.6.0:

| provider | API | model | pi's entry |
|---|---|---|---|
| anthropic | anthropic-messages | `claude-fable-5-2` | anthropic-messages |
| openai | openai-responses | `gpt-6.1-sol` | openai-responses |
| google | google-generative-ai | `gemini-4-neon` | google-generative-ai |
| openrouter | openai-completions | `openai/gpt-6.1-sol-pro` | openai-completions |
| openrouter | anthropic-messages | `anthropic/claude-fable-5.2` | anthropic-messages |
| xai | openai-responses | `grok-4.7` | openai-responses |
| groq | its one API | `qwen/qwen3.8-27b` | openai-completions |
| cerebras | its one API | `qwen-3.8-27b` | openai-completions |
| mistral | its one API | `mistral-large-4-0-8` | mistral-conversations |

Each is one exact entry. The tie-break, or another ranking, can only pick another model of the
same catalog, so the result holds for any pick. The live proof is the template build: it runs
the probe plan of the `main` it is dispatched on, with real pi on throwaway sandboxes. It proves
all nine steps only once #5852 is on `main` too, so the build is dispatched after both merge.
- **Comments:** `owner/test/runs.test.ts` named "pi 1.4.0's" stream. The stream is unchanged,
  and the comment now names no version.

## Owed

- [ ] untangle/pi-ai-watch: the watch's draft PR bumps the platform's pi-ai and pi's record and
  lock together (the new guard requires it), and says that its merge waits for a
  `sandbox-template.yml` build and the pin PR.
- [ ] untangle/template-move: a sandbox made from a template other than the pin keeps its
  engines for as long as it lives. No row names its template, and a paused, idle sandbox is
  never retired. So a new pin never reaches an owner's existing sandbox. It needs its own
  research: what E2B reports of a sandbox's template, what a replacement loses (the workspace
  is synced), and when one is safe (no live run). Until it lands, the live bug stays live for
  every owner whose sandbox already exists, the stage's among them: the pin and the deploy fix
  it only for sandboxes made after them, and the template build's pass is on throwaway ones.
- [ ] live: the stage's pi runs on pi 1.6.0 only once its owner's sandbox is made from the new
  pin (untangle/template-move, or that sandbox killed at E2B with no run live, an
  irreversible act the founder approves); then one `auto` pi run on an OpenRouter connection
  sealed with the Messages API answers.
- [x] live: `sandbox-template.yml` (`mode: build`, on `main` once this and #5852 have merged)
  builds `tinyassets-sandbox:sb31f0bce0edffd2a` and passes every standing check, all nine of
  pi's route steps among them. Its `passed-json` and the pin in `sandbox/wrangler.jsonc` land
  as one PR, and deploys wait for it. Done: run 46112336531 on main f81d40aa6, landed as #5863.
