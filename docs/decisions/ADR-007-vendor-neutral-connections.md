# ADR-007: Vendor-Neutral Connections, No Vendor Code

## Status

Accepted

## Date

2026-09-24

## Context

Vendor-specific provider paths meant every new model source, CLI update or API
change needed a platform patch, and the platform's provider list decided what a
user could connect.

## Decision

Users connect any LLM or other platform through the same vendor-neutral
connection primitives:

- generic OAuth with token refresh, preferred whenever the provider offers it,
  discovered through standard metadata (RFC 8414, OpenID configuration, PKCE);
- API key, bearer or custom-header auth, as the fallback;
- standard HTTP model protocols, such as OpenAI-compatible chat;
- local or self-hosted endpoints;
- a user-configured command adapter.

An LLM is one more connection, like any other online service. No vendor names,
code paths or special cases exist on the platform; today's vendors are
configurations of these primitives. Model choice belongs to the user and their
connection: explicit selections and saved fallback order win, and the default is
the strongest connected source, not a compiled model list. A failure is recorded
as structured facts (stage, effects, provider detail, class, reference), not
looked up in a per-error message table. Dev tooling is exempt.

## Consequences

- Existing vendor-specific paths are migration debt, removed as they are
  touched.
- As-built: `openspec/specs/provider-routing/`,
  `openspec/specs/universe-connection-oauth/`,
  `openspec/specs/agent-model-selection/`,
  `openspec/specs/conversation-failure-history/`.
