# r8-copy-restore as built (2026-11-15)

Lane R's eighth PR: the restore from the off-account copy and its monthly drill (`tasks.md` §10, R8; `r4.md` item 13; `r8-retire-gate.md` item 7). Researched first, as its own step (Fact 14), on `origin/main` at `245216677`.

1. **What main holds.**
   - **The copy (`deploy/offsite.ts`, R4).** Each night `rclone sync`s the files bucket into B2 at the same keys: `o/<ns>/c/<name>` chunks and `o/<ns>/m/<gen>` roots with their parts and log segments (`files/src/keys.ts`). Each version is locked `KEEP_DAYS` (22) from its upload, and the lifecycle deletes it 22 days after it is hidden or replaced. Every object is sealed with its own R2 key as additional data, so it opens only at that key. `--b2-version-at` appeared only in comments. Nothing wrote R2 from the copy, and `checklist.md` forbade a copy back by hand.
   - **The owner's restore (R5r, R5w).** Mode `restore` loads one generation `{namespace, gen}` from R2 into the owner's own object: `restore.source`, then the pin and wipe for a live owner, then begin, step, admit. It reads only through the files Worker's manifest reads (`r5r.md` item 12: "R8 is a source swap").
   - **The erasure's records.** The object's mark, then the directory's `o:<owner>` erased record (the owner's principal and a time, no namespace), then the issuer's identity deleted, then R2's `o/<ns>/` deleted a day after the mark, then the tombstone. Every one of them is inside the account. The copy shows an erasure only as hides on the next night, and a hide looks the same whatever deleted the object. A view at a time before the hide (`--b2-version-at`) shows the erased namespace whole.
   - **The rotation gate's reader (#5641).** `retire` reads `deploy:offsite:restore` (`restoreBegan`) and holds the gate to the first pass that swept and began at or after its `began`.
2. **What R8 restores: whole R2 objects of one owner's namespace.** Only the objects the files bucket lacks, as the copy held them at a time the operator gives (`--at`), or as it stands. Each goes back at its own key, and each is read back byte for byte (`copyBack`).
   - **Not one owner's generations.** The copy can't read a generation: it is sealed under the owner's data key, and nothing outside the files Worker opens one. Mode `restore` already reads generations from R2. R8 puts the bytes back where it reads them, then mode `restore` runs unchanged if the database must go back too.
   - **Not the whole bucket.** Only an owner's object maps a namespace to an owner. A whole-bucket copy back would also bring back every erased owner's namespace still in the copy at that time (item 3).
   - **Only what R2 lacks (`--ignore-existing`, and the list of names R2 lacks).** Every object in the bucket is immutable except a root, which a rotation rewraps in place. Replacing a live root with an older wrap gains nothing and brings an old kid back. So a copy back only ever adds.
   - **At a time.** A deletion is hidden in the copy at the next night, so the copy as it stands lacks anything R2 lost more than a night ago. The copy keeps a hidden version `KEEP_DAYS` after the hide, so a view at a time inside them is whole. `BACK_DAYS` (21) is the oldest time taken, one day short of that, for the run itself (`readableAt`).
3. **How erasures are found and applied first: by refusing, before any byte (`r4.md` item 13's second way).**
   - The namespace a copy back writes is only ever the one the owner's own object answers (`restore.source` through the operator channel).
   - The doors refuse that op for an erased owner three times over: the directory's erased record at the channel's gate, the object's tombstone, and its erasure mark while it is erasing (`r5r.md` item 10, `r5w.md` item 4).
   - So an erased owner's namespace is never named, and nothing of it is listed, recorded or written. An object that names no namespace (a lost one) is refused by the kit, with nothing read.
   - These are the erasure's own records, so the kit keeps no list of its own beside them.
   - An erasure marked after `restore.source` answered still wins: its prefix delete waits `PUT_GRACE_MS` (a day) past the mark and repeats after the wipe, and the apply job ends within 30 minutes, so whatever the copy back wrote goes with the rest.
4. **The restore record goes before the first byte.**
   - `copyBack` lists R2 and the copy, writes `{kind, began}` to `deploy:offsite:restore` (`recordRestore`, beside its reader), and only then copies.
   - A refused put stops the run with nothing written. With nothing lacking, nothing is written or recorded.
   - The copy back is mode `copyback` of the kit, so it runs in `platform-deploy.yml` and its concurrency group `platform-deploy` (`r8-retire-gate.md` item 3).
   - `copyBack` is the only write from the copy into R2 there is.
5. **Not built: a lost object, or every owner after an account loss.** No object names a namespace there, and every erasure record above is gone with the account. Restoring either needs two things main lacks:
   - `locate`, a namespace's owner read from its generation (part 0's header, sealed: the files Worker's job, `r5r.md` item 12);
   - an erasure record that survives the account.

   The record is **A, an engineering choice** (review, 2026-11-15; Fact 4): A keeps no personal identifier (a namespace is a random 128-bit id, tied to a person only by the owner's object, which the erasure deletes), spends nothing new, and is neither irreversible nor outward-facing. B keeps none either, but restores an erased foreign issuer's owner, so it fails the erasure on its own. Only C keeps a person's id (the owner principal) outside the account, a privacy commitment only the founder could make; it is not taken.
   - **A. The files bucket records each erased namespace** (an object per namespace, written at the mark), so the copy carries it. No person's id leaves the account. Erasures marked after the last passed night are still missed (at most `HIDE_NIGHTS`, under the canary's alarm). Owner and files code change.
   - **B. The issuer is the record.** The erasure deletes the identity at the issuer at the mark, so after a loss only owners whose identity still stands are restored. No new data is kept. It misses a foreign issuer's owners (`iss-`), whose identity is not the platform's to delete.
   - **C. The nightly copies the directory's erased records off the account.** A person's id is then kept outside the account for good, and the nightly needs a KV read key.

   Until then R8 refuses these cases, loudly, and `checklist.md` says so. Owed: `untangle/r8-account-loss`, building A: the marker kept outside `o/<ns>/`, so the erasure's own prefix delete spares it, for at least `KEEP_DAYS` past the namespace's last hide.
6. **The drill, monthly, on the whole bucket; it writes nothing (`drill`).**
   - A second night runs on the 1st (`37 6 1 * *`), or a dispatch with `drill`. After its copy, it downloads every object R2 held when the night began from both sides and compares them byte for byte, the whole bucket rather than the nightly's 16. So a copy back as the copy stands writes exactly R2's bytes, which open at their keys.
   - It then runs a copy back's own reads (`readBack`) over the whole bucket, as the copy stood `BACK_DAYS` ago, the oldest time it takes. It counts what that copy back would write, and reads 16 of those objects whole (`rclone cat --discard`).
   - **What it does not prove:**
     - The R2 write: no standing key can write the files bucket. The first copy back of a rehearsal owner on the stage proves it (`live:`, `tasks.md`).
     - The doors' erasure refusals: their own suites prove those.
   - **Why the whole bucket and not a canary owner.** A restore may need any namespace. The drill reads ciphertext alone. A canary owner would tie the drill to the maintenance gate's list.
   - **Why it writes nothing.** A drill that wrote would need a standing write key. Each one would also restart every `FILES_KEYRING` retirement's gate.
   - A failed drill fails that night's copy job: the run is red, and the canary counts that night as not passed.
   - It runs in the nightly's group, `offsite-copy`, since it writes nothing and must not overlap a night.
7. **Credentials, by name. Dark until stored, loud at run time.**
   - Mode `copyback` reads:
     - `OFFSITE_BUCKET` (a repository variable);
     - the read key `OFFSITE_B2_READ_KEY_ID` and `OFFSITE_B2_READ_KEY` (the nights', from the founder's Backblaze steps);
     - the R2 write pair `OFFSITE_R2_WRITE_ACCESS_KEY_ID` and `OFFSITE_R2_WRITE_SECRET_ACCESS_KEY`, minted for one restore, ending the next day, and revoked after;
     - `OPERATOR_KEYRING` and `CLOUDFLARE_API_TOKEN`.
   - Each is an input of its row, so a run without one stops before any step, naming it. A value out of shape stops it too (`backFrom`).
   - The drill reads the nightly's seven settings and is dark with them.
   - Nothing is spent: B2 egress is free up to three times the stored size a month, and the drill reads the bucket once.
8. **Shapes weighed and rejected.**
   - A whole bucket at a version (item 2).
   - A list of erasures kept by the kit (item 3: the doors hold the records).
   - A drill into a scratch prefix or bucket. The prefix would be copied by the next night, and either needs a standing write key and restarts the gates.
   - A drill as a kit mode on its own workflow. It needs no operator op, so it stays with the nights' keys.
9. **Found and fixed (untangle).**
   - rclone's pin (version and digest) now had two jobs to serve, so it moved into one composite action, `.github/actions/rclone`, used by the nightly and by the deploy's apply job (for `copyback` alone).
   - `copy`'s rclone runner was a closure. It is now `rcloneOf`, shared by `copy`, `copyBack` and `drill`, with `withList` and `sampled` beside it.
   - `settings` and `backFrom` share one check of the stored names (`stored`).
   - `checklist.md`'s "a later step of its own" became the restore's own section.
10. **The proof.** Fakes only; nothing live.
    - `offsite.test.ts`:
      - the record is put after the two listings and before the copy, the copy holds only the names R2 lacks, then they are read back byte for byte;
      - the copy's view at the time, with the read key, and R2 with the write pair alone;
      - no credential as an argument;
      - a refused put writes nothing;
      - a time outside the window, or a missing key, does nothing;
      - the drill's calls and lines;
      - the workflow's drill night, its command, and the one rclone action before each use.
    - `deploy.test.ts`, mode `copyback`:
      - the plan;
      - with `--yes`, `restore.source` first, the record, then the copy;
      - nothing lacking, nothing recorded;
      - an erased owner, or an object naming no namespace, refused with nothing read or written;
      - each missing key refused before any step;
      - a time outside the window refused;
      - a malformed `--at` refused.
    - **Mutations**, each failing its test:
      - the record put after the copy;
      - the null namespace let through;
      - every name copied, not only what R2 lacks;
      - the keys made optional;
      - the window unchecked;
      - a refused put ignored;
      - the drill's byte check dropped;
      - the copy back's byte check dropped.
