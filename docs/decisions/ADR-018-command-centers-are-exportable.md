# ADR-018: Users Can Always Take Their Command Center With Them

## Status

Accepted. Not yet built.

## Date

2026-10-01

## Context

Users hate lock-in and can build an alternative if their options are taken
away. The platform must win on leverage, not on holding their work.

## Decision

One action exports a whole command center to a folder on the user's computer:
harness, roster, rules, workspace, wiki and brain, workflows and schedules,
selected memory, and UI layouts. It uses the same bundle format as sharing and
import. The folder runs standalone with a local model through a small runner,
with no platform account, and is publish-ready as a repository (README, license
placeholder, a secret-excluding `.gitignore` and `.env.example`). Credentials
are never exported. Publishing it anywhere stays user-built.

## Consequences

- The principle is permanent; the module that implements it is refactored as
  the harness changes. Queued in
  `openspec/changes/universe-agent-harness/design.md` §4.17 (D11).
