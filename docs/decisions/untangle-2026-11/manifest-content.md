# manifest-content as built (2026-11-21)

`untangle/manifest-content`. The founder's bar for the cutover is "a clean break from all old
bad shapes". `platform/cutover.json`'s guard held only that each edit's `names` exist, so the
manifest could leave old text behind and nothing failed (verify-break, items 1 to 4 and E13).
Research came first (Fact 14); it is section 1, and the build follows from it.

## 1. The research

**The gap, measured on `45d2b1bcf`.** Three scratch scripts applied each edit's names to its
file and scanned every tracked file for the paths the manifest deletes.
- Literal removal leaves broken text: `AGENTS.md`'s Fact 10 tail, an empty table cell in
  `architecture.md`, `pyproject.toml` keys falling into `[build-system]`, and host-actions'
  sections whose headings go but whose bodies stay.
- `scripts/queue_attempts.py` loses its import of `NO_TEST_RAN`, but line 167 still reads it.
  The merge queue's own workflows would raise `NameError` after the cutover.
- A plain substring scan for every deleted path gives 190 files. Most hits are noise:
  `deploy/` in `platform/` means `platform/deploy/`, and `data/`, `archive/Products/...` and
  `pages/concepts/page-` are not tracked paths at all. Resolving each mention against the
  tree (below) leaves 95 files: 49 decision logs, the platform's ported-from citations, and
  about 45 real ones.

**What an edit should be.** Three shapes were weighed.
- **(A) Names as anchors, as now.** The result is undefined, so neither the guard nor the
  dry run can check it, and the cutover has to guess from `what`. Rejected.
- **(B) The edit names the file's content after the cutover.** The dry run could apply it
  exactly. But it is a second copy of about 110 files, one of them 1,047 lines
  (`tests.yml`), and it drifts with every PR that touches one. It would make the manifest a
  conflict hotspot ten times over. Rejected, as the verifier also found.
- **(C) The file less its names, made exact.** Chosen. `ci/kit/cutover.ts` `edited` defines
  what each name takes:
  - a name that is whole lines takes the block it heads: a Markdown heading's section, a TOML
    table, or else the lines indented deeper than it, with the line at its indent that closes
    it (`)`, `}`, `fi`);
  - a name with leading whitespace counts only at the start of a line;
  - any other name goes as its own text.

  The result is derived, never stored, so there is no second copy. The guard can compute and
  check it, and the dry run can apply it (owed, below). Today's heading and job-key names
  already mean "this section" and "this job". (C) makes that literal.

**Where the result is checked.** Only where a parser can say the result holds:
- Python must compile and bind every name it reads. Ruff's F821 would do this, but ruff is
  declared only in `pyproject.toml`'s dev extra, which the cutover deletes. So the check is
  stdlib `symtable` plus `ast`, in `ci/kit/python_names.py`, and it adds no dependency.
- TOML must parse (`tomllib`).
- An edited workflow must be YAML.

Markdown, shell, TS, JS and JSON are not parsed. A name there may still anchor a sentence or
a line the cutover rewrites. The guard holds that nothing that goes is left unanchored. A
line by line apply there is the dry run's, under its typecheck (owed).

**The verifier's four proposals.**
- **(a) Check the residue.** Yes, through `edited`, every phase's names applied.
- **(b) Build "what goes" from the manifest.** Yes, from the tree: every tracked file under a
  delete, and its folders.
  - A mention counts only when it names one of those paths, and no staying path from the root
    or from `platform/`.
  - A one-word folder counts only with a path below it.
  - Python imports of deleted modules are found by `pythonLoads` on the residue.
  - Under `platform/`, the old stack's own paths are still left out. Platform comments cite
    the code they were ported from, at a commit (b24-manifest item 1). Whether that history
    stays is IQ2, founder call pending.
- **(c) Scan every tracked file.** Yes, for the deleted paths and Python imports. The window's
  vocabulary and the "deleted at the cutover" marker stay `platform/` and `.github/` only:
  `"import"` is ordinary code everywhere else. In an edited file, the marker marks what the
  edit takes, so it counts as an anchor of its own.
- **(d) Split the files that are mostly old stack.** Yes, for `docs/architecture.md`,
  `docs/host-actions.md` and `tests.yml`: the old part moves now into a file of its own, and
  the manifest deletes that file. Open PRs touch all three (#5696, #5343, #5720, #5722), so
  the splits are owed. `pyproject.toml` needs no split: its entry now names whole tables and
  lines, and the result is the tool tables alone.

**Two new kinds of record.**
- **`keep`** records a path that stays as it is although it names what goes, and why. It is
  data, not a special case. A keep that names nothing that goes fails, so a keep cannot
  outlive its reason.
- **The `cutover` guard's owed list** (`ci/guards/rules.ts`, the repo's ratchet,
  `ci/kit/owed.ts`). The guard can land before every entry is fixed. Each owed row is one file.
  A row that stops failing must be deleted, and none may be left when the cutover runs.

**The conflict hotspot (the coordinator's input).** #5709 conflicted with #5710, and #5712
with #5709: every PR appends to the same few lists. Two shapes were weighed.
- **Keep entries sorted.** Two PRs that add neighbouring paths still collide. The one-time
  re-sort is a whole-file diff.
- **One file per area under `platform/cutover/`**, merged by `ci/kit/cutover.ts` into the one
  manifest the guard and the dry run read. An area's PRs then touch only that area's file.
  Chosen.

It is not built here. It moves every entry and every reference to `platform/cutover.json`:
the layer message, the deploy literals' `KEPT`, the `about`, the gates doc and the decision
logs. #5714 and #5722 are editing `cutover.json` now. Owed, below.

## 2. What was built

- **`ci/kit/cutover.ts`:**
  - `edited` and `taken`. A move with `names` takes each name's reach into its `to`, so
    `peer_agent.py`'s carry-out is checked too.
  - `pythonModules` (module to file). `pythonLoads` now reads from it, so the Python check
    finds modules the way the guard does, and the rule is not written twice.
  - The `keep` entry.
- **`ci/kit/python_names.py`.** It reports:
  - a file that does not compile;
  - a global or builtin name that nothing binds;
  - `from x import n` where `x` does not define `n`;
  - `m.n` through `import x as m` where `x` does not define `n`;
  - TOML that does not parse.
- **`ci/guards/cutover.test.ts`:**
  - `leaves` builds each phase's tree: deletes, carry-outs, named moves and edits.
  - `leftProblems` scans it, and holds keeps and edited YAML.
  - `pythonProblems` runs the Python check per phase, over what stays when the hold ends.
  - Both are held against the owed list.
  - The old scope case lives on in the new probes, with cases for the reach rules, the keep
    and the Python check.
- **`ci/kit/sources.ts` `treeFiles(roots)`.** The guard reads `["."]`.

## 3. The entries fixed

- **The verified case, `scripts/queue_attempts.py`.** The oracle's line is now data,
  `STOPPED_LINES = (NO_TEST_RAN,)`. The edit takes the import line and `NO_TEST_RAN,`, which
  leaves `()`. `str.startswith(())` is False, so a job stops counting as "stopped before tests"
  only by the oracle's line.
- **Python made exact:**
  - `invariants_run.py`: whole import lines and list items;
  - `render_marks.py` and `brand_parity.py`: the tray and app-badge lines, defs and the
    `if/else`;
  - `test_scope_guard_policy.py`: the tuple item with its comma;
  - the `peer_agent.py` carry-out: the whole `API_KEY_PROVIDER_ENV_VARS` tuple and the
    `def`, with the import line taken in full;
  - new edits for `provider_context_feed.py` (the old brain's and concerns' sources) and
    `wt.py` (the old specs in the note template).
- **TOML:** `pyproject.toml` names its five old tables, the UI-frame ignore with its comment,
  `testpaths` and the three old markers.
- **YAML:** `platform-deploy.yml`'s hold names `routes, h2, handback, `. The old name left
  `[plan, dark, , control]`, which is not YAML.
- **Anchors added where a mention was left:**
  - 25 platform edits: the import window's paths, `"import"` and `"discard"`, `IMPORT_*`,
    `platform/cutover.json`;
  - two new platform edits, `contract/src/content.ts` and `policy-document.ts`;
  - `secrets-expiry-check.yml`, `.gitignore`, both `cloudflare.md` copies, two `docs/ops`
    files, `executable-gates.md` (#5713's new sentence), `canary_handles.json` and
    `pre-commit`.
- **The guard's own files go with it.** A delete of `python_names.py`, plus edits of
  `owed.ts` and `ci/guards/rules.ts`, take out the `cutover` guard's name and helper.
- **A file fixed now:** `sandbox-template.yml`'s comment cited the deleted
  `tests/test_workflow_production_environment.py`. It now cites its platform home,
  `ci/guards/environment.test.ts` (b24-dry-run item 6).
- **The clean-break item** (`scripts/test_shape.py` and its import in `test_hygiene_gate.py`
  become a delete) was #5722's, which deleted the shape checker and its carried test. Merging
  it in dropped the ratchet's rows for `test_shape.py` and `test_inventory.py`, which the
  manifest now deletes.

## 4. Founder call pending

Each of these stays as it is, recorded as a `keep` in the manifest:
- `docs/decisions`: ADR-001 to ADR-031's status (IQ1) and the decision logs (IQ2);
- `ideas`, `output` (IQ2);
- `openspec/changes/clean-sheet-platform/{tasks,main-agents,sandbox-probe}.md`: the plan and
  its research (IQ2).

The site's links to the deleted `openspec/specs` (the developers page, `lib/site.ts`,
`llms.txt`) are no founder call: they are owed rows, for the site's cutover PR (review).

`templates/` names nothing that goes, so it needs no keep. Not touched either:
- the verbatim old-store archives (IQ8);
- the shells (IQ5);
- the Direction text (IQ6), including the `AGENTS.md` paragraph whose `` `tinyassets/` ``
  name the manifest still holds;
- the platform's ported-from citations (IQ2; section 1 (b)).

## 5. Owed

- [ ] `docs/architecture.md` and `docs/host-actions.md`: split each old part into a file the
  manifest deletes, and drop their owed rows (untangle/manifest-split-docs, after #5696 and
  #5343).
- [ ] `.github/workflows/tests.yml`: the old suite's jobs into a workflow of their own the
  manifest deletes (untangle/manifest-split-tests).
- [ ] The hygiene gate's pytest half; `pr-scope-guard.yml`'s old-suite comment; and the
  process tests' old-suite fixtures (`test_queue_attempts.py`'s `qa.NO_TEST_RAN`,
  `test_review_check.py`, `test_rulebook_ratchet.py`). Each row in `ci/guards/rules.ts` is
  dropped as the ratchet asks (untangle/p6-tests-beside).
- [ ] `scripts/main_red.py`'s junit and quarantine reading (untangle/p6-queue-helpers).
- [ ] The dry run applies every edit (`edited`) and runs the edited tests instead of setting
  them aside. That needs the TS, JSON and Markdown names made exact too
  (untangle/manifest-apply).
- [ ] The manifest as one file per area under `platform/cutover/`, read as one
  (untangle/manifest-split-files).
- Founder question (not an owed item): IQ1 and IQ2 on the founder's page (`founder_batch.md`
  item 13). Each history keep is then confirmed or made a delete; a keep of history names what
  goes for good, so only the founder's answer ends it.
- [ ] The window opens only once `ci/guards/rules.ts` owes the cutover guard nothing
  (tasks.md §9 step 10). Until then only the deletion PR's own checks fail on a row left
  (`rules.ts`' edit takes the helper; `owed.ts`' takes `"cutover"` from `GUARDS`), inside
  the window. The window's runner refuses to open while a row is owed
  (`untangle/window-owed-check`).
