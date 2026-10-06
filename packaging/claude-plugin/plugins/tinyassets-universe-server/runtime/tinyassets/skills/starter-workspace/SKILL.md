---
name: starter-workspace
description: Inspect current workspace state, build UI, deliver files and arrange ongoing work.
---

Work in /u using read, write, edit and bash according to their tool schemas.
Inspect current files, branches, connections and status as needed; do not rely on
an old folder inventory. Discover with `ta search <words>`, inspect arguments with
`ta describe <name>`, then invoke `ta <name> --json '<args>'`.

For earlier turns, missing files, workflows and automations, read the platform
reference linked by `ta describe write_graph` (handbook write_graph.systems).
Long-running work belongs in command-center workflows/automations. For app UI,
consult its write_graph.interfaces reference and submit one component through
the governed app_ui path. Keep shared identity intact when working as another
agent. Owner-authored skills and instructions take precedence over workflow
recommendations, while schemas and permission checks still apply.

Use mermaid fences for flows and chart fences for comparisons when useful.
Chart JSON example: {"type":"bar","labels":["A","B"],"series":[{"name":"Count","values":[2,4]}]}.
Verify exports before attaching a fenced file block such as
{"path":"exports/a.csv"}; consult current delivery guidance for size limits and
remote delivery. Inspect delivery receipts before claiming a recipient got a file.
