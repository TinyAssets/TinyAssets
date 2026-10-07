# The universe is the harness: design (pi-style primitives)

Design only. Sources: worktree `claude/universe-is-the-harness` @ a952254d (origin/main 005accfa + PR #3970), PR #3958 head (jail; OPEN, review verdict APPROVE at 2f8f690f), 2026-09-24. Token figures are chars/4 from FastMCP `list_tools()` run in the local venv (Windows) against that tree. Governing rules: PLAN Scoping Rules 1-5 (+ #3970 "The universe is the harness"), AGENTS Hard Rule 3 (vendor-neutral connections) and Hard Rule 15 (the platform has no LLM).

## 1. Current state inventory

### Served chat turn (`converse`)

`universe_intelligence.converse` (universe_intelligence.py:958-1128) builds a Python-assembled persona prompt (`_build_persona_system_prompt`, :318-548) and calls the writer with `_sandboxed_config` (:234-307).

- **Visitor or non-founder turn, or flag off:** `WebFetch` only (:79), plus a hand-kept denylist of about 40 builtins (:91-111). The denylist rots as the CLIs add tools.
- **Founder turn with `TINYASSETS_ENGINE_MCP_TOOLS` on (:200-215):** `WebFetch` plus the 10 handles in `SERVED_ENGINE_MCP_TOOLS` (served_tools.py:118-129): `read_graph get_status run_graph write_graph browse_commons read_commons_shape read_brain write_brain connect_compute source_channel`. `remix_shape` is registered but excluded.
  - Engine `read_graph` has 16 pinned targets plus `webhooks` (engine_mcp_server.py:247-255).
  - Engine `write_graph` has about 9 targets: branch create/patch, automation, connection, model_preferences, pending_request, receiver, output_link, run_file and webhook (:1342-2245).
- **File tools are denied everywhere** (`HOST_REACH_TOOLS`, providers/base.py:155-161). The code comment gives the reason: own-files access was "DEFERRED to an OS sandbox" (universe_intelligence.py:67-78).
- **Codex:** chat has shell disabled (codex_provider.py:770-777) and an empty tmpfs `/workspace` (:840-848).
- **Claude:** `--system-prompt` replaces the CLI default (claude_provider.py:633). Because it runs with `--setting-sources project` and cwd set to the universe (:550), a vendor-native `.claude/` inside a universe would load.
- **HTTP models:** the engine-owned loop `AgentTurnCoordinator` (agent_turn_coordinator.py:34) calls the same 10 handles via `engine_tool_client` (:1-30).
- **Second LLM call per founder turn:** `extract_learning` / `commit_learning` (:557-785) writes the brain through the governed `soul.edit` whitelist (universe_bundle.py:68-71).

**Measured size of a founder turn:** engine tool definitions ~14,300 tokens (`write_graph` ~9,500; `read_graph` ~1,640; `run_graph` ~990; `connect_compute` ~850), persona prompt ~1,070 on an empty universe (it grows with the grounding files), continuity and input-method blocks ~260. **Total before any user content: ~15.6k.**

For comparison, pi is under 1k in total (4 tools and a ~500-token prompt). A visitor prompt is about 290 tokens.

### Workflow nodes (graph_compiler.py:3584-3760)

- **`prompt_template` (:1243):** calls the provider with `HOST_REACH_TOOLS` denied (claude_provider.py:505-518).
- **`source_code` (:2196):** runs in `node_sandbox`, which is bwrap with no network and rlimits (node_sandbox.py:8-37, :215-241).
- **Platform access from a node:** only `invoke_mcp_action`, over an alias table of about 9 canonical actions (graph_compiler.py:1753-1790): goals/gates leaderboards, wiki reads, `dispatch.enqueue`, `deliver_output` and run files. Each must also appear in `tools_allowed` (:2073-2094).
- **Other node kinds:** the effect node `authenticated_external_call`, `invoke_branch` (:2960) and `await_branch_run` (:3341).

### Public connector (universe_server.py)

Seven handles: `read_graph` :482, `write_graph` :868, `run_graph` :1665, `read_page` :1829, `write_page` :1898, `converse` :2520, `get_status` :3684.

- Tool definitions total about 13,400 tokens. `write_graph` alone is about 7,450 tokens with 25 parameters.
- `read_graph` advertises 21 targets and routes about 27 (:760-790).
- `write_graph` advertises 9 targets and routes about 13 (:1508-1520), with about 25 distinct operations.
- `run_graph` adds `webhook_op` and `source_op` (:1653-1662).
- Four prompts: `control_station` ~8,100 tokens, `branch_design_guide` ~2,100, `extension_guide` ~720, `meet_universe` ~560. Server instructions are about 370 tokens.

### The universe on disk

- **Creation** (api/universe.py:5927 → `seed_okf_bundle`, universe_bundle.py:371) seeds a blank 13-file OKF bundle: `index log soul soul.edit identity founder orgchart projects goals body origin` plus `soul_versions/{index,0001}.md` (:12-15, :47-61).
- **Added later:** `voice.md` (persona.py:43), `config.yaml` (config.py:132), `canon/` (api/universe.py:5029), `workspaces/` (workspace_pool.py:120), and `.runtime/` holding `provider-launch-credentials/` (credential_vault.py:1419) and `provider-child/` (base.py:554). A real local universe has 17-19 entries.
- **Not in the folder:** branches, runs, automations, connections and conversations live in root DBs (`.tinyassets.db`, `.runs.db`, `.auth.db`). The wiki is global (storage/__init__.py:255).

**Harness parts today:** soul/persona = OKF files + `voice.md`; instructions = hardcoded Python ("How I remember", "How I ask", :436-532); agent skills = none (only Branch-carried skill snapshots, branches.py:74-95); prompt templates = chatbot-side only (api/prompts.py); extensions = none; workflows = DB rows.

**Seeding** is blank and generic. Nothing is copied from the founder's harness.

### The OS jail (PR #3958, `tinyassets/providers/provider_jail.py`)

**How it works.** Every provider spawned through `owned_process.aspawn_owned` inside `provider_launch_scope` is wrapped by `confine_launch` (:393-434). That runs bwrap with `--unshare-all --share-net --die-with-parent --new-session` (`jail_argv`, :346-390).

**Mounts** (`default_view`, :210-233): the owning universe read-write at its own path; an empty tmpfs over `.runtime/provider-launch-credentials` with only this launch's own snapshot bound back; the install tree and system paths read-only; a private `/tmp`, `/dev` and pid-namespace `/proc`.

**Refusals.** The launch fails closed without bwrap, on a non-Linux host, or with no owning universe. Adapters may narrow the view (`UniverseView`) but never widen it.

**What it makes safe:** another universe, `/data`, `/app` (the platform source) and the daemon's `/proc/*/environ` are invisible to anything the provider starts, including its hooks and shells. That is the cross-user floor.

**What it does NOT do:** set resource limits (no cgroup, rlimits or disk accounting); isolate the network (it shares the host's, loopback and metadata address included); or keep the launch credential away from any shell the CLI runs in the same jail.

## 2. Target shape: 4 tools + 3 platform verbs

**Agent tool definitions — the whole list:** `read`, `write`, `edit`, `bash` over the universe folder, which is mounted at `/u` in a tool jail.

- **The platform executes the tools, not the vendor CLI.** They go over the per-universe engine route that already serves MCP to both CLIs and the HTTP loop. Every adapter (claude -p, codex, OpenAI-compatible HTTP, a command adapter) sees the same surface, with no vendor tool lists (Hard Rule 3). The CLIs' own builtins stay off.
- **Budget:** base prompt ≤500 tokens and tool definitions ≤500 tokens.

**Platform verbs are one CLI, `ta`, on PATH inside the jail**, not extra tool definitions. `ta --help` gives progressive disclosure, which is pi's argument against MCP bloat. `ta` reaches the platform over a **per-universe unix socket** bind-mounted into the jail. The socket path is the identity, so there is no bearer token in the jail and no identity parameter to forge.

**`ta connect`** — `list`, `ask <shape>` (raises the app request tab: OAuth, key or endpoint), `call <conn> <method> <url> [body]`.
- *Why irreducible (rule 1):* the secret lives in the vault. Only the credential-blind proxy applies it (api_key_http_provider.py:1-30). A jailed process must never hold it, so files and shell cannot compose an authenticated call.
- *One shape:* "send with grant G" or "ask the user for grant G". Consent is the user's act, never the agent's.

**`ta run`** — `start <workflow> [inputs]`, `status` / `cancel <id>`, `schedule` / `unschedule <workflow> <trigger>`, `webhook mint|revoke`.
- *Why irreducible:* bash dies with the turn and on every deploy. Durable runs, triggers that fire while all devices are off, admission and budgets, and the run ledger all need a process that outlives the jail.
- *One shape:* (workflow, trigger, inputs) → run id.

**`ta commons`** — `search`, `get`, `pull <pkg> [path]`, `publish <paths> --name`, `send <node> <payload>` (cross-user node I/O).
- *Why irreducible:* it is the only sanctioned crossing of the user boundary the jail forbids. It carries moderation, attribution and lineage, and wraps foreign content as untrusted.
- *One shape:* a public copy in, a public copy out, and a typed message to an opened node.

**Everything else becomes files, bash or skills:**

| Today | Becomes |
|---|---|
| `read_brain`, `write_brain`, the learning 2nd LLM call, soul.edit governance, `soul_versions/` | `read` / `edit` on the brain files, with history from a per-turn auto-commit to a universe-local git repo (also the undo) |
| Persona Python (:318-548), the "How I remember" and "How I ask" strings | Seed `AGENTS.md`, `skills/remember`, `skills/ask-for-access` |
| Engine `write_graph` branch create/patch and its sanitizers (:835-1340; 37.6k-char description) | `write` / `edit` on `workflows/<name>.yaml`; `ta run start --dry-run` validates |
| `read_graph` branches, branch, graph, run_output, run_file, conversation | `cat` on `workflows/`, `runs/<id>/` and `conversations/*.jsonl`, which the platform materializes |
| `read_graph` runs, run, automations, status; `run_graph`; automation create, pause, resume, delete; webhook | `ta run …` |
| connections, pending_request ask, connect_http / extend_http, connect_compute, source_channel approve, compute, model_options | `ta connect …`. Destination consent folds into the grant ("full channel access is the default ask"). |
| model_preferences, agent_bindings | A `harness/models.yaml` preference file. Binding to a grant stays platform and is checked at call time. |
| browse_commons, read_commons_shape, remix_shape, `write_page scope=commons`, receiver, output_link, delivery | `ta commons …` |
| canon, universe wiki pages, goals, `voice.md` | Plain files: `wiki/`, `goals.md`, `AGENTS.md` |
| Node `invoke_mcp_action` aliases (graph_compiler.py:1753) | `ta` inside the node sandbox, with the socket bound and network still off |

**What stays on the public connector.** `converse` stays as the front door for browser-only users (rule 5). The other six handles become thin views over the same primitives:
- `read_page` / `write_page` → files under a universe path;
- `read_graph` / `write_graph` → files plus `ta connect`;
- `run_graph` → `ta run`.

The 7-handle canary (Hard Rule 11) is unchanged; only the target zoo goes.

## 3. Harness as files

```
<universe>/                 (rw at /u in the tool jail)
  AGENTS.md                 instructions + voice, always loaded (replaces persona Python + voice.md)
  SYSTEM.md                 optional: replaces the platform base prompt (pi semantics)
  identity.md founder.md origin.md body.md orgchart.md projects.md goals.md   the brain (OKF kept; now editable)
  skills/<name>/SKILL.md    frontmatter name+description; only the index sits in the prompt, the body is `read` on demand
  prompts/<name>.md         templates; the app composer offers "/name" and expands it into the turn
  extensions/<name>/extension.yaml   declares bin/ commands and hooks (turn_start, turn_end, run_done):
                            jailed processes, JSON on stdin, stdout may add turn context
  workflows/<name>.yaml     workflow definitions: the ONLY authority (the DB becomes a derived index)
  bin/ wiki/ notes/ …       anything the user builds
  runs/<id>/ conversations/ platform-written, readable by the agent
  harness.lock              lineage: which commons package@version seeded or updated which paths
  .runtime/                 platform-owned; masked out of the tool jail entirely
```

**Turn assembly is mechanical, with no LLM**, in this order: the base prompt (≤500 tokens: the four tools, the folder map, `ta --help`, the untrusted-envelope rule), which `SYSTEM.md` overrides if present → `AGENTS.md` → the skill index → the chosen prompt template → recent conversation as delimited untrusted context (as :1080-1097 already does).

The files are vendor-neutral and the platform assembles them for every adapter. Vendor-native `.claude/` and `.codex/` directories are never the loading mechanism.

**The seed harness** is the founder's *harness subtree only*: `AGENTS.md`, `SYSTEM.md`, `skills/`, `prompts/`, `extensions/`, `workflows/` and `harness/models.yaml`. Brain files, `wiki/`, `runs/` and `conversations/` are never part of it.

1. The founder publishes the subtree with `ta commons publish` as `starter/founder@vN`. It is public by definition (rule 4): a shape, not a service.
2. `create_universe` copies the starter package, writes `harness.lock`, and still seeds a blank brain. This extends `seed_okf_bundle`.
3. The starter is a config pointer (`TINYASSETS_STARTER_HARNESS=<pkg>@<ver>`), not code.
4. A new starter version never rewrites existing universes. `ta commons pull` shows a diff, and the user or agent applies it.

**Choosing the "most popular" starter later** is a mechanical ranking (Hard Rule 15) over commons lineage: count distinct universes whose `harness.lock` descends from the package and that had an owner turn in the last 28 days. That is retention-weighted adoption, not downloads. A package also needs clean moderation and a minimum age. The top package becomes the pointer. Until the metric has a track record, each pointer change is a `host-decision` row; after that it is automatic.

## 4. Safety

**Inside the universe is safe because the floor is cross-user only.** The jail shows the agent only its own universe, so shell and file power cannot reach another user or the platform. pi's point applies: once an agent can write and run code, permission prompts are theatre and containment is the real boundary. Own-universe mistakes, prompt injection included, are owner-level, and the per-turn git snapshot undoes them.

**The tool jail is stricter than the #3958 launch jail.** It reuses `jail_argv` with a narrowed `UniverseView`:
- **Filesystem:** the universe rw at `/u`, with `.runtime/` masked.
- **No credential snapshot.** The tools run in the platform's jail, not as children of a CLI that holds a token.
- **Environment:** `--clearenv`, and the `ta` socket bound in.
- **Resources:** a per-universe cgroup (memory.max, pids.max, cpu.weight), the node_sandbox rlimit profile (:215-241), and folder bytes charged to the tier quota (workspace_pool). On the shared 1 vCPU / 2 GB box a fork bomb or a memory spike is a cross-user outage, so this is floor, not policy.
- **Network is OFF in S1.** From S3 there is public egress only, through a netns (pasta or slirp) that refuses loopback, RFC 1918, link-local and the metadata address. `--share-net` would hand a shell the daemon's loopback routes and other universes' engine ports.

**Outside the universe, always:** credentials (in the vault, used only by `ta connect call` through the credential-blind proxy or by a model launch whose jail has no shell); other users' data (reachable only as commons copies wrapped as untrusted); and the platform itself (source, DBs, secrets, scheduler, ledger, identity, moderation).

**Visitors are other users.** A T1 visitor turn gets a `public/` view, read-only, with no bash writes and no `ta connect`. This replaces the prompt-time disclosure filter (`interlocutor.permitted_grounding_files`) with the jail mechanism.

**Publishing is guarded.** `ta commons publish` takes explicit paths only. It refuses the brain files, `.runtime/`, `conversations/` and anything the secret scanner flags. Pulled content is code the owner chose to run in their own jail. The platform supplies provenance, lineage, diff-before-apply and moderation, not a nested sandbox.

## 5. Three components

| | 1. Universe (user-owned) | 2. Platform | 3. Commons |
|---|---|---|---|
| Is | The folder: harness files, brain, workflows, bin, data | Identity, the vault and connections, routing to the user's own LLM, the jail, cgroups and egress, durable runs and triggers, quota, moderation, the `ta` socket, the `converse` relay | Published packages (skills, prompts, extensions, workflows, starters) plus nodes open to cross-user input |
| Changed by | The user or their agent, by editing files | Platform code; the user configures it through grants and `ta` | `ta commons publish`, then moderation |
| Crosses users? | Never | Enforces that nothing does | The only thing that does, as copies or typed messages |
| Placement test | Could a user replace it by editing a file? | Would a wrong edit break the floor, or does it need a secret or durability? | Is it meant for other people? |

## 6. Migration: live slices in dependency order

**Gate for every slice:** #3958 merged and deployed (`deployed_sha.py --assert-contains`). Each slice ships dark on the founder's universe first, then becomes the default.

**S1. Four tools in the owner's own jailed folder. This is the shape proof.**
- **Build:** platform-executed `read`/`write`/`edit`/`bash` for the founder's served turn. The tool jail has no network and no credential, has cgroup + rlimits, and masks `.runtime`. The base prompt lists the `skills/*/SKILL.md` descriptions. The existing 10 handles stay for now.
- **Evidence:** an OpenSpec change (authority), plus `linux-jail-proof` cases for foreign reads, `/app`, `/proc/1/environ`, loopback, `.runtime`, a contained fork bomb and memory.max.
- **Live acceptance (app):** the founder types "make yourself a skill: when I say 'standup', answer with Yesterday/Today/Blockers bullets" → `skills/standup/SKILL.md` appears → in a **new** turn "standup" gets that format → the founder has it `cat` then delete the file, and the next "standup" gets a plain reply. `ls /data`, `cat /proc/1/environ` and `ls ..` fail.

**S2. `AGENTS.md` is the persona, and the agent writes its own brain.**
- **Build:** move the persona text into seed `AGENTS.md`, `skills/remember` and `skills/ask-for-access`. Delete `extract_learning`/`commit_learning`, `read_brain`/`write_brain` and soul.edit governance for owner turns. Add the per-turn git auto-commit.
- **Live acceptance:** a fact the founder states lands in `identity.md` in the same turn (visible in the app's file view), and another surface recalls it. "Talk tersely from now on" makes the agent edit `AGENTS.md`, and the next turn is terse. Measured tokens: target under 2k, down from ~15.6k.

**S3. `ta connect` plus egress-filtered network.**
- **Build:** `list`, `ask` and `call` through the socket and the proxy. Bash gets filtered public egress. Retire the connection, pending_request, connect_compute and source_channel handles.
- **Live acceptance:** the agent needs GitHub and raises an app tab; the founder approves once; the agent opens a real PR with `ta connect call`. Grepping `/u` for the token finds nothing, and loopback and metadata are refused.

**S4. Workflows are files, run by `ta run`.**
- **Build:** `workflows/*.yaml` becomes the authority. Runs pin the file's content hash and the DB becomes a derived index. Outputs go to `runs/<id>/`.
- **Evidence:** an OpenSpec change (storage shape).
- **Live acceptance (phone app):** "every morning at 7 put an HN summary in notes/hn.md" → the agent writes the yaml and schedules it → with the founder's devices off, the file exists next morning → "make it 5 items" gets a yaml edit, and the next run obeys.

**S5. Workflow nodes get the same harness.**
- **Build:** `prompt_template` nodes may declare `harness: true` to get the four tools in the owner's tool jail. `source_code` nodes call `ta`. Delete `_NODE_MCP_ACTION_ALIASES`.
- **Live acceptance:** a scheduled workflow's LLM node edits `notes/todo.md` using a skill, and the edit shows in the app.

**S6. `ta commons` packages.**
- **Build:** `publish` (path allowlist, secret scan, moderation); `search`, `get` and `pull` (diff, `harness.lock` lineage, attribution); `send` to opened nodes. Retire the browse, read and remix handles and the receiver, output_link and delivery targets.
- **Evidence:** an OpenSpec change (cross-user authority).
- **Live acceptance:** the founder publishes `skills/standup`; a second real account pulls it through its own app and "standup" works there; attribution is recorded; publishing `founder.md` is refused.

**S7. Seed new universes from the founder's harness.**
- **Build:** publish `starter/founder@v1`. `create_universe` copies it and writes `harness.lock`; the brain stays blank.
- **Live acceptance:** a brand-new account signs up in the app; its folder has the founder's `AGENTS.md`, skills and workflows, and its first turn follows them; none of the founder's brain or conversations exist there.

**S8. Collapse the bespoke surface, and pick the starter by popularity.**
- **Build:** reduce connector targets to file, run, connect and commons views (an OpenSpec change, plus the `--assert-handles` canary). Delete the bespoke engine handlers. The adoption ranking drives the starter pointer.
- **Live acceptance:** `ui-test` on both Claude.ai and ChatGPT. A browser-only user builds and schedules a workflow and installs a commons skill, all through `converse`.

## 7. What gets deleted, and the risks

**Deleted** (roughly 6k lines, and about 27k tokens of tool definitions: ~14.3k served plus ~13.4k connector, plus most of `control_station`'s ~8.1k):
- `served_tools.py`.
- Most of `engine_mcp_server.py` (3,189 lines): the sanitizers (:763-1340), the brain handlers (:2699-2895), `connect_compute` (:2896) and `source_channel` (:3013).
- In `universe_intelligence.py`: the denylists (:79-111, :195), persona assembly (:318-548) and the learning path (:557-785).
- The soul.edit whitelist and `soul_versions/`; `voice.md` folds into `AGENTS.md`.
- Codex `sandbox_chat` (:840-848).
- The node alias table.
- The branch DB as authority.
- About 20 connector targets and 20 operations.

**Risks:**
1. **Credential exposure through CLI-native tools.** If a CLI's own Bash runs in the #3958 launch jail, it can read the owner's subscription snapshot, and injected text could exfiltrate it. *Mitigation:* tools are always platform-executed in the credential-free tool jail, and the CLI builtins stay denied. *Residual:* a command adapter (claude -p for a Claude subscription) still holds its token in a shell-less launch jail.
2. **SSRF through `--share-net`.** It reaches loopback and metadata. S1 therefore has no network, and S3 gates network on the filtered netns.
3. **Resource starvation is cross-user.** The jail has no cgroup today, and memory is the capacity limit. This is an S1 prerequisite, not hardening.
4. **Two authorities for one fact.** Workflow file vs DB row, and brain files vs the old soul API. Delete the second definition in the same slice, and never sync both ways.
5. **Weak models with raw tools.** Free OpenRouter models may misuse bash or skip writing the brain. *Mitigation:* seed skills and live measurement per model family, not keeping typed handles as a crutch.
6. **Deploys kill in-flight turns, and so long bash jobs.** Long work belongs in `ta run`, and the seed `AGENTS.md` says so.
7. **The jail is Linux-only**, so Windows and macOS trays refuse jailed launches. A single-tenant tray has no other users, so this needs a decision: file a `docs/concerns/` entry, do not block.
8. **A vendor-native harness leak.** `--setting-sources project` with cwd set to the universe would load a user's `.claude/` hooks. That is safe in the jail but is a second, vendor-specific harness path (Hard Rule 3). Mask it from launch views; decide in S1.
9. **Accidental publication of private content** (S6/S7). Explicit paths, brain refusal, the secret scan and a preview; the founder reviews starter v1.
10. **Browser-only parity (rule 5).** Every step must work via `converse` plus the app's file view. `ui-test` is the proof for S2, S4 and S8.
