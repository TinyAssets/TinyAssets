---
severity: P2
title: K2 one agent definition - remaining instruction differences and release proof
filed: '2026-10-06'
summary: Every provider kind now renders the one definition's tools, instructions (budget line included) and capabilities (guard - tests/test_one_agent_definition.py; codex via app-server dynamicTools, captured). Native executors run activities behind the route fence, the bash/ta stop and native-call cancellation. Still open - claude-code 2.1.290 prepends a one-line identity and an environment block under subscription OAuth with no switch; remote box ta bridge (#4525); live paired trials, deployed SHA, owner app pass.
---

Founder direction 2026-10-06: one agent definition supplies tools,
instructions and capabilities to every provider and every agent; providers only
translate it. Lead decision (round 2): rendering the definition into a
provider's launch -- flags, config, app-server protocol, a model catalog -- is
the adapter's job, as long as what the model sees comes out identical.

## Resolved

- **Codex.** Served agent turns run `codex app-server` with the definition's
  tools as `thread/start.dynamicTools`, no MCP server, every native feature off
  and a reduced model catalog (`tinyassets/providers/codex_launch_contract.py`).
  The model sees exactly `read, write, edit, bash` plus our instructions
  (1,571 resident chars, was 49,713). Every call is forwarded through the
  engine route. Verified on codex-cli 0.160.0 by a loopback capture, a live
  signed-in turn (the catalog survives a ChatGPT session; `model/list` still
  returns it after the turn), a resume capture, and the image build's own gate
  (`scripts/codex_cli_smoke.py`). The image pin moves to 0.160.0.
- **Activities.** Every provider's tools cross the engine route's
  `ActivityFence`, which also serializes an activity's calls. Each `ta`
  request is checked. A `ta` request that stops the activity is never answered
  before the jail dies, and a running bash is killed once the activity stops.
  A native call is cancelled at the stop: its process tree ends and its claim
  is released, proved on Linux with the real Claude adapter. Native executors
  are no longer refused, and #4524's native-only admission refusal is lifted.
  That incident (run a8248a4f0ad54349, turn 57cadc0f43974776aa19cd319df207ce
  left ready with zero rounds on 2026-10-06) is fixed by #4524's
  orphan-turn repair.
- **HTTP budget line.** The coordinator gives every execution kind the same
  instructions, the budget line included.
- **Guard.** `tests/test_one_agent_definition.py` renders the definition
  through every registered provider kind's launch code. Kinds with no agent
  executor (`content_blocks`/`anthropic_messages` without an agent codec,
  ollama with no execution kind) must be refused for agent turns, and are.

## Still open

1. **Claude CLI framing.** claude-code 2.1.290 sends, besides our
   `--system-prompt`, the line "You are a Claude agent, built on Anthropic's
   Claude Agent SDK." (unconditional except on Vertex; `HQn` in the shipped
   binary) and an environment block (cwd, platform, OS, model id, date, a
   downloads-are-untrusted paragraph). `--bare` / `CLAUDE_CODE_SIMPLE=1` drop the
   environment block but refuse OAuth (captured: no request is sent), and
   `--exclude-dynamic-system-prompt-sections` is ignored with `--system-prompt`.
   No tool or capability differs; the instruction text does. Founder call:
   accept as vendor framing, or ask Anthropic for a switch.
2. **Remote box ta bridge** (`agent_loop/served_chat.py`): separate lane #4525.
3. **Release proof:** paired live trials across providers, deployed SHA, a real
   owner app pass.
