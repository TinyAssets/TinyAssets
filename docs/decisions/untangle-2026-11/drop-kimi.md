# drop-kimi as built (2026-11-21)

The founder's call 17 (`founder-answers.md`): Kimi's record is dropped until it has a usable
path, with its lock, its template install, its probes and its plan lines. It had none: its one
credential was the vendor's own sign-in, which no Kimi page permits a platform to host
(`hosted_use` `unclear`, so switched off), and it had no API-key path. No owner could make an
agent run on it.

## What went

1. **The record and its lock**: `sources/records/kimi.json`, `sources/locks/kimi/`, its line in
   the generated `sources/src/records.ts`. The template installs one engine fewer (the recipe's
   18 steps and 9 copies, `template.test.ts`); the image is rebuilt at the next template
   build, with nothing triggered here.
2. **What only Kimi used, which its going leaves with no user:**
   - **Sign-in regions** (`cli.regions`, `{region}`): the schema and the checker's rules, the
     owner's choice of region (`signinStart`'s `region` field, refused now as any unknown
     field), its hosts in `engineHosts`, `vendorHosts` and `signinShape`, `loginArgv`'s
     filling, the probes' one login check a region, the app's region picker and the sources
     list's `signin.regions`. The feature was half built: a run never carried the region its
     provider hosts needed (the plan's open line, dropped).
   - **The resume in a working directory** (`cli.engine.resume`, `continue_in_cwd`): every
     engine resumes by its session id, so the field is gone from the four records. The checker
     asks every record for a `{sid}` in `resume_argv` and a session-id matcher. The runner's
     conversation directories (`/var/lib/ta-conv`), their lockdown exception and the probes'
     handling of them went with it.
   - **The ranking's `kimi-for-coding`** (`model-ranking.json`), a model only Kimi's plan
     served.
   - **The agent form's note** for an engine whose only credential is a switched-off sign-in.
3. **Its tests' fixtures**: each case that used Kimi as a record of a shape (no vendor logout, a
   lock, a sign-in that fails at once, a record with no key, a forbidden-variable pattern,
   `unsupported` decide and definition hooks) runs on Gemini, Codex or a mutated record. Cases
   of Kimi alone went: its regions, its stream shapes, its limit and retry rows, its forbidden
   variables, the runner's code past `SIGNIN_CODE_MAX` (no record's code pattern can show one
   now; `signinShape` still bounds it), and the owner's run on an engine with no hook (no
   record lacks one; the shape stays, checked by mutation in `sources.test.ts`).
4. **Plan lines**: `tasks.md`'s live checks for Kimi Code, its MCP OAuth stores, the region for
   its runs, and its skills on the founder's sign-in test. Item 17 is done.

## Left, and why

- **Frozen formats keep a null region.** The runner protocol 2's sign-in document records
  `region` (`protocol.json`), so the Worker writes `null` until the next protocol drops it; a
  protocol change ships in two deploys. The owner's `sandbox_signins.region` column is in the
  baseline migration and is never written now; the next migration that changes that table
  drops it.
- **Shapes no record uses, which the checker holds for any CLI**: a prompt joined to its option
  instead of on stdin, a `forbid_env` pattern (`X_*`), and the `unsupported` decide, MCP and
  definition entries. Each is a few lines, names no vendor and is covered by a mutation
  test. The engines ADR-034 lists will need some of them, and #5739 built the definition's on
  the same shape.
- **Research and history** that name Kimi stay as written: `subscription-engines.md` §5 and
  §11, `sandbox-probe.md`, `engines-replan.md`, ADR-031, ADR-033, ADR-034, and the decision
  logs. They are the evidence a return starts from. `passed.json`'s past pairs hold Kimi's
  login checks: they are records of what passed.
- **Not this stack's**: the old Python stack's records, docs and scripts (`tinyassets/`,
  `scripts/diagnose_connected_sources.py`), and the provider-name lists in
  `scripts/check_shapes.py` and `scripts/check_channel_agnostic.py`, which name Kimi as a
  provider to detect.
- **`fact15-defaults.md`'s owed research line** still names Kimi among the engines to read. The
  untangle check holds owed items verbatim, and the research reads only the records there are.
- **A sandbox store kept for Kimi** is not swept: Kimi's sign-in was never switched on by
  default, and no runner reads a store whose engine is not served.

## What brings Kimi back

A usable path, one of:

1. **A key**: a Moonshot API-key source record whose models pi-ai serves, with Kimi Code's key
   variables (`KIMI_API_KEY`, `KIMI_BASE_URL`) through the egress door, as Codex and Gemini
   run on theirs. Research first: whether Kimi Code takes a base URL for a plain API key, and
   what its key calls send.
2. **The sign-in**: a Kimi or Moonshot page that lets a platform host the unmodified CLI on a
   member's own sign-in (`unattended.vendor_login_in_sandbox` `allowed`, cited), or the
   founder switching its sign-in on in the control record knowing the terms are unclear.

Then the record is restored from history (`platform/sources/records/kimi.json` and
`platform/sources/locks/kimi/` at `1989e865b`), with what it still owed: its resume (Kimi Code
2.4.1 prints no session id in `-p` mode, so either a version that does or the resume in a
working directory again), its error and usage matchers, where its MCP OAuth stores live, a
root-owned hook layer (none in 2.4.1), its own MCP server entries, and regions again if its
sign-in needs them, carried to its runs this time.
