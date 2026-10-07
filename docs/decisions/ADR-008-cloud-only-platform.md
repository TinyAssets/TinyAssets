# ADR-008: Cloud-Only Platform; the Founder's Desktop Is Never Infrastructure

## Status

Accepted

## Date

2026-09-21

## Context

Earlier host, tray and local-fallback designs let a personal computer serve
platform work. The founder's home PC (`DESKTOP-KCPMGP3`) had local
registrations, tunnels and provider logins that made it look eligible.

## Decision

The public platform and its hosted command-center paths run only on cloud
infrastructure. `DESKTOP-KCPMGP3` never serves platform traffic, executes
platform or command-center work, relays a model, holds required runtime state,
schedules recovery, or acts as a temporary, emergency or fallback dependency.
An existing local process, credential, tunnel, registration or heartbeat is not
permission. Admission requires independently established cloud eligibility; a
hostname denylist alone is not the boundary. Development tools and browsers on
a PC are not platform services.

## Consequences

- This supersedes earlier host/tray bridge, host-fleet and local-fallback
  language for the hosted platform.
- Acceptance is negative admission and routing tests plus real cloud-only
  behaviour. As-built: `openspec/specs/cloud-only-runtime-admission/`; in
  flight: `openspec/changes/cloud-only-runtime-admission/`.
