# L4: editable connect-anything skill

Branch: `feat/connect-anything-skill`. Scope: starter Markdown, delivery through
existing creation/file/handbook surfaces, and scripted tests. No UI changes,
provider-specific code, new model, credential handling or authority surface.

## Investigation

- `api/universe.py:_action_create_universe` calls `universe_bundle.seed_okf_bundle` at
  creation. `universe_tools.skill_index` reads `skills/<name>/SKILL.md` afresh;
  the ordinary read/write/edit tools expose those owner files. Harness packages
  carry `skills/` and remap installed agent content under `agents/<slug>/`.
- `starter-seed-lifecycle` and `starter-agent-out-of-plumbing` are unbuilt on
  this branch's base. This lane adds content to the current creation entry point,
  not another receipt store or per-turn retrofit. Existing owners can fetch the
  same Markdown from handbook chapter `write_graph.connect` and save/edit it as
  a normal skill. Automatic upgrades remain with the planned lifecycle; its
  future manifest can consume `tinyassets/skills/connect/SKILL.md` unchanged.
- `connection_oauth/discovery.resolve_offer` checks the data directory in
  `connection_oauth/providers.json` before standard discovery. A `connect` ask
  invokes this path and offers inline sign-in when configured. The jail cannot
  read platform source; the skill uses the server lookup rather than asking the
  agent to open `providers.json` itself.
- `api/pending_requests` accepts secret fields only for deposit actions. The
  secure owner form calls `answer_request`, which deposits through `connect_http`;
  the agent composes the request but never receives the key.
- `ta_capabilities` lists and calls owner-bound HTTP connections mid-turn.
  `ta_cli` discovers reusable workspace extensions. Native MCP attachment and
  browser login remain unavailable; the skill states those limits explicitly.

## Verification

`test_connect_skill.py` covers creation/index/edit preservation, existing-account
copying/deletion, `ta search connect`, and a scripted HTTP-model turn through the
real engine request handler. The synthetic key enters only in a separate owner
form submission. The read verification replaces the remote effector, not request,
directory, deposit, vault or connection discovery. This proves the tool contract,
not an unscripted model's decisions or a real external service connection.

`test_connect_skill_jail.py` reads and edits the seeded file through real engine
tools and bubblewrap. Existing seed tests remain unchanged.

Final verification, 2026-10-04:

- Windows Python 3.14: **320 passed, 18 skipped, 1 failed** across
  `test_connect_skill`, `test_universe_bundle`, `test_universe_tools`,
  `test_engine_mcp_server`, `test_first_contact`, `test_mcp_instruction_surfaces`,
  `test_ta_capabilities`, `test_agent_node`, and `test_pending_requests`.
  The failure reproduces in an untouched base checkout; see
  [the separate concern](../concerns/2026-10-04-windows-provider-jail-mount-test.md).
- Linux oracle Python **3.11.16**, bubblewrap **0.12.0**, uid 1001:
  **340 passed, no skips**, the same suite plus `test_connect_skill_jail`.
  All five new tests passed here; all four portable new tests passed on Windows.
- Touched-Python Ruff, skill validation, diff whitespace and rebuilt plugin
  mirror parity all passed. No existing test was edited, renamed or removed.
- Scope ends at a branch push as requested: no PR, production deployment or
  live account connection was attempted.
