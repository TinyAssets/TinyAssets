# K1 continuation: review dispositions

Review input: review-k1-r1.md (Claude, ADAPT). All four findings are accepted.

1. AGREE: `conversation_agent` now participates in exact outside agent scope.
   Swept every registered public tool: read_graph, write_graph, run_graph,
   read_page, write_page, converse, get_status. The three agent selectors are
   agent_id, agent_binding_id, conversation_agent. Agent definition and import
   stage IDs are package resources, not addressed-agent selectors. Regression
   demonstrated the ungranted conversation selector passed before the fix.
2. AGREE: the UI fence catches SettingsError per projection, logs its UI/agent
   identity, and hides that projection while retaining ordinary library entries.
   A selected hidden card resets to default. Regression raised SettingsError
   before the fix and now verifies the surviving UI and diagnostic.
3. AGREE: remove dispatch's rejection of current authority beyond the activation
   ceiling. ExtensionStore.active already intersects current authority and the
   stored ceiling; bound connection resolution still checks that intersection.
   New connections no longer break mounted tools/commands/hooks. The three
   regressions failed before the fix. Pre-U1 ordinary code still uses its launch
   socket; no narrower package process boundary is claimed (design.md).
4. AGREE: real-effector wire/identity regression in progress; completion evidence
   will be recorded in the next verified slice.

First slice: Linux oracle 56 passed, zero skips. Ruff and plugin mirror/import
probe passed. No existing tests removed, skipped or weakened. New test fixture
isolation was corrected after the red run (mount context and refusal wording).
