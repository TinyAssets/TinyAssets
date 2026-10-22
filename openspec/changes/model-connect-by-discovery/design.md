## Context

Generic connection OAuth already implements RFC 8414/OIDC, RFC 9728, RFC 7591, PKCE, issuer/callback DCR caching and credential custody. Model onboarding still requires installed source data or manually supplied endpoints.

## Goals / Non-Goals

An address discovers an OpenAI-compatible catalogue and OAuth; successful authorization uses the existing HTTP connection and model definition. No production work or changes to routing/model selection.

## Decisions

Discovery executes in the broker through the existing hardened transport. Probe an address-relative models endpoint and /v1/models; accept only a real catalogue response or explicitly advertised model endpoints. An authentication challenge alone does not prove a model API. Advertised endpoints come from protected-resource metadata. Store exact paths and hosts in the existing frozen flow definition. Never re-discover token destinations during exchange.

The page posts an address and PKCE challenge. No OAuth returns an API-key option with the discovered endpoints. Existing presets remain usable until discovery covers their acquisition protocol. Generic model composition requires owner confirmation and does not claim unknown pricing is free.

Page integration is a delimited block plus one connectBody hook, avoiding lane U2's row implementation. No schema migration is needed.

## Risks / Trade-offs

- Private catalogues without advertised endpoints cannot be identified safely before authentication: report no model API instead of guessing.
- Unknown billing must be shown at confirmation; never manufacture free pricing from a bare catalogue.
- Floor files: broker discovery/dispatch, onboarding ingress/definition and source composition (authority and money), OAuth flow if modified (credentials).
- Independent floor review remains required before merge; this lane expressly prohibits sub-agents.

## Migration Plan

Draft PR to conv/c6-forward-deploy; coordinated router, broker, box and page release by the deployment lane. Retain legacy acquisition until covered. No production changes in this lane.
