---
status: historical
---

> **HISTORICAL — superseded.** This doc captured architecture intent as of 2026-04-14. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# Phase G Pre-Flight — NodeBid Executor + Paid Market Priority Weights

Planner draft, 2026-04-14. For dev handoff on Phase G rollout row.

Phase G is where the priority function stops being a stub. Phases E–F proved the dispatcher can tier-sort BranchTasks from multiple sources (host queue, goal pool) and actually invoke them. Phase G adds the second execution shape from memo §3.2: the **NodeBid**. A NodeBid is a single-node, cross-universe, bid-priced work unit posted to a repo-root `bids/` directory. Any daemon can pull one, run the specified node in a sandboxed single-node executor, and record an `evidence_url`. The bid price feeds the dispatcher scorer's `bid_term` coefficient — which has existed as a stub since Phase E — making high-value work sort ahead of low-value work within a tier.

This phase ships the structural slot so the market can grow into it. No wallet integration, no crypto. Bids are token-denominated and accounted; the market mechanics work in isolation from any real payment settlement.

## 1. Source material

- `docs/exec-plans/daemon_task_economy_rollout.md` §Phase G (lines 181–207).
- `docs/planning/daemon_task_economy.md` §3.2 NodeBid shape, §4.2 tiered selection, §4.3 priority function.
- `docs/specs/phase_f_preflight.md` §4.1 #2–3 — BranchTaskProducer protocol (landed), GoalPoolProducer (landed). Phase G's NodeBidProducer follows the same pattern.
- Live Phase F code: `tinyassets/producers/branch_task.py` (BranchTaskProducer protocol + registry), `tinyassets/producers/goal_pool.py` (concrete producer + pool YAML write), `tinyassets/subscriptions.py` (subscription durability).
- Reserved stubs already in code:
  - `tinyassets/branch_tasks.py`: `bid: float = 0.0`, `required_llm_type: str = ""`, `evidence_url: str = ""`, `"paid_bid"` in `VALID_TRIGGER_SOURCES`.
  - `tinyassets/dispatcher.py`: `accept_paid_bids: bool = False` in `DispatcherConfig`, `"paid_bid": 50.0` in `_DEFAULT_TIER_WEIGHTS`, `bid_coefficient: float = 0.0`, `bid_term = config.bid_coefficient * float(task.bid)` already in `score_task`, `"stubbed (Phase G)"` in `tier_status_map()`.
- PLAN.md "Private chats, public actions" + §Multiplayer Daemon Platform — security model context.
- STATUS.md Phase F R13 note: double-execution accepted for goal pool v1; Phase G bid atomicity is the resolution target.
- STATUS.md Phase E follow-up #1: `queue_cancel` on running tasks returns rejection with no graph-level interrupt — real cancel semantics land in Phase H. NodeBid executor respects this.
- `docs/specs/outcome_gates_phase6.md` — evidence_url shape; NodeBid completion is a gate claim candidate.

## 2. What exists vs. what Phase G adds

**Already live (after Phase F):**
- `bid: float = 0.0`, `required_llm_type: str = ""`, `evidence_url: str = ""` reserved in `BranchTask` dataclass (`tinyassets/branch_tasks.py:72-89`). Phase G is the first consumer of all three fields.
- `accept_paid_bids: bool = False` in `DispatcherConfig` (`tinyassets/dispatcher.py:52`). Phase G activates it.
- `"paid_bid": 50.0` in `_DEFAULT_TIER_WEIGHTS` — tier weight constant already set.
- `bid_coefficient: float = 0.0` in `DispatcherConfig` — Phase G sets a non-zero default when paid market is on.
- `bid_term = config.bid_coefficient * float(task.bid)` already in `score_task` (`tinyassets/dispatcher.py:137`) — wired but always 0 because `bid_coefficient = 0.0`. Phase G activates by setting a non-zero default when flag on.
- `"paid_bid"` in `VALID_TRIGGER_SOURCES` (`tinyassets/branch_tasks.py:49`).
- `"stubbed (Phase G)"` label in `tier_status_map()` (`tinyassets/dispatcher.py:86-87`). Phase G replaces this with `"live"`.
- `BranchTaskProducer` protocol + registry (`tinyassets/producers/branch_task.py`). Phase G's `NodeBidProducer` is another concrete implementation.
- `_try_dispatcher_pick` + `_finalize_claimed_task` in `fantasy_author/__main__.py` — executor wire-up already exists for BranchTasks. Phase G adds a second execution path in the `_finalize_claimed_task` / `_try_dispatcher_pick` code for NodeBid-originated tasks.

**Phase G adds:**
- `tinyassets/node_bid.py` — `NodeBid` dataclass (separate from `BranchTask`). Repo-root storage shape (`bids/<id>.yaml`). `write_node_bid_post()` + `read_node_bids()` I/O helpers. `validate_node_bid_inputs()` mirrors the pool flat-dict invariant.
- `tinyassets/executors/node_bid.py` — `execute_node_bid(bid: NodeBid, *, universe_path: Path | None, config: dict | None) -> NodeBidResult`. Sandboxed single-node executor: compile the named node, run it with `bid.inputs`, record output to `evidence_url`. Narrower sandbox than `compile_branch` (one node, one LLM call, no graph state).
- `tinyassets/producers/node_bid.py` — `NodeBidProducer(BranchTaskProducer)`. Reads `<repo_root>/bids/*.yaml`, filters to bids matching daemon's `required_llm_type`, converts to BranchTask with `trigger_source="paid_bid"`, `bid=bid.bid`. Registered when `TINYASSETS_PAID_MARKET=on`.
- Dispatcher scorer activation: when `TINYASSETS_PAID_MARKET=on`, set `bid_coefficient` default to `1.0` (configurable); replace `"stubbed (Phase G)"` with `"live"` in `tier_status_map()`.
- MCP action `submit_node_bid(node_def_id, required_llm_type, inputs_json, bid)` on the `universe` tool.
- `bids/` repo-root directory + `bids/README.md` conventions.
- Feature flag `TINYASSETS_PAID_MARKET=off` (default). All Phase G behavior behind this flag.
- `dispatcher_config.yaml` addition: `bid_coefficient` field (Phase E already has the Python default; Phase G sets it non-zero in configs when market is on and documents it).

**Explicitly NOT in Phase G:**
- Real wallet/crypto settlement. Bids are numbers; accounting is advisory only.
- Multi-daemon bid atomicity beyond "first claim wins." Phase F R13 accepted this for pool tasks; Phase G narrows it via the `claimed_by` field write on first executor pick, but true atomic exclusion (no double-execution) is not guaranteed without a distributed lock. The claim write reduces double-execution probability to a small race window.
- Bid moderation / flood prevention. v1 has no rate limit on `submit_node_bid`. Revisit when abuse appears.
- Sybil-proof evidence verification. `evidence_url` is self-reported in v1. Coupling to #56 outcome gates is noted (memo §4.3) but the verification design is Phase 6.x scope.
- Cross-host NodeBid discovery. Phase G is local-first (same git repo). Push/pull for cross-host visibility follows the same `git push` hint pattern as `goal_pool/`.
- Per-node sandbox policy beyond the existing `compile_branch` security model. Phase G borrows that model and narrows it; a new node-specific policy layer is future work.

## 3. Risk map

| # | Risk | Blast radius | Reversible? | Mitigation |
|---|------|-------------|-------------|------------|
| R1 | **Sandbox escape via NodeBid bid-side surface.** A submitter crafts a bid referencing a `node_def_id` whose source contains `exec`/`__import__`/`subprocess`/`pickle`/etc., or smuggles executable strings via `inputs` that the node interpolates into `exec()`. This is **adversarial code execution on any daemon that picks the bid** — host's machine, credentials, other universes all exposed | **Catastrophic — arbitrary code execution.** Worst risk in the codebase. | Yes (flag off) | **Three-layer defense (load-bearing for the whole phase):** (1) Node source must match expanded dangerous-pattern list — `_DANGEROUS_PATTERNS` PLUS `("compile", "open(", "importlib", "pickle", "marshal")`. Any match → `NodeBidProducer` rejects pre-pick. (2) Node MUST resolve in the subscriber's local registry AND have `approved=True` — bid YAMLs do NOT carry inline source; `node_def_id` is a reference to subscriber-already-trusted code. (3) `inputs` must be a flat dict of primitives (`str`/`int`/`float`/`bool`/`None`) — same Phase F invariant 1; rejects nested-dict and callable-smuggling vectors. All three layers checked at producer-side AND executor-side (defense-in-depth). The `_DANGEROUS_PATTERNS` expansion lands in `tinyassets/graph_compiler.py`. Invariant 1 + executor tests adversarial-test each layer. |
| R2 | NodeBid executor crashes mid-run, leaves `evidence_url` empty | single node bid, one LLM call | Yes — task marked `failed`, bid YAML stays on disk for retry | `execute_node_bid` wraps in try/except; `_finalize_claimed_task` marks status; no universe state written on crash |
| R3 | `bid_coefficient` non-zero causes BranchTask ordering to diverge from Phase F behavior | per-daemon queue ordering | Yes — set `bid_coefficient=0.0` in config or `TINYASSETS_PAID_MARKET=off` | Default `bid_coefficient=0.0` when flag OFF; activation only when flag ON; all BranchTasks from Phase E/F have `bid=0.0` so `bid_term=0` unless a real bid was submitted |
| R4 | **Bid-term tier-starvation.** `bid_coefficient * task.bid` with no upper bound — a `bid=10000` paid_bid task scores `50 + 10000 = 10050`, swamping host_request's `tier_weight=100`. Memo §4.3 explicitly forbids this ("Host requests can't be starved by paid goal_pool work"). | **High — violates the memo's load-bearing tier invariant.** Hosts who flip `TINYASSETS_PAID_MARKET=on` lose control of their own daemon's prioritization. | Yes (clamp the term) | **`bid_term_cap` in `DispatcherConfig`** (default `30.0` — half the default tier-weight band-width). `score_task` becomes `bid_term = min(config.bid_coefficient * float(task.bid), config.bid_term_cap)`. Result: within paid_bid tier, higher bids sort first; across tiers, tier_weight always dominates. Cap is configurable per host (some hosts may want larger band-width). Invariant 2 + invariant 3 test adversarial bid magnitudes. |
| R5 | **Claim atomicity (R13 from Phase F now load-bearing).** Two daemons read the same `bids/<id>.yaml` with `status=open`, both stamp `claimed_by` locally, both execute. Trust-first settlement makes this worse: which daemon's evidence_url does the requester honor? | Medium — duplicate execution cost; market trust erosion. | Partial (second daemon wastes cycles; first push wins settlement) | **Git file-rename with push contention** — strongest git-native primitive available without server mediation. Daemon claims by `git pull` then `os.rename(bids/<id>.yaml, bids/<id>.yaml.claimed_by_<daemon_id>)` + `git commit + git push`. First successful push wins; subsequent pushes fail non-fast-forward → daemon reverts local rename + marks bid `failed` with `error="claim_race_lost"`. Local-only installs skip push (single daemon, no race). Multi-host double-execution still possible in the window between local rename and remote push — explicit v1 tradeoff documented in invariant 5 + decision log. Stronger than the bare `claimed_by` field write because git-push gives an atomic-publish boundary; weaker than a true distributed lock (impossible without server mediation). The minimum-viable-correct resolution under PLAN.md's pull-only discipline. |
| R6 | NodeBid inputs contain universe-path references that contaminate cross-universe state | cross-universe contamination | Yes — same flat-dict invariant as pool inputs | `validate_node_bid_inputs()` rejects `_`-prefixed keys, nested dicts, reserved universe-path keys; identical to `validate_pool_task_inputs()` guard |
| R7 | `required_llm_type` mismatch causes daemon to skip valid bids | zero pool bids surfaced | Yes — bids stay on disk | `NodeBidProducer._accessible_bids()` filters on `required_llm_type`; unmatched bids silently skipped (INFO log); no queue pollution |
| R8 | **Sybil — requester floods market with self-controlled bids to inflate apparent demand for their LLM type, gaming priority.** Worse with real currency; without it, distorts market signal. | Medium — market-signal distortion. v1 has no real money so no immediate financial cost; signal-trust is the primary cost. | Partial (signal distortion is an economics problem) | **Outcome-gate coupling stub.** Bids carry optional `goal_id` + `gate_rung_key` fields. On successful completion, executor calls existing `gates claim` with the bid's `evidence_url` as proof. Bids without `goal_id` don't claim gates (no signal). Aggregation (daemon completion rate, repeat-requester rate, gate-claim density) is Phase H dashboard territory. v1 ships only the signal-emission path; downstream filtering uses existing Phase 6 surface. Memo §4.3 + §Q2 reputation stub. Invariant 7 tests the coupling. |
| R9 | `bids/` grows unbounded (no TTL, no cleanup) | disk usage | Yes — `git rm bids/<id>.yaml` | TTL field on bid YAML (`expires_at`, default +7d). Producer skips past-TTL bids. Daemon-startup GC moves terminal+expired bids to `bids_archive/`. Documented in `bids/README.md`. |
| R10 | `node_def_id` doesn't resolve on this daemon (node not in registry) | single bid, one skip | Yes — mark `failed` with `error="node_not_available"` | `execute_node_bid` resolves node from registry; raises `CompilerError` (same pattern as Phase D branch not found); `_finalize_claimed_task` marks failed. Producer-side rejection: bid never enters queue. |
| R11 | `TINYASSETS_PAID_MARKET=on` activates `bid_coefficient=1.0`, silently reorders existing queue | queue ordering change on upgrade | Yes — set `bid_coefficient=0.0` explicitly | Document in release notes; default is OFF; coach operators to audit config before flip. R4 cap also limits the magnitude of the reorder. |
| R12 | **Trust-first settlement creates a silent IOU pile.** Every completed bid is a settlement-event waiting for the future token launch. If the record format changes between Phase G and the token phase, all pre-launch records need migration. | Low-medium — migration risk when token ships. | Yes | Settlement-event records land in repo-root `settlements/<bid_id>__<daemon_id>.yaml`. Schema deliberately versioned: `schema_version: "1"`. Fields: `bid_id`, `daemon_id`, `requester_id`, `bid_amount`, `evidence_url`, `completed_at`, `outcome_status`, `settled: false`. `settled: false` everywhere until token launches. Future migration reads `schema_version` and emits v2 with wallet signatures. `settlements/` directory is public-by-construction (git-tracked) — forms the audit trail. §4.1 #6 + §4.12 Q4. |

### Reversibility summary

Everything is reversible by `TINYASSETS_PAID_MARKET=off`. Flag default is off. With flag off: `NodeBidProducer` is not registered, `submit_node_bid` returns `{"status": "not_available"}`, `bid_coefficient` defaults to `0.0` (no change to queue ordering), `tier_status_map()["paid_bid"]` stays `"stubbed (Phase G)"`. A merged-but-flag-off Phase G is inert.

Secondary safety: `bids/` directory is in the repo but Phase G ships it empty. A host who receives a bad bid can `git rm` the file locally.

## 4. Implementation contract

### 4.1 Deliverables

1. **`tinyassets/node_bid.py` — NodeBid dataclass + I/O helpers.**

   ```python
   @dataclass
   class NodeBid:
       node_bid_id: str           # filename stem; e.g. nb_<timestamp>_<random>
       node_def_id: str           # which node to run; slug form
       required_llm_type: str     # hard filter; "" = any
       inputs: dict               # flat dict of primitives; same invariant as pool
       bid: float                 # token price; no floor
       submitted_by: str          # attribution; set from UNIVERSE_SERVER_USER or "host"
       status: str                # open | claimed:<daemon_id> | succeeded | failed | expired
       evidence_url: str          # populated on completion; "" until then
       submitted_at: str          # ISO timestamp
   ```

   - `write_node_bid_post(repo_root, bid_dict) -> Path` — write `<repo_root>/bids/<node_bid_id>.yaml`.
   - `read_node_bids(repo_root) -> list[NodeBid]` — read all `.yaml` files in `<repo_root>/bids/`, skip malformed (log WARN + continue; don't crash producer cycle).
   - `validate_node_bid_inputs(inputs: dict) -> None` — identical invariant to `validate_pool_task_inputs`: reject nested dicts, `_`-prefixed keys, reserved keys. Raises `ValueError` on violation.
   - `claim_node_bid(repo_root, node_bid_id, daemon_id) -> NodeBid | None` — **git-rename + push contention** (R5 resolution, stronger than the original "advisory claim" design). Sequence:
     1. `git pull --rebase` on the repo (subprocess; no `git_bridge` dependency in v1).
     2. Acquire local file-lock sidecar `bids/<id>.yaml.lock`. Read YAML; assert `status == "open"`. If not, release lock and return None (claimed already locally).
     3. `os.rename(bids/<id>.yaml, bids/<id>.yaml.claimed_by_<daemon_id>)`. Update YAML `status` to `claimed:<daemon_id>`. Release lock.
     4. `git add + commit + push` the rename.
     5. If push fails (non-fast-forward — another daemon won remote race): revert (`git reset --hard origin/<branch>`), return None.
     6. If push succeeds, return the claimed NodeBid (now status=claimed:<daemon_id>).
     - Local-only installs (no remote) skip steps 1, 4-5; just do steps 2-3. Single-daemon, no race.
     - Multi-host double-execution still possible in the window between step 3 and step 4 — explicit v1 tradeoff. Settlement records with `settled: false` accumulate; first-push-wins resolution at token-launch migration.
     - Test coverage in invariant 5.

2. **`tinyassets/executors/node_bid.py` — sandboxed single-node executor.**

   ```python
   @dataclass
   class NodeBidResult:
       node_bid_id: str
       status: str           # "succeeded" | "failed"
       output: dict          # node output dict; empty on failure
       evidence_url: str     # artifact path or URL; "" if not produced
       error: str            # "" on success; exception text on failure
   ```

   - `execute_node_bid(bid: NodeBid, *, node_registry, llm_provider) -> NodeBidResult`
     - **Security gates (R1 three-layer defense, executor side):**
       1. Resolve `bid.node_def_id` from `node_registry`. Raise `CompilerError` if not found OR `approved != True`.
       2. Re-validate the node's source against the **expanded dangerous-pattern list**: `_DANGEROUS_PATTERNS` from `tinyassets/graph_compiler.py:108-110` PLUS `("compile", "open(", "importlib", "pickle", "marshal")`. Any match → raise `CompilerError("dangerous_pattern_in_node_source")`. The expansion lands in `tinyassets/graph_compiler.py` as a new `_BID_DANGEROUS_PATTERNS` constant; `_validate_source_code` continues to use the narrower set for Phase D wrapper nodes (those have a different trust model — domain-trusted-callable). Phase G executor uses the wider set.
       3. Re-validate `bid.inputs` as flat-dict-primitive via `validate_node_bid_inputs`. Producer already filtered; executor enforces defense-in-depth.
     - Resolve `bid.required_llm_type in llm_provider.serves_llm_types`. If not, raise `LLMTypeMismatchError` (should not happen if producer filtered correctly).
     - Compile and invoke the node with `bid.inputs` in a throwaway context (no universe state; no `_run_graph` machinery; no SqliteSaver checkpoint).
     - Wrap node call in `_run_with_timeout(timeout_s=node.timeout_seconds)` — same primitive as `compile_branch` source_code path.
     - Validate output `evidence_url` shape: `urlparse` must yield scheme in `{"http", "https", "file"}` and a non-empty host/path. Invalid → `NodeBidResult(status="failed", error="invalid_evidence_url_shape")`. Phase G does NOT fetch the URL.
     - Write output artifact to `<repo_root>/bid_outputs/<node_bid_id>/`. Populate `evidence_url` with the file path.
     - Catch all exceptions; return `NodeBidResult(status="failed", error=str(e))`.
     - Does NOT write any universe-scoped artifact. Cross-universe by construction — no `universe_id` field touched.
   - **Outcome-gate coupling (R8 sybil stub).** If `bid.goal_id != ""` AND `result.status == "succeeded"`, executor calls the existing `gates` tool's `claim` action with `branch_def_id` = the wrapping daemon's default Branch, `rung_key` = `bid.gate_rung_key` (default `"completion"`), `evidence_url` = `result.evidence_url`. Failed bids do NOT claim gates. Invariant 7.
   - **Settlement record (R12).** After `result` is finalized, executor calls `record_settlement_event(repo_root, bid, result, daemon_id)` from `tinyassets/settlements.py` (new module — §4.1 #5b). One YAML per settlement with `settled: false`. Invariant 8.
   - Security model summary: producer-side rejection + executor-side re-validation + bid-references-registered-approved-node-only (no inline source) + flat-dict inputs + timeout + URL-shape validation. Strictest sandbox in the codebase. Adversarial-test re-audit explicitly requested in §4.8.

3. **`tinyassets/producers/node_bid.py` — `NodeBidProducer(BranchTaskProducer)`.**

   - `produce(universe_path, *, subscribed_goals, config) -> list[BranchTask]`
     - Calls `read_node_bids(repo_root)`.
     - Filters: skip `status != "open"`, skip `required_llm_type` mismatch (INFO log), skip on `validate_node_bid_inputs` failure (WARN log).
     - For each accepted bid: emit a `BranchTask` with `trigger_source="paid_bid"`, `bid=bid.bid`, `branch_def_id="<node_bid>" + bid.node_def_id` (special synthetic slug), `goal_id=""`. The dispatcher picks these like any other BranchTask; `_try_dispatcher_pick` routes synthetic `"<node_bid>"` prefix to the NodeBid executor.
   - `name = "node_bid"`. `origin = "paid_bid"`.
   - Registered at module-import time via `register_branch_task_producer(NodeBidProducer())` when `TINYASSETS_PAID_MARKET=on`.
   - Mtime-based cache on `bids/*.yaml` (mirrors GoalPoolProducer's R3 perf guard).

4. **Dispatcher scorer activation + bid-term cap (in `tinyassets/dispatcher.py`).**

   - Replace `"stubbed (Phase G)"` in `tier_status_map()` with `"live" if self.accept_paid_bids else "disabled"`.
   - `bid_coefficient` default in `DispatcherConfig` stays `0.0`. When `TINYASSETS_PAID_MARKET=on`, `_load_dispatcher_config` sets `bid_coefficient=1.0` if the YAML field is absent. This means flipping the flag alone activates bid-weighted sorting.
   - **Add `bid_term_cap: float = 30.0` to `DispatcherConfig`** (R4 resolution). Half the default tier-weight band-width (50 between adjacent tiers). Configurable per host.
   - **Update `score_task`** from `bid_term = config.bid_coefficient * float(task.bid)` to `bid_term = min(config.bid_coefficient * float(task.bid), config.bid_term_cap)`. This is the only behavioral change to `score_task`.
   - Test coverage: invariants 2 + 3.

5. **`fantasy_author/__main__.py` — NodeBid executor routing in `_try_dispatcher_pick`.**

   - If `claimed.branch_def_id.startswith("<node_bid>")`: extract `node_def_id = claimed.branch_def_id[len("<node_bid>"):]`, look up the original `NodeBid` from `bids/`, call `claim_node_bid` (R5 git-rename + push). If claim returns None (race lost), mark BranchTask `failed` with `error="claim_race_lost"` and continue to default path. If claim succeeds, call `execute_node_bid` with the claimed NodeBid; on result, write evidence_url to YAML (commit), call gate-claim if `goal_id != ""`, write settlement record (§5b).
   - If NOT a NodeBid: existing BranchTask execution path unchanged.
   - `_finalize_claimed_task` unchanged — marks BranchTask queue entry succeeded/failed as before.

5b. **`tinyassets/settlements.py` — pre-token settlement ledger (R12, new module).**

   - `record_settlement_event(repo_root: Path, bid: NodeBid, result: NodeBidResult, daemon_id: str) -> Path` — writes `<repo_root>/settlements/<bid_id>__<daemon_id>.yaml`. YAML schema:
     ```
     schema_version: "1"
     bid_id: <str>
     daemon_id: <str>
     requester_id: <str>          # = bid.submitted_by
     bid_amount: <float>          # = bid.bid
     evidence_url: <str>          # = result.evidence_url ("" on failure)
     completed_at: <iso>
     outcome_status: <succeeded|failed>
     settled: false               # always false in v1
     ```
   - Auto-commits the settlement record (Phase 7 commit-per-mutation pattern).
   - Public-by-construction. Forms the audit trail a future token-settlement phase reads to mint tokens.
   - `settled: false` everywhere until token launches; migration phase reads `schema_version: "1"`, validates, mints, sets `settled: true`.
   - Invariant 8 + §4.11 economic durability section.

5c. **Outcome-gate coupling (R8 sybil stub).** Implementation lives inside `execute_node_bid` (per §4.1 #2 above). The MCP `gates claim` call is invoked synchronously after a successful bid completion when `bid.goal_id != ""`. Failed bids do not claim gates. Adds `gate_rung_key: str = "completion"` field to the `NodeBid` dataclass (default if not specified by submitter). Invariant 7.

5d. **Expanded dangerous-pattern list in `tinyassets/graph_compiler.py`.** New module-level constant `_BID_DANGEROUS_PATTERNS = _DANGEROUS_PATTERNS + ("compile", "open(", "importlib", "pickle", "marshal")`. Used by `execute_node_bid` validation (§4.1 #2 layer 2). The narrower `_DANGEROUS_PATTERNS` continues to gate Phase D's wrapper nodes (different trust model — domain-trusted-callable is host-controlled at registration; bid-referenced nodes are not). Single source of truth so the bid market's stricter posture doesn't drift from the wrapper's. Test (new): assert both constants exist; assert bid-list is strict superset of wrapper-list.

6. **MCP action `submit_node_bid` on `universe` tool.**

   - Params: `node_def_id: str`, `required_llm_type: str = ""`, `inputs_json: str`, `bid: float = 0.0`.
   - Validates `inputs_json` parses to flat dict (calls `validate_node_bid_inputs`).
   - Writes bid YAML to `<repo_root>/bids/<node_bid_id>.yaml` via `write_node_bid_post`.
   - Returns `{"status": "ok", "node_bid_id": id, "next_step": "git add bids/<id>.yaml && git commit && git push"}`.
   - Returns `{"status": "not_available", "hint": "TINYASSETS_PAID_MARKET=on required"}` when flag off.
   - Non-host submitters have `bid` clamped to max of their user tier weight (inherits Phase E §4.3 invariant 9 principle across NodeBid path). In v1, anyone may post; `priority_weight` clamping from Phase F is the precedent.

7. **`bids/` repo-root directory.**

   - `bids/README.md` — post format, YAML shape, `required_llm_type` values, `bid` semantics (token-denominated, advisory), TTL convention (posts older than 30 days with `status=open` may be pruned by any subscriber), cross-host visibility (same git push pattern as `goal_pool/`), R3 double-execution note.
   - Commit an empty `.gitkeep` so the directory tracks in git.

8. **Tests** — see §4.4.

9. **Documentation:** `docs/planning/node_bid_conventions.md` — repo-root `bids/` layout, YAML shape, `node_def_id` slug format, `required_llm_type` values, `bid` semantics, TTL. Parallel to `docs/planning/goal_pool_conventions.md`.

### 4.2 Flags

Two flags active in Phase G (in addition to Phase D/E/F flags):

- `TINYASSETS_PAID_MARKET` (new, Phase G) — default `off`. When `on`:
  - `NodeBidProducer` registers at import time.
  - `bid_coefficient` defaults to `1.0` in `_load_dispatcher_config` if not explicitly set.
  - `accept_paid_bids=True` in DispatcherConfig (if not explicitly overridden in YAML).
  - `tier_status_map()["paid_bid"]` returns `"live"`.
  - `submit_node_bid` MCP action is active.
- `TINYASSETS_GOAL_POOL` (Phase F) — `NodeBidProducer` is independent of this flag. A daemon may have goal pool off and paid market on (runs NodeBids only from `bids/`, not from `goal_pool/`).

Flag combination table (Phase D × E × F × G):

| D | E | F | G | Behavior |
|---|---|---|---|----------|
| off | off | off | off | Legacy direct-graph path (existing behavior) |
| on | on | off | off | Dispatcher picks BranchTasks from host queue only; wrapper execution |
| on | on | on | off | + Goal pool subscription + GoalPoolProducer; bid_term=0 always |
| on | on | on | on | Full market: bid-weighted tier sorting + NodeBid execution path |
| on | on | off | on | NodeBid-only market (no goal pool); valid operational mode |

### 4.3 Invariants (must hold across all flag states)

1. **Sandbox contract — three-layer defense (R1).** NodeBid executor rejects: (a) any bid whose referenced node source contains expanded dangerous patterns (`_DANGEROUS_PATTERNS` plus `compile`, `open(`, `importlib`, `pickle`, `marshal`); (b) any bid whose `node_def_id` does not resolve in the local node registry with `approved=True`; (c) any bid whose `inputs` is not a flat dict of primitives. All three checks at producer-side (pre-pick rejection) AND executor-side (defense-in-depth). Test (new): construct adversarial bids exercising each layer; assert each rejected; assert no daemon execution begins.

2. **Bid term clamped (R4).** `score_task` enforces `bid_term = min(bid_coefficient * task.bid, bid_term_cap)`. Default `bid_term_cap = 30.0`. Test (new): submit bid with `bid=1_000_000`; assert `score_task` output's bid component ≤ 30.

3. **Tier hierarchy preserved across bid magnitudes (R4).** For default weights, max-bid paid_bid task scores `50 + 30 = 80`; host_request scores ≥100. Test (new): enumerate combinations of host_request vs max-bid paid_bid; assert host_request always wins.

4. **Universe isolation preserved.** `execute_node_bid` writes NO universe-scoped artifact. `NodeBid` has no `universe_id` field. Node-side defense: invariant 1's three-layer check catches the most likely contamination vectors (nested-dict input, dangerous-pattern source, unapproved node) before execution. Honor-system for what an approved node does internally is the same posture as Phase D opaque-node wrappers.

5. **Claim atomicity via git-rename + push (R5).** Two daemons attempting to claim the same bid: at most one succeeds in `git push`. Test (new, simulated): create bid YAML in temp git repo; mock two daemons attempting `claim_node_bid` in sequence with simulated push race; assert one returns the NodeBid; assert the other returns None with `error="claim_race_lost"` and reverts its local rename cleanly.

6. **Flat-dict inputs invariant.** NodeBid inputs reject nested dicts, `_`-prefixed keys, reserved keys. Identical to `validate_pool_task_inputs`. Enforced at BOTH `submit_node_bid` (post side) and `NodeBidProducer._accessible_bids()` (producer side).

7. **Outcome-gate coupling (R8 sybil stub).** Successful bid completion with `goal_id != ""` writes a gate claim via the existing `gates` tool, with the bid's `evidence_url` as proof; bids without `goal_id` do NOT claim gates. Failed bids never claim gates regardless of `goal_id`. Test (new): execute bid with `goal_id=""` → assert no gate claim; execute with `goal_id="X"` → assert gate claim exists with matching evidence_url; execute with `goal_id="X"` but node raises → assert no gate claim.

8. **Settlement event emitted (R12).** Every succeeded and failed bid writes a `settlements/<bid_id>__<daemon_id>.yaml` record with `settled: false`, `schema_version: "1"`. Test (new): execute bid; assert settlement record exists with correct shape; assert `settled: false`.

9. **Bid-term activation is flag-gated.** `bid_coefficient = 0.0` when `TINYASSETS_PAID_MARKET=off`. Score function unchanged for all existing tests. Verified by running the full Phase E/F test suite with flag off.

4. **`branch_tasks.json` queue unchanged for non-NodeBid tasks.** NodeBid-originated BranchTask entries use synthetic `branch_def_id` prefix `"<node_bid>"`. Dispatcher routing keyed on this prefix. Existing BranchTasks with real `branch_def_id` values are never mistaken for NodeBids.

5. **`claim_node_bid` is advisory, not exclusive.** Two daemons may claim the same bid in the narrow window between read and write. This is R3 (accepted). The `status` field after claim is `"claimed:<daemon_id>"` — the second claimer overwrites. Phase H claim atomicity resolves this. No data corruption; worst case is wasted execution.

6. **Status transitions are monotonic.** `open → claimed → succeeded | failed | expired`. A bid in `succeeded` or `failed` state is never re-queued. Producer filters on `status == "open"` strictly.

7. **No bid floor.** Zero-bid NodeBids are valid. A daemon with `bid_coefficient=0.0` treats all bids as equal (tier weight only). A daemon with `bid_coefficient=1.0` still picks zero-bid NodeBids if no higher-value work is available (score = tier_weight + 0 + recency + ...). Preserves "leave daemon on → works forever" default.

8. **`required_llm_type = ""` means any LLM accepted.** Blank value is not a rejection filter. `NodeBidProducer` skips only when `bid.required_llm_type != ""` AND doesn't match the daemon's configured LLM type.

9. **Feature flag is import-time, not hot-reloadable.** `register_if_enabled()` runs at `tinyassets/producers/node_bid.py` module load. Mid-run flag flips are silently ignored (same as Phase D `TINYASSETS_UNIFIED_EXECUTION` and Phase F `TINYASSETS_GOAL_POOL` discipline). Document in release notes.

### 4.4 Test strategy

Target: `tests/test_phase_g_node_bid.py`. Minimum 50 tests (security additions raised the floor from 35 — see new clusters below).

**NodeBid dataclass + I/O (8 tests):**
- `test_write_and_read_round_trip` — write a bid YAML, read back, fields match.
- `test_filename_stem_matches_id` — `write_node_bid_post` uses `node_bid_id` as filename stem.
- `test_validate_rejects_nested_dict` — `validate_node_bid_inputs` raises on `{"a": {"b": 1}}`.
- `test_validate_rejects_underscore_key` — rejects `{"_private": "x"}`.
- `test_validate_accepts_flat_primitives` — str/int/float/bool/null all accepted.
- `test_claim_node_bid_first_wins` — two sequential claims; first sets `claimed:<id1>`, second returns False.
- `test_read_skips_malformed_yaml` — malformed `.yaml` in `bids/` is skipped with WARN, remaining bids returned.
- `test_read_empty_bids_dir` — empty `bids/` returns `[]`.

**NodeBidProducer (7 tests):**
- `test_producer_emits_open_bids` — `bids/<id>.yaml` with `status=open` → BranchTask emitted with `trigger_source="paid_bid"`.
- `test_producer_skips_claimed_bid` — `status=claimed:other` → not emitted.
- `test_producer_skips_succeeded_bid` — `status=succeeded` → not emitted.
- `test_producer_filters_llm_type_mismatch` — `required_llm_type="claude-opus"` and daemon configured for `"claude-haiku"` → skipped.
- `test_producer_accepts_blank_llm_type` — `required_llm_type=""` accepted by any daemon.
- `test_producer_skips_invalid_inputs` — malformed flat-dict inputs → skipped (WARN log), other bids still returned.
- `test_producer_not_registered_flag_off` — with `TINYASSETS_PAID_MARKET=off`, `NodeBidProducer` absent from registry.

**Executor (6 tests):**
- `test_execute_success` — node resolves, runs with inputs, result has `status="succeeded"`, `evidence_url` populated.
- `test_execute_unknown_node_fails` — unregistered `node_def_id` → `NodeBidResult(status="failed", error=...)`.
- `test_execute_node_raises_marks_failed` — node body raises → result has `status="failed"`.
- `test_execute_no_universe_state_written` — universe dir untouched after executor runs.
- `test_execute_output_written_to_bid_outputs` — output artifact lands in `<repo_root>/bid_outputs/<id>/`.
- `test_execute_result_evidence_url_empty_on_failure` — failed result has `evidence_url=""`.

**Dispatcher scorer (5 tests):**
- `test_bid_term_zero_when_coefficient_zero` — `bid_coefficient=0.0`, `bid=100.0` → `bid_term=0` in score.
- `test_bid_term_scales_with_coefficient` — `bid_coefficient=1.0`, `bid=10.0` → `bid_term=10.0`.
- `test_paid_bid_tier_weight_above_goal_pool` — `paid_bid` tier weight (50.0) > `goal_pool` tier weight (40.0) in `_DEFAULT_TIER_WEIGHTS`.
- `test_tier_status_map_live_flag_on` — `TINYASSETS_PAID_MARKET=on` → `tier_status_map()["paid_bid"] == "live"`.
- `test_tier_status_map_stubbed_flag_off` — `TINYASSETS_PAID_MARKET=off` → `"stubbed"` or `"disabled"` in status.

**MCP submit_node_bid (5 tests):**
- `test_submit_node_bid_flag_off_returns_not_available` — `TINYASSETS_PAID_MARKET=off` → `status=not_available`.
- `test_submit_node_bid_writes_yaml` — flag on, valid inputs → `bids/<id>.yaml` created.
- `test_submit_node_bid_rejects_nested_inputs` — `inputs_json='{"a": {"b": 1}}'` → rejected.
- `test_submit_node_bid_includes_next_step_hint` — response contains `"git add"` hint string.
- `test_submit_node_bid_records_submitted_by` — `posted_by` field in YAML matches `UNIVERSE_SERVER_USER` or `"host"`.

**Sandbox three-layer defense — adversarial (5 tests, R1 + invariant 1):**
- `test_executor_rejects_exec_in_node_source` — node source containing `exec(` → CompilerError raised pre-execution.
- `test_executor_rejects_pickle_in_node_source` — `pickle.loads` in source → rejected (validates expanded `_BID_DANGEROUS_PATTERNS`).
- `test_executor_rejects_unapproved_node` — `node.approved=False` → CompilerError, no execution.
- `test_executor_rejects_nested_inputs_defense_in_depth` — bid with nested-dict inputs that bypassed producer somehow → executor rejects independently.
- `test_bid_dangerous_patterns_strict_superset_of_wrapper` — `_BID_DANGEROUS_PATTERNS` ⊇ `_DANGEROUS_PATTERNS` (5d invariant).

**Bid term cap (4 tests, R4 + invariants 2 + 3):**
- `test_bid_term_capped_at_default` — `bid=1_000_000`, `bid_coefficient=1.0`, `bid_term_cap=30.0` → score's bid component = 30.
- `test_bid_term_below_cap_uncapped` — `bid=20`, `bid_coefficient=1.0`, cap=30 → bid_term=20.
- `test_max_bid_paid_loses_to_zero_bid_host_request` — paid_bid task with max bid + cap; host_request task with bid=0; assert host_request scores higher.
- `test_bid_term_cap_configurable` — host sets `bid_term_cap=100.0` in YAML; cap takes effect.

**Claim atomicity via git-rename (4 tests, R5 + invariant 5):**
- `test_claim_node_bid_local_only_succeeds` — single-daemon, no remote; rename + commit; returns NodeBid.
- `test_claim_node_bid_simulated_race_one_wins` — two mock daemons; both attempt claim; first git-push succeeds, second fails non-fast-forward + reverts cleanly.
- `test_claim_already_claimed_returns_none` — bid status="claimed:other" already; claim attempt returns None without attempting rename.
- `test_claim_revert_on_push_failure` — push fails; assert local rename reverted (file at original location, status restored to open).

**Outcome-gate coupling (3 tests, R8 + invariant 7):**
- `test_succeeded_bid_with_goal_id_writes_gate_claim` — execute bid with `goal_id="X"`; assert `gates claim` action invoked with bid's evidence_url.
- `test_succeeded_bid_no_goal_id_no_gate_claim` — execute bid with `goal_id=""`; assert no gate claim.
- `test_failed_bid_no_gate_claim_regardless_of_goal_id` — node raises; assert no gate claim even with `goal_id="X"`.

**Settlement records (3 tests, R12 + invariant 8):**
- `test_settlement_emitted_on_succeeded_bid` — settlement YAML exists at `<repo>/settlements/<bid>__<daemon>.yaml` with `outcome_status: succeeded` + `settled: false` + `schema_version: "1"`.
- `test_settlement_emitted_on_failed_bid` — failed bid still writes settlement record with `outcome_status: failed`.
- `test_settlement_schema_version_locked` — every settlement has `schema_version == "1"`; future migrations key off this.

**Flag matrix + invariants (6 tests):**
- `test_flag_g_off_no_bid_producer_in_registry` — `TINYASSETS_PAID_MARKET=off` → `registered_branch_task_producers()` has no `"node_bid"` name.
- `test_flag_g_on_bid_producer_in_registry` — `TINYASSETS_PAID_MARKET=on` → `"node_bid"` present.
- `test_flag_g_off_existing_tasks_score_unchanged` — scoring test with flag off: BranchTasks from Phase E/F have same relative order as before Phase G.
- `test_no_universe_id_in_node_bid` — `NodeBid` dataclass has no `universe_id` field.
- `test_synthetic_branch_def_id_prefix` — BranchTask emitted by NodeBidProducer has `branch_def_id` starting with `"<node_bid>"`.
- `test_double_execution_second_claim_returns_false` — invariant 5: second `claim_node_bid` on same bid returns False (advisory, not exclusive).

**R3 double-execution + R6 TTL convention (3 tests):**
- `test_double_execution_not_blocked` — two simultaneous producer calls both see same open bid (accepted race semantics).
- `test_expired_bid_skipped` — `status=expired` → not emitted by producer.
- `test_read_bids_ignores_gitkeep` — `.gitkeep` in `bids/` does not appear in `read_node_bids()` output.

### 4.5 Rollback plan

1. `TINYASSETS_PAID_MARKET=off` (already the default) — reverts all Phase G behavior with no code change.
2. `git rm bids/*.yaml` if any spam bids land in the pool before the host adds access controls.
3. If `bid_coefficient` non-zero reorders queue unexpectedly: set `bid_coefficient: 0.0` in `<universe>/dispatcher_config.yaml` — restores Phase F ordering without changing any code.
4. If `execute_node_bid` crashes mid-run: BranchTask marked `failed` by `_finalize_claimed_task`; bid YAML remains on disk; daemon continues with next task. No silent swallowing.

### 4.6 Non-goals (explicit)

- **No real payment.** Bids are floats. No crypto, no escrow, no wallet.
- **No anti-double-execution guarantee.** `claim_node_bid` is advisory. True atomicity in Phase H.
- **No evidence verification.** `evidence_url` is self-reported. Sybil-resistance via #56 outcome gates is a future coupling.
- **No cross-host discovery by default.** `bids/` is local. Cross-host requires `git push`.
- **No `queue_cancel` for running NodeBid tasks.** Same Phase E follow-up #1 note: in-flight NodeBids require daemon pause/stop.
- **No per-node sandbox policy.** Executor inherits `compile_branch` trusted-node model; formal sandbox isolation is future work.
- **No TTL enforcement.** `bids/README.md` documents the 30-day convention; host manages pruning manually.

### 4.7 Files touched

| File | Change |
|------|--------|
| `tinyassets/node_bid.py` | NEW — NodeBid dataclass + I/O + claim helper |
| `tinyassets/executors/__init__.py` | probably new dir or existing file — add NodeBidResult import if needed |
| `tinyassets/executors/node_bid.py` | NEW — sandboxed single-node executor |
| `tinyassets/producers/node_bid.py` | NEW — NodeBidProducer + register_if_enabled |
| `tinyassets/dispatcher.py` | Replace `"stubbed (Phase G)"` in `tier_status_map`; set `bid_coefficient` default when flag on |
| `fantasy_author/__main__.py` | `_try_dispatcher_pick`: add NodeBid routing branch on `"<node_bid>"` prefix |
| `tinyassets/universe_server.py` | Add `submit_node_bid` action + dispatch table entry + WRITE_ACTIONS ledger |
| `bids/README.md` | NEW — post format, YAML shape, TTL, R3 note |
| `bids/.gitkeep` | NEW — directory tracking |
| `docs/planning/node_bid_conventions.md` | NEW — public reference |
| `tests/test_phase_g_node_bid.py` | NEW — 50+ tests (security/cap/atomicity additions) |
| `tinyassets/settlements.py` | NEW — settlement-event ledger writer (R12) |
| `tinyassets/graph_compiler.py` | EDIT — add `_BID_DANGEROUS_PATTERNS` constant (5d) |
| `STATUS.md` | Phase G row → claimed; add Phase G concerns post-audit |

**Not touched by Phase G:**
- `tinyassets/branch_tasks.py` — reserved fields already present; no new fields needed.
- `tinyassets/subscriptions.py` — subscriptions are goal-pool specific; NodeBid path is subscription-free.
- `goal_pool/` — unchanged.

### 4.8 Success criteria (for reviewer)

Phase G passes if and only if:

1. All 50+ Phase G tests pass with `TINYASSETS_PAID_MARKET=off`.
2. All 50+ Phase G tests pass with `TINYASSETS_PAID_MARKET=on`.
3. Full suite passes (Phase E + F + G tests combined) with no regressions.
4. `tier_status_map()["paid_bid"]` returns `"live"` when flag on, `"stubbed (Phase G)"` or `"disabled"` when off.
5. `bid_term` is `0.0` for all Phase E/F BranchTasks (which have `bid=0.0`) regardless of `bid_coefficient`.
6. `bid_term` capped at `bid_term_cap` (default 30.0) for any bid magnitude (R4 + invariant 2).
7. Max-bid paid_bid task scores below zero-bid host_request task (invariant 3).
8. **Sandbox three-layer defense demonstrable under adversarial test (R1 + invariant 1):** producer rejection, executor rejection, expanded-pattern coverage all green.
9. **Claim atomicity test passes under simulated git-push race (R5 + invariant 5):** non-fast-forward losers revert cleanly.
10. **Settlement records carry `schema_version: "1"` + `settled: false` (R12 + invariant 8) on every emit.**
11. **Outcome-gate coupling opt-in working (R8 + invariant 7):** `goal_id != ""` claims, `goal_id == ""` does not.
12. `execute_node_bid` on a valid node writes an artifact to `bid_outputs/` and populates `evidence_url`.
13. `execute_node_bid` on an unknown `node_def_id` returns `NodeBidResult(status="failed")` without crashing the daemon.
14. `submit_node_bid` with flag off returns `{"status": "not_available"}`.
15. No universe-scoped artifact written by the NodeBid executor path.
16. `tinyassets/node_bid.py`, `tinyassets/executors/node_bid.py`, `tinyassets/producers/node_bid.py` all have test coverage for the flat-dict inputs invariant.
17. **Economic-layer durability checklist (§4.13)**: flag kill switch verified, all settlements `settled: false`, `bids/` + `settlements/` git-tracked. Reviewer confirms all three at landing.

**Reviewer re-audit specifically requested for:** §4.1 #2 sandbox layers (R1) + §4.1 #1 git-rename claim (R5) + §4.1 #5d expanded dangerous-pattern constant. These are the load-bearing security surfaces.

### 4.9 Decision log

| # | Question | Decision | Rationale |
|---|---------|----------|-----------|
| D1 | Separate `NodeBid` dataclass vs reuse `BranchTask` with extra fields? | Separate `NodeBid` in `tinyassets/node_bid.py` | Memo §3.2 explicitly calls out the structural difference: NodeBid is cross-universe, bid-market, atomic-completion; BranchTask is universe-bound, graph-execution, multi-phase. Merging them hides a real security boundary. |
| D2 | Synthetic `branch_def_id` prefix vs a separate queue for NodeBids? | Synthetic prefix `"<node_bid>"` in BranchTask emitted from producer | Lets the same dispatcher + queue infrastructure handle both shapes with one routing branch in `_try_dispatcher_pick`. Avoids a second queue file and a second lock surface. The prefix is clearly non-slug (angle brackets never appear in real `branch_def_id` slugs). |
| D3 | Async NodeBidProducer? | Sync, v1 | NodeBid I/O is local file reads — milliseconds. Memo §1.1 "async variant for I/O-heavy cross-universe producers" was aimed at HTTP producers. Keep sync; revisit if a producer does HTTP. |
| D4 | `bid_coefficient` default when flag off? | `0.0` (unchanged from Phase E) | Avoids any change to Phase E/F queue ordering when Phase G lands flag-off. Only activates when host explicitly flips `TINYASSETS_PAID_MARKET=on`. |
| D5 | Synthetic prefix format? | `"<node_bid>"` (literal angle-bracket wrapper) | Cannot appear in a real YAML slug (`<` and `>` are not valid in slugs per our naming convention). Unambiguous at a glance. |
| D6 | TTL on `bids/` entries? | Convention only (30-day pruning documented in README) | v1 has no runtime enforcer. Host manages via git. Formal TTL enforcement adds complexity for a problem that won't appear until real market volume lands. |
| D7 | NodeBid executor output location? | `<repo_root>/bid_outputs/<node_bid_id>/` | Repo-root (not universe-scoped). NodeBids are cross-universe; universe scoping would be incorrect. `bid_outputs/` parallel to `goal_pool/` and `bids/` in the repo-root layout convention. |
| D8 | Bid-term cap shape — clamp magnitude vs within-tier-rank? | Clamp at `bid_term_cap=30.0` (R4) | Simplest correct form. "Within-tier rank" (multiplicative) is more elegant but ~10× the code; clamp is the minimum-viable resolution. Cap magnitude pinned at half the tier-weight band-width so bids always sort within-tier but never cross tiers. Memo §4.3 invariant ("host requests can't be starved") becomes provable. |
| D9 | Claim atomicity mechanism — `claimed_by` field stamp vs git file-rename? | Git file-rename + push-contention (R5, upgraded from original draft's advisory stamp) | Original draft's stamp-only design left R13 unresolved at the file level. Git-rename gives an atomic-publish boundary at push time; non-fast-forward push is a clean fail signal. Stronger than bare stamp; weaker than distributed lock (impossible without server mediation). Multi-host double-execution still possible in the local-rename-to-push window — explicit v1 tradeoff with settlement-record audit trail to detect post-hoc. |
| D10 | Settlement records — emit per bid completion vs lazy-aggregation later? | Per-completion, `settled: false` always in v1 (R12) | Pre-token ledger forms the audit trail a future token-mint phase reads. Forward-compat via `schema_version: "1"`. Lazy aggregation would mean reconstructing settlement events from git log + commit messages at token-launch — fragile. Per-completion records are the durable substrate. |
| D11 | Sandbox-pattern strictness — same as `_DANGEROUS_PATTERNS` vs expanded for bids? | Expanded `_BID_DANGEROUS_PATTERNS` (R1 + 5d) | Phase D wrapper nodes are domain-trusted (host-controlled at registration); Phase G bid-referenced nodes are not (anyone can submit a bid referencing any approved node). Different trust model → stricter pattern set. Single source of truth in `tinyassets/graph_compiler.py`; bid-list explicit superset of wrapper-list. Test invariant. |
| D12 | Outcome-gate coupling — opt-in vs always? | Opt-in via bid's `goal_id` field (R8 + invariant 7) | Always-claim would create gate-claim spam for bids unrelated to any tracked Goal. Opt-in respects the bid submitter's intent: if they coupled their bid to a Goal, executor claims; otherwise stays clean. Aggregation (reputation, density) is Phase H. |

### 4.10 Phase H atomicity note (UPDATED for R5 resolution)

Original Phase F R13 concern named Phase G as the resolution target. Phase G achieves **partial resolution** via git-rename + push-contention (D9, R5): cross-host claim-races now have a clean publish boundary; second-pusher reverts cleanly. Multi-host double-execution still possible in the narrow local-rename-to-remote-push window — settlement records preserve the audit trail so post-hoc dispute resolution at token launch can identify the first publish. Update R13 concern text in STATUS.md after Phase G audit clears to reflect: **partially resolved** (atomic-publish at push boundary), full resolution requires server-mediated coordination (Phase I+ if it ever ships).

### 4.11 Outcome gates coupling

NodeBid completion populates `evidence_url`. Memo §4.3 flags this as the outcome-gate sybil-resistance coupling: a paid claim should produce verifiable evidence that the node ran. **Phase G activates the coupling for opt-in cases** (R8): when a bid carries `goal_id != ""`, executor calls `gates claim` synchronously on success. Failed bids do not claim. v1 evidence_url is self-reported and unverified (no fetch); the gate-claim couples the existing Phase 6 evidence-validation surface to bid completion. Aggregation (reputation per daemon, completion rate) is Phase H dashboard territory. Document in `node_bid_conventions.md` + `bids/README.md`.

### 4.13 Economic-layer durability (Phase G specific)

Phase G is the first phase with adversarial economic surface. Three structural safeguards beyond tactical risk mitigations:

1. **Flag-off is the hard circuit breaker.** If any adversarial pattern emerges (sandbox escape per R1, bid-term-cap evasion per R4, claim-race exploitation per R5, sybil flood per R8), `TINYASSETS_PAID_MARKET=off` instantly disables the entire market: producer unregisters, MCP actions return `not_available`, `bid_coefficient` reverts to `0.0`, executor unreachable. Tested in invariant 9 + §4.4 flag-off cluster.

2. **No real money in v1.** `settlements/` records all carry `settled: false`. No token transfers until a future phase ships the token + signed-mint migration. This means a full compromise of the bid market in Phase G has zero financial cost — only reputation/trust cost. The reversibility window for adversarial discoveries is measured in days, not dollars.

3. **Public-by-construction audit trail.** `bids/`, `settlements/`, `claimed_by_<daemon_id>` filenames, git log of all mutations — every economic action recorded in public git history. A bad actor's pattern is forensically recoverable. Trades privacy for accountability at the economic layer (per PLAN.md "Private chats, public actions" applied to bids).

These three — flag kill switch, no-money-yet, public audit — are the economic-layer equivalent of Phase D's rollback plan. Reviewer should confirm all three at landing.

### 4.12 Open design questions (non-blocking, flag for host input)

These do not block Phase G implementation but sharpen future phases:

1. **Token denomination.** Bids are floats. What token? Project-native points? Project token from STATUS.md paid-requests memory `project_paid_requests_model.md`? In v1 it doesn't matter — any float works. Becomes a question when inter-host settlement lands.
2. **Who decides `required_llm_type` values?** In v1, any string is accepted. A shared vocabulary of type strings (`"claude-opus-4-6"`, `"claude-haiku-4-5"`, etc.) becomes important when cross-host matching is needed. Document recommended values in `node_bid_conventions.md`; defer enforcement.
3. **Bid expiry.** 30-day convention is in `bids/README.md`. Should `NodeBidProducer` auto-expire and write `status=expired`? v1 says no (host manages). Revisit at first reports of stale-bid confusion.
4. **`bid_outputs/` retention.** Output artifacts from executed NodeBids accumulate in `bid_outputs/`. Who cleans them? Same TTL question as `bids/`. Defer to a maintenance-pool task (ironic).

## 5. Handoff

Phase G is unblocked. Phase F landed at `1d02903` (2026-04-14). All Phase F tests passing; reserved stubs confirmed in dispatcher + branch_tasks.

**To claim:** Set Phase G row to `claimed:yourname` in STATUS.md Work table. Write `tinyassets/node_bid.py` first (dataclass + I/O), then `tinyassets/executors/node_bid.py` (executor), then `tinyassets/producers/node_bid.py` (producer + registration), then dispatcher activation in `tinyassets/dispatcher.py`, then MCP action in `tinyassets/universe_server.py`, then `_try_dispatcher_pick` routing in `fantasy_author/__main__.py`, then tests.

**Reviewer note:** Run the full Phase E + F + G suite before marking done. Check that `bid_term` is `0.0` for all Phase E/F BranchTasks (bid=0.0 reserved field). Check that `tier_status_map()["paid_bid"]` flips correctly.

**User-sim gate:** User-sim Mission 9 (end-to-end pool test, Phase F flag gate) should land before Phase G live validation. Phase G adds a third flag (`TINYASSETS_PAID_MARKET`) to the three-flag matrix; Mission 10 should test the full D+E+F+G stack with a real submitted NodeBid.
