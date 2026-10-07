## ADDED Requirements

### Requirement: The served agent reads its own brain
The served universe agent SHALL be able to read its OWN brain — the OKF grounding
sections (identity, founder, origin, body) with frontmatter stripped, its learned
self-model, and which sections are governed-editable — scoped to its own universe
by a pinned graph id that is never caller-supplied. The read SHALL bind the
founder identity with read/list capabilities only and SHALL fail closed when the
agent is not bound to both a principal and a universe.

#### Scenario: read returns the editable grounding sections
- **WHEN** a bound served agent calls `read_brain`
- **THEN** it receives the current identity/founder/origin/body bodies, the
  self-model, and the list of governed-editable sections, for its own universe

#### Scenario: unbound read is refused
- **WHEN** the engine is not bound to both a principal and a universe
- **THEN** `read_brain` refuses without reading any universe

### Requirement: The served agent writes its own brain through a governed path
The served universe agent SHALL be able to durably write its grounding sections
(identity, founder, origin, body) and a learned name through the governed soul
writer, so the change is reflected in the NEXT turn's system prompt. The write
SHALL only touch files whitelisted by the universe's `soul.edit.md` policy, under
a per-universe lock with compare-and-swap, and SHALL bind least-privilege
capabilities (no costly / submit / branch-write authority).

#### Scenario: a written section appears in the next system prompt
- **WHEN** the agent writes an identity edit via `write_brain`
- **THEN** the next persona system prompt, rebuilt from the universe's brain
  files, contains that edit

#### Scenario: read-edit-write round-trip is stable
- **WHEN** the agent writes a section, reads it back, and writes the read body
  again
- **THEN** the stored body is unchanged and managed frontmatter is not nested

### Requirement: Brain writes never reach the control-plane or execute
`write_brain` SHALL NOT write `soul.md` (whose frontmatter carries the executable
`loop_branch_def_id` / `effect_authority`) or any non-grounding file, SHALL NOT
create or execute code, and the written files SHALL be consumed only as
system-prompt text.

#### Scenario: soul.md is not an accepted target
- **WHEN** the agent invokes `write_brain`
- **THEN** only identity/founder/origin/body (and a name) are accepted and
  `soul.md` is never written

### Requirement: The write sink refuses inode-aliased governed files
The governed soul writer SHALL, before writing a governed file, refuse it when it
is a symlink, is hardlinked (link count greater than one), or resolves outside its
universe slot — so a whitelisted write cannot be redirected through an aliased
inode onto a control-plane or external file.

#### Scenario: a hardlinked grounding file is refused
- **WHEN** a governed grounding file is a hardlink aliasing `soul.md` (or an
  external file) and `write_brain` targets it
- **THEN** the write is refused and the aliased file is left unchanged

### Requirement: Brain writes are universe-pinned, allowlisted, and bounded
`write_brain` SHALL write only the agent's OWN universe (pinned graph id), SHALL
be limited to allowlisted universes while multi-tenant confinement is unhardened,
SHALL bound each section's size, and SHALL enforce a rolling write limit that
fails closed on ledger error.

#### Scenario: off-allowlist write is refused
- **WHEN** the agent's universe is not on the engine write allowlist
- **THEN** `write_brain` refuses without writing

#### Scenario: oversized section is refused
- **WHEN** a section body exceeds the per-section size cap
- **THEN** `write_brain` refuses without writing
