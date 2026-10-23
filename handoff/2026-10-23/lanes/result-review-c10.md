Status: pin binding looks context-safe so far (no mutable module state, lifespan binds). Now checking the `ta` bridge path and then the tool-list/handle comparison.

Status: standalone-node registry removal found; now checking for callers still passing removed kwargs and for on-disk `__standalone_nodes__` / `.node_registry.json` readers.

Status: domain deletion side-effects look clean so far; checking the moved canon writer's path-confinement helper, then the goals/outcomes split and the public canary.

Tool surfaces are identical (names, params, descriptions) on both sides; only 7 `extensions.*` action scopes vanished. Checking whether any public handle routed to them.

Status: no stale imports left in tests. Now checking which main tests referenced the removed modules, and running the touched test files.

Status: owner/center binding comes from the authenticated per-endpoint bearer path, not caller input. Last check: importing every `tinyassets` module on both sides to catch import-time failures.

I found no blocking defect. The engine-server fold is close to a mechanical move. The one real semantic change, where owner and center pins come from, is sound: they now come from the authenticated endpoint, not a module copy. The only user-visible removals are the retired standalone-node paths, and no public handle exposed those.

**How I checked**
- **Tool surface:** I dumped the tools and action scopes from `origin/main` (extracted with `git archive` into the scratchpad) and from HEAD, then diffed them.
  - Owner-pinned engine tools: the same 14 names, with identical parameter schemas and descriptions.
  - Public handles (`read_graph`, `write_graph`, `run_graph`, `read_page`, `write_page`, `converse`, `get_status`): identical, so the `mcp_public_canary.py --assert-handles` expectations still hold.
  - Action-scope registry: 197 rows before, 190 after. The only removals are `extensions.{register,list,inspect,approve,disable,enable,remove}`. No remaining action changed effect.
- **`universe_tools.py`:** it is exactly the old `universe_tools.py` plus `engine_mcp_server.py` concatenated. The only edits are the pins becoming `_actor_id()`/`_graph_id()`, `sys.modules[__name__]` becoming `current_server()`, the async `read_file`/`write_file`/`edit_file` renamed to `served_*`, and `api.extensions` renamed to `api.workflow_actions`. The merged module has no duplicate top-level names, so nothing gets silently shadowed.
- **Imports:** importing every `tinyassets.*` module fails nothing on either side. Nothing outside docs, openspec, attic and `output/` still names `engine_mcp_server`, `api.market`, `api.extensions` or `domains.*`. `discovery.py` handles a missing `domains/` directory.
- **Tests (Windows):** `test_c10_tool_surface`, `test_served_*`, `test_shape_registry`, `test_workspace_authority` and `test_engine_secret_inventory` pass. The other 5 failures are all `socket.AF_UNIX` and the 16 errors are all the Linux-only box driver. That leaves `test_owner_extensions` and the box-based tests in `test_ta_capabilities` (cross-owner extension, grant widening, signed-turn mutation) **not run here**. They need Linux CI or `scripts/linux_oracle.py`, and a skip is not a pass. `check_shapes.py`: clean.

**Cross-owner binding:** it is not caller-supplied.
- `binding()` reads a ContextVar that only `EngineEndpoint.__call__` sets, after the per-endpoint bearer check (`tinyassets/engine_endpoint.py:92-104`). Lifespan is bound too.
- Nodes reach served tools over the HTTP route (`graph_compiler.py:2017` via `open_engine_tools`), never through an in-process client, so the pins always come from the authenticated endpoint.
- The `ta` bridge (`ta_capabilities.py:591` `run_coroutine_threadsafe`, `asyncio.to_thread`) carries the caller's context.
- The new engine code keeps no mutable module state, so endpoints sharing the module don't share state.
- With no binding, the pins fall back to `""` and the call refuses (`universe_tools.py:424-425`, `:5183-5192`).
- `test_c10_tool_surface.py:8` proves two concurrent endpoints keep separate pins.

**Findings**

| Severity | Where | What |
|---|---|---|
| Low (user-visible) | `tinyassets/api/branches.py:2180-2186`, `:2218` | `node_ref={"source":"standalone",...}` now answers "branch not found". The check that refused a bare `node_id` colliding with a standalone registration (#66) is gone. Branches that copied a node earlier keep their snapshot, so no workflow breaks. |
| Low (user-visible) | `tinyassets/api/workflow_actions.py` (whole file), `auth/provider.py:519` | The 7 `extensions.*` node-registration actions above are removed, and `_extensions_impl` lost its `dependencies` and `enabled_only` parameters. No caller passes either one. No public or served handle routed to these actions; main itself said discovery was "not exposed by the advertised handles". |
| Low (data) | The `__standalone_nodes__` branch row in each author DB; root `.node_registry.json` | Nothing reads these any more, and the JSON→SQLite migration in `_ensure_standalone_branch` was deleted. The data is orphaned, not lost: `scoped_reset.py:243` and `storage_accounting.py:672` still account for the JSON file. If anyone wants the bodies, the old code is in git history. |
| Low (test weakness) | `tests/support/box_world.py:85`, `tests/engine_authority_helpers.py:50` | Fixtures still pin by monkeypatching the module-level `_ACTOR_ID`/`_GRAPH_ID` fallback. The box authority tests therefore exercise the fallback, not the production `binding()` path; only `test_c10_tool_surface` covers that path. |
| Nit (stale guidance served on the public door) | `tinyassets/universe_server.py:688-726` | The `extension_guide` prompt still says "registered nodes are discovered and conditionally wired… at the declared phase" and gives a "register a consistency-checker" example. |
| Nit | `tinyassets/api/branches.py:2234`, `:51`, `:1205`, `:2150` | Error text and docstrings still mention `'standalone'`. |
| Nit | `docs/architecture.md:51`, `:123-124`, `:155` | The map still lists `engine_mcp_server.py` and the fantasy/research_probe domains, while rows S6, S7 and S18 now claim converged. |
| Nit | `.github/workflows/linux-jail-proof.yml:125/128`, `:202/205` | `universe_tools.py` is listed twice. `build-image.yml:38` `fantasy_daemon/**` and `pyproject.toml:123` `"fantasy_author"` were already stale on main. |
| Nit | `tinyassets/checkpointing/sqlite_saver.py:20-21`, `:42` | The usage docstring still uses `graphs["scene"]`, and there is a leftover empty `if TYPE_CHECKING: pass`. |

**Every user-visible removal I can confirm**
1. The `extensions` actions `register`, `list`, `inspect`, `approve`, `disable`, `enable` and `remove`.
2. `node_ref` with source `"standalone"`.
3. The standalone-collision refusal for a bare `node_id`.
4. The bundled fantasy and research_probe domains. They had no public handle, and the `compile_all_graphs` checkpointing helper went with them.
5. The `domains.fantasy_daemon` check in `tier3_smoke`.

No MCP tool, public handle, tool parameter or tool description changed.

VERDICT: AGREE
