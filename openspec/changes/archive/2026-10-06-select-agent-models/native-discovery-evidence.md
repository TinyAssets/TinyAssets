# Native discovery evidence for preference integration

September10 2026. Documentation and installed CLI help only; no account discovery,
provider inference, credential read, hosted endpoint or new dependency installed.
These source-derived implementation choices need opposite-family review before
building the native adapter. They do not gate unrelated already-reviewed HTTP work.

## Codex boundary

Official [App Server model/list](https://learn.chatgpt.com/docs/app-server#list-models-modellist)
documents paginated available models, opaque identifiers, input modalities, hidden
entries and a recommended default. includeHidden permits listing entries omitted
from the normal picker. This supplies a dynamic discovery seam without compiling
model releases. The documented initialize/initialized handshake precedes requests.
Installed `codex app-server --help` confirms stdio transport. Do not attach to the
host app-server or inherit its account; a future bounded discovery process must
use the exact owner-bound custody snapshot and suppress unrelated tools/hooks.
Metadata discovery must not replace primary writing via codex exec.

Do not manufacture capabilities from missing fields. The documentation's
backward-compatible modality default is not fresh account-specific evidence.
Preserve discovery freshness, recommended default and actual-answer telemetry as
separate facts. Do not print account records or credentials when probing models.

## Claude boundary

Official [Agent SDK reference](https://code.claude.com/docs/en/agent-sdk/typescript)
documents supportedModels and initializationResult; ModelInfo carries opaque value,
optional resolvedModel and display metadata. Its current reinitialize method
refreshes the initialization response rather than returning the cached initial
one. This establishes a control-protocol investigation seam, not proof that every
model/alias is enumerated or that the snapshot is freshly account-filtered.
The page was retrieved as official Markdown because the browser fetch rejected
the large HTML response. No third-party issue is treated as current behavior.

Installed `claude --help` confirms per-session --model and an ordered --fallback-model
option; primary retries can occur within its native behavior. Do not enable that
fallback automatically before mapping it to accepted model/account scopes and
proving no progress replay. Preserve primary writing via claude -p. Do not adopt
an API-key SDK writer or hardcode aliases from the documentation examples.

## Shared engine implication

Connection adapters normalize discovery and per-request selection; the shared
kernel sees opaque references, source kind, default, freshness and capabilities.
Unknown future executors need a declared discovery/selection contract, not a new
release-name table in the engine. Provider-default execution remains distinct
from full model enumeration; neither is evidence the other works. Native/local
availability must derive from this owner's executable route and custody, not
installed maintainer binaries. Actual bound-account discovery tests are pending.
