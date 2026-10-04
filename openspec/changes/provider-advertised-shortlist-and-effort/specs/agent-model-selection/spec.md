# agent-model-selection (delta)

## ADDED Requirements

### Requirement: A source's own current shortlist is selectable without a platform release

An executor that declares a metadata protocol SHALL have its advertised models
admitted as ordinary candidates, carrying `availability_basis`
`executor_enumerated`, whenever the accepted member's model scope is
`discovered`. Such a model SHALL NOT carry `model_access_optin_required`: that
reason belongs to reviewed-list and owner-verified candidates, which remain
offers to grant.

Enumerated rows SHALL be keyed on the executor's resolved execution identifier,
never on a display label or an alias. Where an executor lists several alias
entries resolving to one execution id, they SHALL collapse to one choice, and
SHALL collapse only when every advertised fact about them agrees; disagreeing
facts for one id SHALL refuse the catalogue.

A row the executor itself marked unselectable SHALL NOT be offered as a choice.

An executor that declares no metadata protocol SHALL continue to report unknown
enumeration, and its independently authorized provider-default invocation SHALL
remain usable. An installed executor that declares a protocol but ANSWERS that
it does not implement the method SHALL report the same unknown enumeration
rather than a source failure: support is detected by asking, never from a
platform-held version or release table. Any other error SHALL remain a failure.

#### Scenario: An executor too old for the method

- **GIVEN** an accepted native source whose installed executor answers that the
  metadata method is unsupported
- **WHEN** the catalogue is read
- **THEN** the source reports unknown enumeration, not a failure
- **AND** its provider-default invocation and the reviewed candidate list are
  unaffected

#### Scenario: A newly released model appears without a platform change

- **GIVEN** an accepted native source whose model scope is `discovered`
- **WHEN** the executor's catalogue begins advertising an id it did not before
- **THEN** the next catalogue read offers that id as a selectable choice with
  basis `executor_enumerated` and no refusal reason
- **AND** no reviewed static list was edited and no release shipped

#### Scenario: An alias and its resolved model are one choice

- **GIVEN** an executor listing a `default` alias and a named alias that both
  resolve to the same execution id
- **WHEN** the catalogue is read
- **THEN** exactly one choice is offered, identified by the resolved execution
  id, and it is marked as the source's default

#### Scenario: Two conflicting claims for one execution id

- **GIVEN** an executor advertising one execution id twice with different
  advertised capabilities
- **WHEN** the catalogue is read
- **THEN** the read refuses, rather than choosing one of the two claims

#### Scenario: A withdrawn model is not offered

- **GIVEN** an executor marking an advertised row unselectable
- **WHEN** the catalogue is read
- **THEN** that row is not among the selectable choices

### Requirement: Effort levels are advertised per model and never invented

The catalogue SHALL carry, per model, the effort levels its source advertised,
in the source's own order. A model whose source advertised none SHALL carry an
empty set, and a client SHALL NOT present an effort control for it.

The platform SHALL NOT define, substitute or extend an effort vocabulary. A
source claiming effort support without naming its levels SHALL refuse the
catalogue rather than be read as supporting a guessed set.

An absent choice SHALL mean the executor's own default, never a level the
platform selected.

#### Scenario: Two models of one source advertise different levels

- **GIVEN** one source advertising five levels for one model and four for
  another
- **WHEN** the catalogue is read
- **THEN** each model carries exactly the levels its source named for it

#### Scenario: A model with no effort support

- **GIVEN** a source advertising a model without effort support
- **WHEN** the catalogue is read
- **THEN** that model carries no levels and no control is presented for it

#### Scenario: Claimed support with no levels

- **GIVEN** a source claiming a model supports effort but naming no levels
- **WHEN** the catalogue is read
- **THEN** the read refuses

### Requirement: A saved effort level is held to what its model advertised, and reaches the provider

The owner's effort level SHALL be stored per model, independently of selection
mode and of which model is currently default, so that changing model or
returning to automatic does not clear a level set for another model.

An OWNER-PREFERENCE level SHALL be admitted only against the advertised levels
of the exact enumerated model being launched, checked where the catalogue is in
hand. A level outside that set SHALL refuse the launch rather than run at the
executor's default, because running at a level the owner did not choose while
reporting success is a silent failure.

An admitted owner-preference level SHALL be carried to the provider as that
provider's own real setting, not as prompt text, and SHALL travel on the same
validated per-attempt authority as the selected model id, so an ordinary
caller's configuration cannot raise a served turn's effort. Where that authority
carries an enumerated selection, it SHALL be the only source of the level —
including when the owner saved none, which clears any inherited value.

An owner-declared model id, which carries no advertised level list, SHALL NOT
attest an effort level.

This requirement governs the owner-preference path only. A workflow node's own
declared effort is separate, pre-existing node configuration on a path that has
no advertised level list to check against (a provider-default invocation names
no model), and SHALL continue to reach the provider unchanged. An enumerated
selection SHALL NOT overwrite it, because a selection that could not validate a
level has nothing to say about one.

#### Scenario: A workflow node's own effort is not erased by selection

- **GIVEN** a workflow node declaring its own effort
- **WHEN** it runs on a native source whose selection is owner-declared or a
  provider default
- **THEN** the provider receives the node's declared effort

#### Scenario: A saved level reaches the invocation

- **GIVEN** an owner who saved an advertised level for their selected model
- **WHEN** a served turn launches on that model
- **THEN** the provider is invoked with that level as its own setting

#### Scenario: A level the model no longer advertises

- **GIVEN** a saved level for a model whose source has stopped advertising it
- **WHEN** a served turn launches on that model
- **THEN** the launch is refused and no inference runs

#### Scenario: Switching model preserves another model's level

- **GIVEN** a saved level for one model
- **WHEN** the owner makes a different model their default
- **THEN** the first model's level is still stored

#### Scenario: No level saved

- **GIVEN** a model with no saved level
- **WHEN** a served turn launches on it
- **THEN** no effort setting is passed and the executor's own default applies

### Requirement: Stored preferences and evidence from before effort remain readable

A stored preference document written before per-model effort existed SHALL read
as carrying no effort choice, and SHALL NOT be treated as unreadable. Stored
native selection evidence written before effort existed SHALL continue to parse
unchanged.

#### Scenario: An existing saved default is read

- **GIVEN** a preference document stored in the pre-effort shape
- **WHEN** the owner's preferences are read
- **THEN** the saved default and order are returned with no effort choice, and
  the picker is not held
