---
name: peer-agents
description: Use when dispatching a bounded task or independent adversarial review to the Claude Code or Codex CLI on that subscription's budget.
---

# peer-agents

`scripts/peer_agent.py` runs `claude -p` or `codex exec` as a headless peer agent. The peer spends ITS OWN subscription budget (Claude Max / ChatGPT Pro), not your context. Both CLIs must be installed and logged in (`claude --version`, `codex login status`).

## How to dispatch

Launch as a **background** Bash task (peers take minutes, not seconds), then read the `--out` file when the completion notification arrives:

```bash
# Review this repo with Claude (read-only default):
python scripts/peer_agent.py claude --out output/peer-review.md \
    --prompt-file brief.md

# Have Codex fix something in a worktree (write mode):
python scripts/peer_agent.py codex --model sol --out output/codex-fix.md \
    --prompt "Fix the failing test in platform/owner/test/ and run npm run test:workerd -w owner" \
    --cwd ../wt-fix --write
```

For big briefs, write the brief to a file and pass `--prompt-file`. The prompt always goes to the peer via stdin.

## A dispatched peer must not dispatch

**State this in every brief.** A peer given a review brief will, left to itself,
farm the review out rather than do it -- two dispatched reviews once spawned four
children and three worktrees and wrote nothing in 34 minutes. Recursive, not deep.

Put a constraints block in the brief itself -- the CLI has no flag for it:

```
HARD CONSTRAINTS ON HOW YOU WORK:
- Do NOT dispatch sub-agents. No scripts/peer_agent.py, no claude/codex
  subprocess, no new worktree. You are the reviewer; review it yourself.
- Do NOT run the full suite; CI does. Run at most the one test file needed.
- Budget ~10 minutes. Read the diff and the cited files and reason.
- Do the untangle skill's searches (Fact 16) yourself, inside this block's
  limits: no subagents.
- Lanes are not capped, but they are RECONCILED: if this brief collides with
  another open lane (shared files or overlapping intent), or the design direction
  it assumes has changed, say so and stop rather than working around it. The lead
  folds colliding or superseded lanes into one, or rebases the briefs onto the new
  direction, so every objective keeps moving. Merges stay serialized.
```

Habits from the same incident:

- **Parallel dispatches are fine once every brief carries the block above.**
  The storm came from peers dispatching peers, not from two siblings; give
  each its own `--out` and worktree so its processes stay attributable.
- **Set `--timeout` to what you will actually wait** (900s over the 1800s
  default). A peer with nothing at the halfway mark is not about to produce.
- **A `nohup ... &` dispatch from the Bash tool is not reliably backgrounded** --
  it can read as dead (exit 0, no output) and still be running. Use the tool's own
  background mode.

## Output contract

- Success: `--out` holds every Claude assistant text block in stream order, including Stop-hook continuations, or Codex's final message; exit 0. Claude requires a successful terminal result; its duplicate echo of the last text block is omitted. Thinking, tool payloads, user/system events and raw JSON are excluded. The full retained result is also on task stdout.
- Failure: the file holds a `[peer_agent] ERROR ...` block; exit 2 (provider error), 124 (timeout), 127 (CLI not found — set `CLAUDE_BIN`/`CODEX_BIN`).
- Never treat a missing or stale `--out` file as a result; check the exit code in the task status first.

## Modes

- **Default (read-only-ish).** claude: plain `-p` (Read/Glob/Grep allowed, edit/bash denied). codex: `-s read-only -c approval_policy=never`. Safe to point at the live checkout.
- **`--write` (full agent).** claude: `--dangerously-skip-permissions`. codex: `--full-auto` (workspace-write sandbox). **Always point `--cwd` at a worktree in write mode, never the live checkout or main.** The peer can then edit, run tests, and iterate on its own.

Useful flags: `--timeout SEC` (default 1800), `--effort low|medium|high|xhigh` (both CLIs; `low` for trivial tasks, `xhigh` to escalate). **Never `--effort minimal` — the GPT-6 models reject it with a 400.** Also `--system TEXT` (codex: prepended to prompt), `--cwd DIR`.

**Models are classes, never versions; each resolves to its newest.** claude: `--model opus` (over fable). codex: `luna` mechanical, `sol` (default) build/verify, `astra` hard/escalation; resolved from `~/.codex/models_cache.json`, one retry on the next-newest, model printed. Pin a slug only with a stated reason.

## When to use which peer

- **Every PR is reviewed** (the untangle skill's "How this project uses it"); this skill owns the mechanics. Dispatch a separate reviewer as soon as the diff is pushed, alongside CI, told to break the change: either CLI, any model family, never the authoring session. Prefer the other family when it is available on this host.

## Notes

- API keys are stripped from the peer's environment (subscription auth only).
- **You write the review contract:** `peer_agent.py` sends your prompt verbatim and enforces
  no verdict. Ask for one **in the prompt** ("end with exactly one line:
  `VERDICT: APPROVE|ADAPT|REJECT`") and check that it arrived.
- Peers do not see your chat context. Put everything they need in the prompt/brief: file paths, line numbers, what "done" means, and any constraints (e.g. "do not commit").
