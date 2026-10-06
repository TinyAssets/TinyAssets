## ADDED Requirements

### Requirement: General Typed Named-Connection Secret Bundle Resolution
The vault SHALL support a general `connection` credential record and a single daemon-side
resolver that returns a TYPED, per-connection-type secret bundle for one named connection, or an
empty result when the universe's vault holds no such connection. The bundle shape SHALL be fixed
by connection type rather than a single untyped "secret" — for example Slack SHALL keep its bot
token and its non-interchangeable app-level token as distinct bundle members, and Twitter SHALL
carry its four OAuth 1.0a values — so that a resolver caller cannot conflate members or provoke
whole-record exposure. Resolution SHALL be vault-first and SHALL NEVER fall through to host
process environment variables; an absent connection means "this universe is not authorized for
that connection", not "borrow the host's credential". The resolver SHALL be invoked only inside
the credential-blind execution seam (the spawned broker child), and its returned bundle SHALL
NEVER be echoed into caller-visible summaries, logs, run state, or evidence. The existing
per-service resolvers (`resolve_github_token`, `resolve_slack_token`, `resolve_slack_app_token`)
MAY remain as thin compatibility wrappers over this resolver until every caller is migrated, then
be collapsed.

#### Scenario: A named connection resolves as a typed bundle by connection type
- **WHEN** a Slack connection is resolved inside the credential-blind seam
- **THEN** the resolver returns a typed bundle whose bot token and app-level token are distinct members, without the caller specifying a service-specific record shape

#### Scenario: Missing connection returns empty and never falls through to host env
- **WHEN** the universe's vault holds no record for the requested connection
- **THEN** the resolver returns empty and does NOT read any host process environment variable as a fallback

#### Scenario: The resolved bundle stays out of summaries and evidence
- **WHEN** a connection bundle is resolved and used for a call
- **THEN** no vault write summary, log line, run state, or effect evidence contains any bundle member
