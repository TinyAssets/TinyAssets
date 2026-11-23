# policy-proposal-shape as built (2026-11-23)

`untangle/policy-proposal-shape`, owed by `policy-answers.md` (#5832): an agent's policy
proposal was a whole document (`{ type: "policy_proposal", expected_generation, policy }`), and
Apply wrote it whole. An agent reads no policy (`policyRead` is caller `owner`; no tool reads
it), so a whole document it files replaces settings it never saw: the owner's `ask` and `never`
rules, its allowed hosts, its tools. The target: a proposal names only what it changes, and
Apply makes those changes to the settings as they stand, as the major agents' rule suggestions
do. With it, the live line `policy-answers.md` holds (`policyProposal` and `policyWrite`'s
dropped `proposal`) is taken out: its precondition holds.

## Research (Fact 14)

- **Claude Code ("Yes, and don't ask again").** A permission prompt carries `suggestions`, a
  list of `PermissionUpdate`s; the host's yes returns the ones it takes as
  `updatedPermissions` (Agent SDK, `CanUseTool` and `PermissionResult`). Each update is a
  delta on one destination's rules: `addRules` or `removeRules` (a list of
  `{ toolName, ruleContent? }` under one behavior, `allow`, `deny` or `ask`), `setMode` (one
  setting), `addDirectories` or `removeDirectories`; `replaceRules` exists, but the suggestion
  a prompt offers is an add. Applying one changes only the rules it names; a rule already
  there is no change, and none is read back to the agent. Source:
  `code.claude.com/docs/en/agent-sdk/typescript`, "Permission Types".
- **Codex (smart approvals).** An escalation request may carry a
  `proposed_execpolicy_amendment`, one `prefix_rule(pattern, decision)` the user accepts with
  the approval, appended to `~/.codex/rules/default.rules`; network approvals carry
  `proposed_network_policy_amendments`, a host to allow. Each is one rule added, never the
  rules file rewritten. Sources: `developers.openai.com/codex/rules` (by search: that host is
  outside this session's network) and the execpolicy crates' source.
- **Claude's and ChatGPT's connectors.** Each tool, or category of tools, has its own setting
  (Always allow, Needs approval, Blocked), changed one at a time; an "always allow" on a
  prompt sets that tool's setting alone (`support.claude.com` 11176164).
- **So:** every one of them suggests a delta, each part naming one unit (a rule, a host, a
  setting), applied onto the settings as they stand; none hands the agent the settings, and
  none suggests a whole file.
- **Our document's units** (`contract/src/policy-document.ts`). Two lists of entries, each
  entry a whole rule: the `actions` table's rules (`{ act, do, agent?, tools? }`, the strictest
  holding rule decides, so order means nothing, `actions.ts` `ruleOn`) and the egress
  table's `destinations` (`{ host, port }`). Every other unit is one setting of an object
  table: `egress.mode`, `codemode.on`, each `files` and `models` setting, and each source's
  ceilings under `models.sources`. The `tools` table's one list, `custom`, is no list of
  entries: left out, it is every tool, including ones added later (`customTools`), so adding
  to it or taking from it has no meaning of its own; the owner sets it on Connected apps.
- **What else reads the old shape.** Only `Policy.apply` (`type`, `expected_generation`,
  `policy`) and its tests; the app's card shows the change as its JSON beside the owner's
  settings now ("which applying replaces whole", `namesFor` reading `policy` for it). The
  proposal's answer names the current `generation` only so an agent can file a whole document
  at it. Pending cards of the old shape may be held on the stage (any agent could file any
  object); none is read by anything else.
- **The live line's precondition.** The stage serves 8c80b02c8 (#5846) on all six Workers
  (deploy run 45281806227), a descendant of #5832 (f3bf49268). There `contract/doors.json`
  marks `policyProposal` dropped and `policyWrite`'s `proposal` dropped, and `doors/src/app.ts`
  routes neither: the doors at the serving version, and every version since #5832 a rollback
  can reach, call neither. `handedHold` against the base branch (`deploy/guards/handed.test.ts`)
  is `[]` with both gone.

## Decided

1. **A proposal is a delta, shaped as the document is** (`PolicyChange`,
   `contract/src/policy-document.ts`): `set`, a JSON merge patch (RFC 7396) of the settings,
   nested as the document is, a value set and null putting a setting back to its default;
   `add` and `remove`, entries of the two lists at their places (`actions`,
   `egress.destinations`). It names at least one change. A `set` never names a list nor
   anything holding one (`actions`, `egress`'s destinations, `tools`): a list changes by its
   entries, or on the owner's own screen.
2. **Apply merges it onto the settings as they stand** (`edited`): the set merged, the removed
   entries taken out, the added ones put in unless there already, the whole validated as any
   write is. It needs no generation: it names only its own units, so a change the owner made
   since keeps; and adding an entry that is there, or removing one that is not, changes nothing
   and says nothing, as Claude Code's `addRules` does, so no answer tells an agent the owner's
   rules.
3. **The proposal is checked when filed**: `edited` onto the settings now, so a malformed one
   is refused to the agent, in the validator's words, and never reaches the owner (Fact 8).
   Apply checks it again; one that no longer applies (an old-shaped card, or an act since
   gone) is refused in its words and waits to be declined.
4. **The tool's words** say what the change is and that nothing it does not name changes; its
   answer no longer names `generation`.
5. **The card** shows the change as its JSON and says that applying makes only those changes;
   it no longer shows the owner's settings, so `namesFor` reads them no more.
6. **`policyProposal` and `policyWrite`'s dropped `proposal` go** (both tables' rows, its
   handler, its request type, `door-map.ts`); `Dropped` is `never` again.

## Problems, under the fix that removes each

- A proposal is a delta; Apply merges it.
  - An agent that reads no policy could only file a whole document, which replaced what it
    left out (`policy-answers.md` research).
  - `apply` read the change's own `type`, `expected_generation` and `policy`: a card shape only
    it knew, and a generation an agent had to carry.
  - The proposal's answer named `generation` for that alone.
  - The card showed the owner's settings beside the change, and its note warned that applying
    replaces them whole: words for the old shape's harm.
  - A malformed proposal was held for the owner to decline, never refused to the agent.
- The dropped one-card read and the write's dropped card go: no doors version the stage
  serves, or a rollback reaches, calls them.

## Found and left

- `tools.custom` is out of a proposal's reach (research): the owner sets it on Connected apps.
  A delta of a list whose absence means every tool is a design of its own, with no need seen.
- A proposal may both remove and add one entry: removes go first, so it is there after.
  Refusing it is a case for a card the owner reads either way.
- The card still shows the change as its JSON: plain words per change are a feature editor the
  platform does not build (Direction 3); the delta's JSON names only what changes.
- `policy_propose` keeps `idempotentHint: false`: refiling one card with the same change is a
  no-op, but a filing naming no `request_id` is held under a new card each time.

## Owed

- [ ] live: after the next deploy of the owner and the doors, on the stage: claude.ai's
  `policy_propose` with `{ "add": { "egress": { "destinations": [...] } } }` answers an
  `approve_url`, the card is on Home, and Apply there adds the host and keeps the rest.
