# runner-protocol-4 as built (2026-11-22)

`untangle/runner-protocol-4`: runner protocol 4's first deploy, which D5 (`untangle/agent-hooks`,
`agent_definition_research.md` §4.4) needs, as `runner-protocol-3.md` (#5757) was D4's. The rule
and its guard are `runner-protocol-3.md`'s: two deploys, the first Worker speaking both protocols
while its runner speaks the old one; the guard reads one tree, so two PRs are the order of the
deploys, not a check.

## What landed

1. **Protocol 4's record** (`protocol.json`), pinned (`1d3c4ee1…932b`). It is protocol 3 with
   two changes, both D5's:
   - the start document gains `hooks` (an object): the agent's own hooks by event, as its
     admission read `hooks.json`, an event absent holding none;
   - the commands gain `hooks`, the dispatcher a run's CLI runs from its root-owned hook file
     (internal, as `decide` is: the Worker never sends it).
2. **The Worker speaks 2, 3 and 4; its runner still speaks 3** (`SPEAKS`, `PROTOCOL`).
3. **The start document carries `hooks: {}`** until D5's admission reads `hooks.json`; a
   protocol 2 or 3 runner does not read the field. `StartDoc` and `DocHook` are protocol 4's
   own types, keyed by the event's name, so the owner contract's `HookEvent` can change without
   changing what a live runner reads. The route probe's and the tests' start documents carry
   the empty field.
4. **Its test** is `runner-protocol-3.md`'s: the sandbox Worker's test holds each document to
   the joined shape of the protocols it speaks (`spokenShape`), and checks the empty `hooks`.

## Owed

- [x] `untangle/agent-hooks`: protocol 4's second deploy (the runner speaks 4 and takes `hooks`, the Worker 3 and 4), with D5's admission, records and dispatcher. Done (`agent-hooks.md`).
