# Published Village and House omit chat agents

Read-only production catalogue inspection on 2026-10-04 found:

- Fantasy Village `agent_01m4279gd9bfvb6q1e4eh4m7yt`: legacy
  `tinyassets.system.v1`, one screen, two workflows, two automations, zero
  `tinyassets.agent-ref.v1` components.
- Furry House `agent_01m428kqahpqmmccfvbm553j48`: legacy
  `tinyassets.system.v1`, one screen, one workflow, zero agent-ref components.

Neither is a command-center file package. The public Furry House preview describes
the residents as the viewer's agents; its script constructs residents from agent
rows (`sync(agents,w)`, keyed by `agent_id`). The connector's preview output is
truncated, so this investigation did not verify the entire scripts or their
complete reference maps. The complete component catalogues above establish that
neither publication includes chat-agent templates. Private source bindings are
not publication components, even when their instruction definitions are public.

## Cause and bounded fix

`publish_requests.build_snapshot` exports only explicitly selected
`agent_templates`, defaulting to none. `system_copy_requests._source` and
`package_requests._plan` collect those public components and validate declared
`agent_refs`. Their shared `package_requests._materialise` creates new private
recipient bindings to the public instructions and remaps aliases. It does not
read or copy the publisher's private bindings. Workflows are private remixes;
`workflow_refs` map to those remixes, not chat agents. Legacy source workflow IDs
are resolved to component keys before copying. Package `manifest.agents` is a
file inventory; relocating instruction files does not create chat bindings.

Thus #4416 already copies explicitly included public templates, but cannot fill
in an undeclared source roster. Both kinds can install screens with zero agents.
The reporting fix makes publish and copy consent explain this consequence and
the need to select public templates and republish. Declared missing agent aliases
are named in the existing refusal text. No storage, authority, or API shape
changes; no private source data is read to construct a recipient warning.

Six new parameterized regression cases failed before the fix: screen installed
with no additional bindings and no actionable warning (both kinds), publication
omission warning absent (both), unresolved aliases unnamed (both). The successful
copy assertions precede the failing warning assertions, proving the symptom.
Existing public-template copy tests prove new recipient agents are addressable.

Validation after the fix: the agent-template, package, system-copy and publish-
intent files passed 180 tests (one POSIX-only skip). The picker, preview,
in-platform-system and publish-discovery files passed 105 tests (two platform
skips). All basetemps were outside the repository. The browser public-template
copy/chat test passed; the empty-system browser test completed its new warning
assertions but failed its final transport check twice, tracked separately in
`2026-10-04-system-browser-windows-loopback-abort.md`. Ruff and regenerated mirror
parity passed. No full suite was run.

## Still needed

The publisher must explicitly select the intended agents' supported public
instruction templates and republish both designs. This code change does not
retroactively add agents to immutable publications or existing installations.
Do not copy private bindings, model choices, settings, or conversations to repair
them. Delete this concern after republished designs have passed a second-account
copy and chat check. This lane is committed and pushed only; deployment and that
live check are outside its requested scope.
