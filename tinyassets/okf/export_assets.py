"""Serialize the shipped standalone runtime as inert bytes, never execute it."""

from importlib.resources import files


def generated_files() -> dict[str, bytes]:
    return {
        "run/agent.py": files("tinyassets.okf").joinpath("assets", "agent.py.txt").read_bytes(),
        "run/rules.json": b'{"bash": "ask"}\n',
        "run/schedule": b'#!/bin/sh\nexec python3 "$(dirname "$0")/agent.py" --schedule "$@"\n',
        "automations.example.json": b'{"schema_version":1,"automations":[{"id":"daily-note",'
            b'"agent":"main","message":"Summarize the project","enabled":false,'
            b'"trigger":{"kind":"cron","expression":"0 9 * * *","timezone":"UTC"}}]}\n',
        ".env.example": b"MODEL_BASE_URL=\nMODEL_NAME=\nMODEL_API_KEY=\n",
        ".gitignore": b".env\n.env.*\n!.env.example\n*.key\n*.pem\n.run-state/\n.sessions/\n",
        "LICENSE": b"No license selected. Choose a license before publishing this folder.\n",
        "README.md": _README.encode("utf-8"),
    }


_README = """# Exported command center

This private folder contains the files selected in the content preview and
the previewed broker rule projection for each selected agent.
See command-center.json for the included agents and the complete file manifest.
Include agents/<agent>/AGENTS.md in the preview to run that agent locally.
Selected wiki/pages Markdown is converted into the OKF bundle rooted at wiki/.
Its absolute links resolve within that wiki bundle; open it as the OKF root.
Other selected files retain their original paths.
The main agent's selected root harness is placed in agents/main/. Settings
replace source model/connection identifiers with local references and omit
source owner identifiers. Source access permissions are not portable grants.

With Python 3.11+ on POSIX and a local chat-completions model endpoint running:

```sh
export MODEL_BASE_URL=http://localhost:11434/v1
export MODEL_NAME=your-local-model
python run/agent.py --agent main 'Read the files and summarize this project'
```

Replace main with an included agent's folder name. For a remote model set an
HTTPS MODEL_BASE_URL and MODEL_API_KEY in your shell environment. No TinyAssets
account or network connection is needed. .env.example lists empty placeholders;
copy it to .env and fill the model variables, or set them in your shell.
The runner reads .env as literal data, never as shell code. Shell environment
values override .env; duplicate/unknown keys and malformed values fail startup.
A shell endpoint override drops the file's old endpoint key unless you also
set the key in the shell.
The manifest roster names each agent's optional AGENT_n model environment group.
Set that group's MODEL_BASE_URL and MODEL_NAME together to override the global
endpoint for that agent; its MODEL_API_KEY is separate and never inherited from
the global endpoint. Empty groups use the global model. .env.example lists the
empty variables; model and connection references require your own local binding.

Chat, read/write/edit/bash tools, instructions, skills and selected files work
locally. Bash has your host privileges and asks before every command by default.
You may change run/rules.json's bash policy to ask, deny or allow. The agent's
file tools cannot edit policies, settings, runtime, schedule state or manifest.
agents/<agent>/rules.json carries that agent's action behaviors. File tools
enforce workspace.files; bash enforces workspace.shell and the local bash policy.
ask_first and do_if_preapproved require fresh local consent; source preapprovals
never travel. hand_off asks you to perform the action yourself and describe the
result to the model; the agent does not execute it. Without a terminal these
requests refuse. Connection/operation-specific behaviors are conservatively
applied to the whole action class, so local rules can be stricter.
Review imported run/
code before executing it; its own prompts are not proof it is trustworthy.

Hosted jail, egress checks, auto-review, browser broker, delivery, phone push,
quota accounting and always-on execution are absent. All portable action classes
have terminal decisions, but arbitrary host shell commands have no broker to
classify their effects. When other classes have restrictions, you must select
all applicable classes before a shell command can proceed; model-provided labels
cannot authorize it. Any hand-off rule keeps execution with you. An all-do policy
plus bash allow is an explicit opt-in to unattended execution. Keep bash on ask
and review each command; this runner is not an OS sandbox. Windows is not supported yet.

Selected workflows are data in workflows/exported/. The runner supports linear
prompt graphs with one text output per node, explicit str/overwrite state, and
no hosted effects, code, tools, retries or source deadlines. It validates the
whole graph before the first model call; other graph forms remain inspectable
but refuse local execution. For a supported graph:

```sh
python3 run/agent.py --workflow workflows/exported/workflow-1.json --inputs '{"notes":"Hello"}'
```

Local schedules are a separate opt-in. Exported automations.json entries arrive
disabled with their referenced workflows. Review each workflow, input and trigger,
then set enabled to true only for the entries you want. Hosted event subscriptions
remain disabled data; there is no local event listener. For a new prompt schedule,
copy automations.example.json to automations.json. Start explicitly:


```sh
sh run/schedule --enable
```

This loop runs only while your machine is on. It supports intervals (seconds
and an offset-bearing start_at), one-time offset-bearing at timestamps, and
numeric five-field cron with an IANA timezone. All five cron fields must match;
weekday is 0=Sunday through 6=Saturday. There is no offline backlog replay.
--once checks the present slot and exits. File/shell permission still applies;
unattended input cannot approve an ask-first action. A durable local claim
precedes each turn, and only one scheduler can hold the OS lock. If a turn is
interrupted or fails, inspect .run-state/schedules.json and any external effects
before resetting state; the runner will not automatically retry an uncertain
attempt. Configuration changes require stopping, reviewing and resetting that
state. Resetting discards deduplication history, so do it only after inspection.

Credential detection cannot prove a file is free of personal data. This export
is private local output. Choose a license and review every file again before
publishing, with no private contact inclusions. Publishing is a separate action.
"""
