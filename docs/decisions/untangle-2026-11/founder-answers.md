# founder-answers as built (2026-11-21)

`untangle/founder-answers`, doc only. The conformance check read the platform against the
Direction, the Facts, ADR-032 to ADR-037, the founder's Clean-Sheet Architecture doc and
`main-agents.md`. One mapper wrote the rows; four verifiers then tried to prove each area wrong;
the coordinator checked the top findings in the code. The founder's page (2026-11-21) held six
shortfalls, the clean break's gaps and 18 calls, each with a recommendation. The founder answered
"as you recommend for all those questions", so every recommendation below is decided. Where an
item needs work, `tasks.md` holds it with its branch, in the step it belongs to.

## The intent, and the cutover rule

- The founder's intent, verbatim: "making sure we actually are doing what we set out to do... is
  kinda the entire point of all this so get it right".
- **Every shape fix stays before the cutover, internal code shape included.** This covers shims,
  copies, test-only surface, special cases and wrong data shapes. Only cosmetic passes go after:
  comments, titles and test placement (`passes-after-cutover.md`). When a split of the shape fixes
  was proposed, the founder answered "No dont make that split" (2026-11-21), so it is not proposed
  again. The untangle skill's cutover rule still allows its one exception: a fix for a rare,
  loud, recoverable failure that nothing builds on.

## The shortfalls

1. **Direction 4, "strongest".** A plan reached the ranking with no model. Built: #5723 has each
   sign-in record declare its plan's model. The rest is call 4.
2. **Owner isolation was by convention.** Built: #5724. The sandbox Worker acts only for its own
   binding's owner. The files Worker is left as it is, for the reason `owner-isolation.md` §1
   records. Call 16's postmaster is the other half.
3. **The agent's own files do not fully govern its runs.** The record's instruction fields are
   read by nothing: a stray `CLAUDE.md` replaces `AGENTS.md` on Claude Code, and Codex cuts
   `AGENTS.md` past 32 KiB. These become real (`untangle/record-instructions`). The door's fixed
   main-agent text is call 1; hooks are call 2; MCP servers are call 3; packages are call 9.
4. **"Matches and beats" has no measure.** The cutover's bar (below) puts the missing capabilities
   after the cutover. The standing comparison stays in Step 19.
5. **Promises the ADRs and the Clean-Sheet doc make but the code does not keep.** All five are
   built before the cutover:
   - ADR-034.7's stop on a vendor's rate-limit or billing error (`untangle/vendor-limit-stop`);
   - code mode's default (call 10);
   - the canary's full run: a bash call, a TinyAssets MCP call and the run's receipts read
     (`untangle/canary-full-run`);
   - gradual deploys (call 15);
   - Muse's key on `/mcp` (call 6).

   Fact 15's caps are call 11. Every cap in code is tied to `limits-audit.md` by a guard
   (`untangle/limits-guard`).
6. **Engines are proven only in pieces.** The pair check's capture server answers the run's MCP
   door, so every pin proves its record's MCP leg (`untangle/probe-mcp-leg`). Kimi is call 17.

## The calls, as decided

1. **The user's own main agent is covered by "one agent definition".** The door serves the
   owner's `AGENTS.md` and skills as MCP prompts and resources that the client loads. They are not
   tool text that steers. This replaces the fixed `CLIENT_INSTRUCTIONS` and the starter's paths
   restated in platform code. It reverses d3 §A9 ("B20 doesn't make a workspace file an
   instruction"), which now carries a correction line. ADR-034 decisions 1.2 and 2 gain a Status
   line. Branch: `untangle/main-agent-definition`, research first: which main agents load
   prompts and resources, and under what rules.
2. **One hook definition.** The agent's files hold one hook definition. Each engine's record
   renders it into that engine's format as data. R5's root-owned hooks always win (#5707 built
   them). This amends ADR-034 decision 11, "hooks are the engine's own", and decision 2's "in the
   formats these CLIs read" for hooks. Both gain a Status line. Branch: `untangle/agent-hooks`.
3. **The agent's own MCP servers run.** They are merged with TinyAssets' door and limited to the
   hosts the egress allowlist permits. Each engine is researched first (Claude's
   `--strict-mcp-config`, Gemini's allow-list). This moves Step 19's "connectors for the owner's
   own agents" before the cutover. Branch: `untangle/agent-mcp`.
4. **"Strongest" is a ranking shipped as data, which the owner can override in a file.** A plan
   beats another provider's key when its model ranks higher. #5723 built that ranking by the
   plan's model. The coordinator's record adds that a plan whose model is in the ranking's top
   tier wins over a metered key. The ranking's `about` stops claiming that a package keeps it
   current. Branch: `untangle/strongest-ranking`.
5. **An endpoint the owner declares is a model source.** It is an owner-written `custom` record
   (`curated: false`), held by the same record check, with a door to add it. The gateway already
   seals one. Branch: `untangle/custom-source`.
6. **Muse connects both ways.** It uses a key on `/mcp`, as ADR-028 says, through the `/api`
   door's own key check, so one handler serves keys and OAuth alike. It also uses OAuth, which
   the directory listings need anyway. `apps.json` and `wake-kinds.json` are made to agree.
   Branch: `untangle/mcp-keys`.
7. **"Customize the interface" includes the owner app,** through ADR-029's app manifest, after
   the cutover (S6, Step 20). Published user apps must not go dark at `routes`, so S6's app serving
   comes before the cutover. Branch: `untangle/s6-app-serving`.
8. **The "New agent" form becomes a template picker** that writes the agent's files. Its UI-only
   name rule (`AGENT_NAME`) goes with it. Branch: `untangle/template-picker`, on call 9's
   format.
9. **A package is the starter format generalized:** a folder of agent files plus a manifest. Any
   owner can publish one, and installing it copies it. The format and install by copy come before
   the cutover, under the bar below. Branch: `untangle/packages`.
10. **Code mode is off by default until its isolation probe (H2) passes,** as the Clean-Sheet doc
    says. It is turned on by a later change, only on H2's pass. Branch: `untangle/codemode-off`.
11. **Fact 15:**
    - **Retentions** become owner settings, where off means keep forever, unless a ceiling is
      recorded with its derivation. `limits-audit.md`'s "Chosen retentions" list goes. This
      amends ADR-032 decision 5's kept versions and supersedes ADR-035 decision 5's
      collection-history retention. Both gain a Status line.
    - **`client_share` and `owner_reserve`** are off by default.
    - **The Clean-Sheet doc's per-agent run, sandbox-second and wake caps** are dropped. Only
      vendors' limits apply. None was built, so no code changes.
    - **The shares of the 10 GB guard** are technical ceilings, each with its reason.
    - The caps outside Fact 15's kinds become one of them: `openPerRun`, code mode's limits
      taken from pi's defaults, and the 300 s idle cut, made data per engine.

    Branches: `untangle/fact15-defaults` and `untangle/fact15-caps`.
12. **The tier's 5 simultaneous runs are a promise.** Raising E2B's tier is spending, so the
    founder decides it when the canary's E2B metrics near the ceiling. Until then, a run past the
    ceiling waits with a clear message. Branch: `untangle/runs-wait-message`.
13. **An unreadable control record is served from a last-good copy.** Only writes that need a
    fresh copy are refused. This amends ADR-032 decision 2's "fail-closed `control` record", and
    ADR-032 gains a Status line. Branch: `untangle/control-last-good`.
14. **GitHub is an acceptable home for the kill switch and all monitoring,** since no host of
    ours is involved. A Cloudflare-side kill switch is added as a second path. Branch:
    `untangle/kill-switch-cloudflare`.
15. **Gradual deploys are built before the cutover,** with Workers' gradual deployments. The
    canary is the promote gate, and a rollback mode switches back to the previous version, except
    across a Durable Object migration. Branch: `untangle/gradual-deploy`.
16. **Cross-owner traffic goes through ADR-032's postmaster.** It is decided with the isolation
    fix, since both decide who may reach whom. #5724 landed the isolation half first. The
    postmaster follows, researched first, before the cutover. Branch: `untangle/postmaster`.
17. **Kimi's record is dropped** until it has a usable path, together with its lock, its template
    install, its probes and its plan lines. Branch: `untangle/drop-kimi`.
18. **The clean break:**
    - **Old ADRs.** ADR-001 to ADR-031 are marked superseded by ADR-032, and the principles
      ADR-032 says stand are kept. The answers that amend an ADR also go into an ADR, so they
      outlive this log. Branch: `untangle/old-adrs`.
    - **`ideas/`, `output/`, `templates/` and the untangle logs** are deleted at the cutover; git
      keeps the history. `untangle/manifest-content` (#5725) recorded them as keeps pending this
      answer, so they become deletes. Branch: `untangle/manifest-history`.
    - **No verbatim old-store archives in the workspace.** Owners get an export download during
      the hold instead. Branch: `untangle/import-export-download`.
    - **The Shape registry's founder rows** are carried into the plan before
      `docs/architecture.md`'s registry is deleted. Done here, in `tasks.md` Step 20.
    - **The old names are renamed for what they now mean:** `read_graph`, `write_graph`,
      `run_graph`, the message ledger's Python columns, `turn_*`, `pkce_user_key_v1`, and the
      salts and limits whose only reason is the Python. "Command center" is dropped, unless
      research shows it is a real platform namespace. Branches: `untangle/old-names` and
      `untangle/ledger-shape`, before real data.
    - **The site and the shells** are updated in the cutover: the site PR in Step 5, and the
      shells' old desktop sign-in return and copy (`untangle/shells-cutover`).
    - **The Direction text that goes stale at the cutover:** the coordinator drafts the
      replacement lines for the founder to approve. The draft is not in the repository, since the
      founder owns the block.

Later answers on the same page:
- **The dark deploy** runs as soon as the stage PRs land. A redeploy follows before the founder's
  test (live action approved).
- **The cutover's bar is that the shapes exist.** That means the package format, and a connector
  credential path that needs no host of ours (research: E2B's per-host header transforms, which
  already carry the egress door's identity). Missing capabilities can follow. This answers
  `tasks.md`'s founder question 3 for the shape. Branch: `untangle/connector-credentials`.

## Also decided ("now, because each was already decided")

- The commitments-to-proof table: one row per commitment with its test or live check, updated by
  every PR that touches a commitment and checked by reviewers. The last gate before the cutover is
  that every row is proven. Branch: `untangle/commitments-table`.
- The verifiers' smaller shape fixes, each before the cutover. `tasks.md` Step 5 lists them with
  their branches.

## Supersessions made in this change

- ADR-032's Status line: decision 2's control record (call 13) and decision 5's kept versions
  (call 11).
- ADR-034's Status line: decisions 1.2 and 2 (call 1), decisions 2 and 11 (calls 2 and 3).
- ADR-035's Status line: decision 5's collection-history retention (call 11).
- d3 §A9, corrected in place (call 1).

No ADR body is edited (ADR-005). The founder's Clean-Sheet doc is his own, so it is not edited
here. Its per-agent caps, its "sandbox-seconds … capped by plan" and its wake caps of "12 an
hour by default" are superseded by call 11.
