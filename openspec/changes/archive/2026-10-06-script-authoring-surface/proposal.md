## Why

Founder, 2026-08-31, after four consecutive production gates:

> "seems we are still doing patch for patch for patch instead of letting our
> users build thier workflows. like why cant the user just build .py's"

The four gates are the argument, and none of them was about doing work:

1. **#2737** — the served node builder constructed `NodeDefinition` with an
   explicit keyword list and never passed `workspace`, so the binding was
   dropped silently.
2. **#2742** — `_sanitize_served_branch_spec` allowed exactly one effect sink,
   so `"effects": ["workspace"]` was refused outright.
3. **#2742** — the same consent was reachable under two spellings
   (`channel_type` and `sink`), letting the agent self-approve it.
4. **#2748** — the docs showed four example packets and none carried
   `"sink": "workspace"`, the one field `_parse_packet` matches on. The
   universe wrote the documented shape twice and was refused
   `no_matching_packet` both times.

Every one is a defect in a **translation layer**: a hand-written JSON
description (`node_defs`, `edges`, `entry_point`, `effects: [...]`, a
`workspace_packet` with a `sink` discriminator, a `workspace:` binding field)
of what the code was going to do anyway. Each field is a place the description
and the runtime can disagree, and four of them did in one day. Tests never
caught any of it because tests construct the objects directly; only a live
universe writing the documented shape ever hits the gap.

**The mechanism for the alternative already ships.** `invoke_mcp_action` is
already injected into every node's namespace (`node_sandbox.py:1337`,
`namespace["invoke_mcp_action"]` at `:1261`): node code writes a JSON request
to stdout, blocks on stdin, and the parent performs the action and replies
(`graph_compiler.py:1833`). It is already consent-checked, event-logged and
rate-bounded, and the node never holds a credential. What it exposes today is
platform internals — `goals`, `gates`, `wiki` reads — and not the capabilities
a user actually wants.

So the graph DSL is not load-bearing for safety. Credential blindness is
enforced by the RPC boundary, not by the JSON.

## What Changes

A user authors **one Python file**. The platform supplies capabilities as
objects; the file is the workflow.

```python
def main():
    ws = workspace.checkout(connection="http_7931…", repo="o/n", ref="main")
    if ws.run(["python", "-m", "compileall", "-q", "."])["returncode"] != 0:
        return {"status": "compile failed"}
    ws.write("README.md", ws.read("README.md").rstrip() + "
")
    ws.push(branch="whatever-the-user-wants")
    return http.request("POST", "/repos/o/n/pulls", json={...})
```

`workspace` and `http` are **library code the user imports**, not platform
verbs. Two workspaces? Make two. A retry? Write a loop. A different pooling
strategy? Fork the library and the commons gets a better one.

**Deleted from the authoring path** (not from the codebase, initially):
`node_defs`, `edges`, `entry_point`, `effects: [...]`, the served sink
allowlist, the packet `sink` discriminator, `_find_packet` / `_parse_packet`,
and the `workspace:` binding field. All four gates above live in exactly those.

### There is no "must be declared up front"

An earlier draft claimed a workspace had to be chosen before the jail launches,
because a bwrap bind cannot be added to a running process. The founder rejected
it, correctly. A *given* jail's mounts are fixed; that says nothing about how
many jails a run may have. Point `ws.run` at the parent over the RPC channel
that already exists, spawn a jail per workspace on demand, and a script creates
workspaces at runtime and holds several at once.

The founder then pushed once further: the thing that *runs* the workspaces is
also user-buildable. It is. See `design.md` — the platform reduces to four
primitives (isolated execution, authenticated call, durable state, identity and
arbitration), and workspace, graph, pooling, retry and resume all become
libraries the commons can remix.

So the entry point carries no declarations at all:

```
run_script(source: str)
```

### The floor is other people, not the user

Founder, 2026-08-31:

> "the floor is they cant effect other users except what ever ways we build for
> users to interact. but within your own universe you are god as if you have
> your own computer in the cloud with an openclaw ageint on it that has full
> access, but also has commons libraries of graphs"

One invariant, not four primitives: **you cannot affect another user except
through an interaction surface we deliberately built.** An earlier draft of
this proposal listed four primitives, half of which were protecting the user
from themselves. See `design.md` for every constraint re-run against the
correct test.

The immediate consequence, worked through today: the `ws.__globals__` finding
filed this morning as a P1 sandbox escape is **not a vulnerability**. It is
fully contained by the jail - no network, no `/data`, no credential mount - so
it never crosses the tenant boundary. It is the author's code reaching the
author's own tools. What it actually proves is that `ALLOWED_IMPORTS` and
`FORBIDDEN_PATTERNS` are theatre: defeated by `'sub' + 'process'`, protecting
nobody but the author from themselves, and misleading about where the real
boundary is. **Delete them.** That concern file has been rewritten to say so.

## Impact

* **New public MCP surface** — `run_script`. This is the category that gets
  designed before it is built, which is why this proposal exists rather than a
  branch.
* **Additive.** `write_graph` keeps working; nothing is removed until the
  script path is proven live. Deprecating the DSL is a separate, founder-owned
  decision (`PLAN.md`).
* **Same trust boundary**, reached by a path with fewer places to disagree.
* **Proof obligation.** Not "the tests pass". The founder's universe does the
  README-fix job as one `.py`, uncoached, through the live connector — the same
  job that has now produced four gates — and the gate count is zero.
