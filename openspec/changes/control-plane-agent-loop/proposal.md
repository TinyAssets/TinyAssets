## Why

Slice S7 of `target-architecture` (D6, D11). A served turn on an HTTP model
protocol is mostly waiting on a streamed response, and today it waits as a
turn-private event loop on a parked worker thread, with the four tools served
by a per-command-center engine MCP process and executed in a per-call tool
jail. A CLI turn costs far more: ~77 MB PSS per waiting provider subprocess in
production, 338 MiB per Claude Code run in a gVisor box (box-cost research §E).
Memory is the binding resource. Anthropic's Managed Agents split -- harness
outside, sandbox for tools -- is the industry shape and cut p50 TTFT by 60%.

The thin loop is that split, owned by us: the model loop runs as `asyncio`
coroutines in the platform (the always-on execution owner once S8 lands) and
calls the model only through the existing credential broker; tools run in the command center's box over a
handle bound at turn start; no model credential ever enters the box.

## What Changes

- `tinyassets/agent_loop/`: the box tools, the owner reads, and the tool
  router that sends each call to exactly one place. Turns keep their own event
  loop for now; one shared loop waits on a task-aware admission lock (design
  decision 1).
- `read`/`write`/`edit`/`bash` are forwarded to the turn's box through the
  `BoxProvider` contract (D2) by `op_id` = the call's journal position. A lost
  reply is asked about with the SAME `op_id`, once; anything unresolved is an
  unknown outcome, the turn holds and nothing replays. Writes through the box
  tools are ordered by a lock, so of two edits that read the same bytes the
  second refuses instead of silently overwriting the first.
- `history` and `activity` are answered by the loop, read-only, through the
  same domain reads and identity gates as the owner door and the engine; they
  never reach the box.
- Every other served tool keeps the engine route, so the owner's rules and the
  auto-review (harness D1) gate consequential actions exactly where they do
  today.
- `AgentTurnCoordinator` opens its tools through the adapter when the adapter
  provides them and passes the `op_id`; the journal is unchanged.
- Opt-in switch `TINYASSETS_AGENT_LOOP=thin`. Unset, every path is today's.
- `scripts/measure_agent_loop_memory.py`: resident memory per waiting turn,
  500 concurrent turns against a mock SSE server.

Not in this change (owed by later PRs of this change, see `tasks.md`): model
token streaming to the app (needs S6's streaming broker contract), the shared
loop (needs a task-aware admission lock), the production box driver (S4), the S8a lease generation, CLI-in-box, retiring the
engine route for the four tools in production, the live proof and spec sync.

## Impact

- Authority: no new authority. The box handle is bound once per turn from the
  owner the coordinator already checked; owner reads re-check founder home and
  run under a read-only identity. A granted box tool with no box provider is
  refused before the first inference, never served by the tool jail instead.
- Storage: none. The journal schema is untouched.
- Public MCP surface: unchanged. `history`/`activity` exist only inside the
  thin loop's turn, not on the connector or the engine route.
- Rollback: unset the switch.
