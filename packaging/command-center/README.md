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
No key is required for a local endpoint. You can instead copy `.env.example` to
`.env` and fill its variables. The runner reads it as bounded literal data,
never shell code; process environment values override file values. Linked,
malformed or duplicate-key files fail visibly. The manifest roster names each
agent's optional `AGENT_n` model group. Set its URL and model together; that
endpoint never inherits the global API key.
Changing an endpoint through the shell environment drops the file's old endpoint
key unless the shell also supplies a key.
It never contacts TinyAssets. Model traffic goes to the endpoint you configure.

Bash runs with your host privileges. By default every command requires typing
`yes` in a terminal; noninteractive input refuses commands. The owner may write
`run/rules.json` containing `{"bash":"deny"}`, `{"bash":"ask"}` or
`{"bash":"allow"}`. Broker behavior rules travel in `agents/<agent>/rules.json`;
file tools enforce `workspace.files` and bash also enforces `workspace.shell`.
`hand_off` refuses; `ask_first` and `do_if_preapproved` need fresh local consent.
Source grants never travel. Qualified rules apply conservatively to the whole
action class. File tools cannot modify policies, settings or runner code and reject
credential paths, links and paths outside the folder. Bash, once authorized,
is a host shell and is not confined by the file-tool checks.

Review an imported runner before executing it. Hosted jail, egress and
review guarantees are absent locally. Schedules need the separate
`sh run/schedule --enable` command and reviewed `automations.json` prompt data;
the bundled example is disabled. Intervals, one-time timestamps and numeric
five-field cron with IANA zones run only while the machine is on. Durable
claims and an OS lock prevent automatic replay after uncertain execution.
`--once` checks the current slot and exits; no offline backlog is replayed.
Platform workflow/schedule serialization into this local prompt format,
Windows and broker-equivalent enforcement of other action classes remain
outstanding C12 work. Other behaviors are supplied to the model, but a host
shell has no broker to classify its effects; keep its default command prompts.
