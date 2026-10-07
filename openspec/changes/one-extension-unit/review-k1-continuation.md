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
4. AGREE: four real-effector/broker/vault cases prove exact JSON-RPC bytes,
   MCP session/protocol/Accept headers, JSON and SSE success, actual stalled SSE
   partial bodies, unknown outcome without replay, and outside effect leases.
   They exposed missing launch identity inside ta's async dispatch. Dispatch now
   binds the captured identity across awaited coroutines and worker threads.
   All four failed before this fix. The sink uses the git fixture's synthetic
   HTTPS loopback/TLS seam and the effector fixture's in-process broker seam;
   the real driver uses a shortened idle window to produce stalled responses.
   This proves adapter handling of broker-stalled bodies, not a new production
   idle-stream policy for non-inference HTTP connections.

Second slice: Linux oracle remote MCP and ta tests 42 passed, zero skips.
Main merge check: origin/main a97c17c26e already included. Claude cross-family
review via peer-agents: APPROVE, all four findings AGREE; full review in
review-k1-final.md. Its stale-note observation was already corrected while
the review was running. Broader Linux oracle: 232 passed, zero skips. Ruff
passes all 45 changed canonical Python files; mirror/import probe passes.
Hygiene: 74 added test functions, 0 removed, 0 tampering. The four new regression
functions expand to 11 cases. No static prompt budget changes.

First slice: Linux oracle 56 passed, zero skips. Ruff and plugin mirror/import
probe passed. No existing tests removed, skipped or weakened. New test fixture
isolation was corrected after the red run (mount context and refusal wording).

Final affected heavy-file run (provider_work_authority, universe_server_isolation,
branch_runner, plus the final remote fixture): 219 passed, zero skips.
Final fetch/merge check found origin/main a97c17c26e already included.
All four requested fixes complete; PR may be marked ready. Deployment, live-user
acceptance and post-deploy spec sync remain pending, not claimed here.
