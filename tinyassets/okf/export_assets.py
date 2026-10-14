"""Serialize the shipped standalone runtime as inert bytes, never execute it."""

from importlib.resources import files


def generated_files() -> dict[str, bytes]:
    return {
        "run/agent.py": files("tinyassets.okf").joinpath("assets", "agent.py.txt").read_bytes(),
        "run/rules.json": b'{"bash": "ask"}\n',
        ".env.example": b"MODEL_BASE_URL=\nMODEL_NAME=\nMODEL_API_KEY=\n",
        ".gitignore": b".env\n.env.*\n!.env.example\n*.key\n*.pem\n.run-state/\n.sessions/\n",
        "LICENSE": b"No license selected. Choose a license before publishing this folder.\n",
        "README.md": _README.encode("utf-8"),
    }


_README = """# Exported command center

This private folder contains only the files selected in the content preview.
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
file tools cannot edit this policy, runtime or manifest. Review imported run/
code before executing it; its own prompts are not proof it is trustworthy.

Hosted jail, egress checks, auto-review, browser broker, delivery, phone push,
quota accounting and always-on execution are absent. Schedules are not started
or implemented by this runner. Full platform action-rule mapping is not yet
supported; the local policy only governs bash. Windows is not supported yet.

Credential detection cannot prove a file is free of personal data. This export
is private local output. Choose a license and review every file again before
publishing, with no private contact inclusions. Publishing is a separate action.
"""
