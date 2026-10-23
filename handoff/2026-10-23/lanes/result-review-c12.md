Diff and yardsticks are read; now I'm checking the guard script, the T10/publish coverage, and the conversion's code targets.

Almost done. I'm confirming line numbers for the findings.

The spec's direction is right, but four gaps keep it from making S17 read converged: the convergence guard isn't concrete, T10 is missing, some old memory data has no stated fate, and the deletions don't name what they delete. Paths below are relative to `openspec/changes/c12-knowledge-files-export/` unless they start with `docs/`, `scripts/` or `tinyassets/`.

**What it gets right:**
- **Files win:** design.md:13 says files are canonical and a failed file write does not publish a newer index.
- **Indexes derived and rebuilt in the box:** knowledge spec:17-29.
- **Router forwards, box assembles:** export spec:17-20, matching T1, T4 and T8.
- **No change to the C6 migration:** design.md:9 and :14. It also rejects a wiki-only second exporter (design.md:15).

**Findings**

1. **High: the S17 guard is not concrete.** The new guard text in `docs/architecture.md:170` and tasks.md:21 is prose ("rejects second canonical writers and shared owner paths"), with no globs, regexes or test names.
   - The S17 entry that exists today, `scripts/check_shapes.py:182`, points its new-path glob at `tinyassets/okf/**`, which does not exist. Its old-path list holds only `daemon_brain.py`, with a ceiling of 1.
   - The guard can't just count files under `knowledge/` and `retrieval/`, because K2 (design.md:22) keeps that code as derived indexes. It needs patterns that match canonical writes.
   - For "shared owner paths", it should reuse C6's T4 structural test rather than define a new check.
   - The spec should name the target path, each old (glob, regex) pair, the ceilings that drop to 0, and the test for each exit proof.

2. **Medium: T10 is not addressed.** No requirement says published or commons knowledge is copied out of the box at publish time via `BOX_PUBLISH`.
   - The removed "Scope-Isolation" requirement (knowledge spec:47-50) was what passed universe-public rows through. Nothing replaces it.
   - ADR-017 calls the commons an OKF bundle, which leaves open whether public listings read owner files.
   - Needed: a requirement and scenario saying public knowledge is a publish-time copy, the listing works with boxes down, and neither indexes nor export serve it.

3. **Medium: the conversion can silently drop data.**
   - Only "durable selected memory" and "durable mini-brain entries" are converted (knowledge spec:33, :60; design.md:23).
   - The mini-brain lifecycle being removed has `candidate`, `rejected` and `superseded` entries (main spec:155). Quality-replay results and the cost ledger are other mini-brain data the change removes. None of these has a stated fate.
   - Graph facts in `knowledge.db` that no file produced are neither "authoritative" nor rebuildable.
   - The check "compare counts/content … of authoritative records" (design.md:14) only covers whatever gets classified as authoritative.
   - Needed: a rule that every row in a store being deleted is converted to a file, moved to a named operational store, or listed as derived with its rebuild source; the conversion refuses to proceed while any row is unclassified.

4. **Medium: the slices don't name what they delete.**
   - K1 deletes "database-canonical writes in `knowledge/`" (design.md:21), but most of `knowledge/` is index code that K2 keeps.
   - K3 names no modules, though `memory/` has 13 (`temporal.py`, `consolidation.py`, `promotion.py`, `project.py`, `versioning.py`, `episodic.py`, …).
   - Stores that aren't named at all:
     - `tinyassets/daemon_memory.py`
     - `tinyassets/daemon_wiki.py`
     - the `notes.json` store (`notes.py`)
     - `tinyassets/storage_accounting.py:276`, which reads the brain DB and wiki root to charge usage; T2 says billing uses box-reported usage.
   - Main-spec requirements the delta neither modifies nor removes, so the main spec contradicts itself after archive:
     - "Bounded Combined Daemon Memory Packet" (main spec:209): host-local, with a Mini-Brain section the delta removes.
     - "Tiered Memory Scope Model" (:82)
     - "Project-Scoped Versioned Key-Value Memory" (:251)
     - "Standalone Temporal Fact Library" (:279)
     - "Episodic Promotion Candidate Scan"
   - Each slice should list its modules and requirements to delete or modify.

5. **Low: "exactly one export" is enforced against `okf_export.py` only.** Design.md:24 says "any duplicate export door" but never decides on the others:
   - `conversation_custody.export_conversation` and `storage/conversation_custody.export_thread`
   - the `workspace_git` `refs/tiny/export` ref
   - `publish_requests.export_ui_component`

   Each should be classified as a duplicate (deleted) or a different action (kept, with the reason).

6. **Low: import is left open.** The export spec:10 and tasks.md:20 rely on "the bundle format used for sharing", which isn't defined anywhere. `tinyassets/boxes/boxd.py:633 import_bundle` already exists. The spec should name that or say the export manifest defines the format.

7. **Low: overbuilt and open.**
   - K1 and K3 each do their own per-owner conversion, each with a snapshot and a write stop. One conversion per owner covering knowledge and memory together has less process (Direction 6) and fewer windows.
   - design.md:38-40 says "None blocking", yet classifying the data and ordering consumers are both deferred to the inventory in tasks.md:3. That deferral is exactly what findings 3 and 4 depend on.

VERDICT: DISAGREE_EVIDENCE
