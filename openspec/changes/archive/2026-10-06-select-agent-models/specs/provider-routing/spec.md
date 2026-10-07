## ADDED Requirements

### Requirement: Reconnecting an accepted source preserves model setup
An owner-driven reconnect of a source in a ready accepted model manifest SHALL preserve the existing root, accepted membership, model scopes, spending ceilings, model preferences and the selected agent content last authored by that owner. Credential replacement SHALL NOT imply consent to replace that manifest with legacy single-provider setup, adopt collaborator edits or add a new source.

#### Scenario: Refreshing a non-root accepted source
- **WHEN** the owner reconnects a source already accepted alongside other models
- **THEN** the original root and accepted model setup remain intact while current custody is revalidated
- **AND** no other user's credentials, approvals or connections are substituted

#### Scenario: Unknown or concurrently changed model setup
- **WHEN** the nominated source is unaccepted, the model assignment changed, or the current owner binding cannot be identified safely
- **THEN** reconnect refuses or requests existing owner confirmation without discarding the model setup, broadening access, or resetting private agent content

#### Scenario: Credential stored but serving cannot resume
- **WHEN** the credential deposit succeeds but binding or enablement fails
- **THEN** the app reports that partial result without claiming successful sign-in or switching to another source

### Requirement: Automatic interactive-agent selection respects owner priorities
Automatic interactive-agent routing SHALL prefer eligible owner-connected subscription or local sources over OpenRouter, then order suitable OpenRouter models using fresh ranking evidence; explicit owner choices and accepted fallback order SHALL override automatic ranking.

#### Scenario: Multiple connected sources
- **WHEN** automatic mode has an eligible subscription/local source and OpenRouter
- **THEN** it starts with the subscription/local source without borrowing any ambient host credential

#### Scenario: OpenRouter only
- **WHEN** automatic mode has only OpenRouter
- **THEN** it selects the best-ranked eligible model and maintains ordered compatible alternatives, without embedding model-release names in platform code

### Requirement: Fallback preserves spend and inference authority
Every interactive fallback attempt SHALL revalidate current owner authority, capability and cost constraints; free onboarding SHALL NOT authorize paid fallback.

#### Scenario: Revoked candidate
- **WHEN** an accepted fallback's grant is revoked before invocation
- **THEN** no inference uses that grant and no broader credential is substituted

#### Scenario: Free model withdrawn
- **WHEN** a free-only choice becomes unavailable or paid
- **THEN** the runtime uses another authorized free compatible candidate or waits without charging for a paid replacement

### Requirement: Capacity fallback preserves completed work
The runtime SHALL distinguish model-local capacity from proven shared account limits, honor retry information and preserve completed tool results without replaying effects.

#### Scenario: Account allowance exhausted
- **WHEN** a typed refusal identifies exhaustion shared by all models on an account
- **THEN** the runtime skips those sibling models and tries an authorized independent source or shows a waiting state

#### Scenario: Ambiguous tool completion
- **WHEN** an inference fails after a tool might have executed without a durable result
- **THEN** fallback does not replay that action as a fresh turn
