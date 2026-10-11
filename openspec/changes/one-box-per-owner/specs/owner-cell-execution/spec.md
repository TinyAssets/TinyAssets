## MODIFIED Requirements

### Requirement: Tool networking retains filtered egress
Tool bash SHALL reach permitted HTTP and HTTPS endpoints only through the owner box's egress
gate at the broker. The box itself SHALL have no network interface.

#### Scenario: Production-copy acceptance
- **WHEN** acceptance restores the production copy and starts the real server
- **THEN** background and automation code and HTTP graphs, a sub-agent, and tool bash curl to a local fixture succeed through the box egress gate
- **AND** cross-owner, private-destination and owner-disallowed requests remain refused

## REMOVED Requirements

### Requirement: Cell execution resolves the admitted owner
**Reason**: Execution runs in the owner's box under one admission at session start.
**Migration**: `owner-box` "Admission happens once at box entry"; background, automation and sub-agent sessions bind the same owner's box.

### Requirement: Cell protocol failures retain bounded diagnostics
**Reason**: Cells are deleted.
**Migration**: `BoxProvider` errors carry the operation id and a bounded reason.
