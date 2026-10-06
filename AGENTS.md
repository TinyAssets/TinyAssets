# TinyAssets

<!-- direction:start -->
## Direction

The founder owns this section. To re-steer, replace a line; never add one beside it.
`python scripts/sync_direction.py` copies it verbatim into `AGENTS.md`.

1. Product: a Meta-Muse-level agent in the chat bubble, on pi.dev-style plumbing; users build from there.
2. Plumbing: the model sees 4 tools (read/write/edit/bash) plus `ta`; one extension unit; one agent definition for every model provider; no provider-specific code.
3. The agent's abilities are editable files, skills and packages. The platform does not build feature editors or pre-built features.
4. Users connect any model source; default to their strongest connected source.
5. Foundational patches are brought FORWARD, so pre-migration band-aids never delay positive architectural moves. Weigh the totality of pending work, so each module is always pursuing or maintaining its best architecture, and refactor and reorder the remaining work to get there. (Today that foundation is per-owner isolation, landed in small slices.)
6. Ship live fast and verify live. LESS process: delete stale docs, tests and notes rather than adding more.
7. Long-term goal: in both the Google Play and Apple App Store, with growing downloads and positive reviews.
8. 24/7 uptime with zero hosts online: every surface works with no host machine on.

No longer the direction: "a global goals engine", Goal ladders, and fantasy as the default domain.
Where `PLAN.md` or any older doc disagrees with this section, this section wins.
<!-- direction:end -->

Architecture and operating principles: `PLAN.md`. Here: the loop, and facts a
model would get wrong. Every other rule lives at its point of use.

## Live state

| Kind | Home |
|---|---|
| Queued work | `openspec/changes/` |
| Unresolved findings | `docs/concerns/` — one file each, delete to resolve |
| Founder-only work | `docs/host-actions.md` |
| Who has what | branches and open PRs |
| Narrative | the git log: your commit message |

Orient: the Direction above, then `python scripts/openspec_flow.py audit`.
Write durable state before replying.

## The loop

1. One intent, one worktree, disjoint files, one PR; new scope is a new lane.
2. A one-sentence diff gets no plan; hard-to-reverse surfaces get a spec first.
3. Run the tests your diff touches plus affected heavy files, and `ruff`; CI does
   the rest. Mutation tables only for data-loss or cross-user guards.
4. Review only floor-class changes and gate files: one cross-family round
   (`peer-agents`), floor and correctness findings only, `AGREE`/`DISAGREE_EVIDENCE`.
5. No lane cap: fold colliding or superseded lanes into one; serialize merges.
6. Done = sha asserted deployed, one naive-user pass through the app agent, spec
   synced. A pasted chat is a bug report.
7. Same error three times, or the same finding twice: hand off, do not patch.
8. A new rule deletes an old one; a rule a script can enforce gets no line.

## Facts (numbers are cited repo-wide)

1. `SqliteSaver`, never `AsyncSqliteSaver`.
2. Reuse the LanceDB connection object; never recreate it.
3. Vendor-neutral compute only — `PLAN.md`.
4. Gates default autonomously — `PLAN.md`.
5. Accumulating state needs `Annotated[list, operator.add]` —
   `domains/fantasy_daemon/state/book_state.py`.
6. `FactWithContext` carries truth-value typing — `tinyassets/knowledge/models.py`.
7. Python 3.11+ — `pyproject.toml`.
8. Fail loudly; a mock fallback that looks real is worse than a crash.
9. Uploads are verbatim — `PLAN.md`.
10. Ship-time attribution — `tinyassets/attribution/`.
11. `https://tinyassets.io/mcp` is the only public endpoint; `mcp.tinyassets.io`
    is internal and never documented. After DNS/tunnel/connector edits:
    `python scripts/mcp_public_canary.py --assert-handles`.
13. Prove a path is on a remote or in history before destroying it; never switch
    a dirty worktree to `main`.
14. `python scripts/deployed_sha.py --assert-contains <sha>` before "shipped".
15. The platform has no LLM — `PLAN.md`.

Required CI excludes `.github/heavy-test-files.txt`; `heavy-tests` skips PRs.
Sandbox, filesystem or process-limit work needs `python scripts/linux_oracle.py`;
a skip is not a pass. No temp root inside the repo. Secrets:
`set -a; source scripts/load_secrets.sh; set +a`. Gates:
`docs/reference/executable-gates.md`.
