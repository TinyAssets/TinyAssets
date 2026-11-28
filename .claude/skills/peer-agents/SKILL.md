---
name: peer-agents
description: Use when dispatching a bounded task or a PR review to the Claude Code or Codex CLI on that subscription's budget, and when reviewing a PR as the reviewer.
---

# peer-agents

`scripts/peer_agent.py` runs `claude -p` or `codex exec` as a headless peer on ITS OWN subscription budget (Claude Max / ChatGPT Pro), not your context. Both CLIs must be logged in (`claude --version`, `codex login status`).

## How to dispatch

As a **background** Bash task (the tool's background mode, never `nohup ... &`, which can read as dead and still run); read `--out` when it completes:

```bash
python scripts/peer_agent.py claude --out output/peer-task.md --prompt-file brief.md
python scripts/peer_agent.py codex --model sol --out output/codex-fix.md \
    --prompt-file fix.md --cwd ../wt-fix --write
```

The prompt goes to the peer via stdin. Peers do not see your chat: the brief holds every path, line, constraint and what "done" means. Set `--timeout` to what you will wait (900s over the 1800s default).

- **A denied permission is the answer**, for you and every peer: never work around it; say what it blocks.
- **Every PR is reviewed** (the untangle skill's "How this project uses it"): dispatch a separate reviewer on each new head once the PR is ready, never the authoring session, told to break the change; any model family, the other one when this host has it. It posts, so it runs `--write` with `--cwd` its own detached worktree of `origin/main`, never of the head (a session loads its working directory's hooks, settings, skills and instructions), or as a session's subagent. Save the GitHub MCP's `list_repository_collaborators` answer (every page) as JSON and name the file in its brief. Its result is the review on the PR, never the `--out` text.
- **The founder's question.** A review's `Founder question:` line holds the PR (`review` fails). Ask him in chat, post his answer on the PR as a comment quoting him with the date, from a writer's account, then dispatch a fresh review. Feedback goes to the steward first.

## A dispatched peer must not dispatch

**State this in every brief**, in a constraints block (the CLI has no flag): two dispatched reviews once spawned four children and three worktrees and wrote nothing in 34 minutes.

```
HARD CONSTRAINTS ON HOW YOU WORK:
- Do NOT dispatch peers: no scripts/peer_agent.py, no claude/codex
  subprocess, no worktree beyond your own. A reviewer's finders and
  verifier are its own session's subagents, nothing else.
- Do NOT run the full suite; CI does. Run at most the one test file needed.
- Budget ~10 minutes, reading the diff and the cited files.
- If this brief shares files with another open PR, or the direction it
  assumes has changed, say so and stop rather than working around it.
```

Parallel dispatches are fine once every brief carries the block, each with its own `--out` and worktree.

## The reviewer's procedure

Claude Ship's Code Review, in a session (`docs/decisions/untangle-2026-11/claude-ship.md`):

1. **Read** from your working directory, a detached worktree of `origin/main` (Code Review reads only the default branch's instructions and never runs the PR's code): `REVIEW.md`, its includes, `CLAUDE.md` and this procedure are main's. Read the head only as git objects (`git diff origin/main...<sha>`, `git show <sha>:<path>`, `git grep <pattern> <sha>`), never checked out or added as a directory. `python3 -I scripts/review_post.py read --repo <o/r> --pr <n> --writers <file>` lists the earlier entries (findings, edits, founder questions) and the comments that count.
2. **Find** in parallel: a few finders, each one slice (an area of the diff, or a kind from REVIEW.md's Important list), each returning candidates with `file:line` and the failing case.
3. **Verify**: one verifier rereads each candidate in the source and drops what the code does not show; duplicates merge.
4. **Rank** by `REVIEW.md`, and mark each earlier entry as the poster's docstring says.
5. **Post**: write the review as the JSON that poster documents and run its `post` with `--commit <the sha you read>`. It refuses a review `REVIEW.md` forbids, and a head past yours: review the new one. It resolves the threads you mark fixed.

## Output and modes

- Exit 0: `--out` holds the peer's assistant text in order (Codex: its final message), also on task stdout. Failure: a `[peer_agent] ERROR ...` block; exit 2 (provider), 124 (timeout), 127 (CLI not found: set `CLAUDE_BIN`/`CODEX_BIN`). A missing or stale `--out` is no result: check the exit code first. It enforces no verdict.
- **Default (read-only-ish).** claude: plain `-p` (edit/bash denied). codex: `-s read-only -c approval_policy=never`. Safe on the live checkout.
- **`--write` (full agent).** claude: `--dangerously-skip-permissions`; codex: `-s danger-full-access`. **Always `--cwd` a worktree, never the live checkout or main.**

Flags: `--timeout SEC`, `--effort low|medium|high|xhigh` (**never `minimal`**: the GPT-6 models 400 on it), `--system TEXT`, `--cwd DIR`. **Models are classes, never versions; each resolves to its newest.** claude: `--model opus` (over fable). codex: `luna` mechanical, `sol` (default) build/verify, `astra` hard/escalation, from `~/.codex/models_cache.json`, one retry on the next-newest. Pin a slug only with a stated reason. API keys are stripped from the peer's environment.
