# Starter agent: Muse fit on pi plumbing

Date: 2026-10-06. Founder direction: the platform plumbing is pi.dev-minimal
(four model-visible tools, `ta` for breadth, one extension unit), but **the default
bubble starter agent is a full agent modeled on Meta Muse**. Its Muse-level
abilities ship as the starter's *package*: editable files, skills, extensions,
connections and a default command-center layout, all built on the primitives. The
platform does not hard-code them. A user can change, replace or extend any of it.

Rule for every row below: the capability lives in the starter package (or a
primitive the package uses), never in platform-only code, and the resident prompt
stays small because the package is loaded on demand.

Sources: Meta announcement, 2026-09-08
(about.fb.com/news/2026/09/introducing-muse-personal-ai-agent); CNN hands-on,
2026-09-23; Connect 2026 coverage (theaiinsider.tech, 2026-09-25); the VM tab,
voice widget and takeover in test builds (progressiverobot.com, 2026-09-27); the
Muse research notes of 2026-10-04 in this folder.

## Muse capability -> starter package -> status

| Muse capability | Where it lives in our shape | Status 2026-10-06 |
|---|---|---|
| Chat agent that plans multi-step tasks and picks its skills | Starter AGENTS.md + on-demand skills; `ta search` for breadth | Present; resident prompt too large (K2) |
| Asks before anything sensitive (once / task / site / always), records what it did | Protected approval sheet + rules (primitive) | Present (#4469, #4483) |
| Connectors directory; "connect my X" in chat | Directory-as-data + connect sheet (primitive) | Present for Google (#4495); others via MCP |
| Custom connector from an MCP link (OAuth sign-in link) | One extension unit: MCP contribution (K1) | K1 #4519 ports remote HTTPS/OAuth onto today's broker; only stdio needs U1 |
| Agent writes its own connector for any API | One extension unit authored by the agent (K1) | K1 #4519 implements pinned tools plus local credential slots; draft, not deployed |
| Browser fallback in a cloud computer; watch / take over / stop; injected logins | Browser primitive in the owner's cell; starter skill for when to use it | On hold (#4512); needs isolation |
| Persistent cloud computer ("VM tab": watch the agent's machine) | The owner's box (persistent /u, bash, git) + a starter view of it | Box partial (#4485 merged); no live view |
| Keeps working after the app closes; monitors; reminders; scheduled checks | Workflows/automations (primitive) + starter skills ("watch X, tell me if Y") | Present (scheduled notes work) |
| Notifies when something meaningfully changes | Notify primitive (#4488) used by starter skills | Present |
| Proactivity dial (off / low / high), suggestions nobody asked for | A starter setting file + skill that reads it | Missing (starter package) |
| Remembers preferences across sessions; editable; "forget X" | MEMORY.md file the agent edits; any view is a package UI | Present (files; #4505 view) |
| Goals list tracked from conversations | Starter goals file + skill + default layout view | Missing (starter package) |
| Feed of interests/priorities; Ideas tab of prompt examples | Default command-center layout (app_ui extension) over files | Missing (starter package) |
| Files & media | Agent files + download chips (#4489) + a layout view | Partial |
| Generates documents and images | Documents: files. Images: a connected image model via `ta` | Documents present; images missing |
| Voice conversation while it works (call-like widget; custom voice) | Voice in every command center (baseline) + starter defaults | Partial (voice exists; no call-style widget) |
| Name and style chosen at first run | identity.md / soul.md the agent writes during onboarding | Present (files) |
| Own email address for the agent | A connection/extension (e.g. an inbox provider) the agent can set up | Missing |
| Operates desktop apps (Mac); runs on glasses | Out of scope now (computer use is a later primitive) | Deferred |
| Shopping and payments with approval | Connections plus approval sheet; spend approval | Deferred (payments) |

## What this means for the plan

- **K2 (four-tool cutover)** shrinks only the *resident* payload. Every row above
  marked Present must keep working through `ta` and on-demand skills; K2's tests
  should exercise them.
- **A starter-package lane (K3, after K1/K2)** builds the Muse-default package on
  the primitives: the proactivity dial, the goals file and skill, the default
  layout (chat, feed, ideas, goals, files), monitor and reminder skills, an
  images skill, an agent-inbox recipe, and a live view of the box. It is all
  editable by the user, and none of it is platform code.
- **Browser and live box view** come after isolation, as primitives the starter
  package uses.
