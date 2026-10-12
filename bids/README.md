# Node Bids (Phase G)

This directory holds **NodeBid posts** — cross-universe, bid-priced
execution requests for individual nodes.

## YAML shape

```yaml
node_bid_id: nb_1712345678901_abcd1234
node_def_id: extract_entities   # must exist in an accessible branches/*.yaml
required_llm_type: ""           # empty = any daemon; otherwise e.g. "claude-opus"
inputs:                         # FLAT dict of primitives only
  text: "some input"
  max_tokens: 500
bid: 1.5                        # float; 0 = no paid priority
submitted_by: alice
status: open                    # open | claimed:<daemon_id> | succeeded | failed | expired
evidence_url: ""                # populated by the executor on completion
submitted_at: "2026-04-14T12:00:00+00:00"
```

## Lifecycle

1. A user posts via MCP: `universe action=submit_node_bid node_def_id=... inputs_json=... bid=...`
   (requires `TINYASSETS_PAID_MARKET=on` on the posting host). A YAML
   lands in `bids/<node_bid_id>.yaml`.
2. Commit + push so cross-host daemons can see it:
   `git add bids/<id>.yaml && git commit && git push`.
3. Any daemon with `TINYASSETS_PAID_MARKET=on` whose NodeBidProducer
   runs during its dispatcher cycle will emit a BranchTask for the
   bid. The dispatcher picks it alongside Branch-based work per the
   scoring function.
4. When a daemon's dispatcher claims the BranchTask, the runner
   routes to the NodeBid executor (not the Branch wrapper stream),
   executes the referenced node with **stripped inputs** (no
   universe state), writes `bid_outputs/<id>/output.json` under the
   universe output dir, and updates the bid's `status` +
   `evidence_url`.
5. A settlement record lands at
   `<repo_root>/settlements/<bid_id>__<daemon_id>.yaml` (fields:
   `bid_id`, `daemon_id`, `bid_amount`, `evidence_url`,
   `completed_at`, `success`, `settled: false`).

## LLM-type filter

Dispatcher-side: if a bid declares `required_llm_type` and the
daemon sets `served_llm_type` in its dispatcher_config.yaml, only
matching tasks are eligible. Empty on either side = no filter.

## Security posture

**Read this before enabling `TINYASSETS_PAID_MARKET=on`.**

### What runs

The NodeBid executor calls Python `exec()` on the `source_code` of
an approved node. The call site is
`tinyassets/executors/node_bid.py:149`:

```python
exec(source, {"__builtins__": __builtins__}, local_scope)
```

**This is Python `exec`, not a sandbox.** The source executes in the
daemon process with full interpreter privileges (filesystem,
network, subprocess via any module already imported into
`__builtins__`). There is no OS-level isolation, no seccomp, no
resource cap, no separate process. The pattern scan below is a
defense-in-depth string filter, NOT a sandbox.

### Trust boundary

The node posting a bid (`node_bid_id`) is **adversarial input** —
any user on any host with write access to the shared `bids/`
directory can submit. The node being executed (`node_def_id`) is
**trusted input** — it must already exist in the daemon's local node
registry and be marked `approved=True` by the host operator.

The whole model rests on that distinction: a bid points at an
already-approved node in the daemon's own registry. Bids cannot
ship new code. Only nodes the host has already accepted can run.

### Three-layer defense

Enforced at both producer side (pre-queue rejection, see
`tinyassets/producers/node_bid.py:_producer_sandbox_reject`) and
executor side (defense-in-depth, see `tinyassets/executors/node_bid.py:execute_node_bid`).
Preflight §4.3 invariant 1 requires BOTH boundaries:

1. **Registration + approval.** `node_def_id` must resolve in the
   local node registry with `approved=True`. Unknown →
   `node_not_found`. Unapproved → `unapproved_node`.
2. **Source-pattern rejection.** The approved node's `source_code`
   is scanned against `_BID_DANGEROUS_PATTERNS` in
   `tinyassets/graph_compiler.py:122` — a **strict superset** of the
   wrapper-node list: `os.system`, `subprocess`, `eval(`, `exec(`,
   `__import__`, `compile(`, `open(`, `importlib`, `pickle`,
   `marshal`. Match → `dangerous_pattern:<pattern>`. Network
   patterns (`urllib`, `requests`, `socket`, `http.client`) are
   intentionally NOT rejected — approved nodes may legitimately
   call LLM APIs.
3. **Inputs flat-dict validation (producer-only).** Producer rejects
   non-flat / non-primitive inputs via `validate_node_bid_inputs`
   before the bid is queued — only str/int/float/bool/None values
   are accepted; nested structures fail at submit time. The executor
   strips producer-internal `__`-prefixed keys (`__node_bid_id`,
   `__node_def_id`) before `run(state)` but does NOT re-validate
   input shapes. If this matters for your threat model, add an
   executor-side recheck.

### Residual risk

- **String filter is bypassable.** Dangerous capability can be
  reached without matching the literal patterns (e.g. `getattr`
  chains, aliasing, attribute access on objects already in scope).
  The filter catches obvious misuse; it is not a security boundary.
  **The approval gate is the real boundary — treat every approval
  as a code review.**
- **`__builtins__` is the full module.** `open`, `__import__`, and
  friends are reachable at runtime even after the source scan
  rejects their literal tokens.
- **No timeout, no memory cap.** v1 has neither. A hostile approved
  node can wedge or OOM the daemon. Approve accordingly.
- **Shared filesystem = shared trust surface.** Any host with write
  to `bids/` can queue work. Approval happens per daemon, but a
  compromised approved node on one host becomes a vector for every
  cooperating daemon.
- **Pick-waste on unapproved bids.** When the producer-side lookup
  is unavailable (e.g. `repo_root_path` fails at daemon start),
  unapproved bids can reach the dispatcher and be picked. The
  executor-side approval gate (`tinyassets/executors/node_bid.py:108`)
  fails them, but the daemon's per-cycle pick contract
  (`fantasy_author/__main__.py:1000`) finalizes and exits rather
  than re-picking. Result: one dispatcher cycle is consumed per
  rejected unapproved bid. The bid's atomic claim still removes it
  from the market, so this is bounded to one cycle per offending
  bid, not a sustained drain. Accept as a known tradeoff of the
  one-pick-per-cycle contract.

### Operator checklist before flipping the flag

- [ ] Every node marked `approved=True` has been code-reviewed by
      a trusted operator — not just "it ran once."
- [ ] `_BID_DANGEROUS_PATTERNS` is understood to be defense-in-depth,
      not a sandbox.
- [ ] Daemon process runs with least-privilege OS-level permissions
      (no uncontrolled write paths, no long-lived secrets on disk
      readable by the daemon user).
- [ ] Host has read `node_bid_conventions.md (archived in git)` for the
      full contract (claim atomicity, settlements, outcome-gate
      coupling).

### See also

- `node_bid_conventions.md (archived in git)` — full Phase G contract
  (commit `20c3dd9`).
- `tinyassets/graph_compiler.py:_BID_DANGEROUS_PATTERNS` — single
  source of truth for the pattern list.
- `tinyassets/producers/node_bid.py:_producer_sandbox_reject` —
  producer-side layers 1+2.
- `tinyassets/executors/node_bid.py:execute_node_bid` — executor-side
  layers 1+2+3.

## Race note

Two daemons on the same `repo_root` race on claim via git-rename
plus push; the loser reverts on push-fail. G.2 (`20e8886`) closed
the local-test race-bypass; Phase H added multi-process stress
coverage in `tests/test_phase_h_claim_stress.py`. Local-only
installs (no configured remote) skip pull/push — single-daemon, no
race.

### Revert-on-push-fail is destructive

**Read this before enabling `TINYASSETS_PAID_MARKET=on` as default.**

When a claim push fails (remote race lost, auth failure, network
hiccup), the claim is rolled back by `_revert_claim` in
`tinyassets/node_bid.py:225`. The revert runs:

```
git reset --hard origin/<branch>
```

against the daemon's `repo_root`, then deletes
`bid_outputs/<node_bid_id>/`.

**What this can destroy.** `git reset --hard` discards every
uncommitted change in the working tree and index — not just the
claim-rename commit that failed to push. If the daemon's
`repo_root` has unrelated uncommitted edits (host-owned WIP, other
tool state, in-flight dev work, another process mid-write), those
edits are gone. There is no stash, no backup, no prompt.

**Why the daemon does this.** The only way to preserve git's
invariant "working tree matches remote HEAD after a failed claim"
is a hard reset. A soft reset would leave the failed-commit's
rename on disk and confuse the next cycle.

**Operational implication.** The daemon's `repo_root` must be a
**dedicated checkout owned by the daemon**, not a shared developer
clone. Running the daemon in your main dev worktree is safe only if
you never have uncommitted work there while the flag is on — which
in practice means: don't.

**Mitigations before flipping the default:**

- [ ] `repo_root` is a dedicated clone used by the daemon only
      (separate from any human working tree).
- [ ] No other process (editor save-on-exit, file sync tool,
      another daemon, another provider's worktree) writes into
      `repo_root/`.
- [ ] Host is comfortable that a rare push-fail can wipe any
      surprise uncommitted state in that tree.

Local-only installs (no remote configured) skip the push step and
therefore never hit this revert path. The risk only applies when a
remote is configured AND claim-push can fail.

## evidence_url

Always a `file://` URL pointing at the output artifact on the
executing host. The settlement layer (future) is responsible for
translating this into an archive URL for cross-host verification.
