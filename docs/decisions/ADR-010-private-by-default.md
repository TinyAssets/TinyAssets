# ADR-010: Command Centers and Their Nodes Are Private by Default

## Status

Accepted

## Date

2026-09-26

## Context

The founder: *"universes and the nodes in them need to be default private and
we need to make sure that is set correctly for new users also"*.

## Decision

The platform never declares an open level on an owner's behalf. Every creation
path writes `private`: first-contact home materialization (the new-user path),
the declaring migration, and Branch creation. Exposure is a separate, explicit
owner action. A visibility level the platform does not enforce on every reader
is not offered.

## Consequences

- As-built: `openspec/specs/universe-visibility/spec.md`; change
  `openspec/changes/archive/2026-09-30-private-by-default-universes/`.
