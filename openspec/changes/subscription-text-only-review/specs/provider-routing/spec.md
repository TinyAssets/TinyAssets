## MODIFIED Requirements

### Requirement: Every role chain terminates at the local model
The provider router (`tinyassets/providers/router.py`) SHALL define a fallback chain for each ordinary LLM role (`writer`, `judge`, `extract`, `embed`) that ends at the `ollama-local` provider, so an ordinary role call keeps producing output with zero cloud providers reachable subject to its existing owner authority. Roles with no explicit chain SHALL default to the `writer` chain. For ordinary role calls, the system SHALL only stop for provider unavailability when the local model itself is also unavailable. Required external-write review SHALL be an explicit exception: it SHALL route only within the command center's authorized, different-family, enforced-text-only review candidates, SHALL NOT enter a generic role chain, and SHALL hold if that eligible set is empty even if another model is reachable.

#### Scenario: writer routes to local when all cloud providers are gone
- **WHEN** an ordinary `writer` call is routed and every non-local provider is unregistered, in cooldown, or filtered out
- **THEN** the router attempts `ollama-local` subject to existing owner authority
- **AND** returns its response instead of raising when it is available

#### Scenario: chains cover the four canonical roles
- **WHEN** the router resolves an ordinary chain for `writer`, `judge`, `extract`, or `embed`
- **THEN** the resolved chain ends with `ollama-local`
- **AND** an unknown ordinary role name resolves to the `writer` chain

#### Scenario: Required review has no eligible candidate
- **WHEN** a required external-write review has no currently authorized different-family source with enforced text-only support
- **THEN** it holds the external write with an actionable owner remedy
- **AND** neither the conversation's writer chain nor a reachable local model is used to bypass eligibility

#### Scenario: An owner-connected local model qualifies
- **WHEN** a local model is explicitly authorized for this command center's review, has a different verified family, and has enforced text-only execution
- **THEN** it can be selected as a review candidate on the same terms as any other source
- **AND** local availability alone grants no review authority
