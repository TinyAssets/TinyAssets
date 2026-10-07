## ADDED Requirements

### Requirement: Shared unpowered model catalogue
The read_graph handle SHALL accept target=model_options without changing its
arguments or direct string/structured-adapter return contract. The read SHALL
require the authenticated owner's complete current home and explicit admin ACL.
It SHALL NOT create a home, agent, assignment, preference or inference grant.

#### Scenario: Unpowered current home
- **WHEN** the owner has a complete home but no serving agent or working model
- **THEN** the read returns available registered inventory or an empty catalogue
- **AND** unavailable sources and missing saved model references remain distinguishable

#### Scenario: Unknown or foreign scope
- **WHEN** an explicit graph is not the current owned home or lacks admin access
- **THEN** the read refuses without disclosing that graph's model inventory
- **AND** omitted scope never resolves to a designated public universe

#### Scenario: Complete choices, not a first-page sample
- **WHEN** approved discovery returns more models than the default read limit
- **THEN** all protocol-bounded choices survive in structured content
- **AND** limit does not silently hide models from this catalogue target

#### Scenario: Registration is not execution authority
- **WHEN** an owned registered HTTP source has approved discovery but is not accepted for inference
- **THEN** its models remain visible with source_not_accepted and no execution candidates
- **AND** server-derived bind keys and complete existing model_access constraints are provided separately

#### Scenario: Freshness and source-level reasons
- **WHEN** a source is revoked or expires during refresh
- **THEN** that source loses its model rows without concealing independent sources
- **AND** a changed home, admin scope, serving binding or assignment refuses the whole snapshot
- **AND** source failures are a separate channel, not invented empty model identifiers

#### Scenario: Existing native default
- **WHEN** the owner has a current legacy native serving chain
- **THEN** its provider default is visible as legacy_single_provider
- **AND** the read neither invents an actual model name nor grants expanded model selection

#### Scenario: Existing legacy HTTP configuration
- **WHEN** the current legacy serving chain survives the read's final authority fence
- **THEN** legacy_source identifies its provider, bind key and configured fixed model
- **AND** these fields are not actual answering-model receipts or newly admitted candidates
- **AND** revocation during refresh clears the legacy-source projection

### Requirement: Served model setup preserves the person-only access boundary
The served agent SHALL be able to read pinned model options and its universe's
private agent bindings, save current-home model preferences by expected
generation, and configure discovery metadata on existing owned connections.
These operations SHALL NOT grant inference, widen endpoints, change spending
ceilings or silently enroll a provider. Shared connector preference saving SHALL
use the same parser, actor/current-home checks and generation store.

#### Scenario: Catalogue admission and isolation
- **WHEN** the served agent requests model options or an exact private binding
- **THEN** the graph is server-pinned and foreign binding ids are not disclosed
- **AND** catalogue refresh requires admission and envelopes remote strings as untrusted

#### Scenario: Preference and discovery setup are not grants
- **WHEN** a model preference or discovery descriptor is saved
- **THEN** inference membership and connection grants remain unchanged
- **AND** stale generation, changed home and outside-grant URLs refuse without overwrite

#### Scenario: Explicit owner model-access approval
- **WHEN** the agent raises a bind_model_access pending request
- **THEN** the server validates current home, creator, revision and each owned source before showing it
- **AND** captures the baseline assignment and exact model-access proposal server-side
- **AND** the person sees deterministic before/after scope, unchanged spending ceilings and reconnect warning
- **AND** other accepted providers and their scopes are preserved; new sources are free-only
- **AND** the request has no answer fields and the served agent cannot answer it

#### Scenario: Reconnection failure can be retried safely
- **WHEN** publication or reconnect fails during the person's confirmation
- **THEN** the request remains pending and the UI shows the actionable error without claiming delivery to an offline agent
- **AND** replay distinguishes untouched, failed-publication, bound and serving states using revision, exact membership and assignment fences
- **AND** repeated failed publication generations are recoverable without blindly overwriting a superseding assignment
- **AND** reconnect rechecks the exact assignment digest and current home inside its transaction
- **AND** success is reported only after serving and request resolution are confirmed

### Requirement: Converse accepts non-authoritative current model choice
The authenticated converse handle SHALL accept optional model_choice using the
existing versioned preferences document. Omission SHALL use supported saved
preferences or preserve absent-policy legacy behavior. A current override SHALL
replace the entire current order without modifying saved defaults or authority.

#### Scenario: One-turn explicit choice
- **WHEN** an authorized owner supplies valid explicit model_choice
- **THEN** this turn captures that primary and exact fallback tail
- **AND** the saved row and generation remain unchanged

#### Scenario: One-turn automatic choice
- **WHEN** model_choice requests automatic mode
- **THEN** this turn clears the saved primary and uses eligible automatic ordering
- **AND** it does not append the old default as an explicit fallback

#### Scenario: Invalid or unauthorized choice
- **WHEN** the document is malformed, unsupported, outside supported owner scope or accepted authority
- **THEN** the runtime refuses before launch without widening access

#### Scenario: Cross-client compatibility
- **WHEN** the changed tool is tested through ChatGPT and Claude
- **THEN** both render structured results and final narration without wedging
- **AND** protected canary --assert-handles and deployed-SHA gates remain required
