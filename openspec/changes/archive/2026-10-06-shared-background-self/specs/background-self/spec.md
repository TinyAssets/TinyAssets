## ADDED Requirements

### Requirement: Shared-self Branch invocation
An explicitly opted-in owner-authored single writer prompt Branch SHALL reuse the foreground universe persona, founder-thread reconstruction and sandboxed engine tools after existing run admission and a current owner-home/admin check. Run inputs SHALL NOT activate this capability. Ordinary Branches SHALL retain their existing execution behavior. Missing tools and ambiguous multi-prompt shared-self shapes SHALL fail visibly. Background execution SHALL NOT create synthetic founder messages or run a learning extractor against generated output.

#### Scenario: Background retrieval parity
- WHEN the founder starts an opted-in wake
- THEN the current persona and recent principal conversation are loaded through the same helpers as converse
- AND the instance can use the same pinned live graph, brain and retained-conversation retrieval tools.

### Requirement: Retained conversation retrieval
The engine read_graph conversation target SHALL expose only the verified current founder's principal session in the pinned universe, as untrusted evidence. It SHALL support stable metadata pagination and exact Unicode message chunks without creating or migrating storage. Deleted history SHALL NOT be represented as available.

#### Scenario: Older message and concurrent new arrival
- WHEN an instance pages toward older messages while a new message arrives
- THEN the before-message-id cursor preserves older coverage
- AND an exact message id belonging to another session returns no content.
