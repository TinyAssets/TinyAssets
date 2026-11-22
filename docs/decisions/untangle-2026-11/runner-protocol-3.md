# runner-protocol-3 as built (2026-11-22)

`untangle/runner-protocol-3`, owed by `drop-kimi.md` (the sign-in document's `region`) and the
expand deploy D4 (`untangle/agent-mcp`, `agent-mcp.md` item 4) needs: runner protocol 3's first
deploy, as `r5-hooks-speaks.md` (#5706) was protocol 2's.

## What the rule and its guard require

- **The rule** (`sandbox/runner/protocol.ts`'s header): a protocol change ships in two deploys.
  The first Worker speaks both protocols while its runner speaks the old one. The second runner
  speaks the new one.
- **The guard** (`sandbox/runner/state-format.test.ts`) holds that every record is frozen by its
  digest, that `SPEAKS` holds `PROTOCOL` and only protocols one either side of it, and that each
  spoken protocol is recorded. It reads one tree. Nothing checks PRs, history or deploys, so the
  guard does not require two PRs.
- **What a deploy in between protects.** The Worker and its runner deploy together, so mixed
  versions meet only in sandboxes that live across a deploy, paused ones included. The Worker
  asks a sandbox's runner its protocol (`state`) before it adopts its own runner, and refuses a
  runner speaking a protocol it does not speak (`sandbox_runner_protocol`). D4's Worker speaks 2
  and 3, so it already takes every sandbox main's Worker left (protocol 2 runners). What the
  expand deploy adds is a rollback past D4's deploy: this Worker speaks 3, so a sandbox left with
  D4's runner is not refused after a rollback to it. As `r5-hooks.md` item 5 recorded for
  protocol 2, the dark platform has no such rollback to make. So the rule is followed by deploy:
  if this merges before the dark redeploy and D4 after it, that redeploy is the expand deploy. If
  both merge before it, the one deploy carries D4, and only the rollback case is lost.
- Two PRs all the same, as the coordinator asked and as P-hooks did. This one is the expand alone,
  and D4 merges main on top.

## What landed

1. **Protocol 3's record** (`protocol.json`), byte for byte D4's and pinned
   (`f46e144d…d5ff`). It is protocol 2 with two changes:
   - the start document gains `mcp_servers` (an object);
   - the sign-in document loses `region`.
   One record carries both, because a record is frozen once recorded.
2. **The Worker speaks 1, 2 and 3; its runner still speaks 2** (`SPEAKS`, `PROTOCOL`).
3. **The Worker writes what each protocol it speaks reads** (`protocol.ts`'s header):
   - the start document carries `mcp_servers: {}`. No admission keeps a server until D4, and a
     protocol 2 runner does not read the field;
   - the sign-in document keeps `region: null` for protocols 1 and 2. A protocol 3 runner does
     not read it.
   - `StartDoc` and `DocMcpServer` are D4's types. The route probe's and the tests' start
     documents carry the empty field.
4. **Its test** (`sandbox/test/sandbox.test.ts`) holds each document the Worker writes to the
   joined shape of the protocols it speaks (`spokenShape`, `runner/test/state-shape.ts`), not to
   its runner's protocol alone. A Worker that speaks 3 and writes no `mcp_servers` fails it, and
   so does one that drops 3 from `SPEAKS`.

## Owed

- [ ] `untangle/agent-mcp`: protocol 3's second deploy (the runner speaks 3, the Worker 2 and 3), with D4's admission and rendering.
- [ ] `untangle/runner-protocol-3-contract`: once no deployed runner speaks 2, `region` goes from `SigninDoc` and `signinDocument`. That is what is left of `drop-kimi.md`'s `untangle/runner-protocol-3` line (`protocol.json`'s part is done here).
