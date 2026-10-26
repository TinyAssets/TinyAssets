# TinyAssets

<!-- direction:start -->
## Direction

The founder owns this section. To re-steer, replace a line; never add one beside it.
`python scripts/sync_direction.py` copies it verbatim into `AGENTS.md`.

1. Product: a default bubble agent that continuously matches and beats the industry-leading main agents (Grok Bot, Muse, ChatGPT dots, Claude Duet), built as a starter package on a pi.dev-modelled harness; users customize the harness and interface to build anything.
2. Plumbing: the model sees 4 tools (read/write/edit/bash) plus `ta`; one extension unit; one agent definition for every model provider; no provider-specific code.
3. The agent's abilities are editable files, skills and packages. The platform does not build feature editors or pre-built features.
4. Users connect any model source; default to their strongest connected source.
5. Foundational patches are brought FORWARD, so pre-migration band-aids never delay positive architectural moves. Weigh the totality of pending work, so each module is always pursuing or maintaining its best architecture, and refactor and reorder the remaining work to get there. (Today that foundation is per-owner isolation, as one clean cutover: the isolated path is the only path, one migration in a short maintenance window after a full backup, and cross-owner app checks the OS now enforces are deleted in the same change.)
6. Ship live fast and verify live. LESS process: delete stale docs, tests and notes rather than adding more.
7. Long-term goal: in both the Google Play and Apple App Store, and listed in the major agents' connector directories (Meta Muse, ChatGPT, Claude) so users find TinyAssets by search, not only as a custom connector, with growing downloads and positive reviews.
8. 24/7 uptime with zero hosts online: every surface works with no host machine on.

No longer the direction: "a global goals engine", Goal ladders, and fantasy as the default domain.
Where any older doc disagrees with this section, this section wins.
<!-- direction:end -->

Map, why: `docs/architecture.md`, `docs/decisions/`.

## Facts (numbers are cited repo-wide)

1. `SqliteSaver`, never `AsyncSqliteSaver`.
2. Reuse the LanceDB connection; never recreate it.
4. Gates (`docs/reference/executable-gates.md`) default autonomously; ask only
   for new spending, irreversible acts, or outward-facing acts outside the
   agreed work (host-only: a host-actions row that blocks nothing).
5. Accumulating state needs `Annotated[list, operator.add]`.
6. `FactWithContext` carries truth-value typing.
8. Fail loudly; a real-looking mock fallback is worse than a crash.
9. Uploads are verbatim.
10. Ship-time attribution — `tinyassets/attribution/`.
11. Doors: `tinyassets.io/mcp`, `/api` (keyed), `/hooks/inbound`; `mcp.`
    internal; after DNS/tunnel/connector edits, run the handles canary.
13. Prove a path is pushed or in history before destroying it; never switch
    a dirty worktree to `main`.

Sandbox, filesystem or process-limit work needs `scripts/linux_oracle.py`;
a skip is not a pass. Secrets: `set -a; source scripts/load_secrets.sh;
set +a`.
