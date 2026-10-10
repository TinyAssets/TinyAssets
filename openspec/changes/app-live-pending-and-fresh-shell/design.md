## Context

The heartbeat reads only activity; history loads once. Canonical admission already durably stores intent before execution but readers only inspect the terminal transcript. The shell sends no-store; build probes use release metadata and typing holds can prevent upgrades.

## Goals / Non-Goals

Make accepted conversation intent and completion visible across surfaces and update shell code without draft loss. No new execution, authority, or admission storage.

## Decisions

Read pending canonical intent from the existing admission/run records under the verified home and principal. Expose pending entries separately from paged terminal rows, with admission identity and live state. Exclude terminal projections using their durable marker, including the crash window before the admission projection flag updates. Preserve connector fencing. Merge both sets in the browser by stable identity; poll with owner/home/agent/login checks and no overlapping reads.

Use a hash of served shell assets/config as the build identity, retaining no-store for shell and immutable content-keyed modules. Reuse the existing session-scoped draft recovery for automatic updates. Avoid reload while an in-flight send or attachment cannot be recovered safely.

## Risks / Trade-offs

Cross-owner leakage -> query exact authenticated principal, universe and run owner; negative tests and cross-family review. Projection races -> pending read precedes terminal read and client merge uses stable identity. Poll load -> one nonoverlapping read per heartbeat. Draft loss -> save before navigation, restore only matching owner/home/agent, refuse automatic navigation if storage fails.

## Migration Plan

No schema migration. Existing admissions immediately become observable. Deploy server and shell together; rollback leaves durable data unchanged.
