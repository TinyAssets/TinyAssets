# What Step 20 can reuse for device placement

ADR-034 assigns the agent loop to the user's unmodified engine and calls for
one runner. ADR-024 assigns a paired device only its owner's admitted work.
ADR-031 decision 5 keeps its vendor login on the device. ADR-033 decision 3
distinguishes hosted permission from running the owner's own CLI locally.
These decisions also apply to an app with players belonging to different
owners. Hosting a match on one player's computer does not authorize running
another player's agent there.

## Existing invocation and placement

On main `f934871ad` and the combined source at `c20aec73b`, the real
`sandbox/runner/engine.ts` invocation was evaluated for a first and a
continued turn for each of the four sign-in-capable records. The exercise
supplied synthetic home, system, conversation and PATH values, and a fake
MCP URL. It ran neither a CLI nor a hosted or paired device. Node 22 and 24
gave the same eight results on each tree.

| Engines | Executable and working directory | Parts that accepted the supplied locations |
| --- | --- | --- |
| Claude Code, Codex, Gemini | The executable came from the hosted install under `/opt/ta/engines`. All six turns used `/workspace/agents/fixture`. | The run took the supplied home and PATH. Generated files were under the supplied home or system directory. |
| Kimi | Both turns also used the hosted install. They took the supplied conversation directory. | Home, PATH and generated files followed the supplied locations. |
| pi | The served record takes an API key. | The local-login comparison has no pi case. |

Passing a device PATH or home therefore does not by itself select a locally
installed CLI. The invocation and session templates still belong to the
source records. The current `vendor_login_in_sandbox` credential and
`signinOn` describe hosted login. The code also has a distinct hosted job
protocol, including its provisioned users and scratch and lease directories.
A hosted reboot can continue a run from the saved session. ADR-024 requires
an unknown outcome for in-flight device work on disconnect and excludes its
automatic replay.

## Dependency before S7 and S8

Choose how the paired user's local executable, workspace projection and
existing login enter the one runner. Prove which placement admitted the run,
which account's work the device sees and which outcome the owner records if
the device disappears. Recheck hosted behavior on that change. An engine's
hosted switch establishes neither the availability of a local executable
nor consent to use it. Resolve the supported device environments through
the placement research.

S8's host election consumes S7's advertisement. S6 still owes player
identity and seat grants. The old `tinyassets/universe_seats.py` accounts
for concurrent agent calls per account. It contains no match-player grant.
The importer explicitly disposes of that old concurrency state in
`platform/import/rules.ts`. Establish a match's owner, its players' separate
admissions, and the meaning of host loss before choosing a relay transport.
The old S8 acceptance calls for 10,000 lockstep turns and an agent order within
two turns. The invocation exercise measures neither.
