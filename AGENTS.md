# TinyAssets

<!-- direction:start -->
## Direction

The founder owns this section. To re-steer, replace a line; never add one beside it.
`python scripts/sync_direction.py` copies it verbatim into `AGENTS.md`.

1. Product: a default bubble agent that continuously matches and beats the industry-leading main agents (Grok Bot, Muse, ChatGPT dots, Claude Duet), shipped as a starter package of agent files that runs on the user's own engine: their provider's unmodified CLI on their subscription or key, or their own main agent; users customize the agent's files and interface to build anything.
2. Plumbing: an agent is files (`AGENTS.md`, skills, MCP config, hooks) plus TinyAssets state, run by an unmodified engine (a provider's CLI in the user's sandbox, the user's main agent over MCP, or pi's CLI); TinyAssets is everything around the engine and reaches every engine through one MCP door; one agent definition for every engine; no provider-specific code: engine differences are data.
3. The agent's abilities are editable files, skills and packages. The platform does not build feature editors or pre-built features.
4. Users connect any model source; default to their strongest connected source.
5. Foundational patches are brought FORWARD, so pre-migration band-aids never delay positive architectural moves. Weigh the totality of pending work, so each module is always pursuing or maintaining its best architecture, and refactor and reorder the remaining work to get there. (Today that foundation is the clean-sheet platform, as one clean cutover: the clean-sheet platform is the only path, one migration in a short maintenance window after a full backup, and the old stack, with its docs, skills and rules, is deleted in the same change.)
6. Ship live fast and verify live. LESS process: delete stale docs, tests and notes rather than adding more.
7. Long-term goal: in both the Google Play and Apple App Store, and listed in the major agents' connector directories (Meta Muse, ChatGPT, Claude) so users find TinyAssets by search, not only as a custom connector, with growing downloads and positive reviews.
8. 24/7 uptime with zero hosts online: every surface works with no host machine on.

No longer the direction: "a global goals engine", Goal ladders, and fantasy as the default domain.
Where any older doc disagrees with this section, this section wins.
<!-- direction:end -->

Map, why: `docs/architecture.md`, `docs/decisions/`.

`platform/` is the build (ADR-032 to ADR-037). The old Python stack
(`tinyassets/`, `deploy/`, the droplet) is retiring: fixes only, deleted at the
cutover.

## Facts (numbers are cited repo-wide)

4. Gates (`docs/reference/executable-gates.md`) default autonomously; ask only
   for new spending, irreversible acts, or outward-facing acts outside the
   agreed work (host-only: a host-actions row that blocks nothing).
8. Fail loudly; a real-looking mock fallback is worse than a crash.
9. Uploads are verbatim.
11. Doors: `tinyassets.io/mcp`, `/api` (keyed), `/hooks/inbound`. Until the
    cutover's `routes` run, the old proxy serves them (`mcp.` internal); after
    it, the doors Worker does (`platform/deploy/deploy.ts` `ROUTES`), and
    `egress.tinyassets.io` is the sandbox's door. After DNS, route or
    connector edits, run the handles canary.
13. Prove a path is pushed or in history before destroying it; never switch
    a dirty worktree to `main`.
14. Research comes before building, as its own step. How to do a thing is never
    decided before its research, nor left to a builder to settle: the design
    is chosen from the research, checked against the intent, then built.
    Research again when a build grows past its plan, or when it needs patches
    that suggest a bad shape (founder, 2026-11-03).

Sandbox proof: `npm run test:root -w sandbox`; memory proof: `npm run
test:memory -w owner` (in `platform/`). A skip is not a pass. Platform secrets live
only in the GitHub `production` environment; its tests need none.
