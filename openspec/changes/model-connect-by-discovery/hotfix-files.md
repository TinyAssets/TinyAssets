# Source-record convergence handoff

Scope: WX changes relative to the supplied `995cf0f1c1d` worktree. Production was not inspected or changed. The branch already contains W and X and broader inherited isolation work; this is not a blanket instruction to copy the full `origin/main...HEAD` diff.

## Files by running role

Paths below are repository-relative. Copy the shared group with every Python role: the loader validates all installed records even when that process only uses HTTP. Python code uses existing dependencies; schema validation at runtime does not require jsonschema. Existing credential stores, provider definitions, and pending-flow tables keep their layouts.

### Shared: router/daemon container, broker container, box harness

- `tinyassets/providers/source_record.py`
- `tinyassets/providers/source_record.schema.json`
- `tinyassets/providers/source_records/claude-code.json`
- `tinyassets/providers/source_records/codex.json`
- `tinyassets/providers/source_records/gemini.json`
- `tinyassets/providers/source_records/kimi.json`
- `tinyassets/providers/source_records/openrouter_user_models_v1.json`
- `tinyassets/providers/source_records/groq.json`
- `tinyassets/providers/source_records/google_ai_studio.json`
- `tinyassets/providers/source_records/mistral.json`
- `tinyassets/providers/source_records/cerebras.json`
- `tinyassets/providers/subscription_source.py`
- `tinyassets/providers/discovery_presets.py`
- `tinyassets/providers/free_sources.py`
- `tinyassets/connection_oauth/model_discovery.py`
- `tinyassets/onboarding/hosted_model_auth.py`

### Router/daemon container, additionally

- `tinyassets/onboarding/__init__.py`
- `tinyassets/onboarding/model_connect.py`
- `tinyassets/onboarding/inline_model_connect.py`

### Broker container, additionally

- `tinyassets/broker/model_connect.py`

### Box harness, additionally

- `tinyassets/api/pending_requests.py`
- `tinyassets/onboarding/subscription_connect.py`
- `tinyassets/providers/subscription_runtime.py`
- `tinyassets/providers/subscription_catalog.py`

### Page

- `tinyassets/onboarding/app/main.js`

### Retired data paths

The new loaders do not read these paths. Remove them with the coordinated copy, after their replacement records are present:

- `tinyassets/providers/acquisition_presets.json`
- `tinyassets/providers/free_source_presets.json`
- `tinyassets/providers/model_discovery_addresses.json`
- `tinyassets/providers/source_contract_presets.json`
- `tinyassets/providers/subscription_sources/{claude-code,codex,gemini,kimi}.json`

The formerly disabled free-source sign-in entry is no longer an installed special case; its addresses use ordinary discovery. Owner-authored files use the new schema in `.ta-cli/sources/`. Existing owner-authored files in the previous CLI format must be converted before loading them; login files themselves are unchanged.

## Inherited X prerequisites if the image only has W

The supplied baseline already includes these X files. They remain needed on an image that has not received X:

- Broker: `tinyassets/broker/http_connect.py`, `tinyassets/broker/server.py` (discovery operation admission).
- Box: `tinyassets/onboarding/source_connect.py` (detected model composition).
- Shared: `tinyassets/connection_oauth/transport.py` (bounded catalogue reads), `tinyassets/connection_oauth/pkce.py` (pending-flow layout repair), `tinyassets/onboarding/session_store.py` (broker import avoids router restore-seal custody).

All role groups must agree before serving the new page. Deploy/import evidence here is the local Linux oracle on Python 3.11, not a test against a production image. No deployment, live canary, or production SHA assertion was attempted under the explicit production prohibition.

## Floor-class files in the WX delta

- `tinyassets/providers/source_record.py`, `source_record.schema.json`, every `source_records/*.json`, and `subscription_source.py`: exact egress, native execution declarations, credential custody and source validation.
- `tinyassets/connection_oauth/model_discovery.py`: discovered API and authorization destinations.
- `tinyassets/broker/model_connect.py`: protected broker discovery and declaration admission.
- `tinyassets/onboarding/model_connect.py`, `inline_model_connect.py`, `hosted_model_auth.py`: owner gestures, PKCE, browser-bound return, one-shot exchanges and pending-flow compatibility.
- `tinyassets/onboarding/subscription_connect.py`, `tinyassets/providers/subscription_runtime.py`, `subscription_catalog.py`: owner-box login, invocation and selection fingerprints.
- `tinyassets/providers/discovery_presets.py`, `free_sources.py`: model eligibility and billing metadata projections from records.
- `tinyassets/onboarding/app/main.js`: PKCE storage and handoff/return behavior, including the exact stale-approval exception.

No gate files were changed in WX. The inherited combined branch has gate/floor changes outside this delta. Independent cross-family floor review was not performed because this task expressly prohibits sub-agents. Existing model-use consent and spending guards remain in place after connecting; connection completion closes the dialog and any required model-use approval stays on the request rail.

## Verification

- Linux oracle: 131 targeted tests passed; the final HTTP/record/native-return regression pass added the declared-default case and passed all 102 selected tests. Coverage includes real owner-box installation, native login, restart/isolation, CLI model selection/invocation, HTTP discovery/exchange/model call, native return, hosted pending-flow regression, and the affected heavy instruction-surface tests.
- Chromium: one address/Connect control, API-key fallback and secret clearing, web same-tab CLI return, native CLI handoff, and expired approval query cleanup; six cases pass.
- Provider/host guard covers source loaders, mechanism consumers, detector, broker, flow and the page controllers.
- Pending hosted-flow digest was compared to the supplied baseline and is unchanged for the migrated record.
- JavaScript syntax, changed Python ruff checks, and git whitespace checks pass.
- `origin/feat/box-subscription-sources` was fetched and merged again; latest observed head remains `14e0de05767`, already contained.
