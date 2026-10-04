# Graph Execution Substrate

> As-built baseline (2026-07-19, change `spec-out-existing-platform`): describes landed behavior on `main` at baseline time, known limitations included. Future behavior changes arrive as OpenSpec change deltas against this capability.

## Purpose

The domain-agnostic engine that turns a user-authored branch into a runnable, checkpointed, resumable LangGraph execution: BranchDefinition/NodeDefinition model, graph compiler (reducers, conditional edges), runs engine with failure taxonomy and resume.
## Requirements

### Requirement: Guarded run execution preserves ownership through actual scoped use

Start and terminal transitions SHALL be expected-status guarded when a run-keyed OS guard is held, whether supplied by the caller or minted for a family-associated row, regardless of family association.
Ordinary runs without a held guard retain legacy unguarded transitions and startup recovery.
A derivative same-process execution-use
receipt MAY pin that owner's lifetime through code-node and RPC operations, but
SHALL NOT satisfy owner-only mutation or release checks. Owner retirement SHALL
refuse new pins and drain entered scopes before releasing the original OS lock.
Family closure and fresh effect authority SHALL remain separate from lifetime.

#### Scenario: a delayed prepared worker loses its queued state
- **WHEN** cancellation, interruption, completion or another valid transition changed a guarded prepared worker's queued run before start
- **THEN** the expected-status start refuses before invocation, including for a run without a resource-family association
- **AND** the losing worker does not overwrite the winner's status or output

#### Scenario: a real RPC callback outlives the node's drain join
- **WHEN** an entered callback continues after the node returns
- **THEN** the original owner lock remains held until that actual scoped callback exits
- **AND** new effects still require fresh authority and cannot reopen a closed family

#### Scenario: a queued durable admission has no local Future
- **WHEN** its OS guard is temporarily free while an unstarted worker handoff may still be queued elsewhere
- **THEN** legacy recovery does not infer abandonment or execution permission from age, missing process-local Future or guard availability alone

### Requirement: Branch and node definitions are validated dataclasses with lossless JSON round-trip
Community-designed graph topologies SHALL be represented by two `@dataclass` types in `tinyassets.branches` — `NodeDefinition` (one node) and `BranchDefinition` (a full topology of nodes, edges, conditional edges, entry point, and state schema) — each serializable to and from a plain JSON-compatible dict via `to_dict` / `from_dict` (BranchDefinition also exposes `to_json`). A BranchDefinition SHALL store its graph as a single embedded JSON blob so fork, clone, and export stay atomic (one row equals one complete topology). NodeDefinition construction SHALL fail loudly per Hard Rule #8 when a persisted row supplies a non-list value for `input_keys`, `output_keys`, `tools_allowed`, or `effects`, or a non-string element inside one, rather than silently accepting a bare string that would later be iterated character-by-character.

#### Scenario: a node round-trips through its dict form
- **WHEN** a NodeDefinition is serialized with `to_dict` and reconstructed with `from_dict`
- **THEN** the reconstructed node preserves its declared fields (`input_keys`, `output_keys`, `source_code` or `prompt_template`, dependencies, and execution policy)
- **AND** unknown keys in the input dict are ignored rather than raising

#### Scenario: a malformed key list is rejected at construction
- **WHEN** a NodeDefinition is constructed with `input_keys` set to a bare string instead of a list (for example from a pre-fix write path)
- **THEN** construction raises `NodeDefinitionValidationError` naming the offending field
- **AND** the branch fails to load rather than corrupting sandbox and state handling downstream

#### Scenario: an invalid phase is rejected
- **WHEN** a NodeDefinition is constructed with a `phase` outside the valid phase vocabulary
- **THEN** construction raises `ValueError` listing the acceptable phases

### Requirement: Branch validation is the compile gate
`BranchDefinition.validate()` SHALL return the list of structural errors that make a topology unrunnable — missing name, no nodes, missing or dangling entry point, duplicate node IDs, edge or conditional-edge endpoints that are not defined nodes, graph nodes unreachable from the entry point (orphans), cycles with no path to `END`, duplicate or node-colliding state-field names, and undeclared prompt-template placeholders. `compile_branch` SHALL call `validate()` first and SHALL raise `CompilerError` listing those errors instead of producing a `StateGraph` when any are present, so an invalid branch can never compile.

#### Scenario: an orphan node fails validation
- **WHEN** a branch contains a graph node with no path from the entry point (or `START`)
- **THEN** `validate()` returns an error naming the unreachable node
- **AND** `compile_branch` raises `CompilerError` rather than returning a compiled graph

#### Scenario: a cycle with no exit fails validation
- **WHEN** a branch contains a cycle whose nodes cannot reach `END`
- **THEN** `validate()` returns an error naming the nodes in the exitless cycle

#### Scenario: a valid topology compiles
- **WHEN** a branch with a reachable entry point, resolvable edges, and a terminating path is compiled
- **THEN** `validate()` returns an empty list and `compile_branch` returns a `CompiledBranch` carrying the uncompiled `StateGraph` and the synthesized state TypedDict

### Requirement: State fields accumulate per their declared reducer
The compiler SHALL synthesize the run's state type as a `TypedDict` (Hard Rule #5) whose per-field merge behavior follows each `state_schema` field's declared `reducer`: `append` maps to `Annotated[list, operator.add]`, `merge` maps to `Annotated[dict, <shallow merger>]` under a single-writer contract, and any other value (including unset) is last-write-wins overwrite. For every merge-reduced field, compilation SHALL reject a graph with more than one node that declares the field through `output_keys` or `output_mapping`, and node execution SHALL fail closed if a node writes that field without declaring it. With exactly one declared writer, the merger SHALL remain a shallow, right-biased `dict.update`: right-hand top-level keys overwrite matching left-hand keys, left-only keys remain, and nested values are replaced wholesale rather than deep-merged.

#### Scenario: an append field concatenates contributions
- **WHEN** two nodes each write a list to a state field declared `reducer="append"`
- **THEN** the field's value is the concatenation of both contributions via `operator.add`

#### Scenario: an unreduced field is overwritten
- **WHEN** a field has no recognized reducer and two nodes write it
- **THEN** the last write wins and the earlier value is discarded

#### Scenario: multiple declared merge writers fail compilation
- **WHEN** more than one graph node declares the same `reducer="merge"` field in `output_keys` or `output_mapping`
- **THEN** `compile_branch` raises `CompilerError` because a merge-reduced field requires a single writer

#### Scenario: an undeclared merge write fails closed at runtime
- **WHEN** a compiled node returns a value for a `reducer="merge"` field absent from that node's `output_keys` and `output_mapping`
- **THEN** node execution raises `CompilerError` instead of applying the undeclared write

#### Scenario: one declared merge writer shallow-merges right-biased
- **WHEN** the sole declared writer updates a merge-reduced dict containing overlapping top-level keys, left-only keys, and a nested value
- **THEN** the resulting dict preserves left-only keys and uses the writer's values for overlapping keys
- **AND** the writer's nested value replaces the prior nested value wholesale rather than being deep-merged

### Requirement: Conditional edges route by path_map label, not target node id
A conditional-edge router built by the compiler SHALL return a key into LangGraph's `path_map` (a declared condition label), not a target node id — matching the `add_conditional_edges(source, router, path_map=conditions)` contract where LangGraph resolves the target from the returned label. The router SHALL read the source node's first `output_key` from state and return that value verbatim when it is a declared label, and SHALL fall back to the first declared label (or `END` when none) when the output key is absent, empty, or not a valid label, so the graph advances rather than raising `KeyError`. This is the as-built fix for the BUG-019/021/022 routing failure where returning `conditions[value]` (a target) was always looked up as a path_map key and always raised.

#### Scenario: a matching label routes to its branch
- **WHEN** the source node writes an output value equal to a declared condition label
- **THEN** the router returns that label and LangGraph advances to the mapped target node

#### Scenario: a missing or unknown output falls back to the first label
- **WHEN** the source node's output key is absent, non-string, or not among the declared labels
- **THEN** the router returns the first declared label so the graph advances instead of hanging or `KeyError`-ing

### Requirement: source_code nodes execute in an OS-isolated subprocess with data and no credentials

A `source_code` node SHALL execute in a child process launched through the
host's OS sandbox (bubblewrap on Linux: no network, no data directory, no
universe root, no credential mounts, cleared environment, private `/tmp`,
`--die-with-parent`), with address-space, CPU, file-size and descriptor
limits set first thing in the child. The child SHALL receive only the node's
declared `input_keys` (plus schema-defaulted keys) as `state` and the
`{status, body}` of its graph ancestors' authenticated calls as `effects` —
never response headers — and SHALL return a dict, which passes through to
state exactly as an in-process node's return did (the single-merge-writer
guard sees it unfiltered; undeclared keys are named in the node's event).
Calls to `invoke_mcp_action` inside the child SHALL be answered synchronously
by the parent over the sandbox's pipes, through the run's invoker with the
run's authority (at most 32 per run, replies bounded); the child SHALL never
hold the invoker. The in-process `exec` path SHALL NOT exist. A host without the OS
sandbox SHALL fail the run loudly as `sandbox_unavailable`; no environment
variable SHALL select an unsandboxed launcher (test doubles are injected).
Source SHALL still be refused for a disallowed pattern, a size over 50 KB or
a syntax error.

Execution authority SHALL be authorship: a `source_code` node runs only when
the run's `caller_provenance` is `own` (the branch was authored by the actor
the run executes as). A public foreign branch run directly SHALL refuse at
compile with a message naming the remedy (remix into the caller's universe),
classified `node_not_accepted`. `approved` / `approved_source_hash` SHALL be
provenance only and SHALL NOT gate execution.

#### Scenario: an owner-authored code node runs without approval
- **WHEN** a branch authored by the run's actor contains a `source_code` node with `approved=False`
- **THEN** the node executes in the sandbox and its declared outputs land in state

#### Scenario: a foreign branch's code refuses
- **WHEN** a run executes a public branch authored by someone else and it contains a `source_code` node
- **THEN** compilation raises before any node runs, the run fails as `node_not_accepted`, and the message says to remix the branch

#### Scenario: the child sees ancestors' bodies and no headers
- **WHEN** a code node's ancestor fetched a document with a `Set-Cookie` header
- **THEN** `effects[<ancestor>]` carries `status` and the full `body` and no `headers` key

#### Scenario: a print flood cannot exhaust the daemon
- **WHEN** code prints far past the user-print buffer (64 KiB)
- **THEN** the prints are truncated in the child and the node still succeeds with its `stdout_tail`; and
- **WHEN** the child writes past the 8 MiB protocol-stdout cap
- **THEN** the parent kills it at the cap and the node fails with "output too large"

A `source_code` node MAY additionally declare `workspace: "<node id>"`, naming
an ancestor checkout node in the same run. It then runs with that checkout's
generation bound read-write at `/workspace` as the jail's **only** additional
bind, resolved solely through the run's effect chain into an internal
capability that never round-trips through state, `$ta.ref` or JSON; naming a
node that is not an ancestor SHALL fail at compile time, before any node runs.
Where the run's capability carries a held directory descriptor the bind SHALL
be made through it (`--bind /proc/self/fd/<n> /workspace`, with `<n>` passed to
the child), so that a rename of the lease path between admission and mount
cannot change what is mounted; that spelling SHALL be admitted only when `<n>`
is one of the descriptors the child inherits, and a descriptor that is closed
or is no longer a directory SHALL fail the node rather than fall back to the
path. A plain path bind SHALL additionally be required to sit beneath a root
the caller vouched for, both literally and after `realpath`. Every other
property of the jail (no network, cleared environment, no data directory, the
authorship gate, the request's context on RPC) is unchanged.

The runner SHALL expose `ws.run(argv, timeout=, cwd=, env=)`,
`ws.read(relpath, max_bytes=)`, `ws.write(relpath, text)`,
`ws.read_bytes(relpath, max_bytes=)`, `ws.write_bytes(relpath, b64)`,
`ws.glob(pattern)` and `ws.bundle(commit_sha)` (a self-contained, prerequisite-free bundle from
one synthetic ref at that commit, hooks and replacements disabled, created
without credentials inside the jail; its reading of the workspace's own `.git`
is accepted residual parser input because the process stays in the jail and its
output is treated as hostile). Paths SHALL be relative, free of `..`, and
resolved beneath `/workspace` without following a link out of it, with the leaf
opened `O_NOFOLLOW`. `ws.run` SHALL take an argv list and never a shell string,
SHALL build the child's environment from a fixed base plus caller keys matching
`^[A-Z_][A-Z0-9_]*$`, SHALL stream output through bounded incremental drains
into capped tails, and SHALL count commands and returned bytes against per-node
caps of 64 commands and 1 MiB. That fixed base SHALL disable background git
maintenance (`GIT_CONFIG_COUNT=2` with `gc.auto=0` and `maintenance.auto=false`)
and SHALL refuse a caller key beginning `GIT_CONFIG` rather than ignoring it,
because a detached `gc --auto` outlives the node and holds the workspace open;
`ws.bundle` SHALL pass the same two settings on its git command lines.

Every `ws` argument SHALL be validated to the EXACT built-in type before any
method of it is called, so a `str` subclass cannot run node code inside path
validation. The import allowlist SHALL decide by WHOSE code is importing — the
calling frame's globals being the node's namespace — and not by a recursion
depth: a depth counter suspends the check for everything running beneath it,
including user code reached through an overridden method, which is a bypass.
An allowlisted module's own imports and the runner's own lazy ones are therefore
unchecked while node code is checked, and preloading a module does NOT make it
importable by node code, because the allowlist refuses by name.

`ws` paths SHALL be resolved component-wise from a descriptor opened on the
workspace root, each component opened `O_NOFOLLOW` relative to the previous one
and the leaf likewise, so that no name survives to be swapped between a check
and the open it guards; `ws.run(cwd=)` SHALL chdir through the resolved
descriptor and `ws.write` SHALL create parents relative to it. Where the
platform provides no `dir_fd` the same rules SHALL be enforced against resolved
paths, which is the tests-only launcher and never the jail.
`ws.read(max_bytes=)` SHALL CLAMP to the configured cap rather than replace it.

Binary artifacts SHALL have a way out of the workspace: the result channel is
JSON lines and node code cannot call `open()`, so `ws.read_bytes` SHALL return
the file's bytes base64-encoded and `ws.write_bytes` SHALL write the bytes of a
base64 string, both under the same relative-path, no-`..`, `O_NOFOLLOW`
resolution as their text counterparts. Their `max_bytes` and their charge
against the node's cumulative output cap SHALL both be measured in RAW bytes,
never in the encoded length, so the 4/3 expansion neither shrinks what a node
may read nor overcharges what it moved, and reading a file as bytes is not a
way around a cap that reading it as text would hit. `ws.write_bytes` SHALL
decode STRICTLY and refuse a non-base64 payload by name rather than discarding
the characters outside the alphabet, which would write a corrupt file that
looks like a successful one, and SHALL charge the budget before it opens the
destination so a refused write leaves no partial artifact.

On a command timeout the runner SHALL leave without giving the node a chance to
catch it, and the parent SHALL SIGKILL the tracked process and confirm its exit
within a bounded wait, raising rather than continuing if it does not die. For
the jail that tracked process is the bubblewrap supervisor and PID 1 of the
jail's pid namespace, so ending it ends every descendant including a
double-forked `setsid` one; the node fails as `workspace_command_timeout`,
classified from a flag on the result rather than a phrase in a message.

A workspace node SHALL run under the workspace limits profile (`RLIMIT_AS`
1.5 GiB, `RLIMIT_NPROC` 1024, `RLIMIT_NOFILE` 1024, `RLIMIT_FSIZE` 512 MiB,
`RLIMIT_CORE` 0), applied and read back in the child before its message is
parsed, and under an aggregate process-tree RSS cap of 2 GiB. `RLIMIT_NPROC`
SHALL be RAISED toward its cap and never lowered: it is per-UID rather than
per-process, so lowering it bounds every process the host's user already runs,
and on a host whose uid is past the number the next `fork` fails with EAGAIN —
which is not a bound on the node but a broken host. A limit already tighter than
the cap is somebody else's decision and stands, so on a permissive host this
limit binds nothing and the memory watchdog and the jail are what bound a
process explosion. Any git the runtime spawns inside the jail SHALL pass
`pack.threads=1` for the same reason. `RLIMIT_AS` bounds
each process and nothing bounds their sum, so a parent-side watchdog SHALL sample
the tracked process's whole tree and, on passing the cap, kill the tracked
supervisor exactly as the timeout path does; the node then fails as
`code_node_failed` naming the memory cap, there being no memory class in the
failure taxonomy to invent. A tree the watchdog cannot measure SHALL stop the
watchdog, never kill the node.

A node declaring `workspace:` SHALL declare `0 < timeout_seconds <= 1800`,
refused at compile time naming the bound and again when a persisted definition
loads, because such a node holds the universe's job lock and the host-wide slot
for its whole run. The bound is read from the DECLARED value: a zero reads as
"unset" elsewhere, so a node must say how long it may hold the slot.

The capability SHALL be ACQUIRED for the length of one use, not merely read:
the decision that it is still live and the taking of it SHALL happen under one
lock, so an acquisition cannot straddle a `discard`, and what the caller holds
SHALL be duplicated descriptors rather than the registry's own. A parallel
discard closes the originals and the next checkout is handed the same descriptor
numbers back, so a holder still using the originals would be reading another
branch's repository; a duplicate cannot be reused while it is held. Release
SHALL happen in a `finally` under a single owner, and the registry's descriptors
SHALL be closed only when the run ends.

A `discard` between nodes SHALL make the next workspace node fail, naming the
discard. Revoking a capability **inside** a node that is already running is not
built in this change — it needs a parent-to-child signal on the runner's
existing pipe — and is a named residual.

#### Scenario: a discard during acquisition cannot hand out a stale descriptor
- **WHEN** a `discard` runs concurrently with a node acquiring the same workspace, and a later checkout reopens directories onto the same descriptor numbers
- **THEN** the acquisition either takes duplicated descriptors that remain valid for its whole use, or fails naming the discard, and never reads the directory the reused numbers now name

#### Scenario: a str subclass cannot run node code inside path validation
- **WHEN** a node passes `ws.read` a `str` subclass whose `replace` imports a module the allowlist refuses
- **THEN** the argument is refused for its type before any method of it is called, and the override never runs

#### Scenario: the whole process tree is what the memory cap bounds
- **WHEN** a node's commands together hold more than 2 GiB resident, no single process exceeding `RLIMIT_AS`
- **THEN** the watchdog kills the tracked supervisor and the node fails as `code_node_failed` naming the memory cap

#### Scenario: a workspace node cannot hold the host slot indefinitely
- **WHEN** a node declares `workspace:` with `timeout_seconds` of 0 or above 1800
- **THEN** it is refused at compile time naming the bound, and a persisted definition carrying it fails to load

#### Scenario: git maintenance cannot outlive the node
- **WHEN** node code runs `git commit` in the workspace, and separately tries to set `GIT_CONFIG_COUNT` through `ws.run(env=)`
- **THEN** no detached `gc --auto` is started because the jail's fixed environment disables it, and the caller's key is refused rather than ignored

#### Scenario: a code node reads and runs the checked-out project
- **WHEN** a node declares `workspace: "checkout"` and its ancestor `checkout` delivered
- **THEN** `run(state, effects)` sees the repository at `/workspace`, `ws.run([...])` returns an exit code and bounded tails, and no network is reachable

#### Scenario: a workspace reference outside the chain refuses
- **WHEN** a node's `workspace:` names a node that is not an ancestor checkout in this run, or a branch tries to supply a lease id through state
- **THEN** compilation fails before any command runs, naming the rule

#### Scenario: a rename of the lease path cannot change what is mounted
- **WHEN** the capability carries a held directory descriptor and the lease directory is renamed away, with another directory moved into its place, between admission and the mount
- **THEN** the bind resolves through the descriptor to the original directory, while the path now names the substitute

#### Scenario: a descriptor the child does not inherit is refused
- **WHEN** a bind names `/proc/self/fd/<n>` and `<n>` is not among the descriptors passed to the child
- **THEN** the bind is refused before the jail starts, because that path in a process without that descriptor names whatever it does have open there

#### Scenario: a command that outlives its timeout ends the whole sandbox
- **WHEN** `ws.run` runs a command that double-forks a `setsid` sleeper and exceeds the timeout
- **THEN** the runner leaves, the parent SIGKILLs the tracked bwrap supervisor and confirms its exit, the pid namespace ends every descendant with it, and the node fails as `workspace_command_timeout`

#### Scenario: a path that leaves the workspace refuses, and a link out is not a way out
- **WHEN** `ws.read`, `ws.read_bytes`, `ws.write`, `ws.write_bytes`, `ws.glob` or a `ws.run` cwd names `..`, an absolute path, or a symlink pointing outside the workspace
- **THEN** the call raises inside `run()` naming the rule that refused it, and nothing outside the workspace is read or written

#### Scenario: a binary artifact a command produced leaves the workspace intact
- **WHEN** a node produces a file with `ws.run`, reads it with `ws.read_bytes`, and writes it back with `ws.write_bytes`
- **THEN** the sha256 a separate command computes over the produced file equals the sha256 of the decoded bytes and of the rewritten copy, while `ws.read` of the same file would not reproduce them

#### Scenario: the byte doors are bounded and charged in raw bytes
- **WHEN** a node reads a 2048-byte file whose base64 form is 2732 bytes, under a 2500-byte read cap and a 2500-byte cumulative output cap
- **THEN** the first read is admitted because both bounds count RAW bytes, and a second read is refused as a workspace limit naming `read_bytes`

#### Scenario: a payload that is not strict base64 is refused rather than silently truncated
- **WHEN** `ws.write_bytes` is given a string containing characters outside the base64 alphabet
- **THEN** it raises naming strict base64 and creates no file, instead of decoding the surviving characters into a corrupt artifact

### Requirement: Runs are checkpointed LangGraph executions with a fixed terminal status set
The runs engine (`tinyassets.runs`) SHALL execute a compiled branch as a checkpointed LangGraph run using a synchronous `SqliteSaver` (never `AsyncSqliteSaver`, per Hard Rule #1) persisted at `.langgraph_runs.db`, with the LangGraph `thread_id` equal to the `run_id`. A run's lifecycle status SHALL be one of `queued`, `running`, `completed`, `failed`, `cancelled`, `interrupted`, or `resumed`. The graph SHALL be invoked with a recursion ceiling defaulting to `DEFAULT_RECURSION_LIMIT = 100` (raised from LangGraph's stock 25 to accommodate multi-iteration gate loops), overridable per call within validated bounds.

#### Scenario: run state persists to disk under its thread id
- **WHEN** a run executes with a file-backed `SqliteSaver` at `.langgraph_runs.db`
- **THEN** its checkpoint is written keyed by `thread_id == run_id` and survives across checkpointer instances
- **AND** distinct runs (distinct thread ids) do not read each other's checkpoints

#### Scenario: the default recursion limit is 100
- **WHEN** a run is invoked without a recursion override
- **THEN** the applied recursion limit is 100, above LangGraph's stock 25
- **AND** an explicit override outside the accepted min/max range is rejected

### Requirement: Run failures map to a terminal status taxonomy
The executor SHALL translate every terminating condition into a terminal run status with a diagnostic error message rather than leaving a run wedged or crashing the daemon: cancellation (including LangGraph-wrapped cancellation) maps to `cancelled`; a LangGraph interrupt or a child-invocation receipt-timeout maps to `interrupted` (the latter carrying a `child_invocation_receipt_gate` marker so it can be reclaimed); and `GraphRecursionError`, node timeout, empty-LLM-response, and propagated child-run failure each map to `failed` with a reason-specific message. A separate presentation helper (`_classify_failure`) SHALL fold a stored run record into a short failure-class label (for example `cancelled`, `interrupted`, `child_receipt_waiting`, `empty_llm_response`, `timeout`, `provider_exhausted`, `sandbox_unavailable`) for run-history surfaces.

The executor SHALL additionally classify a sandboxed code node's failure as
`code_node_failed` (actionable by the chatbot: the message carries the
child's stderr tail), a refused foreign code node as `node_not_accepted`
(chatbot: remix), and an effect failure raised at node time by its
`external write failed - <node>/<sink>: <error> [<kind>]` message (the
existing `external_write_failed` / `external_write_refused` classes). All
other clauses of this requirement are unchanged.

#### Scenario: an empty LLM response terminates the run as failed
- **WHEN** a node's provider returns an empty response that surfaces as an empty-response error
- **THEN** the run status becomes `failed` with a message identifying the empty response and the responsible node

#### Scenario: exceeding the recursion limit terminates the run as failed
- **WHEN** a run trips the applied recursion limit
- **THEN** the run status becomes `failed` with a `GraphRecursionError` message naming the applied limit and how to raise it

#### Scenario: a cancelled run reports cancelled, not failed
- **WHEN** a run is cancelled between nodes
- **THEN** the run status becomes `cancelled` with a cancellation message, distinct from a crash

#### Scenario: a code node that raises fails the run with its stderr
- **WHEN** `run()` raises inside the sandbox
- **THEN** the run status is `failed`, the class is `code_node_failed`, and the error contains the exception text from the child's stderr

The executor SHALL additionally classify `workspace_checkout_failed`,
`workspace_push_refused`, `workspace_busy`, `workspace_pool_busy`,
`workspace_quota_exceeded`, `workspace_command_timeout`,
`workspace_provision_refused`, `workspace_provision_failed` and
`workspace_discard_failed`, each actionable by the chatbot with a fixed
suggested action. `workspace_provision_failed` is classified but not yet
raised: the resolver whose transport, cache-bound and offline-install failures
would produce it is the named follow-up. All other clauses of this requirement
are unchanged.

#### Scenario: a busy workspace is a wait, not a crash
- **WHEN** a second workspace job starts while the universe's (or the host's) slot is held
- **THEN** it waits up to its timeout and then fails as `workspace_busy` with the advice to retry

#### Scenario: a workspace command timeout is its own class
- **WHEN** a `ws.run` command outlives its budget
- **THEN** the run fails as `workspace_command_timeout`, distinct from a node timeout, classified from a flag on the sandbox result rather than by matching a message

The executor SHALL additionally classify a DELIVERED response that rejects the
credential it presented as `credential_rejected`, actionable by the USER — a third
effect class beside the existing `external_write_failed` / `external_write_refused`
pair, not a refinement of either. What counts as such a response, and what the
agent is told to do about it, belong to "A rejected credential is its own failure
class" and "The action for a rejected credential is the replace card" rather than
being restated here. All other clauses of this requirement are unchanged.

#### Scenario: the taxonomy names the third effect class
- **WHEN** a run's effect was delivered and the far side rejected the credential
- **THEN** its failure class is `credential_rejected`, distinct from both
  `external_write_failed` and `external_write_refused`

### Requirement: A node that went terminal on timeout SHALL NOT launch new work
The shared worker pool SHALL refuse node work that reaches worker entry after its admitted deadline.

A prompt-template node's `timeout_seconds` is measured from the moment its
provider call is submitted to the shared bounded worker pool, so a call can
spend its entire budget queued behind a saturated pool. When the deadline fires,
the executor SHALL cancel work that has not yet begun, and SHALL additionally
refuse, at worker entry, any submitted work whose deadline has already passed —
`Future.cancel()` returns `False` once a worker has picked the item up, so
cancellation alone leaves the outcome to scheduling. The check defines the start of the submitted callable; it does not interrupt
a callable which has already passed that check. Work with
budget remaining at worker entry SHALL start normally.

Scope, stated as the guarantee actually implemented: the worker-entry check
lives in the shared `_run_with_timeout` helper, so it covers every call routed
through the shared pool — today the prompt-template node's policy-router and
provider-bridge paths. `source_code` nodes do not route through that pool; they
carry their own sandbox-runner timeout, and this requirement makes no claim
about their runtime budget propagation. Router-internal retry and admission
budgets are likewise out of scope.

Work that has already begun SHALL be left to run to completion untouched and
SHALL NOT be replayed: its thread is never killed, the deadline check precedes
the first line of the submitted call, and the provider's own subprocess/HTTP
timeout remains the backstop, because an interrupted call leaves an effect that
cannot be classified.

A call that waited in the queue for a material part of its budget and then
started SHALL be given the remaining budget as its provider absolute
cap, so the provider's own deadline expires with the node's rather than the
queue wait beyond it. The remaining budget SHALL NOT be floored at any value
that re-grants material elapsed queue wait; the implementation retains a 1ms
positivity clamp and ignores scheduling delays below50ms, because
`ModelConfig.stream_timeout_profile()` accepts any finite positive float but
discards a non-positive cap in favour of its 600s default. The legacy
integer-seconds `timeout` scalar, which cannot represent a sub-second budget,
SHALL keep the same `max(1, int(...))` representation floor the per-node config
already carries and SHALL NOT be raised above it. The subtraction SHALL produce
a fresh per-invocation config, never a mutation of the per-node one, and SHALL
leave a call that did not queue with the node's full timeout unchanged.

#### Scenario: queued work is cancelled rather than started after the deadline
- **WHEN** a node's call is still waiting in the worker pool queue as its `timeout_seconds` elapses
- **THEN** the node fails as a node timeout and the queued call is cancelled, never executing

#### Scenario: work reaching a worker after the deadline is refused, not started
- **WHEN** a worker picks up a node's queued call after its `timeout_seconds` has already elapsed, cancellation having lost the race
- **THEN** the call is refused at worker entry as a node timeout and the provider is never invoked

#### Scenario: work reaching a worker within its deadline still runs
- **WHEN** a worker picks up a node's queued call while budget remains
- **THEN** the call executes normally, the worker-entry check refusing expired work only

#### Scenario: a queue wait comes out of the provider's cap, not the node's deadline
- **WHEN** a node's call waits in the worker pool queue for a material part of its `timeout_seconds` and then starts
- **THEN** the provider receives the remaining budget as its absolute cap, while a call that did not queue still receives the node's full timeout

#### Scenario: a sub-second node is not handed its queue wait back by a floor
- **WHEN** a node whose `timeout_seconds` is at or below the legacy one-second floor spends a material part of that budget queued
- **THEN** its provider absolute cap is the remaining fraction, strictly less than the node's own timeout and strictly positive

#### Scenario: work already running is left to settle
- **WHEN** a node's call has already started on a worker as its `timeout_seconds` elapses
- **THEN** the node fails as a node timeout while that call runs to completion undisturbed and is never re-dispatched

### Requirement: Interrupted runs resume from checkpoint under owner, status, checkpoint, and admitted-definition guards

`resume_run` SHALL resume a run only from its `SqliteSaver` checkpoint and only when four guards pass: the caller `actor` owns the run (else `auth_failed`), the run is `interrupted` (a run already `resumed` is idempotently returned; any other status raises `not_interrupted`), a checkpoint exists for the run's `thread_id` (else `no_checkpoint`), and the run's own durable **admission envelope** resolves (else `admission_not_reconstructable`). On resume the run SHALL be marked `resumed` before background re-invocation with `None` inputs (LangGraph's resume signal). At server startup `recover_in_flight_runs` SHALL sweep ordinary `queued` or `running` rows without a managed-family association or durable prepared admission to `interrupted` so no run is falsely reported in flight after a restart. As-built limitation: the `recover_in_flight_runs` docstring still states that `interrupted` is terminal and that mid-run resume via checkpoint is "not available today" — that docstring is stale, because `resume_run` implements exactly that checkpoint-based resume.

The fourth guard replaces the retired `branch_version_mismatch` reason. The
resumed definition SHALL come from the run's private, nullable
`runs.admission_envelope_json` column — the frozen `BranchDefinition` plus the
effective `recursion_limit` and `concurrency_budget_override` captured at
admission, in the same transaction that claims the `thread_id` and strictly
before executable submission. The injected `branch_lookup` parameter is retained
for signature compatibility and SHALL NOT be consulted, because it resolves the
CURRENT editable definition. The envelope SHALL NOT appear in `_row_to_run`,
`get_run`, `list_runs`, or any MCP projection; it is read only after the
ownership gate. `runs.branch_version_id` keeps its existing meaning — the user
selected this published version — and SHALL NOT be repurposed as a platform pin.

There SHALL be no legacy reconstruction: a run admitted before the column
existed, or carrying a corrupt, unknown-schema, malformed or row-mismatched
envelope, SHALL refuse `admission_not_reconstructable` before provider admission
and before any effect fires, rather than substituting the current definition or a
guessed execution default. Admission truth SHALL NOT be inferred from a function
signature. A capture failure SHALL terminalize the reserved run as `failed`
instead of dispatching it.

Every path that submits a reserved run to `_invoke_prepared_branch` SHALL
capture that run's admission envelope before dispatch, from the exact frozen
definition and execution choices it dispatches with — including the receiver
delivery worker and the admitted run-input worker, which execute reserved runs
and are therefore admissions, not non-executing intents. The captured effective
concurrency budget SHALL equal `concurrency_budget_override` when one is
recorded and the frozen definition's own `concurrency_budget` otherwise,
verbatim — `0` (tracked, unbounded) is distinct from absent (untracked), and no
ceiling SHALL be imposed beyond what the compiler can execute. A malformed
execution choice SHALL refuse rather than degrade to absent. The admitted
`recursion_limit` SHALL be re-applied as the recursion **cap for the resumed
segment**, not as a remaining-step budget carried over from the interrupted
segment; this is the same per-invocation ceiling semantics `_invoke_graph`
applies, and it replaces the prior behaviour of silently taking LangGraph's
stock default on resume.

A storage fault anywhere in the transaction that claims the `thread_id` SHALL
fail closed as an envelope capture failure naming the reserved run, so the run
settles instead of remaining queued and undispatchable. Settlement SHALL NOT
rewrite a run that is already cancelled or otherwise terminal. As-built
limitation: when the settle path is itself unable to complete — the storage or
the execution-authority guard it needs is concurrently unavailable — the
reserved row MAY remain `queued`; in that case it is still never dispatched, so
the failure stays closed rather than executing an unprovable definition.

#### Scenario: a non-owner cannot resume
- **WHEN** an actor who does not own the run calls `resume_run`
- **THEN** `ResumeError` with reason `auth_failed` is raised and no resume occurs

#### Scenario: only interrupted runs resume
- **WHEN** `resume_run` is called on a run whose status is not `interrupted` and not `resumed`
- **THEN** `ResumeError` with reason `not_interrupted` is raised carrying the current status

#### Scenario: a second resume is idempotent
- **WHEN** `resume_run` is called on a run already marked `resumed`
- **THEN** it returns the same run outcome without launching a second resume

#### Scenario: startup sweeps in-flight runs to interrupted
- **WHEN** `recover_in_flight_runs` runs at startup with ordinary unassociated, non-admitted rows left `queued` or `running` by a crash
- **THEN** those rows are updated to `interrupted` with a restart message and the count is returned
- **AND** a durable prepared admission or managed-family association remains held for its own guarded recovery; this sweep does not authorize replay or claim managed resume support

#### Scenario: resume runs the admitted definition after a draft edit
- **WHEN** a def-based run is admitted, its draft definition is then edited, and the owner calls `resume_run`
- **THEN** the run resumes on the admitted node set, `branch_lookup` is never called, and the admitted `recursion_limit` and `concurrency_budget_override` reach `compile_branch` and `app.invoke`

#### Scenario: a pre-envelope run refuses instead of guessing
- **WHEN** `resume_run` is called on a run with no admission envelope, with or without a `branch_version_id`
- **THEN** `ResumeError` with reason `admission_not_reconstructable` is raised before provider admission and no graph is dispatched

#### Scenario: envelope capture failure never dispatches
- **WHEN** the admission envelope cannot be written for a run already reserved
- **THEN** that run is terminalized `failed` and never submitted to the executor

#### Scenario: a reserved run executed by a worker is resumable
- **WHEN** the receiver delivery worker or the admitted run-input worker dispatches a reserved run
- **THEN** that run's envelope is captured first and a later `resume_run` resolves the definition and execution choices it actually executed with

#### Scenario: a cancelled run is not rewritten by a capture failure
- **WHEN** a reserved run is cancelled and its admission envelope then fails to persist
- **THEN** the run keeps its `cancelled` status and the reported outcome is that status

### Requirement: Child-Branch node shapes are validated before execution

Branch validation SHALL require a live `branch_def_id`, frozen
`branch_version_id`, or await `run_id_field` for its corresponding node shape;
it MUST reject unsupported wait modes, mixed prompt/source bodies,
simultaneous live and frozen invocation specs, and live/frozen mapped parent
output keys absent from a non-empty parent state schema. Frozen-version
validation MUST also reject unsupported failure modes. Await output mappings
currently are not checked against the parent schema. Runtime compilation MUST
enforce the configured child-invocation depth cap.

#### Scenario: Mutually exclusive child definitions fail validation

- **WHEN** one node declares both `invoke_branch_spec` and `invoke_branch_version_spec`
- **THEN** Branch validation reports the node as invalid before execution

#### Scenario: Output mapping respects the parent schema

- **WHEN** a live or frozen invocation maps output to a key absent from a non-empty parent state schema
- **THEN** Branch validation rejects the mapping

#### Scenario: Await output mapping is not schema-validated

- **WHEN** an await spec maps output to a key absent from a non-empty parent state schema
- **THEN** current Branch validation does not report that mapping error

#### Scenario: Nested invocation stops at the runtime depth cap

- **WHEN** compilation reaches the configured child-invocation depth ceiling
- **THEN** the compiler raises an invocation-depth error rather than spawning another child

### Requirement: Live child invocation maps state and supports blocking or async execution

A live child-invocation node SHALL resolve the current Branch definition, map
declared parent keys into child input keys, and run the child as the parent run's
authenticated actor taken from the immutable execution context (see "An
invoke_branch edge never widens execution authority"). A spec-supplied
`child_actor` SHALL have no effect, and there SHALL be no synthetic actor
fallback: an absent authenticated actor refuses the node. Blocking
mode MUST invoke the child synchronously without a child-poll timeout and map
declared child outputs on success. A non-completed terminal child SHALL apply
`propagate`, `default`, or `retry`; node-local `retry_budget=N` permits up to N
retries after the initial attempt, with zero coerced to the default of one.
The thread-local aggregate counter is reset by synchronous child execution and
therefore does not reliably cap live nested retries beyond the local budget.
Live validation does not reject an unknown failure-mode value, which reaches
runtime and follows the propagate path on child failure. Async mode MUST return
immediately, place the child run ID in the first declared parent output key,
and SHALL NOT apply blocking failure policy.

#### Scenario: Blocking live invocation returns mapped child output

- **WHEN** a live child Branch completes in blocking mode
- **THEN** each declared parent output key receives the corresponding child output value

#### Scenario: Async live invocation returns its run identity

- **WHEN** a live child Branch is started in async mode with an output mapping
- **THEN** the first parent output key receives the child run ID and the parent node does not wait for completion

#### Scenario: Live blocking retry is locally bounded

- **WHEN** a live blocking child repeatedly ends non-completed with `on_child_fail=retry`
- **THEN** the node stops after its local retry budget and propagates, while the thread-local aggregate is not a reliable additional bound for synchronous nested runs

### Requirement: Frozen child invocation binds a version and applies blocking failure policy

A frozen child-invocation node SHALL execute the exact stored
`branch_version_id` snapshot with the same input, actor, depth, and output
mapping semantics as live invocation. Frozen blocking SHALL queue the child and
poll it with a 300-second default timeout rather than invoke synchronously; a
poll timeout MUST follow the parent receipt-wait interruption path before any
failure policy is applied. A non-completed terminal child MUST use `propagate`,
`default`, or `retry` behavior. `retry_budget=N` permits up to N retries after
the initial attempt, with zero coerced to the default of one, and each retry
MUST also consume the thread-local per-parent-run aggregate configured by
`TINYASSETS_MAX_CHILD_RETRIES_TOTAL`; this counter is not process-wide. Frozen
async mode SHALL return the child run ID without applying blocking failure
policy.

#### Scenario: Later live edits do not change a frozen child

- **WHEN** a child is invoked by stored version after its live definition changes
- **THEN** execution reconstructs and runs the frozen version snapshot

#### Scenario: Default policy returns declared fallback outputs

- **WHEN** a blocking child ends non-completed with `on_child_fail=default`
- **THEN** the node returns its declared default outputs through the parent mapping instead of failing the parent

#### Scenario: Retry policy is bounded

- **WHEN** a blocking child continues failing under `on_child_fail=retry`
- **THEN** retries stop at the first exhausted node-local or thread-local parent budget and the failure then propagates

#### Scenario: Frozen blocking timeout precedes failure policy

- **WHEN** a frozen blocking child remains non-terminal for the polling timeout
- **THEN** the parent is interrupted into receipt-waiting rather than applying `on_child_fail`

### Requirement: Await nodes map terminal output but make timeout receipt-recoverable

An await node SHALL read a child run ID from its configured parent-state field,
poll until any terminal child status, and map the stored child output without
interpreting the child terminal status. If the timeout expires while the child
is still non-terminal, the parent run MUST become `interrupted` with structured
`receipt_waiting` output naming the child, timeout, and
`attach_existing_child_run` reclaim action.

#### Scenario: Terminal child output is mapped

- **WHEN** an awaited child reaches any terminal status before timeout
- **THEN** the await node maps the declared fields from its stored output and does not apply blocking invocation failure policy

#### Scenario: Non-terminal timeout preserves a reclaim path

- **WHEN** an awaited child remains non-terminal through the timeout
- **THEN** the parent is interrupted with receipt-wait evidence and the completed child may later be attached rather than rerun

### Requirement: Existing-child attachment is validated, one-shot, and evidenced

The run store SHALL attach only an existing completed child with non-empty
output to a parent already in receipt-waiting state. It MUST validate the
expected, supplied, and actual child Branch identities and any supplied output
digest and reject conflicting prior digests. A successful attachment SHALL
store at most one row for the parent-child pair and update the parent out of
receipt-waiting; a sequential replay, including an identical replay, MUST be
rejected as `parent_not_receipt_waiting`. The same unchanged child MAY attach
to a different receipt-waiting parent, but a changed computed digest MUST be
rejected against its prior attachment.

#### Scenario: Wrong or unfinished child cannot be attached

- **WHEN** the supplied child has the wrong Branch identity, is non-completed, or has no output
- **THEN** attachment fails without updating the parent receipt state

#### Scenario: Matching completed child attaches with stable evidence

- **WHEN** a matching completed child with non-empty output is attached to a receipt-waiting parent
- **THEN** the parent records the child output, digest, receipt, and stable evidence handle

#### Scenario: Sequential replay is rejected

- **WHEN** the same parent-child pair is attached again with the same computed digest
- **THEN** attachment is rejected because the successful first call moved the parent out of receipt-waiting

#### Scenario: Unchanged child can evidence another waiting parent

- **WHEN** the same completed child is attached to a different receipt-waiting parent with the same computed digest
- **THEN** a separate parent-child attachment is permitted while a conflicting computed digest is refused

### Requirement: An invoke_branch edge never widens execution authority

Every invoked child branch — live or frozen, blocking or async, at any nesting depth — SHALL execute under an immutable execution context built ONCE at the authenticated run entry and threaded compile → node builder → invoke closure → child run → the child's own builders. The context SHALL carry the actor the run executes as, the execution universe, the running definition's provenance (`own` | `public-foreign`), the recursion depth, the persisted authenticated owner, the compiled definition's author, and the persisted workspace-family member that is the child's budget parent. A nested edge MAY narrow it and SHALL NOT widen it. Execution authority MUST NOT be read from a node spec, from a caller-supplied `child_actor`, or re-read from the mutable run record, and there SHALL be no synthetic or anonymous actor: an absent authenticated actor refuses the node fail-closed. The actor SHALL be resolved through the canonical principal normalizer rather than used as a raw string. A missing workspace-family member SHALL resolve to the unmanaged budget parent, so absent legacy authority does not turn each child into a new budget root. A BLOCKING live invocation is not capped by a compile-time depth limit (the former cap is retired; a blocking invoke past the old cap compiles). ASYNC live and frozen-version invocations SHALL be bounded by the shared invocation pool's capacity rather than by a depth cap.

Provenance SHALL be recomputed at each authenticated run entry from that run's own
definition rather than inherited as a token: `own` when the definition's author is
the run actor, OR when the author is a canonical owner-actor of the execution
universe — the second clause is what makes a universe's engine-authored branches
its own, because a served turn stores the founder's user id as the author while the
run executes as the universe principal. An empty author, or any failure of the
ownership lookup, SHALL resolve to `public-foreign`.

#### Scenario: child runs as the parent's authenticated actor, not a spec actor

- **GIVEN** a child-invocation node whose spec names any `child_actor`
- **WHEN** the node invokes its child
- **THEN** the child executes as the parent run's authenticated actor in the parent run's universe, and the spec value has no effect

#### Scenario: missing authenticated context fails closed

- **WHEN** an invoke edge is reached with no authenticated actor in the execution context
- **THEN** the node is refused, rather than defaulting to an anonymous or run-record-derived actor

#### Scenario: an unresolvable universe owner is foreign, not own

- **WHEN** provenance resolution cannot complete the universe owner-actor lookup for a definition whose author is not the run actor
- **THEN** the run is treated as `public-foreign`

### Requirement: A child branch reference is authorized by delegated, not ambient, authority

An author-chosen child `branch_def_id` / `branch_version_id` SHALL be authorized
against what the AUTHORING definition may reference, never against the running
actor's ambient readability — the run actor is the potential victim, and their own
readability would otherwise authorize a foreign spec's reference to their private
branch. Authorization SHALL read only minimal descriptor metadata (visibility,
author) and SHALL complete BEFORE the full definition is deserialized, so a
malformed private body cannot raise a distinguishable error. Missing, blank, or
malformed visibility SHALL NOT count as public.

- `own` provenance MAY reference a child authored by the actor, or any public
  child. It MAY additionally reference the persisted authenticated OWNER's private
  child only while the owner's authority and the running parent's authority agree:
  the child's author and the compiled definition's author are both that owner, the
  owner is a canonical owner-actor of the execution universe, and the persisted
  parent run row is still `running` with matching actor, owner, and universe.
- `public-foreign` provenance MAY reference ONLY a public child.

A version reference SHALL be authorized through its definition BEFORE the snapshot
is loaded, AND the snapshot's own publication SHALL be checked independently
(`branch_version_is_public`): an unmarked or private snapshot SHALL require
delegated authorship (the authoring definition's author may reference it) even when
its live definition is public. Every other outcome — absent, unreadable, corrupt, unauthorized, or any
failure of the authority lookups — SHALL raise ONE uniform refusal, so the invoke
surface is neither an existence nor an authorization oracle.

> As-built note: the pinned authoring-time `allowed_child_refs` list proposed for
> this behaviour was NOT built. The visibility-and-author resolver above is what
> ships, and it carries the same property for foreign parents.

#### Scenario: a foreign branch cannot invoke the runner's private branch

- **GIVEN** a victim runs a public branch authored by somebody else, whose spec names one of the victim's PRIVATE branches
- **WHEN** the invoke edge is evaluated
- **THEN** it is refused with the uniform message and the private branch is never deserialized or run

#### Scenario: a foreign branch may invoke a public child

- **GIVEN** a foreign parent whose child reference is public
- **WHEN** the invoke edge is evaluated
- **THEN** it is authorized and the child runs under the victim's own actor and universe

#### Scenario: blank visibility is not public

- **WHEN** a child descriptor carries missing, blank, or unrecognized visibility
- **THEN** it is not treated as public and a foreign parent's reference to it is refused

#### Scenario: absent and unauthorized are indistinguishable

- **WHEN** a child reference is absent, unreadable, corrupt, or merely unauthorized
- **THEN** the same uniform refusal is raised in every case

### Requirement: Foreign provenance cannot run authored code, map secrets, or await another actor's run

A run whose provenance is not `own` SHALL NOT execute a `source_code` node at all:
the sandbox bounds what code can touch, but authorship decides whose code may run,
so the node is refused with guidance to remix the branch into the running universe
first. For an invoke edge on a foreign-provenance run, `inputs_mapping` and
`output_mapping` SHALL be refused at compile time if either side of any mapping
pair names a credential, secret, or authorization-state field, classified by the
same key-classes the agent-definition surface uses for redaction; `own`-provenance
edges are unrestricted, because that is an owner plumbing their own fields. A child
run handle read from run state by an await or poll step SHALL be returned only for a
run bound to the same actor and universe as the execution context, and refused for
another actor's or universe's run id.

#### Scenario: a foreign run refuses an authored code node

- **WHEN** a run whose provenance is `public-foreign` reaches a `source_code` node
- **THEN** the node is refused and the message directs the user to remix the branch into their own universe so the code is theirs

#### Scenario: a foreign spec cannot map a parent secret into its child

- **GIVEN** a foreign parent whose `inputs_mapping` or `output_mapping` names a credential, secret, or auth-state key on either side
- **THEN** compilation of that node is refused

#### Scenario: own provenance may map its own sensitive fields

- **GIVEN** an `own`-provenance parent mapping a sensitive-looking key
- **THEN** the mapping is permitted, because the owner is plumbing their own data

#### Scenario: await binds a state-supplied run id to the caller

- **WHEN** an await or poll step resolves a run id read from run state
- **THEN** it returns only for a run matching the execution context's actor and universe, and refuses another actor's or universe's run

### Requirement: A terminal run can seed a distinct same-Branch run with explicit lineage

The `run_branch resume_from` input SHALL trim surrounding whitespace and then
accept a normalized source run ID containing no whitespace only when that run
exists, is owned by the current Branch-run actor, belongs
to the requested Branch definition, and is `completed`, `failed`, `cancelled`,
or `interrupted`. It MUST start a new run rather than resume the source
checkpoint, merge source inputs with explicitly supplied inputs taking
precedence, and record the chosen source as the new run's lineage parent using
the requested Branch's current version.

#### Scenario: Explicit source wins over recency

- **WHEN** `resume_from` names an owned terminal run that is not the latest run of the requested Branch
- **THEN** the system seeds a new run from that exact source and returns and records its ID as the lineage parent

#### Scenario: Explicit inputs override source inputs

- **WHEN** the source run and new request both provide the same input key
- **THEN** the newly supplied value is used while source-only inputs are retained

#### Scenario: Invalid source is classified before execution

- **WHEN** the normalized source ID contains internal whitespace, is missing, is owned by another Branch-run actor, belongs to another Branch, or is non-terminal
- **THEN** `run_branch` returns the corresponding `resume_from_invalid`, `resume_from_not_found`, `resume_from_forbidden`, `resume_from_branch_mismatch`, or `resume_from_invalid_state` failure without starting the seeded run

#### Scenario: Seeded execution is not checkpoint resume

- **WHEN** `run_branch resume_from` accepts a terminal source
- **THEN** it launches a distinct run of the requested current Branch and does not invoke the interrupted-run `resume_run` checkpoint path

### Requirement: Compiled provider nodes preserve the production sandbox error without generic wrapping

The direct provider bridge and policy-routed provider paths SHALL preserve
sandbox-specific failures. In `compile_branch` they SHALL re-raise
`tinyassets.providers.base.SandboxUnavailableError` before their generic
provider-error wrapping. After a provider returns nonempty text, the graph
path SHALL defensively pass that response through the production
`check_bwrap_failure` recognizer before propagating the text into state.
Recognized response text SHALL therefore raise the provider-layer sandbox error;
normal text SHALL proceed unchanged.

This contract covers only the provider-layer exception class and its
platform-gated signature recognizer. The distinct
`tinyassets.sandbox.detect.SandboxUnavailableError` is not interoperable with
this path. Non-sandbox exceptions raised while importing or invoking the
defensive response checker are swallowed, and win32 recognition remains a
no-op.

#### Scenario: A provider-layer sandbox error crosses the graph boundary unchanged

- **WHEN** an injected or policy-routed provider raises `tinyassets.providers.base.SandboxUnavailableError`
- **THEN** the compiled node re-raises that same sandbox-specific exception
- **AND** generic provider-error wrapping does not replace it

#### Scenario: Leaked sandbox text is rejected before state propagation

- **WHEN** a nonempty provider response contains a recognized Bubblewrap failure signature on a checked platform
- **THEN** the response checker raises the provider-layer sandbox error
- **AND** the text is not returned as successful node output

#### Scenario: Normal provider text proceeds

- **WHEN** a nonempty provider response contains no recognized sandbox signature
- **THEN** the graph node returns the normal provider text

### Requirement: Branch sandbox demand is advisory metadata and never an execution gate

`NodeDefinition.requires_sandbox` SHALL default to false, serialize, and
round-trip. For rows admitted by the ordinary branch visibility and scope
rules, branch listing SHALL report `has_sandbox_nodes`. The
`requires_sandbox` filter SHALL be stripped and lowercased; `none` returns only
branches without marked nodes, `any` returns only branches with at least one
marked node, and an empty or any other value applies no sandbox-demand filter.

Branch validation SHALL best-effort read the cached production sandbox status.
When it is falsey and the branch contains marked nodes, validation SHALL add
one non-fatal warning that lists the sorted marked node IDs, the probe reason,
and remediation. It SHALL not warn for an available probe or an unmarked
branch; an exception while obtaining status SHALL suppress this advisory.

The metadata SHALL NOT affect structural validity or `runnable`, and neither
`compile_branch` nor provider selection consumes it as an admission or
execution gate. The current warning's statement that marked nodes will fail at
runtime is advisory wording, not an enforced or universal outcome.

#### Scenario: An unavailable host discloses marked nodes without blocking the branch

- **WHEN** validation sees a falsey cached probe and a branch with multiple `requires_sandbox=true` nodes
- **THEN** it returns one warning containing the sorted marked node IDs, probe reason, and remediation
- **AND** sandbox availability alone does not change `valid` or `runnable`

#### Scenario: Available and unmarked branches have no sandbox warning

- **WHEN** the cached probe is available or the branch contains no marked node
- **THEN** branch validation emits no sandbox-compatibility warning

#### Scenario: A probe exception suppresses only the advisory

- **WHEN** reading cached sandbox status raises during branch validation
- **THEN** validation continues without the sandbox warning
- **AND** its ordinary structural and approval results remain authoritative

#### Scenario: Branch listing filters declared demand after ordinary scope admission

- **WHEN** scope-eligible branches are listed with `requires_sandbox=none` or `requires_sandbox=any`
- **THEN** it returns respectively only unmarked branches or only branches with at least one marked node
- **AND** each returned row reports its `has_sandbox_nodes` value

#### Scenario: Empty and unknown filters preserve all otherwise-admitted rows

- **WHEN** scope-eligible rows are listed with an empty or unrecognized `requires_sandbox` value
- **THEN** every otherwise-admitted row remains
- **AND** each row still reports `has_sandbox_nodes`

#### Scenario: Runtime ignores the advisory flag

- **WHEN** a structurally runnable branch contains a node marked `requires_sandbox=true`
- **THEN** the flag itself neither blocks compilation nor selects a sandbox-capable provider

### Requirement: Run evidence receipts are typed, bounded, and non-authoritative
The run substrate SHALL accept only `source_acquisition_receipt`, `claim_lineage_receipt`, and `revision_receipt` payloads; normalize their type-specific known fields; reject missing required subject identifiers, non-list or non-string list values, non-boolean source flags, and the defined source-state contradictions; and preserve unknown keys and JSON-compatible values. Values supplied directly that are not JSON-compatible MAY be stringified during sizing and persistence and therefore have no byte-for-byte round-trip guarantee. The substrate SHALL enforce the positive cap selected by `TINYASSETS_RECEIPT_PAYLOAD_MAX_BYTES` (default 65,536 bytes) against its compact, sorted UTF-8 JSON size-check encoding. These receipts MUST remain caller-supplied evidence records: unknown keys and `extensions` gain no validation, signature, truth rank, certification, or external-effect authority.

#### Scenario: Source acquisition aliases and flags normalize
- **WHEN** a source receipt supplies its subject through `source_ref`, `source`, `file_ref`, or `corpus_ref`
- **THEN** the first truthy value in precedence order `source_ref`, `source`, `file_ref`, `corpus_ref` is stringified and trimmed; if that selected value trims empty, validation fails without consulting later aliases; otherwise it becomes `source_ref` and `subject_id`, missing timestamps/string fields and six boolean acquisition flags receive their defaults, and every supplied flag must be a JSON boolean

#### Scenario: Contradictory source states are rejected
- **WHEN** `not_searched` is combined with `fetched`, `viewed`, `verified`, `snapshotted`, or `unavailable`, or `unavailable` is combined with an acquired flag
- **THEN** receipt validation fails before persistence

#### Scenario: Claim lineage and revision lists are normalized
- **WHEN** a claim-lineage receipt names a non-empty `claim_id`, or a revision receipt names at least one of `old_run_id` and `old_claim_id`
- **THEN** claim lineage trims `claim_id`, normalizes `evidence_refs`, `imported_prior_run_claims`, `counter_evidence_refs`, and `changed_claims`, and uses `claim_id` as `subject_id`; revision trims `old_run_id` and `old_claim_id`, normalizes `new_evidence_refs`, `affected_outputs`, and `recommended_reruns`, and uses non-empty `old_claim_id` as `subject_id` before falling back to `old_run_id`

#### Scenario: Extensions survive without gaining authority
- **WHEN** a valid receipt includes unknown top-level keys or an `extensions` object
- **THEN** JSON-compatible values round-trip unchanged and receive no schema validity, truth rank, or authority, while a directly supplied non-JSON-compatible value may be stringified

#### Scenario: Compact size-check payload cap is enforced
- **WHEN** the compact sorted UTF-8 JSON encoding used by the size checker exceeds the configured positive byte cap, or the cap is non-integer or non-positive
- **THEN** recording fails with a validation error and no receipt is inserted, without claiming that the separately encoded on-disk `payload_json` blob is bounded to the same byte count

### Requirement: Run receipt persistence and public actions preserve run visibility
The run substrate SHALL append a receipt only for an existing run, generating a receipt ID when none is supplied and always assigning the current creation time, and SHALL list receipts newest-first with optional exact run, receipt-type, and subject filters and a limit clamped from 1 through 1,000. For a run whose actor begins `universe:` and has a non-empty trimmed suffix, the public `record_run_receipt` action MUST derive that universe and apply its current write authorization before insertion, and `list_run_receipts` MUST apply its current read authorization both for one-run queries and to every resolvable row during unscoped enumeration. As-built limitations: a non-universe actor string or `universe:` with an empty suffix currently passes these helpers without a general run-owner ACL check, and because the foreign key is unenforced, a receipt whose run record later disappears passes the current `rec is None or _run_read_allowed(rec)` visibility predicate.

#### Scenario: Missing run cannot receive a receipt
- **WHEN** a caller records an otherwise valid receipt for a run ID absent from the current data-root runs database
- **THEN** insertion fails even though the declared SQLite foreign key is not currently enforced

#### Scenario: Receipt filtering is bounded and newest-first
- **WHEN** receipts are listed with any combination of run ID, valid receipt type, subject ID, and limit
- **THEN** matching rows are returned by descending creation time with the limit defaulting to 100 and clamped to at least 1 and at most 1,000

#### Scenario: Public receipt list normalizes invalid limits
- **WHEN** the public list action receives a missing or falsey limit, or a value that `int()` cannot convert
- **THEN** it uses the default limit of 100 before the storage-layer clamp

#### Scenario: Private universe-bound run write is filtered before recording
- **WHEN** the public record action resolves an existing `universe:<uid>` run whose universe the current caller may not write
- **THEN** it returns the canonical run-write denial and does not insert a receipt

#### Scenario: Enumeration filters private universe-bound receipts
- **WHEN** the public list action is called with or without a run ID
- **THEN** a receipt whose run still resolves to a `universe:<uid>` actor is returned only when the current caller may read that universe, with repeated receipts for one run sharing the per-request visibility result

#### Scenario: Non-universe run actors bypass resource ACL derivation
- **WHEN** a receipt's resolvable run actor does not begin `universe:` or its suffix trims empty
- **THEN** the current receipt access helpers treat the row as allowed without deriving a universe or checking a general run-owner ACL

#### Scenario: Orphan receipt visibility is not fail-closed
- **WHEN** an unenforced or externally altered data-root leaves a receipt whose referenced run row no longer resolves
- **THEN** the current public list predicate treats that orphan receipt as visible rather than failing closed

#### Scenario: Persistence does not claim caller idempotency
- **WHEN** a caller records the same logical payload repeatedly without reusing a colliding explicit receipt ID
- **THEN** the store may append multiple receipts because it provides no caller idempotency or semantic deduplication guarantee

### Requirement: Installation-local teammate mailbox persists send, receive, and acknowledgement
The run substrate SHALL persist teammate messages with a generated message ID, existing non-empty source run, non-empty destination node, JSON-serializable body, optional reply ID, UTC sent time, and exactly one of `request`, `response`, `broadcast`, `plan_approval_request`, `plan_approval_response`, `shutdown_request`, or `shutdown_response`. It SHALL provide non-destructive receive and idempotent acknowledgement actions over the installation's shared `TINYASSETS_DATA_DIR` runs database while retaining the as-built identity, isolation, and graph-wiring limitations below.

#### Scenario: Send validates the stored message envelope
- **WHEN** a caller sends a message with an existing source run, non-empty destination, allowed type, and JSON body
- **THEN** the mailbox stores it unacknowledged and the public action returns `message_id` plus `delivered_at` equal to the stored `sent_at`

#### Scenario: Invalid source, destination, type, or body is rejected
- **WHEN** the source run is absent, the source or destination ID is empty, the message type is outside the seven-value set, or the body is not JSON-serializable
- **THEN** no teammate message is inserted

#### Scenario: Receive filters destination and broadcasts
- **WHEN** a non-empty node receives messages
- **THEN** it sees rows addressed to that node plus `*` broadcasts, optionally filtered by inclusive `since` and supplied message types, ordered from earliest sent time, with a default limit of 50 clamped to 1 through 1,000 rows

#### Scenario: Unconvertible public mailbox limit can escape the handler
- **WHEN** the public receive action receives a limit value that `int()` cannot convert
- **THEN** its eager conversion may raise before the handler's JSON error wrapper rather than returning a normalized error envelope, while integer-convertible values are coerced successfully

#### Scenario: Empty-node receive enumerates the data-root mailbox
- **WHEN** receive is called with an empty node ID
- **THEN** it enumerates otherwise-filtered rows in the shared data-root database rather than applying a recipient predicate

#### Scenario: Addressee or broadcast acknowledgement is idempotent
- **WHEN** the caller-supplied node ID matches the stored destination or the destination is `*`
- **THEN** acknowledgement sets the message's single global `acked` flag to true and repeated acknowledgement remains successful

#### Scenario: Wrong node cannot acknowledge a directed message
- **WHEN** the caller-supplied node ID differs from a directed message's destination
- **THEN** acknowledgement fails without changing the stored flag

#### Scenario: Public acknowledgement validates required identifiers
- **WHEN** the public acknowledgement action receives an empty message ID or node ID, or the message ID does not exist
- **THEN** it returns an error and does not modify a mailbox row

#### Scenario: Current mailbox identity and reference limitations remain visible
- **WHEN** send, receive, or acknowledge is used through the public actions
- **THEN** the handlers perform no run read/write or universe-access check, the store validates no destination-node or reply-message existence, independently authenticates no sender or caller node identity beyond the surrounding tool context, stores no acknowledgement timestamp, and treats one broadcast acknowledgement as global

#### Scenario: Graph message helpers are callable but detached from Branch execution
- **WHEN** the send, receive, or recipient-validation helper is called directly
- **THEN** its focused helper behavior is available
- **AND** current `NodeDefinition` and `BranchDefinition` shapes expose no message-spec field and `compile_branch` never invokes these helpers, so compiled graph execution is not wired

### Requirement: Owner-authored source nodes enqueue paced same-universe BranchTasks under trusted bounded context
When the node-enqueue capability is enabled and an approved `source_code` node declares the enqueue tool, `enqueue_branch_run` SHALL append one epoch-1 `BranchTask` and SHALL NOT start a run synchronously. The task SHALL target the trusted physical queue universe; use forced `trigger_source=owner_queued` and `request_type=branch_run`; copy only object inputs; use server-derived parent/origin lineage and parent depth plus one; and target an existing public branch. Epoch-1 enqueue MUST reject every private target because it carries no request-scoped authenticated actor evidence. Every trusted root run SHALL derive one stable origin shared by all sibling enqueues. One atomic successful-enqueue budget SHALL be shared across every source node in the compiled run. Missing trusted/run context, a foreign universe, mismatched persisted universe metadata, a missing or private target, invalid inputs, depth or run-wide budget exhaustion, or a shared-cap refusal SHALL fail before append or surface the atomic refusal as `CompilerError`.

When the node-enqueue capability is enabled and an owner-authored
`source_code` node calls `invoke_mcp_action('enqueue_branch_run', …)`, the
parent SHALL perform the enqueue with the run's authority and answer the
child with its result; the enqueue SHALL append one epoch-1 `BranchTask` and
SHALL NOT start a run synchronously. All other clauses (trusted context,
target authority, capacity) are unchanged; "approved" in this requirement
reads "owner-authored".

#### Scenario: Enabled enqueue appends but does not execute
- **WHEN** an approved source node enqueues an existing public branch with valid trusted context and remaining capacity
- **THEN** exactly one forced `owner_queued` `branch_run` task is appended to that trusted universe
- **AND** the target run is left for paced daemon dispatch rather than started synchronously

#### Scenario: Trusted context and target authority fail closed
- **WHEN** enqueue lacks trusted universe or run context, names a foreign universe, or targets a missing or private branch
- **THEN** it raises `CompilerError` without appending a task

#### Scenario: Branch-authored routing metadata cannot escalate
- **WHEN** source-authored arguments attempt to control the universe, request type, trigger source, parent, origin, or depth
- **THEN** the trusted server context and forced routing fields remain authoritative and no privileged scheduler class can be selected

#### Scenario: Root siblings share one stable origin
- **WHEN** one trusted root run with no supplied parent or origin enqueues multiple children
- **THEN** every child receives the same server-derived run origin and competes for one lineage budget

#### Scenario: Source nodes share one run-wide enqueue budget
- **WHEN** multiple source nodes in one compiled run attempt more successful enqueues than `TINYASSETS_NODE_ENQUEUE_MAX_PER_RUN`
- **THEN** the run appends exactly the shared budget across all nodes and every excess attempt is refused

#### Scenario: Process identity cannot authorize a private target
- **WHEN** epoch-1 enqueue targets a private branch and the process or context actor string equals its author
- **THEN** enqueue still fails because no durable request-scoped actor authority is present

### Requirement: Shared in-node enqueue growth caps are atomic under concurrent producers
The in-node enqueue append SHALL read required history, count, and append under the same exclusive per-universe cross-process queue lock. The global cap SHALL count live `pending` and `running` rows. The lineage cap SHALL count unique non-empty `branch_task_id` values across live and archived rows carrying the same trusted `origin_branch_task_id`, plus every matching row without an ID conservatively. Concurrent contenders SHALL admit no more than remaining capacity, preserve every admitted row exactly once in readable queue JSON, and reject every excess contender without append. A missing queue or archive SHALL represent empty history, but an existing blank, whitespace-only, unreadable, invalid-JSON, or non-list required history file SHALL fail closed.

#### Scenario: Concurrent distinct-origin writers stop exactly at global capacity
- **WHEN** more distinct-origin producers contend concurrently than the global active queue has remaining capacity
- **THEN** exactly the remaining number are appended and every excess producer receives a cap refusal
- **AND** the queue contains no duplicate, lost, or corrupt admitted row

#### Scenario: Concurrent same-origin writers stop exactly at lineage capacity
- **WHEN** more producers with one trusted origin contend concurrently than that lineage has remaining lifetime capacity
- **THEN** exactly the remaining number are appended and every excess producer receives a cap refusal
- **AND** unrelated origins remain admissible while global capacity remains

#### Scenario: Archived descendants still consume lineage capacity
- **WHEN** terminal descendants of an origin have moved from the live queue to the archive
- **THEN** those archived rows still count toward later lineage admission

#### Scenario: Crash-window overlap counts one identified descendant
- **WHEN** one identified task row exists in both the archive and live queue after an interrupted archive-first collection
- **THEN** lineage admission counts that `branch_task_id` once rather than falsely exhausting two slots

#### Scenario: Corrupt lineage history refuses admission
- **WHEN** a lineage-capped enqueue requires an archive that cannot be read or decoded
- **THEN** admission fails without appending or resetting lineage history

#### Scenario: Blank persisted history refuses admission
- **WHEN** a required live queue or archive exists but contains zero bytes or only whitespace
- **THEN** admission fails without appending or treating that file as empty history

### Requirement: In-node enqueue remains epoch-1 until transactional v2 preserves its guards
The production in-node enqueue primitive SHALL emit only the epoch-1 file-backed task shape. It MUST NOT emit epoch-2 tasks until the transactional v2 path provides a stable server-owned root origin, one atomic run-wide budget, physical tenant/universe binding, atomic global-active and lifetime-lineage count/check/insert, and fail-closed integrity semantics equivalent to this capability.

#### Scenario: Current enqueue emits epoch-1 work
- **WHEN** a valid in-node enqueue is admitted by the current runtime
- **THEN** it writes the existing file-backed `owner_queued` `branch_run` task and does not select the v2 transport

#### Scenario: V2 migration is guard-complete
- **WHEN** a future change routes in-node enqueue through transactional v2 storage
- **THEN** that change must prove every stable-origin, run-budget, scope-binding, shared-cap, and integrity invariant before enabling the route

### Requirement: Immutable snapshots preserve branch execution choices

New immutable branch snapshots SHALL retain non-null branch-level default_llm_policy and concurrency_budget and include them in content identity. Previously stored snapshots MUST remain unchanged; unset fields MUST retain the prior absent-key snapshot form.

#### Scenario: Chosen execution settings survive publication

- **WHEN** an owner freezes a branch with a default model policy and concurrency budget
- **THEN** loading that version preserves both choices and changing either choice produces a distinct content identity

#### Scenario: Legacy snapshots remain immutable

- **WHEN** an old snapshot lacks these settings or a new branch leaves them unset
- **THEN** the old row is not rewritten, no choice is inferred from the current mutable branch, and new unset snapshots preserve the previous absent-key hash form

### Requirement: Branch execution choices are stored and authorable

Branch definitions SHALL persist an optional branch-level default model policy and an optional branch-level concurrency budget. The storage columns MUST be additive and nullable, NULL meaning unset — no value inferred, no existing row rewritten or backfilled. An owner SHALL be able to set, change and clear each choice through the existing authorized branch build and patch surface, with no new top-level MCP tool and no new field name; clearing MUST be indistinguishable from never having set the choice, in the stored row, the read receipt and the immutable snapshot.

#### Scenario: An owner sets a workflow-wide choice and reads it back

- **WHEN** an owner builds or patches a branch with a default model policy and a concurrency budget
- **THEN** both values are stored, returned by a later read of that branch, described on the authoring surface so they are discoverable, and echoed by the receipt that applied them

#### Scenario: Clearing returns the branch to unset

- **WHEN** an owner clears either choice
- **THEN** the branch behaves exactly as a branch that never set it, and its snapshot keeps the absent-key form

#### Scenario: The app agent changes execution choices through the served boundary

- **WHEN** an owner submits `set_default_llm_policy` or `set_concurrency_budget` in an ordinary served `write_graph` branch patch
- **THEN** the served sanitizer admits the existing setters, the canonical transaction validates and persists the choices, and explicit null clears them
- **AND** malformed batches leave the definition unchanged, foreign-owner edits remain refused, and previously saved versions retain their choices
- **AND** tool guidance describes these setters without implying they grant provider access

#### Scenario: A pre-existing branch is unaffected

- **WHEN** a branch definition stored before the choices existed is loaded, read or forked
- **THEN** both choices read as unset, the stored row is not rewritten, and no value is inferred from any other branch or version

### Requirement: Execution-choice validation matches the execution contract

An authored concurrency budget SHALL be accepted only as a positive integer, with booleans refused rather than coerced, matching the contract already enforced on the per-run override; values that the compiler would silently reinterpret or crash on MUST be refused at authoring time with an actionable message. Validation MUST NOT impose a structural ceiling on the budget. An authored default model policy SHALL be validated by the same policy-shape check used for node-level policies, preserving its forward-compatible treatment of unknown keys: an unknown key inside a policy is stored, not refused. Fields the check already knows to be invalid SHALL produce an explicit error rather than a stored value. Beyond that, the requirement is observability, not detection: a receipt that applies an execution choice MUST report the choices actually stored, so an author can read back what is in effect and see when a submitted field produced no stored value. It is NOT required to identify an unrecognized key inside a forward-compatible policy.

This composes with the as-built "Branch validation is the compile gate" (`openspec/specs/graph-execution-substrate/spec.md:51`) and does not replace it: the same `validate()` return path carries these non-topology field errors, so build, patch and compile inherit one check.

#### Scenario: A meaningless budget is refused at authoring time

- **WHEN** an owner submits a concurrency budget that is zero, negative, a boolean, or not an integer
- **THEN** authoring refuses with an explanatory error instead of storing a value the run would silently reinterpret or fail on later

#### Scenario: A fork keeps the parent's choices

- **WHEN** an owner forks a branch whose parent carries either execution choice
- **THEN** the fork inherits both choices unless its own specification overrides them

### Requirement: Direct runs accept immutable Branch version targets
The runner SHALL accept a published `branch_version_id` as a first-class run target, reconstruct the Branch definition from that immutable snapshot, execute it through the shared run executor, and persist the version ID on the run record. It MUST NOT redirect a version-targeted run through the current live `branch_def_id` definition.
The advertised `run_graph` handle SHALL accept `goal_id` as an alternative to `branch_def_id`, route that request through the Goal canonical dispatcher, and forward inputs, run name, and recursion-limit options to the selected immutable version. Supplying both target identifiers SHALL fail loudly as ambiguous.

#### Scenario: Published version executes after live definition changes
- **WHEN** a caller starts a run with a published `branch_version_id` after the corresponding live Branch definition has changed
- **THEN** execution uses the published snapshot content and records that version ID on the run

#### Scenario: Unknown version fails loudly
- **WHEN** a caller starts a version-targeted run with an unknown `branch_version_id`
- **THEN** the runner rejects the request without starting the current live Branch definition

#### Scenario: Advertised handle runs the actor's Goal canonical
- **WHEN** an authenticated actor invokes `run_graph` with a `goal_id` and no `branch_def_id`
- **THEN** the handle routes through `run_canonical` and executes the actor-resolved immutable Branch version

#### Scenario: Advertised handle rejects ambiguous run targets
- **WHEN** a caller supplies both `branch_def_id` and `goal_id` to `run_graph`
- **THEN** the handle rejects the request without dispatching either target

### Requirement: Gate rejection can route typed patch notes to the actor's Goal canonical
The evaluation contract SHALL support a `route_back` rejection decision containing a `goal_id` and typed `PatchNotes`. The route handler MUST derive `scope_actor` from the originating run actor, append the current `(goal_id, scope_actor)` hop to typed route history, resolve that actor's canonical with default and legacy fallback, and synchronously execute the resolved immutable `branch_version_id` with the patch notes as input. The decision MUST fail loudly for malformed notes, missing canonical/artifact, repeated hops, or route depth greater than three and MUST NOT accept caller-selected authority for another actor.

#### Scenario: Rejection routes to the actor's personal canonical
- **WHEN** a gate returns `route_back` with valid patch notes and the originating actor has a personal canonical for the Goal
- **THEN** the handler invokes that immutable version with the patch notes and records the route hop

#### Scenario: Rejection falls back to the Goal default
- **WHEN** a gate returns `route_back` and the originating actor has no personal canonical but the Goal has a default canonical
- **THEN** the handler invokes the immutable default version with the patch notes

#### Scenario: Missing canonical terminates the route
- **WHEN** neither an actor nor default nor legacy canonical can be resolved
- **THEN** the originating run terminates with a structured `no_canonical_bound` error

#### Scenario: Repeated route hop terminates the loop
- **WHEN** typed route history already contains the target `(goal_id, scope_actor)` or adding it would exceed three hops
- **THEN** the originating run terminates with a structured `route_back_loop` error and starts no routed run

### Requirement: Effects fire at node time in graph order

When a node's function returns, the runtime SHALL immediately dispatch the
node's declared `effects` against the state merged with that node's delta
(reducers applied), record the full result on a run-scoped effect chain and
bounded evidence for persistence, and continue to the next node. A node's
effects SHALL fire at most once per run; a revisit (cycle) SHALL fail the
node with kind `effect_already_fired`. A reference to an earlier effect —
`$ta.effect`, a code node's `effects` — SHALL resolve only to the node's
graph ancestors. A packet refused before the wire, a crashed adapter, a dead
sink, or a delivered call answered ≥ 400 whose status the packet did not
declare in `accept_statuses` SHALL fail the node and end the run `failed`;
later nodes SHALL NOT run. Evidence SHALL persist on every terminal status,
and a run that fired a delivered effect before failing SHALL carry
`failed_after_effects` naming those nodes. After an interrupt, a resumed run
SHALL refuse a reference to an effect fired before the interrupt rather than
resolve it from persisted bounded evidence, SHALL NOT refire an effect that
fired before the interrupt, and SHALL continue the interrupted segment's
`invoke_mcp_action` count and invocation depth. Two graph nodes that fan out
from one parent and both write a field with no reducer SHALL be refused at
compile (LangGraph would reject the step after their effects fired).

#### Scenario: a refused write stops the chain
- **WHEN** `write_readme`'s packet is refused (`invalid_body_transform`) and `open_pr` follows it
- **THEN** the run fails at `write_readme` with `external write failed - write_readme/…`, `open_pr` never fires, and the evidence of `create_branch` is persisted with `failed_after_effects: ["create_branch"]`

#### Scenario: a probe's 404 is data when declared
- **WHEN** a GET packet declares `"accept_statuses": [404]` and the far side answers 404
- **THEN** the node succeeds and a later code node reads `effects["probe"]["status"] == 404`

#### Scenario: a cycle cannot refire an effect
- **WHEN** a conditional edge routes back to a node whose effects already fired in this run
- **THEN** the second visit fails the node with kind `effect_already_fired`

### Requirement: Workspace jobs hold one durable lock per universe and one host-wide slot

The runtime SHALL acquire, in the checkout's admission transaction, a durable job lock keyed by universe and a host-wide slot (one slot in this change), SHALL treat it as reentrant for that run's later workspace nodes and its push, and SHALL release it only through the run's terminal outbox entry.
`workspace_busy` is the refusal when the lock cannot be acquired within the
node's timeout. The runner sidecar / cgroup follow-up is what lifts the
host-wide slot.

#### Scenario: the lock outlives the checkout node
- **WHEN** a run checks out, runs tests in a later node, and pushes in a third
- **THEN** one lock is held from the checkout's admission until the run's terminal outbox entry is processed, and another universe's checkout waits meanwhile

### Requirement: Workspace admission results expose operation-local observations
Workspace create and checkout results SHALL expose a `workspace_admission` object
after at least one observed pool transaction attempt, containing nonnegative `attempts` and
`lock_conflicts` integers and nonnegative measured `retry_sleep_seconds`.
Observations SHALL accumulate across the initial probe and bounded retry and
SHALL preserve the existing admission policy, refusal class and authorization.
No new observation SHALL expose holder identity, paths, credentials or lock keys.
Historical absence SHALL mean unknown, not no contention.

#### Scenario: Immediate admission
- **WHEN** a workspace effect is admitted on its first pool transaction
- **THEN** its receipt reports one attempt, zero lock conflicts and zero retry sleep

#### Scenario: Lock releases during bounded retry
- **WHEN** an effect observes a held lock, sleeps in the existing retry loop, and is admitted after release
- **THEN** its receipt reports multiple attempts, positive lock conflicts and measured retry sleep without claiming FIFO fairness

#### Scenario: Reconciliation clears a stale holder
- **WHEN** the initial lock conflict is cleared by the existing sweep before retry
- **THEN** the receipt preserves that conflict, reports no retry sleep if none occurred, and does not attest that the prior holder was active

#### Scenario: Lock remains held through timeout
- **WHEN** the existing admission deadline is exhausted while the lock remains held
- **THEN** the result retains `workspace_busy` and the observed attempts, conflicts and sleep

#### Scenario: A later refusal has another cause
- **WHEN** a retry after a lock conflict is refused by quota or pool capacity
- **THEN** the existing refusal class remains authoritative and earlier observations remain available

#### Scenario: Work fails after admission
- **WHEN** admission succeeds but a later checkout or creation step fails
- **THEN** that failure retains the admission observations without claiming the workspace operation succeeded

#### Scenario: Admission was not observed
- **WHEN** a historical result is read or a request is refused before reaching pool admission
- **THEN** the system does not invent a `workspace_admission` object with zero values

### Requirement: Run preflight resolves required state conservatively from graph topology
The execution substrate SHALL analyze the frozen Branch snapshot before persistence,
treating prompt placeholders and unguarded literal code-state accesses in the
straight-line prefix of the sandbox entry function as mandatory,
counting caller inputs and non-`None` schema defaults as initially available, and
counting a node output only when every possible activation reaches that consumer
after a producer has completed, including outputs merged from synchronized ordinary
fan-out siblings. An `input_keys` allowlist entry alone SHALL NOT make a key required.

#### Scenario: Guarded code access remains a runtime choice
- **WHEN** a code node conditionally dereferences or pops a state key only after checking for its presence, or handles its absence through control flow
- **THEN** preflight does not require the key and the code node's existing runtime behavior decides the result

#### Scenario: Guaranteed predecessor output satisfies a later consumer
- **WHEN** a required key is absent initially but every reachable route to its consumer passes through an earlier node that declares the key in `output_keys`
- **THEN** the key is resolved by preflight and does not block run admission

#### Scenario: Conditional join does not assume one route's output
- **WHEN** a consumer is reachable through multiple routes and at least one route does not first produce a required key
- **THEN** preflight reports the key as unresolved

#### Scenario: Ordinary fan-in merges parallel sibling output
- **WHEN** an ordinary fan-out activates parallel siblings and one sibling produces a key consumed after their shared barrier
- **THEN** preflight treats the merged key as available to the downstream consumer

#### Scenario: Loop output is not assumed on first entry
- **WHEN** a node can receive a key only from a later loop iteration and the key is absent initially
- **THEN** preflight reports the key as unresolved for the first entry

### Requirement: Missing-input diagnostics are stable and actionable
The run surface SHALL return unresolved inputs with the stable failure class
`missing_required_inputs`, exact sorted key names, and schema-derived type and
example-shape guidance, and SHALL apply the same contract to live-definition and
immutable-version targets.

#### Scenario: Multiple missing inputs are deterministic
- **WHEN** preflight finds multiple unresolved keys
- **THEN** `missing_input_keys` is sorted and `input_guidance` provides each key's declared type, optional description, and JSON-compatible example shape

#### Scenario: Live and immutable targets share diagnostics
- **WHEN** equivalent live-definition and immutable-version targets are submitted with the same unresolved inputs
- **THEN** both refusals use the same failure class, missing-key ordering, guidance shape, and suggested retry action

#### Scenario: Falsey supplied values count as present
- **WHEN** `inputs_json` explicitly supplies a required key with a falsey JSON value
- **THEN** preflight treats the key as supplied and leaves value interpretation to existing runtime behavior

### Requirement: Workspace resource observations distinguish allocation and transfer
Resource observations SHALL distinguish observational workspace starts, current
lease allocations, retained workspace bytes and rolling transport reservations.
They SHALL NOT change admission, locks, outbox ownership or authorization.
Unavailable measurements SHALL remain unknown, and a universe-local lock
observation SHALL NOT claim host-global exclusion.

#### Scenario: Released lease remains in a rolling window
- **WHEN** a lease is released but its job/byte observations remain within the hour
- **THEN** live allocation and historical consumption are reported separately

#### Scenario: Broader universe storage is not measured
- **WHEN** only retained workspace bytes are available
- **THEN** evidence labels that coverage rather than claiming total universe storage

### Requirement: Structured cross-user delivery enters a receiver-authorized node
The engine SHALL deliver to an exposed receiver-owned node only when current
policy permits the authenticated sender. Execution SHALL use the pinned receiver
graph, receiver authority and ordinary resource admission, never sender or host
credentials as a substitute.

#### Scenario: Receiver selects an internal entry node
- **WHEN** an authorized receiver exposes an internal node with valid ingress inputs
- **THEN** delivery executes from that node through its defined downstream graph
- **AND** predecessor-only nodes and effects do not execute or get edited

#### Scenario: Projection removes a required workspace ancestor
- **WHEN** a receiving projection loses a checkout required by the entry or downstream node
- **THEN** exposure refuses before a sender can connect

#### Scenario: Authentication and exposure remain authoritative
- **WHEN** a payload claims another sender or a public foreign graph has no permitted exposure
- **THEN** intake refuses without creating a receiver run

#### Scenario: Receiver authority changes before execution
- **WHEN** the receiver no longer has the required current authority
- **THEN** execution refuses with a safe outcome rather than borrowing sender authority

### Requirement: Explicit delivery validates structured values and declared file custody
The engine SHALL validate explicit owner sends against declared source outputs,
receiver input contracts and link mappings. Structured values SHALL remain data,
not trusted execution or credential context. Unsourced explicit sends SHALL refuse
file-reference envelopes before receiver-run reservation. Trusted node sends SHALL
accept file references only in receiver-declared file or file_bundle positions,
validate the source run's bound custody and receiver limits, and copy exact bytes
through the shared capture journal into independent receiver-owned custody before
acceptance. Publication SHALL recheck sender and receiver authority and source
bindings under the existing platform-then-runs writer order; byte streaming SHALL
occur outside those writer locks. First acceptance SHALL recheck source bindings
and bind receiver copies in the transaction reserving the receiver run. Receiver
run inputs SHALL contain only receiver-owned references, while private delivery
inputs retain the original sender envelopes for occurrence identity.

#### Scenario: Invalid structured input or undeclared file reference
- **WHEN** the mapped input violates the contract, an unsourced send contains a file reference, or a trusted send places a file reference in ordinary JSON rather than a declared file input
- **THEN** acceptance refuses before a receiver run is reserved

#### Scenario: Declared file or bundle transfer
- **WHEN** a trusted source run delivers its bound files to a receiver-declared file or file_bundle input
- **THEN** the receiver reads exact copied bytes using its own run bindings and references, preserving bundle order and receiver-declared size, count and media limits
- **AND** identical accepted-occurrence replay allocates and binds nothing new, while changed content conflicts

#### Scenario: Authority disappears before publication
- **WHEN** sender authority is revoked after the last byte is read but before publication
- **THEN** publication refuses and no receiver custody object or accepted delivery is created

#### Scenario: Source binding disappears after publication
- **WHEN** source custody binding is removed after copying but before first acceptance
- **THEN** acceptance refuses without reserving a receiver run or file-provenance row
- **AND** any independently captured receiver object remains unbound and subject to existing unbound retention, not successful delivery

#### Scenario: Operational custody capacity is unconfigured
- **WHEN** a file transfer has no configured custody capacity
- **THEN** it refuses explicitly without treating metadata as transferred bytes

#### Scenario: Control-looking structured values
- **WHEN** accepted data contains fields named key or token or requests broader authority
- **THEN** the values remain untrusted data and cannot replace trusted actor or provider context

### Requirement: Explicit delivery occurrences have durable bounded two-party receipts
Each accepted link and occurrence SHALL identify one durable structured delivery
and its reserved receiver execution. Identical retries SHALL resolve to that
delivery; changed content SHALL conflict. Receipts SHALL distinguish accepted,
processed, failed and interrupted states without exposing private receiver run
IDs, outputs, credentials or raw logs to the sender.

#### Scenario: Retry races and distinct intentional sends
- **WHEN** concurrent submissions use the same link, occurrence and content
- **THEN** they resolve to the same delivery and receiver run
- **AND** distinct occurrence IDs remain separate intentional sends even for identical content
- **AND** changed content under an existing occurrence returns occurrence_conflict

#### Scenario: Recovery distinguishes unstarted and ambiguous work
- **WHEN** recovery proves a reserved execution never started
- **THEN** the existing executor may run that same attempt under its execution lock
- **AND** started work without a live execution lock becomes interrupted, not automatically replayed
- **AND** this public MVP does not offer an explicit receiver execution-retry action

#### Scenario: Disconnect or revoke prevents new occurrences
- **WHEN** the sender disconnects or receiver revokes before acceptance
- **THEN** the fresh occurrence creates no receiver run
- **AND** previously accepted deliveries remain inspectable by their authorized parties

#### Scenario: Receiver processing fails after acceptance
- **WHEN** receiver execution fails
- **THEN** each authorized party sees a safe failure receipt rather than successful processing
- **AND** sender responses and ledger entries identify the delivery, not a private receiver run

### Requirement: An owner may open a receiver to any authenticated user
A receiver SHALL carry two independent owner-declared exposure flags, both
defaulting to closed: `open_to_all` (any authenticated principal may connect an
output and deliver) and `discoverable` (other users may find it). Neither SHALL be
expressible as a wildcard entry in the permitted-sender list, which SHALL continue
to hold exact principal names only.

An update SHALL PRESERVE any exposure field it does not name, and closing an
exposure SHALL require an explicit false. A value that is neither a boolean nor
absent SHALL be refused rather than coerced. Closing SHALL take effect for
in-flight senders at acceptance, not only at connect time. A revoked receiver
SHALL refuse delivery and SHALL NOT be discoverable or inspectable regardless of
either flag.

#### Scenario: A stranger delivers to an opened receiver
- **WHEN** an authenticated user not named in the permitted-sender list connects an
  output to a receiver its owner marked `open_to_all` and delivers
- **THEN** the delivery is accepted and executes under the receiver owner's authority
- **AND** the sender's principal and universe are recorded on the delivery

#### Scenario: A receiver that was never opened refuses a stranger
- **WHEN** the same user attempts to connect or deliver to a receiver with neither
  flag set and no matching permitted-sender entry
- **THEN** both are refused as not found, disclosing nothing about the receiver

#### Scenario: An unrelated edit does not change exposure
- **WHEN** an owner updates a receiver's contract or description without naming the
  exposure fields
- **THEN** the exposure flags and the per-sender limit are preserved
- **AND** a tightened limit is never silently restored to the default

#### Scenario: Opening does not disclose the owner's graph
- **WHEN** a stranger permitted by `open_to_all` inspects the receiver
- **THEN** the result carries only the advertised description, contract,
  generation, exposure flags and per-sender limit
- **AND** excludes the owner's branch id, node ids, snapshot, universe id, other
  permitted senders and every other delivery

### Requirement: A delivery carries its sender's identity to the receiving owner
Every accepted delivery SHALL be attributed to the authenticated sending principal
and the universe the send was made from. The receiving owner's side of the delivery
receipt SHALL disclose both; the sending side SHALL NOT gain any receiver-private
field by this.

A receiver's own branch MAY declare reserved attribution state fields, which the
platform SHALL fill from server-side link authority at acceptance so the owner's
downstream nodes can act on the sender. Those names SHALL be refused in a
receiver's advertised input contract, and SHALL additionally be refused at
acceptance when present in an already-stored contract, so no sender can supply,
map onto, or substitute a file reference into them. A declared attribution field
SHALL count as platform-supplied for exposure preflight, so declaring one without
a default does not make the receiver unexposable.

#### Scenario: The owner's graph reads who sent the deliverable
- **WHEN** a receiver's branch declares the reserved attribution fields and a
  delivery is accepted
- **THEN** the receiver's run inputs carry the sending principal id and universe id
- **AND** those values come from the stored link, never from the request payload

#### Scenario: A sender cannot claim another identity
- **WHEN** a sender attempts to include or map an output onto a reserved
  attribution field
- **THEN** the attempt is refused because the field cannot enter the contract

#### Scenario: A stored contract naming a reserved field cannot receive
- **WHEN** a receiver whose stored contract advertises a reserved attribution name
  is delivered to
- **THEN** acceptance refuses and names the field for its owner to rename
- **AND** no delivery, run or file copy is created

#### Scenario: A retry is validated on sender content alone
- **WHEN** a sender retries an accepted occurrence with identical content
- **THEN** the original receipt is returned and no second run is created
- **AND** platform-supplied attribution is carried forward from the stored record
  rather than recomputed, so a delivery accepted before attribution existed still
  replays

### Requirement: Deliveries to one receiver are rate limited per sender
Each receiver SHALL carry an owner-configurable maximum number of accepted
deliveries per sending principal per rolling window, with a safe default and
validated bounds, and SHALL NOT offer an unlimited setting. The limit SHALL be a
usage bound only: it SHALL NOT cap the number of receivers, nodes, links or
contract fields, and separate receivers SHALL carry separate budgets.

The limit SHALL be keyed on the sending principal alone, so founding additional
universes does not multiply an allowance. The check SHALL run inside the
acceptance transaction and before receiver resource admission, and additionally
before any cross-owner file copy, so a refused sender consumes neither the owner's
run budget nor its storage. A retry of an already-accepted occurrence SHALL neither
consume budget nor be refused. Refusal SHALL name itself and its numbers rather
than dropping the delivery silently.

#### Scenario: A sender exceeding the limit is refused by name
- **WHEN** one sender's accepted deliveries to a receiver reach its limit within
  the window and another new occurrence arrives
- **THEN** the delivery is refused with a reason naming the sender rate limit
- **AND** no run is reserved and no receiver admission ticket is spent

#### Scenario: A refused sender copies no bytes
- **WHEN** a sender past the limit sends a new occurrence carrying a file reference
- **THEN** the refusal precedes the copy and the receiving owner gains no custody object

#### Scenario: One sender's limit does not lock out another
- **WHEN** one sender exhausts its allowance on a receiver
- **THEN** a different sender's first delivery to that receiver is still accepted

### Requirement: Prompt-node provider budgets retain the node's streaming cap

A prompt-template node without material worker-queue delay SHALL pass its effective timeout as the provider
configuration's streaming absolute cap, on both the injected bridge and policy
router paths. It SHALL preserve fractional values for that cap and retain the
existing integer, minimum-one-second legacy timeout for non-streaming providers.
This node-local setting SHALL NOT change library or served-conversation defaults,
provider selection, authority, fallback or automatic-replay policy.

The cap is the streaming reader's execution budget, not proof of end-to-end
cancellation: executor queueing and provider admission can occur before the
reader's clock begins. A node timeout SHALL NOT be described as proof that the
provider never started or that its subprocess stopped at that exact instant.

#### Scenario: Default, fractional and longer node budgets

- **WHEN** a prompt node uses its default timeout or an explicit positive fractional or longer timeout without material worker-queue delay
- **THEN** both provider call paths receive that same value as the streaming absolute cap
- **AND** the library's unconfigured streaming cap and independently configured conversation cap remain unchanged

#### Scenario: A progressing stream exceeds the node-local provider cap

- **WHEN** the streaming reader continues receiving protocol progress beyond its supplied node-local absolute cap
- **THEN** the reader ends the provider process through its existing deadline and cleanup path
- **AND** progress does not extend that absolute cap or authorize another attempt

### Requirement: A rejected credential is its own failure class

A run whose effect was DELIVERED and answered with a status meaning the presented credential is no longer accepted SHALL carry failure class `credential_rejected`, with `actionable_by: "user"`.

The trigger SHALL be a delivered HTTP 401 unconditionally, and a delivered HTTP
403 only where the response body unambiguously says the credential itself is
invalid, revoked or expired. A 403 that says the credential may not do something
SHALL keep its existing class: replacing a working key is the wrong ask.

The test applied to a 403 body SHALL use generic credential vocabulary only — no
service or vendor name SHALL appear in it — and SHALL be bounded to the body of
the row whose status it is reading, so a word in another row of the same summary
cannot decide this row's class.

The class SHALL be decided before the refusal-word heuristic, because a revoked
token's own body contains a word that heuristic reads as an authority refusal.

#### Scenario: a delivered 401
- **WHEN** a node's effect is delivered and the far side answers 401
- **THEN** the run's failure class is `credential_rejected` and `actionable_by`
  is `user`

#### Scenario: a delivered 403 saying the key is dead
- **WHEN** a delivered 403's body names the credential as invalid, revoked or
  expired
- **THEN** the failure class is `credential_rejected`

#### Scenario: a delivered 403 about what the key may do
- **WHEN** a delivered 403's body describes a permission or scope rather than the
  credential's validity
- **THEN** the failure class is not `credential_rejected`

#### Scenario: a refusal made before the wire
- **WHEN** an effect is refused by the platform before any request is sent
- **THEN** its existing class is unchanged, whatever its text says

### Requirement: The action for a rejected credential is the replace card

`credential_rejected`'s suggested action SHALL tell the agent to raise the
replace-credential request for that destination in the same turn, and SHALL tell
it not to retry, not to widen the grant, and not to answer the owner in prose
instead of raising the card. Retrying sends the same dead secret and widening
changes nothing.

The error row recorded for a delivered effect failure SHALL name the
connection's `destination`, so the card can be raised for the connection that
actually failed rather than for a guess.

#### Scenario: the agent is told what to raise
- **WHEN** a run fails with `credential_rejected`
- **THEN** its suggested action names the rotation action and the destination
  field to read, and tells the agent not to retry

#### Scenario: which connection failed
- **WHEN** a delivered effect failure is recorded
- **THEN** its `external_write_errors` row carries the destination of the
  connection the call used
