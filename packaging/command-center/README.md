# Local export runner

The protected C12 owner transfer door now includes the standalone chat runner
in private exports. Its single source is package data at
`tinyassets/okf/assets/agent.py.txt`; the exporter copies it to `run/agent.py`.
Production availability still depends on C6 provider integration. It needs Python 3.11+ on POSIX and no Python packages.

The folder needs `agents/main/AGENTS.md`. Existing subdirectories may contain
skills and other files; the agent can read them with its file tools. Run:

```sh
export MODEL_BASE_URL=http://localhost:11434/v1
export MODEL_NAME=your-local-model
python run/agent.py 'Read the project and summarize it'
```

For a remote model use HTTPS and set `MODEL_API_KEY` in your shell environment.
No key is required for a local endpoint. The runner does not load `.env` files.
It never contacts TinyAssets. Model traffic goes to the endpoint you configure.

Bash runs with your host privileges. By default every command requires typing
`yes` in a terminal; noninteractive input refuses commands. The owner may write
`run/rules.json` containing `{"bash":"deny"}`, `{"bash":"ask"}` or
`{"bash":"allow"}`. Only this bash rule is currently supported; unsupported rules
fail visibly. File tools cannot modify this policy or runner code and reject
credential paths, links and paths outside the folder. Bash, once authorized,
is a host shell and is not confined by the file-tool checks.

Review an imported runner before executing it. Hosted jail, egress and
review guarantees are absent locally. Schedules do not start here. Windows,
complete harness-rule mapping and schedule execution are outstanding C12 work.
