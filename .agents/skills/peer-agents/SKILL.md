---
name: peer-agents
description: Use when dispatching a bounded task or independent cross-family review to the Claude Code or Codex CLI on that subscription's budget; for task-scoped external implementation examples, use a repo search for precedent to define the research role and return contract.
---

# peer-agents

`scripts/peer_agent.py` runs `claude -p` or `codex exec` as a headless peer agent. The peer spends ITS OWN subscription budget (Claude Max / ChatGPT Pro), not your context — your only cost is launching the job and reading back the result file. Both CLIs must be installed and logged in (`claude --version`, `codex login status`).

## How to dispatch

Launch as a **background** Bash task (peers take minutes, not seconds), then read the `--out` file when the completion notification arrives:

```bash
# Review this repo with Claude (read-only default):
python scripts/peer_agent.py claude --out output/peer-review.md \
    --prompt-file brief.md

# Have Codex fix something in a worktree (write mode):
python scripts/peer_agent.py codex --model gpt-6-astra --out output/codex-fix.md \
    --prompt "Fix the failing test in tests/test_universe_nodes.py and run it" \
    --cwd ../wf-bug126 --write

# Quick foreground question (prints to stdout, no file):
echo "One paragraph: what does workflow/router.py do?" | python scripts/peer_agent.py claude
```

For big briefs, write the brief to a file with your Write tool and pass `--prompt-file` — avoids shell-quoting and Windows command-line limits. The prompt always goes to the peer via stdin.

## A dispatched peer must not dispatch

**State this in every brief.** A peer given a review brief will, left to itself,
farm the review out rather than do it -- two dispatched reviews once spawned four
children and three worktrees and wrote nothing in 34 minutes. Recursive, not deep.

Put a constraints block in the brief itself -- the CLI has no flag for it:

```
HARD CONSTRAINTS ON HOW YOU WORK:
- Do NOT dispatch sub-agents. No scripts/peer_agent.py, no claude/codex
  subprocess, no new worktree. You are the reviewer; review it yourself.
- Do NOT run the full suite. No scripts/ci_required_tests.py, no
  `pytest -m "not slow"`. Run at most the one test file you need.
- Budget ~10 minutes. Read the diff and the cited files and reason.
- Lanes are not capped, but they are RECONCILED: if this brief collides with
  another open lane (shared files or overlapping intent), or the design direction
  it assumes has changed, say so and stop rather than working around it. The lead
  folds colliding or superseded lanes into one, or rebases the briefs onto the new
  direction, so every objective keeps moving. Merges stay serialized.
```

Two more habits that fell out of the same incident:

- **One dispatch at a time.** Two concurrent reviews multiplied the spawn storm
  and made it much harder to tell which tree of processes belonged to what.
- **Set `--timeout` to what you will actually wait** (900s over the 1800s
  default). A peer with nothing at the halfway mark is not about to produce;
  check its `Get-CimInstance Win32_Process` command line instead of waiting.
- **A `nohup ... &` dispatch from the Bash tool is not reliably backgrounded** --
  it can read as dead (exit 0, no output) and still be running. Use the tool's own
  background mode.

## Output contract

- Success: `--out` holds every Claude assistant text block in stream order, including Stop-hook continuations, or Codex's final message; exit 0. Claude requires a successful terminal result; its duplicate echo of the last text block is omitted. Thinking, tool payloads, user/system events and raw JSON are excluded. The wrapper does not choose a verdict: multiple verdicts remain visible and must be read in context. The full retained result is also on task stdout.
- Failure: the file holds a `[peer_agent] ERROR ...` block; exit 2 (provider error), 124 (timeout), 127 (CLI not found — set `CLAUDE_BIN`/`CODEX_BIN` to the full `.cmd` path on Windows).
- Never treat a missing or stale `--out` file as a result; check the exit code in the task status first.

## Modes

- **Default (read-only-ish).** claude: plain `-p` (Read/Glob/Grep allowed, edit/bash denied). codex: `-s read-only -c approval_policy=never`. Safe to point at the live checkout.
- **`--write` (full agent).** claude: `--dangerously-skip-permissions`. codex: `--full-auto` (workspace-write sandbox — weak on Windows). **Always point `--cwd` at a `wf-*` worktree in write mode, never the live checkout or main.** The peer can then edit, run tests, and iterate on its own.

Useful flags: `--timeout SEC` (default 1800), `--effort low|medium|high|xhigh` (codex only; `low` for trivial tasks). **Never `--effort minimal` — gpt-6-astra rejects it with a 400.** Also `--system TEXT` (codex: prepended to prompt), `--cwd DIR`.

**Models are pinned, not inherited.** claude: `--model fable`. codex: `gpt-6-astra`/`medium` is the wrapper default (`DEFAULT_CODEX_MODEL`); the CLI's own default is rejected by a ChatGPT account. Override only with a stated reason (`--model`, `WORKFLOW_CODEX_MODEL`). A failed run writes `[peer_agent] ERROR` + full stderr to `--out`.

## When to use which peer

- **Which changes need a review is not decided here.** `AGENTS.md` § *The loop* (item 4) owns the scope — floor-class changes and gate files, one round, after the PR opens; this skill owns the mechanics. When one is owed: if you are Kimi/Claude, dispatch to codex; if you are Codex/OpenAI, dispatch to claude.
- **claude**: strong at nuanced code review, design critique, long-document analysis. Read-only by default; write mode works but codex is usually the better coding workhorse on this host.
- **codex**: strong autonomous coding loops (edit → run tests → iterate) in `--write` mode inside a worktree.

- **External implementation examples:** a repo search for precedent owns the focused brief, enforced read-only role, source map, and direct-to-coder return. `peer-agents` may run that role but does not replace its research contract.
- **Internal repository localization:** use the harness's read-only codebase explorer or a focused read task; do not invoke the external precedent workflow.

## Notes

- API keys are stripped from the peer's environment (subscription auth only),
  matching the daemon's provider policy in `tinyassets/providers/`.
- **You write the review contract; the wrapper does not.** The retired `codex_review` wrapper used to
  inject an adversarial preamble and demand a trailing `VERDICT:` line. It was deleted
  2026-08-26 and `peer_agent.py` does neither — it sends your prompt verbatim. When you need a
  verdict, ask for one **in the prompt** ("end with exactly one line: `VERDICT: APPROVE|ADAPT|REJECT`")
  and check that it arrived. Do not assume enforcement that no longer exists.
- Peers do not see your chat context. Put everything they need in the prompt/brief: file paths, line numbers, what "done" means, and any constraints (e.g. "do not commit").
- Windows: the wrapper resolves `.cmd` shims and converts Git-Bash paths; run it with plain `python` from Git Bash.
