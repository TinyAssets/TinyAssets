# Tasks — byo-llm-connect-flow

Generic for every user; no special path for the dogfood universe. Security
substrate — Codex-built, dual-family reviewed before any live rollout. The Slack
recall+reply proof completes once slice 1 lands.

## Slice 1 — mint + wire a requester-owned chat/completion provider binding
- [x] 1.1 Add a way to MINT a general-purpose chat/completion `provider_binding`
      (agent/host *serving*, not automation-shaped) from a requester-owned
      credential already present in a universe's vault. Requester-owned +
      forgery-proof (authority derived from server-held state, per keystone #2399,
      not caller-asserted payload).
- [x] 1.2 Wire a universe's `agent_binding.provider_ref` to a real minted binding,
      replacing a placeholder / mis-scoped ref; reject binding to a provider_binding
      whose `allowed_operations` does not include chat/completion.
- [x] 1.3 Host serving path: a controlled write-path so a universe with a connected
      chat/completion binding actually serves conversational turns (the flag the
      connector has no path to today) — fail-closed when absent.
- [ ] 1.4 Reset the drift left by the 2026-08-10 live diagnosis:
      `agent_binding_01kz0k6mwe61a0ph60a2hzp01x` points at the GitHub grant
      `pwb_fbddd0e8…` (grants no authority); repoint at its real minted binding.
- [x] 1.5 Tests: minting is requester-owned + rejects non-chat/completion scopes;
      wiring rejects mis-scoped refs; a served turn resolves the universe's own
      binding and never an ambient host credential. Mirror parity rebuilt.
- [ ] 1.6 Live proof: u-tiny/Demo App serves a real Slack reply; the durable-memory
      recall+reply proof (store advances past 130, recalls prior context) completes.
- [ ] 1.7 COMPLIANCE GATE (Codex ADAPT verdict 2026-08-10, verified w/ sources):
      Claude-SUBSCRIPTION serving-binding minting must be gated to the
      founder/dogfood identity — a multi-user platform holding customers' Claude
      Pro/Max credentials is prohibited without Anthropic approval. Codex/ChatGPT
      minting may be customer-facing (integrate via the official Codex
      SDK/app-server; never raw token extraction). Claude's customer-facing 24/7
      route is a HOST DECISION: Anthropic partnership vs provider-native delegated
      credential vs market/cloud-executor (API-key paste conflicts with
      `retire-mcp-provider-secret-deposit` and cannot power zero-host-online
      heartbeats). See docs/design-notes/2026-08-10-cloud-brain-client-inference-options.md
      addendum.
- [ ] 1.8 VAULT CUSTODY SEALING (Codex structures-review 2026-08-10): the credential
      vault persists recoverable cleartext with best-effort file modes only
      (openspec/specs/credential-vault/spec.md:47 area). Before ANY customer token
      intake could exist, secrets must be sealed from the operator (encryption at
      rest + key separation). Also: the slice branch's Claude rejection at
      provider_serving_binding.py:196 is technical role-coverage, NOT the 1.7
      compliance gate — 1.7 still needs explicit implementation.
- [ ] 1.9 Provider-neutral USER-EXECUTOR tier (from the structures + architecture
      research, Codex-adapted): a user-procured/user-controlled executor (their
      device, their VPS, Tier-2 tray) registers via a distinct registration+lease
      object and executes leased turns on the user's own subscription locally;
      cloud brain re-authorizes and commits effects. This is the ALLOWED-today
      path for customer Claude; hosted customer Claude custody requires WRITTEN
      Anthropic approval first (contact-sales inquiry drafted in
      docs/design-notes/2026-08-10-anthropic-subscription-structures.md).
- [ ] 1.10 AGGREGATE PER-ACCOUNT BUDGETS (precedents review, final ladder): one
      identity/concurrency/run/token budget per PROVIDER ACCOUNT across ALL of a
      user's universes — not merely per-credential — plus Routines-benchmarked
      scheduled-start caps (Pro 5 / Max 15 / Team-Ent 25 as the conservative
      envelope) and concurrency/wall-clock/rolling-budget limits.
- [ ] 1.11 Evaluate Anthropic FIRST-PARTY hosted rails for customer Claude:
      Managed Agents and Workload Identity Federation as TinyAssets-hosted
      customer paths that never take custody of a subscription credential; the
      founder-only hosted-subscription adapter stays behind a fail-closed flag.
      FOUNDER DECISION 2026-08-10: onboarding is NOT gated on the inquiry/approval
      — build intentionally to the allowed patterns (live-precedent playbook) and
      proceed; the contact-sales inquiry stays drafted, DEFERRED until design
      stabilizes + scale warrants. Playbook guardrails are engineering
      requirements, not launch gates.

## Slice 2 — one-click provider-OAuth connect UX + vault capture
- [ ] 2.1 "Connect Claude" / "Connect ChatGPT/Codex" federating to each provider's
      own OAuth/device-flow. RECONCILED with `byo-llm-deposit-surface` (R2-item7):
      the OAuth/device flow OBTAINS the credential, then federates it into
      `byo-llm-deposit-surface`'s `llm_deposit` handler for the owner-scoped
      `llm_subscription` vault write — this slice does NOT write the vault itself, so
      there is exactly one canonical `llm_subscription` writer. The `llm_api_key`
      paste is separately scoped (retired for MCP deposit by
      `retire-mcp-provider-secret-deposit`), not part of this OAuth capture.
- [ ] 2.2 A `connections` entry per connected provider (Anthropic/OpenAI), surfaced
      in `read_graph target=connections`; connect once → mint bindings (slice 1) from it.
- [ ] 2.3 Generic "point this universe at its GitHub project" binding — available to
      any user, no special path — so a universe has both its provider and its repo.
- [ ] 2.4 `ui-test`: a user connects a subscription through the chatbot and the
      universe becomes runnable, end to end.

## Slice 3 — prune every non-intended LLM route + guard
- [ ] 3.1 Remove host-writer / writer-"fleet" / `fantasy_daemon` LLM-provisioning
      code and the ambient host-credential fallback in `providers/base.py`
      (`CLAUDE_CODE_OAUTH_TOKEN`/`CODEX_HOME` borrow) + any platform-supplies-an-LLM
      branch. The connect flow (slices 1–2) is the ONLY surviving LLM route.
- [ ] 3.2 GUARD test: assert no code path can reach an LLM without a
      universe-authorized, requester-owned provider — fail-closed everywhere, no
      ambient borrow. Differential/grep-based enforcement, not just a happy-path test.
- [ ] 3.3 Delta-spec `provider-routing`: sole-path resolution + fail-closed
      requirements + scenarios; sync into `openspec/specs/` on land.

## Cross-cutting
- [ ] 4.1 Dual-family (Claude + Codex, latest models) review BEFORE any live rollout
      of each slice; leave a durable review artifact.
- [ ] 4.2 No platform-supplied-LLM path may survive slice 3 — verify against code.
