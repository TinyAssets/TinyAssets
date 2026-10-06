Package install just copies files and doesn't bind a revision at runtime. Next I'm checking for any other extension activation or credential-slot mechanism.

I agree with your preliminary finding. The package system doesn't change that, because "activation" there means copying files once, not binding a revision when the code runs. A real piece of L10 can be built now on existing code. Exact-revision activation, author/ceiling auto-updates, scoping an extension to its own credential slot, and second-account reuse cannot be built without a launcher that doesn't exist yet. That launcher is the same one the parent lane's stdio task 1.4 is already blocked on. I read code only: no tests run, nothing edited.

## Preliminary findings

**1. `ta_cli` runs any executable in the workspace — AGREE.** `ta_cli.py:33-74` reads `extension.json` from `/u/extensions` and `/u/agents/<agent>/extensions`. The only check on the executable is that its path stays inside the package. Nothing checks a hash, revision or saved record. Those folders are mounted read-write for the agent (`universe_tools.py:378-385`), so a "tested revision" can be rewritten right after its test.

**2. The extension shares the whole launch's `ta` socket — AGREE, with one correction.** It isn't passed down as a file handle. `/tmp/ta.sock` is a fixed path mounted into the entire jail (`universe_tools.py:396-399`), so any process there gets the launch's full grant. That includes every `connection:*` and `mcp:*` tool when `connections_granted` is on (`ta_capabilities.py:51-66, 116-136`). Changing how `ta_cli` starts the extension can't narrow that. What does already hold: the jail clears its environment and the broker keeps custody of credentials. So "credentials absent from sandbox env/files" is supplied today. "Foreign slot denied" is not.

**3. No per-extension revision activation or slot authority — AGREE, with nuance.**
- `command_center_packages` does have consent-bound, exact-digest install pins (`pins` table with claim/finish, `:998-1017, 1265-1345`). But `plan_install`/`write_new_file` copy the files into the mutable workspace, and nothing checks them when the extension runs.
- Credential slots exist per connection incarnation (parent task 1.5, done), not per extension.
- No auto-update path for extensions exists. `command-center-recipient-updates` covers UI releases only.
- `design.md`'s "Reuse the current extension runtime and activation permissions" assumes something that doesn't exist. L10 owns correcting that.

**4. Legacy authoring runs node definitions, not `ta` packages — AGREE.** `authoring/service.py:495-560` runs `NodeSandbox` code nodes with its own single-use confirmation tokens (`:830-870`). Using it for connectors would be a second runtime and a second effect authority. Don't.

## What L10 can build now, on existing code
These are for the owner's own connectors only, no sharing; same-user cases aren't a floor issue:
- **Saved revision and receipt (1.2):** store the exact package bytes with the existing `store_blob`/`record_version` (author, name, version, sha256). Record the test receipt against that sha.
- **Tests (1.3):** self-tests call through `ta` → `run_authenticated_external_call_effector`, so the owner's existing access-mode and review policy applies to any effectful call. That meets "effects require owner policy" without the authoring confirmations.
- **Honest status:** the daemon hashes the package folder when listing it. A mismatch shows "changed — untested" and is never labelled connected.
- **Controls (1.4):** list source/version beside existing connections. Revoke uses the existing connection/MCP revocation fence.
- **Export:** credential-free export reuses `collect`/`scan_public`/`build_publish_package`. Its slot requirements are declarative metadata only.

## What's genuinely missing upstream, and who owns it
Tasks 1.5 and 1.6, plus the spec scenario "extension requests another connection's slot → denied", need a launcher that can:
- run an exact immutable revision,
- give it only its declared connection bindings through a narrowed `ta` grant, with no view of the workspace,
- isolate it from other authors' code.

That launcher is the package-cell launch API in `docs/concerns/2026-10-05-mcp-stdio-owner-cell-prerequisite.md`. Its owners are `per-role-uid-split` and foundation tasks 2.5/2.6, and parent stdio task 1.4 is waiting on the same thing. The raw-key separate-user variant isn't needed here, because L10's default is broker-only custody. Building a separate bwrap-plus-narrowed-socket launcher inside L10 would be exactly the second runtime/activation authority you ruled out. L10 should add a dependency line next to stdio 1.4, not build around it.

None of this rests on anything being undeployed. The missing piece is an API and a security boundary.

## The aborted main merge
This is ordinary integration work and not a reason to stop. But the consent and OAuth conflicts come from the parent's code, so the parent lane (#4496, `feat/mcp-connect-ladder`) should resolve them when it merges `main`. L10 should then rebase on the result. If L10 resolved them itself it would fork the parent's resolution. Keep merges serialized.

VERDICT: ADAPT
