# TinyAssets

Principles and architecture: `PLAN.md` (§ *Operating Principles*). Here: the loop,
and facts a model would get wrong. Every other rule lives at its point of use.

## Live state

| Kind | Home |
|---|---|
| Queued work | `openspec/changes/` |
| Unresolved findings | `docs/concerns/` — one file each, delete to resolve |
| Founder-only work | `docs/host-actions.md` |
| Who has what | branches and open PRs |
| Narrative | the git log: your commit message |

Orient: `python scripts/docview.py headings PLAN.md`, one section, then
`python scripts/openspec_flow.py audit`. Write durable state before replying.

## The loop

1. One intent, one worktree, disjoint files, one PR; new scope is a new lane.
2. A one-sentence diff gets no plan; hard-to-reverse surfaces get a spec first.
3. Run the tests your diff touches plus affected heavy files, and `ruff`; CI does
   the rest. Mutation tables only for data-loss or cross-user guards.
4. Review only floor-class changes and gate files: one cross-family round
   (`peer-agents`), floor, correctness, Shape findings, `AGREE`/`DISAGREE_EVIDENCE`.
5. No lane cap: fold colliding or superseded lanes into one; serialize merges.
6. Done = sha asserted deployed, one real-user app pass, spec synced.
7. Same error three times, or the same finding twice: hand off, do not patch.
8. A new rule deletes an old one; a rule a script can enforce gets no line.

## Facts (numbers are cited repo-wide)

3. Vendor-neutral compute only — `PLAN.md`.
4. Gates default autonomously — `PLAN.md`.
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
16. Shape: the model sees read/write/edit/bash + `ta`, one agent definition for
    every provider; no vendor names, aliases or compat paths.
17. Shape: abilities and prompts are package files and skills, not Python
    strings; no platform editors or features. Breaking Shape is wrong even if
    green; delete the old path with its tests and docs.

Required CI excludes `.github/heavy-test-files.txt`; `heavy-tests` skips PRs.
Sandbox, filesystem or process-limit work needs `python scripts/linux_oracle.py`;
a skip is not a pass. No temp root inside the repo. Secrets:
`set -a; source scripts/load_secrets.sh; set +a`. Gates:
`docs/reference/executable-gates.md`.
