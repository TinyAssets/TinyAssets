# inbound-webhook-triggers (delta)

## ADDED Requirements

### Requirement: A served agent can manage its owner's inbound webhooks

The served graph tools SHALL let a bound agent create an inbound webhook for
one of its owner's own branches, list the universe's active webhooks, and revoke
one. They SHALL use the same owner-scoped handlers as the connector's
`run_graph webhook_op`. The universe and the owner SHALL come from the server's
pins, never from tool arguments. A delivery to a created webhook SHALL enqueue
the branch as `universe:<id>` for the owner who created it, on that universe's
current serving provider, with the run named `webhook`.

#### Scenario: Create a webhook that runs as the owner
- **WHEN** a bound agent calls `write_graph target=webhook operation=create branch_id=<own branch>`
- **THEN** it receives a URL and token shown once, plus the token's non-secret prefix
- **AND** a POST to that URL enqueues the branch in the pinned universe with the bound owner as principal

#### Scenario: A branch the owner did not author is refused
- **WHEN** the branch was authored by someone else
- **THEN** the create is refused as not found and no hook is stored

#### Scenario: List without the secret
- **WHEN** the agent reads `read_graph target=webhooks`
- **THEN** it receives each active hook's branch and token prefix, and never the raw token

#### Scenario: Revoke by the listed prefix
- **WHEN** the agent revokes with `payload_json {"token_prefix": ...}` matching exactly one active hook in its universe
- **THEN** that hook is revoked and further deliveries answer the uniform 404 and enqueue nothing

#### Scenario: Another universe's hook cannot be revoked
- **WHEN** the prefix or token names a hook of a different universe
- **THEN** nothing is revoked and the reply does not reveal that the hook exists

#### Scenario: Creation is admission-limited
- **WHEN** engine admission refuses
- **THEN** no hook is minted and the refusal names the cause

#### Scenario: Malformed request
- **WHEN** the operation is unknown, create lacks branch_id or carries a payload, or revoke lacks a selector
- **THEN** a structured refusal is returned and no hook changes
