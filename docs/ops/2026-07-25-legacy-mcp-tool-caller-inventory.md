# Legacy hidden-MCP-tool caller inventory

> **Historical (2026-08-26):** `scripts/navigator_wiki_sweep.py` was deleted. The
> inventory below records what was true when written; it is not a live runbook.


**Change:** [the `retire-legacy-live-mcp-tools` change](https://github.com/TinyAssets/TinyAssets/tree/88e74e1cd32b1ad19bb8af9bf88ab3bd68e4e979/openspec/changes/archive/2026-08-26-retire-legacy-live-mcp-tools) — evidence for tasks 2.1, 2.2, 2.3.
**Date:** 2026-07-25. **Provider:** claude-code (Opus 5), branch `claude/o5-retire-inventory`.
**Revision:** **v4** — v2 (`9bd88a07`) reworked v1 (`18379010`) after an opposite-provider review
returned **reject**; v3 folded a second review's **adapt** corrections; and v4 folds the third
review's **adapt** correction to Finding 2.3-F's failure mode and canonical reachability. B2's
17/5 result and Findings 2.3-D/2.3-E are unchanged. See §10 for the full disposition of all rounds.
**Base:** analysis tree = `18379010`. Re-verified against `origin/main` on 2026-07-25; the
intervening main commits are STATUS/coordination-only and touch no file cited here.
**Method:** read-only static analysis over the working tree — Python AST (binding-aware),
language-aware literal-call scan over JS/TS/Svelte/mjs, dispatch-table extraction, plus live `gh`
PR-state queries. No runtime code was changed.

Scope note: this is the **repository-internal** inventory. It does **not** satisfy task 1.3
(external hidden-name call telemetry) — see §9.

---

## 1. Premise verification

`tinyassets/universe_server.py` registers **13** MCP tools via `_register_structured_tool`:

- **7 canonical:** `read_graph`, `write_graph`, `run_graph`, `read_page`, `write_page`,
  `converse`, `get_status`.
- **6 hidden legacy:** `universe`, `community_change_context`, `extensions`, `goals`, `gates`,
  `wiki` (`_DEPRECATED_TOOL_NAMES`, universe_server.py:1030).

`_DeprecatedToolVisibility` (universe_server.py:1983) drops the six from `tools/list` but
`on_call_tool` still forwards them — **hidden yet dispatchable**, as the change premise states.

Two consequences worth recording:

- **`scripts/mcp_public_canary.py` is blind to this.** Its `CANONICAL_HANDLES` check reads
  `tools/list`, which the middleware has already filtered. A green `--assert-handles` run is
  therefore *not* evidence that the six are gone. This is exactly why task 3.1 requires a
  registry test "with listing middleware bypassed."
- **Anonymous calls to the six already fail** in gating auth modes: `on_call_tool` raises
  `ToolError` when `write_gate_rejection(name)` fires (the #1441 anonymous-write gate). Only
  signed-in callers and dev mode still reach them.

---

## 2. Task 2.1 — fresh ownership and collision state

Nine PRs named in task 2.1. Head SHAs pinned because open-PR state on a saturated fleet expires
quickly; **the four open rows were re-queried on 2026-07-25 for this revision and are unchanged.**

| PR | State | Draft | Touches retirement runtime files? |
|---|---|---|---|
| #1560 | CLOSED | no | — (no longer an owner) |
| #1550 | CLOSED | no | — |
| #1549 | CLOSED | no | — |
| #1493 | **OPEN** `a4115dd3` | draft | `tinyassets/universe_server.py`, `api/extensions.py`, mirror, `tests/test_universe_server_isolation.py` |
| #1478 | CLOSED | draft | — |
| #1467 | **OPEN** `5fb6d16a` | draft | `universe_server.py`, `api/extensions.py`, **`tinyassets/directory_server.py`**, mirror |
| #1466 | **OPEN** `4427d012` | draft | `universe_server.py`, `api/universe.py`, mirror, `tests/test_universe_server_ledger.py` |
| #1465 | **OPEN** `8eef71b8` | draft | `universe_server.py`, `api/extensions.py`, `api/extensions_consent_actions.py`, mirror |
| #1464 | CLOSED | draft | — |

**Resolution:** five of nine are CLOSED and impose no dependency. **Four remain open** and all four
edit `tinyassets/universe_server.py` — the exact file tasks 4.1/4.2 must rewrite. Per task 2.1's
"resolve **or depend**" clause, these are recorded as **binding preconditions on task 4.1** in §11,
not as advisory notes.

**Collision check** (`scripts/claim_check.py --check-files`) for this artifact's write-set
(`docs/ops/2026-07-25-legacy-mcp-tool-caller-inventory.md`,
[`retire-legacy-live-mcp-tools`'s `tasks.md`](https://github.com/TinyAssets/TinyAssets/blob/88e74e1cd32b1ad19bb8af9bf88ab3bd68e4e979/openspec/changes/archive/2026-08-26-retire-legacy-live-mcp-tools/tasks.md)): **CLEAR — no overlap with any
claimed/in-flight row.** This inventory lane claims no runtime files, so the four open-PR
collisions above cannot be resolved here; they transfer to the implementation lane.

**Honest limit on 2.1:** this artifact does not retain raw command output for the scans, and the
`origin/main` freshness stamp decays. Re-run the collision check at implementation-claim time
rather than relying on this row.

---

## 3. Task 2.2 — stdio-server boundary

- **PR #1561 (`1a5d45af`, OPEN draft — re-queried 2026-07-25) is confirmed limited to the separate
  legacy stdio server.** Its complete file set is 3 files: `tinyassets/mcp_server.py`, its packaged
  mirror, and `tests/test_legacy_mcp_server_fence.py`. It touches neither `universe_server.py` nor
  the canary.
- **`tinyassets/mcp_server.py` is excluded from this change and is structurally unaffected.** It
  is a separate `FastMCP` instance whose tool surface is disjoint from the six: `get_status`,
  `add_note`, `steer`, `get_premise`, `set_premise`, `get_progress`, `get_work_targets`,
  `get_review_state`, `get_chapter`, `get_activity`, `pause`, `resume`, `add_canon`. **None of the
  six legacy names is defined or registered there**, so unregistering them from `universe_server.py`
  cannot affect it.
- **`tinyassets/directory_server.py` is already absent from `origin/main`** — deleted by
  `60f7f9f1` ("Retire /mcp-directory and converge on canonical /mcp", #1718). It must not return.

> ### ⚠ Finding 2.2-A — open PR #1467 carries a modify-delta against the deleted `directory_server.py`
>
> `gh api .../pulls/1467/files` reports `modified +30/-3` on **both**
> `tinyassets/directory_server.py` and its packaged mirror. That branch predates `60f7f9f1`, so
> merging it without a rebase either raises a modify/delete conflict or, if resolved toward the
> branch, **restores the retired directory surface** — a direct violation of tasks 2.2 and 4.4.
> #1467 must be rebased past `60f7f9f1` (and its `directory_server.py` delta dropped) before it
> merges, independently of this retirement change.

---

## 4. Task 2.3 — typed caller census

The decisive distinction: **unregistering an MCP tool breaks only *wire* callers.** A Python
caller holding `from tinyassets.universe_server import extensions` binds the plain wrapper
function and is completely unaffected by `mcp.tool` registration. The census is therefore
classified by **binding kind**, and — per the v1 review — each class is labelled by what it
actually is, not by a rolled-up number.

### 4a. Census summary

| Class | Count | Files | Breaks on unregistration? |
|---|---|---|---|
| **A.** Python explicit imports (`from ...universe_server import <legacy>`) | 57 | 26 | No |
| **B.** Python module-attr **references** (`us.<legacy>` / `us2.<legacy>`) | 119 | 41 | No |
| ⤷ B1. of which are **calls** | 102 | — | No |
| ⤷ B2. of which are **non-call introspection** (`callable`, `inspect.signature`, `__doc__`, `pytest.param`) | 17 | **5** | No |
| **C.** Python **call expressions**, total (C1 + B1) | **376** | **62** | No |
| ⤷ C1. via explicitly imported names | 274 | — | No |
| **D.** Python star import of the module (`import *`) | 1 | 1 | No |
| **E.** Wire calls — Python scripts | 4 | 2 | **YES** |
| **F.** Wire calls — website JS/TS/Svelte/mjs | **38** | **8** | **YES** |
| **G.** Wire call — dynamic user-driven dispatcher | 1 | 1 | **YES** |
| **H.** Wire call — registration-level test | 1 | 1 | **YES** (must be updated at 4.1) |
| **I.** Metadata residue (`data_source.tool`) | 3 panels | 1 | No dispatch |
| **J.** Response-label residue (`"tool": "goals"` / `"gates"`) | 3 | 1 | No dispatch |
| **K.** Instructions telling a client/user to call a legacy name | 8+ | 6+ | User-visible breakage |
| **L.** Authorization / action-scope metadata keyed by legacy tool name | 5 tool keys | 1 | No dispatch — **but see 2.3-F** |

> **B2 file-count correction (v3).** v2's B2 row said these 17 references occupy **4** files; a
> re-run of the binding-aware AST pass returns **5**: `tests/test_api_market.py` (2),
> `tests/test_api_universe.py` (1), `tests/test_goals_discoverability.py` (2),
> `tests/test_mcp_dispatch_docstring_parity.py` (11), `tests/test_validate_ship_packet_action.py`
> (1) = 17. The count **17** was correct; only the file count was wrong. Class-B totals (119/41)
> and the call/non-call split (102/17) are unchanged.

> **Label correction (v1 defect).** v1 reported "**176** Python bindings" in a table whose second
> row was headed *"module-attr call"*. Both labels were wrong:
> **176 = 57 imports + 119 references — a *reference* count, not a caller count**, and only
> **102** of the 119 references are calls. The binding-aware call count is **376 across 62 files**.
> All three numbers reproduce exactly under independent AST enumeration; only v1's *labels* were
> false. Every table below is relabelled accordingly.

### 4b. Class A–D — Python bindings (unaffected by unregistration)

| Caller class | Count | Files | Preserve-or-migrate |
|---|---|---|---|
| `tests/` — explicit imports (A) | 57 | 26 | **Preserve.** Unaffected. |
| `tests/` — module-attr references (B), of which 102 calls / 17 introspection | 119 | 41 | **Preserve.** Unaffected. |
| `tests/` — total call expressions (C) | 376 | 62 | **Preserve.** Unaffected. |
| **Production runtime (`tinyassets/` non-test)** | **0** | **0** | n/a — none exist |
| `scripts/` Python-API callers | 0 | 0 | n/a — the script callers are *wire* callers (§4c) |
| `fantasy_daemon/universe_server.py:2` — `from tinyassets.universe_server import *` (D) | 1 | 1 | **Preserve.** Legacy alias shim; re-exports whatever the module defines. Unaffected as long as task 4.1 keeps the wrapper `def`s. |

**Union: 67 distinct files carry classes A/B.** Every one of those 67 — and every one of the 62
files in class C — is under `tests/`.

> **Qualification the v1 checkoff omitted.** The phrase "all under `tests/`" is true **of classes
> A, B and C**. It is **not** true of every repository import of the module:
> `fantasy_daemon/universe_server.py:2` is a **non-test** `import *` (class D). It binds no
> individual legacy name so it is excluded from the 176/376 arithmetic, but any statement of the
> form "every import lives under `tests/`" is false. Preserving the wrapper `def`s keeps it safe.

> ### ✅ Finding 2.3-A — there are zero production-runtime Python callers
>
> An AST pass over `tinyassets/universe_server.py` confirms that **no canonical handler
> delegates into any of the six legacy wrappers** (zero call edges from
> `read_graph`/`write_graph`/`run_graph`/`read_page`/`write_page`/`converse`/`get_status` into
> `universe`/`community_change_context`/`extensions`/`goals`/`gates`/`wiki`). Both surfaces
> delegate *independently* to the same `tinyassets/api/*` implementations
> (`_universe_impl`, `_extensions_impl`, `api.market.goals`/`.gates`, `api.wiki.wiki`).
>
> **Consequence:** task 4.1's default — "preserve wrapper functions unless 2.3 proves an explicit
> caller migration" — is the correct call. Removing the six `_register_structured_tool(...)`
> blocks while leaving the six `def`s in place breaks **no** Python caller and **no** test import.
> Deleting the wrapper bodies would break 62 test files for no benefit.
>
> This finding survived the v1 review unchallenged and is independently reproduced.

### 4c. Class E/F/G/H — wire callers (BROKEN by unregistration)

**42 literal wire invocation sites across 10 files**, plus 1 dynamic dispatcher and 1
registration-level test. v1 reported "6 wire callers" and missed the entire website class.

**Canonical-replaceability legend:** ✅ = a canonical handle emits the same action;
🔴 = **no canonical handle emits this action** (hard blocker, see §6).

#### E — Python scripts (4 sites, 2 files)

| Site | Call | Canonical replacement |
|---|---|---|
| `scripts/navigator_wiki_sweep.py:162` | `wiki action=list` → **`https://tinyassets.io/mcp`** (default) | 🔴 none |
| `scripts/mcp_probe.py:139` | `universe action=list` | ✅ `read_graph target="graphs"` |
| `scripts/mcp_probe.py:146` | `universe action=inspect` | ✅ `read_graph target="graph"` |
| `scripts/mcp_probe.py:155` | `wiki action=list` | 🔴 none |

#### F — website JS/TS/Svelte/mjs (38 sites, 8 files)

All three clients send a real `tools/call`: `live.ts:103` (`callTool` → `rpc('tools/call', …)`),
`playground.ts:132`, and `snapshot-mcp.mjs:210` (official MCP SDK `client.callTool`). In
production all resolve to path `/mcp` on `tinyassets.io`; `snapshot-mcp.mjs:25` hardcodes
`https://tinyassets.io/mcp`. **These are not dead names in comments.**

**`wiki` — 7 sites**

| Site | Action | Replacement |
|---|---|---|
| `WebSite/site-react/lib/live.ts:142` | `list` (in `fetchLive`) | 🔴 none |
| `WebSite/site-react/lib/live.ts:165` | `read` (in `fetchPageBody`) | ✅ `read_page(page=…)` |
| `WebSite/site/src/lib/mcp/live.ts:139` | `list` (in `fetchLive`) | 🔴 none |
| `WebSite/site/src/lib/mcp/live.ts:162` | `read` (in `fetchPageBody`) | ✅ `read_page(page=…)` |
| `WebSite/site/scripts/snapshot-mcp.mjs:220` | `list` | 🔴 none |
| `WebSite/site/scripts/snapshot-mcp.mjs:324` | `since` | ✅ `read_page(changed_since=…)` — **shape differs**, see §6 |
| `WebSite/site/scripts/snapshot-mcp.mjs:385` | `read` | ✅ `read_page(page=…)` |

**`goals` — 9 sites**

| Site | Action | Replacement |
|---|---|---|
| `WebSite/site-react/lib/live.ts:143` | `list` (in `fetchLive`) | ✅ `read_graph target="goals"` |
| `WebSite/site-react/lib/live.ts:1042` | `list` (in `fetchVitals`) | ✅ same |
| `WebSite/site-react/app/goals/_components/GoalsClient.tsx:160` | `list` | ✅ same |
| `WebSite/site-react/app/goal/_components/GoalDetail.tsx:138` | `get` | ✅ `read_graph target="goal"` |
| `WebSite/site/src/lib/mcp/live.ts:140` | `list` (in `fetchLive`) | ✅ same |
| `WebSite/site/src/lib/mcp/live.ts:1039` | `list` (in `fetchVitals`) | ✅ same |
| `WebSite/site/src/routes/goals/+page.svelte:122` | `list` | ✅ same |
| `WebSite/site/src/routes/goals/[id]/+page.svelte:137` | `get` | ✅ same |
| `WebSite/site/scripts/snapshot-mcp.mjs:221` | `list` | ✅ same |

**`universe` — 5 sites** (all `action=list` → ✅ `read_graph target="graphs"`)

`WebSite/site-react/lib/live.ts:144`, `WebSite/site-react/lib/live.ts:1041`,
`WebSite/site/src/lib/mcp/live.ts:141`, `WebSite/site/src/lib/mcp/live.ts:1038`,
`WebSite/site/scripts/snapshot-mcp.mjs:222`.

**`community_change_context` — 2 sites**

| Site | Replacement |
|---|---|
| `WebSite/site-react/lib/live.ts:160` (`fetchChangeContext`) | 🔴 none — the tool has exactly 1 action and 0 are canonical-reachable |
| `WebSite/site/src/lib/mcp/live.ts:157` (`fetchChangeContext`) | 🔴 none |

*Severity note:* `fetchChangeContext` is **exported but has no current in-tree consumer** (grep
across both trees returns only its own definition). It is a maintained public module export on a
dead call path today — real code, lower blast radius than the rest of class F.

**`extensions` — 15 sites**

| Site | Action | Replacement |
|---|---|---|
| `WebSite/site-react/lib/live.ts:849` / `WebSite/site/src/lib/mcp/live.ts:846` | `list_runs` (branch-scoped) | ✅ `read_graph target="runs"` |
| `WebSite/site-react/lib/live.ts:857` / `…/live.ts:854` | `list_runs` | ✅ same |
| `WebSite/site-react/lib/live.ts:870` / `…/live.ts:867` | `list_runs` | ✅ same |
| `WebSite/site-react/lib/live.ts:904` / `…/live.ts:901` | **`stream_run`** | 🔴 **none** |
| `WebSite/site-react/lib/live.ts:918` / `…/live.ts:915` | `get_run` | ✅ `read_graph target="run"` |
| `WebSite/site-react/lib/live.ts:1043` / `…/live.ts:1040` | `list_runs` (in `fetchVitals`) | ✅ same |
| `WebSite/site/src/lib/mcp/playground.ts:238` | `list_runs` | ✅ same |
| `WebSite/site/src/lib/mcp/playground.ts:244` | `get_run` | ✅ same |
| `WebSite/site/src/lib/mcp/playground.ts:298` | `list_runs` | ✅ same |

`gates` — **0** website wire sites.

**Per-tool totals for class F:** `wiki` 7, `goals` 9, `universe` 5, `community_change_context` 2,
`extensions` 15, `gates` 0 = **38**.

> ### 🔴 Finding 2.3-D (new in v2, corrected in v3) — `extensions action=stream_run` is canonically unreachable
>
> **The blocker is canonical *unreachability*, not non-existence.** `read_graph` emits only `list`,
> `get`, `search`, `inspect`, `list_runs`, `get_run`, `get_branch` (AST-extracted literal `action=`
> kwargs), so **no canonical handle emits `stream_run`**. Both `live.ts` trees call it from
> `fetchMcpPatchLoopFeed` (`live.ts:836`), reached from the rendered `/loop` page via the exported
> `fetchPatchLoopFeed` — and `source='mcp'` is that function's **default** branch. So this is a
> live rendered-page dependency reachable *only* through the legacy `extensions` handle.
>
> **v2 prose defect, corrected.** v2 claimed `stream_run` "appears in the repository only inside
> the legacy `extensions` docstring (`universe_server.py:1413`)". **That is false.** It is a fully
> implemented action: `_action_stream_run` at `tinyassets/api/runs.py:924`, dispatch entry
> `_RUN_ACTIONS["stream_run"]` at `tinyassets/api/runs.py:1891`, enumerated in the extensions
> action list at `tinyassets/api/extensions.py:737`, and covered by live tests
> (`tests/test_branch_runner.py:751,925,963`, `tests/test_api_runs.py:37,65,96`). The retirement
> therefore does **not** delete dead code — it removes the only wire route to working functionality.
> That makes the migration decision *harder*, not easier: a canonical route can be added cheaply
> because the implementation already exists and is tested.
>
> **The same correction applies to every other action this artifact called "orphaned."** "Orphaned"
> throughout §5, §4c, and §4d means **unreachable through the canonical seven**, never
> "unimplemented." Spot-verified: `get_node_output` → `_action_get_node_output`
> (`api/evaluation.py:394`, dispatch `:783`); `get_activity` → `api/universe.py:6084`;
> `give_direction` → `:6088`; `read_premise` → `:6089`; `set_premise` → `:6090`;
> `submit_node_bid` → `:6109`. All are real, dispatchable, and canonically unreachable.

> ### 📌 Class F deployment ambiguity — both trees must be treated as live
>
> The two deploy workflows **contradict each other** about which tree is live:
> `deploy-site.yml:4-8` says "AUTO-DEPLOY DISABLED at the React cutover (2026-06-24): the live site
> is now the React build"; `deploy-site-react.yml:3-6` says the React build "is **NOT** yet the live
> surface. Do not run it until the cutover is approved." Both are now `workflow_dispatch`-only and
> share the `pages` concurrency group, so **either can be dispatched to become the live Pages
> deployment** — `deploy-site.yml` is explicitly retained as the rollback path. Repository evidence
> cannot resolve which is currently serving. **Both trees are therefore counted and both must be
> migrated (or one proven irreversibly retired with host sign-off) before unregistration.**
> Omitting the Svelte tree on "React is live" grounds would silently break the documented rollback.

#### G — dynamic dispatcher (1 site)

`WebSite/site/src/lib/components/Playground.svelte:95` —
`callTool(parsedInput.tool, parsedInput.args)`. The tool name comes from **user text typed into a
public REPL**, so it cannot be enumerated statically. It is nonetheless a *known* legacy caller:
the component ships five legacy presets (`Playground.svelte:33-37` — `wiki action=list`,
`extensions action=list_runs`, `universe action=list`, `goals action=list`,
`wiki action=read`), seeds the input with `wiki action=list` (line 46), injects
`extensions action=get_run` / `extensions action=get_node_output` on click (lines 111-112), and
**auto-fires on load** ("The terminal is loaded with `wiki action=list` and will fire
automatically", line 212). `get_node_output` is also canonically unreachable — but it is a real
implemented action (`_action_get_node_output`, `api/evaluation.py:394`, dispatch entry `:783`),
not docstring residue (v2 mis-stated this; see 2.3-D).

#### H — registration-level test (1 site)

`tests/test_universe_server_five_handles.py:127` — `asyncio.run(mcp.call_tool("universe", …))`,
asserting the hidden tools remain dispatchable and emit `deprecated-tool-call name=universe`. This
is the one test that **must** be updated by task 4.1.

### 4d. Class I/J/K — non-dispatch residue and instructions

| # | Site | Kind | Verdict |
|---|---|---|---|
| I | `packaging/conway/panel-metadata.json` (×3 panels) | `data_source.tool: "universe"` + `action: inspect / get_activity / query_world` | **Fix metadata.** No programmatic loader exists; `packaging/INDEX.md` calls `conway/` "speculative". Task 4.5 residue. |
| J | `tinyassets/api/market.py:2600, 3907, 3946` | `"tool": "goals"` / `"tool": "gates"` in error payloads and `_action_gates_list`'s self-description | **Fix strings.** User-facing labels naming a tool that will no longer exist. Task 4.5 residue. |
| K | `packaging/.../skills/premise/SKILL.md:13,15` | instructs `universe action="read_premise"` / `action="set_premise"` | **Migrate before 4.4.** Both canonically unreachable. |
| K | `packaging/.../skills/progress/SKILL.md:14` | instructs `universe action="inspect"` + `action="get_activity"` | **Migrate before 4.4.** `get_activity` is canonically unreachable. |
| K | `packaging/.../skills/status/SKILL.md:14` | instructs `universe action="inspect"` | **Migrate before 4.4** → `read_graph target="graph"`. |
| K | `packaging/.../skills/steer/SKILL.md:15,30` | instructs `universe action="give_direction"` | **Migrate before 4.4.** Canonically unreachable. |
| K | `bids/README.md:24` | instructs `universe action=submit_node_bid` | **Migrate or retire.** Canonically unreachable. |
| K | `WebSite/site/src/lib/components/Playground.svelte:212` | tells users `wiki action=list` fires automatically | **Migrate with class G.** |
| K | `tinyassets/api/universe.py:2540` | hint string `"Use \`wiki action=list category=…\`"` | **Fix string.** Task 4.5 residue. |

> ### 🔴 Finding 2.3-E (new in v2, corrected in v3) — task **4.4**'s mirror rebuild breaks packaged slash commands
>
> The Claude plugin boots the mirrored server directly:
> `packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/server.py:39-40` does
> `from tinyassets import universe_server; universe_server.main(transport="stdio")`. Four packaged
> skills instruct the client to call `universe`:
>
> | Skill | Action(s) instructed | Canonically reachable? |
> |---|---|---|
> | `premise/SKILL.md:13,15` | `read_premise`, `set_premise` | 🔴 no / 🔴 no |
> | `progress/SKILL.md:14` | `inspect`, `get_activity` | ✅ `read_graph target="graph"` / 🔴 no |
> | `status/SKILL.md:14` | `inspect` | ✅ `read_graph target="graph"` |
> | `steer/SKILL.md:15,30` | `give_direction` | 🔴 no |
>
> **Five unique actions; four of them (`read_premise`, `set_premise`, `get_activity`,
> `give_direction`) have no canonical equivalent** — only `inspect` survives. So this is a
> migration *and* a surface-reduction decision, not a rename.
>
> **Three v2 prose defects, corrected:**
>
> 1. **The trigger is task 4.4, not task 4.1.** The mirror is a checked-in copy that still carries
>    its own `_DEPRECATED_TOOL_NAMES` (`runtime/tinyassets/universe_server.py:1030`) and its own
>    registrations. Task 4.1 edits **source only** (`tinyassets/universe_server.py`); the packaged
>    plugin boots the **mirror**, so the slash commands keep working until **task 4.4 regenerates
>    it**. v2's "the moment 4.1 lands" was wrong — the breakage window opens at 4.4. This matters
>    for sequencing: the skill migration must land **before 4.4**, not before 4.1.
> 2. **The premise skill calls `read_premise`, not `get_premise`.** v2 named `get_premise`, which is
>    a tool on the *separate* stdio server `tinyassets/mcp_server.py` (§3) — not a `universe`
>    action. The real `universe` action is `read_premise` (`api/universe.py:6089`).
> 3. **Four of five, not three.** v2 said "three of the five actions … have no canonical
>    equivalent" while listing only two names plus a vague "premise verbs." Enumerated exactly
>    above: four unreachable, one reachable.

### 4f. Class L — authorization / action-scope metadata (new in v3, corrected in v4)

> ### 🔴 Finding 2.3-F (corrected in v4) — legacy tool names are load-bearing authorization and availability state, not residue
>
> `tinyassets/auth/provider.py:516-618` builds the runtime action-scope table by calling
> `_extend_scope_rows(..., tool=<name>, ...)` five times, keyed on **five of the six legacy tool
> names**: `universe` (`:516`), `wiki` (`:525`), `extensions` (`:580`), `gates` (`:600`), `goals`
> (`:610`). Each row carries `write_actions`, `costly_actions`, and `admin_actions` — including the
> money-write set (`escrow_lock/release/refund/fund/set_wallet/withdraw`) that a prior review
> flagged as silently read-classified.
>
> **Five enforcement sites stay live through canonical routes after retirement; `gates` does not.** The
> api-layer dispatchers hardcode legacy tool names as string literals when enforcing scope, and —
> per Finding 2.3-A — canonical handles delegate into five of those api-layer implementations.
> The `gates` enforcement site is reachable only through the hidden legacy dispatcher: §5 confirms
> that all 20 gate actions have zero canonical routes.
>
> | Enforcement site | Tool name passed | Reached from canonical handles? |
> |---|---|---|
> | `api/universe.py:6235` → `_dispatch_scope_error("universe", …)` → `:6151` | `"universe"` | **Yes** — `_universe_impl` is the shared body |
> | `api/wiki.py:2789` → `_dispatch_scope_error("wiki", …)` → `:2651` | `"wiki"` | **Yes** |
> | `api/extensions.py:399` → `_dispatch_scope_error("extensions", …)` → `:248` | `"extensions"` | **Yes** |
> | `api/market.py:3936` `_gates_scope_error` | `"gates"` (hardcoded) | **No** — all 20 gate actions have zero canonical routes (§5) |
> | `api/market.py:2595` | `"goals"` (hardcoded) | **Yes** |
> | `api/first_contact.py:50` | `"universe"` (hardcoded) | **Yes** |
>
> **Consequence for tasks 4.2 and 4.4.** 4.2's contract is to "remove … the legacy-name set, and
> dead registration-only state." This metadata **looks** like exactly that — five tables keyed by
> names the change is retiring — but it is **not registration-only state**. A lookup miss is
> fail-closed: `action_scope_for(tool, action) -> None` makes `require_action_scope` raise
> `PermissionError("No action-scope metadata … refusing gated dispatch.")`, verified against the
> production source SHA `0603aae1`. Removing or mismatching a row therefore denies the action; it
> does not bypass authorization.
>
> The narrower fail-open risk is a **classification-only** removal: if an action remains in the
> derived registry while its `write_actions` / `costly_actions` / `admin_actions` membership is
> removed, it defaults to `read`; resolve-always mode then permits the read-effect path. The table
> is consequently load-bearing for both authorization correctness and action availability.
> **Tasks 4.2 and 4.4 must preserve it or migrate the registry, every `require_action_scope` call
> site, and the packaged mirror in lockstep, and state which path they took.** A regression test
> must prove both that missing metadata denies and that every mutating action remains non-read.
> It is not a wire caller and does **not** change the 42-site count; `community_change_context` has
> no scope row (it is a fixed single action).
>
> `get_action_scope_status` (`api/extensions.py:399`, returning `action_scope_audit()`) is the
> read-back surface for this table and is itself canonically unreachable.

### 4e. Explicitly excluded false positives

A naive grep for the six names produces these; they are **not** callers and must not be "fixed":

- `WebSite/site/src/lib/content/mcp-snapshot.json` — baked **content data** (fetched 2026-04-30).
  Its `"goals"` / `"wiki"` / `"extensions"` / `"universe"` occurrences are goal tags, wiki
  category keys, universe ids, and `tags` values — never tool handles.
- `packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/**` — the byte-parity
  packaged mirror of `tinyassets/`. It carries the same 6 registrations by construction and is
  regenerated by `packaging/claude-plugin/build_plugin.py` (task 4.4), not edited directly.
  (Its `runtime/server.py` boot path and its `skills/` are **not** false positives — see 2.3-E.)
- `logging.getLogger("universe_server.universe")` / `"universe_server.wiki"` — logger names.
- `callTool('loop', …)` at `WebSite/site-react/lib/live.ts:841` and
  `WebSite/site/src/lib/mcp/live.ts:838` — **not** one of the six. `loop` is not among the 13
  registered tools at all, so this is an *already-dead* wire call, unrelated to this retirement.
  Worth logging separately: these clients already tolerate a call to a nonexistent tool.

---

## 5. Action-reachability gap (the load-bearing result)

Each legacy tool is a **passthrough**: it forwards `action=` verbatim into its api-layer dispatch
table. Each canonical handle is a **narrow target router** emitting only hand-picked literal
actions, with an `_unknown_target` fallback. So the retirement's true blast radius is
*(actions in each dispatch table)* − *(literal actions the canonical seven emit)*.

Canonical literal `action=` kwargs, AST-extracted: `read_graph` → `list`, `get`, `search`,
`inspect`, `list_runs`, `get_run`, `get_branch`; `write_graph` → `create_universe`,
`patch_branch`, `propose`; `run_graph` → `run_branch`; `read_page` → `read`, `search`, `since`;
`write_page` → `file_bug`, `patch`, `write`; `converse` / `get_status` → none.

| Legacy tool | Dispatch table | Actions | Reachable via canonical 7 | **Orphaned** |
|---|---|---|---|---|
| `universe` | `UNIVERSE_ACTIONS` (api/universe.py) | 50 | 3 — `list`, `inspect`, `create_universe` | **47** |
| `community_change_context` | fixed `action="community_change_context"` | 1 | 0 | **1** |
| `extensions` | union of 12 tables (api/extensions.py) | 87 | 5 — `list_runs`, `get_run`, `get_branch`, `patch_branch`, `run_branch` | **82** |
| `goals` | `_GOAL_ACTIONS` (api/market.py) | 14 | 4 — `get`, `list`, `search`, `propose` | **10** |
| `gates` | `_GATES_ACTIONS` + `_GATE_EVENT_ACTIONS` | 20 | **0** | **20** |
| `wiki` | `WIKI_ACTIONS` (api/wiki.py) | 15 | 6 — `read`, `search`, `since`, `file_bug`, `patch`, `write` | **9** |
| **Total** | | **187** | **18** | **169** |

Orphaned sets in full for the three smallest:

- **`wiki` (9):** `consolidate`, `cosign_bug`, `delete`, `ingest`, `lint`, `list`, `promote`,
  `supersede`, `sync_projects`.
- **`goals` (10):** `archive_consultation`, `bind`, `common_nodes`, `define_protocol`,
  `get_protocol`, `leaderboard`, `run_canonical`, `set_canonical`, `set_selector`, `update`.
- **`gates` (20):** all of them — no canonical handle routes to `gates` at all.

**Terminology (tightened in v3).** "Orphaned" here means **not reachable through any of the
canonical seven**. It does **not** mean unimplemented, untested, or dead. Every orphaned action
sampled has a real implementation and dispatch entry (2.3-D). Retirement removes the *route*, not
the code — so restoring any one of them is a routing decision, not new feature work.

**Interpretation.** 169 orphans does *not* mean 169 regressions: the reshape's stated intent was
to shrink the advertised surface, so many are deliberately dropped. What the number establishes is
that **retirement is a surface reduction of ~90% of dispatchable actions, not a rename**, and the
change's own evidence gates treat it as the latter.

**But the orphan set is not merely theoretical — 9 live wire sites already depend on it:**

| Orphaned action | Dependent wire sites |
|---|---|
| `wiki action=list` | 5 — `navigator_wiki_sweep.py:162`, `mcp_probe.py:155`, `site-react/lib/live.ts:142`, `site/src/lib/mcp/live.ts:139`, `snapshot-mcp.mjs:220` |
| `community_change_context` | 2 — `site-react/lib/live.ts:160`, `site/src/lib/mcp/live.ts:157` |
| `extensions action=stream_run` | 2 — `site-react/lib/live.ts:904`, `site/src/lib/mcp/live.ts:901` |

Plus the class-G/K instruction surfaces on `get_node_output`, `get_activity`, `give_direction`,
and `submit_node_bid`. And two live STATUS.md rows depend on orphaned actions:

- *"Mark-branch canonical decision (Task #33 phase 0)"* — names `goals action=propose/bind/set_canonical`.
  `propose` survives; **`bind` and `set_canonical` are orphaned.**
- *"BUG-018 canonical filename trailing-hyphen"* — names `wiki action=promote`; **orphaned.**

Before task 4.1 removes the registrations, the host needs an explicit keep/drop decision on the
169 — at minimum on the three orphaned actions with live callers (above) and the two STATUS rows.

---

## 6. Finding 2.3-B (expanded) — `wiki action=list` has no canonical replacement

**v1 said "2 live scripts depend on it." The real dependent set is 5 wire sites plus the public
playground.**

**Why no canonical handle can reach it.** `read_page` (universe_server.py:702-750) has exactly
**three** routes:

1. `page` nonempty → `_wiki_impl(action="read", …)`
2. `changed_since.strip()` **and** no `query` **and** no `category` → `_wiki_impl(action="since", …)`
3. everything else → `_wiki_impl(action="search", …)`

There is no fourth branch and no `action="list"` anywhere in `read_page`.

- **Empty-search is not a workaround.** `_wiki_search` (api/wiki.py:785-794) returns
  `{"error": "query parameter is required."}` on an empty query, so route 3 cannot enumerate.
- **`since` is not `list`.** `_wiki_since` (api/wiki.py:~910) *requires* a timestamp; defaults
  `scope` to `"discovery"` (and `read_page` exposes no `scope` parameter, so a caller cannot widen
  it); caps results at **100** via `_coerce_result_limit` (api/wiki.py:317-322,
  `max(1, min(raw, 100))`); and returns a `results` shape. `_wiki_list` (api/wiki.py:996-1036)
  applies **no** scope filter, has **no** cap, and returns
  `{promoted, promoted_count, drafts, drafts_count}`.
- **The shape difference is load-bearing, not cosmetic.** Every website consumer reads
  `wikiList?.promoted` / `wikiList?.drafts` (`live.ts:147-148`, `snapshot-mcp.mjs:244,259,315`) —
  keys `_wiki_since` does not emit. A handle swap alone silently yields empty lists, and
  `snapshot-mcp.mjs:455-456` would then hit its own
  `"all responses empty — aborting to avoid clobbering existing snapshot"` guard.

**Full dependent set:**

| Dependent | Impact |
|---|---|
| `scripts/navigator_wiki_sweep.py:162` | Navigator's standing 30-min wiki-sweep cadence; defaults to **production** `https://tinyassets.io/mcp`. (Cadence is an agent instruction in `.claude/agents/navigator.md`, not a GH schedule.) |
| `scripts/mcp_probe.py:155` | Operator probe `--wiki`. |
| `WebSite/site/src/lib/mcp/live.ts:139` (`fetchLive`) | Backs `/`, `/commons`, `/graph`, `/host`, `MoodPill`, `lib/live/project.ts` — **7 rendered Svelte consumers**. |
| `WebSite/site-react/lib/live.ts:142` (`fetchLive`) | Backs `HomeClient`, `CommonsClient`, `GraphClient`, `HostClient` — **4 rendered React consumers**. |
| `WebSite/site/scripts/snapshot-mcp.mjs:220` | The **snapshot baker** — `npm run snapshot`, wired into `deploy-site.yml:54-59` as a build step. Its output `mcp-snapshot.json` is the site's offline fallback content. |
| `WebSite/site/src/lib/components/Playground.svelte` | Public REPL: preset + seeded input + **auto-fires on page load**. |

Retirement must therefore be preceded by either (a) extending `read_page` to reach `list` with the
`_wiki_list` shape, or (b) explicitly retiring every dependent above with host sign-off. This is a
**prerequisite**, not cleanup. The same decision is owed for `community_change_context` and
`extensions action=stream_run` (Finding 2.3-D).

> ### ✅ Finding 2.3-C — the migration pattern already has a working precedent in-repo
>
> `scripts/last_activity_canary.py` performed exactly this migration and documents it in its
> module docstring: it "originally read the `universe action=inspect` fat tool, but since the
> anonymous write gate (#1441) the deprecated fat tools refuse ALL anonymous calls — reads
> included — so an unauthenticated canary must use the canonical handle." It now calls
> `read_graph target="graph"`, which "routes to the same `_action_inspect_universe` payload …
> every shape assertion below [is] unchanged." The **33 of 42** literal wire sites marked ✅ in
> §4c should follow this pattern verbatim.

---

## 7. Focused coverage map (task 2.3 "focused coverage for each wrapper")

Existing per-wrapper test surface, all binding the Python API (so all survive unregistration).
Counts are **distinct test files touching that wrapper** (classes A + B), independently reproduced:

| Wrapper | Test files | Representative |
|---|---|---|
| `extensions` | 47 | `test_attach_existing_child_run.py`, `test_attribution_mcp.py`, `test_branch_authoring_actions.py` |
| `goals` | 9 | `test_api_market.py`, `test_canonical_branch_mcp.py`, `test_goals_run_canonical.py` |
| `wiki` | 7 | `test_wiki_cosign_flow.py`, `test_wiki_file_bug.py`, `test_universe_server_mcp_structured_results.py` |
| `universe` | 6 | `test_api_universe.py`, `test_first_contact.py`, `test_get_recent_events.py` |
| `gates` | 5 | `test_gate_bonuses_mcp.py`, `test_outcome_gate_claims.py`, `test_api_market.py` |
| `community_change_context` | 1 | `test_api_universe.py` |

**Gap 1 (registration):** every one of these tests the *wrapper*, not the *registration*. No
existing test asserts which names are registered with the listing middleware bypassed — precisely
the hole task 3.1 fills. `tests/test_universe_server_five_handles.py:127` is the one test that
**will** need updating at task 4.1, since it asserts the hidden tools remain dispatchable.

**Gap 2 (wire surface, new in v2):** there is **no** test coverage of any class-E/F/G wire caller.
No test exercises `fetchLive`, `fetchVitals`, `fetchPatchLoopFeed`, `snapshot-mcp.mjs`, or the
playground against the MCP surface. A retirement that lands green on `pytest` therefore proves
nothing about the 38 website sites.

---

## 8. Reconciliation of the task 2.1–2.3 checkoffs

| Task | v1 state | v2 state | Why |
|---|---|---|---|
| 2.1 | `[x]` | **`[x]` (retained, qualified)** | Deliverables done and PR state re-verified 2026-07-25. The "resolve **or depend**" half is satisfied by converting §11 from advisory to **binding preconditions on 4.1**. Raw scan output is not retained — noted in §2. |
| 2.2 | `[x]` | **`[x]` (retained)** | Unrefuted by review. #1561 re-queried 2026-07-25 (`1a5d45af`, OPEN draft, 3 files). Static tree evidence for `mcp_server.py` disjointness and `directory_server.py` absence is independently reproducible. |
| 2.3 | `[x]` | **`[ ]` — UNCHECKED** | See below. |

**Why 2.3 is unchecked.** Its contract is to "inventory **every** repository import and direct
caller … and record a preserve-or-explicitly-migrate decision … for each wrapper," and both this
artifact and the v1 checkoff used it as *the repository caller-clearance gate*. Three of the v1
checkoff's assertions do not survive:

1. **"6 wire-caller sites"** — false. The true figure is **42 literal wire sites across 10 files**,
   plus a dynamic dispatcher and a registration-level test. The entire website class (38 sites)
   was missed. v1's six rows also mixed unlike things: four were wire calls, one was panel
   metadata, one was response-label residue that v1 itself described as "not dispatch calls."
2. **"176 Python bindings … all under `tests/`"** — the arithmetic reproduces but the labels do
   not. 176 is a *reference* count (57 imports + 119 references), not a caller count; the real
   call count is 376; the row headed "module-attr call" contains 17 non-call introspection
   references; and "all under `tests/`" omits the non-test `import *` at
   `fantasy_daemon/universe_server.py:2`.
3. **"2 live scripts depend on `wiki action=list`"** — undercount. Five wire sites plus the public
   playground depend on it, and two *further* orphaned actions
   (`community_change_context`, `extensions action=stream_run`) have live website callers that v1
   did not surface at all.

The **Python half** of 2.3 (Finding 2.3-A, the preserve decision, the per-wrapper coverage map) is
complete and every figure reproduces exactly under independent AST enumeration — that work is
retained, not discarded. 2.3 reopens for the **wire half**: it cannot be re-checked until the
class E/F/G/K migration decisions in §11 are recorded.

---

## 9. What this inventory does NOT establish

Stated explicitly so it is not mistaken for a cleared gate. Expanded in v2 — the v1 limits section
was directionally honest but understated its blind spots.

- **Task 1.3 (external caller telemetry) is untouched.** This is repository-internal only. It
  cannot see calls from installed connectors, external clients, or the live production server.
  Zero *internal* callers of a name says nothing about *external* ones.
- **No live probe was run.** All findings are static, against tree `18379010`.
- **Class G cannot be enumerated statically.** `Playground.svelte:95` dispatches a user-typed tool
  name. This inventory can list its *presets*, not its actual traffic.
- **Which website tree is live is unresolved.** The two deploy workflows contradict each other
  (§4c). Deciding which caller set is production-critical is a host call; this inventory treats
  both as live.
- **Installed/generated bundle state is out of scope.** Already-installed Claude-plugin copies and
  any deployed site build predate any migration landed here; the repo cannot see them.
- **Non-code surfaces are inventoried, not adjudicated.** Class K (packaged skills, `bids/README`,
  UI copy) lists what instructs a legacy call; whether each migrates or is retired is a host/
  interface-review call.
- **The 169 orphaned actions are surfaced, not adjudicated.** Deciding which are deliberate
  reductions versus regressions is a host/interface-review call (tasks 1.5 and 5.3).
- **Tasks 2.1–2.2 being checked does not unblock task 4.1.** The §1 gates (1.1–1.5), reopened task
  2.3, and Findings 2.3-B/D/E all remain open.

---

## 10. Review rounds — what each opposite-provider review refuted

### Round 1 → v2. Verdict: **reject**

Review verdict: **reject** (2026-07-25, opposite-provider, read-only static analysis of
`18379010`). Every count in the verdict was independently re-derived here before folding; all
reproduced exactly.

| Verdict finding | Disposition |
|---|---|
| 1 — 176 is a *reference* count, not a caller count; 17 of the "call" bucket are non-call; 376 real calls / 62 files; `import *` breaks "all under tests" | **Accepted, reproduced exactly** (57/26, 119/41, 102, 17, 274, 376/62, union 67). §4a–4b relabelled. |
| 2 — "6 wire callers" refuted; 38 website sites across 8 files missed | **Accepted, reproduced exactly** (per-tool 7/9/5/2/15 = 38). §4c enumerates all with file:line. |
| 3 — `wiki action=list` blocker real but caller scope larger | **Accepted and extended.** §6: 5 wire sites + playground; added the `promoted`/`drafts` vs `results` shape argument and the 100-cap citation. |
| 4 — additional missed classes (packaged skills, plugin boot, `bids/README`, playground copy) | **Accepted, spot-checked, folded** as class K + Finding 2.3-E. Verdict's "mostly clean" reads on `uptime-canary.yml`, `mcp_tool_canary.py`, MCPB manifest and `registry/server.json` were confirmed and are not restated as findings. |
| 5 — 2.1 partially evidenced; 2.2 satisfied; 2.3 must be reopened | **Accepted.** §8: 2.3 unchecked; 2.1 qualified + §11 made binding; 2.2 retained with refreshed PR state. |
| 6 — limits section understates blind spots | **Accepted.** §9 rewritten. |
| 7 — required adaptation (typed census, migrate both trees, resolve `list`, keep 1.3 open) | **Done** in §4a, §4c, §6, §9, §11. |

**Two findings v2 adds beyond the verdict**, both from re-verification rather than the review:

- **Finding 2.3-D** — `extensions action=stream_run` is a *second* orphaned action with live
  website callers on a rendered page path (`/loop`, default `source='mcp'`).
- **Finding 2.3-E** — task 4.4's mirror rebuild breaks four packaged slash commands, three of
  whose actions have no canonical equivalent.

Also corrected in v2: the `callTool('loop', …)` sites are **not** legacy callers (`loop` is not a
registered tool at all) and were correctly excluded from the 38 — logged in §4e so a later reader
does not re-add them.

### Round 2 → v3. Verdict: **adapt**

Second opposite-provider review, of v2 at `9bd88a07` (2026-07-25, read-only static analysis plus
read-only GitHub PR queries). It **confirmed every headline count in v2** — reproducing 38 website
sites / 8 files with the per-tool split 7/9/5/2/15, 42 literal wire sites / 10 files, 57 imports /
26 files, 119 references / 41 files, 176 references, 376 calls / 62 files, 17 non-call references,
and the non-test star import — and confirmed both new blockers are real and the 2.3 uncheck
correct. It required three corrections, each **independently re-derived before folding**:

| # | Required correction | Re-derivation | Disposition |
|---|---|---|---|
| 1 | B2's 17 non-call references occupy **5** files, not 4 | Re-ran the binding-aware AST pass: `test_api_market.py` 2, `test_api_universe.py` 1, `test_goals_discoverability.py` 2, `test_mcp_dispatch_docstring_parity.py` 11, `test_validate_ship_packet_action.py` 1 = **17 / 5 files**. Verdict's file list matches exactly. | **Accepted.** §4a row + inline note. Count 17 stands. |
| 2 | 2.3-D: `stream_run` is **not** docstring-only | Confirmed `_action_stream_run` `api/runs.py:924`, `_RUN_ACTIONS` `:1891`, `api/extensions.py:737`, tests in `test_branch_runner.py` / `test_api_runs.py`. v2's claim was **false**. | **Accepted.** 2.3-D reframed to canonical *unreachability*; the same fix applied to every other "docstring-only" claim (`get_node_output` et al.) and §5's "orphaned" definition tightened. |
| 3 | 2.3-E: `read_premise` not `get_premise`; **four** of five unreachable; breakage at **4.4** not 4.1 | `premise/SKILL.md:13` reads `action="read_premise"`; `get_premise` is a `tinyassets/mcp_server.py` tool, a different server. Unique actions = {`read_premise`, `set_premise`, `inspect`, `get_activity`, `give_direction`}; only `inspect` is canonically reachable ⇒ **4** unreachable. Mirror at `runtime/tinyassets/universe_server.py:1030` still carries its own `_DEPRECATED_TOOL_NAMES`, so the plugin keeps working until 4.4 regenerates it. | **Accepted.** All three corrected; 2.3-E retitled to task 4.4. |
| 4 | Add a typed **authorization/action-scope metadata** class | Confirmed `auth/provider.py:516-618` builds scope rows for `universe`/`wiki`/`extensions`/`gates`/`goals`, and traced all six `require_action_scope` call sites to hardcoded legacy tool names. Five sites are on shared api-layer bodies reached canonically; `gates` is hidden-dispatch-only (corrected in v4). | **Accepted.** New class **L** + **Finding 2.3-F**, plus precondition 10. Does **not** change the 42. |

The round-2 verdict also confirmed the 2.1/2.2 checkoffs may remain checked (fresh PR queries
reproduced #1493/#1467/#1466/#1465 OPEN with matching head SHAs, and #1561 OPEN draft at
`1a5d45af` with exactly 3 files), that 2.3 is correctly unchecked, and that
`openspec validate retire-legacy-live-mcp-tools --strict` passes.

### Round 3 → v4. Verdict: **adapt**

Third opposite-provider review, of v3 at `6d71f3e9` (2026-07-25, focused static analysis plus a
read-only production probe). It **confirmed without change** B2's 17 non-call references across
5 files, Finding 2.3-D's implemented-but-canonically-unreachable framing, and Finding 2.3-E's
`read_premise` / four-of-five / task-4.4 sequencing.

It corrected Finding 2.3-F in two places. First, deletion or mismatch of an action-scope row does
not fail open: `require_action_scope` raises `PermissionError` when `action_scope_for` returns
`None`, reproduced against deployed source SHA `0603aae1`. The residual risk is narrower:
removing only mutating classifications while leaving the actions in the registry defaults them to
`read`, which can fail open in resolve-always mode. Second, the `gates` enforcement site is not
canonically reached; §5's 20-of-20 orphan result was already correct. Both corrections are folded
into §4f, §11, and tasks 2.3/3.5/4.2/4.4. Task 2.3 remains unchecked.

---

## 11. Binding preconditions for the implementation lane

Per task 2.1's "resolve **or depend**" clause these are **dependency edges on task 4.1**, not
recommendations.

1. **Depend on open PRs #1493 (`a4115dd3`), #1467 (`5fb6d16a`), #1466 (`4427d012`), #1465
   (`8eef71b8`)** — all four edit `universe_server.py`.
2. **Rebase #1467 past `60f7f9f1` and drop its `directory_server.py` delta** (Finding 2.2-A).
3. **Resolve the three orphaned actions with live wire callers before unregistration** —
   `wiki action=list` (5 sites, §6), `community_change_context` (2 sites), and
   `extensions action=stream_run` (2 sites, Finding 2.3-D). Each needs either a canonical route or
   explicit host-approved retirement of every dependent.
4. **Migrate both website trees, the snapshot baker, and the playground** — or obtain host sign-off
   that one tree is irreversibly retired. `deploy-site.yml` is the documented rollback path, so
   "React is live" is not sufficient grounds to skip the Svelte tree (§4c).
5. **Migrate the four packaged plugin skills before task 4.4 regenerates the mirror**
   (Finding 2.3-E), and decide `bids/README.md:24`. The deadline is **4.4, not 4.1** — the mirror
   carries its own registrations, so the plugin keeps working through 4.1. Four of the five actions
   they name (`read_premise`, `set_premise`, `get_activity`, `give_direction`) need a canonical
   route or explicit retirement; only `inspect` has one today.
6. **Migrate the 33 straightforward wire sites now**, using the `last_activity_canary.py` pattern
   (Finding 2.3-C). They are safe today and shrink the retirement diff.
7. **Add wire-surface coverage** (§7 Gap 2) — no current test exercises any class E/F/G caller, so
   a green `pytest` proves nothing about the 38 website sites.
8. **Fold the §5 orphan table into the task 1.5 host-approval packet.**
9. **Keep task 1.3's predeclared telemetry window and installed-client migration gate open** —
   repository static analysis cannot substitute for it (§9).
10. **Tasks 4.2 and 4.4 must explicitly preserve or lockstep-migrate the action-scope registry,
    every `require_action_scope` call site, and the packaged mirror** and state which path they took
    (Finding 2.3-F). The registry is keyed by five legacy tool names and therefore *looks* like the
    "legacy-name set … dead registration-only state" 4.2 is told to delete, but it is load-bearing
    authorization and availability state. A missing row fails closed with `PermissionError`; the
    narrower fail-open risk is removing only write/costly/admin classifications while leaving
    mutating actions to default to `read` in resolve-always mode. Gate the migration with a
    regression test proving (a) missing metadata denies and (b) every mutating action is non-read.
